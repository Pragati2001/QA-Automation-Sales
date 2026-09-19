import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import CheckResultRecord, GateStatus
from app.repositories.base import BaseRepository
from app.models import Recording
from app.schemas.check_result import CheckStatus
from app.services.gate.gate_service import decide_call
from app.services.scoring.scoring_service import score_call
from tests.services.scenario import (
    CRM_OK, DEAD_AIR, DISCLAIMER, DOB, EMAIL, JAN, RATE, add_library, make_call, make_retailer,
)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def retailer(session):
    return make_retailer(session, "review_retailer")


def scored_call(session, retailer, checks, **call_kwargs):
    add_library(session, retailer, 3, JAN, checks=checks)
    call = make_call(session, retailer, **call_kwargs)
    decide_call(session, call.id, score_call(session, call.id))
    session.commit()
    return call


def review(client, call_id):
    resp = client.get(f"/api/v1/calls/{call_id}/review")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_unknown_call_is_404(client):
    resp = client.get("/api/v1/calls/999999999/review")
    assert resp.status_code == 404 and "not found" in resp.json()["detail"]


def test_non_numeric_call_id_is_rejected(client):
    assert client.get("/api/v1/calls/abc/review").status_code == 422


def test_payload_has_call_lead_gate_decision_and_checks(client, session, retailer):
    call = scored_call(session, retailer, [DISCLAIMER, DOB, RATE])
    data = review(client, call.id)

    assert set(data) == {"call", "lead", "gate_decision", "checks"}
    assert data["call"]["id"] == call.id and data["call"]["status"] == "PROCESSING"
    assert data["call"]["call_started_at"] is not None and data["call"]["recording"] is None

    assert data["lead"]["external_lead_id"] == "1"
    assert data["lead"]["retailer"] == {"id": retailer.id, "code": "review_retailer", "name": "review_retailer"}
    assert "crm_fields" not in data["lead"] and "crm_fields" not in str(data)  # the CRM snapshot is not exposed

    gate = data["gate_decision"]
    assert gate["status"] == "AUTO_PASSED" and gate["check_library_version"] == 3
    assert gate["reason"].startswith("Auto-passed") and gate["decided_at"]
    assert len(data["checks"]) == 3


def test_each_check_has_identity_status_values_confidence_and_evidence(client, session, retailer):
    call = scored_call(session, retailer, [DISCLAIMER, DOB])
    checks = {c["check"]["code"]: c for c in review(client, call.id)["checks"]}
    dob = checks["dob_match"]

    assert dob["check"] == {"id": dob["check"]["id"], "code": "dob_match", "name": "dob_match",
                            "type": "FACTUAL", "critical": True, "version": 3}
    assert dob["status"] == "PASS" and dob["reason"]
    assert (dob["expected_value"], dob["actual_value"]) == ("1990-01-01", "the first of january  nineteen ninety")
    assert dob["confidence"] == {"asr": None, "extraction": 0.93, "rule": 1.0, "overall": 0.93}

    segment = next(s for s in call.transcript.segments if s.start_time == 21.4)
    assert dob["evidence"] == [{"segment_id": str(segment.id), "text": "It's the first of January, nineteen ninety.",
                                "start_time": 21.4, "end_time": 24.8}]

    disclaimer = checks["recording_disclaimer"]
    assert disclaimer["check"]["type"] == "VERBATIM"
    assert disclaimer["confidence"]["asr"] == 0.97 and disclaimer["confidence"]["rule"] is None
    assert disclaimer["confidence"]["overall"] == 0.97  # the lowest of the values that exist
    assert (disclaimer["evidence"][0]["start_time"], disclaimer["evidence"][0]["end_time"]) == (0.0, 7.4)


def test_failed_and_unresolved_checks_carry_their_reasons_and_values(client, session, retailer):
    call = scored_call(session, retailer, [DOB, RATE], crm={"dob": "1991-05-05"})  # wrong dob, no peak_rate
    data = review(client, call.id)
    checks = {c["check"]["code"]: c for c in data["checks"]}

    assert data["gate_decision"]["status"] == "HELD"
    assert checks["dob_match"]["status"] == "FAIL"
    assert (checks["dob_match"]["expected_value"], checks["dob_match"]["actual_value"]) == (
        "1991-05-05", "the first of january  nineteen ninety")
    assert checks["rate_match"]["status"] == "NOT_CHECKABLE"
    assert "Expected value not available" in checks["rate_match"]["reason"]
    assert checks["rate_match"]["evidence"] == []
    assert checks["rate_match"]["confidence"] == {"asr": None, "extraction": None, "rule": None, "overall": None}


def test_checks_come_back_in_a_stable_order_with_problems_first(client, session, retailer):
    """FAIL, LOW_CONFIDENCE, NOT_CHECKABLE, PASS; within a status critical checks first, then by code."""
    optional_unchecked = dict(RATE, code="a_optional", critical=False,
                              configuration={"field": "phone", "source_of_truth": "CRM.phone"})
    call = scored_call(session, retailer, [DISCLAIMER, DOB, RATE, EMAIL, optional_unchecked],
                       crm={"dob": "1991-05-05", "email": CRM_OK["email"]})  # dob FAIL, rate NOT_CHECKABLE
    # email: force LOW_CONFIDENCE after scoring
    session.query(CheckResultRecord).filter_by(call_id=call.id, check_code="email_match").update(
        {"status": CheckStatus.LOW_CONFIDENCE})
    session.commit()

    order = [(c["status"], c["check"]["code"]) for c in review(client, call.id)["checks"]]

    assert order == [
        ("FAIL", "dob_match"),
        ("LOW_CONFIDENCE", "email_match"),
        ("NOT_CHECKABLE", "rate_match"),        # critical before non-critical
        ("NOT_CHECKABLE", "a_optional"),
        ("PASS", "recording_disclaimer"),
    ]
    # ...and identical on every request
    assert [(c["status"], c["check"]["code"]) for c in review(client, call.id)["checks"]] == order


def test_behaviour_results_are_included_and_sorted_with_the_rest(client, session, retailer):
    """Phase 9: behaviour checks produce results, so the review page shows them (non-critical, never FAIL)."""
    call = scored_call(session, retailer, [DISCLAIMER, DEAD_AIR])
    checks = review(client, call.id)["checks"]

    assert [(c["status"], c["check"]["code"]) for c in checks] == [
        ("NOT_CHECKABLE", "dead_air"),  # no threshold configured in the demo fixture
        ("PASS", "recording_disclaimer"),
    ]
    assert checks[0]["check"]["type"] == "BEHAVIOUR" and checks[0]["check"]["critical"] is False


def test_a_call_still_processing_has_no_decision_and_no_checks(client, session, retailer):
    call = make_call(session, retailer)  # not scored yet
    data = review(client, call.id)
    assert data["gate_decision"] is None and data["checks"] == []
    assert data["call"]["status"] == "PROCESSING"


def test_a_call_routed_to_qa_because_there_is_no_library_still_reviews_cleanly(client, session, retailer):
    call = make_call(session, retailer)
    decide_call(session, call.id, score_call(session, call.id))
    session.commit()

    data = review(client, call.id)
    assert data["gate_decision"]["status"] == GateStatus.QA_REVIEW.value
    assert data["gate_decision"]["check_library_version"] is None
    assert data["checks"] == []


def test_recording_metadata_is_included_but_not_the_storage_path(client, session, retailer):
    call = make_call(session, retailer)
    BaseRepository(Recording, session).create(
        call_id=call.id, storage_reference="C:/secret/place/a.wav", original_filename="a.wav",
        content_type="audio/wav", size_bytes=1234,
    )
    session.commit()

    data = review(client, call.id)
    assert data["call"]["recording"] == {"original_filename": "a.wav", "content_type": "audio/wav", "size_bytes": 1234}
    assert "secret" not in str(data)
