from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "CoursePilot Agent"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = (
        "postgresql+asyncpg://coursepilot:coursepilot@localhost:5432/learning_platform"
    )
    checkpoint_database_url: str = (
        "postgresql://coursepilot:coursepilot@localhost:5432/learning_platform"
    )

    agent_internal_secret: str = Field(min_length=32)
    agent_jwt_issuer: str = "learnhub-web"
    agent_jwt_audience: str = "coursepilot-agent"

    llm_api_key: str
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3:8b"
    llm_provider: Literal["ollama", "openai_compatible"] = "ollama"
    llm_context_size: int = Field(default=8192, ge=4096, le=32768)
    llm_startup_probe: bool = True
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_disable_thinking: bool = False
    llm_max_tokens: int = Field(default=4096, ge=64, le=8192)
    llm_timeout_seconds: float = Field(default=120, gt=0, le=120)
    max_tool_iterations: int = Field(default=4, ge=1, le=8)
    run_timeout_seconds: float = Field(default=360, gt=0, le=600)
    max_concurrent_runs: int = Field(default=1, ge=1, le=16)
    migration_database_url: str | None = None
    checkpoint_setup: bool = True

    uploads_dir: str = "/data/public"
    allowed_content_hosts: str = "amazonaws.com,digitaloceanspaces.com"
    max_pdf_bytes: int = Field(default=20_000_000, ge=1_000_000, le=50_000_000)
    max_pdf_pages: int = Field(default=300, ge=1, le=1000)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    retrieval_top_k: int = Field(default=6, ge=1, le=12)
    retrieval_min_score: float = Field(default=0.25, ge=-1, le=1)
    retrieval_mode: str = "hybrid"
    chunker_version: str = "words400-overlap60-v2"
    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("llm_base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
