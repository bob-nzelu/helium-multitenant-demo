"""Relay API Service (Stub - Deferred to Phase 2)"""

from typing import Optional, Dict, Any
from ..base import BaseRelayService


class RelayAPIService(BaseRelayService):
    """Webhook/custom HTTP endpoints - Deferred to Phase 2"""

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "RelayAPIService is deferred to Phase 2. "
            "See Services/Relay/Documentation/RELAY_SERVICE_TYPES.md for specifications."
        )
