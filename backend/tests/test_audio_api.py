import pytest
from fastapi.testclient import TestClient

from app.api.audio import _media_type, _parse_range
from app.db import get_db
from app.main import app
from app.models import Recording
from app.repositories.base import BaseRepository
from app.services.storage.audio_storage import LocalAudioStorage, get_audio_storage
from tests.services.scenario import make_call, make_retailer

DATA = bytes(range(256)) * 4  # 1024 bytes, every value recognisable by position


@pytest.fixture
def storage(tmp_path):
    return LocalAudioStorage(str(tmp_path))


@pytest.fixture
def client(session, storage):
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_audio_storage] = lambda: storage
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def call_with_audio(session, storage):
    def make(filename="call.wav", content_type="audio/wav", data=DATA):
        call = make_call(session, make_retailer(session, f"audio_retailer_{filename}"))
        reference = storage.save(str(call.id), filename, data)
        BaseRepository(Recording, session).create(
            call_id=call.id, storage_reference=reference, original_filename=filename,
            content_type=content_type, size_bytes=len(data),
        )
        session.commit()
        return call

    return make


def get(client, call_id, range_header=None):
    return client.get(f"/api/v1/calls/{call_id}/audio", headers={"Range": range_header} if range_header else {})


# --- full file ----------------------------------------------------------------------

def test_serves_the_whole_recording_and_advertises_range_support(client, call_with_audio):
    call = call_with_audio()
    resp = get(client, call.id)

    assert resp.status_code == 200
    assert resp.content == DATA
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-length"] == str(len(DATA))
    assert resp.headers["cache-control"] == "private"
    assert "call.wav" in resp.headers["content-disposition"] and resp.headers["content-disposition"].startswith("inline")


# --- range / seek ----------------------------------------------------------------------

@pytest.mark.parametrize("header, first, last", [
    ("bytes=0-99", 0, 99),
    ("bytes=100-199", 100, 199),
    ("bytes=1000-", 1000, 1023),       # open-ended: to the end
    ("bytes=-24", 1000, 1023),         # suffix: the last 24 bytes
    ("bytes=1020-5000", 1020, 1023),   # end past the file is clamped
    ("bytes=0-0", 0, 0),
    ("bytes=-5000", 0, 1023),          # suffix longer than the file: all of it
    ("bytes = 10-19", 10, 19),
])
def test_range_requests_return_206_with_exactly_the_requested_bytes(client, call_with_audio, header, first, last):
    call = call_with_audio()
    resp = get(client, call.id, header)

    assert resp.status_code == 206
    assert resp.content == DATA[first : last + 1]
    assert resp.headers["content-range"] == f"bytes {first}-{last}/{len(DATA)}"
    assert resp.headers["content-length"] == str(last - first + 1)
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-type"] == "audio/wav"


def test_a_range_starting_past_the_end_is_416_and_reports_the_size(client, call_with_audio):
    call = call_with_audio()
    resp = get(client, call.id, "bytes=1024-")
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{len(DATA)}"


def test_a_zero_length_suffix_is_416(client, call_with_audio):
    assert get(client, call_with_audio().id, "bytes=-0").status_code == 416


@pytest.mark.parametrize("header", ["bytes=abc", "bytes=5-2", "bytes=-", "bytes=", "items=0-10", "bytes=1-2-3", "garbage"])
def test_a_malformed_range_is_400(client, call_with_audio, header):
    resp = get(client, call_with_audio().id, header)
    assert resp.status_code == 400, header


def test_multiple_ranges_are_not_supported_and_get_the_whole_file(client, call_with_audio):
    resp = get(client, call_with_audio().id, "bytes=0-9,20-29")
    assert resp.status_code == 200 and resp.content == DATA


def test_successive_seeks_each_get_their_own_slice(client, call_with_audio):
    """What the <audio> element does when the user clicks evidence at different timestamps."""
    call = call_with_audio()
    for first in (0, 512, 900, 256):
        resp = get(client, call.id, f"bytes={first}-")
        assert resp.status_code == 206 and resp.content == DATA[first:]


# --- errors ------------------------------------------------------------------------------

def test_unknown_call_is_404(client):
    resp = client.get("/api/v1/calls/999999999/audio")
    assert resp.status_code == 404 and "not found" in resp.json()["detail"]


def test_a_call_without_a_recording_is_404(client, session):
    call = make_call(session, make_retailer(session, "audio_no_recording"))
    resp = get(client, call.id)
    assert resp.status_code == 404 and "no recording" in resp.json()["detail"]


def test_a_recording_missing_from_storage_is_404_not_a_server_error(client, call_with_audio, storage):
    call = call_with_audio()
    storage.delete(call.recording.storage_reference)
    resp = get(client, call.id)
    assert resp.status_code == 404 and "missing from storage" in resp.json()["detail"]
    assert "tmp" not in resp.json()["detail"].lower()  # the internal path is not leaked


def test_a_non_numeric_call_id_is_rejected(client):
    assert client.get("/api/v1/calls/abc/audio").status_code == 422


def test_an_empty_file_can_be_served_but_not_ranged(client, call_with_audio):
    call = call_with_audio(data=b"")
    assert get(client, call.id).status_code == 200
    assert get(client, call.id, "bytes=0-").status_code == 416


# --- content type ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename, stored, expected", [
    ("call.mp3", "audio/mpeg", {"audio/mpeg"}),
    ("call.mp3", "application/octet-stream", {"audio/mpeg"}),  # a generic upload type: go by the file name
    ("call.wav", None, {"audio/x-wav", "audio/wav", "audio/wave"}),  # the name of the type varies by platform
    ("call.bin", None, {"application/octet-stream"}),
    ("call.bin", "application/x-thing", {"application/x-thing"}),
])
def test_media_type_prefers_an_audio_upload_type_else_the_file_name(filename, stored, expected):
    assert _media_type(Recording(original_filename=filename, content_type=stored)) in expected


def test_the_response_uses_the_filename_type_for_generic_uploads(client, call_with_audio):
    call = call_with_audio(filename="call.mp3", content_type="application/octet-stream")
    assert get(client, call.id).headers["content-type"] == "audio/mpeg"


# --- the parser on its own ---------------------------------------------------------------------

def test_parse_range_returns_none_for_multiple_ranges():
    assert _parse_range("bytes=0-1,5-6", 100) is None
