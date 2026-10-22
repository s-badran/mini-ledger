"""Threads, real Postgres, no mocks.

These tests document the two things that actually go wrong when postings run in
parallel: the same key arriving twice at once, and two entries grabbing the same
accounts in opposite order.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import JournalEntry, JournalLine, Side
from app.schemas import EntryCreate
from app.services import ledger
from tests.support import entry_body


def run_together[T](count: int, work: Callable[[int], T]) -> list[T]:
    """Start `count` calls as close to simultaneously as threads allow."""
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> T:
        barrier.wait(timeout=10)
        return work(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(wrapped, index) for index in range(count)]
        return [future.result() for future in futures]


def post(
    session_factory: sessionmaker[Session], payload: EntryCreate, key: str | None = None
) -> ledger.PostingResult:
    with session_factory() as session:
        return ledger.post_entry(session, payload, key)


def payload_for(chart: dict[str, str], *lines: tuple[str, str, int]) -> EntryCreate:
    return EntryCreate.model_validate(entry_body(*lines))


def test_same_key_in_flight_creates_one_entry(
    session_factory: sessionmaker[Session], chart: dict[str, str]
) -> None:
    payload = payload_for(chart, (chart["1000"], "debit", 7_500), (chart["4000"], "credit", 7_500))

    results = run_together(8, lambda _: post(session_factory, payload, "race-key"))

    assert len({result.entry.id for result in results}) == 1
    assert [result.replayed for result in results].count(False) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JournalEntry)) == 1


def test_opposite_line_order_does_not_deadlock(
    session_factory: sessionmaker[Session], chart: dict[str, str], db_session: Session
) -> None:
    debit_cash = payload_for(
        chart, (chart["1000"], "debit", 1_000), (chart["4000"], "credit", 1_000)
    )
    credit_cash = payload_for(
        chart, (chart["4000"], "debit", 1_000), (chart["1000"], "credit", 1_000)
    )

    rounds = 10
    for _ in range(rounds):
        run_together(
            2, lambda index: post(session_factory, debit_cash if index == 0 else credit_cash)
        )

    assert db_session.scalar(select(func.count()).select_from(JournalEntry)) == rounds * 2
    net = db_session.scalar(
        select(func.sum(JournalLine.amount)).where(
            JournalLine.account_id == chart["1000"], JournalLine.side == Side.debit
        )
    )
    assert net == rounds * 1_000


def test_parallel_postings_to_one_account_all_land(
    session_factory: sessionmaker[Session], chart: dict[str, str], db_session: Session
) -> None:
    amount = 250
    payload = payload_for(
        chart, (chart["1000"], "debit", amount), (chart["4000"], "credit", amount)
    )
    workers = 12

    results = run_together(workers, lambda _: post(session_factory, payload))

    assert all(not result.replayed for result in results)
    assert db_session.scalar(select(func.count()).select_from(JournalEntry)) == workers
    debits = db_session.scalar(
        select(func.sum(JournalLine.amount)).where(JournalLine.account_id == chart["1000"])
    )
    assert debits == workers * amount
