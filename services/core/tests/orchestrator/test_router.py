"""
WS3 Orchestrator — Router endpoint tests.

Tests POST /api/v1/process_preview for 200, 202, 400, 404, and 409 responses.
Uses FastAPI's httpx.AsyncClient with a minimal app fixture that wires up
all required state without touching real DB or services.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from src.config import CoreConfig
from src.orchestrator.models import ProcessPreviewResponse200, ProcessPreviewResponse202
from src.orchestrator.router import router
from src.processing.models import PipelineContext


# ---------------------------------------------------------------------------
# App factory for tests
# ---------------------------------------------------------------------------


def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the orchestrator router and mock state."""
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> CoreConfig:
    return CoreConfig(batch_size=100)


@pytest.fixture
def mock_pool() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_sse() -> AsyncMock:
    sse = AsyncMock()
    sse.publish = AsyncMock()
    return sse


@pytest.fixture
def mock_heartbeat() -> AsyncMock:
    from src.ingestion.models import BlobResponse
    client = AsyncMock()
    client.fetch_blob = AsyncMock(return_value=BlobResponse(
        content=b"data", content_type="application/xlsx",
        filename="test.xlsx", size=4,
    ))
    client.upload_blob = AsyncMock()
    return client


@pytest.fixture
def mock_registry() -> MagicMock:
    from src.ingestion.models import ParseMetadata, ParseResult
    parser = AsyncMock()
    parser.parse = AsyncMock(return_value=ParseResult(
        file_type="excel",
        raw_data=[],
        metadata=ParseMetadata(parser_type="excel", original_filename="test.xlsx", row_count=0),
        is_hlm=False,
    ))
    registry = MagicMock()
    registry.get = MagicMock(return_value=parser)
    return registry


def _pending_row() -> dict:
    return {
        "queue_id": "q-001",
        "data_uuid": "d-001",
        "blob_uuid": "blob-001",
        "company_id": "COMP-001",
        "trace_id": "trace-001",
        "uploaded_by": "user-001",
        "status": "PENDING",
        "immediate_processing": False,
    }


def _make_conn_returning(row):
    """Build a mock DB conn that returns `row` on fetchone()."""
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value=cursor)
    return conn


@pytest_asyncio.fixture
async def client(config, mock_pool, mock_sse, mock_heartbeat, mock_registry):
    app = _make_test_app()
    app.state.config = config
    app.state.pool = mock_pool
    app.state.sse_manager = mock_sse
    app.state.heartbeat_client = mock_heartbeat
    app.state.parser_registry = mock_registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# 400 — invalid request body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preview_400_missing_queue_id(client):
    response = await client.post(
        "/api/v1/process_preview",
        json={"data_uuid": "d-001"},  # Missing queue_id
    )
    assert response.status_code == 422  # FastAPI validation returns 422


@pytest.mark.asyncio
async def test_process_preview_400_missing_data_uuid(client):
    response = await client.post(
        "/api/v1/process_preview",
        json={"queue_id": "q-001"},  # Missing data_uuid
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_process_preview_400_empty_body(client):
    response = await client.post("/api/v1/process_preview", json={})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 404 — queue entry not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preview_404_queue_not_found(client):
    conn = _make_conn_returning(None)  # fetchone returns None → not found

    async def _ctx(*args, **kwargs):
        class CM:
            async def __aenter__(self_cm):
                return conn
            async def __aexit__(self_cm, *a):
                pass
        return CM()

    with patch("src.orchestrator.router.get_connection", new=AsyncMock(side_effect=_ctx)):
        response = await client.post(
            "/api/v1/process_preview",
            json={"queue_id": "q-unknown", "data_uuid": "d-001"},
        )

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error_code"] == "ORCH_002"


# ---------------------------------------------------------------------------
# 409 — already processing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preview_409_already_processing(client):
    row = {**_pending_row(), "status": "PROCESSING"}
    conn = _make_conn_returning(row)

    async def _ctx(*args, **kwargs):
        class CM:
            async def __aenter__(self_cm):
                return conn
            async def __aexit__(self_cm, *a):
                pass
        return CM()

    with patch("src.orchestrator.router.get_connection", new=AsyncMock(side_effect=_ctx)):
        response = await client.post(
            "/api/v1/process_preview",
            json={"queue_id": "q-001", "data_uuid": "d-001"},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error_code"] == "ORCH_003"
    assert "PROCESSING" in body["detail"]["message"]


@pytest.mark.asyncio
async def test_process_preview_409_preview_ready(client):
    row = {**_pending_row(), "status": "PREVIEW_READY"}
    conn = _make_conn_returning(row)

    async def _ctx(*args, **kwargs):
        class CM:
            async def __aenter__(self_cm):
                return conn
            async def __aexit__(self_cm, *a):
                pass
        return CM()

    with patch("src.orchestrator.router.get_connection", new=AsyncMock(side_effect=_ctx)):
        response = await client.post(
            "/api/v1/process_preview",
            json={"queue_id": "q-001", "data_uuid": "d-001"},
        )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# 200 — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preview_200_happy_path(client):
    row = _pending_row()
    conn = _make_conn_returning(row)
    call_count = 0

    async def _ctx(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        class CM:
            async def __aenter__(self_cm):
                return conn
            async def __aexit__(self_cm, *a):
                pass
        return CM()

    mock_200 = ProcessPreviewResponse200(
        queue_id="q-001",
        data_uuid="d-001",
        status="preview_ready",
        statistics=__import__("src.orchestrator.models", fromlist=["StatisticsModel"]).StatisticsModel(),
        hlx_blob_uuid="blob-xyz",
    )

    with patch("src.orchestrator.router.get_connection", new=AsyncMock(side_effect=_ctx)), \
         patch("src.orchestrator.router.PipelineOrchestrator") as MockOrch, \
         patch("src.orchestrator.router.Transformer"), \
         patch("src.orchestrator.router.Enricher"), \
         patch("src.orchestrator.router.Resolver"):
        mock_instance = AsyncMock()
        mock_instance.process = AsyncMock(return_value=mock_200)
        MockOrch.return_value = mock_instance

        response = await client.post(
            "/api/v1/process_preview",
            json={"queue_id": "q-001", "data_uuid": "d-001"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["queue_id"] == "q-001"
    assert body["status"] == "preview_ready"
    assert body["hlx_blob_uuid"] == "blob-xyz"


# ---------------------------------------------------------------------------
# 202 — backgrounded (soft timeout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preview_202_soft_timeout(client):
    row = _pending_row()
    conn = _make_conn_returning(row)

    async def _ctx(*args, **kwargs):
        class CM:
            async def __aenter__(self_cm):
                return conn
            async def __aexit__(self_cm, *a):
                pass
        return CM()

    from src.orchestrator.models import ProgressModel
    mock_202 = ProcessPreviewResponse202(
        queue_id="q-001",
        data_uuid="d-001",
        phases_completed=2,
        current_phase="enrich",
    )

    with patch("src.orchestrator.router.get_connection", new=AsyncMock(side_effect=_ctx)), \
         patch("src.orchestrator.router.PipelineOrchestrator") as MockOrch, \
         patch("src.orchestrator.router.Transformer"), \
         patch("src.orchestrator.router.Enricher"), \
         patch("src.orchestrator.router.Resolver"):
        mock_instance = AsyncMock()
        mock_instance.process = AsyncMock(return_value=mock_202)
        MockOrch.return_value = mock_instance

        response = await client.post(
            "/api/v1/process_preview",
            json={"queue_id": "q-001", "data_uuid": "d-001"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "processing"
    assert body["phases_completed"] == 2


# ---------------------------------------------------------------------------
# 500 — pipeline exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preview_500_pipeline_error(client):
    row = _pending_row()
    conn = _make_conn_returning(row)

    async def _ctx(*args, **kwargs):
        class CM:
            async def __aenter__(self_cm):
                return conn
            async def __aexit__(self_cm, *a):
                pass
        return CM()

    with patch("src.orchestrator.router.get_connection", new=AsyncMock(side_effect=_ctx)), \
         patch("src.orchestrator.router.PipelineOrchestrator") as MockOrch, \
         patch("src.orchestrator.router.Transformer"), \
         patch("src.orchestrator.router.Enricher"), \
         patch("src.orchestrator.router.Resolver"):
        mock_instance = AsyncMock()
        mock_instance.process = AsyncMock(side_effect=RuntimeError("catastrophic failure"))
        MockOrch.return_value = mock_instance

        response = await client.post(
            "/api/v1/process_preview",
            json={"queue_id": "q-001", "data_uuid": "d-001"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["detail"]["error_code"] == "ORCH_004"
