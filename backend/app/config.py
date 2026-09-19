from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "QA Automation"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str

    # Browser origins allowed to call the API directly (the Vite dev server). Not needed when the frontend
    # goes through Vite's /api proxy; needed if VITE_API_BASE points at the backend. Override with a JSON
    # list in CORS_ORIGINS.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    audio_storage_path: str
    transcript_storage_path: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
