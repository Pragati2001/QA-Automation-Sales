import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_db
from app.main import app
from app.models import Call, CallStatus, Lead, Recording, Retailer
from app.repositories.base import BaseRepository
from app.services.calls import processing_service
from app.services.storage.audio_storage import LocalAudioStorage, get_audio_storage

AUDIO = b"RIFF....WAVEfmt fake-but-non-empty-audio-bytes"
CRM = {"email": "j.smith@gmial.com", "dob": "1985-04-12", "plan_peak_rate": "31.9c/kWh"}


@pytest.fixture
def processed(monkeypatch):
    """Records process_call invocations instead of running the (unwired) pipeline."""
    calls: list[str] = []
    monkeypatch.setattr(processing_service, "process_call", calls.append)
    return calls


@pytest.fixture
def storage_dir(tmp_path):
    return tmp_path


@pytest.fixture
def client(session, storage_dir, processed):
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_audio_storage] = lambda: LocalAudioStorage(str(storage_dir))
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def retailer(session):
    repo = BaseRepository(Retailer, session)
    r = repo.create(code="calls_test_retailer", name="Calls Test")
    repo.commit()  # so a rollback inside the route can't discard the retailer
    return r


def post_call(client, *, lead_id="3613790", crm=CRM, audio=AUDIO, filename="call.wav", code="calls_test_retailer"):
    return client.post(
        "/api/v1/calls",
        data={"retailer_code": code, "external_lead_id": lead_id, "crm_fields": json.dumps(crm)},
        files={"audio": (filename, audio, "audio/wav")},
    )


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_post_creates_lead_call_recording_and_schedules_processing(
    client, session, retailer, processed, storage_dir
):
    resp = post_call(client)

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "PROCESSING"
    call_id = body["call_id"]

    call = session.get(Call, call_id)
    assert call.status is CallStatus.PROCESSING
    assert (call.lead.external_lead_id, call.lead.retailer_id) == ("3613790", retailer.id)
    assert call.lead.crm_fields == CRM
    assert call.call_started_at is not None and call.call_started_at.tzinfo is not None  # set automatically
    rec = call.recording
    assert (rec.original_filename, rec.content_type, rec.size_bytes) == ("call.wav", "audio/wav", len(AUDIO))
    assert Path(rec.storage_reference).read_bytes() == AUDIO
    assert Path(rec.storage_reference).parent == storage_dir / "raw" / str(call_id)
    assert processed == [str(call_id)]


def test_get_call(client, retailer):
    call_id = post_call(client).json()["call_id"]
    resp = client.get(f"/api/v1/calls/{call_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["id"], body["status"]) == (call_id, "PROCESSING")
    assert (body["retailer_code"], body["external_lead_id"]) == ("calls_test_retailer", "3613790")
    assert body["recording"] == {"original_filename": "call.wav", "content_type": "audio/wav", "size_bytes": len(AUDIO)}
    assert "crm_fields" not in body and "storage_reference" not in json.dumps(body)


def test_get_unknown_call_is_404(client):
    assert client.get("/api/v1/calls/999999999").status_code == 404


def test_same_lead_is_reused_and_snapshot_refreshed(client, session, retailer):
    first = post_call(client).json()["call_id"]
    leads_before = count(session, Lead)
    second = post_call(client, crm={"email": "fixed@example.com"}).json()["call_id"]

    assert second != first
    assert count(session, Lead) == leads_before
    lead = session.get(Call, second).lead
    assert session.get(Call, first).lead_id == lead.id
    assert lead.crm_fields == {"email": "fixed@example.com"}


def test_empty_crm_snapshot_does_not_wipe_existing(client, session, retailer):
    post_call(client)
    call_id = post_call(client, crm={}).json()["call_id"]
    assert session.get(Call, call_id).lead.crm_fields == CRM


def test_same_external_lead_id_under_another_retailer_is_a_different_lead(client, session, retailer):
    other = BaseRepository(Retailer, session).create(code="calls_test_other", name="Other")
    a = post_call(client).json()["call_id"]
    b = post_call(client, code="calls_test_other").json()["call_id"]
    assert session.get(Call, a).lead_id != session.get(Call, b).lead_id
    assert session.get(Call, b).lead.retailer_id == other.id


def test_unknown_retailer_is_404_and_writes_nothing(client, session, processed):
    before = [count(session, m) for m in (Lead, Call, Recording)]
    resp = post_call(client, code="does_not_exist")
    assert resp.status_code == 404
    assert [count(session, m) for m in (Lead, Call, Recording)] == before
    assert processed == []


@pytest.mark.parametrize("bad", ["not json", "[1, 2]", '"str"'])
def test_invalid_crm_fields_is_422(client, retailer, processed, bad):
    resp = client.post(
        "/api/v1/calls",
        data={"retailer_code": "calls_test_retailer", "external_lead_id": "1", "crm_fields": bad},
        files={"audio": ("a.wav", AUDIO, "audio/wav")},
    )
    assert resp.status_code == 422
    assert processed == []


def test_empty_audio_is_422(client, retailer, processed):
    assert post_call(client, audio=b"").status_code == 422
    assert processed == []


def test_missing_audio_is_422(client, retailer):
    resp = client.post("/api/v1/calls", data={"retailer_code": "calls_test_retailer", "external_lead_id": "1"})
    assert resp.status_code == 422


def test_malicious_filename_cannot_escape_the_call_directory(client, session, retailer, storage_dir):
    call_id = post_call(client, filename="../../evil.wav").json()["call_id"]
    rec = session.get(Call, call_id).recording
    assert rec.original_filename == "evil.wav"
    assert Path(rec.storage_reference).parent == storage_dir / "raw" / str(call_id)
    assert not (storage_dir / "evil.wav").exists()


def test_storage_failure_persists_nothing_and_schedules_nothing(session, retailer, processed):
    class BrokenStorage(LocalAudioStorage):
        def save(self, call_id, filename, content):
            raise OSError("disk full")

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_audio_storage] = lambda: BrokenStorage.__new__(BrokenStorage)
    before = [count(session, m) for m in (Lead, Call, Recording)]
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            assert post_call(c).status_code == 500
    finally:
        app.dependency_overrides.clear()

    assert [count(session, m) for m in (Lead, Call, Recording)] == before
    assert processed == []
