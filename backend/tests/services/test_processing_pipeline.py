"""process_call end to end: transcript (already stored) -> scoring -> gate -> COMPLETED / FAILED."""

from contextlib import nullcontext

import pytest
from sqlalchemy import func, select

from app.models import Call, CallStatus, CheckResultRecord, CheckType, GateDecision, GateStatus
from app.schemas.check_result import CheckStatus
from app.services.calls import processing_service
from app.services.checks.base import CheckRunner
from app.services.scoring import scoring_service
from app.services.gate import gate_service
from app.services.storage.audio_storage import LocalAudioStorage
from tests.services.scenario import (
    CRM_OK, DEAD_AIR, DISCLAIMER, DOB, EMAIL, JAN, RATE, DEFAULT_SEGMENTS, add_library,
    make_call, make_retailer, with_confidence,
)

EMAIL_SEGMENT = 6  # index of the customer email segment in DEFAULT_SEGMENTS


@pytest.fixture
def wired(monkeypatch, session, tmp_path):
    """Point process_call at the test session; the transcript already exists, so no STT runs."""
    monkeypatch.setattr(processing_service, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(processing_service, "get_audio_storage", lambda: LocalAudioStorage(str(tmp_path)))


@pytest.fixture
def retailer(session):
    return make_retailer(session, "pipeline_retailer")


def process(session, call):
    processing_service.process_call(str(call.id))
    session.expire_all()
    return session.get(Call, call.id)


def decision_of(session, call):
    return session.scalar(select(GateDecision).where(GateDecision.call_id == call.id))


def results_of(session, call):
    return {r.check_code: r for r in session.scalars(select(CheckResultRecord).where(CheckResultRecord.call_id == call.id))}


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


# --- the routing rules, through the whole pipeline ------------------------------------------

def test_all_critical_checks_pass_is_auto_passed_and_the_call_completes(wired, session, retailer):
    library = add_library(session, retailer, 2, JAN, checks=[DISCLAIMER, DOB, RATE, EMAIL, DEAD_AIR])
    call = make_call(session, retailer)

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED
    decision = decision_of(session, call)
    assert decision.status is GateStatus.AUTO_PASSED
    assert (decision.check_library_id, decision.check_library_version) == (library.id, 2)
    results = results_of(session, call)
    assert set(results) == {"recording_disclaimer", "dob_match", "rate_match", "email_match", "dead_air"}
    # the behaviour result exists (Phase 9) but is non-blocking: NOT_CHECKABLE here (no threshold), yet AUTO_PASSED
    assert results["dead_air"].status is CheckStatus.NOT_CHECKABLE and results["dead_air"].critical is False
    checked = {code: r for code, r in results.items() if code != "dead_air"}
    assert all(r.status is CheckStatus.PASS and r.check_version == 2 for r in checked.values())
    assert "Auto-passed" in decision.reason


def test_a_critical_fail_is_held(wired, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer, crm={**CRM_OK, "dob": "1991-01-01"})

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED  # HELD is a successful outcome: it is routed to the TL
    assert decision_of(session, call).status is GateStatus.HELD
    assert results_of(session, call)["dob_match"].status is CheckStatus.FAIL


def test_low_confidence_goes_to_qa_review(wired, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, EMAIL])
    call = make_call(session, retailer, segments=with_confidence(DEFAULT_SEGMENTS, EMAIL_SEGMENT, 0.5))

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED
    assert decision_of(session, call).status is GateStatus.QA_REVIEW
    assert results_of(session, call)["email_match"].status is CheckStatus.LOW_CONFIDENCE


def test_a_critical_not_checkable_goes_to_qa_review(wired, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, RATE])
    call = make_call(session, retailer, crm={"dob": "1990-01-01"})  # no peak_rate -> rate_match not checkable

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED
    assert decision_of(session, call).status is GateStatus.QA_REVIEW
    assert results_of(session, call)["rate_match"].status is CheckStatus.NOT_CHECKABLE


def test_a_non_critical_not_checkable_does_not_stop_an_auto_pass(wired, session, retailer):
    optional = dict(RATE, code="nice_to_have", critical=False,
                    configuration={"field": "phone", "source_of_truth": "CRM.phone"})  # no phone in the CRM
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB, optional])
    call = make_call(session, retailer)

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED
    decision = decision_of(session, call)
    assert decision.status is GateStatus.AUTO_PASSED
    assert results_of(session, call)["nice_to_have"].status is CheckStatus.NOT_CHECKABLE
    assert "Non-critical, not blocking: nice_to_have" in decision.reason


def test_a_missing_check_library_goes_to_qa_review_and_the_call_completes(wired, session, retailer):
    call = make_call(session, retailer)  # the retailer has no library

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED
    decision = decision_of(session, call)
    assert decision.status is GateStatus.QA_REVIEW and decision.check_library_id is None
    assert "no check library is active" in decision.reason
    assert results_of(session, call) == {}


def test_a_call_with_no_start_time_goes_to_qa_review(wired, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    call = make_call(session, retailer, started_at=None)

    done = process(session, call)

    assert done.status is CallStatus.COMPLETED
    decision = decision_of(session, call)
    assert decision.status is GateStatus.QA_REVIEW and "start time is not set" in decision.reason


def test_a_library_with_only_behaviour_checks_is_not_a_vacuous_pass(wired, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DEAD_AIR])
    call = make_call(session, retailer)

    process(session, call)

    assert decision_of(session, call).status is GateStatus.QA_REVIEW
    assert "no checks were evaluated" in decision_of(session, call).reason


# --- versioned library selection through the pipeline ---------------------------------------

def test_the_library_active_on_call_started_at_is_the_one_used(wired, session, retailer):
    from datetime import datetime
    from tests.services.scenario import UTC

    add_library(session, retailer, 1, JAN, datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC), checks=[DISCLAIMER])
    add_library(session, retailer, 2, datetime(2026, 7, 1, tzinfo=UTC), checks=[DISCLAIMER, DOB])
    old = make_call(session, retailer, started_at=datetime(2026, 3, 1, tzinfo=UTC), external_lead_id="old")
    new = make_call(session, retailer, started_at=datetime(2026, 9, 1, tzinfo=UTC), external_lead_id="new")

    process(session, old)
    process(session, new)

    assert decision_of(session, old).check_library_version == 1
    assert {r.check_version for r in results_of(session, old).values()} == {1}
    assert decision_of(session, new).check_library_version == 2
    assert set(results_of(session, new)) == {"recording_disclaimer", "dob_match"}


# --- errors: FAILED, and never AUTO_PASSED --------------------------------------------------

class ExplodingRunner(CheckRunner):
    def run(self, call, check, segments, crm_fields=None, retailer_plan=None):
        raise RuntimeError("runner exploded")


def test_a_scoring_error_is_qa_review_marks_the_call_failed_and_is_never_auto_passed(
    wired, monkeypatch, session, retailer
):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    monkeypatch.setitem(scoring_service.RUNNERS, CheckType.FACTUAL, ExplodingRunner())

    done = process(session, call)

    assert done.status is CallStatus.FAILED
    decision = decision_of(session, call)  # a human still gets the call
    assert decision.status is GateStatus.QA_REVIEW
    assert "scoring error: dob_match: RuntimeError: runner exploded" in decision.reason
    results = results_of(session, call)
    assert results["recording_disclaimer"].status is CheckStatus.PASS
    assert results["dob_match"].status is CheckStatus.NOT_CHECKABLE


def test_an_unexpected_error_marks_the_call_failed_records_no_decision_and_reraises(
    wired, monkeypatch, session, retailer
):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)

    def boom(*args, **kwargs):
        raise RuntimeError("scoring blew up")

    monkeypatch.setattr(scoring_service, "score_call", boom)

    with pytest.raises(RuntimeError, match="scoring blew up"):
        processing_service.process_call(str(call.id))
    session.expire_all()

    assert session.get(Call, call.id).status is CallStatus.FAILED
    assert decision_of(session, call) is None
    assert results_of(session, call) == {}


def test_a_failure_while_recording_the_decision_leaves_nothing_half_scored(wired, monkeypatch, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)

    def boom(*args, **kwargs):
        raise RuntimeError("could not record decision")

    monkeypatch.setattr(gate_service, "decide_call", boom)

    with pytest.raises(RuntimeError):
        processing_service.process_call(str(call.id))
    session.expire_all()

    assert session.get(Call, call.id).status is CallStatus.FAILED
    assert results_of(session, call) == {}  # the scored results were rolled back with the failed decision
    assert decision_of(session, call) is None


def test_a_call_is_only_completed_after_the_whole_pipeline_succeeds(wired, monkeypatch, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    call = make_call(session, retailer)
    seen = {}

    real_decide = gate_service.decide_call

    def spy(session_, call_id, scoring, engine=None):
        seen["status_while_gating"] = session_.get(Call, call_id).status
        return real_decide(session_, call_id, scoring, engine)

    monkeypatch.setattr(gate_service, "decide_call", spy)
    process(session, call)

    assert seen["status_while_gating"] is CallStatus.PROCESSING
    assert session.get(Call, call.id).status is CallStatus.COMPLETED


# --- audit history ------------------------------------------------------------------------

def test_running_the_pipeline_again_changes_nothing(wired, session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    process(session, call)
    first = decision_of(session, call)
    snapshot = (first.id, first.status, first.reason, first.decided_at, sorted(r.id for r in results_of(session, call).values()))

    process(session, call)  # e.g. the background task runs twice

    again = decision_of(session, call)
    assert (again.id, again.status, again.reason, again.decided_at, sorted(r.id for r in results_of(session, call).values())) == snapshot
    assert count(session, GateDecision) >= 1 and session.get(Call, call.id).status is CallStatus.COMPLETED
