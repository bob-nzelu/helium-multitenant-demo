"""
Unit Tests for BulkValidationPipeline

Tests all 6 validation steps:
1. HMAC signature verification
2. File count validation
3. File extension validation
4. File size validation
5. Daily usage limit check
6. Malware scanning (optional)

Target Coverage: 100%
"""

import pytest
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from src.bulk.validation import BulkValidationPipeline
from src.services.errors import (
    ValidationFailedError,
    AuthenticationFailedError,
    RateLimitExceededError,
    MalwareDetectedError,
)


# =============================================================================
# HMAC Validation Tests
# =============================================================================

class TestHMACValidation:
    """Tests for HMAC-SHA256 signature verification."""

    def test_valid_hmac_signature_passes(self, validation_pipeline, api_key_secrets):
        """Valid HMAC signature should pass validation."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test request body"

        # Generate valid signature
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Should not raise
        result = validation_pipeline.validate_hmac(api_key, timestamp, signature, body)
        assert result == api_key

    def test_expired_timestamp_fails(self, validation_pipeline, api_key_secrets):
        """Timestamp older than 5 minutes should fail."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        # 6 minutes ago
        expired_time = datetime.now(timezone.utc) - timedelta(minutes=6)
        timestamp = expired_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test request body"

        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        with pytest.raises(AuthenticationFailedError) as exc_info:
            validation_pipeline.validate_hmac(api_key, timestamp, signature, body)

        assert exc_info.value.error_code == "TIMESTAMP_EXPIRED"

    def test_future_timestamp_within_window_passes(self, validation_pipeline, api_key_secrets):
        """Timestamp slightly in future (clock skew) should pass if within 5 min."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        # 2 minutes in future (within window)
        future_time = datetime.now(timezone.utc) + timedelta(minutes=2)
        timestamp = future_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test request body"

        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Should not raise
        result = validation_pipeline.validate_hmac(api_key, timestamp, signature, body)
        assert result == api_key

    def test_invalid_api_key_fails(self, validation_pipeline):
        """Unknown API key should fail."""
        api_key = "unknown_api_key"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test request body"
        signature = "fake_signature"

        with pytest.raises(AuthenticationFailedError) as exc_info:
            validation_pipeline.validate_hmac(api_key, timestamp, signature, body)

        assert exc_info.value.error_code == "INVALID_API_KEY"

    def test_invalid_signature_fails(self, validation_pipeline, api_key_secrets):
        """Wrong signature should fail."""
        api_key = "test_api_key_123"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test request body"
        signature = "invalid_signature_abc123"

        with pytest.raises(AuthenticationFailedError) as exc_info:
            validation_pipeline.validate_hmac(api_key, timestamp, signature, body)

        assert exc_info.value.error_code == "SIGNATURE_MISMATCH"

    def test_tampered_body_fails(self, validation_pipeline, api_key_secrets):
        """Signature for different body should fail."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        original_body = b"original request body"
        tampered_body = b"tampered request body"

        # Generate signature for original body
        body_hash = hashlib.sha256(original_body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Verify with tampered body should fail
        with pytest.raises(AuthenticationFailedError) as exc_info:
            validation_pipeline.validate_hmac(api_key, timestamp, signature, tampered_body)

        assert exc_info.value.error_code == "SIGNATURE_MISMATCH"

    def test_invalid_timestamp_format_fails(self, validation_pipeline, api_key_secrets):
        """Invalid timestamp format should fail."""
        api_key = "test_api_key_123"
        timestamp = "invalid-timestamp-format"
        body = b"test request body"
        signature = "any_signature"

        with pytest.raises(AuthenticationFailedError) as exc_info:
            validation_pipeline.validate_hmac(api_key, timestamp, signature, body)

        assert exc_info.value.error_code == "INVALID_TIMESTAMP"

    def test_empty_body_hmac_works(self, validation_pipeline, api_key_secrets):
        """Empty body should still work with HMAC."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b""

        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = validation_pipeline.validate_hmac(api_key, timestamp, signature, body)
        assert result == api_key


# =============================================================================
# File Count Validation Tests
# =============================================================================

class TestFileCountValidation:
    """Tests for file count validation."""

    def test_single_file_passes(self, validation_pipeline):
        """Single file should pass."""
        validation_pipeline.validate_file_count(1)  # Should not raise

    def test_max_files_passes(self, validation_pipeline):
        """Max allowed files (3) should pass."""
        validation_pipeline.validate_file_count(3)  # Should not raise

    def test_zero_files_fails(self, validation_pipeline):
        """Zero files should fail."""
        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_count(0)

        assert exc_info.value.error_code == "NO_FILES_PROVIDED"

    def test_too_many_files_fails(self, validation_pipeline):
        """More than max files (4+) should fail."""
        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_count(4)

        assert exc_info.value.error_code == "TOO_MANY_FILES"
        assert "Max: 3" in exc_info.value.message

    def test_negative_file_count_fails(self, validation_pipeline):
        """Negative file count should fail (treated as 0)."""
        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_count(-1)

        # Will be caught by NO_FILES_PROVIDED or similar
        assert "NO_FILES" in exc_info.value.error_code or "VALIDATION" in exc_info.value.error_code


# =============================================================================
# File Extension Validation Tests
# =============================================================================

class TestFileExtensionValidation:
    """Tests for file extension validation."""

    def test_pdf_extension_passes(self, validation_pipeline, sample_pdf_content):
        """PDF files should pass."""
        files = [("invoice.pdf", sample_pdf_content)]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_csv_extension_passes(self, validation_pipeline, sample_csv_content):
        """CSV files should pass."""
        files = [("data.csv", sample_csv_content)]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_json_extension_passes(self, validation_pipeline, sample_json_content):
        """JSON files should pass."""
        files = [("invoice.json", sample_json_content)]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_xml_extension_passes(self, validation_pipeline, sample_xml_content):
        """XML files should pass."""
        files = [("invoice.xml", sample_xml_content)]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_xlsx_extension_passes(self, validation_pipeline):
        """XLSX files should pass."""
        files = [("data.xlsx", b"fake xlsx content")]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_exe_extension_fails(self, validation_pipeline):
        """EXE files should fail."""
        files = [("malware.exe", b"MZ\x90")]

        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_extensions(files)

        assert exc_info.value.error_code == "INVALID_FILE_TYPE"
        assert ".exe" in str(exc_info.value.details)

    def test_multiple_valid_extensions_pass(self, validation_pipeline, sample_pdf_content, sample_csv_content):
        """Multiple files with valid extensions should pass."""
        files = [
            ("invoice.pdf", sample_pdf_content),
            ("data.csv", sample_csv_content),
        ]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_mixed_valid_invalid_fails(self, validation_pipeline, sample_pdf_content):
        """Mix of valid and invalid extensions should fail."""
        files = [
            ("invoice.pdf", sample_pdf_content),
            ("script.sh", b"#!/bin/bash"),
        ]

        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_extensions(files)

        assert exc_info.value.error_code == "INVALID_FILE_TYPE"

    def test_case_insensitive_extension(self, validation_pipeline, sample_pdf_content):
        """Extension matching should be case-insensitive."""
        files = [("invoice.PDF", sample_pdf_content)]
        validation_pipeline.validate_file_extensions(files)  # Should not raise

    def test_no_extension_fails(self, validation_pipeline):
        """File without extension should fail."""
        files = [("filename_no_ext", b"content")]

        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_extensions(files)

        assert exc_info.value.error_code == "INVALID_FILE_TYPE"


# =============================================================================
# File Size Validation Tests
# =============================================================================

class TestFileSizeValidation:
    """Tests for file size validation."""

    def test_small_file_passes(self, validation_pipeline):
        """Small file (< 10MB) should pass."""
        files = [("small.pdf", b"x" * 1000)]  # 1KB
        validation_pipeline.validate_file_sizes(files)  # Should not raise

    def test_max_size_file_passes(self, validation_pipeline):
        """File at exactly max size (10MB) should pass."""
        files = [("exact.pdf", b"x" * (10 * 1024 * 1024))]  # Exactly 10MB
        validation_pipeline.validate_file_sizes(files)  # Should not raise

    def test_oversized_file_fails(self, validation_pipeline):
        """File over 10MB should fail."""
        files = [("large.pdf", b"x" * (11 * 1024 * 1024))]  # 11MB

        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_sizes(files)

        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"
        assert "11" in str(exc_info.value.details) or "exceeds" in exc_info.value.message.lower()

    def test_total_size_exceeded_fails(self, validation_pipeline):
        """Total size over 30MB should fail even if individual files are OK."""
        files = [
            ("file1.pdf", b"x" * (10 * 1024 * 1024)),  # 10MB
            ("file2.pdf", b"x" * (10 * 1024 * 1024)),  # 10MB
            ("file3.pdf", b"x" * (11 * 1024 * 1024)),  # 11MB = 31MB total
        ]

        with pytest.raises(ValidationFailedError) as exc_info:
            validation_pipeline.validate_file_sizes(files)

        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"

    def test_multiple_small_files_pass(self, validation_pipeline):
        """Multiple small files within total limit should pass."""
        files = [
            ("file1.pdf", b"x" * (5 * 1024 * 1024)),  # 5MB
            ("file2.pdf", b"x" * (5 * 1024 * 1024)),  # 5MB
            ("file3.pdf", b"x" * (5 * 1024 * 1024)),  # 5MB = 15MB total
        ]
        validation_pipeline.validate_file_sizes(files)  # Should not raise

    def test_empty_file_passes(self, validation_pipeline):
        """Empty file (0 bytes) should pass size validation."""
        files = [("empty.pdf", b"")]
        validation_pipeline.validate_file_sizes(files)  # Should not raise


# =============================================================================
# Daily Usage Limit Tests
# =============================================================================

class TestDailyUsageLimit:
    """Tests for daily usage limit checking."""

    @pytest.mark.asyncio
    async def test_within_limit_passes(self, validation_pipeline, mock_heartbeat_client):
        """Request within daily limit should pass."""
        mock_heartbeat_client.check_daily_usage_response = {
            "status": "allowed",
            "current_usage": 10,
            "daily_limit": 500,
            "remaining": 490,
        }

        # Should not raise
        await validation_pipeline.check_daily_limit("test_api_key", "company_123", 2)

    @pytest.mark.asyncio
    async def test_limit_exceeded_fails(self, validation_pipeline, mock_heartbeat_client):
        """Request exceeding daily limit should fail."""
        mock_heartbeat_client.check_daily_usage_response = {
            "status": "limit_exceeded",
            "current_usage": 500,
            "daily_limit": 500,
            "remaining": 0,
            "resets_at": "2026-02-02T00:00:00Z",
        }

        with pytest.raises(RateLimitExceededError) as exc_info:
            await validation_pipeline.check_daily_limit("test_api_key", "company_123", 2)

        assert exc_info.value.error_code == "RATE_LIMIT_EXCEEDED"

    @pytest.mark.asyncio
    async def test_heartbeat_unavailable_graceful_degradation(
        self, validation_pipeline, mock_heartbeat_client
    ):
        """If HeartBeat is unavailable, should gracefully degrade (allow upload)."""
        mock_heartbeat_client.should_be_unavailable = True

        # Should NOT raise - graceful degradation
        await validation_pipeline.check_daily_limit("test_api_key", "company_123", 2)


# =============================================================================
# Malware Scanning Tests
# =============================================================================

class TestMalwareScanning:
    """Tests for malware scanning (optional feature)."""

    @pytest.mark.asyncio
    async def test_scanning_disabled_skips(self, validation_pipeline, sample_pdf_content):
        """When malware scanning is disabled, should skip."""
        validation_pipeline.malware_scan_enabled = False
        files = [("invoice.pdf", sample_pdf_content)]

        # Should not raise
        await validation_pipeline.scan_for_malware(files)

    @pytest.mark.asyncio
    async def test_scanning_enabled_but_not_implemented_skips(
        self, validation_pipeline, sample_pdf_content
    ):
        """When scanning enabled but not implemented, should log and skip."""
        validation_pipeline.malware_scan_enabled = True
        files = [("invoice.pdf", sample_pdf_content)]

        # Current implementation logs warning and skips (not implemented yet)
        await validation_pipeline.scan_for_malware(files)


# =============================================================================
# Full Validation Pipeline Tests
# =============================================================================

class TestValidateAll:
    """Tests for the complete validate_all() method."""

    @pytest.mark.asyncio
    async def test_all_validations_pass(
        self,
        validation_pipeline,
        api_key_secrets,
        sample_pdf_content,
        mock_heartbeat_client,
    ):
        """All validations passing should return api_key."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test body"
        files = [("invoice.pdf", sample_pdf_content)]

        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result = await validation_pipeline.validate_all(
            api_key=api_key,
            timestamp=timestamp,
            signature=signature,
            body=body,
            files=files,
            company_id="company_123",
        )

        assert result == api_key

    @pytest.mark.asyncio
    async def test_hmac_failure_stops_pipeline(
        self,
        validation_pipeline,
        sample_pdf_content,
    ):
        """HMAC failure should stop pipeline before other validations."""
        files = [("invoice.pdf", sample_pdf_content)]

        with pytest.raises(AuthenticationFailedError):
            await validation_pipeline.validate_all(
                api_key="invalid_key",
                timestamp="invalid",
                signature="invalid",
                body=b"body",
                files=files,
                company_id="company_123",
            )

    @pytest.mark.asyncio
    async def test_file_count_failure_stops_after_hmac(
        self,
        validation_pipeline,
        api_key_secrets,
    ):
        """File count failure should occur after HMAC passes."""
        api_key = "test_api_key_123"
        secret = api_key_secrets[api_key]
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = b"test body"
        files = []  # No files

        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        with pytest.raises(ValidationFailedError) as exc_info:
            await validation_pipeline.validate_all(
                api_key=api_key,
                timestamp=timestamp,
                signature=signature,
                body=body,
                files=files,
                company_id="company_123",
            )

        assert exc_info.value.error_code == "NO_FILES_PROVIDED"
