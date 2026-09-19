"""
ScoringService: run a call's transcript against the check library that was live when the call
started, and persist one CheckResultRecord per evaluated check.

    * The library comes from RetailerRepository.get_active_check_library(retailer, call_started_at).
      No start time, no active library, or overlapping libraries are reported as a `problem`
      (the gate sends those to QA review). It never falls back to another version.
    * VERBATIM checks run on VerbatimCheckRunner, FACTUAL on FactualCheckRunner, BEHAVIOUR on
      BehaviourCheckRunner (the dispatch table below). Behaviour results are persisted like any
      other (so the review page shows the coaching notes) but the gate ignores them.
    * A runner that blows up on one check does not abort the others: that check is stored as
      NOT_CHECKABLE with a "Scoring error" reason and the error is reported, so the gate
      routes the call to QA review instead of trusting a partial score.
    * Existing results are never replaced: scoring a call twice raises CallAlreadyScoredError.

This service flushes but does not commit: the caller commits the results together with the
gate decision, so a call never ends up half-scored.
"""

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Call, Check, CheckLibrary, CheckResultRecord, CheckType
from app.repositories.retailer_repository import AmbiguousCheckLibraryError, RetailerRepository
from app.schemas.check_result import CheckResult, CheckStatus
from app.services.checks.base import CheckRunner
from app.services.checks.behaviour_check import BehaviourCheckRunner
from app.services.checks.factual_check import FactualCheckRunner
from app.services.checks.verbatim_runner import VerbatimCheckRunner

logger = logging.getLogger(__name__)

RUNNERS: dict[CheckType, CheckRunner] = {
    CheckType.VERBATIM: VerbatimCheckRunner(),
    CheckType.FACTUAL: FactualCheckRunner(),
    CheckType.BEHAVIOUR: BehaviourCheckRunner(),
}


class CallAlreadyScoredError(Exception):
    """The call already has check results; they are audit history and are never overwritten."""


@dataclass
class ScoringOutcome:
    library: CheckLibrary | None = None
    problem: str | None = None  # why no library could be applied (None when one was)
    results: list[CheckResult] = field(default_factory=list)  # in check order, for the gate
    records: list[CheckResultRecord] = field(default_factory=list)  # the persisted rows
    errors: list[str] = field(default_factory=list)  # checks that hit a scoring error
    skipped: list[str] = field(default_factory=list)  # check codes with no runner (BEHAVIOUR)


def score_call(
    session: Session, call_id: int, runners: Mapping[CheckType, CheckRunner] | None = None
) -> ScoringOutcome:
    runners = RUNNERS if runners is None else runners
    call = session.get(Call, call_id)
    if call is None:
        raise ValueError(f"Call {call_id} not found")
    if session.scalar(select(CheckResultRecord.id).where(CheckResultRecord.call_id == call_id).limit(1)):
        raise CallAlreadyScoredError(f"Call {call_id} already has check results")
    if call.transcript is None:
        raise ValueError(f"Call {call_id} has no transcript to score")

    library, problem = _select_library(session, call)
    if library is None:
        return ScoringOutcome(problem=problem)

    outcome = ScoringOutcome(library=library)
    segments = call.transcript.segments
    crm_fields = call.lead.crm_fields
    for check in library.checks:
        runner = runners.get(check.check_type)
        if runner is None:
            outcome.skipped.append(check.code)
            continue
        try:
            # No retailer plan / rate card source exists yet, so checks that read
            # RETAILER_PLAN.* resolve to "expected value not available" (NOT_CHECKABLE).
            result = runner.run(call, check, segments, crm_fields=crm_fields, retailer_plan=None)
        except Exception as e:
            logger.exception("Scoring error on check %s for call %s", check.code, call_id)
            detail = f"{type(e).__name__}: {e}"
            outcome.errors.append(f"{check.code}: {detail}")
            result = CheckResult(
                check_id=check.code, check_version=library.version, check_type=check.check_type.value,
                critical=check.critical, status=CheckStatus.NOT_CHECKABLE, reason=f"Scoring error: {detail}",
            )
        record = _to_record(call.id, check, result)
        session.add(record)
        outcome.results.append(result)
        outcome.records.append(record)
    session.flush()
    return outcome


def _select_library(session: Session, call: Call) -> tuple[CheckLibrary | None, str | None]:
    if call.call_started_at is None:
        return None, "the call start time is not set, so the applicable check library version cannot be determined"
    retailer_id = call.lead.retailer_id
    try:
        library = RetailerRepository(session).get_active_check_library(retailer_id, call.call_started_at)
    except AmbiguousCheckLibraryError as e:
        return None, f"more than one check library is active for this call date ({e})"
    if library is None:
        return None, f"no check library is active for retailer {retailer_id} at {call.call_started_at.isoformat()}"
    return library, None


def _to_record(call_id: int, check: Check, result: CheckResult) -> CheckResultRecord:
    return CheckResultRecord(
        call_id=call_id,
        check_id=check.id,
        check_code=result.check_id,
        check_version=result.check_version,
        check_type=result.check_type,
        critical=result.critical,
        status=result.status,
        reason=result.reason,
        asr_confidence=result.asr_confidence,
        extraction_confidence=result.extraction_confidence,
        rule_confidence=result.rule_confidence,
        expected_value=result.expected_value,
        actual_value=result.actual_value,
        evidence=[asdict(e) for e in result.evidence],
    )
