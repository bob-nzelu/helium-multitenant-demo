"""
Encrypted Envelope — X25519 + XSalsa20-Poly1305 (NaCl Box)

All data between Float/external clients and Relay is encrypted with this envelope.
Cloudflare (or any proxy) terminates TLS but cannot read the inner encrypted data.

Envelope wire format (binary):
    [1 byte version][32 bytes ephemeral_public_key][encrypted_payload]

The encrypted_payload is a NaCl sealed box:
    XSalsa20-Poly1305(shared_secret, nonce, plaintext)
    where shared_secret = X25519(ephemeral_private, relay_public)

NaCl's Box.encrypt() prepends the 24-byte nonce automatically,
so the wire format is simple and doesn't need separate nonce fields.

Client-side:
    envelope_bytes = encrypt(plaintext, relay_public_key)
    → POST body

Relay-side:
    plaintext = decrypt(envelope_bytes, relay_private_key)
    → process normally
"""

import logging
from dataclasses import dataclass
from typing import Tuple

from nacl.public import Box, PrivateKey, PublicKey, SealedBox

logger = logging.getLogger(__name__)

# Protocol version (bump on breaking changes)
ENVELOPE_VERSION = 1


@dataclass(frozen=True)
class EncryptedEnvelope:
    """
    Parsed encrypted envelope.

    Attributes:
        version: Protocol version (currently 1).
        ephemeral_public_key: Client's ephemeral X25519 public key (32 bytes).
        ciphertext: NaCl Box encrypted payload (includes nonce + tag).
    """
    version: int
    ephemeral_public_key: bytes
    ciphertext: bytes

    def to_bytes(self) -> bytes:
        """Serialize to wire format."""
        return (
            bytes([self.version])
            + self.ephemeral_public_key
            + self.ciphertext
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedEnvelope":
        """
        Parse wire format into EncryptedEnvelope.

        Args:
            data: Raw bytes from request body.

        Returns:
            Parsed EncryptedEnvelope.

        Raises:
            ValueError: If data is too short or has unsupported version.
        """
        # Minimum: 1 (version) + 32 (ephemeral key) + 40 (smallest NaCl box)
        if len(data) < 73:
            raise ValueError(
                f"Envelope too short: {len(data)} bytes (minimum 73)"
            )

        version = data[0]
        if version != ENVELOPE_VERSION:
            raise ValueError(
                f"Unsupported envelope version: {version} (expected {ENVELOPE_VERSION})"
            )

        ephemeral_public_key = data[1:33]
        ciphertext = data[33:]

        return cls(
            version=version,
            ephemeral_public_key=ephemeral_public_key,
            ciphertext=ciphertext,
        )


def encrypt(plaintext: bytes, relay_public_key: PublicKey) -> Tuple[bytes, PrivateKey]:
    """
    Encrypt plaintext for Relay using ephemeral X25519 key exchange.

    This is what clients (Float, external API) call before sending data.

    Args:
        plaintext: Raw data to encrypt.
        relay_public_key: Relay server's X25519 public key.

    Returns:
        (envelope_bytes, ephemeral_private_key) — envelope is the wire format,
        ephemeral key is returned for testing/debugging only.
    """
    # Generate ephemeral keypair for this request
    ephemeral_private = PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key

    # Create NaCl Box with shared secret
    box = Box(ephemeral_private, relay_public_key)

    # Encrypt (nonce is auto-generated and prepended by NaCl)
    ciphertext = box.encrypt(plaintext)

    envelope = EncryptedEnvelope(
        version=ENVELOPE_VERSION,
        ephemeral_public_key=bytes(ephemeral_public),
        ciphertext=ciphertext,
    )

    logger.debug(
        f"Encrypted {len(plaintext)} bytes → {len(envelope.to_bytes())} byte envelope"
    )

    return envelope.to_bytes(), ephemeral_private


def decrypt(envelope_bytes: bytes, relay_private_key: PrivateKey) -> bytes:
    """
    Decrypt an incoming encrypted envelope.

    This is what Relay calls when processing an encrypted request.

    Args:
        envelope_bytes: Raw wire-format bytes from request body.
        relay_private_key: Relay server's X25519 private key.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        ValueError: If envelope is malformed.
        nacl.exceptions.CryptoError: If decryption fails (wrong key, tampered data).
    """
    envelope = EncryptedEnvelope.from_bytes(envelope_bytes)

    # Reconstruct client's ephemeral public key
    ephemeral_public = PublicKey(envelope.ephemeral_public_key)

    # Create NaCl Box with shared secret
    box = Box(relay_private_key, ephemeral_public)

    # Decrypt (NaCl extracts nonce from prepended bytes)
    plaintext = box.decrypt(envelope.ciphertext)

    logger.debug(
        f"Decrypted {len(envelope_bytes)} byte envelope → {len(plaintext)} bytes"
    )

    return plaintext


def encrypt_for_client(
    plaintext: bytes,
    relay_private_key: PrivateKey,
    client_public_key: PublicKey,
) -> bytes:
    """
    Encrypt a response back to the client (for bidirectional E2EE).

    Used when Relay needs to send encrypted data back (e.g., preview results).

    Args:
        plaintext: Response data to encrypt.
        relay_private_key: Relay's private key.
        client_public_key: Client's public key (from request header or registration).

    Returns:
        Encrypted bytes (NaCl Box format with auto-generated nonce).
    """
    box = Box(relay_private_key, client_public_key)
    return box.encrypt(plaintext)
