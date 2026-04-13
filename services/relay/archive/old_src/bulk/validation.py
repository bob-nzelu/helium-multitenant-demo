"""
Bulk Upload Validation Pipeline

Implements all validation steps for bulk file uploads:
1. HMAC signature verification
2. File count validation
3. File extension validation
4. File size validation
5. Daily usage limit check (via HeartBeat)
6. Optional malware scanning

Decision from RELAY_DECISIONS.md:
All validation happens BEFORE blob write. If validation fails, no data is persisted.
"""

import logging
import hashlib
import hmac
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path

from ..services.clients import HeartBeatClient
from ..services.errors import (
    ValidationFailedError,
    AuthenticationFailedError,
    RateLimitExceededError,
    MalwareDetectedError,
)


logger = logging.getLogger(__name__)


class BulkValidationPipeline:
    """
    File validation pipeline for bulk uploads.

    Validates HMAC signatures, file counts, extensions, sizes, daily limits.

    All validations are synchronous and fail-fast.
    """

    def __init__(
        self,
        heartbeat_client: HeartBeatClient,
        config: Dict[str, Any],
        api_key_secrets: Dict[str, str],
        trace_id: Optional[str] = None,
    ):
        """
        Initialize validation pipeline.

        Args:
            heartbeat_client: Client for HeartBeat API (daily limits, dedup)
            config: Bulk service configuration
            api_key_secrets: Mapping of API keys to secrets for HMAC verification
            trace_id: Optional trace ID for request tracking
        """
        self.heartbeat_client = heartbeat_client
        self.config = config
        self.api_key_secrets = api_key_secrets
        self.trace_id = trace_id

        # Extract config values
        self.max_files_per_request = config.get("max_files_per_request", 3)
        self.max_file_size_mb = config.get("max_file_size_mb", 10)
        self.max_total_size_mb = config.get("max_total_size_mb", 30)
        self.allowed_extensions = [
            ext.lower() for ext in config.get("allowed_extensions", [".pdf", ".xml", ".json", ".csv", ".xlsx"])
        ]
        self.malware_scan_enabled = config.get("malware_scan_enabled", False)
        self.malware_scan_url = config.get("malware_scan_url")

        logger.debug(
            f"Initialized BulkValidationPipeline - trace_id={self.trace_id}",
            extra={"trace_id": self.trace_id},
        )

    def validate_hmac(
        self,
        api_key: str,
        timestamp: str,
        signature: str,
        body: bytes,
    ) -> str:
        """
        Validate HMAC-SHA256 signature.

        Args:
            api_key: Client API key from X-API-Key header
            timestamp: ISO 8601 timestamp from X-Timestamp header
            signature: HMAC signature from X-Signature header
            body: Raw request body bytes

        Returns:
            api_key if validation succeeds

        Raises:
            AuthenticationFailedError: If signature is invalid or timestamp expired
        """
        # 1. Check timestamp (5-minute window)
        try:
            request_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as e:
            logger.warning(
                f"Invalid timestamp format: {timestamp}",
                extra={"trace_id": self.trace_id, "api_key": api_key},
            )
            raise AuthenticationFailedError(
                "INVALID_TIMESTAMP",
                "Timestamp must be in ISO 8601 format with Z suffix (e.g., 2026-01-31T10:00:00Z)",
            ) from e

        current_time = datetime.now(timezone.utc)
        time_diff_seconds = abs((current_time - request_time).total_seconds())

        if time_diff_seconds > 300:  # 5 minutes
            logger.warning(
                f"Timestamp expired - diff={time_diff_seconds}s",
                extra={"trace_id": self.trace_id, "api_key": api_key},
            )
            raise AuthenticationFailedError(
                "TIMESTAMP_EXPIRED",
                f"Timestamp is {int(time_diff_seconds)}s old. Must be within 300s (5 minutes).",
            )

        # 2. Lookup secret for API key
        secret = self.api_key_secrets.get(api_key)
        if secret is None:
            logger.warning(
                f"API key not found: {api_key}",
                extra={"trace_id": self.trace_id, "api_key": api_key},
            )
            raise AuthenticationFailedError(
                "INVALID_API_KEY",
                "API key not recognized. Please check your credentials.",
            )

        # 3. Recompute signature
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # 4. Constant-time comparison
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning(
                "HMAC signature mismatch",
                extra={"trace_id": self.trace_id, "api_key": api_key},
            )
            raise AuthenticationFailedError(
                "SIGNATURE_MISMATCH",
                "HMAC signature verification failed. Please check your signing logic.",
            )

        logger.info(
            "HMAC validation successful",
            extra={"trace_id": self.trace_id, "api_key": api_key},
        )

        return api_key

    def validate_file_count(self, file_count: int) -> None:
        """
        Validate file count is within limits.

        Args:
            file_count: Number of files in request

        Raises:
            ValidationFailedError: If file count is 0 or exceeds max_files_per_request
        """
        if file_count == 0:
            raise ValidationFailedError(
                "NO_FILES_PROVIDED",
                "No files provided. Please upload at least 1 file.",
            )

        if file_count > self.max_files_per_request:
            raise ValidationFailedError(
                "TOO_MANY_FILES",
                f"Too many files. Max: {self.max_files_per_request}, Received: {file_count}",
            )

        logger.debug(
            f"File count validation passed - count={file_count}",
            extra={"trace_id": self.trace_id},
        )

    def validate_file_extensions(
        self, files: List[Tuple[str, bytes]]
    ) -> None:
        """
        Validate file extensions are allowed.

        Args:
            files: List of (filename, file_data) tuples

        Raises:
            ValidationFailedError: If any file has disallowed extension
        """
        errors = []

        for filename, _ in files:
            extension = Path(filename).suffix.lower()

            if extension not in self.allowed_extensions:
                errors.append({
                    "filename": filename,
                    "error": f"File type {extension} not allowed. Allowed: {', '.join(self.allowed_extensions)}",
                })

        if errors:
            logger.warning(
                f"File extension validation failed - {len(errors)} errors",
                extra={"trace_id": self.trace_id, "errors": errors},
            )
            raise ValidationFailedError(
                "INVALID_FILE_TYPE",
                "File type validation failed",
                details=errors,
            )

        logger.debug(
            "File extension validation passed",
            extra={"trace_id": self.trace_id},
        )

    def validate_file_sizes(
        self, files: List[Tuple[str, bytes]]
    ) -> None:
        """
        Validate file sizes are within limits.

        Args:
            files: List of (filename, file_data) tuples

        Raises:
            ValidationFailedError: If any file exceeds size limits
        """
        errors = []
        total_size_bytes = 0

        for filename, file_data in files:
            file_size_bytes = len(file_data)
            file_size_mb = file_size_bytes / (1024 * 1024)
            total_size_bytes += file_size_bytes

            if file_size_mb > self.max_file_size_mb:
                errors.append({
                    "filename": filename,
                    "error": f"File size ({file_size_mb:.2f}MB) exceeds limit of {self.max_file_size_mb}MB",
                })

        total_size_mb = total_size_bytes / (1024 * 1024)
        if total_size_mb > self.max_total_size_mb:
            errors.append({
                "error": f"Total upload size ({total_size_mb:.2f}MB) exceeds limit of {self.max_total_size_mb}MB",
            })

        if errors:
            logger.warning(
                f"File size validation failed - {len(errors)} errors",
                extra={"trace_id": self.trace_id, "errors": errors},
            )
            raise ValidationFailedError(
                "FILE_SIZE_EXCEEDED",
                "File size validation failed",
                details=errors,
            )

        logger.debug(
            f"File size validation passed - total={total_size_mb:.2f}MB",
            extra={"trace_id": self.trace_id},
        )

    async def check_daily_limit(
        self, api_key: str, company_id: str, file_count: int
    ) -> None:
        """
        Check if upload exceeds daily usage limit.

        Args:
            api_key: Client API key
            company_id: Company identifier
            file_count: Number of files in this request

        Raises:
            RateLimitExceededError: If daily limit exceeded
        """
        # TODO: HeartBeat API integration
        # For Phase 1B, we implement the retry logic assuming HeartBeat API exists.
        # HeartBeat team will implement the actual endpoint.

        try:
            response = await self.heartbeat_client.check_daily_usage(
                company_id=company_id,
                file_count=file_count,
            )

            if response.get("status") == "limit_exceeded":
                logger.warning(
                    f"Daily limit exceeded - company={company_id}",
                    extra={"trace_id": self.trace_id, "company_id": company_id},
                )
                raise RateLimitExceededError(
                    "RATE_LIMIT_EXCEEDED",
                    f"Daily limit of {response.get('daily_limit')} files exceeded for company. Resets at midnight.",
                    retry_after=response.get("resets_at"),
                )

            logger.debug(
                f"Daily limit check passed - usage={response.get('current_usage')}/{response.get('daily_limit')}",
                extra={"trace_id": self.trace_id, "company_id": company_id},
            )

        except Exception as e:
            # Graceful degradation: If HeartBeat unavailable, allow upload
            logger.warning(
                f"Daily limit check failed (HeartBeat unavailable) - allowing upload: {e}",
                extra={"trace_id": self.trace_id, "company_id": company_id},
            )
            # Do not raise - graceful degradation

    async def scan_for_malware(
        self, files: List[Tuple[str, bytes]]
    ) -> None:
        """
        Scan files for malware using ClamAV (optional, configurable).

        ClamAV runs LOCALLY - no external network calls.
        If ClamAV is unavailable, behavior depends on config:
        - on_unavailable="allow" -> skip scan, allow upload (default)
        - on_unavailable="block" -> reject upload

        Args:
            files: List of (filename, file_data) tuples

        Raises:
            MalwareDetectedError: If malware detected in any file
            ValidationFailedError: If on_unavailable="block" and ClamAV unavailable
        """
        # Check if malware scanning is enabled (legacy config check)
        malware_config = self.config.get("malware_scanning", {})
        enabled = malware_config.get("enabled", self.malware_scan_enabled)

        if not enabled:
            logger.debug(
                "Malware scanning disabled",
                extra={"trace_id": self.trace_id},
            )
            return

        # Import and create scanner
        try:
            from .scanner import ClamAVScanner

            scanner = ClamAVScanner(self.config, trace_id=self.trace_id)
            results = await scanner.scan_files(files)

            # Check for infected files
            errors = []
            for result in results:
                if result.status == "infected":
                    errors.append({
                        "filename": result.filename,
                        "error": f"Malware detected: {result.virus_name}",
                    })
                elif result.status == "error":
                    # ClamAV unavailable and on_unavailable="block"
                    on_unavailable = malware_config.get("on_unavailable", "allow")
                    if on_unavailable == "block":
                        raise ValidationFailedError(
                            "MALWARE_SCAN_UNAVAILABLE",
                            f"Malware scanner unavailable: {result.message}",
                            details=[{"filename": result.filename, "error": result.message}],
                        )
                    # else: allow (scan skipped)

            if errors:
                raise MalwareDetectedError(
                    "MALWARE_DETECTED",
                    "Malware detected in uploaded files",
                    details=errors,
                )

            logger.info(
                f"Malware scan completed - {len(files)} files scanned, all clean",
                extra={"trace_id": self.trace_id},
            )

        except ImportError:
            # pyclamd not installed
            on_unavailable = malware_config.get("on_unavailable", "allow")
            if on_unavailable == "block":
                raise ValidationFailedError(
                    "MALWARE_SCAN_UNAVAILABLE",
                    "Malware scanner not available (pyclamd not installed)",
                )
            logger.warning(
                "pyclamd not installed - malware scanning skipped",
                extra={"trace_id": self.trace_id},
            )

    async def validate_all(
        self,
        api_key: str,
        timestamp: str,
        signature: str,
        body: bytes,
        files: List[Tuple[str, bytes]],
        company_id: str,
    ) -> str:
        """
        Run all validations in sequence.

        Args:
            api_key: Client API key from X-API-Key header
            timestamp: ISO 8601 timestamp from X-Timestamp header
            signature: HMAC signature from X-Signature header
            body: Raw request body bytes
            files: List of (filename, file_data) tuples
            company_id: Company identifier

        Returns:
            Validated api_key

        Raises:
            AuthenticationFailedError: If HMAC validation fails
            ValidationFailedError: If file validation fails
            RateLimitExceededError: If daily limit exceeded
            MalwareDetectedError: If malware detected
        """
        # 1. HMAC signature validation
        validated_api_key = self.validate_hmac(api_key, timestamp, signature, body)

        # 2. File count validation
        self.validate_file_count(len(files))

        # 3. File extension validation
        self.validate_file_extensions(files)

        # 4. File size validation
        self.validate_file_sizes(files)

        # 5. Daily usage limit check
        await self.check_daily_limit(validated_api_key, company_id, len(files))

        # 6. Optional malware scanning
        await self.scan_for_malware(files)

        logger.info(
            f"All validations passed - {len(files)} files",
            extra={"trace_id": self.trace_id, "api_key": validated_api_key},
        )

        return validated_api_key
