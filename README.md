# mini-ledger

A double-entry ledger behind a small HTTP API. You create accounts, post journal
entries with debits and credits, and read balances that are computed from the
lines every time you ask. Entries are append-only, so a correction is a second
entry that reverses the first.

I built it because two problems show up in every billing or payments backend I
have worked on, and both of them are easier to discuss with running code than
with a diagram: keeping debits and credits in agreement when several clients
write at the same time, and making a retried `POST` safe to send twice.

## Why I built it

Most "ledger" examples online skip the parts that make a ledger a ledger. They
store a balance column, they let you edit a transaction, or they deduplicate
retries in application memory and hope the process stays up. This one takes the
boring positions: balances are sums, entries never change, and idempotency is a
constraint in the database. The scope stays small enough to read in one sitting.

## Design decisions

1. **Synchronous SQLAlchemy.** Async would let a single worker multiplex more
   requests while waiting on Postgres. Posting an entry is one short transaction
   touching a handful of rows, and the latency is spent inside the database
   either way, so the extra concurrency model costs more than it buys here. A
   report endpoint that fans out to several slow queries is where I would reach
   for async; this one is not getting rewritten for it.

2. **Amounts are integers in minor units.** Postgres `NUMERIC` is exact, but it
   serialises to JSON as a string and clients convert it back to a double sooner
   or later. Integers survive the wire unchanged. The trade-off is that the
   currency is implicit: `125000` means cents because everyone agrees it does.

3. **Balances are derived, never stored.** A cached balance column is faster and
   becomes a lie the first time two writes race. Summing an indexed `account_id`
   with an aggregate filter is fast enough for the amounts this was built for,
   and there is exactly one place where a balance can be wrong. If an account
   ever grows past a few million lines I would add periodic snapshots, not a
   live counter.

4. **Append-only is enforced by a trigger in Postgres.** Not exposing `PUT` or
   `DELETE` for entries is the first layer of the defence. The trigger is the
   second: it fires on any `UPDATE` or `DELETE` of `journal_entries` or
   `journal_lines`, from any session, including psql and whatever migration
   script is written next year. Corrections go through reversal entries.

5. **Idempotency keys live in Postgres, behind a partial unique index.** The
   common alternative is Redis with `SETNX` and a TTL. That adds a second store
   to keep in step with the transaction that actually wrote the entry, and the
   failure mode is a key marked as used with no entry behind it. A unique index
   deduplicates inside the same transaction: two requests carrying one key
   cannot both insert, and the loser reads the winner's row. The index is
   partial, so the majority of entries posted without a key do not sit in it.
   That index is a separate migration because building it on a live table is the
   statement you would want to run with `CONCURRENTLY`.

## How a posting works

`POST /entries` runs in one transaction:

1. Sum the debit lines and the credit lines. Unequal totals stop here.
2. If an `Idempotency-Key` header is present, hash the parsed payload and look
   the key up. A hit with a matching hash is a replay; a hit with a different
   hash is a `409`.
3. `SELECT ... FOR UPDATE` over the accounts in the entry, ordered by id.
4. Insert the entry and its lines.
5. Commit.

The row locks are not protecting a stored total, since there isn't one. They
make the read of the accounts and the write of the entry one critical section per
account, which is what a rule like "this account may not go negative" would need
the day I add it. Today the visible effect is that two writers on the same
account queue up instead of interleaving, and the wait shows up in
`pg_stat_activity` rather than in a mystery.

Ordering those locks by id matters. Two postings that touch the same pair of
accounts in opposite order will deadlock each other if the lock order follows the
request, and Postgres resolves that by killing one of them. Sorted acquisition
removes the possibility instead of lowering the odds.

## Quick start

Needs Docker and Python 3.12.

```bash
git clone https://github.com/s-badran/mini-ledger.git
cd mini-ledger

docker compose up -d db

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head
uvicorn app.main:app --reload
```

Compose publishes Postgres on port **5433**, because 5432 is where whatever
else you have running already listens. Point `LEDGER_DATABASE_URL` somewhere else
if you prefer your own server; `app/config.py` is the only place that reads it.

Two migrations: the initial schema, then the idempotency index.

## API walkthrough

Examples use `jq` to pull ids out of responses.

```bash
BASE=http://localhost:8000

CASH=$(curl -s -X POST $BASE/accounts -H 'Content-Type: application/json' \
  -d '{"code":"1000","name":"Cash","type":"asset"}' | jq -r .id)

SALES=$(curl -s -X POST $BASE/accounts -H 'Content-Type: application/json' \
  -d '{"code":"4000","name":"Sales","type":"income"}' | jq -r .id)

RENT=$(curl -s -X POST $BASE/accounts -H 'Content-Type: application/json' \
  -d '{"code":"5000","name":"Rent","type":"expense"}' | jq -r .id)
```

Post an invoice, with a key so the client can retry it:

```bash
curl -s -X POST $BASE/entries \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: invoice-2026-031' \
  -d "{
    \"description\": \"Invoice 31\",
    \"lines\": [
      {\"account_id\": \"$CASH\",  \"side\": \"debit\",  \"amount\": 125000},
      {\"account_id\": \"$SALES\", \"side\": \"credit\", \"amount\": 125000}
    ]
  }"
```

```json
{
  "id": "0f3a6a0e-5c1e-4d15-9a3b-6a2f0b7e11c4",
  "created_at": "2026-10-27T09:12:08.716451Z",
  "description": "Invoice 31",
  "lines": [
    {"account_id": "b7f1c1a2-...", "side": "debit", "amount": 125000},
    {"account_id": "3c9e0d77-...", "side": "credit", "amount": 125000}
  ]
}
```

Send the exact same request again and nothing new is written. The response is
`200` instead of `201`, carries `Idempotent-Replay: true`, and contains the
original entry:

```bash
curl -s -i -X POST $BASE/entries \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: invoice-2026-031' \
  -d '{ ... same body ... }' | head -5
```

Change the amount while reusing the key and you get a conflict:

```
HTTP/1.1 409 Conflict

{"detail": "idempotency key 'invoice-2026-031' was already used for a different payload"}
```

Pay rent, then read both balances:

```bash
curl -s -X POST $BASE/entries -H 'Content-Type: application/json' \
  -d "{
    \"description\": \"October rent\",
    \"lines\": [
      {\"account_id\": \"$RENT\",  \"side\": \"debit\",  \"amount\": 40000},
      {\"account_id\": \"$CASH\",  \"side\": \"credit\", \"amount\": 40000}
    ]
  }"

curl -s $BASE/accounts/$CASH/balance
```

```json
{
  "account_id": "b7f1c1a2-...",
  "as_of": null,
  "debits": 125000,
  "credits": 40000,
  "balance": 85000,
  "normal_side": "debit"
}
```

`balance` is signed towards the account's normal side: assets and expenses read
positive on debits, liabilities, equity and income read positive on credits. A
negative number is legal and usually means the account was credited more than it
was debited (a refund, an overpayment).

`as_of` takes a date and includes that whole UTC day:

```bash
curl -s "$BASE/accounts/$CASH/balance?as_of=2026-10-26"
```

Other endpoints: `GET /accounts`, `GET /accounts/{id}`, `GET /entries/{id}`,
`GET /healthz`.

Status codes, briefly: `201` posted, `200` replayed or read, `409` duplicate
account code or idempotency key with a different payload, `422` an entry that
does not balance or references an account that does not exist, `404` unknown id.

## Testing

```bash
docker compose up -d db
pytest
```

The suite creates `ledger_test` on the same server, runs the migrations against
it, and truncates the tables between tests. Everything runs against real
Postgres. Swapping in SQLite would delete the parts worth testing (`FOR UPDATE`,
the trigger, the partial index), so the tests refuse the shortcut the same way
the app does.

What the tests pin down:

- an entry whose debits and credits disagree returns `422` and leaves both
  tables untouched;
- zero, negative and fractional amounts are refused, as is naming the same
  account twice in one entry;
- unknown accounts are refused before anything is written;
- the trigger rejects `UPDATE` and `DELETE` on posted entries and lines;
- balances match hand-computed fixtures for all five account types, and the
  `as_of` cutoff falls exactly at midnight UTC;
- a repeated key returns the original entry, a changed payload under the same
  key returns `409`, and a posting without a key still posts twice;
- eight threads posting one key at once produce one entry and seven replays;
- two threads posting the same accounts in opposite line order finish without
  deadlocking, and the money nets to zero;
- twelve threads posting to one account keep every posting.

Coverage is not 100% and I am not going to pretend it is. Untested: the
`downgrade()` path of either migration, behaviour when the connection drops
mid-transaction, malformed UUIDs in paths, and any property-based generation of
random balanced entries. There is also a `TODO` in `tests/test_invariants.py`
about parameterising the entry shapes; it is honest about where I stopped.

## Known limitations

- Single currency. Amounts carry no currency code, so the ledger assumes one.
- No authentication and no rate limiting. Every endpoint is open; run it on
  localhost or behind something that authenticates.
- Balances aggregate the whole history of an account on every request. Fine at
  small volumes, linear forever.
- Idempotency keys are never expired. An entry posted with a key keeps that key
  until the row is gone, which in an append-only table means forever.
- No pagination or filtering on entries. `GET /entries/{id}` only.
- Postgres-specific by design: `FOR UPDATE` ordering, partial indexes, a plpgsql
  trigger. Porting this to another database is a rewrite of the interesting
  parts, not a dialect tweak.

## What I'd do differently

1. Enforce the balance rule in the database too. Right now the debit-equals-credit
   check lives in `services/ledger.py`, so a second writer path (a data import
   script, say) could insert an unbalanced entry. A deferred constraint trigger
   on `journal_lines` would make it impossible rather than merely unlikely.
2. Give idempotency its own table with the stored response and a retention
   window. Today the key lives on the entry and replays re-serialise the entry,
   which is correct only as long as the response shape never changes under an
   old key.
3. Store a book timezone per account instead of using UTC days. A German company
   closing the books on 31 March means 23:00 UTC on 31 March, not midnight, and
   `as_of` currently gets that boundary wrong by an hour or two.

## License

MIT. See `LICENSE`.
