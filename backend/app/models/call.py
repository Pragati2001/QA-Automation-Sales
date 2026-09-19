import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.gate_decision import GateDecision
    from app.models.lead import Lead
    from app.models.recording import Recording
    from app.models.transcript import Transcript


class CallStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Call(TimestampMixin, Base):
    __tablename__ = "calls"
    __table_args__ = (Index("ix_calls_lead_id", "lead_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="RESTRICT"))
    # VARCHAR + CHECK rather than a native enum, same as Check.check_type.
    status: Mapped[CallStatus] = mapped_column(
        Enum(
            CallStatus,
            name="ck_calls_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=CallStatus.PROCESSING,
        server_default=CallStatus.PROCESSING.value,
    )

    # When the call started. It decides which check-library version applies (the rules that were
    # live on the call date). NULL = unknown: scoring then routes the call to QA review.
    call_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    lead: Mapped["Lead"] = relationship(back_populates="calls")
    recording: Mapped["Recording | None"] = relationship(back_populates="call")
    transcript: Mapped["Transcript | None"] = relationship(back_populates="call")
    gate_decision: Mapped["GateDecision | None"] = relationship(back_populates="call")

    def __repr__(self) -> str:
        return f"Call(id={self.id!r}, lead_id={self.lead_id!r}, status={self.status!r})"
