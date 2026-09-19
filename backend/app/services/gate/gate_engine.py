"""
GateEngine: turns a call's check results into a routing decision. Pure: no database, no I/O.

    critical FAIL                                    -> HELD        (team leader queue)
    else any of: a LOW_CONFIDENCE result,
                 a critical NOT_CHECKABLE result,
                 no usable check library,
                 a scoring/system error,
                 nothing was evaluated at all        -> QA_REVIEW   (a human decides)
    else                                             -> AUTO_PASSED

A critical FAIL wins over the QA_REVIEW triggers: a definite failure is held even if something
else is also uncertain (the summary mentions both). Uncertainty is never turned into a pass.

BEHAVIOUR results are ignored: they are coaching notes and do not block a sale. A non-critical
NOT_CHECKABLE (or non-critical FAIL) is reported in the summary but does not stop an auto-pass.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.models import GateStatus
from app.schemas.check_result import CheckResult, CheckStatus

BEHAVIOUR = "BEHAVIOUR"


@dataclass(frozen=True)
class GateOutcome:
    status: GateStatus
    reason: str  # the reasoning summary


class GateEngine:
    def decide(
        self,
        results: Sequence[CheckResult],
        *,
        library_problem: str | None = None,
        errors: Sequence[str] = (),
    ) -> GateOutcome:
        """`library_problem`: why no check library could be applied (None if one was).
        `errors`: scoring/system errors that happened while producing `results`."""
        relevant = [r for r in results if r.check_type != BEHAVIOUR]

        critical_fails = _codes(relevant, CheckStatus.FAIL, critical_only=True)
        low_confidence = _codes(relevant, CheckStatus.LOW_CONFIDENCE)
        critical_unchecked = _codes(relevant, CheckStatus.NOT_CHECKABLE, critical_only=True)

        review: list[str] = []
        if library_problem:
            review.append(library_problem)
        review += [f"scoring error: {e}" for e in errors]
        if low_confidence:
            review.append(f"low confidence on {_join(low_confidence)}")
        if critical_unchecked:
            review.append(f"critical check(s) could not be checked: {_join(critical_unchecked)}")
        if not relevant and not library_problem and not errors:
            review.append("no checks were evaluated for this call")

        notes = _notes(relevant)
        if critical_fails:
            text = f"Held: critical check(s) failed: {_join(critical_fails)}."
            if review:
                text += f" Also needs QA review: {'; '.join(review)}."
            return GateOutcome(GateStatus.HELD, f"{text} {notes}".strip())
        if review:
            return GateOutcome(GateStatus.QA_REVIEW, f"Sent to QA review: {'; '.join(review)}. {notes}".strip())
        return GateOutcome(GateStatus.AUTO_PASSED, f"Auto-passed: every critical check passed. {notes}".strip())


def _codes(results: Sequence[CheckResult], status: CheckStatus, *, critical_only: bool = False) -> list[str]:
    return [r.check_id for r in results if r.status is status and (r.critical or not critical_only)]


def _join(codes: Sequence[str]) -> str:
    return ", ".join(codes)


def _notes(relevant: Sequence[CheckResult]) -> str:
    """Counts, plus the non-critical items that were deliberately not allowed to block."""
    if not relevant:
        return "No checks were evaluated."
    counts = Counter(r.status for r in relevant)
    parts = [f"{counts[s]} {s.value}" for s in CheckStatus if counts[s]]
    text = f"Checks evaluated: {len(relevant)} ({', '.join(parts)})."
    non_blocking = [
        f"{r.check_id} ({r.status.value})"
        for r in relevant
        if not r.critical and r.status in (CheckStatus.FAIL, CheckStatus.NOT_CHECKABLE)
    ]
    if non_blocking:
        text += f" Non-critical, not blocking: {_join(non_blocking)}."
    return text
