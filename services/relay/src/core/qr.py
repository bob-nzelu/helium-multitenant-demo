"""
QR Generator Wrapper

Thin wrapper around the cached Transforma QR module.
Generates QR code data (base64) for external API callers. The QR encodes
the IRN and is optionally encrypted with the FIRS public key.

Usage:
    qr_gen = QRGenerator(module_cache)
    qr_data = qr_gen.generate(irn)
    qr_bytes = qr_gen.generate_image(irn)
"""

import logging
from typing import Any, Optional

from ..errors import ModuleNotLoadedError, QRGenerationError

logger = logging.getLogger(__name__)


class QRGenerator:
    """
    Generate QR code data using the cached Transforma QR module.

    The actual generation logic lives in the Transforma script (Python code
    stored in HeartBeat's config.db). This class wraps the cached module
    with error handling and access to FIRS service keys.
    """

    def __init__(self, module_cache: Any):
        self._cache = module_cache

    def generate(self, irn: str) -> str:
        """
        Generate QR code data string for the given IRN.

        Args:
            irn: The Invoice Reference Number.

        Returns:
            Base64-encoded QR data string.

        Raises:
            ModuleNotLoadedError: If Transforma modules not yet loaded.
            QRGenerationError: If QR generation fails.
        """
        try:
            module = self._cache.get_qr_module()
        except ModuleNotLoadedError:
            raise

        try:
            qr_data = module.generate_qr_data(irn)
            logger.debug(f"QR data generated ({len(qr_data)} chars)")
            return qr_data
        except Exception as e:
            raise QRGenerationError(
                message=f"QR data generation failed: {e}"
            ) from e

    def generate_image(self, irn: str) -> bytes:
        """
        Generate QR code as PNG image bytes.

        Args:
            irn: The Invoice Reference Number.

        Returns:
            PNG image bytes.

        Raises:
            ModuleNotLoadedError: If Transforma modules not yet loaded.
            QRGenerationError: If QR image generation fails.
        """
        try:
            module = self._cache.get_qr_module()
        except ModuleNotLoadedError:
            raise

        try:
            img_bytes = module.create_qr_image_bytes(irn)
            logger.debug(f"QR image generated ({len(img_bytes)} bytes)")
            return img_bytes
        except Exception as e:
            raise QRGenerationError(
                message=f"QR image generation failed: {e}"
            ) from e
