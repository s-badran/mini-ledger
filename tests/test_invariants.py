"""What the ledger refuses to accept, and what it refuses to change afterwards."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalLine
from tests.support import entry_body


def count_entries(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(JournalEntry)) or 0


def count_lines(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(JournalLine)) or 0


def test_out_of_balance_entry_is_rejected_and_stores_nothing(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    response = client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 125_000), (chart["4000"], "credit", 120_000)),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "entry does not balance: debits 125000, credits 120000"
    assert count_entries(db_session) == 0
    assert count_lines(db_session) == 0


def test_multi_line_entry_only_needs_the_totals_to_match(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    response = client.post(
        "/entries",
        json=entry_body(
            (chart["1000"], "debit", 80_000),
            (chart["5000"], "debit", 20_000),
            (chart["4000"], "credit", 100_000),
        ),
    )

    assert response.status_code == 201
    assert count_lines(db_session) == 3


def test_entry_needs_at_least_two_lines(client: TestClient, chart: dict[str, str]) -> None:
    response = client.post("/entries", json=entry_body((chart["1000"], "debit", 100)))

    assert response.status_code == 422


def test_amounts_must_be_positive_integers(client: TestClient, chart: dict[str, str]) -> None:
    for amount in (0, -500, 10.5):
        response = client.post(
            "/entries",
            json={
                "description": "bad amount",
                "lines": [
                    {"account_id": chart["1000"], "side": "debit", "amount": amount},
                    {"account_id": chart["4000"], "side": "credit", "amount": amount},
                ],
            },
        )
        assert response.status_code == 422, f"amount {amount} was accepted"


def test_same_account_on_both_sides_is_rejected(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    response = client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 100), (chart["1000"], "credit", 100)),
    )

    assert response.status_code == 422
    assert count_entries(db_session) == 0


def test_unknown_account_is_rejected_and_stores_nothing(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    ghost = "00000000-0000-0000-0000-000000000000"
    response = client.post(
        "/entries",
        json=entry_body((ghost, "debit", 100), (chart["4000"], "credit", 100)),
    )

    assert response.status_code == 422
    assert ghost in response.json()["detail"]
    assert count_entries(db_session) == 0


def test_posted_entry_cannot_be_updated(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    entry_id = client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 500), (chart["4000"], "credit", 500)),
    ).json()["id"]

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text("UPDATE journal_lines SET amount = 1 WHERE entry_id = :id"), {"id": entry_id}
        )
        db_session.commit()
    db_session.rollback()

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text("UPDATE journal_entries SET description = 'edited' WHERE id = :id"),
            {"id": entry_id},
        )
        db_session.commit()
    db_session.rollback()


def test_posted_entry_cannot_be_deleted(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    entry_id = client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 700), (chart["4000"], "credit", 700)),
    ).json()["id"]

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text("DELETE FROM journal_lines WHERE entry_id = :id"), {"id": entry_id})
        db_session.commit()
    db_session.rollback()

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text("DELETE FROM journal_entries WHERE id = :id"), {"id": entry_id})
        db_session.commit()
    db_session.rollback()

    assert count_entries(db_session) == 1


def test_corrections_are_new_entries_not_edits(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    original = client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 5000), (chart["4000"], "credit", 5000)),
    )
    assert original.status_code == 201

    # Reversal: same accounts, sides swapped.
    reversal = client.post(
        "/entries",
        json=entry_body((chart["4000"], "debit", 5000), (chart["1000"], "credit", 5000)),
    )

    assert reversal.status_code == 201
    assert reversal.json()["id"] != original.json()["id"]
    assert count_entries(db_session) == 2


# TODO: parameterize these over entry shape (1..5 lines, mixed sides) once the
#       fixture for generating balanced random entries exists.
