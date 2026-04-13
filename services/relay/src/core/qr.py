"""
QR Generator Wrapper

Thin wrapper around the cached Transforma QR module.
Falls back to inline QR generation when Transforma modules not loaded.

QR Data Format: NRS:{irn}:{hash_8chars}
"""

import base64
import hashlib
import logging
from typing import Any

from ..errors import ModuleNotLoadedError, QRGenerationError

logger = logging.getLogger(__name__)

# 1x1 transparent PNG placeholder (for when real QR lib not available)
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _inline_qr_data(irn: str) -> str:
    """Fallback QR data generation: NRS:{irn}:{hash_8chars}"""
    h = hashlib.sha256(irn.encode()).hexdigest()[:8].upper()
    return f"NRS:{irn}:{h}"


def _inline_qr_image(irn: str) -> str:
    """Fallback QR image: base64 placeholder PNG with data URI."""
    return f"data:image/png;base64,{base64.b64encode(_TINY_PNG).decode()}"


class QRGenerator:
    """
    Generate QR code data using the cached Transforma QR module.

    Falls back to inline generation if module cache is not loaded.
    """

    def __init__(self, module_cache: Any):
        self._cache = module_cache

    def generate(self, irn: str) -> str:
        """Generate QR code data string for the given IRN."""
        try:
            module = self._cache.get_qr_module()
            qr_data = module.generate_qr_data(irn)
            logger.debug(f"QR data generated (Transforma): {len(qr_data)} chars")
            return qr_data
        except (ModuleNotLoadedError, AttributeError):
            qr_data = _inline_qr_data(irn)
            logger.debug(f"QR data generated (inline fallback): {qr_data[:30]}...")
            return qr_data
        except Exception as e:
            raise QRGenerationError(message=f"QR data generation failed: {e}") from e

    def generate_image(self, irn: str) -> str:
        """Generate QR code as base64 data URI (placeholder PNG)."""
        try:
            module = self._cache.get_qr_module()
            img_bytes = module.create_qr_image_bytes(irn)
            return f"data:image/png;base64,{base64.b64encode(img_bytes).decode()}"
        except (ModuleNotLoadedError, AttributeError):
            return _inline_qr_image(irn)
        except Exception as e:
            raise QRGenerationError(message=f"QR image generation failed: {e}") from e
