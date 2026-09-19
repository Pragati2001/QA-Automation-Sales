"""Builders for scoring / gate / pipeline tests: a retailer, versioned libraries, and a call with a
transcript, all created in the (rolled-back) test transaction."""

from datetime import datetime, timedelta, timezone

from app.models import (
    Call, CallStatus, Check, CheckLibrary, CheckType, Lead, Retailer, Transcript, TranscriptSegment,
)
from app.repositories.base import BaseRepository

UTC = timezone.utc
JAN = datetime(2026, 1, 1, tzinfo=UTC)
PHRASE = "this call is recorded for quality and compliance purposes"

# (speaker, text, start, end, confidence): a short call in the shape of the Phase 4 fixture
DEFAULT_SEGMENTS = [
    ("AGENT", f"Good afternoon, thanks for calling. Just so you know, {PHRASE}.", 0.0, 7.4, 0.97),
    ("CUSTOMER", "Okay, that's fine.", 7.9, 9.6, 0.95),
    ("AGENT", "Can you confirm your date of birth?", 16.8, 20.9, 0.97),
    ("CUSTOMER", "It's the first of January, nineteen ninety.", 21.4, 24.8, 0.93),
    ("AGENT", "On this plan the peak rate is thirty one point nine cents per kilowatt hour.", 26.0, 33.2, 0.94),
    ("AGENT", "And what email address should we send the confirmation to?", 85.0, 88.6, 0.97),
    ("CUSTOMER", "It's synthetic dot test at example dot com.", 89.2, 93.1, 0.95),
]

CRM_OK = {"dob": "1990-01-01", "peak_rate": "31.9c/kWh", "email": "synthetic.test@example.com"}

DISCLAIMER = dict(code="recording_disclaimer", type=CheckType.VERBATIM, critical=True,
                  configuration={"required_phrases": [PHRASE], "match_threshold": 0.9})
DOB = dict(code="dob_match", type=CheckType.FACTUAL, critical=True,
           configuration={"field": "dob", "source_of_truth": "CRM.dob"})
RATE = dict(code="rate_match", type=CheckType.FACTUAL, critical=True,
            configuration={"field": "rate", "source_of_truth": "CRM.peak_rate"})
EMAIL = dict(code="email_match", type=CheckType.FACTUAL, critical=True,
             configuration={"field": "email", "source_of_truth": "CRM.email"})
DEAD_AIR = dict(code="dead_air", type=CheckType.BEHAVIOUR, critical=False,
                configuration={"threshold_seconds": None})


def make_retailer(session, code="scenario_retailer"):
    return BaseRepository(Retailer, session).create(code=code, name=code)


def add_library(session, retailer, version, effective_from, effective_to=None, checks=()):
    library = BaseRepository(CheckLibrary, session).create(
        retailer_id=retailer.id, name=f"v{version}", version=version,
        effective_from=effective_from, effective_to=effective_to,
    )
    for c in checks:
        BaseRepository(Check, session).create(
            library_id=library.id, code=c["code"], name=c["code"], check_type=c["type"],
            critical=c["critical"], configuration=c["configuration"],
        )
    return library


def make_call(session, retailer, *, started_at=JAN + timedelta(days=30), crm=None,
              segments=DEFAULT_SEGMENTS, with_transcript=True, external_lead_id="1"):
    lead = BaseRepository(Lead, session).create(
        retailer_id=retailer.id, external_lead_id=external_lead_id, crm_fields=dict(CRM_OK if crm is None else crm),
    )
    call = BaseRepository(Call, session).create(
        lead_id=lead.id, status=CallStatus.PROCESSING, call_started_at=started_at,
    )
    if with_transcript:
        transcript = BaseRepository(Transcript, session).create(call_id=call.id, provider="test")
        session.add_all(
            TranscriptSegment(transcript_id=transcript.id, sequence=i, speaker=sp, text=text,
                              start_time=start, end_time=end, confidence=conf)
            for i, (sp, text, start, end, conf) in enumerate(segments, start=1)
        )
    session.commit()  # release the savepoint so a later rollback inside the code under test keeps this
    return call


def with_confidence(segments, index, confidence):
    """A copy of `segments` with one segment's ASR confidence changed."""
    edited = list(segments)
    speaker, text, start, end, _ = edited[index]
    edited[index] = (speaker, text, start, end, confidence)
    return edited
