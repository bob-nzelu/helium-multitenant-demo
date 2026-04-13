"""
Relay Queue Service (Stub - Deferred to Phase 2)

Internal relay queue service for processing backlog when primary relay is unavailable.

Status: DEFERRED to Phase 2
See: Services/Relay/Documentation/RELAY_SERVICE_TYPES.md

This is a stub that inherits from BaseRelayService but raises NotImplementedError
in the ingest_file() method. Will be fully implemented in Phase 2.
"""

from typing import Optional, Dict, Any

from ..base import BaseRelayService
from ..services.clients import (
    CoreAPIClient,
    HeartBeatClient,
    AuditAPIClient,
)
from ..services.errors import RelayError


class RelayQueueService(BaseRelayService):
    """
    Internal Relay Queue Service (DEFERRED to Phase 2)

    Purpose: Queue files for processing when primary relay is unavailable

    Planned Implementation:
    - Internal queue table in relay.db
    - Polling mechanism to process queued files
    - Retry logic for failed queue entries
    - Cleanup of processed entries

    Phase: 2+ (not Phase 1A or 1B)
    """

    async def ingest_file(
        self,
        file_data: bytes,
        filename: str,
        batch_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Ingest file to queue (stub - not implemented).

        Raises:
            NotImplementedError: This service is deferred to Phase 2
        """

        raise NotImplementedError(
            "RelayQueueService is deferred to Phase 2. "
            "See Services/Relay/Documentation/RELAY_SERVICE_TYPES.md for specifications."
        )
