"""
IRN Generator Wrapper

Thin wrapper around the cached Transforma IRN module.
Relay generates quick IRNs for external API callers — the full IRN lifecycle
(FIRS submission, signing) happens later in Core's 8-step pipeline.

Usage:
    irn_gen = IRNGenerator(module_cache)
    irn = irn_gen.generate(invoice_data)
"""

import logging
from typing import Any, Dict

from ..errors import IRNGenerationError, ModuleNotLoadedError

logger = logging.getLogger(__name__)


class IRNGenerator:
    """
    Generate IRN strings using the cached Transforma IRN module.

    The actual generation logic lives in the Transforma script (Python code
    stored in HeartBeat's config.db). This class wraps the cached module
    with error handling.
    """

    def __init__(self, module_cache: Any):
        self._cache = module_cache

    def generate(self, invoice_data: Dict[str, Any]) -> str:
        """
        Generate an IRN for the given invoice data.

        Args:
            invoice_data: Invoice fields needed for IRN generation
                          (exact schema defined by the Transforma module).

        Returns:
            IRN string.

        Raises:
            ModuleNotLoadedError: If Transforma modules not yet loaded.
            IRNGenerationError: If IRN generation fails.
        """
        try:
            module = self._cache.get_irn_module()
        except ModuleNotLoadedError:
            raise

        try:
            irn = module.generate_irn(invoice_data)
            logger.debug(f"IRN generated: {irn[:12]}...")
            return irn
        except Exception as e:
            raise IRNGenerationError(
                message=f"IRN generation failed: {e}"
            ) from e
