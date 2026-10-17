"""Try named SQL blocks with named parameters, print what they return, then ROLL BACK.

psql cannot fill %(name)s placeholders, so use this to test a statement before the app does.
Run it from the project root:

    uv run --directory server python -m db.try_block queries/holdings.sql holdings_for_account account_id=101

Several blocks (comma-separated) run in order inside ONE transaction, all with the same
parameters - the same way the app runs them:

    uv run --directory server python -m db.try_block queries/transfer.sql reserve_idempotency_key,lock_positions actor_user_id=11 source_account_id=101 destination_account_id=102 asset_id=1 quantity=5 idempotency_key=try-1 request_hash=h

(One line: it works the same in PowerShell and in bash.)

Value types, the same as the app sends them: quantity and every name ending in _id are sent as
integers; every other name is sent as text, even when it looks like a number (idempotency_key=2024
is the text '2024'); the word null is sent as NULL. Quote a value that contains spaces.
Nothing is ever saved: the transaction is always rolled back at the end.
--hold keeps the transaction (and its row locks) open until you press Enter; run the same
lock_position command in a second terminal and watch it wait. While you hold a lock, a running
dev server's requests that need the same row give up after 5 s (lock_timeout) with a 503.
"""  # noqa: E501 (the example command is one line on purpose)

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from app.sql import load_sql_blocks

SERVER_DIR = Path(__file__).resolve().parent.parent
DB_DIR = SERVER_DIR / "db"


def is_integer_name(name: str) -> bool:
    """The app sends these as Python ints (so "-%(quantity)s" works); everything else is text."""
    return name == "quantity" or name.endswith("_id")


def parse_value(name: str, text: str) -> int | str | None:
    if text == "null":
        return None
    if is_integer_name(name):
        try:
            return int(text)
        except ValueError:
            return text  # sent as text: Postgres reports the bad value loudly
    return text


def print_result(name: str, cur: psycopg.Cursor) -> None:
    print(f"-- {name}")
    if cur.description is None:
        print(cur.statusmessage)
        return
    columns = [column.name for column in cur.description]
    rows = [["(null)" if value is None else str(value) for value in row] for row in cur.fetchall()]
    widths = [max(len(text) for text in [column, *(row[i] for row in rows)]) for i, column in enumerate(columns)]
    print(" | ".join(column.ljust(width) for column, width in zip(columns, widths, strict=True)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)))
    print(f"({len(rows)} row{'' if len(rows) == 1 else 's'})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run named SQL blocks, print the result, roll back.")
    parser.add_argument("file", help="path under server/db, e.g. queries/holdings.sql")
    parser.add_argument("blocks", help="block name, or several separated by commas")
    parser.add_argument("params", nargs="*", help="name=value pairs")
    parser.add_argument("--hold", action="store_true", help="keep the transaction open until Enter")
    args = parser.parse_args(argv)

    blocks = load_sql_blocks(DB_DIR / args.file)
    names = args.blocks.split(",")
    unknown = [name for name in names if name not in blocks]
    if unknown:
        sys.exit(f"no block named {', '.join(unknown)} in {args.file} (it has: {', '.join(blocks)})")
    empty = [name for name in names if not blocks[name]]
    if empty:
        sys.exit(f"{', '.join(empty)} in {args.file} has no SQL yet: write it below its -- name: line.")
    params = {}
    for pair in args.params:
        name, sep, value = pair.partition("=")
        if not sep:
            sys.exit(f"parameter {pair!r} must look like name=value")
        params[name] = parse_value(name, value)

    load_dotenv(SERVER_DIR / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL is not set. Copy server/.env.example to server/.env.")

    with psycopg.connect(database_url) as conn:
        try:
            with conn.transaction():
                for name in names:
                    # A block without placeholders is sent as-is (no parameter parsing at all).
                    cur = conn.execute(blocks[name], params if "%(" in blocks[name] else None)
                    print_result(name, cur)
                if args.hold:
                    input("Transaction open, locks held. Press Enter to roll back... ")
                raise psycopg.Rollback()
        except psycopg.Error as err:
            code = f" {err.sqlstate}" if err.sqlstate else ""  # e.g. ERROR 23514: ... (none for client-side errors)
            sys.exit(f"ERROR{code}: {err}\nRolled back: nothing was saved.")
    print("Rolled back: nothing was saved.")


if __name__ == "__main__":
    main()
