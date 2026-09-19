"""
BehaviourCheckRunner: coaching signals from the transcript's timing and wording.

Behaviour checks never block a sale. Their result is PASS (with a coaching note in `reason`
when something was flagged) or NOT_CHECKABLE (nothing to evaluate); never FAIL. Whether a
check is critical comes from the Check row and is passed straight through, not decided here.
The gate ignores BEHAVIOUR results as well, so they can never cause HELD or QA_REVIEW.

Each Check picks one signal with config["signal"] (default: the check's code, so a check
called "dead_air" needs no "signal"). Thresholds come from Check.configuration:

  dead_air            {"threshold_seconds": 10}
                      A silence of at least this many seconds between the end of the speech so
                      far and the next segment. Required: there is no sensible default.
  filler_ratio        {"max_ratio": 0.05, "filler_words": ["um", "uh"], "speaker": "agent"}
                      Share of the speaker's words that are filler words; flagged above max_ratio.
                      max_ratio is required. Defaults: filler_words = um/umm/uh/uhh/er/erm/ah/hmm
                      (the unambiguous ones), speaker = "agent" ("customer" and "any" also work).
  overlapping_speech  {"overlap_tolerance_seconds": 0.5}
                      Segments sorted by start time: a segment that starts more than the tolerance
                      before the previous one ended (interruption / crosstalk). Default 0.5.

Every flagged instance contributes evidence (segment id, times and text; both segments for an
overlap or a silence), capped at MAX_EVIDENCE_INSTANCES so a very noisy call stays readable.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.models import Call, Check, CheckType, TranscriptSegment
from app.schemas.check_result import CheckResult, CheckStatus, Evidence
from app.services.checks.base import CheckRunner
from app.services.checks.text_utils import normalize_text

SIGNALS = ("dead_air", "filler_ratio", "overlapping_speech")
DEFAULT_OVERLAP_TOLERANCE_SECONDS = 0.5
DEFAULT_FILLER_WORDS = ("um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm")
MAX_EVIDENCE_INSTANCES = 10


class _InvalidConfig(Exception):
    pass


class BehaviourCheckRunner(CheckRunner):
    def run(
        self,
        call: Call,
        check: Check,
        segments: Sequence[TranscriptSegment],
        crm_fields: Mapping[str, Any] | None = None,
        retailer_plan: Mapping[str, Any] | None = None,
    ) -> CheckResult:
        if check.check_type is not CheckType.BEHAVIOUR:
            raise ValueError(
                f"BehaviourCheckRunner cannot run check {check.code!r} of type {check.check_type}"
            )
        return self.evaluate(
            check_id=check.code,
            check_version=check.library.version,
            critical=check.critical,
            config=check.configuration or {},
            segments=segments,
        )

    def evaluate(
        self,
        check_id: str,
        check_version: int,
        critical: bool,
        config: Mapping[str, Any],
        segments: Sequence[TranscriptSegment],
    ) -> CheckResult:
        def result(status: CheckStatus, reason: str, **fields: Any) -> CheckResult:
            return CheckResult(
                check_id=check_id, check_version=check_version, check_type=CheckType.BEHAVIOUR.value,
                critical=critical, status=status, reason=reason, **fields,
            )

        config = dict(config or {})
        signal = config.get("signal") or check_id
        try:
            if signal not in SIGNALS:
                raise _InvalidConfig(f"'signal' must be one of {list(SIGNALS)}, not {signal!r}")
            ordered = sorted(segments, key=lambda s: s.start_time)  # a sorted copy; the input is untouched
            outcome = _SIGNAL_FUNCTIONS[signal](ordered, config)
        except _InvalidConfig as e:
            return result(CheckStatus.NOT_CHECKABLE, f"Check configuration is unusable: {e}")
        if outcome is None:
            return result(
                CheckStatus.NOT_CHECKABLE,
                f"Not enough timed transcript data to evaluate {signal.replace('_', ' ')}",
            )

        note, expected, actual, flagged = outcome
        evidence = [
            Evidence(str(s.id), s.start_time, s.end_time, s.text)
            for s in _unique(flagged)[: MAX_EVIDENCE_INSTANCES * 2]
        ]
        return result(
            CheckStatus.PASS, note, expected_value=expected, actual_value=actual, evidence=evidence
        )


# --- signals: each returns (reason, expected, actual, flagged segments) or None if there is nothing to evaluate ---

def _dead_air(ordered: list[TranscriptSegment], config: dict):
    threshold = _number(config, "threshold_seconds", default=None, minimum=0, exclusive=True)
    if len(ordered) < 2:
        return None
    latest = ordered[0]  # the segment that has ended latest so far (segments may overlap)
    gaps: list[tuple[float, TranscriptSegment, TranscriptSegment]] = []
    for segment in ordered[1:]:
        gaps.append((segment.start_time - latest.end_time, latest, segment))
        if segment.end_time > latest.end_time:
            latest = segment
    longest = max(gap for gap, _, _ in gaps)
    flagged = [(gap, before, after) for gap, before, after in gaps if gap >= threshold]
    expected = f"less than {_fmt(threshold)} s of silence"
    actual = f"longest silence {_fmt(max(longest, 0))} s"
    if not flagged:
        return f"No dead air of {_fmt(threshold)} s or more (longest silence {_fmt(max(longest, 0))} s).", expected, actual, []
    worst = max(flagged, key=lambda item: item[0])
    note = (
        f"Coaching note: {len(flagged)} silence(s) of {_fmt(threshold)} s or more; the longest was "
        f"{_fmt(worst[0])} s, between {_clock(worst[1].end_time)} and {_clock(worst[2].start_time)}."
    )
    ranked = sorted(flagged, key=lambda item: -item[0])[:MAX_EVIDENCE_INSTANCES]
    return note, expected, actual, [s for _, before, after in sorted(ranked, key=lambda i: i[1].end_time) for s in (before, after)]


def _filler_ratio(ordered: list[TranscriptSegment], config: dict):
    max_ratio = _number(config, "max_ratio", default=None, minimum=0, maximum=1)
    fillers = _filler_words(config)
    speaker = str(config.get("speaker") or "agent").strip().lower()
    if speaker not in ("agent", "customer", "any"):
        raise _InvalidConfig("'speaker' must be 'agent', 'customer' or 'any'")

    total = 0
    per_segment: list[tuple[int, TranscriptSegment]] = []
    for segment in ordered:
        if speaker != "any" and (segment.speaker or "").strip().lower() != speaker:
            continue
        words = normalize_text(segment.text).split()
        total += len(words)
        count = sum(1 for word in words if word in fillers)
        if count:
            per_segment.append((count, segment))
    if total == 0:
        return None

    used = sum(count for count, _ in per_segment)
    ratio = used / total
    expected = f"at most {max_ratio:.1%} filler words"
    actual = f"{ratio:.1%} ({used} of {total} words)"
    if ratio <= max_ratio:
        return f"Filler words are within the limit: {ratio:.1%} of {speaker} speech.", expected, actual, []
    worst = sorted(per_segment, key=lambda item: (-item[0], item[1].start_time))[:MAX_EVIDENCE_INSTANCES]
    note = (
        f"Coaching note: filler words are {ratio:.1%} of {speaker} speech "
        f"({used} of {total} words), above the {max_ratio:.1%} limit."
    )
    return note, expected, actual, [segment for _, segment in sorted(worst, key=lambda item: item[1].start_time)]


def _overlapping_speech(ordered: list[TranscriptSegment], config: dict):
    tolerance = _number(
        config, "overlap_tolerance_seconds", default=DEFAULT_OVERLAP_TOLERANCE_SECONDS, minimum=0
    )
    if len(ordered) < 2:
        return None
    overlaps = [
        (previous.end_time - segment.start_time, previous, segment)
        for previous, segment in zip(ordered, ordered[1:])
        if previous.end_time - segment.start_time > tolerance
    ]
    longest = max((amount for amount, _, _ in overlaps), default=0.0)
    expected = f"overlaps of at most {_fmt(tolerance)} s"
    if not overlaps:
        return "No overlapping speech beyond the tolerance.", expected, "no overlaps", []
    actual = f"{len(overlaps)} overlap(s), longest {_fmt(longest)} s"
    first = overlaps[0]
    note = (
        f"Coaching note: {len(overlaps)} overlap(s) of more than {_fmt(tolerance)} s "
        f"(longest {_fmt(longest)} s); the first at {_clock(first[2].start_time)} "
        f"({first[1].speaker} and {first[2].speaker} talking at once)."
    )
    return note, expected, actual, [s for _, before, after in overlaps[:MAX_EVIDENCE_INSTANCES] for s in (before, after)]


_SIGNAL_FUNCTIONS = {
    "dead_air": _dead_air,
    "filler_ratio": _filler_ratio,
    "overlapping_speech": _overlapping_speech,
}


# --- helpers -----------------------------------------------------------------------------------

def _number(config: dict, key: str, *, default, minimum=None, maximum=None, exclusive=False) -> float:
    value = config.get(key, default)
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    if ok and minimum is not None:
        ok = value > minimum if exclusive else value >= minimum
    if ok and maximum is not None:
        ok = value <= maximum
    if not ok:
        raise _InvalidConfig(f"{key!r} must be set to a number" + _range(minimum, maximum, exclusive))
    return float(value)


def _range(minimum, maximum, exclusive) -> str:
    if minimum is not None and maximum is not None:
        return f" between {minimum} and {maximum}"
    if minimum is not None:
        return f" {'above' if exclusive else 'of at least'} {minimum}"
    return ""


def _filler_words(config: dict) -> set[str]:
    words = config.get("filler_words", DEFAULT_FILLER_WORDS)
    if not isinstance(words, (list, tuple)) or not words:
        raise _InvalidConfig("'filler_words' must be a non-empty list of single words")
    normalized = [normalize_text(w).split() if isinstance(w, str) else [] for w in words]
    if any(len(parts) != 1 for parts in normalized):
        raise _InvalidConfig("'filler_words' must be a non-empty list of single words")
    return {parts[0] for parts in normalized}


def _unique(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """De-duplicate by segment (a segment can be part of several flagged pairs), keeping order."""
    seen: set[Any] = set()
    unique = []
    for segment in segments:
        if id(segment) not in seen:
            seen.add(id(segment))
            unique.append(segment)
    return unique


def _fmt(seconds: float) -> str:
    return f"{seconds:.1f}".rstrip("0").rstrip(".") if seconds != int(seconds) else str(int(seconds))


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"

