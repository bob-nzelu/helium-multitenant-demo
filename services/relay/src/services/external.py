"""
External Service (API Flow)

External systems submit invoices via the API. Relay:
    1. Runs the ingestion pipeline
    2. Fires-and-forgets Core processing (no preview wait)
    3. Generates a quick IRN + QR code from cached Transforma modules
    4. Returns {queue_id, irn, qr_code} immediately

If Transforma modules are not loaded (cache cold), returns 503.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .ingestion import IngestionService, IngestResult
from ..core.irn import IRNGenerator
from ..core.qr import QRGenerator

logger = logging.getLogger(__name__)


@dataclass
class ExternalResult:
    """Result of external API flow."""

    ingest: IngestResult
    irn: str
    qr_code: str
    status: str = "processed"


class ExternalService:
    """
    External API flow: ingest → fire-and-forget Core → IRN/QR → return.

    Usage:
        service = ExternalService(ingestion, core, irn_gen, qr_gen)
        result = await service.process(files, api_key, trace_id, invoice_data)
    """

    def __init__(
        self,
        ingestion_service: IngestionService,
        core_client: Any,
        irn_generator: IRNGenerator,
        qr_generator: QRGenerator,
    ):
        self._ingestion = ingestion_service
        self._core = core_client
        self._irn = irn_generator
        self._qr = qr_generator

    async def process(
        self,
        files: List[Tuple[str, bytes]],
        api_key: str,
        trace_id: str = "",
        invoice_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict] = None,
        jwt_token: Optional[str] = None,
    ) -> ExternalResult:
        """
        Run external flow: ingest → fire-and-forget → IRN/QR.

        Args:
            files: List of (filename, file_data) tuples.
            api_key: Authenticated API key.
            trace_id: Request trace ID.
            invoice_data: Invoice fields for IRN generation.
            metadata: SDK identity/trace fields (forwarded through pipeline).
            jwt_token: Bearer JWT (forwarded to HeartBeat/Core).

        Returns:
            ExternalResult with IngestResult + IRN + QR code.

        Raises:
            ModuleNotLoadedError: If Transforma modules not cached (503).
            IRNGenerationError: If IRN generation fails.
            QRGenerationError: If QR generation fails.
        """
        # Step 1: Run ingestion pipeline (metadata + JWT forwarded to HeartBeat)
        ingest_result = await self._ingestion.ingest(
            files, api_key, trace_id,
            metadata=metadata, jwt_token=jwt_token,
        )

        # Step 2: Fire-and-forget Core processing
        try:
            await self._core.process_immediate(
                queue_id=ingest_result.queue_id
            )
            logger.info(
                f"[{trace_id}] External fire-and-forget — "
                f"queue_id={ingest_result.queue_id}"
            )
        except Exception as e:
            logger.warning(
                f"[{trace_id}] Core process_immediate failed — "
                f"queue_id={ingest_result.queue_id}: {e}"
            )

        # Step 3: Generate IRN + QR
        inv_data = invoice_data or {}
        irn = self._irn.generate(inv_data)
        qr_code = self._qr.generate(irn)

        logger.info(
            f"[{trace_id}] External complete — "
            f"irn={irn[:12]}..., qr_len={len(qr_code)}"
        )

        return ExternalResult(
            ingest=ingest_result,
            irn=irn,
            qr_code=qr_code,
        )
