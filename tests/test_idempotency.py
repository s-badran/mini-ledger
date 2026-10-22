"""Retries are normal traffic, not an exotic case. A key makes them free."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JournalEntry
from tests.support import entry_body


def entry_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(JournalEntry)) or 0


def test_replay_returns_the_original_entry(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    payload = entry_body((chart["1000"], "debit", 30_000), (chart["4000"], "credit", 30_000))
    headers = {"Idempotency-Key": "invoice-42"}

    first = client.post("/entries", json=payload, headers=headers)
    second = client.post("/entries", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.json() == first.json()
    assert entry_count(db_session) == 1


def test_json_key_order_does_not_become_a_conflict(
    client: TestClient, chart: dict[str, str]
) -> None:
    headers = {"Idempotency-Key": "invoice-43"}

    first = client.post(
        "/entries",
        json={
            "description": "same thing",
            "lines": [
                {"account_id": chart["1000"], "side": "debit", "amount": 100},
                {"account_id": chart["4000"], "side": "credit", "amount": 100},
            ],
        },
        headers=headers,
    )
    # Same values, different key order in the JSON objects.
    second = client.post(
        "/entries",
        json={
            "lines": [
                {"side": "debit", "amount": 100, "account_id": chart["1000"]},
                {"amount": 100, "account_id": chart["4000"], "side": "credit"},
            ],
            "description": "same thing",
        },
        headers=headers,
    )

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_same_key_with_a_different_payload_is_a_conflict(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    headers = {"Idempotency-Key": "invoice-44"}
    client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 100), (chart["4000"], "credit", 100)),
        headers=headers,
    )

    response = client.post(
        "/entries",
        json=entry_body((chart["1000"], "debit", 200), (chart["4000"], "credit", 200)),
        headers=headers,
    )

    assert response.status_code == 409
    assert "invoice-44" in response.json()["detail"]
    assert entry_count(db_session) == 1


def test_a_different_description_is_a_different_payload(
    client: TestClient, chart: dict[str, str]
) -> None:
    headers = {"Idempotency-Key": "invoice-45"}
    payload = entry_body((chart["1000"], "debit", 100), (chart["4000"], "credit", 100))
    client.post("/entries", json=payload, headers=headers)

    response = client.post(
        "/entries", json={**payload, "description": "changed my mind"}, headers=headers
    )

    assert response.status_code == 409


def test_posting_without_a_key_is_not_deduplicated(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    payload = entry_body((chart["1000"], "debit", 100), (chart["4000"], "credit", 100))

    first = client.post("/entries", json=payload)
    second = client.post("/entries", json=payload)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert entry_count(db_session) == 2


def test_different_keys_post_twice(client: TestClient, chart: dict[str, str]) -> None:
    payload = entry_body((chart["1000"], "debit", 100), (chart["4000"], "credit", 100))

    first = client.post("/entries", json=payload, headers={"Idempotency-Key": "a"})
    second = client.post("/entries", json=payload, headers={"Idempotency-Key": "b"})

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_oversized_and_empty_keys_are_refused(client: TestClient, chart: dict[str, str]) -> None:
    payload = entry_body((chart["1000"], "debit", 100), (chart["4000"], "credit", 100))

    too_long = client.post("/entries", json=payload, headers={"Idempotency-Key": "k" * 256})
    empty = client.post("/entries", json=payload, headers={"Idempotency-Key": ""})

    assert too_long.status_code == 422
    assert empty.status_code == 422
