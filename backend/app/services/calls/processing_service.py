"""
ProcessingService is the one place that knows this pipeline currently
runs on FastAPI BackgroundTasks. The API layer calls process_call();
it must never call transcription/scoring/gate services directly.

Swap path: replace the call site in api/calls.py from
    background_tasks.add_task(processing_service.process_call, call_id)
to
    celery_app.send_task("process_call", args=[call_id])
without touching anything below this function.

Pipeline: transcribe -> score -> gate. The check results, the gate decision and the
call's final status are committed together in one transaction, so a call is never left
half-scored, and the call only becomes COMPLETED once all of it has succeeded.

Call status afterwards:
  COMPLETED  the pipeline ran to the end and a decision was recorded (which may be HELD or
             QA_REVIEW: "routed to a human" is a successful outcome, e.g. no check library).
  FAILED     something went wrong. A scoring error on a check still records a QA_REVIEW decision
             (so a human sees the call) and marks it FAILED. Any other unexpected error marks it
             FAILED with no decision and re-raises. Nothing that failed is ever AUTO_PASSED.
"""

import logging

from app.db import SessionLocal
from app.models import Call, CallStatus
from app.services.gate import gate_service
from app.services.scoring import scoring_service
from app.services.storage.audio_storage import get_audio_storage
from app.services.transcription.transcription_service import (
    get_transcription_provider,
    transcribe_call,
)

logger = logging.getLogger(__name__)


def process_call(call_id: str) -> None:
    call_pk = int(call_id)
    with SessionLocal() as session:
        try:
            transcribe_call(session, call_pk, get_transcription_provider(), get_audio_storage())
            # Steps still to be built: redaction_service.redact(call_id)

            call = session.get(Call, call_pk)
            if call.gate_decision is not None:
                # Already fully processed (e.g. the task ran twice): audit history is not redone.
                logger.info("Call %s already has a gate decision; nothing to do", call_pk)
                return

            scoring = scoring_service.score_call(session, call_pk)
            decision = gate_service.decide_call(session, call_pk, scoring)
            call.status = CallStatus.FAILED if scoring.errors else CallStatus.COMPLETED
            session.commit()
            logger.info("Call %s: %s (%s)", call_pk, decision.status.value, call.status.value)
        except Exception:
            session.rollback()
            call = session.get(Call, call_pk)
            if call is not None:
                call.status = CallStatus.FAILED
                session.commit()
            raise
