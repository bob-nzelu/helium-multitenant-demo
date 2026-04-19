"""HeartBeat Satellite API — Proxy endpoints forwarding to Primary."""

from .endpoints import router as satellite_router

__all__ = ["satellite_router"]
