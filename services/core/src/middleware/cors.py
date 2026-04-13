"""
CORS Configuration

Per D-WS0-010: Development allows all origins (*).
Production uses comma-separated list via CORE_CORS_ORIGINS env var.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import CoreConfig


def configure_cors(app: FastAPI, config: CoreConfig) -> None:
    """Add CORS middleware to the app based on config."""
    origins = [o.strip() for o in config.cors_origins.split(",")]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
