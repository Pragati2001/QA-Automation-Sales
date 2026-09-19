from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.check_library import CheckLibrary


class Retailer(TimestampMixin, Base):
    __tablename__ = "retailers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable business identifier (slug), e.g. "acme-motors".
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=true(), default=True
    )

    check_libraries: Mapped[list["CheckLibrary"]] = relationship(
        back_populates="retailer",
        order_by="CheckLibrary.version",
    )

    def __repr__(self) -> str:
        return f"Retailer(id={self.id!r}, code={self.code!r})"
