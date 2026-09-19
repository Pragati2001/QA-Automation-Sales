import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import CallStatus, GateStatus


class CallCreated(BaseModel):
    call_id: int
    status: CallStatus


class RecordingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    original_filename: str
    content_type: str | None
    size_bytes: int


class GateDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: GateStatus
    reason: str
    decided_at: datetime
    check_library_version: int | None


class CallRead(BaseModel):
    id: int
    status: CallStatus
    retailer_code: str
    external_lead_id: str
    recording: RecordingRead | None
    call_started_at: datetime | None
    gate_decision: GateDecisionRead | None
    created_at: datetime
    updated_at: datetime


def parse_crm_fields(raw: str) -> dict[str, Any]:
    """The CRM snapshot arrives as a JSON string in a multipart form field."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"crm_fields is not valid JSON: {e.msg}") from e
    if not isinstance(value, dict):
        raise ValueError("crm_fields must be a JSON object")
    return value
