"""
Redaction of spoken card numbers. The numbers below are SYNTHETIC test data (a well-known test-card
pattern), never a real card. Assertions never embed the raw value: they check that nothing
card-shaped is left, so a failure message can't print one either.
"""

import copy
import logging
import re
from contextlib import nullcontext

import pytest
from sqlalchemy import func, select, text

from app.models import Call, CallStatus, Lead, Recording, Retailer, Transcript, TranscriptSegment
from app.repositories.base import BaseRepository
from app.services.calls import processing_service
from app.services.redaction.redaction_service import DEFAULT_PATTERNS, REDACTED, redact_segments
from app.services.storage.audio_storage import LocalAudioStorage
from app.services.transcription.base import TranscriptionProvider, TranscriptSegmentData
from app.services.transcription.transcription_service import transcribe_call
from tests.services.scenario import DISCLAIMER, JAN, add_library

LOGGER = "app.services.redaction.redaction_service"

# synthetic card number in the forms a transcript might hold it
CARD_PLAIN = "4111" * 4
CARD_SPACED = " ".join(["4111"] * 4)
CARD_DASHED = "-".join(["4111"] * 4)
CARD_SPOKEN = " ".join(["four", "one", "one", "one"] * 4)

CARD_SHAPED = re.compile(r"(?:\d[ -]?){13}")  # anything that still looks like a card number
DIGIT_WORD_RUN = re.compile(r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)\b[ ,-]*){10}", re.IGNORECASE)


def seg(text_, speaker="CUSTOMER", start=0.0, end=3.0, confidence=0.9):
    return {"speaker": speaker, "text": text_, "start_time": start, "end_time": end, "confidence": confidence}


def leaks(value: str) -> bool:
    return bool(CARD_SHAPED.search(value) or DIGIT_WORD_RUN.search(value))


# ---------------------------------------------------------------------------
# redact_segments
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("card", [CARD_PLAIN, CARD_SPACED, CARD_DASHED, CARD_SPOKEN])
def test_a_spoken_card_number_is_replaced_with_redacted(card):
    result, redacted = redact_segments([seg(f"Sure, my card number is {card} and the expiry is soon.")])

    assert redacted is True
    assert result[0]["text"] == f"Sure, my card number is {REDACTED} and the expiry is soon."


@pytest.mark.parametrize("digits, redacted", [
    (12, False), (13, True), (16, True), (19, True), (20, False),  # card numbers are 13-19 digits
])
def test_only_13_to_19_digit_sequences_count(digits, redacted):
    number = ("4111" * 5)[:digits]
    _, was_redacted = redact_segments([seg(f"the number is {number} thanks")])
    assert was_redacted is redacted


@pytest.mark.parametrize("innocent", [
    "call me on 0412 345 678",              # a phone number
    "born on 1990-01-01",                   # a date
    "the peak rate is thirty one point nine cents",
    "the total minimum cost is $1,250.00",
    "it is unit 5, 12 Smith Street",
    "one two three four five six seven eight nine",        # only 9 digit words
    "oh okay, one moment, two seconds, three minutes",
    "Good afternoon, this call is recorded for quality and compliance purposes.",
])
def test_ordinary_numbers_and_sentences_are_left_alone(innocent):
    result, redacted = redact_segments([seg(innocent)])
    assert redacted is False and result[0]["text"] == innocent


def test_returns_new_dicts_and_never_mutates_the_input():
    original = [seg(f"card {CARD_SPACED} ok"), seg("nothing sensitive here", speaker="AGENT", start=4.0, end=6.0)]
    snapshot = copy.deepcopy(original)

    result, redacted = redact_segments(original)

    assert original == snapshot  # untouched, including the segment that had a card in it
    assert redacted is True
    assert all(new is not old for new, old in zip(result, original))
    assert result[1] == original[1] and result[1] is not original[1]  # unaffected segments are copied too


def test_only_the_text_changes():
    result, _ = redact_segments([seg(f"{CARD_PLAIN}", speaker="CUSTOMER", start=12.5, end=15.0, confidence=0.71)])
    assert result == [{"speaker": "CUSTOMER", "text": REDACTED, "start_time": 12.5, "end_time": 15.0, "confidence": 0.71}]


def test_every_card_in_every_segment_is_redacted_and_order_is_kept():
    segments = [
        seg(f"first {CARD_SPACED} and again {CARD_DASHED}", start=1.0),
        seg("no card in this one", start=2.0),
        seg(f"read out: {CARD_SPOKEN}", speaker="AGENT", start=3.0),
    ]
    result, redacted = redact_segments(segments)

    assert redacted is True
    assert [s["start_time"] for s in result] == [1.0, 2.0, 3.0]
    assert result[0]["text"] == f"first {REDACTED} and again {REDACTED}"
    assert result[1]["text"] == "no card in this one"
    assert result[2]["text"] == f"read out: {REDACTED}"
    assert not any(leaks(s["text"]) for s in result)


def test_nothing_to_redact_returns_false_and_equal_copies():
    segments = [seg("hello"), seg("goodbye", speaker="AGENT")]
    result, redacted = redact_segments(segments)
    assert redacted is False and result == segments


def test_redacting_twice_is_stable():
    once, _ = redact_segments([seg(f"card {CARD_SPACED}")])
    twice, redacted_again = redact_segments(once)
    assert twice == once and redacted_again is False


def test_extra_named_patterns_are_added_to_the_defaults():
    result, redacted = redact_segments(
        [seg(f"my pin is 4321 and the card is {CARD_PLAIN}")], patterns={"pin": r"(?<!\d)\d{4}(?!\d)"}
    )
    assert redacted is True
    assert result[0]["text"] == f"my pin is {REDACTED} and the card is {REDACTED}"
    assert set(DEFAULT_PATTERNS) == {"card_number", "card_number_spoken"}  # the defaults are not modified


def test_a_custom_pattern_alone_flags_redaction(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result, redacted = redact_segments([seg("my member id is ABC-12345")], patterns={"member_id": r"ABC-\d+"})
    assert redacted is True and result[0]["text"] == f"my member id is {REDACTED}"


def test_segments_without_text_are_passed_through():
    result, redacted = redact_segments([{"speaker": "AGENT", "start_time": 0, "end_time": 1}])
    assert redacted is False and result == [{"speaker": "AGENT", "start_time": 0, "end_time": 1}]


# --- logging: the pattern's name only, never the match ------------------------------------------

def test_only_the_pattern_name_is_logged(caplog):
    with caplog.at_level(logging.DEBUG):  # capture everything, at every level
        redact_segments([seg(f"my card is {CARD_SPACED}"), seg(f"or {CARD_SPOKEN}")])

    messages = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    assert messages == ["redaction occurred: card_number", "redaction occurred: card_number_spoken"]
    assert not any(leaks(m) or re.search(r"\d", m) for m in messages)  # not a digit in any message
    assert all(not r.args or all(isinstance(a, str) and a in DEFAULT_PATTERNS for a in r.args) for r in caplog.records)


def test_nothing_is_logged_when_nothing_was_redacted(caplog):
    with caplog.at_level(logging.DEBUG):
        redact_segments([seg("hello there")])
    assert [r for r in caplog.records if r.name.startswith("app.")] == []


def test_each_pattern_is_logged_once_per_call_however_many_matches(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        redact_segments([seg(f"{CARD_PLAIN} and {CARD_SPACED}"), seg(f"{CARD_DASHED}")])
    assert [r.getMessage() for r in caplog.records] == ["redaction occurred: card_number"]


# ---------------------------------------------------------------------------
# wired into transcription: Transcript.is_redacted and what gets stored
# ---------------------------------------------------------------------------

class ListProvider(TranscriptionProvider):
    name = "list"

    def __init__(self, *segments):
        self.segments = segments

    def transcribe(self, audio):
        return [TranscriptSegmentData(**s) for s in self.segments]


@pytest.fixture
def storage(tmp_path):
    return LocalAudioStorage(str(tmp_path))


@pytest.fixture
def make_call(session, storage):
    def _make(code="redaction_retailer"):
        retailer = BaseRepository(Retailer, session).create(code=code, name=code)
        lead = BaseRepository(Lead, session).create(retailer_id=retailer.id, external_lead_id="1", crm_fields={})
        call = BaseRepository(Call, session).create(
            lead_id=lead.id, status=CallStatus.PROCESSING, call_started_at=JAN
        )
        reference = storage.save(str(call.id), "a.wav", b"fake-audio-bytes")
        BaseRepository(Recording, session).create(
            call_id=call.id, storage_reference=reference, original_filename="a.wav",
            content_type="audio/wav", size_bytes=16,
        )
        session.commit()
        return call, retailer

    return _make


def stored_texts(session, transcript):
    return [s.text for s in session.scalars(
        select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id).order_by(TranscriptSegment.sequence))]


def test_transcribing_a_call_with_a_spoken_card_number_marks_the_transcript_redacted(session, storage, make_call):
    call, _ = make_call()
    provider = ListProvider(
        seg("What card would you like to use?", "AGENT", 0.0, 3.0),
        seg(f"It's {CARD_SPACED}, expiry next year.", "CUSTOMER", 3.5, 8.0),
    )

    transcript = transcribe_call(session, call.id, provider, storage)
    session.expire_all()

    assert transcript.is_redacted is True
    texts = stored_texts(session, transcript)
    assert texts == ["What card would you like to use?", f"It's {REDACTED}, expiry next year."]
    assert not any(leaks(t) for t in texts)


def test_a_transcript_with_nothing_to_redact_is_not_marked(session, storage, make_call):
    call, _ = make_call()
    transcript = transcribe_call(session, call.id, ListProvider(seg("hello", "AGENT")), storage)
    session.expire_all()
    assert transcript.is_redacted is False
    assert stored_texts(session, transcript) == ["hello"]


def test_the_default_fixture_transcript_is_not_redacted(session, storage, make_call):
    from app.services.transcription.fixture_provider import FixtureTranscriptionProvider

    call, _ = make_call()
    transcript = transcribe_call(session, call.id, FixtureTranscriptionProvider(), storage)
    assert transcript.is_redacted is False


def test_redaction_keeps_timings_speakers_and_order(session, storage, make_call):
    call, _ = make_call()
    provider = ListProvider(seg(f"card {CARD_PLAIN}", "CUSTOMER", 9.0, 12.5, 0.66), seg("thanks", "AGENT", 2.0, 4.0, 0.99))

    transcript = transcribe_call(session, call.id, provider, storage)
    session.expire_all()

    rows = [(s.sequence, s.speaker, s.text, s.start_time, s.end_time, s.confidence) for s in transcript.segments]
    assert rows == [(1, "AGENT", "thanks", 2.0, 4.0, 0.99), (2, "CUSTOMER", f"card {REDACTED}", 9.0, 12.5, 0.66)]


def test_the_raw_number_is_not_logged_while_transcribing(session, storage, make_call, caplog):
    call, _ = make_call()
    with caplog.at_level(logging.DEBUG):
        transcribe_call(session, call.id, ListProvider(seg(f"card {CARD_SPACED}")), storage)

    app_messages = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    assert app_messages == ["redaction occurred: card_number"]
    assert not any(leaks(r.getMessage()) for r in caplog.records)


# --- end to end: nothing card-shaped reaches ANY table --------------------------------------------

def test_a_spoken_card_number_never_reaches_the_database_through_the_whole_pipeline(
    session, storage, make_call, monkeypatch
):
    call, retailer = make_call("redaction_pipeline_retailer")
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    session.commit()

    provider = ListProvider(
        seg("Good afternoon. This call is recorded for quality and compliance purposes.", "AGENT", 0.0, 6.0, 0.97),
        seg(f"Okay, the card is {CARD_DASHED}", "CUSTOMER", 7.0, 11.0),
        seg(f"Sorry, that was {CARD_SPOKEN}", "CUSTOMER", 12.0, 18.0),
    )
    monkeypatch.setattr(processing_service, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(processing_service, "get_audio_storage", lambda: storage)
    monkeypatch.setattr(processing_service, "get_transcription_provider", lambda: provider)

    processing_service.process_call(str(call.id))
    session.expire_all()

    transcript = session.scalar(select(Transcript).where(Transcript.call_id == call.id))
    assert transcript.is_redacted is True
    assert session.get(Call, call.id).status is CallStatus.COMPLETED  # the pipeline still ran to the end

    # Scan every text-bearing column that could hold it, directly in Postgres.
    card_like = "([0-9][ -]?){13}"
    spoken_like = "((zero|oh|one|two|three|four|five|six|seven|eight|nine)[ ,-]*){10}"
    queries = {
        "transcript_segments.text": "select count(*) from transcript_segments where transcript_id = :t and (text ~* :c or text ~* :w)",
        "check_results": "select count(*) from check_results where call_id = :call and "
                         "(reason ~* :c or reason ~* :w or coalesce(actual_value, '') ~* :c or coalesce(actual_value, '') ~* :w "
                         "or coalesce(expected_value, '') ~* :c or evidence::text ~* :c or evidence::text ~* :w)",
        "gate_decisions.reason": "select count(*) from gate_decisions where call_id = :call and (reason ~* :c or reason ~* :w)",
    }
    for name, sql in queries.items():
        found = session.execute(text(sql), {"t": transcript.id, "call": call.id, "c": card_like, "w": spoken_like}).scalar()
        assert found == 0, f"card-shaped data found in {name}"
    assert session.scalar(
        select(func.count()).select_from(TranscriptSegment).where(
            TranscriptSegment.transcript_id == transcript.id, TranscriptSegment.text.contains(REDACTED))
    ) == 2
