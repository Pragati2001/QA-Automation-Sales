from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import CheckResultRecord, CheckType
from app.schemas.check_result import CheckStatus
from app.services.checks.base import CheckRunner
from app.services.checks.factual_check import FactualCheckRunner
from app.services.checks.verbatim_runner import VerbatimCheckRunner
from app.services.scoring.scoring_service import CallAlreadyScoredError, score_call
from tests.services.scenario import (
    CRM_OK, DEAD_AIR, DISCLAIMER, DOB, JAN, RATE, UTC, add_library, make_call, make_retailer,
)


@pytest.fixture
def retailer(session):
    return make_retailer(session)


def records_for(session, call):
    return list(session.scalars(
        select(CheckResultRecord).where(CheckResultRecord.call_id == call.id).order_by(CheckResultRecord.check_code)
    ))


# --- one result per applicable check --------------------------------------------

def test_persists_one_check_result_for_every_verbatim_and_factual_check(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB, RATE])
    call = make_call(session, retailer)

    outcome = score_call(session, call.id)

    rows = records_for(session, call)
    assert [r.check_code for r in rows] == ["dob_match", "rate_match", "recording_disclaimer"]
    assert [r.check_code for r in outcome.records] == ["dob_match", "rate_match", "recording_disclaimer"]  # library order (by code)
    assert all(r.status is CheckStatus.PASS for r in rows)
    assert outcome.problem is None and outcome.errors == [] and outcome.skipped == []
    assert len(outcome.results) == 3


def test_check_result_rows_carry_the_full_result_and_a_snapshot_of_the_check(session, retailer):
    library = add_library(session, retailer, 4, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    score_call(session, call.id)
    session.commit()

    dob = next(r for r in records_for(session, call) if r.check_code == "dob_match")
    assert (dob.call_id, dob.check_version, dob.check_type, dob.critical) == (call.id, 4, "FACTUAL", True)
    assert dob.check_id == next(c.id for c in library.checks if c.code == "dob_match")
    assert (dob.expected_value, dob.actual_value) == ("1990-01-01", "the first of january  nineteen ninety")
    assert dob.extraction_confidence == 0.93 and dob.rule_confidence == 1.0
    assert dob.reason
    dob_segment = next(sg for sg in call.transcript.segments if sg.start_time == 21.4)
    assert dob.evidence == [{"segment_id": str(dob_segment.id), "start_time": 21.4,
                             "end_time": 24.8, "text": "It's the first of January, nineteen ninety."}]

    disclaimer = next(r for r in records_for(session, call) if r.check_code == "recording_disclaimer")
    assert disclaimer.check_type == "VERBATIM"
    assert disclaimer.evidence[0]["start_time"] == 0.0 and disclaimer.evidence[0]["end_time"] == 7.4


def test_failures_and_not_checkable_results_are_persisted_too(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DOB, RATE])
    call = make_call(session, retailer, crm={"dob": "1991-05-05"})  # wrong dob; no peak_rate at all

    score_call(session, call.id)

    by_code = {r.check_code: r for r in records_for(session, call)}
    assert by_code["dob_match"].status is CheckStatus.FAIL
    assert (by_code["dob_match"].expected_value, by_code["dob_match"].actual_value) == (
        "1991-05-05", "the first of january  nineteen ninety")
    assert by_code["rate_match"].status is CheckStatus.NOT_CHECKABLE
    assert "Expected value not available" in by_code["rate_match"].reason


def test_behaviour_checks_are_scored_and_persisted_but_never_fail(session, retailer):
    """Phase 9: behaviour checks have a runner now. The demo dead_air check has no threshold configured,
    so it is NOT_CHECKABLE (non-critical), and either way it is stored like any other result."""
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DEAD_AIR])
    call = make_call(session, retailer)

    outcome = score_call(session, call.id)

    assert outcome.skipped == []
    by_code = {r.check_code: r for r in records_for(session, call)}
    assert set(by_code) == {"dead_air", "recording_disclaimer"}
    assert by_code["dead_air"].check_type == "BEHAVIOUR"
    assert by_code["dead_air"].critical is False
    assert by_code["dead_air"].status is CheckStatus.NOT_CHECKABLE  # threshold_seconds is null in the fixture
    assert {r.status for r in outcome.results if r.check_type == "BEHAVIOUR"} <= {CheckStatus.PASS, CheckStatus.NOT_CHECKABLE}


def test_a_check_with_a_retailer_plan_source_is_not_checkable_because_no_plan_exists_yet(session, retailer):
    plan_check = dict(DOB, code="plan_rate", configuration={"field": "rate", "source_of_truth": "RETAILER_PLAN.peak_rate"})
    add_library(session, retailer, 1, JAN, checks=[plan_check])
    call = make_call(session, retailer)

    outcome = score_call(session, call.id)
    assert outcome.results[0].status is CheckStatus.NOT_CHECKABLE


def test_lead_crm_fields_are_what_factual_checks_compare_against(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DOB])
    matching = make_call(session, retailer, crm=CRM_OK, external_lead_id="a")
    mismatching = make_call(session, retailer, crm={"dob": "1985-02-03"}, external_lead_id="b")

    assert score_call(session, matching.id).results[0].status is CheckStatus.PASS
    assert score_call(session, mismatching.id).results[0].status is CheckStatus.FAIL


# --- the versioned library is chosen by call_started_at ----------------------------

V1_END = datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)
V2_START = datetime(2026, 7, 1, tzinfo=UTC)


def two_versions(session, retailer):
    v1 = add_library(session, retailer, 1, JAN, V1_END, checks=[DISCLAIMER])
    v2 = add_library(session, retailer, 2, V2_START, checks=[DISCLAIMER, DOB])
    return v1, v2


def test_the_library_version_in_effect_on_the_call_date_is_used(session, retailer):
    v1, v2 = two_versions(session, retailer)
    march = make_call(session, retailer, started_at=datetime(2026, 3, 10, tzinfo=UTC), external_lead_id="m")
    august = make_call(session, retailer, started_at=datetime(2026, 8, 20, tzinfo=UTC), external_lead_id="a")

    early = score_call(session, march.id)
    late = score_call(session, august.id)

    assert early.library.id == v1.id
    assert {r.check_code for r in records_for(session, march)} == {"recording_disclaimer"}
    assert {r.check_version for r in records_for(session, march)} == {1}

    assert late.library.id == v2.id
    assert {r.check_code for r in records_for(session, august)} == {"recording_disclaimer", "dob_match"}
    assert {r.check_version for r in records_for(session, august)} == {2}


def test_the_call_date_not_today_decides_the_version(session, retailer):
    """Scored 'against the rules that were live': an old call keeps its old version even though
    a newer version now exists."""
    two_versions(session, retailer)
    old_call = make_call(session, retailer, started_at=V1_END)  # last instant of v1
    assert score_call(session, old_call.id).library.version == 1


def test_a_call_in_a_gap_between_versions_gets_no_library_and_no_fallback(session, retailer):
    add_library(session, retailer, 1, JAN, datetime(2026, 3, 1, tzinfo=UTC), checks=[DISCLAIMER])
    add_library(session, retailer, 2, datetime(2026, 5, 1, tzinfo=UTC), checks=[DISCLAIMER])
    call = make_call(session, retailer, started_at=datetime(2026, 4, 1, tzinfo=UTC))

    outcome = score_call(session, call.id)

    assert outcome.library is None and outcome.results == []
    assert "no check library is active" in outcome.problem
    assert records_for(session, call) == []  # neither v1 nor v2 was used


def test_a_call_before_any_library_existed_gets_no_library(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    outcome = score_call(session, make_call(session, retailer, started_at=JAN - timedelta(days=1)).id)
    assert outcome.library is None and "no check library is active" in outcome.problem


def test_a_retailer_without_any_library_gets_none(session, retailer):
    outcome = score_call(session, make_call(session, retailer).id)
    assert outcome.library is None and outcome.results == []


def test_other_retailers_libraries_are_never_used(session, retailer):
    other = make_retailer(session, "somebody_else")
    add_library(session, other, 1, JAN, checks=[DISCLAIMER])
    assert score_call(session, make_call(session, retailer).id).library is None


def test_a_call_without_a_start_time_gets_no_library(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    call = make_call(session, retailer, started_at=None)

    outcome = score_call(session, call.id)

    assert outcome.library is None and "start time is not set" in outcome.problem
    assert records_for(session, call) == []


def test_overlapping_libraries_are_reported_never_silently_resolved(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    add_library(session, retailer, 2, datetime(2026, 1, 15, tzinfo=UTC), checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer, started_at=datetime(2026, 2, 1, tzinfo=UTC))

    outcome = score_call(session, call.id)

    assert outcome.library is None and "more than one check library" in outcome.problem
    assert records_for(session, call) == []


# --- errors and audit history ------------------------------------------------------------

class ExplodingRunner(CheckRunner):
    def run(self, call, check, segments, crm_fields=None, retailer_plan=None):
        raise RuntimeError("runner exploded")


def test_a_runner_error_is_isolated_persisted_as_not_checkable_and_reported(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    runners = {CheckType.VERBATIM: VerbatimCheckRunner(), CheckType.FACTUAL: ExplodingRunner()}

    outcome = score_call(session, call.id, runners=runners)

    by_code = {r.check_code: r for r in records_for(session, call)}
    assert by_code["recording_disclaimer"].status is CheckStatus.PASS  # the other check still ran
    assert by_code["dob_match"].status is CheckStatus.NOT_CHECKABLE
    assert by_code["dob_match"].reason.startswith("Scoring error: RuntimeError: runner exploded")
    assert by_code["dob_match"].critical is True
    assert outcome.errors == ["dob_match: RuntimeError: runner exploded"]


def test_scoring_a_call_twice_raises_and_leaves_the_first_results_untouched(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER, DOB])
    call = make_call(session, retailer)
    score_call(session, call.id)
    session.commit()
    ids_before = [r.id for r in records_for(session, call)]

    with pytest.raises(CallAlreadyScoredError):
        score_call(session, call.id)

    assert [r.id for r in records_for(session, call)] == ids_before


def test_the_database_itself_refuses_a_second_result_for_the_same_check(session, retailer):
    from sqlalchemy.exc import IntegrityError

    add_library(session, retailer, 1, JAN, checks=[DOB])
    call = make_call(session, retailer)
    outcome = score_call(session, call.id)
    session.commit()

    duplicate = CheckResultRecord(
        call_id=call.id, check_id=outcome.records[0].check_id, check_code="dob_match", check_version=1,
        check_type="FACTUAL", critical=True, status=CheckStatus.PASS, reason="dup", evidence=[],
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_call_and_missing_transcript_are_errors_not_results(session, retailer):
    add_library(session, retailer, 1, JAN, checks=[DISCLAIMER])
    with pytest.raises(ValueError, match="not found"):
        score_call(session, 999_999_999)

    no_transcript = make_call(session, retailer, with_transcript=False)
    with pytest.raises(ValueError, match="no transcript"):
        score_call(session, no_transcript.id)
    assert session.scalar(select(func.count()).select_from(CheckResultRecord).where(CheckResultRecord.call_id == no_transcript.id)) == 0
