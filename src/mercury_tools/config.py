"""Runtime configuration for Mercury Tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIM = 1536


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_role_key: str
    openai_api_key: str
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    mercury_agent_path: Path | None = None
    mercury_home: Path | None = None

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)


def load_settings(*, dotenv_path: str | Path | None = None) -> Settings:
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    embedding_dim_raw = os.environ.get("MERCURY_TOOLS_EMBEDDING_DIM", str(DEFAULT_EMBEDDING_DIM))
    try:
        embedding_dim = int(embedding_dim_raw)
    except ValueError:
        embedding_dim = DEFAULT_EMBEDDING_DIM

    mercury_agent = os.environ.get("MERCURY_AGENT_PATH", "").strip()
    mercury_home = os.environ.get("MERCURY_HOME", "").strip()
    return Settings(
        supabase_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        embedding_model=os.environ.get("MERCURY_TOOLS_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        embedding_dim=embedding_dim,
        mercury_agent_path=Path(mercury_agent).expanduser() if mercury_agent else None,
        mercury_home=Path(mercury_home).expanduser() if mercury_home else None,
    )


def require_supabase(settings: Settings) -> None:
    if not settings.supabase_configured:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")

