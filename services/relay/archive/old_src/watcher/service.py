"""Relay Watcher Service (Stub - Deferred to Phase 2)"""

from typing import Optional, Dict, Any
from ..base import BaseRelayService


class RelayWatcherService(BaseRelayService):
    """File system monitoring (NAS, SMB, external repos) - Deferred to Phase 2"""

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "RelayWatcherService is deferred to Phase 2. "
            "See Services/Relay/Documentation/RELAY_SERVICE_TYPES.md for specifications."
        )
