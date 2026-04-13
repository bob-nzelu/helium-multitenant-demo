"""
Transforma Module Cache

Loads and caches Transforma modules (IRN generator, QR generator) from
HeartBeat's config.db via API. Modules are Python source code stored as
blobs in config.db — written to temp files and loaded via importlib.

Lifecycle:
    1. Startup: load_all() fetches modules + service keys from HeartBeat
    2. Every 12h: background task calls refresh()
    3. On-demand: HeartBeat calls /internal/refresh-cache → refresh()
    4. Shutdown: cleanup() removes temp files, cancels background task
"""

import asyncio
import logging
import shutil
import tempfile
import types
from pathlib import Path
from typing import Any, Dict, Optional

from ..errors import ModuleCacheError, ModuleNotLoadedError
from .service_keys import ServiceKeys

logger = logging.getLogger(__name__)


class TransformaModuleCache:
    """
    Cache for Transforma modules loaded from HeartBeat's config.db.

    Modules are Python source code written to temp .py files and loaded
    via importlib. Service keys (FIRS public key, CSID, certificate) are
    stored in-memory as a frozen ServiceKeys dataclass.
    """

    def __init__(
        self,
        heartbeat_client: Any,
        refresh_interval_s: int = 43200,  # 12 hours
    ):
        self._heartbeat = heartbeat_client
        self._refresh_interval_s = refresh_interval_s

        # Cache state
        self._cache_dir: Optional[Path] = None
        self._modules: Dict[str, types.ModuleType] = {}
        self._checksums: Dict[str, str] = {}
        self._versions: Dict[str, str] = {}
        self._service_keys: Optional[ServiceKeys] = None
        self._loaded: bool = False

        # Background refresh task
        self._refresh_task: Optional[asyncio.Task] = None

    @property
    def is_loaded(self) -> bool:
        """Whether modules have been successfully loaded."""
        return self._loaded

    @property
    def service_keys(self) -> ServiceKeys:
        """Get cached FIRS service keys. Raises if not loaded."""
        if self._service_keys is None:
            raise ModuleNotLoadedError("service_keys")
        return self._service_keys

    @property
    def module_names(self) -> list:
        """List of loaded module names."""
        return list(self._modules.keys())

    def get_module(self, name: str) -> types.ModuleType:
        """
        Get a cached module by name.

        Args:
            name: Module name (e.g., "irn_generator", "qr_generator")

        Returns:
            The loaded Python module.

        Raises:
            ModuleNotLoadedError: If module not loaded.
        """
        if name not in self._modules:
            raise ModuleNotLoadedError(name)
        return self._modules[name]

    def get_irn_module(self) -> types.ModuleType:
        """Get the cached IRN generator module."""
        return self.get_module("irn_generator")

    def get_qr_module(self) -> types.ModuleType:
        """Get the cached QR generator module."""
        return self.get_module("qr_generator")

    async def load_all(self) -> None:
        """
        Load all Transforma modules and service keys from HeartBeat.

        Called once at startup (FastAPI lifespan). Creates temp directory,
        fetches modules, writes to temp files, and loads via importlib.

        On failure: logs warning, sets _loaded=False. Bulk flow still works
        (no IRN/QR needed). External flow returns 503 until modules load.
        """
        try:
            # Create cache directory
            self._cache_dir = Path(tempfile.mkdtemp(prefix="relay_transforma_"))
            logger.info(f"Module cache dir: {self._cache_dir}")

            # Fetch from HeartBeat
            config_data = await self._heartbeat.get_transforma_config()

            # Load modules
            for module_info in config_data.get("modules", []):
                self._load_module_from_info(module_info)

            # Load service keys
            keys_data = config_data.get("service_keys", {})
            if keys_data:
                self._service_keys = ServiceKeys.from_api_response(keys_data)
                logger.info(
                    f"Service keys loaded — CSID expires: {self._service_keys.csid_expires_at}"
                )

            self._loaded = True
            logger.info(
                f"Module cache loaded: {list(self._modules.keys())} "
                f"({len(self._modules)} modules)"
            )

        except Exception as e:
            logger.warning(
                f"Module cache load failed (degraded mode): {e}"
            )
            self._loaded = False

    async def refresh(self) -> Dict[str, Any]:
        """
        Re-fetch modules from HeartBeat. Only reload if checksum changed.

        Returns:
            {"modules_updated": [...], "keys_updated": bool}
        """
        result = {"modules_updated": [], "keys_updated": False}

        try:
            config_data = await self._heartbeat.get_transforma_config()

            # Check each module for changes
            for module_info in config_data.get("modules", []):
                name = module_info["module_name"]
                new_checksum = module_info.get("checksum", "")

                if new_checksum and new_checksum == self._checksums.get(name):
                    logger.debug(f"Module '{name}' unchanged (checksum match)")
                    continue

                self._load_module_from_info(module_info)
                result["modules_updated"].append(name)
                logger.info(f"Module '{name}' updated to v{module_info.get('version', '?')}")

            # Update service keys
            keys_data = config_data.get("service_keys", {})
            if keys_data:
                new_keys = ServiceKeys.from_api_response(keys_data)
                if self._service_keys is None or new_keys.csid != self._service_keys.csid:
                    self._service_keys = new_keys
                    result["keys_updated"] = True
                    logger.info("Service keys updated")

            self._loaded = True

        except Exception as e:
            logger.warning(f"Module cache refresh failed: {e}")

        return result

    async def start_refresh_loop(self) -> None:
        """Start background refresh task (called from lifespan)."""
        if self._refresh_task is not None:
            return

        async def _loop():
            while True:
                await asyncio.sleep(self._refresh_interval_s)
                logger.info("Module cache: periodic refresh")
                await self.refresh()

        self._refresh_task = asyncio.create_task(_loop())
        logger.info(
            f"Module cache refresh loop started "
            f"(every {self._refresh_interval_s}s)"
        )

    async def cleanup(self) -> None:
        """Remove temp directory and cancel background task."""
        # Cancel refresh task
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

        # Remove temp directory
        if self._cache_dir and self._cache_dir.exists():
            try:
                shutil.rmtree(self._cache_dir)
                logger.info(f"Module cache dir removed: {self._cache_dir}")
            except OSError as e:
                logger.warning(f"Failed to clean cache dir: {e}")

        self._modules.clear()
        self._checksums.clear()
        self._versions.clear()
        self._service_keys = None
        self._loaded = False

    def _load_module_from_info(self, module_info: Dict[str, Any]) -> None:
        """
        Write module source to temp file and load via importlib.

        Args:
            module_info: {"module_name", "source_code", "version", "checksum"}
        """
        name = module_info["module_name"]
        source = module_info["source_code"]
        checksum = module_info.get("checksum", "")
        version = module_info.get("version", "unknown")

        if self._cache_dir is None:
            raise ModuleCacheError("Cache directory not initialized")

        # Write to temp file (for tracebacks and debugging)
        module_path = self._cache_dir / f"{name}.py"
        module_path.write_text(source, encoding="utf-8")

        # Create a fresh module and exec source directly into it.
        # Using exec() instead of importlib file loader avoids Python's
        # bytecode/mtime caching which prevents reloading changed files.
        module = types.ModuleType(f"transforma.{name}")
        module.__file__ = str(module_path)
        code = compile(source, str(module_path), "exec")
        exec(code, module.__dict__)  # noqa: S102

        # Store in cache
        self._modules[name] = module
        self._checksums[name] = checksum
        self._versions[name] = version

        logger.debug(f"Module '{name}' v{version} loaded from {module_path}")
