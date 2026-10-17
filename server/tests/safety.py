"""Which database the tests may touch: TEST_DATABASE_URL, a throwaway one - never the dev database.

The suite changes data on purpose (corruption drills, un-approving a wallet, killed connections),
and tests/reset_db.py drops and recreates the database. So the URL must be given explicitly.
"""

import os
from pathlib import Path

from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

SERVER_DIR = Path(__file__).resolve().parent.parent
PROTECTED_NAMES = {"ttlab_dev", "postgres", "template0", "template1"}


class UnsafeDatabase(Exception):
    pass


def _target(url: str) -> tuple[str, str, str]:
    parts = conninfo_to_dict(url)
    return (str(parts.get("host", "localhost")), str(parts.get("port", "5432")), str(parts.get("dbname", "")))


def resolve_test_database_url() -> str:
    env_file = dotenv_values(SERVER_DIR / ".env")  # read only: puts nothing into os.environ
    url = os.environ.get("TEST_DATABASE_URL") or env_file.get("TEST_DATABASE_URL")
    if not url:
        raise UnsafeDatabase(
            "Set TEST_DATABASE_URL to a throwaway database (see server/.env.example); "
            "the tests drop and recreate it."
        )
    host, port, name = _target(url)
    if name in PROTECTED_NAMES:
        raise UnsafeDatabase(f"TEST_DATABASE_URL names the database {name!r}; use a separate one, e.g. ttlab_test.")
    for dev_url in (os.environ.get("DATABASE_URL"), env_file.get("DATABASE_URL")):
        if dev_url and _target(dev_url) == (host, port, name):
            raise UnsafeDatabase("TEST_DATABASE_URL points at the same database as DATABASE_URL; use a separate one.")
    return url
