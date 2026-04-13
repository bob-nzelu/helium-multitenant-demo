"""Relay DBC Service (Stub - Deferred to Phase 2)"""

from typing import Optional, Dict, Any
from ..base import BaseRelayService


class RelayDBCService(BaseRelayService):
    """Database connectivity (ODBC/JDBC) - Deferred to Phase 2"""

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        raise NotImplementedError(
            "RelayDBCService is deferred to Phase 2. "
            "See Services/Relay/Documentation/RELAY_SERVICE_TYPES.md for specifications."
        )
