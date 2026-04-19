"""
HeartBeat Process — Monitors and optionally manages the HeartBeat FastAPI process.

The tray app is NOT a lifecycle owner. HeartBeat starts independently via:
- Registry Run key (Standard/Test tier)
- NSSM service (Pro/Enterprise tier)

This module only polls the readiness endpoint and reports status.
It can optionally start HeartBeat as a last resort (if not running and tier=test).
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# HeartBeat default endpoint
DEFAULT_HEARTBEAT_URL = "http://localhost:9000"
READINESS_PATH = "/api/status/readiness"
HEALTH_PATH = "/health"

# Polling timeout
POLL_TIMEOUT = 5.0


class HeartBeatProcess:
    """
    Monitors a running HeartBeat instance via its REST API.

    Attributes:
        base_url: HeartBeat base URL (default: http://localhost:9000).
        _process: Optional subprocess.Popen if we started HeartBeat ourselves.
    """

    def __init__(self, base_url: str = DEFAULT_HEARTBEAT_URL):
        self.base_url = base_url.rstrip("/")
        self._process: Optional[subprocess.Popen] = None
        self._client = httpx.Client(timeout=POLL_TIMEOUT)

    def poll_readiness(self) -> Dict[str, Any]:
        """
        GET /api/status/readiness from HeartBeat.

        Returns:
            Readiness response dict, or error dict if unreachable.
        """
        try:
            resp = self._client.get(f"{self.base_url}{READINESS_PATH}")
            if resp.status_code == 200:
                return resp.json()
            return {
                "ready": False,
                "error": f"HTTP {resp.status_code}",
            }
        except httpx.ConnectError:
            return {"ready": False, "error": "connection_refused"}
        except httpx.TimeoutException:
            return {"ready": False, "error": "timeout"}
        except Exception as e:
            return {"ready": False, "error": str(e)}

    def is_reachable(self) -> bool:
        """Quick check — is HeartBeat responding to /health?"""
        try:
            resp = self._client.get(f"{self.base_url}{HEALTH_PATH}")
            return resp.status_code == 200
        except Exception:
            return False

    def start_heartbeat(self) -> bool:
        """
        Start HeartBeat as a detached subprocess (last resort for test tier).

        Only use this if HeartBeat is confirmed not running. The tray app
        should normally just monitor, not manage lifecycle.

        Returns:
            True if process started, False on failure.
        """
        if self._process is not None and self._process.poll() is None:
            logger.debug("HeartBeat process already tracked")
            return True

        heartbeat_dir = Path(__file__).resolve().parent.parent.parent
        main_module = heartbeat_dir / "src" / "main.py"

        if not main_module.exists():
            logger.error(f"HeartBeat main.py not found at {main_module}")
            return False

        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )

            self._process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "src.main:app",
                 "--host", "0.0.0.0", "--port", "9000"],
                cwd=str(heartbeat_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            logger.info(f"HeartBeat started (PID {self._process.pid})")
            return True

        except Exception as e:
            logger.error(f"Failed to start HeartBeat: {e}")
            return False

    def close(self) -> None:
        """Clean up HTTP client."""
        try:
            self._client.close()
        except Exception:
            pass
