"""
CheckRunner abstraction.

One runner per check type (Verbatim now, Factual/Behaviour later). Every
runner takes the same inputs and returns the evidence-first CheckResult, so
the scoring layer can dispatch on Check.check_type without knowing how any
runner works. Runners are pure: they never modify the call, check or segments.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from app.models import Call, Check, TranscriptSegment
from app.schemas.check_result import CheckResult


class CheckRunner(ABC):
    @abstractmethod
    def run(
        self,
        call: Call,
        check: Check,
        segments: Sequence[TranscriptSegment],
        crm_fields: Mapping[str, Any] | None = None,
        retailer_plan: Mapping[str, Any] | None = None,
    ) -> CheckResult:
        """Evaluate `check` against the call's transcript segments.

        `crm_fields` / `retailer_plan` are the reference data factual checks
        compare against; runners that don't need them ignore them.
        """
