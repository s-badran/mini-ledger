import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AccountType, Side


class AccountCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=120)
    type: AccountType


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    type: AccountType


class LineIn(BaseModel):
    account_id: uuid.UUID
    side: Side
    amount: int = Field(gt=0, description="minor units, always positive")


class EntryCreate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    lines: list[LineIn] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def _reject_duplicate_accounts(self) -> "EntryCreate":
        accounts = [line.account_id for line in self.lines]
        if len(set(accounts)) != len(accounts):
            raise ValueError("an entry names the same account twice")
        return self


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    side: Side
    amount: int


class EntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    description: str | None
    lines: list[LineRead]


class BalanceRead(BaseModel):
    account_id: uuid.UUID
    as_of: date | None
    debits: int
    credits: int
    # Signed towards the account's normal side, so a liability reads positive
    # when it is owed. Can go negative; that is information, not an error.
    balance: int
    normal_side: Side
