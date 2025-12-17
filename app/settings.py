from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/app.db",
        description="SQLAlchemy URL. Use Postgres for horizontal scaling.",
        alias="DATABASE_URL",
    )

    worker_poll_seconds: int = Field(default=30, alias="WORKER_POLL_SECONDS")
    worker_batch_size: int = Field(default=200, alias="WORKER_BATCH_SIZE")
    worker_lock_lease_seconds: int = Field(default=120, alias="WORKER_LOCK_LEASE_SECONDS")
    max_concurrent_runs: int = Field(default=100, alias="MAX_CONCURRENT_RUNS")

    puzzlebot_base_url: str = Field(default="https://api.puzzlebot.top/", alias="PUZZLEBOT_BASE_URL")
    http_timeout_seconds: int = Field(default=20, alias="HTTP_TIMEOUT_SECONDS")
    http_retries: int = Field(default=2, alias="HTTP_RETRIES")


settings = Settings()
