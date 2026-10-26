from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.support import backdated_entry, entry_body


def get_balance(client: TestClient, account_id: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"/accounts/{account_id}/balance", params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_balances_match_hand_computed_fixtures(client: TestClient, chart: dict[str, str]) -> None:
    postings = [
        entry_body((chart["1000"], "debit", 125_000), (chart["4000"], "credit", 125_000)),
        entry_body((chart["5000"], "debit", 40_000), (chart["1000"], "credit", 40_000)),
        entry_body((chart["1000"], "debit", 500_000), (chart["3000"], "credit", 500_000)),
        entry_body((chart["5000"], "debit", 10_000), (chart["2000"], "credit", 10_000)),
    ]
    for posting in postings:
        assert client.post("/entries", json=posting).status_code == 201

    cash = get_balance(client, chart["1000"])
    assert (cash["debits"], cash["credits"], cash["balance"]) == (625_000, 40_000, 585_000)
    assert cash["normal_side"] == "debit"

    sales = get_balance(client, chart["4000"])
    assert (sales["debits"], sales["credits"], sales["balance"]) == (0, 125_000, 125_000)
    assert sales["normal_side"] == "credit"

    rent = get_balance(client, chart["5000"])
    assert rent["balance"] == 50_000

    equity = get_balance(client, chart["3000"])
    assert (equity["debits"], equity["credits"], equity["balance"]) == (0, 500_000, 500_000)

    payable = get_balance(client, chart["2000"])
    assert (payable["debits"], payable["credits"], payable["balance"]) == (0, 10_000, 10_000)

    # Everything above is one closed system: debits and credits have to agree.
    total_debits = sum(get_balance(client, account)["debits"] for account in chart.values())
    total_credits = sum(get_balance(client, account)["credits"] for account in chart.values())
    assert total_debits == total_credits == 675_000


def test_as_of_counts_whole_utc_days(
    client: TestClient, chart: dict[str, str], db_session: Session
) -> None:
    backdated_entry(
        db_session,
        when=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
        lines=((chart["1000"], "debit", 1_000), (chart["4000"], "credit", 1_000)),
    )
    backdated_entry(
        db_session,
        when=datetime(2026, 1, 11, 0, 0, tzinfo=UTC),
        lines=((chart["1000"], "debit", 2_000), (chart["4000"], "credit", 2_000)),
    )

    assert get_balance(client, chart["1000"])["balance"] == 3_000
    assert get_balance(client, chart["1000"], as_of="2026-01-10")["balance"] == 1_000
    assert get_balance(client, chart["1000"], as_of="2026-01-09")["balance"] == 0
    assert get_balance(client, chart["1000"], as_of="2026-01-11")["balance"] == 3_000


def test_as_of_comes_back_in_the_response(client: TestClient, chart: dict[str, str]) -> None:
    body = get_balance(client, chart["1000"], as_of="2026-01-31")

    assert body["as_of"] == "2026-01-31"


def test_asset_can_go_negative_and_that_is_not_an_error(
    client: TestClient, chart: dict[str, str]
) -> None:
    # A refund out of an account that never had anything put in it.
    response = client.post(
        "/entries",
        json=entry_body((chart["5000"], "debit", 2_500), (chart["1000"], "credit", 2_500)),
    )
    assert response.status_code == 201

    assert get_balance(client, chart["1000"])["balance"] == -2_500


def test_account_with_no_postings_reads_zero(client: TestClient, chart: dict[str, str]) -> None:
    body = get_balance(client, chart["2000"])

    assert (body["debits"], body["credits"], body["balance"]) == (0, 0, 0)


def test_balance_for_unknown_account_is_404(client: TestClient) -> None:
    response = client.get("/accounts/00000000-0000-0000-0000-000000000000/balance")

    assert response.status_code == 404
