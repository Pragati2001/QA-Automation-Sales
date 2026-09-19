from datetime import datetime, timezone
from pathlib import PureWindowsPath
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Call, CallStatus, Lead, Recording
from app.repositories.base import BaseRepository
from app.repositories.retailer_repository import RetailerRepository
from app.schemas.calls import CallCreated, CallRead, parse_crm_fields
from app.services.calls import processing_service
from app.services.storage.audio_storage import AudioStorage, get_audio_storage

router = APIRouter(prefix="/calls", tags=["calls"])


@router.post("", response_model=CallCreated, status_code=202)
def create_call(
    background_tasks: BackgroundTasks,
    retailer_code: Annotated[str, Form(description="Retailer code, e.g. retailer1")],
    external_lead_id: Annotated[str, Form(description="The CRM's lead id, e.g. 3613790")],
    audio: UploadFile,
    crm_fields: Annotated[
        str, Form(description='CRM snapshot as a JSON object, e.g. {"email": "j.smith@example.com"}')
    ] = "{}",
    call_started_at: Annotated[
        datetime | None,
        Form(description="Optional. When the call started (ISO 8601 with a timezone offset). It decides "
                         "which check-library version applies. Leave it out and the server uses the time "
                         "the call was received."),
    ] = None,
    db: Session = Depends(get_db),
    storage: AudioStorage = Depends(get_audio_storage),
):
    """Ingest a call: upsert the lead, store the audio, then process in the background."""
    retailer = next(iter(RetailerRepository(db).list(code=retailer_code)), None)
    if retailer is None:
        raise HTTPException(404, f"Unknown retailer_code {retailer_code!r}")
    try:
        crm = parse_crm_fields(crm_fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if call_started_at is not None and call_started_at.tzinfo is None:
        raise HTTPException(422, "call_started_at must include a timezone offset, e.g. 2026-01-15T10:30:00+05:30")
    content = audio.file.read()
    if not content:
        raise HTTPException(422, "Audio file is empty")

    leads = BaseRepository(Lead, db)
    lead = next(iter(leads.list(retailer_id=retailer.id, external_lead_id=external_lead_id)), None)
    if lead is None:
        lead = leads.create(retailer_id=retailer.id, external_lead_id=external_lead_id, crm_fields=crm)
    elif crm:  # an empty snapshot must not wipe the existing one
        lead.crm_fields = crm

    # Calls are ingested within minutes of hangup, so when the caller doesn't say when the call started
    # we use the time it arrived (timezone-aware UTC).
    if call_started_at is None:
        call_started_at = datetime.now(timezone.utc)
    call = BaseRepository(Call, db).create(
        lead_id=lead.id, status=CallStatus.PROCESSING, call_started_at=call_started_at
    )
    call_id = call.id
    reference = None
    try:
        # The filename is client-controlled: keep only its basename (no path traversal).
        filename = PureWindowsPath(audio.filename or "").name or "recording"
        reference = storage.save(str(call_id), filename, content)
        BaseRepository(Recording, db).create(
            call_id=call_id,
            storage_reference=reference,
            original_filename=filename,
            content_type=audio.content_type,
            size_bytes=len(content),
        )
        db.commit()
    except Exception:
        db.rollback()
        if reference:
            storage.delete(reference)  # don't leave an orphan file behind
        raise

    background_tasks.add_task(processing_service.process_call, str(call_id))
    return CallCreated(call_id=call_id, status=CallStatus.PROCESSING)


@router.get("/{call_id}", response_model=CallRead)
def get_call(call_id: int, db: Session = Depends(get_db)):
    call = BaseRepository(Call, db).get(call_id)
    if call is None:
        raise HTTPException(404, f"Call {call_id} not found")
    return CallRead(
        id=call.id,
        status=call.status,
        retailer_code=call.lead.retailer.code,
        external_lead_id=call.lead.external_lead_id,
        recording=call.recording,
        call_started_at=call.call_started_at,
        gate_decision=call.gate_decision,
        created_at=call.created_at,
        updated_at=call.updated_at,
    )
