import logging
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Configuration from environment variables (and an optional local .env file).

    DATABASE_URL is the only variable that must be set. Everything else has a default that works
    on Render (see backend/.env.example for the full list).
    """

    app_name: str = "QA Automation"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    # Required: there is no sensible default. Hosts such as Render hand out postgres:// or
    # postgresql:// URLs; they are rewritten to the psycopg (v3) driver we ship, see below.
    database_url: str

    # Browser origins allowed to call the API directly (the Vite dev server). Not needed when the frontend
    # goes through Vite's /api proxy; needed if VITE_API_BASE points at the backend. Override with a JSON
    # list in CORS_ORIGINS.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Where recordings / transcripts are kept. The defaults live in /tmp, which exists on Render but is
    # EPHEMERAL there: files are lost on every restart or deploy. Point AUDIO_STORAGE_PATH at a persistent
    # disk mount (e.g. /var/data/audio) if recordings must survive.
    audio_storage_path: str = "/tmp/audio"
    transcript_storage_path: str = "/tmp/transcripts"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @field_validator("database_url")
    @classmethod
    def _use_the_psycopg_driver(cls, value: str) -> str:
        """postgres:// and postgresql:// -> postgresql+psycopg://.

        SQLAlchemy 2 does not know the postgres:// scheme at all, and postgresql:// selects psycopg2,
        which is not installed. This only picks the driver the project already uses; anything that is
        not a plain postgres URL (including one that already names a driver) is left alone.
        """
        value = value.strip()
        if not value:
            raise ValueError("DATABASE_URL must not be empty")
        for scheme in ("postgres://", "postgresql://"):
            if value.startswith(scheme):
                return "postgresql+psycopg://" + value[len(scheme):]
        return value

    def ensure_storage_dirs(self) -> None:
        """Create the storage directories if they are missing. Called at startup so a bad path shows up in
        the logs at boot; a failure is logged, not fatal (the first upload will raise a precise error)."""
        for path in (self.audio_storage_path, self.transcript_storage_path):
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except OSError as error:
                logger.warning("Could not create storage directory %s: %s", path, error)


settings = Settings()
