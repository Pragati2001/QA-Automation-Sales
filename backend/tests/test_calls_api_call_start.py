import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.main import app
from app.models import Call, CheckResultRecord, GateDecision, GateStatus
from app.services.calls import processing_service
from app.services.storage.audio_storage import LocalAudioStorage, get_audio_storage
from tests.services.scenario import CRM_OK, DISCLAIMER, DOB, JAN, RATE, add_library, make_retailer

AUDIO = b"RIFF....WAVEfmt fake-but-non-empty-audio-bytes"


@pytest.fixture
def client(session, tmp_path, monkeypatch):
    monkeypatch.setattr(processing_service, "process_call", lambda call_id: None)  # not under test here
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_audio_storage] = lambda: LocalAudioStorage(str(tmp_path))
    make_retailer(session, "call_start_retailer")
    session.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def post(client, **extra):
    return client.post(
        "/api/v1/calls",
        data={"retailer_code": "call_start_retailer", "external_lead_id": "1", **extra},
        files={"audio": ("call.wav", AUDIO, "audio/wav")},
    )


def test_call_started_at_is_stored_and_returned(client, session):
    resp = post(client, call_started_at="2026-03-10T10:30:00+05:30")

    assert resp.status_code == 202
    call = session.get(Call, resp.json()["call_id"])
    assert call.call_started_at == datetime(2026, 3, 10, 5, 0, tzinfo=timezone.utc)  # same instant, UTC
    body = client.get(f"/api/v1/calls/{call.id}").json()
    assert datetime.fromisoformat(body["call_started_at"]) == call.call_started_at


def test_call_started_at_is_set_automatically_when_the_request_does_not_send_it(client, session):
    """The client only needs retailer_code, external_lead_id and audio."""
    before = datetime.now(timezone.utc)
    resp = post(client)  # no call_started_at
    after = datetime.now(timezone.utc)

    assert resp.status_code == 202
    call = session.get(Call, resp.json()["call_id"])
    assert call.call_started_at is not None
    assert call.call_started_at.tzinfo is not None  # timezone-aware
    assert before <= call.call_started_at <= after  # "now", not a guess

    body = client.get(f"/api/v1/calls/{call.id}").json()  # and it is returned to the client
    assert datetime.fromisoformat(body["call_started_at"]) == call.call_started_at


def test_the_request_needs_only_retailer_code_external_lead_id_and_audio(client):
    resp = client.post(
        "/api/v1/calls",
        data={"retailer_code": "call_start_retailer", "external_lead_id": "1"},
        files={"audio": ("call.wav", AUDIO, "audio/wav")},
    )
    assert resp.status_code == 202


def test_the_automatic_start_is_now_in_utc_and_timezone_aware(client, session, monkeypatch):
    """Pins the exact behaviour: datetime.now(timezone.utc), not a naive local time."""
    fixed = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    seen_tz = []

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            seen_tz.append(tz)
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr("app.api.calls.datetime", FrozenDatetime)
    resp = post(client)

    assert seen_tz == [timezone.utc]  # asked for UTC, timezone-aware
    assert session.get(Call, resp.json()["call_id"]).call_started_at == fixed


def test_calls_created_one_after_another_never_go_backwards_in_time(client, session):
    ids = [post(client).json()["call_id"] for _ in range(3)]
    started = [session.get(Call, i).call_started_at for i in ids]
    assert started == sorted(started)


def test_a_call_start_without_a_timezone_is_rejected(client):
    resp = post(client, call_started_at="2026-03-10T10:30:00")
    assert resp.status_code == 422 and "timezone" in resp.json()["detail"]


def test_an_unparseable_call_start_is_rejected(client):
    assert post(client, call_started_at="last tuesday").status_code == 422


def test_get_call_shows_the_gate_decision_once_there_is_one(client, session):
    call_id = post(client, call_started_at="2026-03-10T10:30:00+00:00").json()["call_id"]
    assert client.get(f"/api/v1/calls/{call_id}").json()["gate_decision"] is None

    session.add(GateDecision(call_id=call_id, status=GateStatus.HELD, reason="Held: dob_match failed",
                             check_library_version=3))
    session.commit()

    decision = client.get(f"/api/v1/calls/{call_id}").json()["gate_decision"]
    assert decision["status"] == "HELD" and decision["reason"] == "Held: dob_match failed"
    assert decision["check_library_version"] == 3 and decision["decided_at"]


# ---------------------------------------------------------------------------------------------
# Phase 7 uses the generated call_started_at to pick the check-library version.
# These run the real pipeline (transcription fixture -> scoring -> gate) after the POST.
# ---------------------------------------------------------------------------------------------

@pytest.fixture
def pipeline_client(session, tmp_path, monkeypatch):
    from contextlib import nullcontext

    storage = LocalAudioStorage(str(tmp_path))
    monkeypatch.setattr(processing_service, "SessionLocal", lambda: nullcontext(session))
    monkeypatch.setattr(processing_service, "get_audio_storage", lambda: storage)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_audio_storage] = lambda: storage
    retailer = make_retailer(session, "call_start_retailer")
    session.commit()
    with TestClient(app) as c:
        c.retailer = retailer
        yield c
    app.dependency_overrides.clear()


def scored(session, call_id):
    session.expire_all()
    call = session.get(Call, call_id)
    results = list(session.scalars(select(CheckResultRecord).where(CheckResultRecord.call_id == call_id)))
    return call, call.gate_decision, results


def test_the_generated_call_start_selects_the_active_library_and_the_call_auto_passes(pipeline_client, session):
    now = datetime.now(timezone.utc)
    library = add_library(session, pipeline_client.retailer, 1, JAN, checks=[DISCLAIMER, DOB, RATE])
    session.commit()

    resp = post(pipeline_client, crm_fields=json.dumps(CRM_OK))  # no call_started_at
    call, decision, results = scored(session, resp.json()["call_id"])

    assert call.call_started_at is not None and abs(call.call_started_at - now) < timedelta(seconds=30)
    assert decision.status is GateStatus.AUTO_PASSED
    assert (decision.check_library_id, decision.check_library_version) == (library.id, 1)
    assert {r.check_code for r in results} == {"recording_disclaimer", "dob_match", "rate_match"}
    assert call.status.value == "COMPLETED"


def test_the_version_in_effect_now_is_used_not_an_older_one(pipeline_client, session):
    now = datetime.now(timezone.utc)
    add_library(session, pipeline_client.retailer, 1, now - timedelta(days=30), now - timedelta(days=10), checks=[DISCLAIMER])
    v2 = add_library(session, pipeline_client.retailer, 2, now - timedelta(days=5), checks=[DISCLAIMER, DOB])
    session.commit()

    resp = post(pipeline_client, crm_fields=json.dumps(CRM_OK))
    _, decision, results = scored(session, resp.json()["call_id"])

    assert decision.check_library_id == v2.id and decision.check_library_version == 2
    assert {r.check_version for r in results} == {2}
    assert {r.check_code for r in results} == {"recording_disclaimer", "dob_match"}


def test_a_library_that_is_not_yet_effective_is_not_used_and_there_is_no_fallback(pipeline_client, session):
    now = datetime.now(timezone.utc)
    add_library(session, pipeline_client.retailer, 1, now + timedelta(days=1), checks=[DISCLAIMER])  # starts tomorrow
    session.commit()

    resp = post(pipeline_client, crm_fields=json.dumps(CRM_OK))
    _, decision, results = scored(session, resp.json()["call_id"])

    assert decision.status is GateStatus.QA_REVIEW and decision.check_library_id is None
    assert "no check library is active" in decision.reason
    assert results == []


def test_a_supplied_call_start_still_overrides_the_automatic_one(pipeline_client, session):
    """A late upload can say when the call really happened, and gets the version live back then."""
    now = datetime.now(timezone.utc)
    v1 = add_library(session, pipeline_client.retailer, 1, now - timedelta(days=30), now - timedelta(days=10), checks=[DISCLAIMER])
    add_library(session, pipeline_client.retailer, 2, now - timedelta(days=5), checks=[DISCLAIMER, DOB])
    session.commit()

    started = (now - timedelta(days=20)).isoformat()
    resp = post(pipeline_client, crm_fields=json.dumps(CRM_OK), call_started_at=started)
    call, decision, _ = scored(session, resp.json()["call_id"])

    assert call.call_started_at == datetime.fromisoformat(started)
    assert decision.check_library_id == v1.id and decision.check_library_version == 1
