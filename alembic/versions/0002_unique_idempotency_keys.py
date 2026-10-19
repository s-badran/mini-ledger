"""unique index on idempotency keys

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27 12:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Partial index: the column is null for every entry posted without a key,
    # and those rows have no business sitting in this index. On a live table
    # this is the statement that would need CONCURRENTLY.
    op.create_index(
        "ux_journal_entries_idempotency_key",
        "journal_entries",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_journal_entries_idempotency_key", table_name="journal_entries")
