import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account
from app.schemas import AccountCreate, AccountRead, BalanceRead
from app.services import ledger

router = APIRouter(prefix="/accounts", tags=["accounts"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, session: SessionDep) -> Account:
    account = Account(code=payload.code, name=payload.name, type=payload.type)
    session.add(account)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"account code {payload.code!r} is already in use",
        ) from None
    return account


@router.get("", response_model=list[AccountRead])
def list_accounts(session: SessionDep) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.code)))


@router.get("/{account_id}", response_model=AccountRead)
def get_account(account_id: uuid.UUID, session: SessionDep) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such account")
    return account


@router.get("/{account_id}/balance", response_model=BalanceRead)
def get_balance(
    account_id: uuid.UUID,
    session: SessionDep,
    as_of: Annotated[date | None, Query(description="UTC day, inclusive")] = None,
) -> BalanceRead:
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such account")

    balance = ledger.account_balance(session, account, as_of)
    return BalanceRead(
        account_id=account.id,
        as_of=as_of,
        debits=balance.debits,
        credits=balance.credits,
        balance=balance.balance,
        normal_side=balance.normal_side,
    )
