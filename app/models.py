import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountType(enum.StrEnum):
    asset = "asset"
    liability = "liability"
    equity = "equity"
    income = "income"
    expense = "expense"

    @property
    def normal_side(self) -> "Side":
        """Which side increases this account. Assets and expenses live on debits."""
        if self in (AccountType.asset, AccountType.expense):
            return Side.debit
        return Side.credit


class Side(enum.StrEnum):
    debit = "debit"
    credit = "credit"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    # A varchar plus check constraint instead of a native enum: adding a type
    # later is a one-line constraint change rather than an enum migration.
    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", native_enum=False, length=16)
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    description: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="JournalLine.position",
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        PrimaryKeyConstraint("entry_id", "position"),
        CheckConstraint("amount > 0", name="ck_journal_lines_amount_positive"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("journal_entries.id"))
    # Kept only so responses and tests see lines in the order they were posted.
    position: Mapped[int] = mapped_column()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"), index=True)
    side: Mapped[Side] = mapped_column(Enum(Side, name="entry_side", native_enum=False, length=8))
    amount: Mapped[int] = mapped_column(BigInteger)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
