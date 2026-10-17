"""Login-session settings, read from the environment (server/.env in development).

SESSION_SECRET          signs the session cookie. At least 32 random characters. Generate one:
                          uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
SESSION_COOKIE_SECURE   "true" (the default) sends the cookie over HTTPS only. Set it to
                        "false" in server/.env for local development over plain http.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MIN_SECRET_LENGTH = 32


@dataclass(frozen=True)
class Settings:
    session_secret: str
    session_cookie_secure: bool
    session_max_age_seconds: int = 8 * 60 * 60  # one working day; then log in again


def load_settings() -> Settings:
    secret = os.environ.get("SESSION_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"SESSION_SECRET must be set to at least {MIN_SECRET_LENGTH} random characters "
            "(see server/.env.example)."
        )
    # Secure by default: only an explicit "false" allows the cookie over plain http.
    secure = os.environ.get("SESSION_COOKIE_SECURE", "true").strip().lower() not in {"false", "0", "no"}
    return Settings(session_secret=secret, session_cookie_secure=secure)
