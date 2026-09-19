from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.transcript import Transcript


class TranscriptSegment(TimestampMixin, Base):
    """One speaker turn. `sequence` (1-based) is the order within the transcript."""

    __tablename__ = "transcript_segments"
    __table_args__ = (
        # Also indexes transcript_id for "all segments of a transcript, in order".
        UniqueConstraint(
            "transcript_id", "sequence", name="uq_transcript_segments_transcript_sequence"
        ),
        CheckConstraint(
            "end_time >= start_time", name="ck_transcript_segments_time_order"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_transcript_segments_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(
        ForeignKey("transcripts.id", ondelete="RESTRICT")
    )
    sequence: Mapped[int]
    speaker: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    # Seconds into the recording.
    start_time: Mapped[float] = mapped_column(Float)
    end_time: Mapped[float] = mapped_column(Float)
    # ASR confidence, 0..1; NULL when the provider doesn't supply one.
    confidence: Mapped[float | None] = mapped_column(Float, default=None)

    transcript: Mapped["Transcript"] = relationship(back_populates="segments")

    def __repr__(self) -> str:
        return (
            f"TranscriptSegment(id={self.id!r}, transcript_id={self.transcript_id!r}, "
            f"sequence={self.sequence!r})"
        )
