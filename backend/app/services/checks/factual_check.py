"""
FactualCheckRunner: does what was said on the call match the authoritative data?

Flow: resolve the expected value -> extract the spoken value -> normalize both -> compare.

Reads its rules from Check.configuration:

    {"field": "email",                       # one of extraction.FIELD_SPECS
     "source_of_truth": "CRM.email",         # or "RETAILER_PLAN.<path>"
     "speaker": "agent",                     # optional: override the field's default speaker
     "confidence_threshold": 0.7,            # optional: extraction confidence below this -> LOW_CONFIDENCE
     "tolerance": 0.01,                      # optional: numeric fields (rates, money, speeds)
     "fuzzy_threshold": 0.85,                # optional: name / address / modem_model similarity (defaults 0.85 / 0.8 / 0.95)
     "context_keywords": ["peak"]}           # optional: only segments mentioning one of these count

Status meanings here:
  PASS            normalized values match
  FAIL            normalized values differ
  LOW_CONFIDENCE  the value was found but we are not sure of it: ASR confidence under the
                  threshold, several conflicting values said, or a spoken value we could not read
  NOT_CHECKABLE   no honest verdict possible: unusable config, no expected value from the
                  source of truth, the value spoken but redacted in our transcript, or the
                  value not found in the transcript (the reasons say which)

`extraction_confidence` (how sure we are of the transcript value) and `rule_confidence`
(how the comparison was made: 1.0 for exact/numeric, the similarity for fuzzy text) are
different things and are reported separately.

There are two entry points with the same behaviour. run() is the CheckRunner interface, the same
signature VerbatimCheckRunner has; evaluate() takes the already-unpacked values (check id, version,
criticality, config) and is what run() calls.
"""

import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any

from app.models import Call, Check, CheckType, TranscriptSegment
from app.schemas.check_result import CheckResult, CheckStatus, Evidence
from app.services.checks.base import CheckRunner
from app.services.checks.extraction import FIELD_SPECS, extract_value
from app.services.checks.normalization import normalize_field
from app.services.checks.source_resolver import resolve_expected_value

DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_TOLERANCE = 0.01
DEFAULT_FUZZY_THRESHOLD = {"name": 0.85, "address": 0.8, "modem": 0.95}
_NUMERIC_KINDS = {"rate", "money", "speed"}
_FUZZY_KINDS = set(DEFAULT_FUZZY_THRESHOLD)


class FactualCheckRunner(CheckRunner):
    def run(
        self,
        call: Call,
        check: Check,
        segments: Sequence[TranscriptSegment],
        crm_fields: Mapping[str, Any] | None = None,
        retailer_plan: Mapping[str, Any] | None = None,
    ) -> CheckResult:
        if check.check_type is not CheckType.FACTUAL:
            raise ValueError(
                f"FactualCheckRunner cannot run check {check.code!r} of type {check.check_type}"
            )
        return self.evaluate(
            check_id=check.code,
            check_version=check.library.version,
            critical=check.critical,
            config=check.configuration or {},
            segments=segments,
            crm_fields=crm_fields,
            retailer_plan=retailer_plan,
        )

    def evaluate(
        self,
        check_id: str,
        check_version: int,
        critical: bool,
        config: Mapping[str, Any],
        segments: Sequence[TranscriptSegment],
        crm_fields: Mapping[str, Any] | None,
        retailer_plan: Mapping[str, Any] | None,
    ) -> CheckResult:
        def result(status: CheckStatus, reason: str, **fields: Any) -> CheckResult:
            return CheckResult(
                check_id=check_id, check_version=check_version, check_type=CheckType.FACTUAL.value,
                critical=critical, status=status, reason=reason, **fields,
            )

        config = dict(config or {})
        try:
            field_name, kind, options = _read_config(config)
        except _InvalidConfig as e:
            return result(CheckStatus.NOT_CHECKABLE, f"Check configuration is unusable: {e}")
        source = config["source_of_truth"]

        expected_raw = resolve_expected_value(source, crm_fields, retailer_plan)
        if expected_raw is None:
            return result(
                CheckStatus.NOT_CHECKABLE,
                f"Expected value not available: {source!r} is missing or empty in the source of truth",
            )
        expected = str(expected_raw)

        extraction = extract_value(segments, field_name, config)
        by_id = {str(s.id): s for s in segments}

        def evidence(*segment_ids: str | None) -> list[Evidence]:
            picked = [by_id[i] for i in dict.fromkeys(segment_ids) if i in by_id]
            return [Evidence(str(s.id), s.start_time, s.end_time, s.text)
                    for s in sorted(picked, key=lambda s: s.start_time)]

        if extraction.redacted:
            return result(
                CheckStatus.NOT_CHECKABLE,
                f"{field_name} value spoken but redacted in source transcript; "
                f"verify manually against the expected value {expected!r}",
                expected_value=expected, evidence=evidence(extraction.source_segment_id),
            )
        if extraction.value is None:
            return result(
                CheckStatus.NOT_CHECKABLE,
                f"{field_name} value not found in transcript (expected {expected!r})",
                expected_value=expected,
            )

        actual = extraction.value
        if extraction.conflict:
            said = " | ".join(v for v, _ in extraction.alternatives)
            return result(
                CheckStatus.LOW_CONFIDENCE,
                f"Conflicting {field_name} values were said on the call ({said}); cannot pick one",
                expected_value=expected, actual_value=said,
                extraction_confidence=extraction.confidence,
                evidence=evidence(*(sid for _, sid in extraction.alternatives)),
            )
        if extraction.confidence is not None and extraction.confidence < options["confidence_threshold"]:
            return result(
                CheckStatus.LOW_CONFIDENCE,
                f"Transcript confidence {extraction.confidence:.2f} is below the "
                f"{options['confidence_threshold']:.2f} threshold for {field_name} {actual!r}",
                expected_value=expected, actual_value=actual,
                extraction_confidence=extraction.confidence,
                evidence=evidence(extraction.source_segment_id),
            )

        expected_norm = normalize_field(kind, expected_raw)
        if expected_norm is None:
            return result(
                CheckStatus.NOT_CHECKABLE,
                f"Expected {field_name} value {expected!r} could not be interpreted",
                expected_value=expected, actual_value=actual,
                extraction_confidence=extraction.confidence,
                evidence=evidence(extraction.source_segment_id),
            )
        actual_norm = normalize_field(kind, actual)
        if actual_norm is None:
            return result(
                CheckStatus.LOW_CONFIDENCE,
                f"Could not interpret the spoken {field_name} value {actual!r}",
                expected_value=expected, actual_value=actual,
                extraction_confidence=extraction.confidence,
                evidence=evidence(extraction.source_segment_id),
            )

        matched, rule_confidence = _compare(kind, expected_norm, actual_norm, options)
        if matched:
            status = CheckStatus.PASS
            reason = f"{field_name} matches: said {actual!r}, expected {expected!r}"
        else:
            status = CheckStatus.FAIL
            reason = (f"{field_name} mismatch: said {actual!r} (normalized {actual_norm!r}), "
                      f"expected {expected!r} (normalized {expected_norm!r})")
        return result(
            status, reason,
            expected_value=expected, actual_value=actual,
            extraction_confidence=extraction.confidence, rule_confidence=rule_confidence,
            evidence=evidence(extraction.source_segment_id),
        )


class _InvalidConfig(Exception):
    pass


def _read_config(config: dict[str, Any]) -> tuple[str, str, dict[str, float]]:
    field_name = config.get("field")
    if not isinstance(field_name, str) or field_name.strip().lower() not in FIELD_SPECS:
        raise _InvalidConfig(f"'field' must be one of {sorted(FIELD_SPECS)}")
    if not isinstance(config.get("source_of_truth"), str) or not config["source_of_truth"].strip():
        raise _InvalidConfig("'source_of_truth' must be a path like 'CRM.email' or 'RETAILER_PLAN.rate'")
    kind = FIELD_SPECS[field_name.strip().lower()].kind

    def number(key: str, default: float, low: float, high: float) -> float:
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise _InvalidConfig(f"{key!r} must be a number between {low} and {high}")
        return float(value)

    return field_name.strip().lower(), kind, {
        "confidence_threshold": number("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD, 0, 1),
        "tolerance": number("tolerance", DEFAULT_TOLERANCE, 0, float("inf")),
        "fuzzy_threshold": number("fuzzy_threshold", DEFAULT_FUZZY_THRESHOLD.get(kind, 1.0), 0.01, 1),
    }


def _compare(kind: str, expected: Any, actual: Any, options: dict[str, float]) -> tuple[bool, float]:
    """-> (matched, rule_confidence): 1.0 for exact/numeric, the similarity for fuzzy text."""
    if kind in _NUMERIC_KINDS:
        return abs(expected - actual) <= options["tolerance"], 1.0
    if kind in _FUZZY_KINDS:
        return fuzzy_compare(expected, actual, options["fuzzy_threshold"])
    return expected == actual, 1.0


def fuzzy_compare(expected: str, actual: str, threshold: float) -> tuple[bool, float]:
    """Similarity ratio, but every number in the two strings must be identical: one
    digit off in a street number or model number is a different thing, however similar
    the text looks ('12 smith st' vs '21 smith st')."""
    ratio = SequenceMatcher(None, expected, actual, autojunk=False).ratio()
    same_numbers = re.findall(r"\d+", expected) == re.findall(r"\d+", actual)
    return same_numbers and ratio >= threshold, ratio
