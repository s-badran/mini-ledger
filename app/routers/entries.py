import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import JournalEntry
from app.schemas import EntryCreate, EntryRead
from app.services import ledger

router = APIRouter(prefix="/entries", tags=["entries"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=EntryRead, status_code=status.HTTP_201_CREATED)
def create_entry(payload: EntryCreate, session: SessionDep) -> JournalEntry:
    try:
        return ledger.post_entry(session, payload)
    except ledger.LedgerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("/{entry_id}", response_model=EntryRead)
def get_entry(entry_id: uuid.UUID, session: SessionDep) -> JournalEntry:
    entry = session.get(JournalEntry, entry_id, options=[selectinload(JournalEntry.lines)])
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such entry")
    return entry
