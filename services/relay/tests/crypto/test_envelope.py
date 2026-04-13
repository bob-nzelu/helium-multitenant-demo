"""
Tests for crypto/envelope.py — E2EE encrypt/decrypt
"""

import pytest
from nacl.public import PrivateKey, PublicKey, Box
from nacl.exceptions import CryptoError

from src.crypto.envelope import (
    ENVELOPE_VERSION,
    EncryptedEnvelope,
    decrypt,
    encrypt,
    encrypt_for_client,
)


class TestEncryptedEnvelope:
    """Test envelope serialization/deserialization."""

    def test_to_bytes_format(self):
        env = EncryptedEnvelope(
            version=1,
            ephemeral_public_key=b"\x00" * 32,
            ciphertext=b"\xff" * 50,
        )
        data = env.to_bytes()

        assert data[0] == 1                           # Version byte
        assert data[1:33] == b"\x00" * 32            # Ephemeral key
        assert data[33:] == b"\xff" * 50             # Ciphertext

    def test_from_bytes_roundtrip(self):
        env = EncryptedEnvelope(
            version=1,
            ephemeral_public_key=b"\xab" * 32,
            ciphertext=b"\xcd" * 100,
        )
        data = env.to_bytes()
        parsed = EncryptedEnvelope.from_bytes(data)

        assert parsed.version == env.version
        assert parsed.ephemeral_public_key == env.ephemeral_public_key
        assert parsed.ciphertext == env.ciphertext

    def test_from_bytes_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            EncryptedEnvelope.from_bytes(b"\x01" * 50)

    def test_from_bytes_wrong_version(self):
        # Version 99, then 32 bytes key, then 40 bytes ciphertext
        data = bytes([99]) + b"\x00" * 32 + b"\x00" * 40
        with pytest.raises(ValueError, match="Unsupported envelope version"):
            EncryptedEnvelope.from_bytes(data)

    def test_from_bytes_minimum_size(self):
        # Version 1 + 32-byte key + 40 bytes = exactly 73 bytes (minimum)
        data = bytes([1]) + b"\xaa" * 32 + b"\xbb" * 40
        env = EncryptedEnvelope.from_bytes(data)
        assert env.version == 1
        assert len(env.ephemeral_public_key) == 32
        assert len(env.ciphertext) == 40

    def test_frozen_dataclass(self):
        env = EncryptedEnvelope(
            version=1,
            ephemeral_public_key=b"\x00" * 32,
            ciphertext=b"\x00" * 50,
        )
        with pytest.raises(AttributeError):
            env.version = 2


class TestEncryptDecrypt:
    """Test full encrypt → decrypt cycle."""

    def test_basic_roundtrip(self, relay_keypair):
        relay_private, relay_public = relay_keypair
        plaintext = b"Hello, World! This is a test invoice."

        envelope_bytes, _ = encrypt(plaintext, relay_public)
        decrypted = decrypt(envelope_bytes, relay_private)

        assert decrypted == plaintext

    def test_large_payload(self, relay_keypair):
        """Test with a payload larger than typical invoice."""
        relay_private, relay_public = relay_keypair
        plaintext = b"x" * (5 * 1024 * 1024)  # 5 MB

        envelope_bytes, _ = encrypt(plaintext, relay_public)
        decrypted = decrypt(envelope_bytes, relay_private)

        assert decrypted == plaintext

    def test_empty_plaintext(self, relay_keypair):
        relay_private, relay_public = relay_keypair
        plaintext = b""

        envelope_bytes, _ = encrypt(plaintext, relay_public)
        decrypted = decrypt(envelope_bytes, relay_private)

        assert decrypted == plaintext

    def test_binary_data(self, relay_keypair):
        """Test with non-UTF-8 binary data."""
        relay_private, relay_public = relay_keypair
        plaintext = bytes(range(256)) * 10

        envelope_bytes, _ = encrypt(plaintext, relay_public)
        decrypted = decrypt(envelope_bytes, relay_private)

        assert decrypted == plaintext

    def test_envelope_starts_with_version(self, relay_keypair):
        _, relay_public = relay_keypair
        envelope_bytes, _ = encrypt(b"test", relay_public)
        assert envelope_bytes[0] == ENVELOPE_VERSION

    def test_different_ephemeral_keys_each_time(self, relay_keypair):
        _, relay_public = relay_keypair

        _, eph1 = encrypt(b"test", relay_public)
        _, eph2 = encrypt(b"test", relay_public)

        assert bytes(eph1) != bytes(eph2)

    def test_wrong_private_key_fails(self, relay_keypair):
        _, relay_public = relay_keypair
        wrong_private = PrivateKey.generate()

        envelope_bytes, _ = encrypt(b"secret", relay_public)

        with pytest.raises(CryptoError):
            decrypt(envelope_bytes, wrong_private)

    def test_tampered_ciphertext_fails(self, relay_keypair):
        relay_private, relay_public = relay_keypair

        envelope_bytes, _ = encrypt(b"secret data", relay_public)

        # Tamper with the ciphertext (last byte)
        tampered = bytearray(envelope_bytes)
        tampered[-1] ^= 0xFF
        tampered = bytes(tampered)

        with pytest.raises(CryptoError):
            decrypt(tampered, relay_private)

    def test_tampered_ephemeral_key_fails(self, relay_keypair):
        relay_private, relay_public = relay_keypair

        envelope_bytes, _ = encrypt(b"secret data", relay_public)

        # Tamper with the ephemeral public key (byte 5)
        tampered = bytearray(envelope_bytes)
        tampered[5] ^= 0xFF
        tampered = bytes(tampered)

        with pytest.raises(CryptoError):
            decrypt(tampered, relay_private)

    def test_decrypt_malformed_envelope(self, relay_private_key):
        with pytest.raises(ValueError):
            decrypt(b"too short", relay_private_key)


class TestEncryptForClient:
    """Test server-to-client encryption."""

    def test_bidirectional_encryption(self):
        """Test that Relay can encrypt for the client and client can decrypt."""
        relay_private = PrivateKey.generate()
        client_private = PrivateKey.generate()

        plaintext = b"Preview data from Core"

        # Relay encrypts for client
        encrypted = encrypt_for_client(
            plaintext,
            relay_private,
            client_private.public_key,
        )

        # Client decrypts
        box = Box(client_private, relay_private.public_key)
        decrypted = box.decrypt(encrypted)

        assert decrypted == plaintext


class TestMultipleClients:
    """Test that different clients can encrypt for the same Relay."""

    def test_two_clients_same_relay(self, relay_keypair):
        relay_private, relay_public = relay_keypair

        # Client A encrypts
        plaintext_a = b"Invoice from Client A"
        envelope_a, _ = encrypt(plaintext_a, relay_public)

        # Client B encrypts
        plaintext_b = b"Invoice from Client B"
        envelope_b, _ = encrypt(plaintext_b, relay_public)

        # Relay decrypts both
        assert decrypt(envelope_a, relay_private) == plaintext_a
        assert decrypt(envelope_b, relay_private) == plaintext_b
