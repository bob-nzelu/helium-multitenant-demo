"""
Unit Tests for ClamAV Scanner

Tests the optional malware scanning integration:
- Scanner initialization
- Scan results (clean, infected, skipped, error)
- Graceful degradation
- Configuration options

Target Coverage: 100%
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.bulk.scanner import ClamAVScanner, ScanResult, create_scanner


# =============================================================================
# Scanner Initialization Tests
# =============================================================================

class TestScannerInitialization:
    """Tests for ClamAV scanner initialization."""

    def test_scanner_disabled_by_default(self):
        """Scanner should be disabled by default."""
        scanner = ClamAVScanner({})
        assert scanner.enabled is False

    def test_scanner_enabled_via_config(self):
        """Scanner should be enabled when configured."""
        config = {
            "malware_scanning": {
                "enabled": True,
            }
        }
        scanner = ClamAVScanner(config)
        assert scanner.enabled is True

    def test_scanner_default_settings(self):
        """Scanner should use default settings when not configured."""
        scanner = ClamAVScanner({})
        assert scanner.clamd_host == "localhost"
        assert scanner.clamd_port == 3310
        assert scanner.timeout == 30
        assert scanner.on_unavailable == "allow"

    def test_scanner_custom_settings(self):
        """Scanner should use custom settings from config."""
        config = {
            "malware_scanning": {
                "enabled": True,
                "clamd_socket": "/custom/socket.sock",
                "clamd_host": "clamav.local",
                "clamd_port": 9999,
                "timeout_seconds": 60,
                "on_unavailable": "block",
            }
        }
        scanner = ClamAVScanner(config)
        assert scanner.clamd_socket == "/custom/socket.sock"
        assert scanner.clamd_host == "clamav.local"
        assert scanner.clamd_port == 9999
        assert scanner.timeout == 60
        assert scanner.on_unavailable == "block"

    def test_scanner_with_trace_id(self):
        """Scanner should store trace ID for logging."""
        scanner = ClamAVScanner({}, trace_id="test-trace-123")
        assert scanner.trace_id == "test-trace-123"


# =============================================================================
# Scan File Tests - Disabled
# =============================================================================

class TestScanFileDisabled:
    """Tests for scanning when disabled."""

    @pytest.mark.asyncio
    async def test_disabled_scanner_returns_skipped(self):
        """Disabled scanner should return skipped status."""
        scanner = ClamAVScanner({"malware_scanning": {"enabled": False}})

        result = await scanner.scan_file(b"test content", "test.pdf")

        assert result.status == "skipped"
        assert result.filename == "test.pdf"
        assert "disabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_empty_config_returns_skipped(self):
        """Empty config should disable scanner and return skipped."""
        scanner = ClamAVScanner({})

        result = await scanner.scan_file(b"test content", "test.pdf")

        assert result.status == "skipped"


# =============================================================================
# Scan File Tests - Enabled with Mocks
# =============================================================================

class TestScanFileEnabled:
    """Tests for scanning when enabled (with mocked ClamAV)."""

    @pytest.mark.asyncio
    async def test_clean_file_returns_clean(self):
        """Clean file should return clean status."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        # Mock pyclamd
        mock_clamd = MagicMock()
        mock_clamd.ping.return_value = True
        mock_clamd.scan_stream.return_value = None  # None = clean

        with patch.dict('sys.modules', {'pyclamd': MagicMock()}):
            with patch.object(scanner, '_get_clamd_connection', return_value=mock_clamd):
                with patch.object(scanner, '_scan_with_clamd') as mock_scan:
                    mock_scan.return_value = ScanResult(
                        status="clean",
                        filename="test.pdf",
                        message="No threats detected",
                    )

                    result = await scanner.scan_file(b"clean content", "test.pdf")

        assert result.status == "clean"
        assert result.filename == "test.pdf"

    @pytest.mark.asyncio
    async def test_infected_file_returns_infected(self):
        """Infected file should return infected status with virus name."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        mock_clamd = MagicMock()
        mock_clamd.ping.return_value = True

        with patch.object(scanner, '_get_clamd_connection', return_value=mock_clamd):
            with patch.object(scanner, '_scan_with_clamd') as mock_scan:
                mock_scan.return_value = ScanResult(
                    status="infected",
                    filename="malware.exe",
                    virus_name="Eicar-Test-Signature",
                    message="Malware detected: Eicar-Test-Signature",
                )

                result = await scanner.scan_file(b"malware content", "malware.exe")

        assert result.status == "infected"
        assert result.virus_name == "Eicar-Test-Signature"

    @pytest.mark.asyncio
    async def test_scan_tracks_time(self):
        """Scan should track processing time."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        mock_clamd = MagicMock()
        mock_clamd.ping.return_value = True

        with patch.object(scanner, '_get_clamd_connection', return_value=mock_clamd):
            with patch.object(scanner, '_scan_with_clamd') as mock_scan:
                mock_scan.return_value = ScanResult(
                    status="clean",
                    filename="test.pdf",
                )

                result = await scanner.scan_file(b"content", "test.pdf")

        assert result.scan_time_ms is not None
        assert result.scan_time_ms >= 0


# =============================================================================
# Graceful Degradation Tests
# =============================================================================

class TestGracefulDegradation:
    """Tests for graceful degradation when ClamAV unavailable."""

    @pytest.mark.asyncio
    async def test_unavailable_allow_returns_skipped(self):
        """When unavailable and on_unavailable=allow, should return skipped."""
        config = {
            "malware_scanning": {
                "enabled": True,
                "on_unavailable": "allow",
            }
        }
        scanner = ClamAVScanner(config)

        # Mock no connection available
        with patch.object(scanner, '_get_clamd_connection', return_value=None):
            result = await scanner.scan_file(b"content", "test.pdf")

        assert result.status == "skipped"
        assert "unavailable" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unavailable_block_returns_error(self):
        """When unavailable and on_unavailable=block, should return error."""
        config = {
            "malware_scanning": {
                "enabled": True,
                "on_unavailable": "block",
            }
        }
        scanner = ClamAVScanner(config)

        with patch.object(scanner, '_get_clamd_connection', return_value=None):
            result = await scanner.scan_file(b"content", "test.pdf")

        assert result.status == "error"
        assert "blocked" in result.message.lower()

    @pytest.mark.asyncio
    async def test_connection_error_graceful_degradation(self):
        """Connection errors should trigger graceful degradation."""
        config = {
            "malware_scanning": {
                "enabled": True,
                "on_unavailable": "allow",
            }
        }
        scanner = ClamAVScanner(config)

        async def raise_connection_error():
            raise ConnectionError("ClamAV connection failed")

        with patch.object(scanner, '_get_clamd_connection', side_effect=Exception("Connection failed")):
            result = await scanner.scan_file(b"content", "test.pdf")

        assert result.status == "skipped"


# =============================================================================
# Multiple File Scan Tests
# =============================================================================

class TestScanFiles:
    """Tests for scanning multiple files."""

    @pytest.mark.asyncio
    async def test_scan_multiple_files(self):
        """Should scan all files in batch."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        mock_clamd = MagicMock()
        mock_clamd.ping.return_value = True

        files = [
            ("file1.pdf", b"content1"),
            ("file2.pdf", b"content2"),
            ("file3.pdf", b"content3"),
        ]

        with patch.object(scanner, '_get_clamd_connection', return_value=mock_clamd):
            with patch.object(scanner, '_scan_with_clamd') as mock_scan:
                mock_scan.return_value = ScanResult(status="clean", filename="")

                results = await scanner.scan_files(files)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_scan_continues_after_infection(self):
        """Should continue scanning all files even after finding infection."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        files = [
            ("clean1.pdf", b"clean"),
            ("infected.exe", b"malware"),
            ("clean2.pdf", b"clean"),
        ]

        call_count = [0]

        async def mock_scan(data, filename):
            call_count[0] += 1
            if "infected" in filename:
                return ScanResult(
                    status="infected",
                    filename=filename,
                    virus_name="TestVirus",
                )
            return ScanResult(status="clean", filename=filename)

        with patch.object(scanner, 'scan_file', side_effect=mock_scan):
            results = await scanner.scan_files(files)

        # All files should be scanned
        assert call_count[0] == 3
        assert len(results) == 3


# =============================================================================
# Connection Tests
# =============================================================================

class TestConnection:
    """Tests for ClamAV daemon connection."""

    @pytest.mark.asyncio
    async def test_is_available_when_disabled(self):
        """is_available should return False when disabled."""
        scanner = ClamAVScanner({"malware_scanning": {"enabled": False}})
        assert await scanner.is_available() is False

    @pytest.mark.asyncio
    async def test_is_available_when_connected(self):
        """is_available should return True when connected."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        mock_clamd = MagicMock()
        mock_clamd.ping.return_value = True

        with patch.object(scanner, '_get_clamd_connection', return_value=mock_clamd):
            assert await scanner.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_when_not_connected(self):
        """is_available should return False when not connected."""
        config = {"malware_scanning": {"enabled": True}}
        scanner = ClamAVScanner(config)

        with patch.object(scanner, '_get_clamd_connection', return_value=None):
            assert await scanner.is_available() is False

    def test_get_version_when_not_connected(self):
        """get_version should return None when not connected."""
        scanner = ClamAVScanner({})
        assert scanner.get_version() is None


# =============================================================================
# Factory Function Tests
# =============================================================================

class TestCreateScanner:
    """Tests for create_scanner factory function."""

    def test_create_scanner_basic(self):
        """create_scanner should create a ClamAVScanner instance."""
        scanner = create_scanner({})
        assert isinstance(scanner, ClamAVScanner)

    def test_create_scanner_with_config(self):
        """create_scanner should pass config to scanner."""
        config = {
            "malware_scanning": {
                "enabled": True,
                "clamd_port": 9999,
            }
        }
        scanner = create_scanner(config)
        assert scanner.enabled is True
        assert scanner.clamd_port == 9999

    def test_create_scanner_with_trace_id(self):
        """create_scanner should pass trace_id to scanner."""
        scanner = create_scanner({}, trace_id="trace-456")
        assert scanner.trace_id == "trace-456"


# =============================================================================
# ScanResult Tests
# =============================================================================

class TestScanResult:
    """Tests for ScanResult dataclass."""

    def test_scan_result_minimal(self):
        """ScanResult should work with minimal fields."""
        result = ScanResult(status="clean", filename="test.pdf")
        assert result.status == "clean"
        assert result.filename == "test.pdf"
        assert result.virus_name is None
        assert result.message is None

    def test_scan_result_full(self):
        """ScanResult should store all fields."""
        result = ScanResult(
            status="infected",
            filename="malware.exe",
            virus_name="Eicar-Test-Signature",
            message="Malware detected",
            scan_time_ms=150.5,
        )
        assert result.status == "infected"
        assert result.filename == "malware.exe"
        assert result.virus_name == "Eicar-Test-Signature"
        assert result.message == "Malware detected"
        assert result.scan_time_ms == 150.5
