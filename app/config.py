from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


@dataclass
class Settings:
    app_name: str
    base_dir: Path
    database_path: Path
    profiles_dir: Path
    sessions_dir: Path
    templates_dir: Path
    static_dir: Path
    secret_key: str
    admin_username: str
    admin_password: str
    default_http_concurrency: int
    default_browser_concurrency: int
    default_max_retries: int
    default_request_delay_ms: int

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    app_name = os.getenv("APP_NAME", "Redeem Checker")
    database_path = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "app.db")))
    profiles_dir = Path(os.getenv("PROFILES_DIR", str(BASE_DIR / "profiles")))
    sessions_dir = Path(os.getenv("SESSIONS_DIR", str(BASE_DIR / "sessions")))
    templates_dir = Path(os.getenv("TEMPLATES_DIR", str(BASE_DIR / "templates")))
    static_dir = Path(os.getenv("STATIC_DIR", str(BASE_DIR / "static")))

    return Settings(
        app_name=app_name,
        base_dir=BASE_DIR,
        database_path=database_path,
        profiles_dir=profiles_dir,
        sessions_dir=sessions_dir,
        templates_dir=templates_dir,
        static_dir=static_dir,
        secret_key=os.getenv("SECRET_KEY", "change-this-in-production"),
        admin_username=os.getenv("ADMIN_USERNAME", "admin"),
        admin_password=os.getenv("ADMIN_PASSWORD", "change-me-now"),
        default_http_concurrency=_env_int("DEFAULT_HTTP_CONCURRENCY", 20, 1),
        default_browser_concurrency=_env_int("DEFAULT_BROWSER_CONCURRENCY", 1, 0),
        default_max_retries=_env_int("DEFAULT_MAX_RETRIES", 2, 0),
        default_request_delay_ms=_env_int("DEFAULT_REQUEST_DELAY_MS", 100, 0),
    )
