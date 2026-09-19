from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.call import Call


class Recording(TimestampMixin, Base):
    """The audio file for a call; the bytes live in AudioStorage, not the DB."""

    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # One recording per call (unique); this also indexes the FK.
    call_id: Mapped[int] = mapped_column(
        ForeignKey("calls.id", ondelete="RESTRICT"), unique=True
    )
    # Opaque reference returned by AudioStorage.save (a path today, a URL later).
    storage_reference: Mapped[str] = mapped_column(String(1024))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(255), default=None)
    size_bytes: Mapped[int] = mapped_column(BigInteger)

    call: Mapped["Call"] = relationship(back_populates="recording")

    def __repr__(self) -> str:
        return f"Recording(id={self.id!r}, call_id={self.call_id!r})"
