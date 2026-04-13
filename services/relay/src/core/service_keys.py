"""
FIRS Service Key Container

Holds FIRS public key, CSID, and certificate for QR code encryption.
Immutable (frozen dataclass) — replaced atomically on cache refresh.
Memory-only, never written to disk.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict


@dataclass(frozen=True)
class ServiceKeys:
    """
    FIRS service keys for QR encryption and CSID.

    These are fetched from HeartBeat's config.db and cached in memory.
    Replaced atomically when module_cache.refresh() detects changes.
    """

    firs_public_key_pem: str
    csid: str
    csid_expires_at: datetime
    certificate: str
    loaded_at: datetime

    @property
    def is_csid_expired(self) -> bool:
        """Check if CSID has expired."""
        return datetime.now(timezone.utc) >= self.csid_expires_at

    @property
    def is_csid_expiring_soon(self) -> bool:
        """Check if CSID expires within 24 hours."""
        remaining = (self.csid_expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining < 86400  # 24 hours

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "ServiceKeys":
        """
        Parse from HeartBeat API response.

        Expected format:
            {
                "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\\n...",
                "csid": "ABC123-CSID-TOKEN",
                "csid_expires_at": "2026-06-01T00:00:00Z",
                "certificate": "base64-encoded-cert..."
            }
        """
        expires_str = data["csid_expires_at"]
        if isinstance(expires_str, str):
            # Handle both 'Z' suffix and '+00:00'
            expires_str = expires_str.replace("Z", "+00:00")
            csid_expires_at = datetime.fromisoformat(expires_str)
        else:
            csid_expires_at = expires_str

        # Ensure timezone-aware
        if csid_expires_at.tzinfo is None:
            csid_expires_at = csid_expires_at.replace(tzinfo=timezone.utc)

        return cls(
            firs_public_key_pem=data["firs_public_key_pem"],
            csid=data["csid"],
            csid_expires_at=csid_expires_at,
            certificate=data.get("certificate", ""),
            loaded_at=datetime.now(timezone.utc),
        )
