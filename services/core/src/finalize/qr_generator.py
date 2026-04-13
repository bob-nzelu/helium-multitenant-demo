"""
QR Code Generator — 200x200 PNG, base64 encoded.

Generates QR codes containing a minimal 5-field JSON payload for invoice
verification. Stored in ``invoices.qr_code_data`` (TEXT column).

Content: {irn, invoice_number, total_amount, issue_date, seller_tin}

See: HLX_FORMAT.md, DECISIONS_V2.md Decision 10
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# QR code library — optional import (graceful if missing during tests)
try:
    import qrcode
    from PIL import Image

    _QR_AVAILABLE = True
except ImportError:
    _QR_AVAILABLE = False
    logger.warning("qrcode/Pillow not installed — QR generation disabled")


class QRError(Exception):
    """Error during QR code generation."""


@dataclass
class QRInput:
    """Input data for QR code generation."""

    irn: str
    invoice_number: str
    total_amount: str | float
    issue_date: str
    seller_tin: str


def generate_qr_code(data: QRInput, size: int = 200) -> str:
    """Generate a QR code as a base64-encoded PNG string.

    Args:
        data: QR content fields (5-field FIRS verification payload).
        size: Output image size in pixels (default 200x200).

    Returns:
        Base64-encoded PNG string.

    Raises:
        QRError: If generation fails or qrcode library not available.
    """
    if not _QR_AVAILABLE:
        raise QRError("qrcode/Pillow libraries not installed")

    content = json.dumps(
        {
            "irn": data.irn,
            "invoice_number": data.invoice_number,
            "total_amount": str(data.total_amount),
            "issue_date": data.issue_date,
            "seller_tin": data.seller_tin,
        },
        separators=(",", ":"),
    )

    try:
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(content)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        img = img.resize((size, size), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        raise QRError(f"QR code generation failed: {e}") from e


async def generate_qr_codes_batch(
    items: list[QRInput],
    size: int = 200,
) -> list[str]:
    """Generate QR codes in parallel for a batch of invoices.

    Uses a thread pool executor since QR generation is CPU-bound.

    Args:
        items: List of QR input data.
        size: Output image size in pixels.

    Returns:
        List of base64-encoded PNG strings (same order as input).

    Raises:
        QRError: If any generation fails.
    """
    if not items:
        return []

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, generate_qr_code, item, size)
        for item in items
    ]
    return await asyncio.gather(*tasks)
