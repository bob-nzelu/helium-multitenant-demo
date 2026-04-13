"""
X25519 Key Management

Generate, load, and save X25519 keypairs for end-to-end encryption.
Uses PyNaCl (libsodium) for all cryptographic operations.

Key lifecycle:
    1. Production: Load from RELAY_PRIVATE_KEY_PATH (file on disk or Docker secret)
    2. Development: Auto-generate ephemeral keypair if no key file exists
    3. Rotation: Generate new keypair, serve both old+new during transition

Key format on disk: 32-byte raw private key, hex-encoded in a text file.
Public key is always derived from private key (never stored separately).
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from nacl.public import PrivateKey, PublicKey

logger = logging.getLogger(__name__)


def generate_keypair() -> Tuple[PrivateKey, PublicKey]:
    """
    Generate a new X25519 keypair.

    Returns:
        (private_key, public_key) tuple.
    """
    private_key = PrivateKey.generate()
    public_key = private_key.public_key
    logger.info("Generated new X25519 keypair")
    return private_key, public_key


def save_private_key(private_key: PrivateKey, path: str) -> None:
    """
    Save private key to disk as hex-encoded text.

    Args:
        private_key: X25519 private key.
        path: File path to write (created if not exists, parent dirs created).
    """
    key_path = Path(path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(bytes(private_key).hex())
    # Restrict permissions (owner-only read/write)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows doesn't support Unix permissions
    logger.info(f"Private key saved to {path}")


def load_private_key(path: str) -> PrivateKey:
    """
    Load private key from hex-encoded text file.

    Args:
        path: File path containing hex-encoded 32-byte private key.

    Returns:
        X25519 PrivateKey.

    Raises:
        FileNotFoundError: If key file doesn't exist.
        ValueError: If key file content is invalid.
    """
    key_path = Path(path)
    if not key_path.exists():
        raise FileNotFoundError(f"Private key not found: {path}")

    hex_key = key_path.read_text().strip()
    try:
        key_bytes = bytes.fromhex(hex_key)
    except ValueError as e:
        raise ValueError(f"Invalid hex in key file {path}: {e}") from e

    if len(key_bytes) != 32:
        raise ValueError(
            f"Private key must be 32 bytes, got {len(key_bytes)} in {path}"
        )

    logger.info(f"Private key loaded from {path}")
    return PrivateKey(key_bytes)


def load_public_key(hex_key: str) -> PublicKey:
    """
    Load a public key from hex string.

    Used by clients to load the Relay server's public key
    (distributed during API key onboarding).

    Args:
        hex_key: Hex-encoded 32-byte public key.

    Returns:
        X25519 PublicKey.

    Raises:
        ValueError: If hex string is invalid or wrong length.
    """
    try:
        key_bytes = bytes.fromhex(hex_key.strip())
    except ValueError as e:
        raise ValueError(f"Invalid hex in public key: {e}") from e

    if len(key_bytes) != 32:
        raise ValueError(
            f"Public key must be 32 bytes, got {len(key_bytes)}"
        )

    return PublicKey(key_bytes)


def get_public_key_hex(private_key: PrivateKey) -> str:
    """
    Get the hex-encoded public key from a private key.

    This is the value distributed to clients during onboarding.

    Args:
        private_key: X25519 private key.

    Returns:
        Hex-encoded 32-byte public key string.
    """
    return bytes(private_key.public_key).hex()


def load_or_generate(path: Optional[str] = None) -> Tuple[PrivateKey, PublicKey]:
    """
    Load keypair from disk, or generate ephemeral if no path given.

    This is the main entry point used by RelayConfig at startup:
    - Production: path is set via RELAY_PRIVATE_KEY_PATH → load from disk.
    - Development: path is empty → generate ephemeral keypair (logged as warning).

    Args:
        path: Optional file path to private key. Empty/None = ephemeral.

    Returns:
        (private_key, public_key) tuple.
    """
    if path:
        private_key = load_private_key(path)
        return private_key, private_key.public_key

    logger.warning(
        "No RELAY_PRIVATE_KEY_PATH set — using ephemeral keypair. "
        "This is acceptable for development but NOT for production."
    )
    return generate_keypair()
