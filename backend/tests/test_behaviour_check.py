import copy
import inspect
import random

import pytest

from app.models import Call, Check, CheckLibrary, CheckType, GateStatus, TranscriptSegment
from app.schemas.check_result import CheckResult, CheckStatus
from app.services.checks.base import CheckRunner
from app.services.checks.behaviour_check import (
    DEFAULT_OVERLAP_TOLERANCE_SECONDS,
    MAX_EVIDENCE_INSTANCES,
    SIGNALS,
    BehaviourCheckRunner,
)
from app.services.gate.gate_engine import GateEngine

RUNNER = BehaviourCheckRunner()
AGENT, CUSTOMER = "AGENT", "CUSTOMER"


def seg(id, speaker, text, start, end):
    return TranscriptSegment(
        id=id, sequence=id, speaker=speaker, text=text, start_time=start, end_time=end, confidence=0.9
    )


def evaluate(config, segments, *, code="behaviour_check", critical=False):
    return RUNNER.evaluate(code, 3, critical, config, segments)


def behaviour_check(configuration, code="dead_air", critical=False, check_type=CheckType.BEHAVIOUR):
    return Check(id=1, code=code, name=code, check_type=check_type, critical=critical,
                 configuration=configuration, library=CheckLibrary(version=3))


def evidence_of(result):
    return [(e.segment_id, e.start_time, e.end_time, e.text) for e in result.evidence]


# ---------------------------------------------------------------------------
# dead_air
# ---------------------------------------------------------------------------

DEAD_AIR = {"signal": "dead_air", "threshold_seconds": 10}


def test_dead_air_over_the_threshold_is_a_coaching_note_on_a_passing_result():
    segments = [
        seg(1, AGENT, "Let me check that for you.", 0.0, 4.0),
        seg(2, CUSTOMER, "Okay.", 4.5, 5.0),
        seg(3, AGENT, "Sorry about the wait, I have it now.", 52.0, 56.0),  # 47 s after segment 2 ended
    ]
    result = evaluate(DEAD_AIR, segments)

    assert result.status is CheckStatus.PASS
    assert result.reason.startswith("Coaching note") and "47 s" in result.reason
    assert "0:05" in result.reason and "0:52" in result.reason
    assert (result.expected_value, result.actual_value) == ("less than 10 s of silence", "longest silence 47 s")
    # evidence: both segments around the silence, with their real ids, times and text
    assert evidence_of(result) == [
        ("2", 4.5, 5.0, "Okay."),
        ("3", 52.0, 56.0, "Sorry about the wait, I have it now."),
    ]


def test_dead_air_under_the_threshold_passes_without_a_note():
    segments = [seg(1, AGENT, "Hello", 0, 2), seg(2, CUSTOMER, "Hi", 6, 7)]  # 4 s gap
    result = evaluate(DEAD_AIR, segments)
    assert result.status is CheckStatus.PASS
    assert not result.reason.startswith("Coaching note") and "longest silence 4 s" in result.reason
    assert result.evidence == []


def test_dead_air_threshold_is_inclusive():
    segments = [seg(1, AGENT, "a", 0, 2), seg(2, CUSTOMER, "b", 12, 13)]  # exactly 10 s
    assert evaluate(DEAD_AIR, segments).reason.startswith("Coaching note")
    assert not evaluate({**DEAD_AIR, "threshold_seconds": 10.5}, segments).reason.startswith("Coaching note")


def test_dead_air_measures_from_the_latest_end_when_segments_overlap():
    """A long segment still running must not be mistaken for silence after a short one that ended early."""
    segments = [
        seg(1, AGENT, "A long explanation of the plan.", 0.0, 40.0),
        seg(2, CUSTOMER, "Mm.", 5.0, 5.5),
        seg(3, AGENT, "So that is the plan.", 42.0, 45.0),  # only 2 s after the long segment ended
    ]
    assert not evaluate(DEAD_AIR, segments).reason.startswith("Coaching note")


def test_every_long_silence_is_reported():
    segments = [
        seg(1, AGENT, "a", 0, 1), seg(2, CUSTOMER, "b", 20, 21), seg(3, AGENT, "c", 22, 23), seg(4, CUSTOMER, "d", 60, 61),
    ]
    result = evaluate(DEAD_AIR, segments)
    assert "2 silence(s)" in result.reason and "longest was 37 s" in result.reason
    assert [e.segment_id for e in result.evidence] == ["1", "2", "3", "4"]


def test_dead_air_input_order_does_not_matter():
    ordered = [seg(1, AGENT, "a", 0, 2), seg(2, CUSTOMER, "b", 30, 31)]
    assert evaluate(DEAD_AIR, list(reversed(ordered))).reason == evaluate(DEAD_AIR, ordered).reason


@pytest.mark.parametrize("segments", [[], [seg(1, AGENT, "only one", 0, 3)]])
def test_dead_air_needs_at_least_two_timed_segments(segments):
    result = evaluate(DEAD_AIR, segments)
    assert result.status is CheckStatus.NOT_CHECKABLE and "Not enough timed transcript data" in result.reason


@pytest.mark.parametrize("config", [
    {"signal": "dead_air"},
    {"signal": "dead_air", "threshold_seconds": None},  # what the demo Retailer 1 fixture ships with
    {"signal": "dead_air", "threshold_seconds": 0},
    {"signal": "dead_air", "threshold_seconds": -5},
    {"signal": "dead_air", "threshold_seconds": "10"},
    {"signal": "dead_air", "threshold_seconds": True},
])
def test_dead_air_without_a_usable_threshold_is_not_checkable_never_guessed(config):
    result = evaluate(config, [seg(1, AGENT, "a", 0, 1), seg(2, AGENT, "b", 99, 100)])
    assert result.status is CheckStatus.NOT_CHECKABLE and result.reason.startswith("Check configuration is unusable")


# ---------------------------------------------------------------------------
# filler_ratio
# ---------------------------------------------------------------------------

FILLER = {"signal": "filler_ratio", "max_ratio": 0.1}


def test_filler_words_above_the_limit_are_a_coaching_note_with_the_worst_segments_as_evidence():
    segments = [
        seg(1, AGENT, "Um, so uh the peak rate is um thirty one point nine", 0, 7),       # 3 fillers of 12 words
        seg(2, CUSTOMER, "Um okay uh right", 7, 9),                                          # customer: ignored
        seg(3, AGENT, "That is a good plan for you and your family today", 10, 14),        # 0 fillers, 11 words
    ]
    result = evaluate(FILLER, segments)

    assert result.status is CheckStatus.PASS
    assert result.reason.startswith("Coaching note") and "above the 10.0% limit" in result.reason
    assert result.actual_value == "13.0% (3 of 23 words)"  # 12 + 11 agent words; the customer's fillers are not counted
    assert result.expected_value == "at most 10.0% filler words"
    assert evidence_of(result) == [("1", 0, 7, "Um, so uh the peak rate is um thirty one point nine")]


def test_filler_words_within_the_limit_pass_without_a_note():
    segments = [seg(1, AGENT, "Well um the rate is thirty one point nine cents per kilowatt hour for you", 0, 6)]
    result = evaluate({**FILLER, "max_ratio": 0.2}, segments)
    assert result.status is CheckStatus.PASS and not result.reason.startswith("Coaching note")
    assert result.evidence == []


def test_words_that_merely_contain_a_filler_are_not_fillers():
    segments = [seg(1, AGENT, "The umbrella hummus error hurts", 0, 3)]
    assert evaluate({**FILLER, "max_ratio": 0.01}, segments).actual_value == "0.0% (0 of 5 words)"


def test_filler_words_and_speaker_are_configurable():
    segments = [seg(1, AGENT, "basically like you know it is fine", 0, 3), seg(2, CUSTOMER, "um basically yes", 3, 5)]
    only_basically = evaluate({**FILLER, "filler_words": ["basically"], "speaker": "any"}, segments)
    assert only_basically.actual_value == "20.0% (2 of 10 words)"  # both speakers' words, "basically" twice
    customer = evaluate({**FILLER, "speaker": "customer"}, segments)
    assert customer.actual_value == "33.3% (1 of 3 words)"


def test_a_max_ratio_of_zero_is_a_valid_strict_setting():
    """0 means "no filler words at all": any filler is flagged, and it is still only a coaching note."""
    result = evaluate({**FILLER, "max_ratio": 0}, [seg(1, AGENT, "well um hello there", 0, 3)])
    assert result.status is CheckStatus.PASS and result.reason.startswith("Coaching note")
    assert not evaluate({**FILLER, "max_ratio": 0}, [seg(1, AGENT, "hello there", 0, 3)]).reason.startswith("Coaching")


def test_filler_ratio_needs_some_speech_from_the_chosen_speaker():
    only_customer = [seg(1, CUSTOMER, "um hello", 0, 2)]
    result = evaluate(FILLER, only_customer)  # default speaker: agent
    assert result.status is CheckStatus.NOT_CHECKABLE and "Not enough timed transcript data" in result.reason
    assert evaluate(FILLER, []).status is CheckStatus.NOT_CHECKABLE


@pytest.mark.parametrize("config", [
    {"signal": "filler_ratio"},
    {"signal": "filler_ratio", "max_ratio": -0.1},
    {"signal": "filler_ratio", "max_ratio": 1.5},
    {"signal": "filler_ratio", "max_ratio": 0.1, "filler_words": []},
    {"signal": "filler_ratio", "max_ratio": 0.1, "filler_words": ["you know"]},
    {"signal": "filler_ratio", "max_ratio": 0.1, "filler_words": "um"},
    {"signal": "filler_ratio", "max_ratio": 0.1, "speaker": "manager"},
])
def test_filler_ratio_with_an_unusable_configuration_is_not_checkable(config):
    result = evaluate(config, [seg(1, AGENT, "um hello there", 0, 2)])
    assert result.status is CheckStatus.NOT_CHECKABLE and result.reason.startswith("Check configuration is unusable")


# ---------------------------------------------------------------------------
# overlapping_speech
# ---------------------------------------------------------------------------

def test_overlapping_speech_is_flagged_with_both_segments_as_evidence():
    segments = [
        seg(1, AGENT, "So the total minimum cost over the term is", 10.0, 16.0),
        seg(2, CUSTOMER, "Sorry, can I stop you there?", 14.0, 17.0),  # starts 2 s before segment 1 ends
        seg(3, AGENT, "Of course.", 18.0, 19.0),
    ]
    result = evaluate({"signal": "overlapping_speech"}, segments)

    assert result.status is CheckStatus.PASS
    assert result.reason.startswith("Coaching note") and "1 overlap(s)" in result.reason
    assert "AGENT and CUSTOMER" in result.reason and "0:14" in result.reason
    assert result.actual_value == "1 overlap(s), longest 2 s"
    assert evidence_of(result) == [
        ("1", 10.0, 16.0, "So the total minimum cost over the term is"),
        ("2", 14.0, 17.0, "Sorry, can I stop you there?"),
    ]


def test_the_default_tolerance_is_half_a_second_and_exceeding_it_is_strict():
    assert DEFAULT_OVERLAP_TOLERANCE_SECONDS == 0.5
    config = {"signal": "overlapping_speech"}
    for overlap, flagged in [(0.4, False), (0.5, False), (0.6, True)]:
        segments = [seg(1, AGENT, "a", 0.0, 10.0), seg(2, CUSTOMER, "b", 10.0 - overlap, 12.0)]
        assert evaluate(config, segments).reason.startswith("Coaching note") is flagged, overlap


def test_the_tolerance_is_read_from_the_config():
    segments = [seg(1, AGENT, "a", 0.0, 10.0), seg(2, CUSTOMER, "b", 8.0, 12.0)]  # 2 s overlap
    assert evaluate({"signal": "overlapping_speech", "overlap_tolerance_seconds": 3}, segments).reason.startswith("No overlapping")
    assert evaluate({"signal": "overlapping_speech", "overlap_tolerance_seconds": 1}, segments).reason.startswith("Coaching")


def test_a_chain_of_overlaps_lists_each_segment_once():
    segments = [seg(1, AGENT, "a", 0, 10), seg(2, CUSTOMER, "b", 8, 18), seg(3, AGENT, "c", 16, 26)]
    result = evaluate({"signal": "overlapping_speech"}, segments)
    assert "2 overlap(s)" in result.reason
    assert [e.segment_id for e in result.evidence] == ["1", "2", "3"]


def test_overlap_detection_sorts_segments_by_start_time_first():
    segments = [seg(2, CUSTOMER, "b", 8, 12), seg(1, AGENT, "a", 0, 10)]  # given out of order
    assert evaluate({"signal": "overlapping_speech"}, segments).reason.startswith("Coaching note")


def test_no_overlaps_passes_cleanly():
    segments = [seg(1, AGENT, "a", 0, 5), seg(2, CUSTOMER, "b", 5.2, 8), seg(3, AGENT, "c", 9, 12)]
    result = evaluate({"signal": "overlapping_speech"}, segments)
    assert result.status is CheckStatus.PASS and result.evidence == []
    assert result.actual_value == "no overlaps"


def test_a_very_noisy_call_caps_the_evidence():
    segments = [seg(i, AGENT if i % 2 else CUSTOMER, f"line {i}", i * 5, i * 5 + 8) for i in range(1, 40)]
    result = evaluate({"signal": "overlapping_speech"}, segments)
    assert "38 overlap(s)" in result.reason  # the count is complete...
    assert len(result.evidence) <= MAX_EVIDENCE_INSTANCES * 2  # ...the evidence is not unbounded


@pytest.mark.parametrize("segments", [[], [seg(1, AGENT, "alone", 0, 3)]])
def test_overlap_needs_at_least_two_segments(segments):
    assert evaluate({"signal": "overlapping_speech"}, segments).status is CheckStatus.NOT_CHECKABLE


def test_a_negative_tolerance_is_unusable():
    result = evaluate({"signal": "overlapping_speech", "overlap_tolerance_seconds": -1},
                      [seg(1, AGENT, "a", 0, 1), seg(2, AGENT, "b", 0, 1)])
    assert result.status is CheckStatus.NOT_CHECKABLE


# ---------------------------------------------------------------------------
# choosing the signal
# ---------------------------------------------------------------------------

def test_the_signal_defaults_to_the_check_code():
    segments = [seg(1, AGENT, "a", 0, 1), seg(2, CUSTOMER, "b", 30, 31)]
    by_code = evaluate({"threshold_seconds": 10}, segments, code="dead_air")
    assert by_code.status is CheckStatus.PASS and by_code.reason.startswith("Coaching note")


def test_an_explicit_signal_overrides_the_code():
    segments = [seg(1, AGENT, "a", 0, 10), seg(2, CUSTOMER, "b", 5, 12)]
    result = evaluate({"signal": "overlapping_speech"}, segments, code="dead_air")
    assert "overlap" in result.reason


@pytest.mark.parametrize("config, code", [({}, "rapport"), ({"signal": "nope"}, "dead_air"), ({"signal": None}, "custom")])
def test_an_unknown_signal_is_not_checkable(config, code):
    result = evaluate(config, [seg(1, AGENT, "a", 0, 1), seg(2, AGENT, "b", 2, 3)], code=code)
    assert result.status is CheckStatus.NOT_CHECKABLE and "'signal' must be one of" in result.reason


def test_the_three_signals_are_the_supported_ones():
    assert set(SIGNALS) == {"dead_air", "filler_ratio", "overlapping_speech"}


# ---------------------------------------------------------------------------
# behaviour never blocks: never FAIL, criticality comes from the Check row
# ---------------------------------------------------------------------------

def test_no_input_can_produce_a_fail():
    """Random transcripts x random (valid and junk) configs: the status is always PASS or NOT_CHECKABLE."""
    rng = random.Random(9)
    configs = [
        {"signal": "dead_air", "threshold_seconds": 1}, {"signal": "dead_air", "threshold_seconds": 0.1},
        {"signal": "filler_ratio", "max_ratio": 0.01}, {"signal": "filler_ratio", "max_ratio": 1, "speaker": "any"},
        {"signal": "overlapping_speech"}, {"signal": "overlapping_speech", "overlap_tolerance_seconds": 0},
        {}, {"signal": "junk"}, {"signal": "dead_air"}, {"signal": "filler_ratio", "max_ratio": "x"},
    ]
    seen = set()
    for _ in range(300):
        segments = []
        for i in range(rng.randrange(0, 12)):
            start = rng.uniform(0, 300)
            words = " ".join(rng.choice(["um", "uh", "hello", "the", "rate", "plan"]) for _ in range(rng.randrange(0, 8)))
            segments.append(seg(i + 1, rng.choice([AGENT, CUSTOMER, "MANAGER"]), words, start, start + rng.uniform(0, 30)))
        result = evaluate(rng.choice(configs), segments)
        seen.add(result.status)
        assert result.status in (CheckStatus.PASS, CheckStatus.NOT_CHECKABLE)
        assert result.check_type == "BEHAVIOUR" and result.critical is False
    assert seen == {CheckStatus.PASS, CheckStatus.NOT_CHECKABLE}  # both outcomes were actually exercised


def test_every_result_built_from_a_non_critical_check_row_is_non_critical():
    segments = [seg(1, AGENT, "um um um", 0, 4), seg(2, CUSTOMER, "b", 3, 40)]
    for signal_config in (DEAD_AIR, FILLER, {"signal": "overlapping_speech"}):
        result = RUNNER.run(Call(id=1, lead_id=1), behaviour_check(signal_config, critical=False), segments)
        assert result.critical is False and result.status is not CheckStatus.FAIL


def test_criticality_is_taken_from_the_check_row_not_decided_by_the_runner():
    segments = [seg(1, AGENT, "a", 0, 1), seg(2, CUSTOMER, "b", 30, 31)]
    assert RUNNER.run(Call(id=1, lead_id=1), behaviour_check(DEAD_AIR, critical=True), segments).critical is True


def test_behaviour_results_never_move_the_gate_off_auto_passed():
    """gate_engine is unchanged: behaviour results, however noisy, cannot cause HELD or QA_REVIEW."""
    passing = [CheckResult("dob_match", 1, "FACTUAL", True, CheckStatus.PASS, "ok"),
               CheckResult("disclaimer", 1, "VERBATIM", True, CheckStatus.PASS, "ok")]
    segments = [seg(1, AGENT, "um um um uh", 0, 4), seg(2, CUSTOMER, "b", 3, 90)]
    behaviour = [
        evaluate(DEAD_AIR, segments, code="dead_air"), evaluate(FILLER, segments, code="filler_ratio"),
        evaluate({"signal": "overlapping_speech"}, segments, code="overlapping_speech"),
        evaluate({"signal": "dead_air"}, segments, code="broken_config"),  # NOT_CHECKABLE
        evaluate({"signal": "dead_air", "threshold_seconds": 1}, segments, code="critical_by_mistake", critical=True),
    ]
    assert any(r.reason.startswith("Coaching note") for r in behaviour)
    assert any(r.status is CheckStatus.NOT_CHECKABLE for r in behaviour)

    outcome = GateEngine().decide(passing + behaviour)
    assert outcome.status is GateStatus.AUTO_PASSED
    assert "Coaching note" not in outcome.reason  # behaviour is not even part of the gate's reasoning


# ---------------------------------------------------------------------------
# the CheckRunner interface
# ---------------------------------------------------------------------------

def test_run_has_the_same_signature_as_the_other_runners():
    from app.services.checks.factual_check import FactualCheckRunner
    from app.services.checks.verbatim_runner import VerbatimCheckRunner

    assert isinstance(RUNNER, CheckRunner)
    assert inspect.signature(BehaviourCheckRunner.run) == inspect.signature(CheckRunner.run)
    assert inspect.signature(BehaviourCheckRunner.run) == inspect.signature(VerbatimCheckRunner.run)
    assert inspect.signature(BehaviourCheckRunner.run) == inspect.signature(FactualCheckRunner.run)


def test_run_reads_the_check_rows_configuration_and_library_version():
    segments = [seg(1, AGENT, "a", 0, 1), seg(2, CUSTOMER, "b", 30, 31)]
    result = RUNNER.run(Call(id=1, lead_id=1), behaviour_check(DEAD_AIR, code="dead_air"), segments)
    assert (result.check_id, result.check_version, result.check_type) == ("dead_air", 3, "BEHAVIOUR")
    assert result.reason.startswith("Coaching note")
    assert result == RUNNER.evaluate("dead_air", 3, False, DEAD_AIR, segments)


def test_run_rejects_checks_of_another_type():
    with pytest.raises(ValueError, match="cannot run"):
        RUNNER.run(Call(id=1, lead_id=1), behaviour_check({}, check_type=CheckType.FACTUAL), [])


def test_inputs_are_not_modified():
    config = {"signal": "dead_air", "threshold_seconds": 10}
    segments = [seg(2, CUSTOMER, "b", 30, 31), seg(1, AGENT, "um a", 0, 1)]
    before = (copy.deepcopy(config), [(s.id, s.text, s.start_time, s.end_time) for s in segments])
    evaluate(config, segments)
    assert (config, [(s.id, s.text, s.start_time, s.end_time) for s in segments]) == before
