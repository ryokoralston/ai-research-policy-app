from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    tavily_api_key: str = ""
    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-3-large"

    # App login. When set, all /api routes require a bearer token obtained from
    # POST /api/auth/login. When empty, auth is disabled (local-dev convenience)
    # and a warning is logged at startup.
    app_password: str = ""
    session_ttl_hours: int = 12

    database_url: str = "sqlite:///./data/research.db"
    chroma_persist_dir: str = "./data/chroma"
    uploads_dir: str = "./data/uploads"

    claude_model: str = "claude-opus-4-6"
    claude_fast_model: str = "claude-haiku-4-5-20251001"
    max_search_results: int = 10
    cors_origins: str = "http://localhost:3000"

    # Daily digest email settings
    # Gmail app password: https://myaccount.google.com/apppasswords
    digest_email_to: str = ""
    digest_email_from: str = ""
    digest_smtp_password: str = ""
    digest_topics: str = "AI policy,AI regulation,AI governance,AI safety legislation"
    digest_timezone: str = "America/New_York"
    digest_hour: int = 5

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def digest_topics_list(self) -> list[str]:
        return [t.strip() for t in self.digest_topics.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
