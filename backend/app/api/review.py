from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Call, CheckResultRecord
from app.schemas.check_result import CheckStatus
from app.schemas.review import (
    CallReview,
    ReviewCall,
    ReviewCheck,
    ReviewCheckInfo,
    ReviewConfidence,
    ReviewEvidence,
    ReviewGateDecision,
    ReviewLead,
    ReviewRetailer,
)

router = APIRouter(prefix="/calls", tags=["review"])

# Problems first, so the TL sees what needs attention at the top.
_STATUS_ORDER = {
    CheckStatus.FAIL: 0,
    CheckStatus.LOW_CONFIDENCE: 1,
    CheckStatus.NOT_CHECKABLE: 2,
    CheckStatus.PASS: 3,
}


@router.get("/{call_id}/review", response_model=CallReview)
def get_call_review(call_id: int, db: Session = Depends(get_db)):
    """Everything the TL review page needs, in one payload."""
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, f"Call {call_id} not found")

    records = db.scalars(
        select(CheckResultRecord)
        .where(CheckResultRecord.call_id == call_id)
        .options(selectinload(CheckResultRecord.check))
    ).all()
    # Deterministic order that doesn't depend on insertion order or the database.
    records = sorted(records, key=lambda r: (_STATUS_ORDER[r.status], not r.critical, r.check_code, r.id))

    lead = call.lead
    return CallReview(
        call=ReviewCall.model_validate(
            {
                "id": call.id,
                "status": call.status,
                "call_started_at": call.call_started_at,
                "created_at": call.created_at,
                "updated_at": call.updated_at,
                "recording": call.recording,
            },
            from_attributes=True,
        ),
        lead=ReviewLead(
            id=lead.id,
            external_lead_id=lead.external_lead_id,
            retailer=ReviewRetailer(id=lead.retailer.id, code=lead.retailer.code, name=lead.retailer.name),
        ),
        gate_decision=ReviewGateDecision.model_validate(call.gate_decision) if call.gate_decision else None,
        checks=[_to_review_check(r) for r in records],
    )


def _to_review_check(record: CheckResultRecord) -> ReviewCheck:
    confidences = [
        c for c in (record.asr_confidence, record.extraction_confidence, record.rule_confidence) if c is not None
    ]
    return ReviewCheck(
        check=ReviewCheckInfo(
            id=record.check_id,
            code=record.check_code,
            name=record.check.name,
            type=record.check_type,
            critical=record.critical,
            version=record.check_version,
        ),
        status=record.status,
        reason=record.reason,
        expected_value=record.expected_value,
        actual_value=record.actual_value,
        confidence=ReviewConfidence(
            asr=record.asr_confidence,
            extraction=record.extraction_confidence,
            rule=record.rule_confidence,
            overall=min(confidences) if confidences else None,
        ),
        evidence=[ReviewEvidence(**item) for item in record.evidence],
    )
