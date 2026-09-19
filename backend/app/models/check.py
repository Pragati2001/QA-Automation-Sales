import enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Index, String, Text, UniqueConstraint, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.check_library import CheckLibrary


class CheckType(str, enum.Enum):
    VERBATIM = "VERBATIM"
    FACTUAL = "FACTUAL"
    BEHAVIOUR = "BEHAVIOUR"


class Check(TimestampMixin, Base):
    __tablename__ = "checks"
    __table_args__ = (
        UniqueConstraint("library_id", "code", name="uq_checks_library_code"),
        Index("ix_checks_library_check_type", "library_id", "check_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(
        ForeignKey("check_libraries.id", ondelete="RESTRICT")
    )
    # Stable identifier within a library, e.g. "GREETING_01".
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # Stored as VARCHAR + CHECK constraint (not a native PG enum) so adding a
    # type later is a plain migration instead of ALTER TYPE.
    check_type: Mapped[CheckType] = mapped_column(
        Enum(
            CheckType,
            name="ck_checks_check_type",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    critical: Mapped[bool] = mapped_column(
        Boolean, server_default=false(), default=False
    )
    # Check-type-specific rules (e.g. required phrase for VERBATIM, field
    # mapping for FACTUAL). Shape is validated by the check implementations.
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
    )

    library: Mapped["CheckLibrary"] = relationship(back_populates="checks")

    def __repr__(self) -> str:
        return f"Check(id={self.id!r}, library_id={self.library_id!r}, code={self.code!r})"
