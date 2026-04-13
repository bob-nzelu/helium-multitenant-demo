"""
Tenant routing for demo infrastructure.

In production, each tenant gets their own Relay deployment.
For the demo, one Relay serves multiple tenants — API key determines context.

tenants.json maps tenant_id → config (api_key, api_secret, service_id, name).
API key lookup is O(1) via registry dict keyed by api_key.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

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
    tenant_id:   str
    api_key:     str
    api_secret:  str
    service_id:  str          # FIRS service ID / IRN prefix
    name:        str
    fields:      Dict[str, str] = field(default_factory=dict)
    format_type: str = "flat"  # "flat" or "ubl"

    def get_field(self, name: str) -> str:
        return self.fields.get(name) or DEFAULT_FIELDS.get(name, name)

    @property
    def is_ubl(self) -> bool:
        return self.format_type == "ubl"


def load_tenants(path: str) -> Dict[str, TenantConfig]:
    """Load tenant registry from tenants.json. Returns dict keyed by api_key."""
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
