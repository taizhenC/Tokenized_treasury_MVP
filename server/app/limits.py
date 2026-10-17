"""Refuse request bodies over a small size limit (413) before anything reads or parses them.

FastAPI reads and parses the whole JSON body before it runs the login check, so without a limit
one anonymous request could make the server hold and parse megabytes (measured: one 100 MB body
took the server from 68 MB to 1.25 GB of memory). Every body this API accepts is under 1 kB, so the 16 KiB limit never gets in the
way of a real request.
"""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 16 * 1024
TOO_LARGE = "request body too large"


class BodySizeLimit:
    """Pure ASGI middleware: checks Content-Length first, then counts streamed (chunked) bytes."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await JSONResponse({"error": TOO_LARGE}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Raised while the route reads the body; FastAPI re-raises HTTPException and
                    # app.errors turns it into {"error": "request body too large"} with 413.
                    raise HTTPException(413, TOO_LARGE)
            return message

        await self.app(scope, counting_receive, send)
