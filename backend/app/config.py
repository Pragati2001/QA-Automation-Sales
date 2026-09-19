from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "QA Automation"
    app_env: str = "development"
    api_prefix: str = "/api/v1"

    database_url: str

    audio_storage_path: str
    transcript_storage_path: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
