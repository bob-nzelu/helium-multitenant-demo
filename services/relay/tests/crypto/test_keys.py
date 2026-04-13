"""
Tests for crypto/keys.py — X25519 key management
"""

import os
import pytest
from pathlib import Path

from nacl.public import PrivateKey, PublicKey

from src.crypto.keys import (
    generate_keypair,
    get_public_key_hex,
    load_or_generate,
    load_private_key,
    load_public_key,
    save_private_key,
)


class TestGenerateKeypair:
    """Test keypair generation."""

    def test_generates_valid_keypair(self):
        private, public = generate_keypair()
        assert isinstance(private, PrivateKey)
        assert isinstance(public, PublicKey)
        assert len(bytes(private)) == 32
        assert len(bytes(public)) == 32

    def test_generates_unique_keypairs(self):
        pair1 = generate_keypair()
        pair2 = generate_keypair()
        assert bytes(pair1[0]) != bytes(pair2[0])
        assert bytes(pair1[1]) != bytes(pair2[1])

    def test_public_key_derives_from_private(self):
        private, public = generate_keypair()
        assert bytes(private.public_key) == bytes(public)


class TestSaveLoadPrivateKey:
    """Test private key persistence."""

    def test_save_and_load_roundtrip(self, tmp_path):
        private, _ = generate_keypair()
        key_file = str(tmp_path / "relay_key.hex")

        save_private_key(private, key_file)
        loaded = load_private_key(key_file)

        assert bytes(loaded) == bytes(private)

    def test_save_creates_parent_dirs(self, tmp_path):
        private, _ = generate_keypair()
        key_file = str(tmp_path / "deep" / "nested" / "key.hex")

        save_private_key(private, key_file)
        assert Path(key_file).exists()

    def test_save_file_is_hex(self, tmp_path):
        private, _ = generate_keypair()
        key_file = str(tmp_path / "key.hex")

        save_private_key(private, key_file)
        content = Path(key_file).read_text()

        assert len(content) == 64  # 32 bytes * 2 hex chars
        assert all(c in "0123456789abcdef" for c in content)

    def test_load_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_private_key("/nonexistent/path/key.hex")

    def test_load_invalid_hex(self, tmp_path):
        key_file = tmp_path / "bad.hex"
        key_file.write_text("not_valid_hex_data!")

        with pytest.raises(ValueError, match="Invalid hex"):
            load_private_key(str(key_file))

    def test_load_wrong_length(self, tmp_path):
        key_file = tmp_path / "short.hex"
        key_file.write_text("abcd1234")  # 4 bytes, not 32

        with pytest.raises(ValueError, match="32 bytes"):
            load_private_key(str(key_file))

    def test_load_strips_whitespace(self, tmp_path):
        private, _ = generate_keypair()
        key_file = tmp_path / "key.hex"
        # Write with trailing newline and spaces
        key_file.write_text(f"  {bytes(private).hex()}  \n")

        loaded = load_private_key(str(key_file))
        assert bytes(loaded) == bytes(private)


class TestLoadPublicKey:
    """Test public key loading from hex."""

    def test_load_valid_hex(self):
        private, expected_public = generate_keypair()
        hex_key = bytes(expected_public).hex()

        loaded = load_public_key(hex_key)
        assert bytes(loaded) == bytes(expected_public)

    def test_load_with_whitespace(self):
        _, public = generate_keypair()
        hex_key = f"  {bytes(public).hex()}  "

        loaded = load_public_key(hex_key)
        assert bytes(loaded) == bytes(public)

    def test_load_invalid_hex(self):
        with pytest.raises(ValueError, match="Invalid hex"):
            load_public_key("zzzz_not_hex")

    def test_load_wrong_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            load_public_key("aabbccdd")  # Only 4 bytes


class TestGetPublicKeyHex:
    """Test public key hex export."""

    def test_returns_hex_string(self):
        private, public = generate_keypair()
        hex_str = get_public_key_hex(private)

        assert len(hex_str) == 64
        assert all(c in "0123456789abcdef" for c in hex_str)
        assert hex_str == bytes(public).hex()


class TestLoadOrGenerate:
    """Test the main entry point."""

    def test_with_path_loads_from_disk(self, tmp_path):
        # Save a key first
        original_private, _ = generate_keypair()
        key_file = str(tmp_path / "key.hex")
        save_private_key(original_private, key_file)

        # Load it
        private, public = load_or_generate(key_file)
        assert bytes(private) == bytes(original_private)
        assert bytes(public) == bytes(original_private.public_key)

    def test_without_path_generates_ephemeral(self):
        private, public = load_or_generate(None)
        assert isinstance(private, PrivateKey)
        assert isinstance(public, PublicKey)

    def test_empty_string_generates_ephemeral(self):
        private, public = load_or_generate("")
        assert isinstance(private, PrivateKey)
        assert isinstance(public, PublicKey)

    def test_nonexistent_path_raises(self):
        with pytest.raises(FileNotFoundError):
            load_or_generate("/nonexistent/key.hex")
