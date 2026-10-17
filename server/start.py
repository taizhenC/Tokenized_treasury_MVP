"""Production entry point: the Docker image runs `python start.py`.

1. Apply the database migrations, plus the synthetic demo data when SEED_DEMO_DATA=true.
   The runner records every file it applies, so running it on every start is safe: files that
   already ran are skipped. A failed migration stops the start, so the API never serves a
   half-migrated schema.
2. Serve the API and the built React app on $PORT, which the host sets.
"""

import os
import subprocess
import sys

import uvicorn


def main() -> None:
    command = [sys.executable, "-m", "db.migrate"]
    if os.environ.get("SEED_DEMO_DATA", "").strip().lower() == "true":
        command.append("--seed")
    result = subprocess.run(command)
    if result.returncode != 0:
        sys.exit(result.returncode)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
