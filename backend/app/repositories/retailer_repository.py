from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CheckLibrary, Retailer
from app.repositories.base import BaseRepository


class AmbiguousCheckLibraryError(Exception):
    """More than one check library is active for a retailer at the same instant."""


class RetailerRepository(BaseRepository[Retailer]):
    def __init__(self, session: Session) -> None:
        super().__init__(Retailer, session)

    def get_active_check_library(
        self, retailer_id: int, as_of: datetime
    ) -> CheckLibrary | None:
        """The retailer's library in effect at `as_of`, or None if there isn't one.

        Active means effective_from <= as_of AND (effective_to IS NULL OR
        effective_to >= as_of). Raises AmbiguousCheckLibraryError if several
        match -- it never picks one, and never falls back to another version.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        stmt = (
            select(CheckLibrary)
            .where(
                CheckLibrary.retailer_id == retailer_id,
                CheckLibrary.effective_from <= as_of,
                or_(
                    CheckLibrary.effective_to.is_(None),
                    CheckLibrary.effective_to >= as_of,
                ),
            )
            .order_by(CheckLibrary.version)
        )
        matches = list(self.session.scalars(stmt))
        if len(matches) > 1:
            versions = [lib.version for lib in matches]
            raise AmbiguousCheckLibraryError(
                f"Retailer {retailer_id} has {len(matches)} check libraries "
                f"(versions {versions}) active at {as_of.isoformat()}"
            )
        return matches[0] if matches else None
