"""Load named SQL statements from a .sql file.

A file holds blocks like this, each with exactly one statement:

    -- name: list_applied_files
    SELECT ...;

load_sql_blocks() returns {"list_applied_files": "SELECT ...;"}. Full-line "--"
comments are dropped, so notes inside a block never reach the database.
"""

import re
from pathlib import Path

_NAME_LINE = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)


def load_sql_blocks(path: Path) -> dict[str, str]:
    parts = _NAME_LINE.split(path.read_text(encoding="utf-8-sig"))
    # parts = [text before the first name line, name1, body1, name2, body2, ...]
    blocks = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        if name in blocks:
            raise ValueError(f"{path.name}: block '{name}' appears twice")
        lines = [line for line in body.splitlines() if not line.lstrip().startswith("--")]
        blocks[name] = "\n".join(lines).strip()
    return blocks
