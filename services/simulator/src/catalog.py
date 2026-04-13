"""
Seed Data Catalog — loads and caches all JSON catalogs from disk.

Read-only. All data is loaded once at startup and served from memory.
"""

import json
import os
import random
from pathlib import Path
from typing import Any


class CatalogManager:
    """Loads seed data for a given tenant and provides accessors."""

    def __init__(self, data_dir: str, config_dir: str):
        self._data_dir = Path(data_dir)
        self._config_dir = Path(config_dir)
        self._tenants: dict[str, dict] = {}  # tenant_id → tenant HMAC creds
        self._catalogs: dict[str, dict[str, Any]] = {}  # tenant_id → catalog bundle

        self._load_tenants()

    # ── Loading ────────────────────────────────────────────────────────────

    def _load_tenants(self) -> None:
        path = self._config_dir / "tenants.json"
        with open(path, encoding="utf-8") as f:
            self._tenants = json.load(f)

    def _ensure_loaded(self, tenant_id: str) -> dict[str, Any]:
        if tenant_id not in self._catalogs:
            self._load_tenant_data(tenant_id)
        return self._catalogs[tenant_id]

    def _load_tenant_data(self, tenant_id: str) -> None:
        d = self._data_dir / tenant_id
        if not d.is_dir():
            raise ValueError(f"No data directory for tenant '{tenant_id}' at {d}")

        bundle: dict[str, Any] = {}
        for name in (
            "fee_catalog", "borrowers", "enterprises",
            "suppliers", "branches", "tenant_config",
        ):
            path = d / f"{name}.json"
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    bundle[name] = json.load(f)
            else:
                bundle[name] = [] if name != "tenant_config" else {}

        self._catalogs[tenant_id] = bundle

    # ── Accessors ──────────────────────────────────────────────────────────

    def get_tenant_config(self, tenant_id: str) -> dict:
        return self._ensure_loaded(tenant_id)["tenant_config"]

    def get_hmac_credentials(self, tenant_id: str) -> tuple[str, str]:
        """Return (api_key, api_secret) for the tenant."""
        creds = self._tenants.get(tenant_id)
        if not creds:
            raise ValueError(f"No HMAC credentials for tenant '{tenant_id}'")
        return creds["api_key"], creds["api_secret"]

    def get_firs_service_id(self, tenant_id: str) -> str:
        return self._tenants[tenant_id]["firs_service_id"]

    def get_fee_catalog(self, tenant_id: str) -> list[dict]:
        return self._ensure_loaded(tenant_id)["fee_catalog"]

    def get_random_fees(self, tenant_id: str, n: int = None) -> list[dict]:
        """Pick 1-3 (or n) random fees from the catalog."""
        fees = self.get_fee_catalog(tenant_id)
        count = n if n is not None else random.randint(1, 3)
        return random.sample(fees, min(count, len(fees)))

    def get_borrowers(self, tenant_id: str) -> list[dict]:
        return self._ensure_loaded(tenant_id)["borrowers"]

    def get_random_borrower(self, tenant_id: str) -> dict:
        return random.choice(self.get_borrowers(tenant_id))

    def get_enterprises(self, tenant_id: str) -> list[dict]:
        return self._ensure_loaded(tenant_id)["enterprises"]

    def get_random_enterprise(self, tenant_id: str) -> dict:
        return random.choice(self.get_enterprises(tenant_id))

    def get_random_buyer(self, tenant_id: str) -> dict:
        """80% B2C borrower, 20% B2B enterprise."""
        if random.random() < 0.8:
            return self.get_random_borrower(tenant_id)
        return self.get_random_enterprise(tenant_id)

    def get_suppliers(self, tenant_id: str) -> list[dict]:
        return self._ensure_loaded(tenant_id)["suppliers"]

    def get_supplier(self, tenant_id: str, index: int = None) -> dict:
        suppliers = self.get_suppliers(tenant_id)
        if index is not None:
            return suppliers[index % len(suppliers)]
        return random.choice(suppliers)

    def get_branches(self, tenant_id: str) -> list[dict]:
        return self._ensure_loaded(tenant_id)["branches"]

    def get_random_branch(self, tenant_id: str) -> dict:
        return random.choice(self.get_branches(tenant_id))
