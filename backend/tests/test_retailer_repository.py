from datetime import datetime, timedelta, timezone

import pytest

from app.models import CheckLibrary, Retailer
from app.repositories.base import BaseRepository
from app.repositories.retailer_repository import (
    AmbiguousCheckLibraryError,
    RetailerRepository,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


@pytest.fixture
def repo(session):
    return RetailerRepository(session)


@pytest.fixture
def retailer(session):
    return BaseRepository(Retailer, session).create(code="acme", name="Acme")


def add_library(session, retailer, version, effective_from, effective_to=None):
    return BaseRepository(CheckLibrary, session).create(
        retailer_id=retailer.id,
        name=f"v{version}",
        version=version,
        effective_from=effective_from,
        effective_to=effective_to,
    )


def test_open_ended_library_is_active(session, repo, retailer):
    lib = add_library(session, retailer, 1, T0)
    assert repo.get_active_check_library(retailer.id, T0 + 30 * DAY) is lib


def test_bounded_library_is_active_within_range(session, repo, retailer):
    lib = add_library(session, retailer, 1, T0, T0 + 10 * DAY)
    assert repo.get_active_check_library(retailer.id, T0 + 5 * DAY) is lib


def test_boundaries_are_inclusive(session, repo, retailer):
    lib = add_library(session, retailer, 1, T0, T0 + 10 * DAY)
    assert repo.get_active_check_library(retailer.id, T0) is lib
    assert repo.get_active_check_library(retailer.id, T0 + 10 * DAY) is lib


def test_expired_library_returns_none(session, repo, retailer):
    add_library(session, retailer, 1, T0, T0 + 10 * DAY)
    assert repo.get_active_check_library(retailer.id, T0 + 11 * DAY) is None


def test_not_yet_effective_library_returns_none(session, repo, retailer):
    add_library(session, retailer, 1, T0 + 10 * DAY)
    assert repo.get_active_check_library(retailer.id, T0) is None


def test_expired_library_does_not_fall_back_to_older_version(session, repo, retailer):
    add_library(session, retailer, 1, T0, T0 + 10 * DAY)
    add_library(session, retailer, 2, T0 + 20 * DAY, T0 + 30 * DAY)
    # Gap between v1 and v2: no library applies, and v1 must not be substituted.
    assert repo.get_active_check_library(retailer.id, T0 + 15 * DAY) is None


def test_picks_the_version_in_effect_at_as_of(session, repo, retailer):
    v1 = add_library(session, retailer, 1, T0, T0 + 10 * DAY - timedelta(seconds=1))
    v2 = add_library(session, retailer, 2, T0 + 10 * DAY)
    assert repo.get_active_check_library(retailer.id, T0 + 5 * DAY) is v1
    assert repo.get_active_check_library(retailer.id, T0 + 20 * DAY) is v2


def test_missing_retailer_returns_none(repo):
    assert repo.get_active_check_library(999_999_999, T0) is None


def test_retailer_without_libraries_returns_none(repo, retailer):
    assert repo.get_active_check_library(retailer.id, T0) is None


def test_other_retailers_libraries_are_ignored(session, repo, retailer):
    other = BaseRepository(Retailer, session).create(code="other", name="Other")
    add_library(session, other, 1, T0)
    assert repo.get_active_check_library(retailer.id, T0 + DAY) is None


def test_overlapping_libraries_raise(session, repo, retailer):
    add_library(session, retailer, 1, T0, T0 + 20 * DAY)
    add_library(session, retailer, 2, T0 + 10 * DAY)
    with pytest.raises(AmbiguousCheckLibraryError, match=r"versions \[1, 2\]"):
        repo.get_active_check_library(retailer.id, T0 + 15 * DAY)


def test_overlap_only_raises_inside_the_overlap_window(session, repo, retailer):
    v1 = add_library(session, retailer, 1, T0, T0 + 20 * DAY)
    add_library(session, retailer, 2, T0 + 10 * DAY)
    assert repo.get_active_check_library(retailer.id, T0 + 5 * DAY) is v1


def test_shared_boundary_instant_is_ambiguous(session, repo, retailer):
    """effective_to is inclusive, so a hand-off at the same instant overlaps."""
    add_library(session, retailer, 1, T0, T0 + 10 * DAY)
    add_library(session, retailer, 2, T0 + 10 * DAY)
    with pytest.raises(AmbiguousCheckLibraryError):
        repo.get_active_check_library(retailer.id, T0 + 10 * DAY)


def test_naive_as_of_is_rejected(repo, retailer):
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.get_active_check_library(retailer.id, datetime(2026, 1, 1))
