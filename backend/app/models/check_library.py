from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.check import Check
    from app.models.retailer import Retailer


class CheckLibrary(TimestampMixin, Base):
    """A versioned set of checks for one retailer.

    A library version is valid from `effective_from` (inclusive) until
    `effective_to` (inclusive); `effective_to IS NULL` means open-ended, i.e.
    the currently active version. Non-overlap of a retailer's versions is
    not enforced here -- that belongs in the service layer.
    """

    __tablename__ = "check_libraries"
    __table_args__ = (
        UniqueConstraint("retailer_id", "version", name="uq_check_libraries_retailer_version"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_check_libraries_effective_range",
        ),
        # "Which library version applied to this retailer at time T?"
        Index(
            "ix_check_libraries_retailer_effective",
            "retailer_id",
            "effective_from",
            "effective_to",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: libraries (and the checks under them) are audit history that
    # later results will reference, so they must not vanish with a retailer.
    retailer_id: Mapped[int] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int]
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    retailer: Mapped["Retailer"] = relationship(back_populates="check_libraries")
    checks: Mapped[list["Check"]] = relationship(
        back_populates="library",
        order_by="Check.code",
    )

    def __repr__(self) -> str:
        return (
            f"CheckLibrary(id={self.id!r}, retailer_id={self.retailer_id!r}, "
            f"version={self.version!r})"
        )
