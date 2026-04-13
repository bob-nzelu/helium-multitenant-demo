"""
HMAC-SHA256 Signing — signs outbound requests to Relay.

Signature scheme (matches Relay's auth.py exactly):
    body_hash = SHA256(raw_body_bytes).hex()
    message   = "{api_key}:{timestamp}:{body_hash}"
    signature = HMAC-SHA256(secret, message).hex()

The signature covers the raw multipart body bytes.
"""

import hashlib
import hmac as hmac_mod
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .catalog import CatalogManager

logger = logging.getLogger(__name__)


class HMACSigner:
    """Signs and sends requests to Relay with HMAC-SHA256 auth."""

    def __init__(self, catalog: CatalogManager):
        self._catalog = catalog

    def _compute_signature(
        self, api_key: str, api_secret: str, timestamp: str, body: bytes,
    ) -> str:
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{api_key}:{timestamp}:{body_hash}"
        return hmac_mod.new(
            api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def send_to_relay(
        self,
        relay_url: str,
        tenant_id: str,
        invoice_json: dict,
        invoice_data: Optional[dict] = None,
        *,
        override_api_key: Optional[str] = None,
        override_api_secret: Optional[str] = None,
        override_timestamp: Optional[str] = None,
        bad_signature: bool = False,
    ) -> dict:
        """
        Send a single invoice JSON file to Relay /api/ingest.

        Returns the parsed response body from Relay.
        """
        api_key, api_secret = self._catalog.get_hmac_credentials(tenant_id)

        # Allow overrides for bad-call testing
        if override_api_key is not None:
            api_key = override_api_key
        if override_api_secret is not None:
            api_secret = override_api_secret

        timestamp = override_timestamp or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # Build the invoice file bytes
        invoice_number = invoice_json.get("invoice_number", "UNKNOWN")
        file_bytes = json.dumps(invoice_json, ensure_ascii=False).encode("utf-8")
        filename = f"{invoice_number}.json"

        # Build invoice_data_json for IRN generation
        inv_data = invoice_data or {
            "invoice_number": invoice_number,
            "tin": invoice_json.get("seller_tin", ""),
            "issue_date": invoice_json.get("issue_date", ""),
        }
        inv_data_str = json.dumps(inv_data)

        # Build multipart form manually to get raw body for HMAC
        # We use httpx's internal multipart encoder
        boundary = f"----SimulatorBoundary{abs(hash(timestamp)) % 10**12}"
        content_type = f"multipart/form-data; boundary={boundary}"

        body = self._build_multipart_body(
            boundary=boundary,
            filename=filename,
            file_bytes=file_bytes,
            call_type="external",
            invoice_data_json=inv_data_str,
        )

        # Compute HMAC signature over the raw multipart body
        if bad_signature:
            signature = "0" * 64  # intentionally wrong
        else:
            signature = self._compute_signature(api_key, api_secret, timestamp, body)

        headers = {
            "X-API-Key": api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": content_type,
        }

        url = f"{relay_url}/api/ingest"
        logger.info(f"POST {url} — invoice={invoice_number}, api_key={api_key[:12]}...")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, content=body, headers=headers)

        result = {
            "status_code": resp.status_code,
            "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        }
        return result

    async def send_raw_to_relay(
        self,
        relay_url: str,
        tenant_id: str,
        raw_body: bytes,
        filename: str,
        content_type_override: Optional[str] = None,
        *,
        override_api_key: Optional[str] = None,
        override_api_secret: Optional[str] = None,
        override_timestamp: Optional[str] = None,
        bad_signature: bool = False,
    ) -> dict:
        """
        Send raw bytes as a file to Relay — used by bad_calls for
        malformed JSON, oversized files, etc.
        """
        api_key, api_secret = self._catalog.get_hmac_credentials(tenant_id)
        if override_api_key is not None:
            api_key = override_api_key
        if override_api_secret is not None:
            api_secret = override_api_secret

        timestamp = override_timestamp or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        boundary = f"----SimulatorBoundary{abs(hash(timestamp)) % 10**12}"
        ct = f"multipart/form-data; boundary={boundary}"

        body = self._build_multipart_body(
            boundary=boundary,
            filename=filename,
            file_bytes=raw_body,
            call_type="external",
        )

        if bad_signature:
            signature = "0" * 64
        else:
            signature = self._compute_signature(api_key, api_secret, timestamp, body)

        headers = {
            "X-API-Key": api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
            "Content-Type": ct,
        }

        url = f"{relay_url}/api/ingest"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, content=body, headers=headers)

        try:
            resp_body = resp.json()
        except Exception:
            resp_body = resp.text

        return {"status_code": resp.status_code, "body": resp_body}

    def _build_multipart_body(
        self,
        boundary: str,
        filename: str,
        file_bytes: bytes,
        call_type: str = "external",
        invoice_data_json: Optional[str] = None,
        metadata_json: Optional[str] = None,
    ) -> bytes:
        """Build raw multipart/form-data body bytes."""
        parts: list[bytes] = []

        # File part
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
            f"Content-Type: application/json\r\n\r\n".encode()
        )
        parts.append(file_bytes)
        parts.append(b"\r\n")

        # call_type part
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="call_type"\r\n\r\n'
            f"{call_type}".encode()
        )
        parts.append(b"\r\n")

        # invoice_data_json part (optional)
        if invoice_data_json:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="invoice_data_json"\r\n\r\n'
                f"{invoice_data_json}".encode()
            )
            parts.append(b"\r\n")

        # metadata part (optional)
        if metadata_json:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="metadata"\r\n\r\n'
                f"{metadata_json}".encode()
            )
            parts.append(b"\r\n")

        # Closing boundary
        parts.append(f"--{boundary}--\r\n".encode())

        return b"".join(parts)
