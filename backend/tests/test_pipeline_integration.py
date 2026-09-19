"""
Scoring -> gate against fixture data. Calls the real service functions (score_call, decide_call)
directly; the HTTP and background-task layers have their own tests.

(The brief's `run_checks` / `apply_gate` are named `score_call` / `decide_call` in the code.)
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.main import app
from app.models import CheckResultRecord, CheckType, GateStatus
from app.schemas.check_result import CheckStatus
from app.services.gate.gate_service import decide_call
from app.services.scoring.scoring_service import score_call
from tests.services.scenario import (
    CRM_OK, DEFAULT_SEGMENTS, DISCLAIMER, DOB, EMAIL, JAN, RATE, add_library, make_call, make_retailer,
    with_confidence,
)

# --- checks beyond the shared scenario ---------------------------------------------------------

# The Phase 6 case: the agent's FIRST disclosure of the total minimum cost is wrong.
TOTAL_MINIMUM_COST = dict(
    code="total_minimum_cost_disclosure", type=CheckType.FACTUAL, critical=True,
    configuration={"field": "total_minimum_cost", "source_of_truth": "CRM.total_minimum_cost"},
)
DEAD_AIR = dict(code="dead_air", type=CheckType.BEHAVIOUR, critical=False,
                configuration={"signal": "dead_air", "threshold_seconds": 10})
OVERLAP = dict(code="overlapping_speech", type=CheckType.BEHAVIOUR, critical=False,
               configuration={"signal": "overlapping_speech"})
FILLERS = dict(code="filler_ratio", type=CheckType.BEHAVIOUR, critical=False,
               configuration={"signal": "filler_ratio", "max_ratio": 0.02})

# The real fixture-shaped call, plus the agent quoting the total minimum cost (wrongly, then "correcting"
# it later) and a customer interruption that overlaps the agent.
COST_SEGMENTS = [
    *DEFAULT_SEGMENTS,
    ("AGENT", "The total minimum cost over the term is $1,100.", 100.0, 106.0, 0.96),
    ("CUSTOMER", "Sorry to interrupt, how long is the term?", 104.0, 108.0, 0.95),
    ("AGENT", "Looking at the screen, the total minimum cost is $1,250.", 140.0, 146.0, 0.95),
]
CRM_WITH_COST = {**CRM_OK, "total_minimum_cost": 1250}


@pytest.fixture
def retailer(session):
    return make_retailer(session, "integration_retailer")


def run_pipeline(session, retailer, checks, **call_kwargs):
    add_library(session, retailer, 1, JAN, checks=checks)
    call = make_call(session, retailer, **call_kwargs)
    scoring = score_call(session, call.id)
    decision = decide_call(session, call.id, scoring)
    session.commit()
    return call, scoring, decision


def results(scoring):
    return {r.check_id: r for r in scoring.results}


# --- the routing scenarios -----------------------------------------------------------------------

def test_all_checks_pass_is_auto_passed(session, retailer):
    _, scoring, decision = run_pipeline(session, retailer, [DISCLAIMER, DOB, RATE, EMAIL])

    assert {r.status for r in scoring.results} == {CheckStatus.PASS}
    assert decision.status is GateStatus.AUTO_PASSED
    assert "4 PASS" in decision.reason


def test_one_critical_fail_is_held_and_its_evidence_is_exactly_right(session, retailer):
    call, scoring, decision = run_pipeline(
        session, retailer, [DISCLAIMER, DOB, RATE, TOTAL_MINIMUM_COST],
        crm=CRM_WITH_COST, segments=COST_SEGMENTS,
    )

    failed = results(scoring)["total_minimum_cost_disclosure"]
    assert failed.status is CheckStatus.FAIL and failed.critical is True
    assert decision.status is GateStatus.HELD
    assert "total_minimum_cost_disclosure" in decision.reason
    # the other critical checks still passed: this one failure alone holds the sale
    assert {r.status for code, r in results(scoring).items() if code != "total_minimum_cost_disclosure"} == {CheckStatus.PASS}

    # FIRST disclosure wins (not the later figure that happens to match): $1,100 said at 100.0-106.0
    first_disclosure = next(s for s in call.transcript.segments if s.start_time == 100.0)
    expected_evidence = {"segment_id": str(first_disclosure.id), "start_time": 100.0, "end_time": 106.0,
                         "text": "The total minimum cost over the term is $1,100."}
    assert (failed.expected_value, failed.actual_value) == ("1250", "$1,100")

    persisted = session.scalar(select(CheckResultRecord).where(
        CheckResultRecord.call_id == call.id, CheckResultRecord.check_code == "total_minimum_cost_disclosure"))
    assert persisted.evidence == [expected_evidence]  # what was stored
    assert [vars_of(e) for e in failed.evidence] == [expected_evidence]  # what the runner returned


def vars_of(evidence):
    return {"segment_id": evidence.segment_id, "start_time": evidence.start_time,
            "end_time": evidence.end_time, "text": evidence.text}


def test_the_held_evidence_reaches_the_review_api_in_one_request(session, retailer):
    call, _, _ = run_pipeline(session, retailer, [DISCLAIMER, TOTAL_MINIMUM_COST], crm=CRM_WITH_COST, segments=COST_SEGMENTS)
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as client:
            resp = client.get(f"/api/v1/calls/{call.id}/review")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["gate_decision"]["status"] == "HELD" and "total_minimum_cost_disclosure" in data["gate_decision"]["reason"]
    held = data["checks"][0]  # problems sort first
    first_disclosure = next(s for s in call.transcript.segments if s.start_time == 100.0)
    assert held["check"]["code"] == "total_minimum_cost_disclosure" and held["status"] == "FAIL"
    assert held["check"]["type"] == "FACTUAL" and held["check"]["critical"] is True
    assert (held["expected_value"], held["actual_value"]) == ("1250", "$1,100")
    assert held["evidence"] == [{"segment_id": str(first_disclosure.id), "start_time": 100.0, "end_time": 106.0,
                                 "text": "The total minimum cost over the term is $1,100."}]


def test_one_low_confidence_result_is_qa_review(session, retailer):
    _, scoring, decision = run_pipeline(
        session, retailer, [DISCLAIMER, DOB, EMAIL], segments=with_confidence(DEFAULT_SEGMENTS, 6, 0.5)
    )

    assert results(scoring)["email_match"].status is CheckStatus.LOW_CONFIDENCE
    assert results(scoring)["dob_match"].status is CheckStatus.PASS
    assert decision.status is GateStatus.QA_REVIEW
    assert "low confidence on email_match" in decision.reason


def test_one_critical_not_checkable_is_qa_review(session, retailer):
    _, scoring, decision = run_pipeline(session, retailer, [DISCLAIMER, DOB, RATE], crm={"dob": "1990-01-01"})

    assert results(scoring)["rate_match"].status is CheckStatus.NOT_CHECKABLE  # no peak_rate in the CRM
    assert decision.status is GateStatus.QA_REVIEW
    assert "could not be checked: rate_match" in decision.reason


# --- behaviour: a flagged coaching note must never stop an auto-pass -----------------------------

def test_flagged_behaviour_notes_alongside_all_passing_checks_are_still_auto_passed(session, retailer):
    call, scoring, decision = run_pipeline(
        session, retailer, [DISCLAIMER, DOB, RATE, EMAIL, DEAD_AIR, OVERLAP, FILLERS],
        crm=CRM_WITH_COST, segments=COST_SEGMENTS,
    )
    by_code = results(scoring)

    # dead air (a 52 s silence) and overlap (104-106 s) fired; the filler check found no fillers, so it passes quietly
    assert by_code["dead_air"].reason.startswith("Coaching note")
    assert by_code["overlapping_speech"].reason.startswith("Coaching note")
    for code in ("dead_air", "overlapping_speech", "filler_ratio"):
        assert by_code[code].check_type == "BEHAVIOUR"
        assert by_code[code].critical is False
        assert by_code[code].status in (CheckStatus.PASS, CheckStatus.NOT_CHECKABLE)  # never FAIL

    # every critical check passed, so the sale goes through regardless of the coaching notes
    critical_statuses = {r.status for r in scoring.results if r.critical}
    assert critical_statuses == {CheckStatus.PASS}
    assert decision.status is GateStatus.AUTO_PASSED
    assert "Coaching note" not in decision.reason

    # ...and they are persisted, with evidence, so the TL can still see them
    stored = {r.check_code: r for r in session.scalars(select(CheckResultRecord).where(CheckResultRecord.call_id == call.id))}
    assert stored["dead_air"].evidence and stored["overlapping_speech"].evidence
    overlap_ids = {e["segment_id"] for e in stored["overlapping_speech"].evidence}
    interrupted = [s for s in call.transcript.segments if s.start_time in (100.0, 104.0)]
    assert overlap_ids == {str(s.id) for s in interrupted}  # both segments involved in the interruption


def test_a_behaviour_note_does_not_hide_a_critical_fail(session, retailer):
    _, scoring, decision = run_pipeline(
        session, retailer, [DISCLAIMER, TOTAL_MINIMUM_COST, DEAD_AIR, OVERLAP], crm=CRM_WITH_COST, segments=COST_SEGMENTS,
    )
    assert results(scoring)["dead_air"].reason.startswith("Coaching note")
    assert decision.status is GateStatus.HELD  # the critical FAIL still holds the sale


def test_a_misconfigured_behaviour_check_is_recorded_but_does_not_route_the_call_anywhere(session, retailer):
    broken = dict(DEAD_AIR, code="dead_air_broken", configuration={"signal": "dead_air"})  # no threshold
    _, scoring, decision = run_pipeline(session, retailer, [DISCLAIMER, DOB, broken])

    assert results(scoring)["dead_air_broken"].status is CheckStatus.NOT_CHECKABLE
    assert decision.status is GateStatus.AUTO_PASSED
    assert "dead_air_broken" not in decision.reason  # behaviour results are not part of the gate's reasoning
