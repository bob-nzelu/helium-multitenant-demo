"""Dev runner for Core on Windows — uses SelectorEventLoop for psycopg3 compat."""
import asyncio
import os
import selectors
import sys

import uvicorn

from src.app import create_app


def selector_loop_factory():
    """Create an event loop using SelectSelector (required by psycopg3 on Windows)."""
    selector = selectors.SelectSelector()
    return asyncio.SelectorEventLoop(selector)


if __name__ == "__main__":
    host = os.environ.get("CORE_HOST", "0.0.0.0")
    port = int(os.environ.get("CORE_PORT", "8080"))

    config = uvicorn.Config(
        create_app,
        factory=True,
        host=host,
        port=port,
    )

    if sys.platform == "win32":
        # Run with SelectorEventLoop (psycopg3 requires it)
        loop = selector_loop_factory()
        server = uvicorn.Server(config)
        loop.run_until_complete(server.serve())
    else:
        uvicorn.run(create_app, factory=True, host=host, port=port)
