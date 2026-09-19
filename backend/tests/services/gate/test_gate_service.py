from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import GateDecision, GateStatus
from app.services.gate.gate_service import GateDecisionExistsError, decide_call
from app.services.scoring.scoring_service import score_call
from tests.services.scenario import (
    DISCLAIMER, DOB, JAN, add_library, make_call, make_retailer,
)


@pytest.fixture
def retailer(session):
    return make_retailer(session, "gate_service_retailer")


def test_persists_one_decision_with_status_reason_timestamp_and_library(session, retailer):
    library = add_library(session, retailer, 3, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    before = datetime.now(timezone.utc) - timedelta(seconds=5)

    decision = decide_call(session, call.id, score_call(session, call.id))
    session.commit()

    stored = session.scalar(select(GateDecision).where(GateDecision.call_id == call.id))
    assert stored.id == decision.id
    assert stored.status is GateStatus.AUTO_PASSED
    assert "Auto-passed" in stored.reason and "2 PASS" in stored.reason
    assert before <= stored.decided_at <= datetime.now(timezone.utc) + timedelta(seconds=5)
    assert (stored.check_library_id, stored.check_library_version) == (library.id, 3)


def test_a_critical_fail_is_persisted_as_held(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer, crm={"dob": "1991-01-01"})

    decision = decide_call(session, call.id, score_call(session, call.id))

    assert decision.status is GateStatus.HELD
    assert "dob_match" in decision.reason


def test_no_library_is_persisted_as_qa_review_with_no_library_reference(session, retailer):
    call = make_call(session, retailer)  # the retailer has no library at all

    decision = decide_call(session, call.id, score_call(session, call.id))

    assert decision.status is GateStatus.QA_REVIEW
    assert "no check library is active" in decision.reason
    assert (decision.check_library_id, decision.check_library_version) == (None, None)


def test_an_existing_decision_is_never_overwritten(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    first = decide_call(session, call.id, score_call(session, call.id))
    session.commit()
    original = (first.id, first.status, first.reason, first.decided_at)

    class Impostor:  # a scoring outcome that would produce a different decision
        library, problem, errors, results = None, "different outcome", [], []

    with pytest.raises(GateDecisionExistsError):
        decide_call(session, call.id, Impostor())

    session.expire_all()
    rows = list(session.scalars(select(GateDecision).where(GateDecision.call_id == call.id)))
    assert len(rows) == 1
    assert (rows[0].id, rows[0].status, rows[0].reason, rows[0].decided_at) == original


def test_the_database_allows_only_one_decision_per_call(session, retailer):
    from sqlalchemy.exc import IntegrityError

    call = make_call(session, retailer)
    session.add(GateDecision(call_id=call.id, status=GateStatus.QA_REVIEW, reason="a"))
    session.commit()
    session.add(GateDecision(call_id=call.id, status=GateStatus.AUTO_PASSED, reason="b"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_database_only_accepts_the_three_gate_statuses(session, retailer):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    call = make_call(session, retailer)
    with pytest.raises(IntegrityError):
        session.execute(
            text("insert into gate_decisions (call_id, status, reason) values (:c, 'MAYBE', 'x')"), {"c": call.id}
        )
    session.rollback()  # the failed INSERT aborted the transaction
    assert session.scalar(select(func.count()).select_from(GateDecision).where(GateDecision.call_id == call.id)) == 0
