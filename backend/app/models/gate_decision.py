import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.check_library import CheckLibrary


class GateStatus(str, enum.Enum):
    AUTO_PASSED = "AUTO_PASSED"  # every critical check passed: the sale goes through
    HELD = "HELD"  # a critical check failed: held for the team leader
    QA_REVIEW = "QA_REVIEW"  # uncertain or incomplete: a human decides


class GateDecision(TimestampMixin, Base):
    """The outcome of the gate for one call. Append-only audit record: there is at most one
    per call (unique `call_id`) and nothing in the application updates or replaces it."""

    __tablename__ = "gate_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[GateStatus] = mapped_column(
        Enum(
            GateStatus,
            name="ck_gate_decisions_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    reason: Mapped[str] = mapped_column(Text)  # the reasoning summary
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Which library version the checks came from; NULL when no library could be applied.
    check_library_id: Mapped[int | None] = mapped_column(
        ForeignKey("check_libraries.id", ondelete="RESTRICT"), default=None
    )
    check_library_version: Mapped[int | None] = mapped_column(Integer, default=None)

    call: Mapped["Call"] = relationship(back_populates="gate_decision")
    check_library: Mapped["CheckLibrary | None"] = relationship()

    def __repr__(self) -> str:
        return f"GateDecision(id={self.id!r}, call_id={self.call_id!r}, status={self.status!r})"
