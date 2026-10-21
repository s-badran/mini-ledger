"""Small builders so the tests read like ledger entries instead of dict literals."""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalLine, Side

CHART: tuple[tuple[str, str, str], ...] = (
    ("1000", "Cash", "asset"),
    ("2000", "Accounts payable", "liability"),
    ("3000", "Owner equity", "equity"),
    ("4000", "Sales", "income"),
    ("5000", "Rent", "expense"),
)

LineIn = tuple[str, str, int]


def entry_body(*lines: LineIn, description: str | None = "test entry") -> dict[str, Any]:
    return {
        "description": description,
        "lines": [
            {"account_id": account_id, "side": side, "amount": amount}
            for account_id, side, amount in lines
        ],
    }


def backdated_entry(
    session: Session,
    *,
    when: datetime,
    lines: tuple[LineIn, ...],
    description: str = "backdated",
) -> JournalEntry:
    """Insert with a chosen created_at.

    The append-only trigger blocks UPDATE and DELETE, not INSERT, which is what
    makes this possible. Real postings always take the default of now().
    """
    entry = JournalEntry(description=description, created_at=when)
    entry.lines = [
        JournalLine(
            position=position,
            account_id=account_id,
            side=Side(side),
            amount=amount,
        )
        for position, (account_id, side, amount) in enumerate(lines)
    ]
    session.add(entry)
    session.commit()
    return entry
