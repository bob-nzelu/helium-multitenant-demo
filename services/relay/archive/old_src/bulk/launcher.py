"""
Relay Bulk Upload - Subprocess Launcher

For Test/Standard tier, Relay runs as a subprocess spawned by FloatWindow.

Decision from RELAY_DECISIONS.md (Decision 4A):
- Test/Standard: Relay runs as subprocess in FloatWindow
- Pro/Enterprise: Relay runs as Docker container (separate from Float)

This module provides the launcher interface for Float integration.
"""

import logging
import subprocess
import sys
import time
import requests
from typing import Optional
from pathlib import Path


logger = logging.getLogger(__name__)


class RelayBulkLauncher:
    """
    Launcher for Relay Bulk service subprocess (Test/Standard tier).

    Responsibilities:
    - Start Relay as subprocess
    - Wait for /health to respond (max 6 seconds)
    - Graceful shutdown on Float close
    - Log output to Float's log file
    """

    def __init__(
        self,
        port: int = 8082,
        blob_path: str = "/tmp/helium_blobs",
        config_path: Optional[str] = None,
        log_file: Optional[str] = None,
    ):
        """
        Initialize Relay launcher.

        Args:
            port: Port for Relay service (default: 8082)
            blob_path: Path for blob storage (default: /tmp/helium_blobs)
            config_path: Optional path to relay config JSON
            log_file: Optional path to log file (default: stdout)
        """
        self.port = port
        self.blob_path = blob_path
        self.config_path = config_path
        self.log_file = log_file

        self.process: Optional[subprocess.Popen] = None
        self.is_ready = False

        logger.info(
            f"Initialized RelayBulkLauncher - port={port}, blob_path={blob_path}"
        )

    def start(self, timeout: int = 6) -> bool:
        """
        Start Relay service as subprocess.

        Args:
            timeout: Max wait time in seconds for /health to respond (default: 6)

        Returns:
            True if service started successfully, False otherwise
        """
        logger.info("Starting Relay Bulk service subprocess")

        # Prepare environment
        env = {
            "PORT": str(self.port),
            "BLOB_PATH": self.blob_path,
            "REGISTRY_TYPE": "mock",
            "ENVIRONMENT": "test",
        }

        if self.config_path:
            env["CONFIG_PATH"] = self.config_path

        # Prepare command
        # Assumes helium.relay.bulk.main is importable
        cmd = [sys.executable, "-m", "helium.relay.bulk.main"]

        # Open log file if specified
        if self.log_file:
            log_handle = open(self.log_file, "a")
        else:
            log_handle = subprocess.PIPE

        try:
            # Start subprocess
            self.process = subprocess.Popen(
                cmd,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                bufsize=1,  # Line buffered
            )

            logger.info(f"Relay subprocess started - PID={self.process.pid}")

            # Wait for /health to respond
            start_time = time.time()
            max_wait = timeout

            while time.time() - start_time < max_wait:
                try:
                    response = requests.get(
                        f"http://localhost:{self.port}/health",
                        timeout=0.5,
                    )

                    if response.status_code == 200:
                        logger.info("Relay service is ready")
                        self.is_ready = True
                        return True

                except requests.RequestException:
                    pass

                time.sleep(0.2)  # Check every 200ms

            # Timeout reached
            logger.warning(f"Relay service health check timeout after {timeout}s")

            # Log warning but continue (graceful degradation)
            # Float UI can still function without Relay for other features
            return False

        except Exception as e:
            logger.error(f"Failed to start Relay subprocess: {e}", exc_info=True)
            return False

    def stop(self, timeout: int = 5) -> bool:
        """
        Gracefully stop Relay service subprocess.

        Args:
            timeout: Max wait time in seconds for process to terminate (default: 5)

        Returns:
            True if process stopped successfully, False otherwise
        """
        if not self.process:
            logger.warning("Relay subprocess not running")
            return True

        logger.info(f"Stopping Relay subprocess - PID={self.process.pid}")

        try:
            # Send SIGTERM (graceful shutdown)
            self.process.terminate()

            # Wait for process to terminate
            try:
                self.process.wait(timeout=timeout)
                logger.info("Relay subprocess terminated gracefully")
                return True

            except subprocess.TimeoutExpired:
                # Force kill if timeout
                logger.warning("Relay subprocess did not terminate, forcing kill")
                self.process.kill()
                self.process.wait(timeout=2)
                return False

        except Exception as e:
            logger.error(f"Failed to stop Relay subprocess: {e}", exc_info=True)
            return False

        finally:
            self.process = None
            self.is_ready = False

    def is_running(self) -> bool:
        """
        Check if Relay subprocess is running.

        Returns:
            True if process is running, False otherwise
        """
        if not self.process:
            return False

        # Check if process is still alive
        return self.process.poll() is None

    def get_health_status(self) -> dict:
        """
        Get health status from Relay service.

        Returns:
            Health status dict, or error dict if unavailable
        """
        if not self.is_running():
            return {
                "status": "unavailable",
                "message": "Relay service is not running",
            }

        try:
            response = requests.get(
                f"http://localhost:{self.port}/health",
                timeout=2.0,
            )

            if response.status_code == 200:
                return response.json()
            else:
                return {
                    "status": "error",
                    "message": f"Health check failed with status {response.status_code}",
                }

        except requests.RequestException as e:
            return {
                "status": "unavailable",
                "message": f"Health check failed: {e}",
            }


# ============================================================================
# Float Integration Example
# ============================================================================


def integrate_with_float():
    """
    Example integration code for Float UI.

    This shows how FloatWindow should start/stop Relay on startup/close.
    """

    # In FloatWindow.__init__():
    # self.relay_launcher = RelayBulkLauncher(
    #     port=8082,
    #     blob_path="/var/helium/files_blob",
    #     log_file="/var/helium/logs/relay-bulk.log",
    # )
    #
    # # Start Relay on Float startup
    # success = self.relay_launcher.start(timeout=6)
    # if not success:
    #     logger.warning("Relay service did not start - bulk upload disabled")
    #     # Float continues to work, but bulk upload button is disabled

    # In FloatWindow.closeEvent():
    # self.relay_launcher.stop(timeout=5)

    pass


# ============================================================================
# Standalone Testing
# ============================================================================


if __name__ == "__main__":
    """
    Test launcher standalone.

    Usage:
        python -m helium.relay.bulk.launcher
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    launcher = RelayBulkLauncher()

    print("Starting Relay Bulk service...")
    success = launcher.start()

    if success:
        print("✓ Relay service started successfully")
        print(f"✓ Health status: {launcher.get_health_status()}")
        print("\nPress Enter to stop...")
        input()
    else:
        print("✗ Relay service failed to start")

    print("Stopping Relay service...")
    launcher.stop()
    print("✓ Relay service stopped")
