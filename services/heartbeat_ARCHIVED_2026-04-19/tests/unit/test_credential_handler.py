"""
Tests for Credential Handler (src/handlers/credential_handler.py)

Covers:
- Key generation (format, prefix, uniqueness)
- Secret generation (length, uniqueness)
- Hashing and verification (bcrypt round-trip)
- Full lifecycle: create → rotate → revoke
- Validation: active, revoked, inactive, expired
- Edge cases: bad secret, bad hash, nonexistent credential
"""

import pytest
from datetime import datetime, timezone, timedelta

from src.handlers.credential_handler import (
    generate_api_key,
    generate_api_secret,
    hash_secret,
    verify_secret,
    SERVICE_PREFIXES,
    create_credential,
    rotate_credential,
    revoke_credential,
    validate_api_key,
)
from src.database.registry import set_registry_database, reset_registry_database


# ── Key/Secret Generation ────────────────────────────────────────────


class TestKeyGeneration:
    """API key format and uniqueness."""

    def test_key_format_relay(self):
        """Relay key starts with rl_test_."""
        key = generate_api_key("relay", "test")
        assert key.startswith("rl_test_")
        # 32 hex chars after prefix
        parts = key.split("_", 2)
        assert len(parts) == 3
        assert len(parts[2]) == 32

    def test_key_format_core(self):
        """Core key starts with cr_."""
        key = generate_api_key("core", "prod")
        assert key.startswith("cr_prod_")

    def test_key_format_heartbeat(self):
        """HeartBeat key starts with hb_."""
        key = generate_api_key("heartbeat")
        assert key.startswith("hb_test_")

    def test_key_format_edge(self):
        """Edge key starts with ed_."""
        key = generate_api_key("edge")
        assert key.startswith("ed_test_")

    def test_key_format_float_sdk(self):
        """Float SDK key starts with fl_."""
        key = generate_api_key("float-sdk")
        assert key.startswith("fl_test_")

    def test_key_format_unknown_service(self):
        """Unknown service gets xx_ prefix."""
        key = generate_api_key("unknown-service")
        assert key.startswith("xx_test_")

    def test_keys_are_unique(self):
        """Two calls produce different keys."""
        k1 = generate_api_key("relay")
        k2 = generate_api_key("relay")
        assert k1 != k2

    def test_all_prefixes_registered(self):
        """All expected services have prefixes."""
        expected = {"heartbeat", "relay", "core", "edge", "float-sdk"}
        assert set(SERVICE_PREFIXES.keys()) == expected


class TestSecretGeneration:
    """API secret format and uniqueness."""

    def test_secret_length(self):
        """Secret is ~64 chars (48 bytes URL-safe base64)."""
        secret = generate_api_secret()
        assert len(secret) >= 60  # token_urlsafe(48) -> ~64 chars

    def test_secrets_are_unique(self):
        """Two calls produce different secrets."""
        s1 = generate_api_secret()
        s2 = generate_api_secret()
        assert s1 != s2

    def test_secret_is_url_safe(self):
        """Secret contains only URL-safe chars."""
        secret = generate_api_secret()
        import re
        assert re.match(r'^[A-Za-z0-9_-]+$', secret)


# ── Hashing and Verification ────────────────────────────────────────


class TestHashAndVerify:
    """bcrypt hash/verify round-trip."""

    def test_hash_returns_bcrypt_string(self):
        """Hash output starts with $2b$12$."""
        h = hash_secret("my-secret-key")
        assert h.startswith("$2b$12$")

    def test_verify_correct_secret(self):
        """Correct secret verifies True."""
        secret = "test-secret-123"
        h = hash_secret(secret)
        assert verify_secret(secret, h) is True

    def test_verify_wrong_secret(self):
        """Wrong secret verifies False."""
        h = hash_secret("correct-secret")
        assert verify_secret("wrong-secret", h) is False

    def test_verify_malformed_hash(self):
        """Malformed hash returns False (not crash)."""
        assert verify_secret("any-secret", "not-a-bcrypt-hash") is False

    def test_verify_empty_secret(self):
        """Empty secret against valid hash returns False."""
        h = hash_secret("real-secret")
        assert verify_secret("", h) is False

    def test_same_secret_different_hashes(self):
        """bcrypt produces different hashes for the same secret (salted)."""
        h1 = hash_secret("same-secret")
        h2 = hash_secret("same-secret")
        assert h1 != h2  # Different salts
        assert verify_secret("same-secret", h1) is True
        assert verify_secret("same-secret", h2) is True


# ── Credential Lifecycle (async) ─────────────────────────────────────


class TestCredentialLifecycle:
    """Full lifecycle: create → rotate → revoke via async handlers."""

    @pytest.mark.asyncio
    async def test_create_credential(self, registry_db):
        """Create returns key + plaintext secret."""
        set_registry_database(registry_db)

        result = await create_credential(
            service_name="relay",
            issued_to="relay-bulk-1",
            permissions=["blob.write"],
        )

        assert "credential_id" in result
        assert result["api_key"].startswith("rl_test_")
        assert "api_secret" in result  # Plaintext returned at creation
        assert result["service_name"] == "relay"
        assert result["issued_to"] == "relay-bulk-1"

        # Verify it's in the DB
        cred = registry_db.get_credential_by_key(result["api_key"])
        assert cred is not None
        assert cred["status"] == "active"

        # Verify rotation log
        logs = registry_db.execute_query(
            "SELECT * FROM key_rotation_log WHERE credential_id = ?",
            (result["credential_id"],),
        )
        assert len(logs) == 1
        assert logs[0]["action"] == "created"

    @pytest.mark.asyncio
    async def test_rotate_credential(self, registry_db):
        """Rotate produces new key + secret, logs the event."""
        set_registry_database(registry_db)

        created = await create_credential("relay", "relay-1")
        old_key = created["api_key"]

        rotated = await rotate_credential(
            credential_id=created["credential_id"],
            performed_by="admin",
            reason="Scheduled rotation",
        )

        assert rotated["new_api_key"] != old_key
        assert rotated["new_api_key"].startswith("rl_test_")
        assert "new_api_secret" in rotated

        # Old key gone
        assert registry_db.get_credential_by_key(old_key) is None
        # New key present
        assert registry_db.get_credential_by_key(rotated["new_api_key"]) is not None

        # Rotation logged
        logs = registry_db.execute_query(
            "SELECT * FROM key_rotation_log WHERE credential_id = ? ORDER BY id",
            (created["credential_id"],),
        )
        assert len(logs) == 2  # created + rotated
        assert logs[1]["action"] == "rotated"
        assert logs[1]["old_key_prefix"] == old_key[:8]

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_credential(self, registry_db):
        """Rotating a nonexistent credential raises ValueError."""
        set_registry_database(registry_db)
        with pytest.raises(ValueError, match="not found"):
            await rotate_credential("nonexistent-id")

    @pytest.mark.asyncio
    async def test_revoke_credential(self, registry_db):
        """Revoke sets status to 'revoked' and logs it."""
        set_registry_database(registry_db)

        created = await create_credential("edge", "edge-primary")
        result = await revoke_credential(
            credential_id=created["credential_id"],
            performed_by="admin",
            reason="Compromised",
        )

        assert result["status"] == "revoked"

        cred = registry_db.get_credential_by_key(created["api_key"])
        assert cred["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_credential(self, registry_db):
        """Revoking a nonexistent credential raises ValueError."""
        set_registry_database(registry_db)
        with pytest.raises(ValueError, match="not found"):
            await revoke_credential("nonexistent-id")


# ── Credential Validation (async) ───────────────────────────────────


class TestCredentialValidation:
    """validate_api_key checks key, secret, status, and expiry."""

    @pytest.mark.asyncio
    async def test_validate_active_credential(self, registry_db):
        """Valid key + secret returns credential info."""
        set_registry_database(registry_db)

        created = await create_credential(
            "relay", "relay-1", permissions=["blob.write"],
        )

        result = await validate_api_key(created["api_key"], created["api_secret"])
        assert result["credential_id"] == created["credential_id"]
        assert result["service_name"] == "relay"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_validate_wrong_secret(self, registry_db):
        """Correct key but wrong secret raises ValueError."""
        set_registry_database(registry_db)

        created = await create_credential("relay", "relay-1")
        with pytest.raises(ValueError, match="Invalid API secret"):
            await validate_api_key(created["api_key"], "wrong-secret")

    @pytest.mark.asyncio
    async def test_validate_nonexistent_key(self, registry_db):
        """Nonexistent key raises ValueError."""
        set_registry_database(registry_db)
        with pytest.raises(ValueError, match="Invalid API key"):
            await validate_api_key("nonexistent", "any-secret")

    @pytest.mark.asyncio
    async def test_validate_revoked_credential(self, registry_db):
        """Revoked credential raises ValueError."""
        set_registry_database(registry_db)

        created = await create_credential("relay", "relay-1")
        await revoke_credential(created["credential_id"])

        with pytest.raises(ValueError, match="revoked"):
            await validate_api_key(created["api_key"], created["api_secret"])

    @pytest.mark.asyncio
    async def test_validate_inactive_credential(self, registry_db):
        """Inactive credential raises ValueError."""
        set_registry_database(registry_db)

        created = await create_credential("relay", "relay-1")
        registry_db.update_credential_status(created["credential_id"], "inactive")

        with pytest.raises(ValueError, match="inactive"):
            await validate_api_key(created["api_key"], created["api_secret"])

    @pytest.mark.asyncio
    async def test_validate_expired_credential(self, registry_db):
        """Expired credential raises ValueError."""
        set_registry_database(registry_db)

        # Create with expiry in the past
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        created = await create_credential(
            "relay", "relay-1", expires_at=past,
        )

        with pytest.raises(ValueError, match="expired"):
            await validate_api_key(created["api_key"], created["api_secret"])

    @pytest.mark.asyncio
    async def test_validate_updates_last_used(self, registry_db):
        """Successful validation updates last_used_at."""
        set_registry_database(registry_db)

        created = await create_credential("relay", "relay-1")

        # Before validation, last_used_at is None
        cred = registry_db.get_credential_by_key(created["api_key"])
        assert cred["last_used_at"] is None

        await validate_api_key(created["api_key"], created["api_secret"])

        cred = registry_db.get_credential_by_key(created["api_key"])
        assert cred["last_used_at"] is not None
