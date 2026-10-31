import logging
import os
from pathlib import Path
from typing import List, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("acsa.config")

# Resolve .env files relative to this file, NOT the current working directory.
# backend/.env is listed LAST so it wins over a project-root .env
# (pydantic-settings gives later files in the tuple higher priority).
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILES = (str(BACKEND_DIR.parent / ".env"), str(BACKEND_DIR / ".env"))


class Settings(BaseSettings):
    # Groq LLM Configuration
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-20b", alias="GROQ_MODEL")
    # Max completion tokens per LLM call. gpt-oss is a reasoning model: reasoning
    # tokens count against this budget, so keep it generous to avoid truncation.
    # Used automatically if the main model's DAILY quota is exhausted (separate 200k/day on the free tier).
    # Set to empty to disable.
    groq_fallback_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_FALLBACK_MODEL")
    groq_max_tokens: int = Field(default=6144, alias="GROQ_MAX_TOKENS")
    # Prompt size cap per call. Groq's free tier allows 8000 tokens/minute for gpt-oss-20b and
    # rejects any single request above that with 413 "Request too large", so prompts are
    # trimmed (interface stubs instead of full files) to stay under this estimate.
    groq_input_token_budget: int = Field(default=6000, alias="GROQ_INPUT_TOKEN_BUDGET")
    # 429 retries per call (the Groq client honours retry-after, ~60s windows on the free tier)
    groq_max_retries: int = Field(default=8, alias="GROQ_MAX_RETRIES")
    groq_reasoning_effort: str = Field(default="low", alias="GROQ_REASONING_EFFORT")

    # Pipeline behaviour
    # If True, a Writer LLM failure produces a full template project (marked degraded)
    # instead of failing the run. Templates are never mixed with LLM files.
    allow_template_fallback: bool = Field(default=False, alias="ALLOW_TEMPLATE_FALLBACK")
    # LLM attempts per file when the output is truncated / does not parse
    writer_max_attempts: int = Field(default=3, alias="WRITER_MAX_ATTEMPTS")
    max_repair_attempts: int = Field(default=3, alias="MAX_REPAIR_ATTEMPTS")
    test_use_venv: bool = Field(default=True, alias="TEST_USE_VENV")
    test_install_timeout: int = Field(default=300, alias="TEST_INSTALL_TIMEOUT")
    test_timeout: int = Field(default=120, alias="TEST_TIMEOUT")

    # Server Configuration
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    debug: bool = Field(default=True, alias="DEBUG")

    # CORS Configuration
    cors_origins: Union[List[str], str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
        alias="CORS_ORIGINS",
    )

    # Storage Paths
    storage_dir: Path = Field(default=Path("./storage"), alias="STORAGE_DIR")

    @field_validator("groq_api_key", mode="before")
    @classmethod
    def clean_api_key(cls, v):
        # Tolerate stray whitespace/quotes copied into .env (e.g. GROQ_API_KEY="gsk_..." ).
        if isinstance(v, str):
            return v.strip().strip('"').strip("'").strip()
        return v

    @property
    def groq_api_key_source(self) -> str:
        """Where the key came from (never the key itself)."""
        if os.environ.get("GROQ_API_KEY"):
            return "shell environment variable GROQ_API_KEY (overrides any .env file)"
        for f in reversed(ENV_FILES):
            if Path(f).exists():
                return f".env file ({f})"
        return "not set"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def resolved_storage_dir(self) -> Path:
        if self.storage_dir.is_absolute():
            return self.storage_dir
        # Store in project root (outside backend/) so uvicorn --reload in backend/ never reloads on storage changes
        root_dir = Path(__file__).resolve().parent.parent.parent
        return (root_dir / self.storage_dir).resolve()

    @property
    def projects_dir(self) -> Path:
        path = self.resolved_storage_dir / "projects"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def zips_dir(self) -> Path:
        path = self.resolved_storage_dir / "zips"
        path.mkdir(parents=True, exist_ok=True)
        return path

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
