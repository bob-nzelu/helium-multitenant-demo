"""
Process Handle — Wraps a subprocess.Popen for a managed service.

Responsible for starting, stopping, and monitoring a single child process.
Uses psutil for PID monitoring and graceful shutdown.

Lifecycle:
    start() → subprocess.Popen with stdout/stderr → log files
    is_alive() → psutil PID check
    stop(grace_seconds) → SIGTERM → wait → SIGKILL if needed
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Windows-specific process creation flags
_IS_WINDOWS = sys.platform == "win32"


class ProcessHandle:
    """
    Wraps a subprocess.Popen for a managed service.

    Attributes:
        service_name: Canonical service name (e.g., "core", "relay")
        executable_path: Full path to the service executable
        working_directory: CWD for the subprocess
        arguments: CLI arguments (list of strings)
        environment: Extra env vars to merge with os.environ
        health_endpoint: URL for health checks (e.g., "http://localhost:8000/health")
        process: The subprocess.Popen instance (None when stopped)
        pid: Current PID (None when stopped)
        status: Current status string
        restart_count: Number of restarts since last reset
    """

    def __init__(
        self,
        service_name: str,
        executable_path: str,
        working_directory: str,
        arguments: Optional[List[str]] = None,
        environment: Optional[Dict[str, str]] = None,
        health_endpoint: Optional[str] = None,
        log_dir: Optional[str] = None,
    ):
        self.service_name = service_name
        self.executable_path = executable_path
        self.working_directory = working_directory
        self.arguments = arguments or []
        self.environment = environment or {}
        self.health_endpoint = health_endpoint
        self.log_dir = log_dir or "."

        self.process: Optional[subprocess.Popen] = None
        self.pid: Optional[int] = None
        self.status: str = "stopped"
        self.restart_count: int = 0
        self.last_started_at: Optional[datetime] = None
        self.last_stopped_at: Optional[datetime] = None

        # Health tracking
        self._consecutive_health_failures: int = 0
        self._healthy_since: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row: Dict[str, Any], log_dir: str = ".") -> "ProcessHandle":
        """Create a ProcessHandle from a managed_services database row."""
        arguments = json.loads(row["arguments"]) if row.get("arguments") else []
        environment = json.loads(row["environment"]) if row.get("environment") else {}

        handle = cls(
            service_name=row["service_name"],
            executable_path=row["executable_path"],
            working_directory=row["working_directory"],
            arguments=arguments,
            environment=environment,
            health_endpoint=row.get("health_endpoint"),
            log_dir=log_dir,
        )

        # Restore runtime state from DB
        handle.restart_count = row.get("restart_count", 0)
        handle.status = row.get("current_status", "stopped")

        return handle

    async def start(self) -> int:
        """
        Start the service subprocess.

        Returns the PID of the started process.
        Raises RuntimeError if the service is already running.
        """
        if self.process is not None and self.is_alive():
            raise RuntimeError(
                f"Service {self.service_name} is already running (PID {self.pid})"
            )

        # Prepare log files
        os.makedirs(self.log_dir, exist_ok=True)
        stdout_path = os.path.join(self.log_dir, f"{self.service_name}.log")
        stderr_path = os.path.join(self.log_dir, f"{self.service_name}_error.log")

        # Build command
        cmd = [self.executable_path] + self.arguments

        # Merge environment
        env = {**os.environ, **self.environment}

        # Open log files
        stdout_file = open(stdout_path, "a", encoding="utf-8")
        stderr_file = open(stderr_path, "a", encoding="utf-8")

        try:
            # Platform-specific flags
            kwargs: Dict[str, Any] = {
                "cwd": self.working_directory,
                "env": env,
                "stdout": stdout_file,
                "stderr": stderr_file,
            }

            if _IS_WINDOWS:
                # CREATE_NEW_PROCESS_GROUP: child survives parent exit
                # CREATE_NO_WINDOW: no console window for background services
                kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )
            else:
                # Start in new process group on Unix
                kwargs["start_new_session"] = True

            self.process = subprocess.Popen(cmd, **kwargs)
            self.pid = self.process.pid
            self.status = "starting"
            self.last_started_at = datetime.now(timezone.utc)
            self._consecutive_health_failures = 0
            self._healthy_since = None

            logger.info(
                f"Started {self.service_name}: PID={self.pid}, "
                f"cmd={' '.join(cmd)}"
            )

            return self.pid

        except Exception as e:
            stdout_file.close()
            stderr_file.close()
            self.status = "stopped"
            logger.error(f"Failed to start {self.service_name}: {e}")
            raise

    async def stop(self, grace_seconds: int = 30) -> None:
        """
        Gracefully stop the service.

        Sequence:
        1. Send SIGTERM (or TerminateProcess on Windows)
        2. Wait up to grace_seconds for clean exit
        3. If still alive, send SIGKILL (or forceful termination)
        4. Confirm PID is gone
        """
        if self.process is None or not self.is_alive():
            self.status = "stopped"
            self.pid = None
            self.process = None
            self.last_stopped_at = datetime.now(timezone.utc)
            return

        pid = self.pid
        logger.info(f"Stopping {self.service_name} (PID {pid})...")

        try:
            # Phase 1: graceful termination signal
            if _IS_WINDOWS:
                self.process.terminate()
            else:
                os.kill(pid, signal.SIGTERM)

            # Phase 2: wait for graceful exit
            try:
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self.process.wait
                    ),
                    timeout=grace_seconds,
                )
                logger.info(
                    f"{self.service_name} stopped gracefully (PID {pid})"
                )
            except asyncio.TimeoutError:
                # Phase 3: forceful kill
                logger.warning(
                    f"{self.service_name} did not stop within {grace_seconds}s, "
                    f"force killing PID {pid}"
                )
                if _IS_WINDOWS:
                    self.process.kill()
                else:
                    os.kill(pid, signal.SIGKILL)

                # Wait briefly for kill to take effect
                try:
                    await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, self.process.wait
                        ),
                        timeout=10,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"{self.service_name} PID {pid} did not die after SIGKILL"
                    )

        except ProcessLookupError:
            logger.info(f"{self.service_name} PID {pid} already gone")
        except Exception as e:
            logger.error(f"Error stopping {self.service_name}: {e}")

        self.status = "stopped"
        self.pid = None
        self.process = None
        self.last_stopped_at = datetime.now(timezone.utc)

    def is_alive(self) -> bool:
        """
        Check if the service process is still running.

        Uses psutil for reliable cross-platform PID checking.
        Falls back to subprocess.poll() if psutil is unavailable.
        """
        if self.process is None or self.pid is None:
            return False

        try:
            import psutil
            try:
                proc = psutil.Process(self.pid)
                return proc.is_running() and proc.status() != "zombie"
            except psutil.NoSuchProcess:
                return False
        except ImportError:
            # Fallback: use subprocess.poll()
            return self.process.poll() is None

    def record_health_success(self) -> None:
        """Record a successful health check."""
        self._consecutive_health_failures = 0
        if self.status == "starting":
            self.status = "healthy"
            self._healthy_since = datetime.now(timezone.utc)
        elif self.status != "healthy":
            self.status = "healthy"
            self._healthy_since = datetime.now(timezone.utc)

    def record_health_failure(self) -> None:
        """Record a failed health check. 3 consecutive failures → unhealthy."""
        self._consecutive_health_failures += 1
        if self._consecutive_health_failures >= 3:
            self.status = "unhealthy"
            self._healthy_since = None

    @property
    def healthy_duration_seconds(self) -> Optional[float]:
        """How long the service has been continuously healthy (seconds)."""
        if self._healthy_since is None:
            return None
        return (datetime.now(timezone.utc) - self._healthy_since).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "service_name": self.service_name,
            "pid": self.pid,
            "status": self.status,
            "restart_count": self.restart_count,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_stopped_at": self.last_stopped_at.isoformat() if self.last_stopped_at else None,
            "health_endpoint": self.health_endpoint,
            "healthy_since": self._healthy_since.isoformat() if self._healthy_since else None,
        }
