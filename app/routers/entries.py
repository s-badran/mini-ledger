import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import JournalEntry
from app.schemas import EntryCreate, EntryRead
from app.services import ledger

router = APIRouter(prefix="/entries", tags=["entries"])

SessionDep = Annotated[Session, Depends(get_session)]

# 255 matches the column width; anything longer would be a client bug worth
# rejecting rather than quietly truncating.
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)]


@router.post("", response_model=EntryRead, status_code=status.HTTP_201_CREATED)
def create_entry(
    payload: EntryCreate,
    session: SessionDep,
    response: Response,
    idempotency_key: IdempotencyKey = None,
) -> JournalEntry:
    try:
        result = ledger.post_entry(session, payload, idempotency_key)
    except ledger.IdempotencyConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except ledger.LedgerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    if result.replayed:
        # The client sent a key it already used. Same entry, nothing new written.
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replay"] = "true"
    return result.entry


@router.get("/{entry_id}", response_model=EntryRead)
def get_entry(entry_id: uuid.UUID, session: SessionDep) -> JournalEntry:
    entry = session.get(JournalEntry, entry_id, options=[selectinload(JournalEntry.lines)])
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such entry")
    return entry
