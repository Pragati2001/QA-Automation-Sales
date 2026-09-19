from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin
from app.schemas.check_result import CheckStatus

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.check import Check


class CheckResultRecord(TimestampMixin, Base):
    """One persisted check result: what one check concluded about one call.

    The row is the storage form of the `CheckResult` dataclass contract (schemas/check_result.py).
    The check's code / version / type / criticality are copied onto the row, so the result stays
    explainable exactly as it was evaluated even if the library is later superseded.
    """

    __tablename__ = "check_results"
    __table_args__ = (
        # A check is evaluated once per call: re-scoring must never add a second row or replace one.
        UniqueConstraint("call_id", "check_id", name="uq_check_results_call_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="RESTRICT"))
    check_id: Mapped[int] = mapped_column(ForeignKey("checks.id", ondelete="RESTRICT"))

    check_code: Mapped[str] = mapped_column(String(64))
    check_version: Mapped[int] = mapped_column(Integer)  # the check library version that was applied
    check_type: Mapped[str] = mapped_column(String(20))
    critical: Mapped[bool] = mapped_column(Boolean)

    status: Mapped[CheckStatus] = mapped_column(
        Enum(
            CheckStatus,
            name="ck_check_results_status",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    reason: Mapped[str] = mapped_column(Text)
    asr_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    rule_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    expected_value: Mapped[str | None] = mapped_column(Text, default=None)
    actual_value: Mapped[str | None] = mapped_column(Text, default=None)
    # [{"segment_id": "12", "start_time": 5.0, "end_time": 11.4, "text": "..."}, ...]
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=list,
    )

    call: Mapped["Call"] = relationship()
    check: Mapped["Check"] = relationship()

    def __repr__(self) -> str:
        return f"CheckResultRecord(id={self.id!r}, call_id={self.call_id!r}, check={self.check_code!r}, status={self.status!r})"
