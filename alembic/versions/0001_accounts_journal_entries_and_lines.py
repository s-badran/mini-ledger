"""accounts, journal entries and lines

Revision ID: 0001
Revises:
Create Date: 2026-10-27 12:10:43.504073

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "asset",
                "liability",
                "equity",
                "income",
                "expense",
                name="ck_accounts_type",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("code", name="uq_accounts_code"),
    )

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_journal_entries"),
    )
    op.create_index("ix_journal_entries_created_at", "journal_entries", ["created_at"])

    op.create_table(
        "journal_lines",
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "side",
            sa.Enum(
                "debit",
                "credit",
                name="ck_journal_lines_side",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_journal_lines_amount_positive"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_journal_lines_account_id"
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["journal_entries.id"], name="fk_journal_lines_entry_id"
        ),
        sa.PrimaryKeyConstraint("entry_id", "position", name="pk_journal_lines"),
    )
    op.create_index("ix_journal_lines_account_id", "journal_lines", ["account_id"])

    # Nothing in the application updates or deletes a posted entry, but "nothing
    # in the application" stops being true the first time someone opens psql on
    # a bad Tuesday. The trigger is the actual guarantee.
    op.execute(
        """
        CREATE FUNCTION reject_ledger_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_entries_append_only
        BEFORE UPDATE OR DELETE ON journal_entries
        FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER journal_lines_append_only
        BEFORE UPDATE OR DELETE ON journal_lines
        FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
        """
    )


def downgrade() -> None:
    op.drop_index("ix_journal_lines_account_id", table_name="journal_lines")
    op.drop_table("journal_lines")
    op.drop_index("ix_journal_entries_created_at", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_table("accounts")
    op.execute("DROP FUNCTION reject_ledger_mutation()")
