import json
from pathlib import Path

from app.services.transcription.base import TranscriptionProvider, TranscriptSegmentData

BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = BACKEND_DIR / "data/fixtures/synthetic_transcript.json"


class FixtureTranscriptionProvider(TranscriptionProvider):
    """Ignores the audio and replays the same synthetic transcript for every call."""

    name = "fixture"

    def __init__(self, fixture_path: Path | str = DEFAULT_FIXTURE) -> None:
        self.fixture_path = Path(fixture_path)

    def transcribe(self, audio: bytes) -> list[TranscriptSegmentData]:
        data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return [
            TranscriptSegmentData(
                speaker=s["speaker"],
                text=s["text"],
                start_time=float(s["start_time"]),
                end_time=float(s["end_time"]),
                confidence=s.get("confidence"),
            )
            for s in data["segments"]
        ]
