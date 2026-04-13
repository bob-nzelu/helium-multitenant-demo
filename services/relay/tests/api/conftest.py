"""
Shared fixtures for tests/api/ — mocks HeartBeat HTTP endpoints.

All API tests use LifespanManager which calls real HeartBeatClient methods
during startup (module_cache.load_all → get_transforma_config) and during
health checks (heartbeat.health_check). These require HeartBeat to be running.

This conftest provides an autouse fixture that mocks HeartBeat's HTTP
endpoints with respx, so all API tests work without a running HeartBeat.
"""

from datetime import datetime, timezone

import pytest
import respx
import httpx


# ── Stub HeartBeat response data ─────────────────────────────────────────

TRANSFORMA_CONFIG_RESPONSE = {
    "modules": [
        {
            "module_name": "irn_generator",
            "source_code": (
                'def generate_irn(invoice_data: dict) -> str:\n'
                '    inv_num = invoice_data.get("invoice_number", "000")\n'
                '    tin = invoice_data.get("tin", "0000000000")\n'
                '    import hashlib, time\n'
                '    ts = str(int(time.time()))\n'
                '    hash_part = hashlib.sha256(f"{tin}{inv_num}{ts}".encode()).hexdigest()[:8].upper()\n'
                '    return f"{tin[:4]}-{hash_part}-{ts[-6:]}"\n'
            ),
            "version": "1.0.0-stub",
            "checksum": "sha256:stub_irn_checksum",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "module_name": "qr_generator",
            "source_code": (
                'import base64, json\n'
                'from datetime import datetime\n'
                '\n'
                'def generate_qr_data(irn: str, keys=None) -> str:\n'
                '    payload = {"irn": irn, "demo": True,\n'
                '               "timestamp": datetime.now().isoformat()}\n'
                '    return base64.b64encode(\n'
                '        json.dumps(payload).encode()\n'
                '    ).decode()\n'
                '\n'
                'def create_qr_image_bytes(qr_data: str) -> bytes:\n'
                '    return b"PNG_STUB_QR_IMAGE"\n'
            ),
            "version": "1.0.0-stub",
            "checksum": "sha256:stub_qr_checksum",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    ],
    "service_keys": {
        "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\nSTUB_KEY\n-----END PUBLIC KEY-----",
        "csid": "STUB-CSID-TOKEN",
        "csid_expires_at": "2030-01-01T00:00:00Z",
        "certificate": "c3R1Yl9jZXJ0",
    },
}


# ── Autouse fixture for all API tests ────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_heartbeat_http():
    """
    Mock all HeartBeat HTTP endpoints for API tests.

    This runs automatically for every test in tests/api/.
    The respx router intercepts httpx calls to localhost:9000.
    """
    with respx.mock:
        # Startup: module_cache.load_all() calls this
        respx.get("http://localhost:9000/api/platform/transforma/config").mock(
            return_value=httpx.Response(200, json=TRANSFORMA_CONFIG_RESPONSE)
        )

        # Health check: GET /health
        respx.get("http://localhost:9000/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        # Ingestion pipeline: dedup check, blob write, dedup record, register,
        # daily limit, audit, metrics — mock all with success responses
        respx.get("http://localhost:9000/api/dedup/check").mock(
            return_value=httpx.Response(200, json={
                "is_duplicate": False,
                "file_hash": "mock",
                "original_queue_id": None,
            })
        )

        respx.post("http://localhost:9000/api/blobs/write").mock(
            return_value=httpx.Response(200, json={
                "blob_uuid": "mock-blob-uuid",
                "blob_path": "/files_blob/mock-blob",
                "file_size_bytes": 100,
                "file_hash": "mock-hash",
                "status": "uploaded",
            })
        )

        respx.post("http://localhost:9000/api/dedup/record").mock(
            return_value=httpx.Response(201, json={
                "file_hash": "mock",
                "queue_id": "mock",
                "status": "recorded",
            })
        )

        respx.post("http://localhost:9000/api/blobs/register").mock(
            return_value=httpx.Response(201, json={
                "blob_uuid": "mock",
                "status": "registered",
                "tracking_id": "mock-track",
            })
        )

        respx.get("http://localhost:9000/api/limits/daily").mock(
            return_value=httpx.Response(200, json={
                "company_id": "mock",
                "files_today": 0,
                "daily_limit": 500,
                "limit_reached": False,
                "remaining": 500,
            })
        )

        respx.post("http://localhost:9000/api/audit/log").mock(
            return_value=httpx.Response(201, json={
                "status": "ok",
                "audit_id": "mock-audit",
            })
        )

        respx.post("http://localhost:9000/api/metrics/report").mock(
            return_value=httpx.Response(201, json={"status": "ok"})
        )

        yield
