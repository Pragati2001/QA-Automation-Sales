from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models._mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.retailer import Retailer


class Lead(TimestampMixin, Base):
    """A CRM lead, identified by the CRM's own id within a retailer."""

    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint(
            "retailer_id", "external_lead_id", name="uq_leads_retailer_external_lead_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    retailer_id: Mapped[int] = mapped_column(
        ForeignKey("retailers.id", ondelete="RESTRICT")
    )
    external_lead_id: Mapped[str] = mapped_column(String(64))
    # Latest CRM snapshot (factual checks compare the transcript against this).
    crm_fields: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
    )

    retailer: Mapped["Retailer"] = relationship()
    calls: Mapped[list["Call"]] = relationship(
        back_populates="lead",
        order_by="Call.id",
    )

    def __repr__(self) -> str:
        return f"Lead(id={self.id!r}, external_lead_id={self.external_lead_id!r})"
