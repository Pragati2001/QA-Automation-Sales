"""
VerbatimCheckRunner: did the AGENT say every mandatory phrase?

Reads its rules from Check.configuration:

    {"required_phrases": ["this call is recorded for quality ..."],
     "match_threshold": 0.9}          # 0 < threshold <= 1

Only AGENT segments are searched (a customer repeating the phrase doesn't
count). The agent's words are joined across consecutive agent segments, so a
phrase split over several turns (or interrupted by the customer) still matches.
Each phrase is fuzzy-matched (see text_utils.best_window_match); PASS needs
every phrase at or above the threshold. Evidence lists the agent segment(s)
holding the best-matching words, also on FAIL (the closest wording), except
when nothing resembled the phrase, where there is no evidence to show.

Status meanings here:
  PASS           every required phrase matched
  FAIL           at least one phrase is missing / below the threshold
  NOT_CHECKABLE  no honest verdict possible: no agent speech in the transcript,
                 or the check has no usable phrases/threshold configured
                 (we never guess a script or a strictness)

The runner never modifies the call, check or segments. A high similarity is
not proof of identical wording (one inserted "not" barely moves the score),
so legally critical scripts need a high threshold.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.models import Call, Check, CheckType, TranscriptSegment
from app.schemas.check_result import CheckResult, CheckStatus, Evidence
from app.services.checks.base import CheckRunner
from app.services.checks.text_utils import WindowMatch, best_window_match, normalize_text

AGENT_SPEAKER = "AGENT"
REQUIRED_PHRASES_KEY = "required_phrases"
MATCH_THRESHOLD_KEY = "match_threshold"


class _InvalidConfig(Exception):
    pass


class VerbatimCheckRunner(CheckRunner):
    def run(
        self,
        call: Call,
        check: Check,
        segments: Sequence[TranscriptSegment],
        crm_fields: Mapping[str, Any] | None = None,
        retailer_plan: Mapping[str, Any] | None = None,
    ) -> CheckResult:
        if check.check_type is not CheckType.VERBATIM:
            raise ValueError(
                f"VerbatimCheckRunner cannot run check {check.code!r} of type {check.check_type}"
            )

        try:
            phrases, threshold = _read_config(check.configuration)
        except _InvalidConfig as e:
            return _result(check, CheckStatus.NOT_CHECKABLE, f"Check configuration is unusable: {e}")
        expected = " | ".join(phrases)

        agent = sorted((s for s in segments if _is_agent(s)), key=lambda s: s.start_time)
        words: list[str] = []
        owners: list[int] = []  # owners[i] = index in `agent` of the segment that said words[i]
        for idx, seg in enumerate(agent):
            seg_words = normalize_text(seg.text).split()
            words += seg_words
            owners += [idx] * len(seg_words)
        if not words:
            return _result(
                check, CheckStatus.NOT_CHECKABLE,
                "No agent speech in the transcript, so the required phrases cannot be checked",
                expected_value=expected,
            )

        matches = [best_window_match(p, words) for p in phrases]
        failed = [(p, m) for p, m in zip(phrases, matches) if m.similarity < threshold]

        used = sorted({owners[i] for m in matches for i in range(m.start, m.end)})
        evidence = [
            Evidence(segment_id=str(agent[i].id), start_time=agent[i].start_time,
                     end_time=agent[i].end_time, text=agent[i].text)
            for i in used
        ]
        asr = [agent[i].confidence for i in used if agent[i].confidence is not None]

        if failed:
            details = "; ".join(f"{_clip(p)!r} ({_shortfall(m, threshold)})" for p, m in failed)
            status = CheckStatus.FAIL
            reason = f"{len(failed)} of {len(phrases)} required phrase(s) not said by the agent: {details}"
        else:
            status = CheckStatus.PASS
            lowest = min(m.similarity for m in matches)
            reason = (
                f"All {len(phrases)} required phrase(s) said by the agent "
                f"(lowest similarity {lowest:.2f} >= threshold {threshold:.2f})"
            )
        actual = [_matched_text(words, m) for m in matches]
        return _result(
            check, status, reason,
            expected_value=expected,
            actual_value=" | ".join(actual) if any(actual) else None,
            asr_confidence=min(asr) if asr else None,
            evidence=evidence,
        )


def _read_config(config: Mapping[str, Any] | None) -> tuple[list[str], float]:
    config = config or {}
    phrases = config.get(REQUIRED_PHRASES_KEY)
    if (
        not isinstance(phrases, list)
        or not phrases
        or not all(isinstance(p, str) and normalize_text(p) for p in phrases)
    ):
        raise _InvalidConfig(f"{REQUIRED_PHRASES_KEY!r} must be a non-empty list of non-blank strings")
    threshold = config.get(MATCH_THRESHOLD_KEY)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise _InvalidConfig(f"{MATCH_THRESHOLD_KEY!r} must be a number greater than 0 and at most 1")
    return phrases, float(threshold)


def _is_agent(segment: TranscriptSegment) -> bool:
    return (segment.speaker or "").strip().upper() == AGENT_SPEAKER


def _shortfall(match: WindowMatch, threshold: float) -> str:
    if match.start == match.end:  # nothing in the agent's speech resembled the phrase
        return "no similar wording found"
    return f"best similarity {match.similarity:.2f} < {threshold:.2f}"


def _matched_text(words: Sequence[str], match: WindowMatch) -> str:
    return " ".join(words[match.start : match.end])  # normalized form of what the agent said


def _clip(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _result(check: Check, status: CheckStatus, reason: str, **fields: Any) -> CheckResult:
    return CheckResult(
        check_id=check.code,
        check_version=check.library.version,
        check_type=check.check_type.value,
        critical=check.critical,
        status=status,
        reason=reason,
        **fields,
    )
