import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Check, CheckLibrary, CheckType, Retailer
from app.repositories.retailer_repository import RetailerRepository
from app.services.checks.library_loader import load_check_library_from_json

PAYLOAD = {
    "retailer": {"code": "acme", "name": "Acme"},
    "library": {
        "name": "Acme checklist",
        "version": 1,
        "effective_from": "2026-01-01T00:00:00+00:00",
        "effective_to": None,
    },
    "checks": [
        {
            "code": "GREETING_01",
            "name": "Greeting",
            "description": "Agent greets the customer",
            "check_type": "VERBATIM",
            "critical": True,
            "configuration": {"phrase": "Thank you for calling", "case_sensitive": False},
        },
        {
            "code": "PRICE_01",
            "name": "Quoted price",
            "check_type": "FACTUAL",
            "configuration": {"field": "price", "tolerance": 0.5},
        },
        {"code": "TONE_01", "name": "Polite tone", "check_type": "BEHAVIOUR"},
    ],
}


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_creates_retailer_library_and_checks(session):
    library = load_check_library_from_json(session, deepcopy(PAYLOAD))

    assert library.id is not None
    assert (library.retailer.code, library.retailer.name) == ("acme", "Acme")
    assert (library.name, library.version) == ("Acme checklist", 1)
    assert library.effective_from == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert library.effective_to is None
    assert [c.code for c in library.checks] == ["GREETING_01", "PRICE_01", "TONE_01"]


def test_check_fields_and_configuration_are_stored(session):
    library = load_check_library_from_json(session, deepcopy(PAYLOAD))
    greeting, price, tone = library.checks

    assert greeting.check_type is CheckType.VERBATIM and greeting.critical is True
    assert greeting.description == "Agent greets the customer"
    assert greeting.configuration == {"phrase": "Thank you for calling", "case_sensitive": False}
    assert price.check_type is CheckType.FACTUAL and price.critical is False
    assert price.configuration == {"field": "price", "tolerance": 0.5}
    assert tone.check_type is CheckType.BEHAVIOUR
    assert tone.description is None and tone.configuration == {}


def test_reuses_existing_retailer_for_a_new_version(session):
    v1 = load_check_library_from_json(session, deepcopy(PAYLOAD))
    retailers_before, libraries_before = count(session, Retailer), count(session, CheckLibrary)
    v2_payload = deepcopy(PAYLOAD)
    v2_payload["retailer"]["name"] = "Renamed"  # existing retailer is not modified
    v2_payload["library"].update(version=2, effective_from="2026-06-01T00:00:00+00:00")
    v2 = load_check_library_from_json(session, v2_payload)

    assert v2.retailer_id == v1.retailer_id
    assert count(session, Retailer) == retailers_before
    assert v2.retailer.name == "Acme"
    assert count(session, CheckLibrary) == libraries_before + 1


def test_loaded_library_is_found_by_active_lookup(session):
    library = load_check_library_from_json(session, deepcopy(PAYLOAD))
    as_of = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert RetailerRepository(session).get_active_check_library(library.retailer_id, as_of) is library


def test_loads_from_a_json_file(session, tmp_path):
    path = tmp_path / "library.json"
    path.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    library = load_check_library_from_json(session, path)
    assert len(library.checks) == 3


def test_duplicate_version_fails_and_persists_nothing(session):
    load_check_library_from_json(session, deepcopy(PAYLOAD))
    retailers_before, libraries_before, checks_before = (
        count(session, m) for m in (Retailer, CheckLibrary, Check)
    )

    with pytest.raises(IntegrityError):
        load_check_library_from_json(session, deepcopy(PAYLOAD))  # same retailer + version

    assert count(session, Retailer) == retailers_before
    assert count(session, CheckLibrary) == libraries_before
    assert count(session, Check) == checks_before


def test_bad_check_rolls_back_the_whole_load(session):
    payload = deepcopy(PAYLOAD)
    payload["checks"][2]["check_type"] = "NOT_A_TYPE"
    before = [count(session, m) for m in (Retailer, CheckLibrary, Check)]

    with pytest.raises(ValueError):
        load_check_library_from_json(session, payload)

    assert [count(session, m) for m in (Retailer, CheckLibrary, Check)] == before
