"""
HeartBeat JWT Manager — Ed25519 Keypair + JWT Signing/Verification

Manages the Ed25519 keypair used to sign and verify HeartBeat JWTs.
Generates the keypair on first run if it does not exist.

JWT structure matches Part 4 Section 1 exactly:
    sub, tenant_id, role, permissions, last_auth_at,
    issued_at, expires_at, jti

Also includes standard iat/exp claims (UNIX timestamps) for
automatic expiration validation.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from authlib.jose import jwt as authlib_jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization


logger = logging.getLogger(__name__)


class JWTManager:
    """
    Ed25519 JWT manager for HeartBeat.

    Handles:
    - Keypair generation on first run
    - Loading existing keys from PEM files
    - JWT signing (EdDSA / Ed25519)
    - JWT verification and decoding
    """

    def __init__(
        self,
        private_key_path: str,
        public_key_path: str,
    ):
        self.private_key_path = private_key_path
        self.public_key_path = public_key_path

        self._private_key_pem: bytes = b""
        self._public_key_pem: bytes = b""

        self._load_or_generate_keys()

    # ── Key Management ─────────────────────────────────────────────

    def _load_or_generate_keys(self) -> None:
        """Load existing Ed25519 keys or generate a new pair."""
        if (
            os.path.exists(self.private_key_path)
            and os.path.exists(self.public_key_path)
        ):
            # Load existing keys
            with open(self.private_key_path, "rb") as f:
                self._private_key_pem = f.read()
            with open(self.public_key_path, "rb") as f:
                self._public_key_pem = f.read()

            logger.info(
                f"Loaded Ed25519 JWT keys from {self.private_key_path}"
            )
        else:
            # Generate new keypair
            logger.info("Generating new Ed25519 JWT keypair...")
            self._generate_keypair()

    def _generate_keypair(self) -> None:
        """Generate a new Ed25519 keypair and write to disk."""
        private_key = Ed25519PrivateKey.generate()

        self._private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._public_key_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        # Write to disk
        os.makedirs(os.path.dirname(self.private_key_path), exist_ok=True)

        with open(self.private_key_path, "wb") as f:
            f.write(self._private_key_pem)
        with open(self.public_key_path, "wb") as f:
            f.write(self._public_key_pem)

        logger.info(
            f"Ed25519 keypair written to {self.private_key_path} "
            f"and {self.public_key_path}"
        )

    @property
    def public_key_pem(self) -> bytes:
        """Public key PEM bytes (for distribution to other services)."""
        return self._public_key_pem

    # ── JWT Operations ─────────────────────────────────────────────

    def create_token(self, payload: Dict[str, Any]) -> str:
        """
        Sign a JWT with HeartBeat's Ed25519 private key.

        The payload should contain Part 4 Section 1 fields:
            sub, tenant_id, role, permissions, last_auth_at,
            issued_at, expires_at, jti

        Standard iat/exp UNIX timestamps are added automatically.

        Args:
            payload: JWT claims dict.

        Returns:
            Signed JWT string.
        """
        header = {"alg": "EdDSA", "typ": "JWT"}

        # Add standard UNIX timestamps alongside ISO strings
        if "issued_at" in payload and "iat" not in payload:
            issued_dt = datetime.fromisoformat(
                payload["issued_at"].replace("Z", "+00:00")
            )
            payload["iat"] = int(issued_dt.timestamp())

        if "expires_at" in payload and "exp" not in payload:
            expires_dt = datetime.fromisoformat(
                payload["expires_at"].replace("Z", "+00:00")
            )
            payload["exp"] = int(expires_dt.timestamp())

        token = authlib_jwt.encode(header, payload, self._private_key_pem)

        return token.decode("utf-8") if isinstance(token, bytes) else token

    def verify_token(self, token: str) -> Dict[str, Any]:
        """
        Verify a JWT signature and decode claims.

        Validates:
        - Ed25519 signature is valid
        - Token has not expired (via exp claim)

        Args:
            token: JWT string.

        Returns:
            Decoded claims dict.

        Raises:
            authlib.jose.errors.DecodeError: Invalid signature.
            authlib.jose.errors.ExpiredTokenError: Token expired.
        """
        claims = authlib_jwt.decode(token, self._public_key_pem)
        claims.validate()
        return dict(claims)

    def decode_token_unsafe(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Decode a JWT without full validation.

        Used for extracting claims from potentially expired tokens
        (e.g., to identify the session for revocation).

        Returns None if the token is malformed.
        """
        try:
            claims = authlib_jwt.decode(token, self._public_key_pem)
            return dict(claims)
        except Exception:
            return None


# ── Singleton ──────────────────────────────────────────────────────

_jwt_manager_instance: Optional[JWTManager] = None


def get_jwt_manager(
    private_key_path: Optional[str] = None,
    public_key_path: Optional[str] = None,
) -> JWTManager:
    """Get singleton JWTManager instance."""
    global _jwt_manager_instance

    if _jwt_manager_instance is None:
        if private_key_path is None or public_key_path is None:
            raise ValueError(
                "private_key_path and public_key_path required "
                "on first call to get_jwt_manager()"
            )
        _jwt_manager_instance = JWTManager(
            private_key_path=private_key_path,
            public_key_path=public_key_path,
        )

    return _jwt_manager_instance


def reset_jwt_manager() -> None:
    """Reset singleton instance (for testing)."""
    global _jwt_manager_instance
    _jwt_manager_instance = None
