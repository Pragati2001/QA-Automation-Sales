"""
GateService: run the gate engine on a call's scoring outcome and persist the decision.

One GateDecision per call, and it is audit history: if the call already has one this raises
GateDecisionExistsError instead of replacing it. Like the scoring service it flushes but does
not commit, so the decision, the check results and the call's final status commit together.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GateDecision
from app.services.gate.gate_engine import GateEngine
from app.services.scoring.scoring_service import ScoringOutcome


class GateDecisionExistsError(Exception):
    """The call already has a gate decision; it is never overwritten."""


def decide_call(
    session: Session, call_id: int, scoring: ScoringOutcome, engine: GateEngine | None = None
) -> GateDecision:
    if session.scalar(select(GateDecision.id).where(GateDecision.call_id == call_id)):
        raise GateDecisionExistsError(f"Call {call_id} already has a gate decision")

    outcome = (engine or GateEngine()).decide(
        scoring.results, library_problem=scoring.problem, errors=scoring.errors
    )
    decision = GateDecision(
        call_id=call_id,
        status=outcome.status,
        reason=outcome.reason,
        decided_at=datetime.now(timezone.utc),
        check_library_id=scoring.library.id if scoring.library else None,
        check_library_version=scoring.library.version if scoring.library else None,
    )
    session.add(decision)
    session.flush()
    return decision
