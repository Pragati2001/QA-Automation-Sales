import json
from datetime import datetime, timezone
from pathlib import Path

from app.models import CheckType
from app.repositories.retailer_repository import RetailerRepository
from app.services.checks.library_loader import load_check_library_from_json

FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/retailer1_check_library_v1.json"


def test_retailer1_fixture_loads_through_the_loader(session):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # The dev DB may already hold the real retailer1 v1 (scripts/load_check_library.py),
    # so load the fixture under a throwaway retailer code to avoid a version clash.
    data["retailer"]["code"] = "retailer1_test"
    library = load_check_library_from_json(session, data)

    assert (library.retailer.code, library.version, library.effective_to) == ("retailer1_test", 1, None)
    by_code = {c.code: c for c in library.checks}
    assert set(by_code) == {
        "recording_disclaimer", "dmo_verbatim", "rate_match",
        "email_match", "dob_match", "dead_air",
    }
    assert {c.code for c in library.checks if c.critical} == set(by_code) - {"dead_air"}
    assert by_code["recording_disclaimer"].check_type is CheckType.VERBATIM
    assert by_code["rate_match"].check_type is CheckType.FACTUAL
    assert by_code["dead_air"].check_type is CheckType.BEHAVIOUR
    assert by_code["email_match"].configuration == {"compare_to": "crm_field", "field": "email"}

    as_of = datetime(2026, 9, 18, tzinfo=timezone.utc)
    assert RetailerRepository(session).get_active_check_library(library.retailer_id, as_of) is library
