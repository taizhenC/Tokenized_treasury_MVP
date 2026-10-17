"""Development server: restarts the API whenever a Python file in app/ or a .sql file
under db/ changes.

The app reads its SQL (db/queries/*.sql) once, when it starts, so a running server would
keep using the old statements after you edit a query file. Watching db/ too means every
saved .sql edit restarts the API with the new SQL (a broken block shows its error here).

uvicorn's own --reload restarts its worker with a console Ctrl+C event on Windows,
which never arrives without a console and can reach other processes sharing one
(Vite, concurrently). watchfiles instead stops just the server process it started.
"""

import uvicorn
from watchfiles import PythonFilter, run_process


def serve() -> None:
    uvicorn.run("app.main:app", port=8000)


if __name__ == "__main__":
    run_process("app", "db", target=serve, watch_filter=PythonFilter(extra_extensions=(".sql",)))
