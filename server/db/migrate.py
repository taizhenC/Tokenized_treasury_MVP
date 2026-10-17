"""Apply your SQL files to the database, each file exactly once.

Order: db/migrations/*.sql, then (only with --seed) db/seeds/*.sql, each folder in
filename order. Run it from the project root:
    npm run db:migrate    migrations only (safe for the deployed database)
    npm run db:seed       migrations, then local demo data
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from app.sql import load_sql_blocks

SERVER_DIR = Path(__file__).resolve().parent.parent
DB_DIR = SERVER_DIR / "db"
BOOKKEEPING_FILE = DB_DIR / "runner-bookkeeping.sql"
BOOKKEEPING_BLOCKS = ("create_schema_migrations", "list_applied_files", "record_applied_file")


def load_bookkeeping() -> dict[str, str]:
    """Read the three statements you write in Lesson 1, or say which are missing."""
    if not BOOKKEEPING_FILE.exists():
        sys.exit("Lesson 1: create server/db/runner-bookkeeping.sql first.")
    blocks = load_sql_blocks(BOOKKEEPING_FILE)
    missing = [name for name in BOOKKEEPING_BLOCKS if not blocks.get(name)]
    if missing:
        sys.exit(f"Lesson 1: write the SQL for {', '.join(missing)} in server/db/runner-bookkeeping.sql.")
    return blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply SQL migrations (and optionally seeds).")
    parser.add_argument("--seed", action="store_true", help="also apply db/seeds/*.sql (local demo data only)")
    args = parser.parse_args()

    sql = load_bookkeeping()
    load_dotenv(SERVER_DIR / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL is not set. Copy server/.env.example to server/.env.")

    # Bookkeeping names include the folder, e.g. "migrations/001_x.sql", so a
    # migration and a seed file with the same number cannot collide.
    folders = ["migrations", "seeds"] if args.seed else ["migrations"]
    files = [f"{folder}/{path.name}" for folder in folders for path in sorted((DB_DIR / folder).glob("*.sql"))]

    # autocommit=True: nothing runs inside a transaction unless we open one below.
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql["create_schema_migrations"])
        applied = {row[0] for row in conn.execute(sql["list_applied_files"])}

        pending = [name for name in files if name not in applied]
        if not pending:
            print("Database is up to date.")
            return

        for name in pending:
            file_sql = (DB_DIR / name).read_text(encoding="utf-8-sig")
            try:
                # One transaction per file: the file's changes and its bookkeeping
                # row are saved together, or (on any error) neither is saved.
                with conn.transaction():
                    conn.execute(file_sql)
                    conn.execute(sql["record_applied_file"], {"filename": name})
            except psycopg.Error as err:
                sys.exit(f"{name} failed and was rolled back: {err}")
            print(f"applied {name}")


if __name__ == "__main__":
    main()
