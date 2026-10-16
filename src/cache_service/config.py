"""Validated service configuration; no database connections at import time."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CACHE_", extra="ignore")

    database_path: Path = Path("data/cache.db")
    database_timeout: float = Field(default=30, gt=0, le=300)
