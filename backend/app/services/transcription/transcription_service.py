from dataclasses import asdict

from sqlalchemy.orm import Session

from app.models import Call, Transcript, TranscriptSegment
from app.repositories.base import BaseRepository
from app.services.redaction.redaction_service import redact_segments
from app.services.storage.audio_storage import AudioStorage
from app.services.transcription.base import TranscriptionProvider, TranscriptSegmentData
from app.services.transcription.fixture_provider import FixtureTranscriptionProvider


def get_transcription_provider() -> TranscriptionProvider:
    """Swap point: return a real STT provider here later, nothing else changes."""
    return FixtureTranscriptionProvider()


def transcribe_call(
    session: Session,
    call_id: int,
    provider: TranscriptionProvider,
    storage: AudioStorage,
) -> Transcript:
    """Create the call's Transcript and its ordered TranscriptSegments, then commit.

    Sensitive values (card numbers) are redacted before storing and `Transcript.is_redacted`
    records that it happened. Idempotent: if the call already has a transcript it is returned
    untouched, so re-running the pipeline never creates a second one.
    """
    transcripts = BaseRepository(Transcript, session)
    existing = next(iter(transcripts.list(call_id=call_id)), None)
    if existing:
        return existing

    call = BaseRepository(Call, session).get(call_id)
    if call is None:
        raise ValueError(f"Call {call_id} not found")
    if call.recording is None:
        raise ValueError(f"Call {call_id} has no recording to transcribe")

    audio = storage.get(call.recording.storage_reference)
    # Redact BEFORE anything is stored, so a spoken card number never reaches the database.
    redacted, was_redacted = redact_segments([asdict(s) for s in provider.transcribe(audio)])
    # Order by start time (stable, so ties keep the provider's order); crosstalk
    # segments may overlap in time but each still gets its own sequence number.
    segments = sorted((TranscriptSegmentData(**d) for d in redacted), key=lambda s: s.start_time)

    try:
        transcript = transcripts.create(call_id=call_id, provider=provider.name, is_redacted=was_redacted)
        session.add_all(
            TranscriptSegment(
                transcript_id=transcript.id,
                sequence=i,
                speaker=s.speaker,
                text=s.text,
                start_time=s.start_time,
                end_time=s.end_time,
                confidence=s.confidence,
            )
            for i, s in enumerate(segments, start=1)
        )
        transcripts.commit()
    except Exception:
        session.rollback()
        raise
    return transcript
