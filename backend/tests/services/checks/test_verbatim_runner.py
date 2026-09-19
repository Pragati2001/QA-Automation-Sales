import copy
import json

import pytest

from app.models import Call, Check, CheckLibrary, CheckType, TranscriptSegment
from app.schemas.check_result import CheckResult, CheckStatus
from app.services.checks.base import CheckRunner
from app.services.checks.verbatim_runner import VerbatimCheckRunner
from app.services.transcription.fixture_provider import DEFAULT_FIXTURE, FixtureTranscriptionProvider

PHRASE = "This call is recorded for quality and compliance purposes"
SECOND_PHRASE = "Please confirm you are the account holder"


def make_check(phrases=(PHRASE,), threshold=0.9, *, critical=True, version=3,
               check_type=CheckType.VERBATIM, configuration="default"):
    if configuration == "default":
        configuration = {"required_phrases": list(phrases), "match_threshold": threshold}
    return Check(
        id=1, code="recording_disclaimer", name="Recording disclaimer", check_type=check_type,
        critical=critical, configuration=configuration, library=CheckLibrary(version=version),
    )


def seg(id, speaker, text, start, end, confidence=0.95):
    return TranscriptSegment(
        id=id, sequence=id, speaker=speaker, text=text,
        start_time=start, end_time=end, confidence=confidence,
    )


CALL = Call(id=1, lead_id=1)
RUNNER = VerbatimCheckRunner()


def run(check, segments):
    return RUNNER.run(CALL, check, segments)


def test_runner_implements_the_reusable_interface():
    assert isinstance(RUNNER, CheckRunner)
    result = run(make_check(), [seg(1, "AGENT", PHRASE, 0, 5)])
    assert isinstance(result, CheckResult)


# --- the seven behaviours from the spec ------------------------------------

def test_exact_mandatory_phrase_passes():
    segments = [seg(1, "AGENT", f"Good afternoon, thanks for calling. Just so you know, {PHRASE.lower()}.", 0.0, 7.4)]
    result = run(make_check(), segments)

    assert result.status is CheckStatus.PASS
    assert (result.check_id, result.check_version, result.check_type, result.critical) == (
        "recording_disclaimer", 3, "VERBATIM", True,
    )
    assert result.expected_value == PHRASE
    assert result.actual_value == "this call is recorded for quality and compliance purposes"
    assert "lowest similarity 1.00" in result.reason


def test_minor_wording_difference_above_threshold_passes():
    segments = [seg(1, "AGENT", "Hello. This call is being recorded for quality and compliance purposes.", 0, 6)]

    assert run(make_check(threshold=0.9), segments).status is CheckStatus.PASS
    # ...but it isn't an exact match: a near-1.0 threshold rejects the same wording
    assert run(make_check(threshold=0.99), segments).status is CheckStatus.FAIL


def test_case_and_punctuation_are_ignored():
    segments = [seg(1, "AGENT", "THIS CALL, is RECORDED -- for quality; and compliance purposes!", 0, 5)]
    assert run(make_check(threshold=1.0), segments).status is CheckStatus.PASS


def test_missing_phrase_fails_when_nothing_similar_was_said():
    segments = [
        seg(1, "AGENT", "Good afternoon, thanks for calling.", 0, 3),
        seg(2, "AGENT", "How can I help you with your energy plan today?", 4, 8),
    ]
    result = run(make_check(), segments)

    assert result.status is CheckStatus.FAIL
    assert result.expected_value == PHRASE
    assert result.actual_value is None
    assert "not said by the agent" in result.reason and "no similar wording found" in result.reason
    assert result.evidence == []  # nothing resembled the phrase, so nothing to point at


def test_incorrect_phrase_fails_and_shows_the_closest_wording_as_evidence():
    segments = [
        seg(1, "AGENT", "Good afternoon, thanks for calling.", 0, 3, 0.97),
        seg(2, "AGENT", "Please note this call is not being taped for training and other purposes.", 4, 9, 0.92),
    ]
    result = run(make_check(), segments)

    assert result.status is CheckStatus.FAIL
    assert "best similarity" in result.reason and "< 0.90" in result.reason
    assert result.actual_value and result.actual_value != PHRASE.lower()
    assert [(e.segment_id, e.start_time, e.end_time) for e in result.evidence] == [("2", 4, 9)]
    assert result.asr_confidence == 0.92


def test_no_agent_segments_is_not_checkable():
    assert run(make_check(), []).status is CheckStatus.NOT_CHECKABLE
    only_customer = run(make_check(), [seg(1, "CUSTOMER", "Okay, thanks.", 0, 2)])
    assert only_customer.status is CheckStatus.NOT_CHECKABLE
    assert "No agent speech" in only_customer.reason
    assert only_customer.expected_value == PHRASE
    assert only_customer.evidence == []


def test_customer_saying_the_phrase_does_not_count():
    segments = [
        seg(1, "AGENT", "Good afternoon, how can I help?", 0, 3),
        seg(2, "CUSTOMER", PHRASE, 3.5, 8),
    ]
    result = run(make_check(), segments)

    assert result.status is CheckStatus.FAIL
    assert "2" not in [e.segment_id for e in result.evidence]

    # ...and if the customer is the only one speaking there is nothing to check at all
    assert run(make_check(), [segments[1]]).status is CheckStatus.NOT_CHECKABLE


def test_multiple_phrases_one_missing_fails():
    check = make_check(phrases=(PHRASE, SECOND_PHRASE))
    segments = [
        seg(1, "AGENT", f"Hi. {PHRASE}.", 0, 5),
        seg(2, "AGENT", "Thanks, now let's talk about your plan.", 6, 10),
    ]
    result = run(check, segments)

    assert result.status is CheckStatus.FAIL
    assert "1 of 2" in result.reason
    assert SECOND_PHRASE in result.reason and PHRASE not in result.reason  # names only the failing one
    assert result.expected_value == f"{PHRASE} | {SECOND_PHRASE}"

    passing = run(check, segments[:1] + [seg(3, "AGENT", f"{SECOND_PHRASE}?", 11, 14)])
    assert passing.status is CheckStatus.PASS
    assert "All 2" in passing.reason


def test_evidence_has_the_matching_segment_and_timestamps():
    segments = [
        seg(10, "AGENT", "Good afternoon, thanks for calling.", 0.0, 3.2, 0.98),
        seg(11, "CUSTOMER", "Hi there.", 3.5, 4.5, 0.97),
        seg(12, "AGENT", f"Just so you know, {PHRASE.lower()}.", 5.0, 11.4, 0.91),
        seg(13, "AGENT", "Now, how can I help?", 12.0, 14.0, 0.99),
    ]
    result = run(make_check(), segments)

    assert result.status is CheckStatus.PASS
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert (ev.segment_id, ev.start_time, ev.end_time) == ("12", 5.0, 11.4)
    assert ev.text == f"Just so you know, {PHRASE.lower()}."
    assert result.asr_confidence == 0.91  # preserved from the matching segment


def test_phrase_split_across_agent_turns_reports_every_segment_used():
    segments = [
        seg(1, "AGENT", "Just so you know, this call is recorded", 0.0, 3.0, 0.96),
        seg(2, "CUSTOMER", "Mm-hm, okay.", 3.1, 4.0, 0.99),
        seg(3, "AGENT", "for quality and compliance purposes.", 4.2, 7.0, 0.88),
    ]
    result = run(make_check(), segments)

    assert result.status is CheckStatus.PASS
    assert [(e.segment_id, e.start_time, e.end_time) for e in result.evidence] == [
        ("1", 0.0, 3.0), ("3", 4.2, 7.0),
    ]  # the customer's interjection is not evidence
    assert result.asr_confidence == 0.88  # the weakest supporting segment


def test_asr_confidence_is_none_when_segments_have_none():
    result = run(make_check(), [seg(1, "AGENT", PHRASE, 0, 5, confidence=None)])
    assert result.status is CheckStatus.PASS and result.asr_confidence is None


def test_segments_are_ordered_by_time_not_by_list_position():
    segments = [
        seg(2, "AGENT", "for quality and compliance purposes.", 4.2, 7.0),
        seg(1, "AGENT", "This call is recorded", 0.0, 3.0),
    ]
    assert run(make_check(), segments).status is CheckStatus.PASS


# --- unusable configuration: never guess a script or a strictness ----------

@pytest.mark.parametrize("configuration", [
    {},
    None,
    {"approved_script": None},  # what the demo Retailer 1 fixture ships today
    {"match_threshold": 0.9},
    {"required_phrases": [], "match_threshold": 0.9},
    {"required_phrases": None, "match_threshold": 0.9},
    {"required_phrases": "a plain string", "match_threshold": 0.9},
    {"required_phrases": ["   ", "..."], "match_threshold": 0.9},
    {"required_phrases": [PHRASE]},
    {"required_phrases": [PHRASE], "match_threshold": None},
    {"required_phrases": [PHRASE], "match_threshold": 0},
    {"required_phrases": [PHRASE], "match_threshold": 1.5},
    {"required_phrases": [PHRASE], "match_threshold": "0.9"},
    {"required_phrases": [PHRASE], "match_threshold": True},
])
def test_unusable_configuration_is_not_checkable(configuration):
    result = run(make_check(configuration=configuration), [seg(1, "AGENT", PHRASE, 0, 5)])
    assert result.status is CheckStatus.NOT_CHECKABLE
    assert result.reason.startswith("Check configuration is unusable")


def test_the_shipped_retailer1_verbatim_checks_are_not_checkable_until_phrases_are_supplied():
    fixture = json.loads(
        (DEFAULT_FIXTURE.parent / "retailer1_check_library_v1.json").read_text(encoding="utf-8")
    )
    for c in (c for c in fixture["checks"] if c["check_type"] == "VERBATIM"):
        check = make_check(configuration=c["configuration"])
        assert run(check, [seg(1, "AGENT", PHRASE, 0, 5)]).status is CheckStatus.NOT_CHECKABLE


def test_only_verbatim_checks_are_accepted():
    with pytest.raises(ValueError, match="cannot run"):
        run(make_check(check_type=CheckType.FACTUAL), [seg(1, "AGENT", PHRASE, 0, 5)])


# --- purity ----------------------------------------------------------------

def test_inputs_are_not_modified():
    check = make_check(phrases=(PHRASE, SECOND_PHRASE))
    segments = [
        seg(2, "AGENT", "Please confirm you are the account holder", 9, 12),
        seg(1, "AGENT", f"Hi, {PHRASE}", 0, 5),
        seg(3, "CUSTOMER", "Yes", 12, 13),
    ]
    config_before = copy.deepcopy(check.configuration)
    snapshot = [(s.id, s.sequence, s.speaker, s.text, s.start_time, s.end_time, s.confidence) for s in segments]

    run(check, segments)

    assert check.configuration == config_before
    assert [(s.id, s.sequence, s.speaker, s.text, s.start_time, s.end_time, s.confidence) for s in segments] == snapshot


# --- against the Phase 4 synthetic transcript ------------------------------

def _fixture_segments():
    data = FixtureTranscriptionProvider().transcribe(b"")
    return [
        seg(i, s.speaker, s.text, s.start_time, s.end_time, s.confidence)
        for i, s in enumerate(data, start=1)
    ]


def test_synthetic_transcript_disclaimer_passes_at_the_first_agent_turn():
    result = run(make_check(phrases=("this call is recorded for quality and compliance purposes",)), _fixture_segments())
    assert result.status is CheckStatus.PASS
    assert [(e.segment_id, e.start_time, e.end_time) for e in result.evidence] == [("1", 0.0, 7.4)]
    assert result.asr_confidence == 0.97


def test_synthetic_transcript_phrase_only_the_customer_says_fails():
    # the customer says "Yes, I'm the account holder"; the agent never says it
    result = run(make_check(phrases=("Yes I'm the account holder",), threshold=0.95), _fixture_segments())
    assert result.status is CheckStatus.FAIL
