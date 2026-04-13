"""
Relay-API Test Fixtures

Shared fixtures for all test modules.
"""

import pytest
from datetime import datetime, timezone

from nacl.public import PrivateKey

from src.config import RelayConfig
from src.clients.core import CoreClient
from tests.stub_heartbeat import StubHeartBeatClient


# ── Configuration ─────────────────────────────────────────────────────────


@pytest.fixture
def config():
    """Default test configuration."""
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=3,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
    )


# ── Crypto ────────────────────────────────────────────────────────────────


@pytest.fixture
def relay_keypair():
    """Generate a test X25519 keypair for Relay."""
    private_key = PrivateKey.generate()
    return private_key, private_key.public_key


@pytest.fixture
def relay_private_key(relay_keypair):
    """Relay's private key."""
    return relay_keypair[0]


@pytest.fixture
def relay_public_key(relay_keypair):
    """Relay's public key."""
    return relay_keypair[1]


# ── Auth ──────────────────────────────────────────────────────────────────


@pytest.fixture
def api_key_secrets():
    """Test API key → secret mapping."""
    return {
        "test-key-001": "secret-for-test-key-001",
        "test-key-002": "secret-for-test-key-002",
        "bulk-key-float": "secret-for-float-bulk",
    }


@pytest.fixture
def valid_timestamp():
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Clients ───────────────────────────────────────────────────────────────


@pytest.fixture
def core_client():
    """Test Core API client (stub)."""
    return CoreClient(
        core_api_url="http://localhost:8080",
        timeout=5.0,
        preview_timeout=10.0,
        max_attempts=2,
        trace_id="test-trace",
    )


@pytest.fixture
def heartbeat_client():
    """Test HeartBeat client (stub — no real HTTP)."""
    return StubHeartBeatClient(
        heartbeat_api_url="http://localhost:9000",
        timeout=5.0,
        max_attempts=2,
        trace_id="test-trace",
    )


# ── Test Files ────────────────────────────────────────────────────────────


@pytest.fixture
def sample_pdf():
    """Minimal PDF-like test data."""
    return b"%PDF-1.4 test content for invoice.pdf"


@pytest.fixture
def sample_xml():
    """Minimal XML test data."""
    return b'<?xml version="1.0"?><invoice><total>100.00</total></invoice>'


@pytest.fixture
def sample_csv():
    """Minimal CSV test data."""
    return b"item,qty,price\nWidget,10,5.00\nGadget,5,12.50"


@pytest.fixture
def sample_files(sample_pdf, sample_xml):
    """List of (filename, data) tuples for multi-file tests."""
    return [
        ("invoice_001.pdf", sample_pdf),
        ("data_feed.xml", sample_xml),
    ]


@pytest.fixture
def large_file():
    """File that exceeds default 10MB limit."""
    return b"x" * (11 * 1024 * 1024)  # 11 MB
