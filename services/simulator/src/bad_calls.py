"""
Bad Call Generator — crafts intentionally bad requests to test Relay error handling.

Each error_type produces a specific failure scenario.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from .catalog import CatalogManager
from .generators import OutboundGenerator
from .hmac_signer import HMACSigner

logger = logging.getLogger(__name__)


class BadCallGenerator:
    """Generate intentionally bad requests for each error scenario."""

    def __init__(
        self,
        catalog: CatalogManager,
        outbound_gen: OutboundGenerator,
        signer: HMACSigner,
        relay_url: str,
    ):
        self._catalog = catalog
        self._outbound = outbound_gen
        self._signer = signer
        self._relay_url = relay_url
        # Track invoice numbers sent, for duplicate testing
        self._last_invoice: dict | None = None

    async def generate(self, tenant_id: str, error_type: str) -> dict:
        handler = getattr(self, f"_bad_{error_type}", None)
        if handler is None:
            return {
                "error_type": error_type,
                "error": f"Unknown error_type '{error_type}'",
                "valid_types": [
                    "auth_failure", "expired_timestamp", "invalid_api_key",
                    "rate_limit", "empty_batch", "malformed_json",
                    "missing_fields", "duplicate", "oversized_file",
                ],
            }
        return await handler(tenant_id)

    async def _bad_auth_failure(self, tenant_id: str) -> dict:
        """Sign with the wrong secret."""
        invoice = self._outbound.generate(tenant_id)
        result = await self._signer.send_to_relay(
            self._relay_url, tenant_id, invoice,
            bad_signature=True,
        )
        return {
            "error_type": "auth_failure",
            "description": "Sent with invalid HMAC signature",
            "relay_response": result,
        }

    async def _bad_expired_timestamp(self, tenant_id: str) -> dict:
        """Send with a timestamp 10 minutes in the past."""
        invoice = self._outbound.generate(tenant_id)
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        result = await self._signer.send_to_relay(
            self._relay_url, tenant_id, invoice,
            override_timestamp=old_ts,
        )
        return {
            "error_type": "expired_timestamp",
            "description": f"Sent with timestamp 10 min old: {old_ts}",
            "relay_response": result,
        }

    async def _bad_invalid_api_key(self, tenant_id: str) -> dict:
        """Send with a nonexistent API key."""
        invoice = self._outbound.generate(tenant_id)
        result = await self._signer.send_to_relay(
            self._relay_url, tenant_id, invoice,
            override_api_key="FAKE-KEY-XXXXXXXX",
            override_api_secret="fake-secret-that-does-not-exist",
        )
        return {
            "error_type": "invalid_api_key",
            "description": "Sent with nonexistent API key 'FAKE-KEY-XXXXXXXX'",
            "relay_response": result,
        }

    async def _bad_rate_limit(self, tenant_id: str) -> dict:
        """
        Send many rapid requests to trigger rate limiting.
        Note: Relay daily limit is 500. This sends a burst of 10 rapid requests
        to demonstrate the concept without exhausting the daily quota.
        """
        invoice = self._outbound.generate(tenant_id)
        results = []
        for i in range(10):
            r = await self._signer.send_to_relay(
                self._relay_url, tenant_id, invoice,
            )
            results.append(r)
            if r["status_code"] == 429:
                break

        return {
            "error_type": "rate_limit",
            "description": f"Sent {len(results)} rapid requests (daily limit=500)",
            "attempts": len(results),
            "last_response": results[-1] if results else None,
            "hit_429": any(r["status_code"] == 429 for r in results),
        }

    async def _bad_empty_batch(self, tenant_id: str) -> dict:
        """Send an empty JSON array as the invoice file."""
        result = await self._signer.send_raw_to_relay(
            self._relay_url, tenant_id,
            raw_body=b"[]",
            filename="empty_batch.json",
        )
        return {
            "error_type": "empty_batch",
            "description": "Sent empty JSON array as invoice file",
            "relay_response": result,
        }

    async def _bad_malformed_json(self, tenant_id: str) -> dict:
        """Send invalid bytes as a .json file."""
        result = await self._signer.send_raw_to_relay(
            self._relay_url, tenant_id,
            raw_body=b"{this is not valid json!!!",
            filename="malformed.json",
        )
        return {
            "error_type": "malformed_json",
            "description": "Sent invalid bytes as .json file",
            "relay_response": result,
        }

    async def _bad_missing_fields(self, tenant_id: str) -> dict:
        """Send invoice with required fields stripped out."""
        invoice = {"note": "This invoice is missing all required fields"}
        result = await self._signer.send_to_relay(
            self._relay_url, tenant_id, invoice,
        )
        return {
            "error_type": "missing_fields",
            "description": "Sent invoice with no required fields",
            "relay_response": result,
        }

    async def _bad_duplicate(self, tenant_id: str) -> dict:
        """Send the same invoice twice."""
        invoice = self._outbound.generate(tenant_id)
        # First send
        r1 = await self._signer.send_to_relay(
            self._relay_url, tenant_id, invoice,
        )
        # Second send — same invoice_number
        r2 = await self._signer.send_to_relay(
            self._relay_url, tenant_id, invoice,
        )
        return {
            "error_type": "duplicate",
            "description": f"Sent invoice {invoice['invoice_number']} twice",
            "first_response": r1,
            "second_response": r2,
        }

    async def _bad_oversized_file(self, tenant_id: str) -> dict:
        """Send a file exceeding the 10MB limit."""
        # Generate ~11MB of padding
        big_payload = {"data": "X" * (11 * 1024 * 1024)}
        raw = json.dumps(big_payload).encode("utf-8")

        result = await self._signer.send_raw_to_relay(
            self._relay_url, tenant_id,
            raw_body=raw,
            filename="oversized.json",
        )
        return {
            "error_type": "oversized_file",
            "description": f"Sent {len(raw) / 1024 / 1024:.1f}MB file (limit=10MB)",
            "relay_response": result,
        }
