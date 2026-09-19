"""
TranscriptionProvider abstraction.

Today: FixtureTranscriptionProvider replays a synthetic transcript from JSON.
Later: a real speech-to-text provider implements the same interface.
The transcription service depends on this interface only, so swapping the
provider (see get_transcription_provider) touches nothing else.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptSegmentData:
    speaker: str  # e.g. "AGENT" / "CUSTOMER"
    text: str
    start_time: float  # seconds into the recording
    end_time: float
    confidence: float | None  # ASR confidence 0..1, None if the provider gives none


class TranscriptionProvider(ABC):
    name: str  # recorded on Transcript.provider for traceability

    @abstractmethod
    def transcribe(self, audio: bytes) -> list[TranscriptSegmentData]:
        """Return speaker-separated, timestamped segments for the audio."""
