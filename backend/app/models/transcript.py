from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.transcript_segment import TranscriptSegment


class Transcript(TimestampMixin, Base):
    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One transcript per call (unique); this also indexes the FK.
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="RESTRICT"), unique=True
    )
    # Which provider produced it (e.g. "fixture"); traceability for later results.
    provider: Mapped[str] = mapped_column(String(64))
    # True when redaction removed something (e.g. a spoken card number) before the segments were stored.
    is_redacted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    call: Mapped["Call"] = relationship(back_populates="transcript")
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        back_populates="transcript",
        order_by="TranscriptSegment.sequence",
    )

    def __repr__(self) -> str:
        return f"Transcript(id={self.id!r}, call_id={self.call_id!r}, provider={self.provider!r})"
