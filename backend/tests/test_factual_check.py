import copy
import datetime as dt
import inspect

import pytest

from app.models import Call, Check, CheckLibrary, CheckType, TranscriptSegment
from app.schemas.check_result import CheckResult, CheckStatus
from app.services.checks.base import CheckRunner
from app.services.checks.extraction import FIELD_SPECS, ExtractionResult, extract_value
from app.services.checks.factual_check import FactualCheckRunner, fuzzy_compare
from app.services.checks.normalization import normalize_field
from app.services.checks.source_resolver import resolve_expected_value
from app.services.checks.verbatim_runner import VerbatimCheckRunner
from app.services.transcription.fixture_provider import FixtureTranscriptionProvider

AGENT, CUSTOMER = "AGENT", "CUSTOMER"
RUNNER = FactualCheckRunner()


def seg(id, speaker, text, start, confidence=0.95, end=None):
    return TranscriptSegment(
        id=id, sequence=id, speaker=speaker, text=text, start_time=start,
        end_time=start + 3 if end is None else end, confidence=confidence,
    )


def evaluate(config, segments, crm=None, plan=None, *, critical=True, version=3):
    return RUNNER.evaluate("check_x", version, critical, config, segments, crm, plan)


def crm_check(field, **extra):
    return {"field": field, "source_of_truth": f"CRM.{field}", **extra}


# ---------------------------------------------------------------------------
# PASS / FAIL for the core fields
# ---------------------------------------------------------------------------

RATE_CONFIG = {"field": "rate", "source_of_truth": "RETAILER_PLAN.peak_rate"}


def test_price_matching_passes():
    segments = [seg(1, AGENT, "On this plan the peak rate is thirty one point nine cents per kilowatt hour.", 26.0, 0.94)]
    result = evaluate(RATE_CONFIG, segments, plan={"peak_rate": "31.9c/kWh"})

    assert result.status is CheckStatus.PASS
    assert (result.check_id, result.check_version, result.check_type, result.critical) == ("check_x", 3, "FACTUAL", True)
    assert result.expected_value == "31.9c/kWh"
    assert result.actual_value == "thirty one point nine cents"
    assert (result.extraction_confidence, result.rule_confidence) == (0.94, 1.0)


def test_price_mismatch_fails_with_expected_and_actual():
    """The handout's worked example: agent says 28.6, the plan says 31.9."""
    result = evaluate(RATE_CONFIG, [seg(1, AGENT, "Peak is 28.6 cents.", 842.1)], plan={"peak_rate": "31.9c/kWh"})

    assert result.status is CheckStatus.FAIL
    assert (result.expected_value, result.actual_value) == ("31.9c/kWh", "28.6 cents")
    assert "mismatch" in result.reason and "28.6" in result.reason and "31.9" in result.reason
    assert result.rule_confidence == 1.0


def test_numeric_tolerance_is_configurable():
    segments = [seg(1, AGENT, "the rate is 31.95 cents", 0)]
    plan = {"peak_rate": 31.9}
    assert evaluate(RATE_CONFIG, segments, plan=plan).status is CheckStatus.FAIL  # 0.05 > default 0.01
    assert evaluate({**RATE_CONFIG, "tolerance": 0.1}, segments, plan=plan).status is CheckStatus.PASS


def test_dollar_and_cent_rates_are_compared_in_the_same_unit():
    segments = [seg(1, AGENT, "the rate is 31.9 cents per kilowatt hour", 0)]
    assert evaluate(RATE_CONFIG, segments, plan={"peak_rate": "$0.319 per kWh"}).status is CheckStatus.PASS


SPEED_SEGMENT = seg(1, AGENT, "You'll get download speeds of one hundred megabits and upload speeds of twenty megabits.", 5)


@pytest.mark.parametrize("field, crm_value, status", [
    ("download_speed", 100, CheckStatus.PASS),
    ("download_speed", "100 Mbps", CheckStatus.PASS),
    ("download_speed", 50, CheckStatus.FAIL),
    ("upload_speed", "20", CheckStatus.PASS),
    ("upload_speed", 40, CheckStatus.FAIL),
])
def test_speed(field, crm_value, status):
    result = evaluate(crm_check(field), [SPEED_SEGMENT], crm={field: crm_value})
    assert result.status is status


def test_gigabit_speeds_are_converted_to_megabits():
    segments = [seg(1, AGENT, "download speeds of one gigabit", 0)]
    assert evaluate(crm_check("download_speed"), segments, crm={"download_speed": 1000}).status is CheckStatus.PASS


def test_email_matching_passes_regardless_of_case_and_spoken_form():
    segments = [seg(1, CUSTOMER, "It's J dot Smith at Gmail dot com.", 22.1, 0.93)]
    result = evaluate(crm_check("email"), segments, crm={"email": "  J.Smith@gmail.com "})
    assert result.status is CheckStatus.PASS
    assert result.actual_value == "j.smith@gmail.com"


def test_email_mismatch_fails():
    """The handout's example: transcript says gmail, CRM says gmial."""
    segments = [seg(1, CUSTOMER, "j.smith@gmail.com", 1330.0, 0.96)]
    result = evaluate(crm_check("email"), segments, crm={"email": "j.smith@gmial.com"})
    assert result.status is CheckStatus.FAIL
    assert (result.expected_value, result.actual_value) == ("j.smith@gmial.com", "j.smith@gmail.com")


# ---------------------------------------------------------------------------
# dob, name, modem_model, plus the other supported fields
# ---------------------------------------------------------------------------

DOB_SEGMENT = seg(1, CUSTOMER, "It's the first of January, nineteen ninety.", 21.4, 0.93)


@pytest.mark.parametrize("crm_value", ["1990-01-01", "01/01/1990", "1 January 1990", "1.1.1990", dt.date(1990, 1, 1)])
def test_dob_matches_across_crm_date_formats(crm_value):
    result = evaluate(crm_check("dob"), [DOB_SEGMENT], crm={"dob": crm_value})
    assert result.status is CheckStatus.PASS, result.reason


def test_dob_mismatch_fails():
    result = evaluate(crm_check("dob"), [DOB_SEGMENT], crm={"dob": "1990-01-02"})
    assert result.status is CheckStatus.FAIL


def test_unreadable_crm_dob_is_not_checkable():
    result = evaluate(crm_check("dob"), [DOB_SEGMENT], crm={"dob": "12/31/1990"})  # month-first: not guessed at
    assert result.status is CheckStatus.NOT_CHECKABLE and "could not be interpreted" in result.reason


def test_name_matching_passes_and_ignores_case_and_punctuation():
    segments = [seg(1, CUSTOMER, "Hi, my name is John Smith and I'm calling about my bill.", 2)]
    result = evaluate(crm_check("name"), segments, crm={"name": "JOHN  SMITH."})
    assert result.status is CheckStatus.PASS and result.rule_confidence == 1.0


def test_name_uses_fuzzy_matching_and_reports_the_similarity_as_rule_confidence():
    segments = [seg(1, CUSTOMER, "my name is Jon Smith", 2)]
    result = evaluate(crm_check("name"), segments, crm={"name": "John Smith"})
    assert result.status is CheckStatus.PASS
    assert 0.85 <= result.rule_confidence < 1.0  # a comparison confidence, separate from extraction confidence
    assert result.extraction_confidence == 0.95
    assert evaluate(crm_check("name"), segments, crm={"name": "Jane Doe"}).status is CheckStatus.FAIL


def test_modem_model_spoken_number_form_matches():
    segments = [seg(1, AGENT, "Your modem is a CF forty and it will be posted to you.", 60)]
    result = evaluate(crm_check("modem_model"), segments, crm={"modem_model": "CF40"})
    assert result.status is CheckStatus.PASS
    assert result.actual_value == "cf forty"


def test_modem_model_with_a_different_number_fails_even_though_the_text_is_similar():
    segments = [seg(1, AGENT, "Your modem is a CF fifty", 60)]
    result = evaluate(crm_check("modem_model"), segments, crm={"modem_model": "CF40"})
    assert result.status is CheckStatus.FAIL


def test_phone_matches_across_formats():
    segments = [seg(1, CUSTOMER, "my number is oh four one two three four five six seven eight", 5)]
    assert evaluate(crm_check("phone"), segments, crm={"phone": "+61 412 345 678"}).status is CheckStatus.PASS


def test_total_minimum_cost():
    segments = [seg(1, AGENT, "The total minimum cost is $1,250.00 over the 24 months.", 90)]
    assert evaluate(crm_check("total_minimum_cost"), segments, crm={"total_minimum_cost": 1250}).status is CheckStatus.PASS
    assert evaluate(crm_check("total_minimum_cost"), segments, crm={"total_minimum_cost": 1200}).status is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# NOT_CHECKABLE: nothing to compare against, redacted, or not said
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crm", [{}, {"email": None}, {"email": ""}, {"email": "   "}, {"other": "x"}, None])
def test_missing_crm_value_is_not_checkable(crm):
    result = evaluate(crm_check("email"), [seg(1, CUSTOMER, "j.smith@gmail.com", 1)], crm=crm)
    assert result.status is CheckStatus.NOT_CHECKABLE
    assert "Expected value not available" in result.reason
    assert result.expected_value is None


def test_missing_plan_value_is_not_checkable():
    result = evaluate(RATE_CONFIG, [seg(1, AGENT, "the rate is 31.9 cents", 1)], plan={})
    assert result.status is CheckStatus.NOT_CHECKABLE


def test_no_fallback_to_a_different_source_of_truth():
    """The value exists in the plan but the check points at the CRM: there is no expected value."""
    result = evaluate(
        {"field": "rate", "source_of_truth": "CRM.peak_rate"},
        [seg(1, AGENT, "the rate is 31.9 cents", 1)],
        crm={}, plan={"peak_rate": "31.9c/kWh"},
    )
    assert result.status is CheckStatus.NOT_CHECKABLE


def test_redacted_value_is_not_checkable_and_says_so():
    segments = [seg(1, AGENT, "What's your email?", 20), seg(2, CUSTOMER, "Sure, it's [EMAIL].", 22.5, 0.97)]
    result = evaluate(crm_check("email"), segments, crm={"email": "j.smith@gmail.com"})

    assert result.status is CheckStatus.NOT_CHECKABLE
    assert "redacted" in result.reason
    assert "not found in transcript" not in result.reason
    assert result.expected_value == "j.smith@gmail.com"  # so a human reviewer knows what to verify
    assert result.actual_value is None
    assert [e.segment_id for e in result.evidence] == ["2"]  # where the masked value was spoken


def test_redacted_and_never_said_have_different_reasons():
    crm = {"dob": "1990-01-01"}
    redacted = evaluate(crm_check("dob"), [seg(1, CUSTOMER, "my birthday is [DOB]", 5)], crm=crm)
    never_said = evaluate(crm_check("dob"), [seg(1, CUSTOMER, "I'd like to check my bill", 5)], crm=crm)

    assert redacted.status is never_said.status is CheckStatus.NOT_CHECKABLE
    assert "redacted" in redacted.reason and "not found in transcript" not in redacted.reason
    assert "not found in transcript" in never_said.reason and "redacted" not in never_said.reason
    assert never_said.expected_value == "1990-01-01"


def test_a_placeholder_for_another_field_is_not_a_redaction_of_this_one():
    result = evaluate(crm_check("dob"), [seg(1, CUSTOMER, "my email is [EMAIL]", 5)], crm={"dob": "1990-01-01"})
    assert "not found in transcript" in result.reason


def test_a_real_value_alongside_a_placeholder_is_not_redacted():
    segments = [seg(1, CUSTOMER, "it's [EMAIL]", 5), seg(2, CUSTOMER, "yes j.smith@gmail.com", 9)]
    assert evaluate(crm_check("email"), segments, crm={"email": "j.smith@gmail.com"}).status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# LOW_CONFIDENCE
# ---------------------------------------------------------------------------

def test_extraction_confidence_below_default_threshold_is_low_confidence():
    result = evaluate(crm_check("email"), [seg(1, CUSTOMER, "j.smith@gmail.com", 5, confidence=0.62)],
                      crm={"email": "j.smith@gmail.com"})

    assert result.status is CheckStatus.LOW_CONFIDENCE  # even though the values match
    assert result.extraction_confidence == 0.62
    assert result.rule_confidence is None  # no comparison was made
    assert "0.62" in result.reason and "0.70" in result.reason


def test_confidence_threshold_is_configurable():
    segments = [seg(1, CUSTOMER, "j.smith@gmail.com", 5, confidence=0.62)]
    crm = {"email": "j.smith@gmail.com"}
    assert evaluate(crm_check("email", confidence_threshold=0.6), segments, crm=crm).status is CheckStatus.PASS
    assert evaluate(crm_check("email", confidence_threshold=0.9), segments, crm=crm).status is CheckStatus.LOW_CONFIDENCE


def test_low_confidence_beats_a_mismatch():
    """Not sure what was said -> route to a human, don't declare a critical FAIL."""
    result = evaluate(crm_check("email"), [seg(1, CUSTOMER, "j.smith@gmail.com", 5, confidence=0.5)],
                      crm={"email": "someone.else@gmail.com"})
    assert result.status is CheckStatus.LOW_CONFIDENCE


def test_conflicting_mentions_are_low_confidence_not_silently_resolved():
    segments = [
        seg(1, CUSTOMER, "it's j.smith@gmail.com", 10, 0.95),
        seg(2, CUSTOMER, "sorry, that's j.smyth@gmail.com", 20, 0.9),
    ]
    result = evaluate(crm_check("email"), segments, crm={"email": "j.smith@gmail.com"})

    assert result.status is CheckStatus.LOW_CONFIDENCE
    assert "Conflicting" in result.reason
    assert "j.smith@gmail.com" in result.actual_value and "j.smyth@gmail.com" in result.actual_value
    assert [e.segment_id for e in result.evidence] == ["1", "2"]  # both mentions shown


def test_the_same_value_said_twice_is_not_a_conflict():
    segments = [seg(1, CUSTOMER, "j.smith@gmail.com", 10, 0.6), seg(2, CUSTOMER, "yes, j dot smith at gmail dot com", 20, 0.95)]
    result = evaluate(crm_check("email"), segments, crm={"email": "j.smith@gmail.com"})
    assert result.status is CheckStatus.PASS
    assert result.extraction_confidence == 0.95  # the better-heard mention
    assert [e.segment_id for e in result.evidence] == ["2"]


def test_unknown_confidence_is_not_treated_as_low():
    result = evaluate(crm_check("email"), [seg(1, CUSTOMER, "j.smith@gmail.com", 5, confidence=None)],
                      crm={"email": "j.smith@gmail.com"})
    assert result.status is CheckStatus.PASS and result.extraction_confidence is None


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

def test_evidence_has_the_correct_segment_and_timestamps():
    segments = [
        seg(10, AGENT, "What email address should we send that to?", 88.0, end=91.5),
        seg(11, CUSTOMER, "It's synthetic dot test at example dot com.", 92.0, 0.97, end=96.4),
        seg(12, AGENT, "Thanks, got that.", 97.0, end=98.0),
    ]
    result = evaluate(crm_check("email"), segments, crm={"email": "synthetic.test@example.com"})

    assert result.status is CheckStatus.PASS
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert (ev.segment_id, ev.start_time, ev.end_time) == ("11", 92.0, 96.4)
    assert ev.text == "It's synthetic dot test at example dot com."


def test_fail_evidence_points_at_the_segment_where_the_wrong_value_was_said():
    segments = [seg(7, AGENT, "Peak is 28.6 cents.", 842.1, end=846.4), seg(8, CUSTOMER, "Okay.", 847)]
    result = evaluate(RATE_CONFIG, segments, plan={"peak_rate": "31.9c/kWh"})
    assert result.status is CheckStatus.FAIL
    assert [(e.segment_id, e.start_time, e.end_time, e.text) for e in result.evidence] == [
        ("7", 842.1, 846.4, "Peak is 28.6 cents."),
    ]


# ---------------------------------------------------------------------------
# speaker scoping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field, crm_key, crm_value, text", [
    ("rate", None, None, "I saw 31.9 cents on your website"),
    ("price", None, None, "I saw 31.9 cents on your website"),
    ("total_minimum_cost", "total_minimum_cost", 1250, "I read that the total minimum cost is $1,250 in the fine print"),
])
def test_a_customer_stated_figure_is_not_picked_up_for_agent_scoped_fields(field, crm_key, crm_value, text):
    """The customer mentions a number that even matches the expected value in passing; the check is
    about what the AGENT disclosed, so it must not count."""
    config = crm_check("total_minimum_cost") if crm_key else {"field": field, "source_of_truth": "RETAILER_PLAN.peak_rate"}
    result = evaluate(
        config, [seg(1, AGENT, "Let me pull up your plan.", 0), seg(2, CUSTOMER, text, 4)],
        crm={crm_key: crm_value} if crm_key else None, plan={"peak_rate": 31.9},
    )
    assert result.status is CheckStatus.NOT_CHECKABLE
    assert "not found in transcript" in result.reason


def test_the_agent_saying_it_does_count():
    segments = [seg(1, AGENT, "Let me pull up your plan.", 0), seg(2, AGENT, "The peak rate is 31.9 cents.", 4)]
    assert evaluate(RATE_CONFIG, segments, plan={"peak_rate": 31.9}).status is CheckStatus.PASS


def test_customer_scoped_fields_ignore_the_agent_unless_the_speaker_is_overridden():
    segments = [seg(1, AGENT, "Let me read that back: j.smith@gmail.com", 30, 0.95)]
    crm = {"email": "j.smith@gmail.com"}
    assert evaluate(crm_check("email"), segments, crm=crm).status is CheckStatus.NOT_CHECKABLE  # default: customer
    assert evaluate(crm_check("email", speaker="agent"), segments, crm=crm).status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# which mention wins
# ---------------------------------------------------------------------------

def test_delivery_address_uses_the_last_confirmed_mention():
    segments = [
        seg(1, AGENT, "What address should we deliver to?", 10),
        seg(2, CUSTOMER, "12 Smith Street", 13),
        seg(3, AGENT, "Sorry, which address should we deliver it to again?", 20),
        seg(4, CUSTOMER, "Actually, deliver it to 14 Jones Road", 24),
    ]
    correct = evaluate(crm_check("delivery_address"), segments, crm={"delivery_address": "14 Jones Road"})
    assert correct.status is CheckStatus.PASS
    assert correct.actual_value == "14 jones road"
    assert [e.segment_id for e in correct.evidence] == ["4"]

    # ...and the first mention being right does not rescue a wrong final address
    stale = evaluate(crm_check("delivery_address"), segments, crm={"delivery_address": "12 Smith Street"})
    assert stale.status is CheckStatus.FAIL


def test_a_house_number_off_by_one_is_a_mismatch_despite_similar_text():
    segments = [seg(1, AGENT, "and the delivery address?", 10), seg(2, CUSTOMER, "21 Smith Street", 13)]
    result = evaluate(crm_check("delivery_address"), segments, crm={"delivery_address": "12 Smith Street"})
    assert result.status is CheckStatus.FAIL
    assert result.rule_confidence > 0.8  # the text is very similar; the number is what differs


def test_service_and_delivery_addresses_are_compared_independently():
    segments = [
        seg(1, AGENT, "What's the service address?", 0), seg(2, CUSTOMER, "5 Oak Avenue", 3),
        seg(3, AGENT, "And the delivery address?", 8), seg(4, CUSTOMER, "77 Pine Court", 12),
    ]
    crm = {"service_address": "5 Oak Avenue", "delivery_address": "9 Elm Lane"}  # the two legitimately differ
    service = evaluate(crm_check("service_address"), segments, crm=crm)
    delivery = evaluate(crm_check("delivery_address"), segments, crm=crm)

    assert service.status is CheckStatus.PASS and service.actual_value == "5 oak avenue"
    assert delivery.status is CheckStatus.FAIL and delivery.actual_value == "77 pine court"


def test_address_normalization_ignores_case_punctuation_and_spacing():
    segments = [seg(1, AGENT, "service address please?", 0), seg(2, CUSTOMER, "5/12 Smith St., Richmond", 3)]
    result = evaluate(crm_check("service_address"), segments, crm={"service_address": "5 / 12  smith st,  richmond"})
    assert result.status is CheckStatus.PASS


def test_first_agent_price_disclosure_wins_even_when_a_later_figure_matches():
    """The real discrepancy case: the initial disclosure was wrong, a later on-screen figure is right.
    That is a FAIL. It must not be papered over by preferring whichever value happens to match."""
    segments = [
        seg(1, AGENT, "The peak rate is 28.6 cents.", 100),
        seg(2, AGENT, "Looking at the screen it says 31.9 cents.", 400),
    ]
    result = evaluate(RATE_CONFIG, segments, plan={"peak_rate": "31.9c/kWh"})

    assert result.status is CheckStatus.FAIL
    assert result.actual_value == "28.6 cents"
    assert [e.segment_id for e in result.evidence] == ["1"]


def test_first_agent_total_minimum_cost_wins_even_when_a_later_figure_matches():
    segments = [
        seg(1, AGENT, "The total minimum cost is $1,100 over the term.", 100),
        seg(2, AGENT, "Sorry, the screen shows the total minimum cost is $1,250.", 400),
    ]
    result = evaluate(crm_check("total_minimum_cost"), segments, crm={"total_minimum_cost": 1250})
    assert result.status is CheckStatus.FAIL
    assert result.actual_value == "$1,100"


def test_context_keywords_narrow_which_rate_is_used():
    segments = [seg(1, AGENT, "the off-peak rate is 20 cents", 5), seg(2, AGENT, "the peak rate is 31.9 cents", 9)]
    config = {**RATE_CONFIG, "context_keywords": ["the peak rate"]}
    assert evaluate(config, segments, plan={"peak_rate": 31.9}).status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# unusable configuration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("config", [
    {},
    {"source_of_truth": "CRM.email"},
    {"field": "email"},
    {"field": "shoe_size", "source_of_truth": "CRM.shoe_size"},
    {"field": None, "source_of_truth": "CRM.email"},
    {"field": "email", "source_of_truth": ""},
    {"field": "email", "source_of_truth": "CRM.email", "confidence_threshold": 1.5},
    {"field": "email", "source_of_truth": "CRM.email", "confidence_threshold": "high"},
    {"field": "rate", "source_of_truth": "CRM.rate", "tolerance": -1},
    {"field": "name", "source_of_truth": "CRM.name", "fuzzy_threshold": 0},
    {"compare_to": "crm_field", "field": "email"},  # the shape the demo Retailer 1 fixture ships with today
])
def test_unusable_configuration_is_not_checkable(config):
    result = evaluate(config, [seg(1, CUSTOMER, "j.smith@gmail.com", 1)], crm={"email": "j.smith@gmail.com"})
    assert result.status is CheckStatus.NOT_CHECKABLE
    assert result.reason.startswith("Check configuration is unusable")


# ---------------------------------------------------------------------------
# the CheckRunner interface
# ---------------------------------------------------------------------------

def make_check(config, check_type=CheckType.FACTUAL, version=3):
    return Check(id=1, code="email_match", name="Email", check_type=check_type, critical=True,
                 configuration=config, library=CheckLibrary(version=version))


def test_run_has_exactly_the_signature_of_the_verbatim_runner():
    assert isinstance(RUNNER, CheckRunner)
    assert inspect.signature(FactualCheckRunner.run) == inspect.signature(VerbatimCheckRunner.run)
    assert inspect.signature(FactualCheckRunner.run) == inspect.signature(CheckRunner.run)


def test_run_unpacks_the_check_and_gives_the_same_result_as_evaluate():
    segments = [seg(1, CUSTOMER, "j.smith@gmail.com", 5)]
    crm = {"email": "j.smith@gmail.com"}
    via_run = RUNNER.run(Call(id=1, lead_id=1), make_check(crm_check("email")), segments, crm_fields=crm)
    via_evaluate = RUNNER.evaluate("email_match", 3, True, crm_check("email"), segments, crm, None)

    assert isinstance(via_run, CheckResult)
    assert via_run == via_evaluate
    assert (via_run.check_id, via_run.check_version, via_run.critical) == ("email_match", 3, True)


def test_run_rejects_a_check_of_another_type():
    with pytest.raises(ValueError, match="cannot run"):
        RUNNER.run(Call(id=1, lead_id=1), make_check({}, check_type=CheckType.VERBATIM), [])


def test_inputs_are_not_modified():
    config = crm_check("email", speaker="customer")
    segments = [seg(2, CUSTOMER, "it's j.smith@gmail.com", 9), seg(1, AGENT, "hello", 1)]
    crm = {"email": "j.smith@gmail.com"}
    before = copy.deepcopy((config, crm)), [(s.id, s.text, s.speaker, s.start_time) for s in segments]

    evaluate(config, segments, crm=crm)

    assert (config, crm) == before[0]
    assert [(s.id, s.text, s.speaker, s.start_time) for s in segments] == before[1]


# ---------------------------------------------------------------------------
# against the Phase 4 synthetic transcript
# ---------------------------------------------------------------------------

def _synthetic_segments():
    return [
        seg(i, s.speaker, s.text, s.start_time, s.confidence, end=s.end_time)
        for i, s in enumerate(FixtureTranscriptionProvider().transcribe(b""), start=1)
    ]


def test_synthetic_transcript_dob_and_rate_pass_from_spoken_words():
    segments = _synthetic_segments()
    dob = evaluate(crm_check("dob"), segments, crm={"dob": "1990-01-01"})
    rate = evaluate(RATE_CONFIG, segments, plan={"peak_rate": "31.9c/kWh"})
    assert dob.status is CheckStatus.PASS and dob.evidence[0].segment_id == "6"
    assert rate.status is CheckStatus.PASS and rate.evidence[0].segment_id == "7"


def test_synthetic_transcript_email_is_low_confidence_because_the_asr_was_unsure():
    result = evaluate(crm_check("email"), _synthetic_segments(), crm={"email": "synthetic.test@example.com"})
    assert result.status is CheckStatus.LOW_CONFIDENCE
    assert result.extraction_confidence == 0.62
    assert (result.evidence[0].segment_id, result.evidence[0].start_time, result.evidence[0].end_time) == ("10", 89.2, 93.1)


# ---------------------------------------------------------------------------
# resolve_expected_value
# ---------------------------------------------------------------------------

CRM = {"email": "a@b.com", "address": {"service": "5 Oak Ave", "postcode": 3121}, "nothing": None, "empty": "", "tags": ["x"]}
PLAN = {"peak_rate": "31.9c/kWh", "rates": {"peak": 31.9}}


@pytest.mark.parametrize("path, expected", [
    ("CRM.email", "a@b.com"),
    ("CRM.address.service", "5 Oak Ave"),
    ("CRM.address.postcode", 3121),
    ("RETAILER_PLAN.peak_rate", "31.9c/kWh"),
    ("RETAILER_PLAN.rates.peak", 31.9),
    ("crm.email", "a@b.com"),
])
def test_resolves_dot_paths(path, expected):
    assert resolve_expected_value(path, CRM, PLAN) == expected


@pytest.mark.parametrize("path", [
    "CRM.missing", "CRM.address.missing", "CRM.email.deeper", "CRM.nothing", "CRM.empty",
    "CRM.address", "CRM.tags", "RETAILER_PLAN.missing", "OTHER.email", "CRM", "CRM.", "email", "", None, 42,
])
def test_missing_null_or_unusable_paths_give_none_and_never_raise(path):
    assert resolve_expected_value(path, CRM, PLAN) is None


def test_missing_sources_give_none():
    assert resolve_expected_value("CRM.email", None, PLAN) is None
    assert resolve_expected_value("RETAILER_PLAN.peak_rate", CRM, None) is None


def test_resolver_never_falls_back_to_the_other_source():
    assert resolve_expected_value("CRM.peak_rate", CRM, PLAN) is None
    assert resolve_expected_value("RETAILER_PLAN.email", CRM, PLAN) is None


# ---------------------------------------------------------------------------
# normalization (independent of extraction)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind, raw, expected", [
    ("email", "  J.Smith@Gmail.COM ", "j.smith@gmail.com"),
    ("rate", "31.9c/kWh", 31.9), ("rate", "thirty one point nine cents", 31.9), ("rate", "$0.319 per kWh", 31.9),
    ("money", "$1,250.00", 1250.0), ("money", "twelve hundred dollars", 1200.0),
    ("speed", "one hundred megabits per second", 100.0), ("speed", "1 Gbps", 1000.0), ("speed", "20", 20.0),
    ("dob", "1990-01-01", "1990-01-01"), ("dob", "01/01/1990", "1990-01-01"),
    ("dob", "the first of January, nineteen ninety", "1990-01-01"), ("dob", "January 1st, 1990", "1990-01-01"),
    ("dob", "the fifth of june two thousand and five", "2005-06-05"), ("dob", "12 May nineteen oh five", "1905-05-12"),
    ("name", "John  O'Brien-Smith.", "john obrien smith"),
    ("modem", "CF forty", "cf40"), ("modem", "CF-40", "cf40"), ("modem", "cf 40", "cf40"),
    ("address", "5/12  Smith St., Richmond", "5 12 smith st richmond"),
    ("phone", "+61 412 345 678", "0412345678"),
])
def test_normalization(kind, raw, expected):
    got = normalize_field(kind, raw)
    assert got == pytest.approx(expected) if isinstance(expected, float) else got == expected


@pytest.mark.parametrize("kind, raw", [
    ("dob", "12/31/1990"), ("dob", "31/02/1990"), ("dob", "01/01/90"), ("dob", "the first of january twenty five"),
    ("dob", "soon"), ("rate", "cheap"), ("speed", "fast"), ("email", "   "), ("phone", "n/a"),
])
def test_uninterpretable_values_normalize_to_none_rather_than_guessing(kind, raw):
    assert normalize_field(kind, raw) is None


def test_fuzzy_compare_requires_identical_numbers():
    assert fuzzy_compare("12 smith st", "12 smith street", 0.8)[0] is True
    matched, ratio = fuzzy_compare("12 smith st", "21 smith st", 0.8)
    assert matched is False and ratio > 0.8


# ---------------------------------------------------------------------------
# extraction on its own
# ---------------------------------------------------------------------------

def test_extract_value_returns_the_documented_fields():
    result = extract_value([seg(4, CUSTOMER, "j.smith@gmail.com", 1, 0.9)], "email", {})
    assert isinstance(result, ExtractionResult)
    assert (result.value, result.confidence, result.source_segment_id, result.redacted) == ("j.smith@gmail.com", 0.9, "4", False)


def test_extract_value_redacted_result_has_no_value_or_confidence():
    result = extract_value([seg(4, CUSTOMER, "[EMAIL]", 1)], "email", {})
    assert (result.value, result.confidence, result.redacted) == (None, None, True)


def test_extract_value_not_said_is_not_redacted():
    result = extract_value([seg(4, CUSTOMER, "hello", 1)], "email", {})
    assert (result.value, result.confidence, result.source_segment_id, result.redacted) == (None, None, None, False)


def test_extract_value_rejects_an_unsupported_field():
    with pytest.raises(ValueError, match="Unsupported field"):
        extract_value([], "shoe_size", {})


def test_default_speakers_and_selection_rules_match_the_spec():
    agent = {"price", "rate", "download_speed", "upload_speed", "modem_model", "total_minimum_cost"}
    customer = {"email", "dob", "name", "phone", "service_address", "delivery_address"}
    assert {f for f, s in FIELD_SPECS.items() if s.speaker == "agent"} == agent
    assert {f for f, s in FIELD_SPECS.items() if s.speaker == "customer"} == customer
    assert {f for f, s in FIELD_SPECS.items() if s.selection == "first"} == {"price", "rate", "total_minimum_cost"}
    assert {f for f, s in FIELD_SPECS.items() if s.selection == "last"} == {"service_address", "delivery_address"}
