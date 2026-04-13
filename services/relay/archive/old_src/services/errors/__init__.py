"""
Services - Error Definitions Module

Provides all error code definitions and error handling utilities for Relay services.
"""

from .exceptions import (
    RelayError,
    ValidationFailedError,
    NoFilesProvidedError,
    TooManyFilesError,
    InvalidFileExtensionError,
    FileSizeExceededError,
    MalwareDetectedError,
    AuthenticationFailedError,
    InvalidAPIKeyError,
    SignatureVerificationFailedError,
    TimestampExpiredError,
    RateLimitExceededError,
    InternalErrorError,
    ServiceUnavailableError,
    CoreUnavailableError,
    HeartBeatUnavailableError,
    TransientError,
    ConnectionTimeoutError,
    ConnectionResetError,
    QueueNotFoundError,
    DuplicateFileError,
)
from .handlers import format_error_response, format_success_response

__all__ = [
    "RelayError",
    "ValidationFailedError",
    "NoFilesProvidedError",
    "TooManyFilesError",
    "InvalidFileExtensionError",
    "FileSizeExceededError",
    "MalwareDetectedError",
    "AuthenticationFailedError",
    "InvalidAPIKeyError",
    "SignatureVerificationFailedError",
    "TimestampExpiredError",
    "RateLimitExceededError",
    "InternalErrorError",
    "ServiceUnavailableError",
    "CoreUnavailableError",
    "HeartBeatUnavailableError",
    "TransientError",
    "ConnectionTimeoutError",
    "ConnectionResetError",
    "QueueNotFoundError",
    "DuplicateFileError",
    "format_error_response",
    "format_success_response",
]
