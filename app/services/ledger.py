import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def post_entry(session: Session, payload: EntryCreate) -> JournalEntry:
    debits = sum(line.amount for line in payload.lines if line.side is Side.debit)
    credits = sum(line.amount for line in payload.lines if line.side is Side.credit)
    if debits != credits:
        raise UnbalancedEntry(debits, credits)

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

    entry = JournalEntry(description=payload.description)
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
    session.flush()
    session.refresh(entry)
    session.commit()
    return entry
