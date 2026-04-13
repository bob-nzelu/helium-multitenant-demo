"""
Tenant configuration for multi-tenant demo.

Each tenant (AB MFB, Abbey Mortgage, etc.) gets their own API key,
service ID (IRN prefix), and optional field name overrides.

tenants.json is loaded once at startup. API key → TenantConfig lookup is O(1).
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Default field names — used when a tenant's "fields" map is empty or missing a key
DEFAULT_FIELDS = {
    "transaction_id":   "transaction_id",
    "fee_amount":       "fee_amount",
    "vat_amount":       "vat_amount",
    "description":      "description",
    "transaction_date": "transaction_date",
    "branch":           "branch",
    "batch_id":         "batch_id",
    "buyer_name":       "buyer_name",
    "buyer_tin":        "buyer_tin",
    "buyer_address":    "buyer_address",
}


@dataclass
class TenantConfig:
    """Configuration for a single demo tenant."""
    tenant_id:  str                          # e.g. "abmfb", "abbey"
    api_key:    str                          # e.g. "ABMFB-2026-K7X9MNPQ"
    api_secret: str                          # HMAC shared secret
    service_id: str                          # IRN prefix, e.g. "ABMFB", "ABBEY"
    name:       str                          # Display name, e.g. "AB Microfinance Bank"
    fields:     Dict[str, str] = field(default_factory=dict)  # field name overrides
    format_type: str = "flat"                # "flat" (ABMFB default) or "ubl" (Abbey)

    def get_field(self, name: str) -> str:
        """Get the tenant's field name, falling back to default."""
        return self.fields.get(name) or DEFAULT_FIELDS.get(name, name)

    @property
    def is_ubl(self) -> bool:
        return self.format_type == "ubl"


def load_tenants(path: str) -> Dict[str, TenantConfig]:
    """
    Load tenant registry from a JSON file.

    Args:
        path: Path to tenants.json

    Returns:
        Dict keyed by api_key → TenantConfig (for O(1) auth lookup)
    """
    with open(path, "r") as f:
        data = json.load(f)

    registry: Dict[str, TenantConfig] = {}

    for tenant_id, cfg in data.items():
        tenant = TenantConfig(
            tenant_id=tenant_id,
            api_key=cfg["api_key"],
            api_secret=cfg["api_secret"],
            service_id=cfg["service_id"],
            name=cfg["name"],
            fields=cfg.get("fields", {}),
            format_type=cfg.get("format_type", "flat"),
        )
        registry[tenant.api_key] = tenant
        logger.info(f"Tenant loaded: {tenant_id} ({tenant.name}) key={tenant.api_key[:8]}...")

    return registry
