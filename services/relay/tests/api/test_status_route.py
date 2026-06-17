"""
Tests for POST /api/status — Q37 Gap #5.

Covers:
    - Query by batch_id  → results[] (N entries)
    - Query by transaction_id → results[0..1]
    - Query by irn → results[0..1]
    - Unknown id → empty results[] (HTTP 200, not 404)
    - No selector → 400 VALIDATION_FAILED
    - Multiple selectors → 400 VALIDATION_FAILED
    - HB returns [] → empty results
    - Core returns None → firs_status=null (graceful)
    - Authentication required (missing bearer → 401)
    - Bad HMAC signature → 401
    - Invalid JSON body → 400
    - Empty body → 400

Auth: HMAC over the JSON body (same pattern as test_finalize_route.py).
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from src.api.app import create_app
from src.config import RelayConfig
from src.core.auth import compute_signature
from src.services.status_service import StatusService


TEST_API_KEY = "test-key-001"
TEST_SECRET = "secret-001"


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def test_config():
    return RelayConfig(
        host="127.0.0.1",
        port=8082,
        instance_id="relay-test",
        require_encryption=False,
        max_files=5,
        max_file_size_mb=10.0,
        max_total_size_mb=30.0,
        allowed_extensions=(".pdf", ".xml", ".json", ".csv", ".xlsx"),
        internal_service_token="test-internal-token",
        heartbeat_api_key="test-relay-key",
        heartbeat_s2s_signing_key="0123456789abcdef" * 4,
    )


@pytest.fixture
def test_secrets():
    return {TEST_API_KEY: TEST_SECRET}


def _hmac_headers_for_body(body: bytes) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sig = compute_signature(TEST_API_KEY, ts, body, TEST_SECRET)
    return {
        "X-API-Key": TEST_API_KEY,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


def _post_status_args(payload: dict) -> tuple:
    body = json.dumps(payload).encode("utf-8")
    return body, _hmac_headers_for_body(body)


class _MockHB:
    """Mock HeartBeat client for status tests."""

    def __init__(
        self,
        by_batch: Optional[List[Dict[str, Any]]] = None,
        by_txn: Optional[Dict[str, Any]] = None,
        by_irn: Optional[Dict[str, Any]] = None,
    ):
        self._by_batch = by_batch if by_batch is not None else []
        self._by_txn = by_txn
        self._by_irn = by_irn
        self.calls: List[tuple] = []

    async def get_blob_status_by_batch(self, batch_id, tenant_id):
        self.calls.append(("by_batch", batch_id))
        return self._by_batch

    async def get_blob_status_by_transaction_id(self, transaction_id, tenant_id):
        self.calls.append(("by_txn", transaction_id))
        return self._by_txn

    async def get_blob_status_by_irn(self, irn, tenant_id):
        self.calls.append(("by_irn", irn))
        return self._by_irn


class _MockCore:
    """Mock Core client — returns None (stub) unless overridden."""

    def __init__(self, result: Optional[Dict[str, Any]] = None):
        self._result = result
        self.calls: List[tuple] = []

    async def get_invoice_status(self, transaction_id, irn, tenant_id):
        self.calls.append(("get_invoice_status", transaction_id, irn))
        return self._result


def _make_hb_record(**kwargs) -> Dict[str, Any]:
    """Build a minimal HB blob record suitable for StatusEntry construction."""
    defaults = {
        "transaction_id": "TXN001",
        "irn": None,
        "batch_id": "BATCH001",
        "status": "pending",
        "is_duplicate": False,
        "received_at": "2026-06-17T09:30:12Z",
        "processed_at": None,
    }
    defaults.update(kwargs)
    return defaults


@pytest.fixture
async def client_with_mock_svc(test_config, test_secrets):
    """App with StatusService swapped for a controlled mock after lifespan."""
    app = create_app(config=test_config, api_key_secrets=test_secrets)
    async with LifespanManager(app):
        yield app


async def _post_status(app, payload: dict):
    body, headers = _post_status_args(payload)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post("/api/status", content=body, headers=headers)


# ── Selector validation ───────────────────────────────────────────────────


class TestStatusSelectorValidation:
    @pytest.mark.asyncio
    async def test_no_selector_returns_400(self, client_with_mock_svc):
        resp = await _post_status(client_with_mock_svc, {})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "VALIDATION_FAILED"
        assert "No selector" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_multiple_selectors_returns_400(self, client_with_mock_svc):
        resp = await _post_status(
            client_with_mock_svc,
            {"transaction_id": "TXN001", "batch_id": "BATCH001"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "VALIDATION_FAILED"
        assert "Multiple selectors" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_all_three_selectors_returns_400(self, client_with_mock_svc):
        resp = await _post_status(
            client_with_mock_svc,
            {"transaction_id": "T", "irn": "I", "batch_id": "B"},
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_irn_plus_transaction_id_returns_400(self, client_with_mock_svc):
        resp = await _post_status(
            client_with_mock_svc,
            {"transaction_id": "TXN001", "irn": "IRN001"},
        )
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "VALIDATION_FAILED"


# ── Authentication ────────────────────────────────────────────────────────


class TestStatusAuth:
    @pytest.mark.asyncio
    async def test_no_credentials_returns_401(self, client_with_mock_svc):
        body = json.dumps({"batch_id": "BATCH001"}).encode("utf-8")
        transport = ASGITransport(app=client_with_mock_svc)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/status",
                content=body,
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTHENTICATION_FAILED"

    @pytest.mark.asyncio
    async def test_bad_signature_returns_401(self, client_with_mock_svc):
        body = json.dumps({"batch_id": "BATCH001"}).encode("utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        transport = ASGITransport(app=client_with_mock_svc)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/status",
                content=body,
                headers={
                    "X-API-Key": TEST_API_KEY,
                    "X-Timestamp": ts,
                    "X-Signature": "deadbeef",
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 401


# ── Body parsing edge cases ───────────────────────────────────────────────


class TestStatusBodyParsing:
    @pytest.mark.asyncio
    async def test_empty_body_returns_400(self, client_with_mock_svc):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = compute_signature(TEST_API_KEY, ts, b"", TEST_SECRET)
        transport = ASGITransport(app=client_with_mock_svc)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/status",
                content=b"",
                headers={
                    "X-API-Key": TEST_API_KEY,
                    "X-Timestamp": ts,
                    "X-Signature": sig,
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, client_with_mock_svc):
        bad_body = b"not-json"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sig = compute_signature(TEST_API_KEY, ts, bad_body, TEST_SECRET)
        transport = ASGITransport(app=client_with_mock_svc)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/status",
                content=bad_body,
                headers={
                    "X-API-Key": TEST_API_KEY,
                    "X-Timestamp": ts,
                    "X-Signature": sig,
                    "Content-Type": "application/json",
                },
            )
        assert resp.status_code == 400


# ── Status queries via mock StatusService ────────────────────────────────


class TestStatusQueries:
    """Test query-path behaviours by replacing StatusService in app state."""

    def _inject_svc(self, app, mock_hb, mock_core=None):
        if mock_core is None:
            mock_core = _MockCore()
        app.state.status_service = StatusService(mock_hb, mock_core)

    @pytest.mark.asyncio
    async def test_query_by_batch_id_returns_multiple_entries(
        self, client_with_mock_svc
    ):
        records = [
            _make_hb_record(transaction_id="TXN001", batch_id="BATCH001", irn="IRN-001", status="processed"),
            _make_hb_record(transaction_id="TXN002", batch_id="BATCH001", irn=None, status="pending"),
        ]
        self._inject_svc(client_with_mock_svc, _MockHB(by_batch=records))

        resp = await _post_status(client_with_mock_svc, {"batch_id": "BATCH001"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 2
        assert data["results"][0]["transaction_id"] == "TXN001"
        assert data["results"][0]["result"] == "processed"
        assert data["results"][1]["transaction_id"] == "TXN002"
        assert data["results"][1]["result"] == "pending"

    @pytest.mark.asyncio
    async def test_query_by_transaction_id_returns_one_entry(
        self, client_with_mock_svc
    ):
        record = _make_hb_record(
            transaction_id="TXN20260617LAG00002",
            irn="TXN20260617LAG00002-94ND90NR-20260617",
            batch_id="BATCH202606170930",
            status="processed",
            received_at="2026-06-17T09:30:12Z",
        )
        self._inject_svc(client_with_mock_svc, _MockHB(by_txn=record))

        resp = await _post_status(
            client_with_mock_svc, {"transaction_id": "TXN20260617LAG00002"}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["results"]) == 1
        entry = data["results"][0]
        assert entry["transaction_id"] == "TXN20260617LAG00002"
        assert entry["irn"] == "TXN20260617LAG00002-94ND90NR-20260617"
        assert entry["result"] == "processed"
        assert entry["received_at"] == "2026-06-17T09:30:12Z"

    @pytest.mark.asyncio
    async def test_query_by_irn_returns_one_entry(self, client_with_mock_svc):
        record = _make_hb_record(
            transaction_id="TXN001",
            irn="IRN-TEST-001",
            status="processed",
        )
        self._inject_svc(client_with_mock_svc, _MockHB(by_irn=record))

        resp = await _post_status(
            client_with_mock_svc, {"irn": "IRN-TEST-001"}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["irn"] == "IRN-TEST-001"
        assert data["results"][0]["result"] == "processed"

    @pytest.mark.asyncio
    async def test_unknown_batch_id_returns_empty_results(
        self, client_with_mock_svc
    ):
        """Unknown id → HTTP 200 with results=[], never 404."""
        self._inject_svc(client_with_mock_svc, _MockHB(by_batch=[]))

        resp = await _post_status(
            client_with_mock_svc, {"batch_id": "UNKNOWN-BATCH-XYZ"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"results": []}

    @pytest.mark.asyncio
    async def test_unknown_transaction_id_returns_empty_results(
        self, client_with_mock_svc
    ):
        self._inject_svc(client_with_mock_svc, _MockHB(by_txn=None))
        resp = await _post_status(
            client_with_mock_svc, {"transaction_id": "NO-SUCH-TXN"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"results": []}

    @pytest.mark.asyncio
    async def test_unknown_irn_returns_empty_results(self, client_with_mock_svc):
        self._inject_svc(client_with_mock_svc, _MockHB(by_irn=None))
        resp = await _post_status(
            client_with_mock_svc, {"irn": "NO-SUCH-IRN"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"results": []}

    @pytest.mark.asyncio
    async def test_core_returns_none_gives_null_firs_status(
        self, client_with_mock_svc
    ):
        """Core stub returns None → firs_status and invoice_number are null."""
        record = _make_hb_record(
            transaction_id="TXN003",
            irn="IRN-003",
            status="processed",
        )
        self._inject_svc(
            client_with_mock_svc,
            _MockHB(by_txn=record),
            _MockCore(result=None),
        )
        resp = await _post_status(
            client_with_mock_svc, {"transaction_id": "TXN003"}
        )
        assert resp.status_code == 200
        entry = resp.json()["results"][0]
        assert entry["firs_status"] is None
        assert entry["invoice_number"] is None

    @pytest.mark.asyncio
    async def test_hb_returns_empty_list_gives_empty_results(
        self, client_with_mock_svc
    ):
        self._inject_svc(client_with_mock_svc, _MockHB(by_batch=[]))
        resp = await _post_status(
            client_with_mock_svc, {"batch_id": "EMPTY-BATCH"}
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    @pytest.mark.asyncio
    async def test_failed_status_when_hb_status_error(self, client_with_mock_svc):
        record = _make_hb_record(
            transaction_id="TXN-ERR",
            status="error",
        )
        self._inject_svc(client_with_mock_svc, _MockHB(by_txn=record))
        resp = await _post_status(
            client_with_mock_svc, {"transaction_id": "TXN-ERR"}
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["result"] == "failed"

    @pytest.mark.asyncio
    async def test_duplicate_status_when_hb_is_duplicate(
        self, client_with_mock_svc
    ):
        record = _make_hb_record(
            transaction_id="TXN-DUP",
            is_duplicate=True,
            status="pending",
        )
        self._inject_svc(client_with_mock_svc, _MockHB(by_txn=record))
        resp = await _post_status(
            client_with_mock_svc, {"transaction_id": "TXN-DUP"}
        )
        assert resp.status_code == 200
        assert resp.json()["results"][0]["result"] == "duplicate"
