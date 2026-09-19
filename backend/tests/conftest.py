import pytest
from sqlalchemy.orm import Session

from app.db import engine


@pytest.fixture
def session():
    """A Session on the real Postgres, rolled back after each test.

    Postgres (not SQLite) on purpose: the tests depend on timestamptz semantics
    and the real constraints. Requires the migrated DB from DATABASE_URL, and
    must be run from backend/ so .env is found.
    """
    connection = engine.connect()
    transaction = connection.begin()
    # create_savepoint lets code under test call commit() without ending our transaction.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
