"""Central configuration loaded from environment variables.

All settings can be overridden via a ``.env`` file or process environment. The
defaults are deliberately offline-friendly: the fake model provider needs no API
key and Postgres/Redis are expected on the compose-mapped host ports.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for AgentMesh."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Data stores
    database_url: str = Field(
        default="postgresql+asyncpg://agentmesh:agentmesh@localhost:5433/agentmesh",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6380/0", alias="REDIS_URL")

    # LLM provider
    provider: str = Field(default="fake", alias="AGENTMESH_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    model: str = Field(default="gpt-4o-mini", alias="AGENTMESH_MODEL")
    fallback_model: str = Field(default="gpt-4o", alias="AGENTMESH_FALLBACK_MODEL")
    embedding_dim: int = Field(default=256, alias="AGENTMESH_EMBEDDING_DIM")

    # Runtime
    sandbox_root: str = Field(default="", alias="AGENTMESH_SANDBOX_ROOT")
    log_level: str = Field(default="INFO", alias="AGENTMESH_LOG_LEVEL")
    crash_at_node: str = Field(default="", alias="AGENTMESH_CRASH_AT_NODE")
    max_concurrency: int = Field(default=8, alias="AGENTMESH_MAX_CONCURRENCY")

    # API
    api_host: str = Field(default="0.0.0.0", alias="AGENTMESH_API_HOST")
    api_port: int = Field(default=8000, alias="AGENTMESH_API_PORT")
    cors_origins: str = Field(
        default="http://localhost:5173", alias="AGENTMESH_CORS_ORIGINS"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def use_fake_provider(self) -> bool:
        return self.provider.lower() in {"fake", "deterministic", "offline"}


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""

    return Settings()
