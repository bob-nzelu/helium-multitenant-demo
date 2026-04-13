"""Tests for app factory — create_app, lifespan, error handling."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.errors import CoreError, CoreErrorCode


def _mock_pool():
    """Create a mock pool that passes health checks."""
    pool = AsyncMock()
    pool.open = AsyncMock()
    pool.check = AsyncMock()
    pool.close = AsyncMock()

    conn = AsyncMock()
    conn.execute = AsyncMock()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_connection():
        yield conn

    pool.connection = mock_connection
    return pool


@pytest.mark.asyncio
class TestCreateApp:
    """Test app factory basics."""

    async def test_create_app_returns_fastapi(self):
        from src.app import create_app
        from src.config import CoreConfig

        app = create_app(CoreConfig())
        assert app.title == "Helium Core Service"

    async def test_create_app_default_config(self):
        """When no config passed, from_env() is called."""
        from src.app import create_app

        with patch("src.app.CoreConfig") as MockConfig:
            MockConfig.from_env.return_value = MagicMock(
                log_level="INFO",
                port=8080,
                sse_buffer_size=1000,
                sse_heartbeat_interval=15,
            )
            app = create_app()
            assert app is not None


@pytest.mark.asyncio
class TestLifespan:
    """Test lifespan startup/shutdown directly."""

    async def test_lifespan_no_scheduler(self):
        """Full lifespan cycle without scheduler."""
        from src.app import create_app
        from src.config import CoreConfig

        config = CoreConfig()
        mock_pool = _mock_pool()

        with (
            patch("src.app.create_pool", new_callable=AsyncMock, return_value=mock_pool),
            patch("src.app.init_schemas", new_callable=AsyncMock),
            patch("src.app.create_scheduler", return_value=None),
            patch("src.app.close_pool", new_callable=AsyncMock) as mock_close,
        ):
            app = create_app(config)

            # Directly invoke lifespan
            ctx = app.router.lifespan_context(app)
            async with ctx:
                # Startup complete — verify state
                assert app.state.pool is mock_pool
                assert app.state.scheduler is None
                assert app.state.sse_manager is not None
                assert hasattr(app.state, "start_time")

            # Shutdown complete
            mock_close.assert_awaited_once()

    async def test_lifespan_with_scheduler(self):
        """Full lifespan cycle with scheduler."""
        from src.app import create_app
        from src.config import CoreConfig

        config = CoreConfig()
        mock_pool = _mock_pool()
        mock_scheduler = AsyncMock()
        mock_scheduler.__aenter__ = AsyncMock(return_value=mock_scheduler)
        mock_scheduler.__aexit__ = AsyncMock(return_value=False)
        mock_scheduler.start_in_background = AsyncMock()

        with (
            patch("src.app.create_pool", new_callable=AsyncMock, return_value=mock_pool),
            patch("src.app.init_schemas", new_callable=AsyncMock),
            patch("src.app.create_scheduler", return_value=mock_scheduler),
            patch("src.app.register_jobs", new_callable=AsyncMock) as mock_register,
            patch("src.app.close_pool", new_callable=AsyncMock),
        ):
            app = create_app(config)

            ctx = app.router.lifespan_context(app)
            async with ctx:
                assert app.state.scheduler is mock_scheduler

            mock_scheduler.__aenter__.assert_awaited_once()
            mock_register.assert_awaited_once()
            mock_scheduler.start_in_background.assert_awaited_once()
            mock_scheduler.__aexit__.assert_awaited_once()

    async def test_lifespan_scheduler_exit_error(self):
        """Scheduler __aexit__ error should be swallowed."""
        from src.app import create_app
        from src.config import CoreConfig

        config = CoreConfig()
        mock_scheduler = AsyncMock()
        mock_scheduler.__aenter__ = AsyncMock(return_value=mock_scheduler)
        mock_scheduler.__aexit__ = AsyncMock(side_effect=RuntimeError("boom"))
        mock_scheduler.start_in_background = AsyncMock()

        with (
            patch("src.app.create_pool", new_callable=AsyncMock, return_value=_mock_pool()),
            patch("src.app.init_schemas", new_callable=AsyncMock),
            patch("src.app.create_scheduler", return_value=mock_scheduler),
            patch("src.app.register_jobs", new_callable=AsyncMock),
            patch("src.app.close_pool", new_callable=AsyncMock),
        ):
            app = create_app(config)

            ctx = app.router.lifespan_context(app)
            async with ctx:
                pass
            # No exception raised — error swallowed


@pytest.mark.asyncio
class TestErrorHandler:
    """Test CoreError exception handler."""

    async def test_core_error_returns_json(self):
        from src.app import create_app
        from src.config import CoreConfig
        from src.errors import ValidationError

        app = create_app(CoreConfig())

        @app.get("/test-error")
        async def raise_error():
            raise ValidationError("bad input", details=[{"field": "x"}])

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/test-error")

        assert resp.status_code == 400
        body = resp.json()
        assert body["error"] == "INV_001"
        assert "bad input" in body["message"]
