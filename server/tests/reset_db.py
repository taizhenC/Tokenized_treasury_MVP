"""Drop and recreate the TEST database, then build it with the real runner: migrations, then seeds.

Run from server/ before the suite:
    uv run python -m tests.reset_db
    uv run pytest

Exactly what a fresh start looks like: an empty database, `python -m db.migrate` (migrations only),
`python -m db.migrate --seed` (adds the seeds), and once more to show it is now a no-op.
"""

import os
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.sql import load_sql_blocks
from tests.safety import SERVER_DIR, UnsafeDatabase, resolve_test_database_url

ADMIN = load_sql_blocks(SERVER_DIR / "tests" / "sql" / "admin.sql")


def main() -> None:
    try:
        url = resolve_test_database_url()
    except UnsafeDatabase as err:
        sys.exit(str(err))
    name = conninfo_to_dict(url)["dbname"]
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as conn:
        for block in ("drop_database", "create_database"):
            conn.execute(sql.SQL(ADMIN[block]).format(database=sql.Identifier(name)))
    print(f"recreated an empty database: {name}")

    env = {**os.environ, "DATABASE_URL": url}  # the runner reads DATABASE_URL; .env never overrides it
    for extra in ([], ["--seed"], ["--seed"]):
        print("$ python -m db.migrate", *extra, flush=True)
        subprocess.run([sys.executable, "-m", "db.migrate", *extra], cwd=SERVER_DIR, env=env, check=True)


if __name__ == "__main__":
    main()
