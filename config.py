from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    google_api_key: str = ""
    database_url: str = "sqlite:///./clinic.db"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    backend_url: str = "http://localhost:8000"

    # LLM
    llm_model: str = "gemini-2.5-flash-lite"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096

    # Rate limiting (Gemini free tier: 60 RPM)
    llm_rate_limit_rpm: int = 60

    # Scheduling
    slot_duration_minutes: int = 30
    clinic_timezone: str = "UTC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
