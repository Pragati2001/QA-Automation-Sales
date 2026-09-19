"""
Transcript-level redaction: strip sensitive values out of segments BEFORE they are stored.

This is separate from extraction.py's placeholder awareness, which handles transcripts that
arrive already masked ("[EMAIL]") and reports "spoken but redacted"; here we are the ones
doing the masking, at the point where raw provider segments become TranscriptSegment rows.

    redact_segments(segments, patterns=None) -> (new segments, whether anything was redacted)

Rules:
  * Input segments are never modified; the result holds new dicts.
  * The matched value is never logged, returned or stored. The only thing logged is the NAME of
    the pattern that matched, once per call to redact_segments: "redaction occurred: <name>".
  * `patterns` adds named regexes to the defaults (or replaces a default of the same name).

Default patterns:
  card_number         13-19 digits, each optionally followed by a single space or dash.
  card_number_spoken  13-19 digit words in a row ("four one one one ..."). Included because this
                      project's transcripts write numbers out as words, so a spoken card number
                      would not contain any digits for the pattern above to find.

There is no checksum (Luhn) test: anything card-shaped is masked, since wrongly masking a
long number is much cheaper than leaking a card. Only the transcript text is redacted; the
audio recording still contains whatever was said.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

REDACTED = "[REDACTED]"

_DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"

DEFAULT_PATTERNS: dict[str, str] = {
    "card_number": r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)",
    "card_number_spoken": rf"\b{_DIGIT_WORD}\b(?:[ ,-]+{_DIGIT_WORD}\b){{12,18}}",
}


def redact_segments(
    segments: Sequence[Mapping[str, Any]], patterns: Mapping[str, str] | None = None
) -> tuple[list[dict[str, Any]], bool]:
    compiled = {
        name: re.compile(regex, re.IGNORECASE) for name, regex in {**DEFAULT_PATTERNS, **(patterns or {})}.items()
    }
    matched_names: set[str] = set()
    redacted_segments: list[dict[str, Any]] = []
    for segment in segments:
        new_segment = dict(segment)  # never touch the caller's dict
        text = new_segment.get("text")
        if isinstance(text, str):
            for name, regex in compiled.items():
                text, count = regex.subn(REDACTED, text)
                if count:
                    matched_names.add(name)
            new_segment["text"] = text
        redacted_segments.append(new_segment)

    for name in sorted(matched_names):
        logger.info("redaction occurred: %s", name)  # the pattern's name only, never what it matched
    return redacted_segments, bool(matched_names)
