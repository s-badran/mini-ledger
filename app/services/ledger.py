import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Account, JournalEntry, JournalLine, Side
from app.schemas import EntryCreate


class LedgerError(Exception):
    """Domain failure. The router decides which status code that becomes."""


class UnbalancedEntry(LedgerError):
    def __init__(self, debits: int, credits: int) -> None:
        self.debits = debits
        self.credits = credits
        super().__init__(f"entry does not balance: debits {debits}, credits {credits}")


class UnknownAccount(LedgerError):
    def __init__(self, account_id: uuid.UUID) -> None:
        self.account_id = account_id
        super().__init__(f"account {account_id} does not exist")


class IdempotencyConflict(LedgerError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"idempotency key {key!r} was already used for a different payload")


@dataclass(frozen=True)
class PostingResult:
    entry: JournalEntry
    replayed: bool


def _fingerprint(payload: EntryCreate) -> str:
    # Serialising the parsed model rather than hashing the raw body means a
    # client can reorder JSON keys without tripping the conflict check. Line
    # order still counts, because the list is part of the payload.
    return hashlib.sha256(payload.model_dump_json().encode()).hexdigest()


def _find_by_key(session: Session, key: str) -> JournalEntry | None:
    return session.scalars(
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.idempotency_key == key)
    ).one_or_none()


def _replay(existing: JournalEntry, fingerprint: str, key: str) -> PostingResult:
    if existing.request_fingerprint != fingerprint:
        raise IdempotencyConflict(key)
    return PostingResult(entry=existing, replayed=True)


def post_entry(
    session: Session,
    payload: EntryCreate,
    idempotency_key: str | None = None,
) -> PostingResult:
    debits = sum(line.amount for line in payload.lines if line.side is Side.debit)
    credits = sum(line.amount for line in payload.lines if line.side is Side.credit)
    if debits != credits:
        raise UnbalancedEntry(debits, credits)

    fingerprint = _fingerprint(payload) if idempotency_key is not None else None

    if idempotency_key is not None:
        # Cheap path first, no locks: most retries arrive long after the
        # original request committed.
        existing = _find_by_key(session, idempotency_key)
        if existing is not None:
            return _replay(existing, fingerprint or "", idempotency_key)

    # Lock every account the entry touches before writing anything, always in
    # id order. Two postings that hit the same pair of accounts in opposite
    # order would otherwise deadlock each other inside Postgres, and sorted
    # acquisition makes that impossible rather than unlikely.
    account_ids = sorted({line.account_id for line in payload.lines})
    locked = session.scalars(
        select(Account).where(Account.id.in_(account_ids)).order_by(Account.id).with_for_update()
    ).all()
    missing = set(account_ids) - {account.id for account in locked}
    if missing:
        raise UnknownAccount(min(missing))

    entry = JournalEntry(
        description=payload.description,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    entry.lines = [
        JournalLine(
            position=position,
            account_id=line.account_id,
            side=line.side,
            amount=line.amount,
        )
        for position, line in enumerate(payload.lines)
    ]
    session.add(entry)

    try:
        session.flush()
    except IntegrityError:
        # Same key, two requests in flight: the loser blocks on the unique
        # index until the winner commits, then lands here. Everything in this
        # transaction is discarded and the winner's entry is the answer.
        session.rollback()
        if idempotency_key is None:
            raise
        existing = _find_by_key(session, idempotency_key)
        if existing is None:
            raise
        return _replay(existing, fingerprint or "", idempotency_key)

    session.refresh(entry)
    session.commit()
    return PostingResult(entry=entry, replayed=False)
