"""Render-compatible configuration: DATABASE_URL required, everything else defaulted, directories created."""

import logging
import re
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, settings
from app.main import app

BACKEND = Path(__file__).resolve().parents[1]
ENV_VARS = (
    "DATABASE_URL", "APP_NAME", "APP_ENV", "API_PREFIX", "CORS_ORIGINS",
    "AUDIO_STORAGE_PATH", "TRANSCRIPT_STORAGE_PATH",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Each test starts from an empty environment, like a fresh Render service."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_settings(**env) -> Settings:
    return Settings(_env_file=None, **env)  # _env_file=None: ignore the developer's own .env


# --- what the deploy needs ------------------------------------------------------------------------

def test_database_url_alone_is_enough_to_start(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/qa")
    s = make_settings()

    assert s.database_url == "postgresql+psycopg://u:p@db:5432/qa"
    assert s.audio_storage_path == "/tmp/audio"
    assert s.transcript_storage_path == "/tmp/transcripts"
    assert (s.app_name, s.app_env, s.api_prefix) == ("QA Automation", "development", "/api/v1")


def test_database_url_is_still_required():
    with pytest.raises(ValidationError) as error:
        make_settings()
    missing = {e["loc"][0] for e in error.value.errors() if e["type"] == "missing"}
    assert missing == {"database_url"}  # the storage paths are no longer "missing"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_database_url_is_rejected(monkeypatch, blank):
    monkeypatch.setenv("DATABASE_URL", blank)
    with pytest.raises(ValidationError, match="DATABASE_URL must not be empty"):
        make_settings()


def test_the_storage_paths_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("AUDIO_STORAGE_PATH", "/var/data/audio")
    monkeypatch.setenv("TRANSCRIPT_STORAGE_PATH", "/var/data/transcripts")
    s = make_settings()
    assert (s.audio_storage_path, s.transcript_storage_path) == ("/var/data/audio", "/var/data/transcripts")


def test_cors_origins_can_be_set_as_a_json_list(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("CORS_ORIGINS", '["https://app.example.com"]')
    assert make_settings().cors_origins == ["https://app.example.com"]


# --- the URL form hosts hand out --------------------------------------------------------------------

@pytest.mark.parametrize("given, expected", [
    ("postgres://u:p@dpg-abc.oregon-postgres.render.com/qa", "postgresql+psycopg://u:p@dpg-abc.oregon-postgres.render.com/qa"),
    ("postgresql://u:p@dpg-abc/qa", "postgresql+psycopg://u:p@dpg-abc/qa"),
    ("postgresql://u:p@h:5432/qa?sslmode=require", "postgresql+psycopg://u:p@h:5432/qa?sslmode=require"),
    ("  postgres://u:p@h/qa \n", "postgresql+psycopg://u:p@h/qa"),
    ("postgresql+psycopg://u:p@h/qa", "postgresql+psycopg://u:p@h/qa"),  # already right: untouched
    ("postgresql+asyncpg://u:p@h/qa", "postgresql+asyncpg://u:p@h/qa"),  # an explicit driver is respected
    ("sqlite:///x.db", "sqlite:///x.db"),
])
def test_render_style_database_urls_are_rewritten_to_the_psycopg_driver(monkeypatch, given, expected):
    monkeypatch.setenv("DATABASE_URL", given)
    assert make_settings().database_url == expected


def test_the_rewritten_url_is_one_sqlalchemy_can_build_an_engine_for(monkeypatch):
    from sqlalchemy import create_engine

    for scheme in ("postgres", "postgresql"):
        monkeypatch.setenv("DATABASE_URL", f"{scheme}://u:p@localhost:5432/qa")
        engine = create_engine(make_settings().database_url)  # the raw schemes raise NoSuchModuleError / psycopg2
        assert engine.dialect.driver == "psycopg"


# --- directories ---------------------------------------------------------------------------------------

def test_ensure_storage_dirs_creates_missing_nested_directories(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    s = make_settings(audio_storage_path=str(tmp_path / "a" / "b" / "audio"), transcript_storage_path=str(tmp_path / "t" / "transcripts"))

    s.ensure_storage_dirs()

    assert (tmp_path / "a" / "b" / "audio").is_dir() and (tmp_path / "t" / "transcripts").is_dir()


def test_ensure_storage_dirs_is_idempotent_and_leaves_existing_files_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    audio = tmp_path / "audio"
    (audio / "raw" / "7").mkdir(parents=True)
    (audio / "raw" / "7" / "call.wav").write_bytes(b"keep me")
    s = make_settings(audio_storage_path=str(audio), transcript_storage_path=str(tmp_path / "t"))

    s.ensure_storage_dirs()
    s.ensure_storage_dirs()

    assert (audio / "raw" / "7" / "call.wav").read_bytes() == b"keep me"


def test_a_directory_that_cannot_be_created_is_logged_not_fatal(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    s = make_settings(audio_storage_path=str(blocker / "audio"), transcript_storage_path=str(tmp_path / "ok"))

    with caplog.at_level(logging.WARNING, logger="app.config"):
        s.ensure_storage_dirs()  # must not raise

    assert "Could not create storage directory" in caplog.text
    assert (tmp_path / "ok").is_dir()  # the other directory was still created


def test_the_app_creates_the_directories_at_startup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "audio_storage_path", str(tmp_path / "audio"))
    monkeypatch.setattr(settings, "transcript_storage_path", str(tmp_path / "transcripts"))
    assert not (tmp_path / "audio").exists()

    with TestClient(app) as client:  # entering the context runs the app's startup
        assert client.get("/health").status_code == 200

    assert (tmp_path / "audio").is_dir() and (tmp_path / "transcripts").is_dir()


def test_the_audio_storage_creates_its_own_directory_on_first_use(monkeypatch, tmp_path):
    from app.services.storage.audio_storage import LocalAudioStorage

    monkeypatch.setattr(settings, "audio_storage_path", str(tmp_path / "fresh" / "audio"))
    reference = LocalAudioStorage().save("1", "a.wav", b"bytes")  # nothing existed beforehand
    assert Path(reference).read_bytes() == b"bytes"


# --- Alembic / database wiring --------------------------------------------------------------------------

def test_alembic_env_escapes_percent_signs_in_the_url():
    """A percent-encoded password ("%40") would otherwise raise 'invalid interpolation syntax'."""
    source = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    assert 'settings.database_url.replace("%", "%%")' in source

    url = "postgresql+psycopg://user:p%40ss@host:5432/db"
    config = Config()
    with pytest.raises(ValueError, match="interpolation"):
        config.set_main_option("sqlalchemy.url", url)  # what happens without the escape
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url  # and the value survives the round trip


def test_alembic_takes_the_url_from_settings_not_from_alembic_ini():
    ini = (BACKEND / "alembic.ini").read_text(encoding="utf-8")
    env = (BACKEND / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "driver://user:pass@localhost/dbname" in ini  # a placeholder only
    assert "from app.config import settings" in env and 'set_main_option("sqlalchemy.url"' in env


def test_the_database_engine_uses_the_configured_url():
    from app.db import engine

    assert engine.url.render_as_string(hide_password=False) == settings.database_url


# --- documentation -----------------------------------------------------------------------------------------

def test_env_example_documents_every_setting_and_holds_no_real_secrets():
    text = (BACKEND / ".env.example").read_text(encoding="utf-8")
    for field in Settings.model_fields:
        assert re.search(rf"^#?\s*{field.upper()}=", text, re.MULTILINE), f"{field.upper()} is not documented in .env.example"

    database_url = re.search(r"^DATABASE_URL=(.*)$", text, re.MULTILINE).group(1)
    assert database_url.startswith("postgresql+psycopg://") and "localhost" in database_url  # a local placeholder
    assert not re.search(r"dpg-[a-z0-9]+|onrender\.com/[a-z_]+", text)  # no real Render hostnames
