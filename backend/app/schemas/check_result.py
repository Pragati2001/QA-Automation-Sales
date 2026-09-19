"""
The evidence-first contract every check implementation (Verbatim, Factual,
Behaviour) must return. Built first, deliberately, so no check can be
written that produces a bare PASS/FAIL with no explanation attached.

Status meanings:
  PASS            - criterion satisfied
  FAIL            - criterion violated, expected vs actual are known
  LOW_CONFIDENCE  - system attempted the check but isn't sure -> QA review
  NOT_CHECKABLE   - required input data was missing/null, no honest verdict
                    possible -> QA review if the check is critical, else
                    logged as SKIPPED (see gate engine)
"""

from dataclasses import dataclass, field
from enum import Enum


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NOT_CHECKABLE = "NOT_CHECKABLE"


@dataclass
class Evidence:
    segment_id: str
    start_time: float  # seconds into the recording
    end_time: float
    text: str  # the actual transcript text supporting this result


@dataclass
class CheckResult:
    check_id: str
    check_version: int
    check_type: str  # "VERBATIM" | "FACTUAL" | "BEHAVIOUR"
    critical: bool
    status: CheckStatus
    reason: str

    # Confidence is decomposed, not a single fused number, per the
    # agreed architecture -- combine these in the gate/scoring layer,
    # don't hide the components.
    asr_confidence: float | None = None
    extraction_confidence: float | None = None
    rule_confidence: float | None = None

    expected_value: str | None = None
    actual_value: str | None = None

    evidence: list[Evidence] = field(default_factory=list)

    def overall_confidence(self) -> float | None:
        """Simple minimum-of-components rule for the build-day version.
        Do not over-engineer this into a weighted fusion model yet --
        the point is that low confidence anywhere should suppress a
        confident-looking final verdict.
        """
        components = [
            c for c in (self.asr_confidence, self.extraction_confidence, self.rule_confidence)
            if c is not None
        ]
        return min(components) if components else None
