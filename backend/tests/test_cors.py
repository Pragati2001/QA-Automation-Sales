"""The Vite dev origins may call the API directly (only needed when VITE_API_BASE is an absolute URL)."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)
ALLOWED = "http://localhost:5173"


def test_the_dev_origins_are_the_configured_defaults():
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_a_dev_origin_gets_cors_headers_on_a_normal_request(origin):
    resp = client.get("/health", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


def test_the_upload_preflight_is_allowed_for_a_dev_origin():
    resp = client.options(
        "/api/v1/calls",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ALLOWED
    assert "POST" in resp.headers["access-control-allow-methods"]


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:9999", "http://localhost:5173.evil.example"])
def test_other_origins_get_no_cors_headers(origin):
    resp = client.get("/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in resp.headers


def test_methods_the_api_does_not_use_are_not_advertised():
    resp = client.options("/api/v1/calls", headers={"Origin": ALLOWED, "Access-Control-Request-Method": "DELETE"})
    assert resp.status_code == 400  # the preflight is refused


def test_requests_without_an_origin_are_unaffected():
    resp = client.get("/health")
    assert resp.status_code == 200 and "access-control-allow-origin" not in resp.headers
