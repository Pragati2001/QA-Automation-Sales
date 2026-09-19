from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Thin data-access wrapper around one model. No business logic here."""

    def __init__(self, model: type[ModelT], session: Session) -> None:
        self.model = model
        self.session = session

    def get(self, id: int) -> ModelT | None:
        return self.session.get(self.model, id)

    def list(self, **filters: Any) -> list[ModelT]:
        """Equality filters on column names, e.g. list(retailer_id=1)."""
        stmt = select(self.model).filter_by(**filters)
        return list(self.session.scalars(stmt))

    def create(self, **kwargs: Any) -> ModelT:
        """Add and flush (so the PK is populated); call commit() to persist."""
        obj = self.model(**kwargs)
        self.session.add(obj)
        self.session.flush()
        return obj

    def commit(self) -> None:
        self.session.commit()
