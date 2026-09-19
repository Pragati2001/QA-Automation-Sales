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


# --- Retailer 1 v2: the same demo library with config the runners can actually use --------------

V2_FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/retailer1_check_library_v2.json"
TRANSCRIPT_FIXTURE = Path(__file__).resolve().parents[1] / "data/fixtures/synthetic_transcript.json"


def test_retailer1_v2_fixture_config_is_usable_for_every_check(session):
    """No check may come back NOT_CHECKABLE from bad config (the v1 fixture's problem). Against the synthetic
    transcript: disclaimer/rate/dob PASS, the placeholder DMO FAILs (absent wording, not missing config), the
    email is LOW_CONFIDENCE (the fixture's deliberate 0.62 ASR segment), and dead air is a passing coaching note."""
    from app.schemas.check_result import CheckStatus
    from app.services.scoring.scoring_service import score_call
    from tests.services.scenario import make_call

    data = json.loads(V2_FIXTURE.read_text(encoding="utf-8"))
    assert data["library"]["version"] == 2  # a NEW version: v1 is never edited in place
    data["retailer"]["code"] = "retailer1_v2_test"  # the dev DB may already hold the real retailer1
    data["library"]["effective_from"] = "2026-01-01T00:00:00+00:00"  # so the scenario's call date falls inside it
    library = load_check_library_from_json(session, data)

    segments = [
        (s["speaker"], s["text"], s["start_time"], s["end_time"], s["confidence"])
        for s in json.loads(TRANSCRIPT_FIXTURE.read_text(encoding="utf-8"))["segments"]
    ]
    crm = {"email": "synthetic.test@example.com", "dob": "1990-01-01", "rate": "31.9c/kWh"}  # the keys the checks read
    call = make_call(session, library.retailer, crm=crm, segments=segments)

    outcome = score_call(session, call.id)
    status = {r.check_id: r.status for r in outcome.results}

    assert status == {
        "recording_disclaimer": CheckStatus.PASS,
        "dmo_verbatim": CheckStatus.FAIL,
        "rate_match": CheckStatus.PASS,
        "email_match": CheckStatus.LOW_CONFIDENCE,
        "dob_match": CheckStatus.PASS,
        "dead_air": CheckStatus.PASS,
    }
    assert CheckStatus.NOT_CHECKABLE not in status.values()
    assert outcome.errors == [] and outcome.library.version == 2
    assert not any("configuration is unusable" in r.reason for r in outcome.results)


def test_the_v1_fixture_is_left_as_it_was_and_v2_does_not_reuse_its_version():
    v1 = json.loads(FIXTURE.read_text(encoding="utf-8"))
    v2 = json.loads(V2_FIXTURE.read_text(encoding="utf-8"))
    assert (v1["library"]["version"], v2["library"]["version"]) == (1, 2)
    assert {c["code"] for c in v1["checks"]} == {c["code"] for c in v2["checks"]}
    assert v1["checks"][0]["configuration"] == {"approved_script": None}  # v1 is the historical, unusable shape
