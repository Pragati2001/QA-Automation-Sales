"""
Deterministic (regex / keyword) extraction of a field's value from the transcript.
No LLM. The runner normalizes and compares what comes out of here.

Per field there is a default speaker (overridable with config["speaker"], since
retailer scripts differ: who reads the email back varies) and a rule for choosing
between several mentions:

  first   the FIRST mention wins. Used for rates and the total minimum cost: the
          compliance-relevant moment is the initial disclosure, so a later figure
          that differs is a discrepancy to catch, not to paper over.
  last    the LAST mention wins. Used for addresses, which get corrected mid-call.
  single  one mention is expected. Repeats of the same value corroborate it; two
          different values set `conflict`, and the runner turns that into
          LOW_CONFIDENCE instead of silently picking one.

Redaction: if the only candidate segments for a field are masked placeholders such
as "[EMAIL]" / "[DOB]", the result is redacted=True (value spoken, but hidden from
us), which is not the same thing as the field never being said.

Known limits: the finders are heuristics for typical phrasing, and the first two
mentions of one field in the same segment are both seen. A redacted mention that
comes after a real one is not noticed. Speaker labels must be "AGENT" / "CUSTOMER".
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models import TranscriptSegment
from app.services.checks.normalization import (
    DIGIT_WORDS,
    NUMBER,
    SPOKEN_NUMBER,
    find_dates,
    normalize_field,
)


@dataclass
class ExtractionResult:
    value: str | None  # the value as it was said/written (not normalized)
    confidence: float | None  # ASR confidence of the segment it came from; None if unknown
    source_segment_id: str | None
    redacted: bool = False
    conflict: bool = False  # several different values were said (single-mention fields)
    alternatives: list[tuple[str, str]] = field(default_factory=list)  # (value, segment_id) per distinct value


# --- finders: (segment text, previous segment's text, config) -> raw values, in order ----

_Finder = Callable[[str, str, dict], list[str]]

_STOP_TAIL = {"and", "with", "for", "which", "that", "so", "you", "it", "will", "we", "the", "is", "has",
              "comes", "to", "from", "calling", "speaking", "here", "today", "i", "im", "my", "a", "at", "are"}


def _ordered(hits: list[tuple[int, str]]) -> list[str]:
    return [value for _, value in sorted(hits)]


def _context_ok(text: str, config: dict) -> bool:
    keywords = config.get("context_keywords")
    return not keywords or any(str(k).lower() in text for k in keywords)


def _trim_tail(words: list[str]) -> list[str]:
    while words and words[-1] in _STOP_TAIL:
        words = words[:-1]
    return words


_EMAIL_LITERAL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_EMAIL_SPOKEN = re.compile(
    r"(?P<local>[a-z0-9_+-]+(?:\s+(?:dot|underscore|dash|hyphen)\s+[a-z0-9_+-]+)*)"
    r"\s+at\s+(?P<domain>[a-z0-9-]+(?:\s+dot\s+[a-z0-9-]+)+)"
)


def _despoken(part: str) -> str:
    part = re.sub(r"\s+dot\s+", ".", part)
    part = re.sub(r"\s+underscore\s+", "_", part)
    return re.sub(r"\s+(?:dash|hyphen)\s+", "-", part)


def find_emails(text: str, prev: str, config: dict) -> list[str]:
    low = text.lower()
    hits = [(m.start(), m.group().rstrip(".")) for m in _EMAIL_LITERAL.finditer(low)]
    spoken = re.sub(r"\.(?=\s|$)", " ", re.sub(r"[,;:!?]", " ", low))  # 1:1 replacements keep offsets
    for m in _EMAIL_SPOKEN.finditer(spoken):
        hits.append((m.start(), f"{_despoken(m['local'])}@{_despoken(m['domain'])}"))
    return _ordered(hits)


def find_dobs(text: str, prev: str, config: dict) -> list[str]:
    return [d.raw for d in find_dates(text)]


_NAME = re.compile(
    r"(?:\bmy\s+(?:full\s+|first\s+|last\s+)?name\s+is|\bmy\s+name'?s|\bthe\s+name\s+is|\bspeaking\s+(?:with|to))\s+"
    r"(?P<n>[a-z'-]+(?:\s+[a-z'-]+){0,2})"
)


def find_names(text: str, prev: str, config: dict) -> list[str]:
    hits = []
    for m in _NAME.finditer(re.sub(r"[,.;:!?]", " ", text.lower())):
        words = _trim_tail(m["n"].split())
        if words:
            hits.append((m.start(), " ".join(words)))
    return _ordered(hits)


_PHONE_DIGITS = re.compile(r"(?<![\d.])\+?\d[\d\s()-]{6,14}\d(?![\d.])")
_PHONE_SPOKEN = re.compile(rf"\b(?:(?:{'|'.join(DIGIT_WORDS)})\b\s*){{8,12}}")


def find_phones(text: str, prev: str, config: dict) -> list[str]:
    low = text.lower()
    hits = [(m.start(), m.group().strip()) for m in _PHONE_DIGITS.finditer(low)
            if 8 <= len(re.sub(r"\D", "", m.group())) <= 12]
    for m in _PHONE_SPOKEN.finditer(low):
        hits.append((m.start(), "".join(DIGIT_WORDS[w] for w in m.group().split())))
    return _ordered(hits)


_RATE_CENTS = re.compile(rf"\b{NUMBER}\s*(?:cents?|c|¢)(?![a-z])")
_RATE_DOLLARS = re.compile(rf"(?:\$\s*{NUMBER}|\b{NUMBER}\s*dollars?\b)")
_RATE_CONTEXT = re.compile(
    r"\b(?:rates?|price[sd]?|charges?|kwh|kilowatt|monthly|per\s+(?:month|day|week|unit))\b"
)


def find_rates(text: str, prev: str, config: dict) -> list[str]:
    low = text.lower()
    if not _context_ok(low, config):
        return []
    hits = [(m.start(), m.group().strip()) for m in _RATE_CENTS.finditer(low)]
    if _RATE_CONTEXT.search(low):  # a dollar figure only counts as a rate when it is talked about as one
        hits += [(m.start(), m.group().strip()) for m in _RATE_DOLLARS.finditer(low)]
    return _ordered(hits)


_TOTAL_COST = re.compile(r"\b(?:total\s+minimum|minimum\s+total|total\s+(?:cost|amount))\b")


def find_total_minimum_costs(text: str, prev: str, config: dict) -> list[str]:
    low = text.lower()
    keyword = _TOTAL_COST.search(low)
    if not keyword or not _context_ok(low, config):
        return []
    return [m.group().strip() for m in _RATE_DOLLARS.finditer(low, keyword.end())]


_SPEED_UNIT = r"(?:gbps|gigabits?|gigs?|gb/s|mbps|megabits?|megs?|mb/s|mb)\b"


def _speed_finder(direction: str) -> _Finder:
    pattern = re.compile(
        rf"\b{direction}(?:\s+speeds?)?(?:\s+[a-z]+){{0,3}}?\s+(?P<v>{NUMBER}\s*{_SPEED_UNIT})"
    )

    def find(text: str, prev: str, config: dict) -> list[str]:
        return [m["v"].strip() for m in pattern.finditer(text.lower())]

    return find


_MODEM = re.compile(
    r"\b(?:modem|router)(?:\s+(?:model|type))?(?:\s+(?:is|will\s+be|would\s+be|called|named|being))?"
    r"(?:\s+(?:an?|the))?\s+(?P<m>[a-z0-9-]+(?:\s+[a-z0-9-]+){0,3})"
)


def find_modem_models(text: str, prev: str, config: dict) -> list[str]:
    hits = []
    for m in _MODEM.finditer(re.sub(r"[,.;:!?]", " ", text.lower())):
        words = _trim_tail(m["m"].split())
        if words:
            hits.append((m.start(), " ".join(words)))
    return _ordered(hits)


_STREET_TYPES = (
    "street|st|road|rd|avenue|ave|drive|dr|court|ct|place|pl|lane|ln|boulevard|blvd|crescent|cres|"
    "terrace|tce|way|close|parade|pde|highway|hwy|circuit|cct"
)
_STATES = "nsw|vic|qld|sa|wa|tas|nt|act"
_STREET_NUMBER = rf"(?:\d+[a-z]?(?:\s*/\s*\d+)?|{SPOKEN_NUMBER})"
_NOT_SUBURB = (
    "and|my|is|the|it|its|i|im|please|thanks|thank|yes|no|okay|ok|for|to|at|with|that|which|so|you|we|can|"
    "will|our|your|but|also|actually"
)
_ADDRESS = re.compile(
    rf"\b(?:(?:unit|apartment|apt|flat)\s+{_STREET_NUMBER}\s+)?{_STREET_NUMBER}\s+"
    rf"(?:[a-z']+\s+){{1,3}}?(?:{_STREET_TYPES})\b\.?,?"
    # then, if said, the suburb / state / postcode (up to a comma-less run of ordinary words)
    rf"(?:(?:\s+(?!(?:{_NOT_SUBURB})\b)[a-z]+,?){{0,4}}(?:\s+\d{{4}}\b)?)"
)
_DELIVERY_WORDS = ("deliver", "delivery", "send", "post", "ship", "mail")
_SERVICE_WORDS = ("service", "supply", "connect", "install", "property", "premises", "moving")


def _address_finder(kind: str) -> _Finder:
    def find(text: str, prev: str, config: dict) -> list[str]:
        low = text.lower()
        # The answer to "what's the delivery address?" often carries no keyword itself,
        # so the segment before it counts as context.
        context = f"{prev.lower()} {low}"
        if config.get("context_keywords"):
            allowed = _context_ok(context, config)
        elif kind == "delivery":
            allowed = any(w in context for w in _DELIVERY_WORDS)
        else:  # a bare "address" is the service address unless it is talking about delivery
            allowed = any(w in context for w in _SERVICE_WORDS) or (
                "address" in context and not any(w in context for w in _DELIVERY_WORDS)
            )
        if not allowed:
            return []
        return [m.group().strip() for m in _ADDRESS.finditer(re.sub(r"[;:!?]", " ", low))]

    return find


# --- per-field configuration -------------------------------------------------------------

@dataclass(frozen=True)
class FieldSpec:
    kind: str  # which normalizer / comparison applies (see normalization.normalize_field)
    speaker: str  # default speaker: "agent" or "customer"
    selection: str  # "first" | "last" | "single"
    finder: _Finder
    redaction_tags: frozenset[str] = frozenset()


_ADDRESS_TAGS = frozenset({"ADDRESS", "SERVICE_ADDRESS", "DELIVERY_ADDRESS"})

FIELD_SPECS: dict[str, FieldSpec] = {
    "price": FieldSpec("rate", "agent", "first", find_rates),
    "rate": FieldSpec("rate", "agent", "first", find_rates),
    "total_minimum_cost": FieldSpec("money", "agent", "first", find_total_minimum_costs),
    "download_speed": FieldSpec("speed", "agent", "single", _speed_finder("download")),
    "upload_speed": FieldSpec("speed", "agent", "single", _speed_finder("upload")),
    "modem_model": FieldSpec("modem", "agent", "single", find_modem_models),
    "email": FieldSpec("email", "customer", "single", find_emails, frozenset({"EMAIL"})),
    "dob": FieldSpec("dob", "customer", "single", find_dobs, frozenset({"DOB", "DATE_OF_BIRTH"})),
    "name": FieldSpec("name", "customer", "single", find_names, frozenset({"NAME", "PERSON"})),
    "phone": FieldSpec("phone", "customer", "single", find_phones, frozenset({"PHONE", "PHONE_NUMBER"})),
    "service_address": FieldSpec("address", "customer", "last", _address_finder("service"), _ADDRESS_TAGS),
    "delivery_address": FieldSpec("address", "customer", "last", _address_finder("delivery"), _ADDRESS_TAGS),
}

_PLACEHOLDER = re.compile(r"\[([A-Z_]+)\]")


def _speaker_matches(segment: TranscriptSegment, speaker: str) -> bool:
    return speaker == "any" or (segment.speaker or "").strip().lower() == speaker


def extract_value(
    segments: Sequence[TranscriptSegment], field_hint: str, config: dict[str, Any]
) -> ExtractionResult:
    spec = FIELD_SPECS.get(str(field_hint).strip().lower())
    if spec is None:
        raise ValueError(f"Unsupported field {field_hint!r}; supported: {sorted(FIELD_SPECS)}")
    speaker = str(config.get("speaker") or spec.speaker).strip().lower()
    tags = spec.redaction_tags | {str(field_hint).strip().upper()}

    ordered = sorted(segments, key=lambda s: s.start_time)  # stable: ties keep the given order
    mentions: list[tuple[str, TranscriptSegment]] = []
    placeholders: list[TranscriptSegment] = []
    for i, seg in enumerate(ordered):
        if not _speaker_matches(seg, speaker):
            continue
        found = spec.finder(seg.text, ordered[i - 1].text if i else "", config)
        mentions += [(value, seg) for value in found]
        if not found and set(_PLACEHOLDER.findall(seg.text)) & tags:
            placeholders.append(seg)

    if not mentions:
        if placeholders:  # spoken, but masked in the transcript we were given
            return ExtractionResult(None, None, str(placeholders[0].id), redacted=True)
        return ExtractionResult(None, None, None)

    if spec.selection in ("first", "last"):
        value, seg = mentions[0] if spec.selection == "first" else mentions[-1]
        return ExtractionResult(value, seg.confidence, str(seg.id))

    # "single": group mentions by their normalized value
    groups: dict[Any, list[tuple[str, TranscriptSegment]]] = {}
    for value, seg in mentions:
        key = normalize_field(spec.kind, value)
        groups.setdefault(value.lower() if key is None else key, []).append((value, seg))

    def confidences(items):
        return [s.confidence for _, s in items if s.confidence is not None]

    if len(groups) == 1:  # the same value again is corroboration: take the best-heard mention
        items = next(iter(groups.values()))
        value, seg = max(items, key=lambda vs: vs[1].confidence if vs[1].confidence is not None else -1.0)
        best = confidences(items)
        return ExtractionResult(value, max(best) if best else None, str(seg.id))

    firsts = [items[0] for items in groups.values()]
    everything = confidences(mentions)
    return ExtractionResult(
        value=mentions[0][0],
        confidence=min(everything) if everything else None,
        source_segment_id=str(mentions[0][1].id),
        conflict=True,
        alternatives=[(value, str(seg.id)) for value, seg in firsts],
    )
