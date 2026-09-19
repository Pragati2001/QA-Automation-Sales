"""
AudioStorage abstraction.

Today: LocalAudioStorage writes to disk.
Later: AzureBlobAudioStorage implements the same interface.
Nothing downstream (transcription, scoring) should import a concrete
implementation directly -- depend on this interface instead.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from app.config import settings


class AudioStorage(ABC):
    @abstractmethod
    def save(self, call_id: str, filename: str, content: bytes) -> str:
        """Persist audio bytes, return a storage reference (path or URL)."""

    @abstractmethod
    def get(self, reference: str) -> bytes:
        """Retrieve audio bytes by storage reference."""

    @abstractmethod
    def delete(self, reference: str) -> None:
        """Remove audio at the given reference."""


class LocalAudioStorage(AudioStorage):
    def __init__(self, base_path: str | None = None, subdir: str = "raw"):
        self.base_path = Path(base_path or settings.audio_storage_path) / subdir
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save(self, call_id: str, filename: str, content: bytes) -> str:
        call_dir = self.base_path / call_id
        call_dir.mkdir(parents=True, exist_ok=True)
        file_path = call_dir / filename
        file_path.write_bytes(content)
        return str(file_path)

    def get(self, reference: str) -> bytes:
        return Path(reference).read_bytes()

    def delete(self, reference: str) -> None:
        Path(reference).unlink(missing_ok=True)


def get_audio_storage() -> AudioStorage:
    """Swap point: return AzureBlobAudioStorage() here later, nothing else changes."""
    return LocalAudioStorage()
