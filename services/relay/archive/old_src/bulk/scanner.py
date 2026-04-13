"""
ClamAV Malware Scanner Integration

Provides optional malware scanning for uploaded files using ClamAV daemon.

Features:
- Optional (configurable via config)
- Local daemon connection (no external calls)
- Graceful degradation when ClamAV unavailable
- Socket or TCP connection support

Configuration:
    {
        "malware_scanning": {
            "enabled": true,
            "clamd_socket": "/var/run/clamav/clamd.sock",  # Unix socket (preferred)
            "clamd_host": "localhost",                      # TCP fallback
            "clamd_port": 3310,
            "timeout_seconds": 30,
            "on_unavailable": "allow"  # "allow" or "block"
        }
    }

Usage:
    scanner = ClamAVScanner(config, trace_id="req_123")
    result = await scanner.scan_file(file_data, filename)

    if result["status"] == "infected":
        raise MalwareDetectedError(filename)
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result of a malware scan."""

    status: str  # "clean", "infected", "error", "skipped"
    filename: str
    virus_name: Optional[str] = None
    message: Optional[str] = None
    scan_time_ms: Optional[float] = None


class ClamAVScanner:
    """
    ClamAV malware scanner for file uploads.

    Connects to clamd (ClamAV daemon) via Unix socket or TCP.
    All scanning is done LOCALLY - no external network calls.

    Graceful Degradation:
    - If ClamAV is unavailable and on_unavailable="allow", files pass through
    - If ClamAV is unavailable and on_unavailable="block", uploads are rejected
    """

    def __init__(
        self,
        config: Dict[str, Any],
        trace_id: Optional[str] = None,
    ):
        """
        Initialize ClamAV scanner.

        Args:
            config: Malware scanning configuration
            trace_id: Optional trace ID for logging
        """
        self.config = config.get("malware_scanning", {})
        self.trace_id = trace_id

        # Configuration
        self.enabled = self.config.get("enabled", False)
        self.clamd_socket = self.config.get("clamd_socket")
        self.clamd_host = self.config.get("clamd_host", "localhost")
        self.clamd_port = self.config.get("clamd_port", 3310)
        self.timeout = self.config.get("timeout_seconds", 30)
        self.on_unavailable = self.config.get("on_unavailable", "allow")

        # Connection (lazy initialized)
        self._clamd = None
        self._connection_type = None

        logger.debug(
            f"ClamAV scanner initialized - enabled={self.enabled}, "
            f"on_unavailable={self.on_unavailable}",
            extra={"trace_id": self.trace_id},
        )

    async def scan_file(
        self,
        file_data: bytes,
        filename: str,
    ) -> ScanResult:
        """
        Scan a single file for malware.

        Args:
            file_data: File content bytes
            filename: Original filename (for logging)

        Returns:
            ScanResult with status and details
        """
        import time
        start_time = time.time()

        if not self.enabled:
            return ScanResult(
                status="skipped",
                filename=filename,
                message="Malware scanning disabled",
            )

        try:
            # Try to connect to ClamAV
            clamd = await self._get_clamd_connection()

            if clamd is None:
                return await self._handle_unavailable(filename)

            # Perform scan
            result = await self._scan_with_clamd(clamd, file_data, filename)

            scan_time = (time.time() - start_time) * 1000
            result.scan_time_ms = scan_time

            logger.info(
                f"File scanned - filename={filename}, status={result.status}, "
                f"time={scan_time:.2f}ms",
                extra={"trace_id": self.trace_id},
            )

            return result

        except Exception as e:
            logger.warning(
                f"ClamAV scan error - filename={filename}: {e}",
                extra={"trace_id": self.trace_id},
            )
            return await self._handle_unavailable(filename, error=str(e))

    async def scan_files(
        self,
        files: List[Tuple[str, bytes]],
    ) -> List[ScanResult]:
        """
        Scan multiple files for malware.

        Args:
            files: List of (filename, file_data) tuples

        Returns:
            List of ScanResult for each file
        """
        results = []

        for filename, file_data in files:
            result = await self.scan_file(file_data, filename)
            results.append(result)

            # If any file is infected, we might want to stop early
            if result.status == "infected":
                logger.warning(
                    f"Infected file detected, continuing scan for audit trail",
                    extra={"trace_id": self.trace_id},
                )

        return results

    async def _get_clamd_connection(self):
        """
        Get connection to ClamAV daemon.

        Tries Unix socket first (more efficient), falls back to TCP.

        Returns:
            pyclamd connection or None if unavailable
        """
        if self._clamd is not None:
            return self._clamd

        try:
            import pyclamd
        except ImportError:
            logger.warning(
                "pyclamd not installed - malware scanning unavailable",
                extra={"trace_id": self.trace_id},
            )
            return None

        # Try Unix socket first (if configured)
        if self.clamd_socket:
            try:
                clamd = pyclamd.ClamdUnixSocket(filename=self.clamd_socket)
                if clamd.ping():
                    self._clamd = clamd
                    self._connection_type = "socket"
                    logger.info(
                        f"Connected to ClamAV via Unix socket: {self.clamd_socket}",
                        extra={"trace_id": self.trace_id},
                    )
                    return clamd
            except Exception as e:
                logger.debug(
                    f"Unix socket connection failed: {e}",
                    extra={"trace_id": self.trace_id},
                )

        # Fall back to TCP
        try:
            clamd = pyclamd.ClamdNetworkSocket(
                host=self.clamd_host,
                port=self.clamd_port,
                timeout=self.timeout,
            )
            if clamd.ping():
                self._clamd = clamd
                self._connection_type = "tcp"
                logger.info(
                    f"Connected to ClamAV via TCP: {self.clamd_host}:{self.clamd_port}",
                    extra={"trace_id": self.trace_id},
                )
                return clamd
        except Exception as e:
            logger.debug(
                f"TCP connection failed: {e}",
                extra={"trace_id": self.trace_id},
            )

        return None

    async def _scan_with_clamd(
        self,
        clamd,
        file_data: bytes,
        filename: str,
    ) -> ScanResult:
        """
        Perform actual scan using pyclamd.

        Args:
            clamd: pyclamd connection
            file_data: File bytes
            filename: Original filename

        Returns:
            ScanResult with scan details
        """
        # pyclamd is synchronous, run in thread pool
        loop = asyncio.get_event_loop()

        def _scan():
            return clamd.scan_stream(file_data)

        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _scan),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return ScanResult(
                status="error",
                filename=filename,
                message=f"Scan timeout ({self.timeout}s)",
            )

        # pyclamd returns None for clean files, or dict for infected
        if result is None:
            return ScanResult(
                status="clean",
                filename=filename,
                message="No threats detected",
            )

        # Result format: {'stream': ('FOUND', 'Eicar-Test-Signature')}
        if "stream" in result:
            status, virus_name = result["stream"]
            if status == "FOUND":
                return ScanResult(
                    status="infected",
                    filename=filename,
                    virus_name=virus_name,
                    message=f"Malware detected: {virus_name}",
                )

        return ScanResult(
            status="clean",
            filename=filename,
            message="No threats detected",
        )

    async def _handle_unavailable(
        self,
        filename: str,
        error: Optional[str] = None,
    ) -> ScanResult:
        """
        Handle ClamAV unavailable scenario based on configuration.

        Args:
            filename: File being scanned
            error: Optional error message

        Returns:
            ScanResult based on on_unavailable setting
        """
        if self.on_unavailable == "block":
            logger.error(
                f"ClamAV unavailable, blocking upload - filename={filename}",
                extra={"trace_id": self.trace_id},
            )
            return ScanResult(
                status="error",
                filename=filename,
                message=f"Malware scanner unavailable. Upload blocked. Error: {error or 'Connection failed'}",
            )
        else:  # "allow"
            logger.warning(
                f"ClamAV unavailable, allowing upload - filename={filename}",
                extra={"trace_id": self.trace_id},
            )
            return ScanResult(
                status="skipped",
                filename=filename,
                message=f"Malware scanner unavailable, scan skipped. Error: {error or 'Connection failed'}",
            )

    async def is_available(self) -> bool:
        """
        Check if ClamAV is available.

        Returns:
            True if ClamAV daemon is reachable
        """
        if not self.enabled:
            return False

        clamd = await self._get_clamd_connection()
        return clamd is not None

    def get_version(self) -> Optional[str]:
        """
        Get ClamAV version string.

        Returns:
            Version string or None if unavailable
        """
        if self._clamd is None:
            return None

        try:
            return self._clamd.version()
        except Exception:
            return None


# =============================================================================
# Factory Function
# =============================================================================

def create_scanner(config: Dict[str, Any], trace_id: Optional[str] = None) -> ClamAVScanner:
    """
    Create a ClamAV scanner instance.

    Args:
        config: Service configuration (should contain malware_scanning section)
        trace_id: Optional trace ID

    Returns:
        ClamAVScanner instance
    """
    return ClamAVScanner(config, trace_id)
