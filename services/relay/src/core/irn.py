"""
IRN Generator Wrapper

Thin wrapper around the cached Transforma IRN module.
Falls back to inline IRN generation when Transforma modules not loaded.

IRN Format: {cleaned_invoice_number}-{firs_service_id}-{YYYYMMDD}
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict

from ..errors import IRNGenerationError, ModuleNotLoadedError

logger = logging.getLogger(__name__)


def _inline_irn(invoice_data: Dict[str, Any]) -> str:
    """
    Fallback inline IRN generation when Transforma modules not cached.

    Format: {cleaned_id}-{service_id}-{YYYYMMDD}
    """
    raw_id = (
        invoice_data.get("invoice_number")
        or invoice_data.get("transaction_id")
        or invoice_data.get("invoiceTypeCode")
        or invoice_data.get("invoice_type_code")
        or "UNKNOWN"
    )
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(raw_id)).upper()

    service_id = invoice_data.get("firs_service_id", "A8BM72KQ")
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

    return f"{cleaned}-{service_id}-{date_str}"


class IRNGenerator:
    """
    Generate IRN strings using the cached Transforma IRN module.

    Falls back to inline generation if module cache is not loaded.
    """

    def __init__(self, module_cache: Any):
        self._cache = module_cache

    def generate(self, invoice_data: Dict[str, Any]) -> str:
        """
        Generate an IRN for the given invoice data.

        Uses Transforma module if available, inline fallback otherwise.
        """
        try:
            module = self._cache.get_irn_module()
            irn = module.generate_irn(invoice_data)
            logger.debug(f"IRN generated (Transforma): {irn[:20]}...")
            return irn
        except (ModuleNotLoadedError, AttributeError):
            # Fallback: inline IRN generation
            irn = _inline_irn(invoice_data)
            logger.debug(f"IRN generated (inline fallback): {irn[:20]}...")
            return irn
        except Exception as e:
            raise IRNGenerationError(
                message=f"IRN generation failed: {e}"
            ) from e
