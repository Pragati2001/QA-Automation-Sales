"""
Field-specific normalizers for factual checks. Pure functions: no I/O, no
transcript or database access, so they are testable on their own.

Each normalizer turns either side of a comparison (a CRM/plan value or something
extracted from speech) into one canonical form, or returns None when the value
cannot be interpreted. Spoken forms are understood because the transcript is
speech: "thirty one point nine cents", "the first of January nineteen ninety",
"CF forty".

Assumptions worth knowing (the real CRM export's formats are not available yet):
  * dates: ISO (YYYY-MM-DD) and DAY-first numeric dates (DD/MM/YYYY, Australian);
    month-first numeric dates and two-digit years are NOT guessed at (-> None).
  * rates are compared in cents, money in dollars; "$" / "dollars" / "cents" / "c"
    units are honoured, a bare number is taken to already be in the target unit.
"""

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from app.services.checks.text_utils import normalize_text

# --- spoken numbers -----------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_ORDINALS = {w: i for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split(), start=1)}
_ORDINALS.update({"twentieth": 20, "thirtieth": 30})
DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _alt(words) -> str:
    return "|".join(sorted(words, key=len, reverse=True))


_NUM_WORD = _alt([*_UNITS, *_TENS, "hundred", "thousand"])
_DIGIT_WORD = _alt(DIGIT_WORDS)
SPOKEN_NUMBER = (
    rf"\b(?:{_NUM_WORD})(?:\s+(?:and\s+)?(?:{_NUM_WORD}))*"
    rf"(?:\s+point(?:\s+(?:{_DIGIT_WORD}))+)?\b"
)
# A number as either digits (with optional thousands commas / decimals) or spoken words.
NUMBER = rf"(?:\d[\d,]*(?:\.\d+)?|{SPOKEN_NUMBER})"
_NUMBER_RE = re.compile(NUMBER)


def _words_to_int(words: list[str]) -> int | None:
    total = current = 0
    seen = False
    for w in words:
        if w == "and":
            continue
        if w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
        seen = True
    return total + current if seen else None


def parse_number(text: str) -> float | None:
    """'31.9', '1,250', 'thirty one point nine', 'one hundred and twenty' -> float."""
    s = text.strip().lower().replace(",", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    words = s.replace("-", " ").split()
    if "point" in words:
        i = words.index("point")
        whole, fraction = words[:i], words[i + 1 :]
    else:
        whole, fraction = words, []
    n = _words_to_int(whole)
    if n is None:
        return None
    if fraction:
        if not all(w in DIGIT_WORDS for w in fraction):
            return None
        return float(f"{n}.{''.join(DIGIT_WORDS[w] for w in fraction)}")
    return float(n)


# --- amounts, speeds ------------------------------------------------------------

_CENTS_AFTER = re.compile(r"\s*(?:cents?|c|¢)(?![a-z])")
_DOLLARS_AFTER = re.compile(r"\s*dollars?\b")


def normalize_amount(value: Any, unit: str) -> float | None:
    """Parse a price/amount to a float in `unit` ("cents" or "dollars")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    m = _NUMBER_RE.search(text)
    number = parse_number(m.group()) if m else None
    if number is None:
        return None
    if _CENTS_AFTER.match(text, m.end()):
        source = "cents"
    elif "$" in text or _DOLLARS_AFTER.match(text, m.end()):
        source = "dollars"
    else:
        source = unit  # no unit given: assume it is already in the target unit
    if source == unit:
        return number
    return number / 100 if source == "cents" else number * 100


_SPEED_UNIT = re.compile(r"\s*(gbps|gigabits?|gigs?|gb/s|mbps|megabits?|megs?|mb/s|mb)\b")


def normalize_speed(value: Any) -> float | None:
    """Parse a speed to megabits per second (a bare number is taken as Mbps)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    m = _NUMBER_RE.search(text)
    number = parse_number(m.group()) if m else None
    if number is None:
        return None
    unit = _SPEED_UNIT.match(text, m.end())
    return number * 1000 if unit and unit.group(1).startswith(("g",)) else number


# --- email, phone, names, addresses, modem models ---------------------------------

def normalize_email(value: Any) -> str | None:
    return str(value).strip().lower() or None


def normalize_phone(value: Any) -> str | None:
    """Digits only; an Australian +61 / 61 prefix becomes the leading 0."""
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("61") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits or None


def normalize_name(value: Any) -> str | None:
    return normalize_text(str(value)) or None  # lowercase, punctuation out, whitespace collapsed


def normalize_address(value: Any) -> str | None:
    # Same treatment as a name. service_address and delivery_address are each normalized
    # on their own; nothing here assumes they are the same, or that a difference is an error.
    return normalize_text(str(value)) or None


def normalize_modem_model(value: Any) -> str | None:
    """Case/punctuation-insensitive, spoken numbers as digits, and a short letter prefix
    joined to its number: 'CF forty' -> 'cf40', 'CF-40' -> 'cf40'."""
    text = re.sub(SPOKEN_NUMBER, lambda m: _spoken_to_digits(m.group()), str(value).lower())
    merged: list[str] = []
    for token in normalize_text(text).split():
        if merged and token[0].isdigit() and merged[-1].isalpha() and len(merged[-1]) <= 4:
            merged[-1] += token
        else:
            merged.append(token)
    return " ".join(merged) or None


def _spoken_to_digits(phrase: str) -> str:
    n = parse_number(phrase)
    return phrase if n is None else (str(int(n)) if n == int(n) else str(n))


# --- dates ------------------------------------------------------------------------

_MONTHS = {name: i for i, names in enumerate(
    ["january jan", "february feb", "march mar", "april apr", "may", "june jun", "july jul",
     "august aug", "september sep sept", "october oct", "november nov", "december dec"], start=1)
    for name in names.split()}
_U19 = "one|two|three|four|five|six|seven|eight|nine"
_TEENS = "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_TENS_RE = _alt(_TENS)
_TEEN_OR_TENS = rf"(?:{_TEENS}|(?:{_TENS_RE})(?:\s+(?:{_U19}))?)"
_YEAR_WORDS = (
    rf"(?:(?:nineteen|twenty)\s+(?:oh\s+(?:{_U19})|{_TEEN_OR_TENS})"
    rf"|(?:nineteen|twenty)\s+hundred"
    rf"|two\s+thousand(?:\s+(?:and\s+)?(?:{_TEEN_OR_TENS}|{_U19}))?)"
)
_DAY = (
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?"
    rf"|(?:(?:twenty|thirty)\s+)?(?:{_alt(k for k in _ORDINALS if _ORDINALS[k] < 20)})"
    rf"|twentieth|thirtieth"
    rf"|(?:twenty|thirty)(?:\s+(?:{_U19}))?"
    rf"|(?:{_TEENS}|{_U19}))"
)
_MONTH = _alt(_MONTHS)
_YEAR = rf"(?:\d{{4}}|{_YEAR_WORDS})"

_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DAY_FIRST_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)")
_DAY_MONTH_YEAR = re.compile(rf"\b(?:the\s+)?(?P<d>{_DAY})\s+(?:of\s+)?(?P<m>{_MONTH})\b[\s,]*(?P<y>{_YEAR})\b")
_MONTH_DAY_YEAR = re.compile(rf"\b(?P<m>{_MONTH})\s+(?:the\s+)?(?P<d>{_DAY})[\s,]+(?:(?:of|in)\s+)?(?P<y>{_YEAR})\b")


@dataclass(frozen=True)
class DateMatch:
    iso: str
    raw: str
    start: int


def _parse_day(token: str) -> int | None:
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", token)
    if m:
        return int(m.group(1))
    words = token.split()
    if words[-1] in _ORDINALS:
        base = _ORDINALS[words[-1]]
        if len(words) == 1:
            return base
        return _TENS[words[0]] + base if len(words) == 2 and words[0] in _TENS and base < 10 else None
    return _words_to_int(words)


def _parse_year(token: str) -> int | None:
    if re.fullmatch(r"\d{4}", token):
        return int(token)
    words = token.split()
    if words[0] in ("nineteen", "twenty") and len(words) >= 2:
        century = 1900 if words[0] == "nineteen" else 2000
        rest = words[1:]
        if rest == ["hundred"]:
            return century
        n = _words_to_int(rest[1:]) if rest[0] == "oh" else _words_to_int(rest)
        return century + n if n is not None and n < 100 else None
    if words[:2] == ["two", "thousand"]:
        rest = [w for w in words[2:] if w != "and"]
        n = _words_to_int(rest) if rest else 0
        return 2000 + n if n is not None else None
    return None


def _make_iso(year: int | None, month: int | None, day: int | None) -> str | None:
    if year is None or month is None or day is None:
        return None
    try:
        date = dt.date(year, month, day)
    except ValueError:
        return None
    return date.isoformat() if 1900 <= year <= dt.date.today().year else None


def find_dates(text: str) -> list[DateMatch]:
    """Every full date (day, month, four-digit or spoken year) in `text`, as ISO strings."""
    low = text.lower()
    found: list[DateMatch] = []
    for m in _ISO.finditer(low):
        if iso := _make_iso(int(m[1]), int(m[2]), int(m[3])):
            found.append(DateMatch(iso, m.group(), m.start()))
    for m in _DAY_FIRST_NUMERIC.finditer(low):
        if iso := _make_iso(int(m[3]), int(m[2]), int(m[1])):
            found.append(DateMatch(iso, m.group(), m.start()))
    words = re.sub(r"[,;.]", " ", re.sub(r"(?<=[a-z])-(?=[a-z])", " ", low))
    for pattern in (_DAY_MONTH_YEAR, _MONTH_DAY_YEAR):
        for m in pattern.finditer(words):
            iso = _make_iso(_parse_year(m["y"]), _MONTHS[m["m"]], _parse_day(m["d"]))
            if iso:
                found.append(DateMatch(iso, m.group().strip(), m.start()))
    return sorted(found, key=lambda d: d.start)


def normalize_dob(value: Any) -> str | None:
    """-> 'YYYY-MM-DD', or None if no full date can be read from the value."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    dates = find_dates(str(value))
    return dates[0].iso if dates else None


# --- dispatch ---------------------------------------------------------------------

def normalize_field(kind: str, value: Any) -> str | float | None:
    """Normalize `value` for a field of the given kind (see extraction.FIELD_SPECS)."""
    if kind == "rate":
        return normalize_amount(value, "cents")
    if kind == "money":
        return normalize_amount(value, "dollars")
    normalizers = {
        "speed": normalize_speed, "email": normalize_email, "phone": normalize_phone,
        "name": normalize_name, "address": normalize_address, "modem": normalize_modem_model,
        "dob": normalize_dob,
    }
    return normalizers[kind](value)
