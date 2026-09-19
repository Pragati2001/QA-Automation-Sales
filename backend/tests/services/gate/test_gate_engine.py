import pytest

from app.models import GateStatus
from app.schemas.check_result import CheckResult, CheckStatus, Evidence
from app.services.gate.gate_engine import GateEngine, GateOutcome

ENGINE = GateEngine()
PASS, FAIL, LOW, NC = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.LOW_CONFIDENCE, CheckStatus.NOT_CHECKABLE


def res(code, status, *, critical=True, check_type="FACTUAL"):
    return CheckResult(check_id=code, check_version=1, check_type=check_type, critical=critical,
                       status=status, reason="r", evidence=[Evidence("1", 0.0, 1.0, "t")])


def decide(*results, **kwargs) -> GateOutcome:
    return ENGINE.decide(list(results), **kwargs)


# --- the required routing rules ------------------------------------------------

def test_all_critical_checks_pass_is_auto_passed():
    outcome = decide(res("a", PASS), res("b", PASS, check_type="VERBATIM"), res("c", PASS))
    assert outcome.status is GateStatus.AUTO_PASSED
    assert "Auto-passed" in outcome.reason and "3 PASS" in outcome.reason


def test_critical_fail_is_held():
    outcome = decide(res("a", PASS), res("dob_match", FAIL))
    assert outcome.status is GateStatus.HELD
    assert "dob_match" in outcome.reason and outcome.reason.startswith("Held")


def test_low_confidence_is_qa_review():
    outcome = decide(res("a", PASS), res("email_match", LOW))
    assert outcome.status is GateStatus.QA_REVIEW
    assert "low confidence on email_match" in outcome.reason


def test_a_non_critical_low_confidence_still_goes_to_qa_review():
    """The brief: ANY low-confidence result routes to QA, critical or not."""
    assert decide(res("a", PASS), res("b", LOW, critical=False)).status is GateStatus.QA_REVIEW


def test_critical_not_checkable_is_qa_review():
    outcome = decide(res("a", PASS), res("rate_match", NC))
    assert outcome.status is GateStatus.QA_REVIEW
    assert "could not be checked: rate_match" in outcome.reason


def test_non_critical_not_checkable_does_not_force_qa_review():
    outcome = decide(res("a", PASS), res("nice_to_have", NC, critical=False))
    assert outcome.status is GateStatus.AUTO_PASSED
    assert "Non-critical, not blocking: nice_to_have (NOT_CHECKABLE)" in outcome.reason


def test_missing_check_library_is_qa_review():
    outcome = decide(library_problem="no check library is active for retailer 7 at 2026-01-01")
    assert outcome.status is GateStatus.QA_REVIEW
    assert "no check library is active" in outcome.reason


def test_scoring_or_system_error_is_qa_review():
    outcome = decide(res("a", PASS), errors=["email_match: RuntimeError: boom"])
    assert outcome.status is GateStatus.QA_REVIEW
    assert "scoring error: email_match: RuntimeError: boom" in outcome.reason


# --- precedence and edge cases ---------------------------------------------------

def test_a_critical_fail_beats_the_qa_review_triggers_but_the_summary_mentions_both():
    outcome = decide(res("dob_match", FAIL), res("email_match", LOW), library_problem=None, errors=["x: boom"])
    assert outcome.status is GateStatus.HELD
    assert "dob_match" in outcome.reason and "email_match" in outcome.reason and "scoring error" in outcome.reason


def test_a_non_critical_fail_does_not_hold_the_sale():
    outcome = decide(res("a", PASS), res("coaching", FAIL, critical=False))
    assert outcome.status is GateStatus.AUTO_PASSED
    assert "coaching (FAIL)" in outcome.reason


def test_behaviour_results_never_influence_the_gate():
    noisy = [res("dead_air", FAIL, check_type="BEHAVIOUR"), res("rapport", LOW, check_type="BEHAVIOUR"),
             res("interrupt", NC, check_type="BEHAVIOUR")]
    assert decide(res("a", PASS), *noisy).status is GateStatus.AUTO_PASSED


def test_nothing_evaluated_is_not_a_vacuous_auto_pass():
    """No checks scored (e.g. a library holding only behaviour checks): a human must look."""
    for results in ([], [res("dead_air", PASS, check_type="BEHAVIOUR")]):
        outcome = decide(*results)
        assert outcome.status is GateStatus.QA_REVIEW
        assert "no checks were evaluated" in outcome.reason


def test_never_auto_passes_when_anything_is_wrong():
    clean = [res("a", PASS), res("b", PASS)]
    assert decide(*clean).status is GateStatus.AUTO_PASSED
    assert decide(*clean, library_problem="x").status is not GateStatus.AUTO_PASSED
    assert decide(*clean, errors=["x"]).status is not GateStatus.AUTO_PASSED


@pytest.mark.parametrize("statuses, expected", [
    ([PASS, PASS], GateStatus.AUTO_PASSED),
    ([PASS, FAIL], GateStatus.HELD),
    ([FAIL, LOW], GateStatus.HELD),
    ([PASS, LOW], GateStatus.QA_REVIEW),
    ([PASS, NC], GateStatus.QA_REVIEW),
    ([LOW, NC], GateStatus.QA_REVIEW),
])
def test_routing_matrix_for_critical_results(statuses, expected):
    assert decide(*[res(f"c{i}", s) for i, s in enumerate(statuses)]).status is expected


def test_the_outcome_carries_a_reasoning_summary_with_counts():
    outcome = decide(res("a", PASS), res("b", PASS), res("c", FAIL))
    assert isinstance(outcome, GateOutcome)
    assert "Checks evaluated: 3 (2 PASS, 1 FAIL)" in outcome.reason


def test_the_engine_does_not_mutate_its_inputs():
    results = [res("a", PASS), res("b", FAIL)]
    before = [(r.check_id, r.status, r.critical) for r in results]
    ENGINE.decide(results, library_problem=None, errors=["e"])
    assert [(r.check_id, r.status, r.critical) for r in results] == before
