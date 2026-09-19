"""Response shapes for the TL review payload (GET /calls/{id}/review)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import CallStatus, CheckType, GateStatus
from app.schemas.check_result import CheckStatus


class ReviewRecording(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    original_filename: str
    content_type: str | None
    size_bytes: int


class ReviewCall(BaseModel):
    id: int
    status: CallStatus
    call_started_at: datetime | None
    created_at: datetime
    updated_at: datetime
    recording: ReviewRecording | None  # None: there is no audio to play


class ReviewRetailer(BaseModel):
    id: int
    code: str
    name: str


class ReviewLead(BaseModel):
    id: int
    external_lead_id: str
    retailer: ReviewRetailer
    # The CRM snapshot is deliberately not included: the values that were compared appear as
    # `expected_value` on the checks, and the endpoint has no authentication yet.


class ReviewGateDecision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: GateStatus
    reason: str
    decided_at: datetime
    check_library_id: int | None
    check_library_version: int | None


class ReviewCheckInfo(BaseModel):
    id: int
    code: str
    name: str
    type: CheckType
    critical: bool
    version: int  # the check library version this result was produced with


class ReviewConfidence(BaseModel):
    asr: float | None
    extraction: float | None
    rule: float | None
    overall: float | None  # the lowest of the three that are present


class ReviewEvidence(BaseModel):
    segment_id: str
    text: str
    start_time: float  # seconds into the recording
    end_time: float


class ReviewCheck(BaseModel):
    check: ReviewCheckInfo
    status: CheckStatus
    reason: str
    expected_value: str | None
    actual_value: str | None
    confidence: ReviewConfidence
    evidence: list[ReviewEvidence]


class CallReview(BaseModel):
    call: ReviewCall
    lead: ReviewLead
    gate_decision: ReviewGateDecision | None  # None until the pipeline has decided
    checks: list[ReviewCheck]  # problems first: FAIL, LOW_CONFIDENCE, NOT_CHECKABLE, PASS; then critical, then code
