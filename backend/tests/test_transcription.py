import json
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import get_db
from app.main import app
from app.models import Call, CallStatus, GateStatus, Lead, Recording, Retailer, Transcript, TranscriptSegment
from app.repositories.base import BaseRepository
from app.services.calls import processing_service
from app.services.storage.audio_storage import LocalAudioStorage, get_audio_storage
from app.services.transcription.base import TranscriptionProvider, TranscriptSegmentData
from app.services.transcription.fixture_provider import DEFAULT_FIXTURE, FixtureTranscriptionProvider
from app.services.transcription.transcription_service import transcribe_call

AUDIO = b"RIFF....WAVEfmt fake-but-non-empty-audio-bytes"
FIXTURE = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))["segments"]


class ListProvider(TranscriptionProvider):
    name = "list"

    def __init__(self, segments):
        self.segments = segments

    def transcribe(self, audio):
        return list(self.segments)


class BrokenProvider(TranscriptionProvider):
    name = "broken"

    def transcribe(self, audio):
        raise RuntimeError("stt exploded")


def seg(start, end, text="hi", speaker="AGENT", confidence=0.9):
    return TranscriptSegmentData(speaker, text, start, end, confidence)


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def storage(tmp_path):
    return LocalAudioStorage(str(tmp_path))


@pytest.fixture
def make_call(session, storage):
    """Creates retailer/lead/call (+ optionally a stored recording); committed."""

    def _make(with_recording=True):
        n = count(session, Call)
        retailer = BaseRepository(Retailer, session).create(code=f"tr_test_{n}", name="T")
        lead = BaseRepository(Lead, session).create(retailer_id=retailer.id, external_lead_id="1", crm_fields={})
        call = BaseRepository(Call, session).create(lead_id=lead.id, status=CallStatus.PROCESSING)
        if with_recording:
            ref = storage.save(str(call.id), "a.wav", AUDIO)
            BaseRepository(Recording, session).create(
                call_id=call.id, storage_reference=ref, original_filename="a.wav",
                content_type="audio/wav", size_bytes=len(AUDIO),
            )
        session.commit()
        return call

    return _make


# --- provider -------------------------------------------------------------

def test_fixture_provider_returns_the_five_fields():
    segments = FixtureTranscriptionProvider().transcribe(AUDIO)
    assert len(segments) == len(FIXTURE) >= 10
    first = segments[0]
    assert (first.speaker, first.start_time, first.end_time, first.confidence) == ("AGENT", 0.0, 7.4, 0.97)
    assert "recorded" in first.text
    assert {s.speaker for s in segments} == {"AGENT", "CUSTOMER"}


# --- transcribe_call ------------------------------------------------------

def test_creates_one_transcript_with_ordered_segments(session, storage, make_call):
    call = make_call()
    transcript = transcribe_call(session, call.id, FixtureTranscriptionProvider(), storage)

    assert transcript.call_id == call.id and transcript.provider == "fixture"
    assert count(session, Transcript) >= 1
    rows = transcript.segments
    assert [r.sequence for r in rows] == list(range(1, len(FIXTURE) + 1))
    assert [(r.speaker, r.text, r.start_time, r.end_time, r.confidence) for r in rows] == [
        (s["speaker"], s["text"], s["start_time"], s["end_time"], s["confidence"]) for s in FIXTURE
    ]
    starts = [r.start_time for r in rows]
    assert starts == sorted(starts)


def test_out_of_order_provider_output_is_sorted_and_overlaps_are_kept(session, storage, make_call):
    call = make_call()
    provider = ListProvider([seg(10, 14, "b"), seg(0, 5, "a"), seg(13, 16, "c", "CUSTOMER")])
    rows = transcribe_call(session, call.id, provider, storage).segments

    assert [(r.sequence, r.text) for r in rows] == [(1, "a"), (2, "b"), (3, "c")]
    assert rows[1].end_time > rows[2].start_time  # the overlap survives


def test_null_confidence_is_stored(session, storage, make_call):
    call = make_call()
    rows = transcribe_call(session, call.id, ListProvider([seg(0, 1, confidence=None)]), storage).segments
    assert rows[0].confidence is None


def test_is_idempotent(session, storage, make_call):
    call = make_call()
    first = transcribe_call(session, call.id, FixtureTranscriptionProvider(), storage)
    segments_before = count(session, TranscriptSegment)
    second = transcribe_call(session, call.id, FixtureTranscriptionProvider(), storage)

    assert second.id == first.id
    assert count(session, TranscriptSegment) == segments_before


def test_unknown_call_raises(session, storage):
    with pytest.raises(ValueError, match="not found"):
        transcribe_call(session, 999_999_999, FixtureTranscriptionProvider(), storage)


def test_call_without_recording_raises_and_creates_nothing(session, storage, make_call):
    call = make_call(with_recording=False)
    before = count(session, Transcript)
    with pytest.raises(ValueError, match="no recording"):
        transcribe_call(session, call.id, FixtureTranscriptionProvider(), storage)
    assert count(session, Transcript) == before


def test_provider_failure_persists_nothing(session, storage, make_call):
    call = make_call()
    before = (count(session, Transcript), count(session, TranscriptSegment))
    with pytest.raises(RuntimeError, match="stt exploded"):
        transcribe_call(session, call.id, BrokenProvider(), storage)
    assert (count(session, Transcript), count(session, TranscriptSegment)) == before


def test_bad_segment_rolls_back_the_whole_transcript(session, storage, make_call):
    call = make_call()
    before = (count(session, Transcript), count(session, TranscriptSegment))
    with pytest.raises(IntegrityError):  # end_time < start_time violates the CHECK
        transcribe_call(session, call.id, ListProvider([seg(0, 5), seg(9, 3)]), storage)
    assert (count(session, Transcript), count(session, TranscriptSegment)) == before


def test_confidence_outside_0_1_is_rejected_by_the_db(session, storage, make_call):
    call = make_call()
    with pytest.raises(IntegrityError):
        transcribe_call(session, call.id, ListProvider([seg(0, 1, confidence=1.5)]), storage)


# --- ProcessingService wiring --------------------------------------------

@pytest.fixture
def wired(monkeypatch, session, storage):
    """Point process_call at the test session/storage (nullcontext: don't close the session)."""
    monkeypatch.setattr(processing_service, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(processing_service, "get_audio_storage", lambda: storage)


def test_process_call_transcribes_then_scores_and_completes_the_call(wired, session, make_call):
    call = make_call()
    processing_service.process_call(str(call.id))

    transcript = session.scalar(select(Transcript).where(Transcript.call_id == call.id))
    assert transcript is not None and len(transcript.segments) == len(FIXTURE)
    # Phase 7: scoring + gate now run after transcription. This call has no start time / check
    # library, so it is routed to QA review, and the pipeline still COMPLETES (a human decides).
    session.refresh(call)
    assert call.status is CallStatus.COMPLETED
    assert call.gate_decision.status is GateStatus.QA_REVIEW


def test_process_call_marks_call_failed_and_reraises(wired, monkeypatch, session, make_call):
    call = make_call()
    monkeypatch.setattr(processing_service, "get_transcription_provider", lambda: BrokenProvider())

    with pytest.raises(RuntimeError, match="stt exploded"):
        processing_service.process_call(str(call.id))

    assert session.get(Call, call.id).status is CallStatus.FAILED
    assert session.scalar(select(func.count()).select_from(Transcript).where(Transcript.call_id == call.id)) == 0


def test_post_call_end_to_end_produces_a_transcript(wired, session, storage):
    """POST /calls -> BackgroundTasks -> real process_call -> transcript rows."""
    repo = BaseRepository(Retailer, session)
    repo.create(code="tr_e2e_retailer", name="E2E")
    repo.commit()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_audio_storage] = lambda: storage
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/calls",
                data={"retailer_code": "tr_e2e_retailer", "external_lead_id": "9000002", "crm_fields": "{}"},
                files={"audio": ("call.wav", AUDIO, "audio/wav")},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 202 and resp.json()["status"] == "PROCESSING"
    call_id = resp.json()["call_id"]
    transcript = session.scalar(select(Transcript).where(Transcript.call_id == call_id))
    assert transcript is not None
    assert [s.sequence for s in transcript.segments] == list(range(1, len(FIXTURE) + 1))
