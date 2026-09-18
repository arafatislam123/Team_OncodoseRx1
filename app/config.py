"""Runtime settings, read from environment variables (and a local .env file if present)."""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so local runs don't need an extra dependency.
    Real environment variables always win over the file."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


@dataclass(frozen=True)
class Settings:
    primary: ProviderConfig
    backup: ProviderConfig
    llm_timeout_s: float
    request_deadline_s: float
    enable_degraded_fallback: bool
    cache_size: int
    log_level: str


def load_settings() -> Settings:
    return Settings(
        primary=ProviderConfig(
            name="primary",
            base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
        ),
        backup=ProviderConfig(
            name="backup",
            base_url=os.getenv("LLM_BACKUP_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("LLM_BACKUP_API_KEY", ""),
            model=os.getenv("LLM_BACKUP_MODEL", ""),
        ),
        llm_timeout_s=_float("LLM_TIMEOUT_S", 8.0),
        request_deadline_s=_float("REQUEST_DEADLINE_S", 20.0),
        enable_degraded_fallback=_bool("ENABLE_DEGRADED_FALLBACK", True),
        cache_size=_int("CACHE_SIZE", 512),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


settings = load_settings()
