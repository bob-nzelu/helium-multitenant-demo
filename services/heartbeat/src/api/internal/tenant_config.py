"""
Tenant Configuration API — Full config for Float/SDK and backend services.

Endpoints:
    GET  /api/v1/config/{float_id}     — Full tenant config for Float/SDK
    POST /api/v1/config/register        — Float instance registration
    GET  /api/v1/heartbeat/config       — Full config for backend services (webhook contract)

Per-service response shaping:
    Float/SDK gets: tenant identity, branding, user, bank accounts, endpoints, behaviour, schema
    Backend services get: tenant identity, FIRS, SMTP, NAS, crypto, endpoints, tier limits

See: TENANT_CONFIG_HANDOFF_SPEC.md + WEBHOOK_CONFIG_CONTRACT.md
"""

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel, Field

from ...auth.dependencies import get_optional_user_token
from ...database.config_db import get_config_database

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tenant-config"])


# ── Pydantic Response Models ─────────────────────────────────────────────

class FloatInstanceModel(BaseModel):
    float_id: str
    machine_guid: Optional[str] = None
    mac_address: Optional[str] = None
    computer_name: Optional[str] = None
    registered_at: Optional[str] = None
    tenant_id: str


class TenantModel(BaseModel):
    tenant_id: str
    company_name: str
    trading_name: Optional[str] = None
    tin: Optional[str] = None
    rc_number: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state_code: Optional[str] = None
    country_code: str = "NG"
    email: Optional[str] = None
    phone: Optional[str] = None
    default_currency: str = "NGN"
    default_due_date_days: int = 30
    invoice_prefix: Optional[str] = None


class BrandingModel(BaseModel):
    logo_base64: Optional[str] = None
    logo_mime_type: Optional[str] = None
    signature_enabled: bool = True
    signer_name: Optional[str] = None
    signer_title: Optional[str] = None
    signer_email: Optional[str] = None
    signature_image_base64: Optional[str] = None


class UserModel(BaseModel):
    user_id: str
    display_name: str
    email: str
    role: str
    title: Optional[str] = None
    phone: Optional[str] = None
    last_login_at: Optional[str] = None
    avatar_base64: Optional[str] = None
    avatar_mime_type: Optional[str] = None
    permissions: List[str] = []


class BankAccountModel(BaseModel):
    bank_name: str
    account_name: str
    account_number: str
    bank_code: Optional[str] = None
    currency: str = "NGN"
    is_primary: bool = False
    display_order: int = 0


class ServiceEndpointModel(BaseModel):
    service_name: str
    api_url: str
    sse_url: Optional[str] = None
    # NOTE: api_key/api_secret are NOT included here.
    # Credentials live in registry.db and are provisioned per-machine
    # by the Installer, not delivered via config API.


class RegistrationModel(BaseModel):
    authority: str
    registration_id: Optional[str] = None
    registration_date: Optional[str] = None
    expiry_date: Optional[str] = None
    status: str = "active"
    metadata: Dict[str, Any] = {}


class LicenseModel(BaseModel):
    license_id: Optional[str] = None
    tenant_id: str
    tier: str = "standard"
    max_users: int = 10
    max_invoices_monthly: int = 5000
    features: Dict[str, bool] = {}
    issued_at: Optional[str] = None
    expires_at: Optional[str] = None
    status: str = "active"
    signature: Optional[str] = None


class SecurityBehaviourModel(BaseModel):
    pin_max_attempts: int = 5
    pin_typein_interval_hours: float = 5.0
    inactivity_timeout_minutes: int = 60
    lull_timeout_seconds: int = 60
    deactivate_timeout_seconds: int = 10
    set_new_pin_timeout_minutes: int = 40
    session_check_interval_seconds: int = 30
    session_timeout_hours: int = 8
    require_2fa: bool = False


class SyncBehaviourModel(BaseModel):
    connection_timeout_seconds: int = 30
    sync_timeout_seconds: int = 60
    polling_fallback_enabled: bool = True
    polling_interval_seconds: int = 30


class UploadBehaviourModel(BaseModel):
    max_file_size_mb: int = 5
    max_batch_size_mb: int = 10
    daily_upload_limit: int = 100
    allowed_extensions: List[str] = [".pdf", ".xml", ".json", ".csv", ".xlsx"]
    bulk_preview_timeout_seconds: int = 310


class CacheBehaviourModel(BaseModel):
    max_invoices: int = 5000
    search_cache_ttl_seconds: int = 60
    search_cache_max_size: int = 1000
    blob_file_cache_ttl_hours: int = 48


class RateLimitBehaviourModel(BaseModel):
    per_minute: int = 100
    per_hour: int = 1000


class BehaviourModel(BaseModel):
    security: SecurityBehaviourModel = SecurityBehaviourModel()
    sync: SyncBehaviourModel = SyncBehaviourModel()
    uploads: UploadBehaviourModel = UploadBehaviourModel()
    cache: CacheBehaviourModel = CacheBehaviourModel()
    rate_limits: RateLimitBehaviourModel = RateLimitBehaviourModel()


class SchemaModel(BaseModel):
    sync_db_version: str = "5.2"
    canonical_invoice_version: str = "2.1.3.0"
    canonical_customer_version: str = "1.2.0"
    canonical_inventory_version: str = "1.1.0"
    canonical_blob_version: str = "1.3.0"
    canonical_audit_version: str = "1.0.0"
    migration_available: bool = False
    migration_url: Optional[str] = None


class FloatConfigResponse(BaseModel):
    """Full config response for Float/SDK — GET /api/v1/config/{float_id}"""
    config_version: str = "1.0.0"
    generated_at: str

    float_instance: FloatInstanceModel
    tenant: TenantModel
    branding: BrandingModel
    user: UserModel
    bank_accounts: List[BankAccountModel] = []
    service_endpoints: List[ServiceEndpointModel] = []
    registrations: List[RegistrationModel] = []
    behaviour: BehaviourModel = BehaviourModel()
    schema_info: SchemaModel = Field(default_factory=SchemaModel, alias="schema")


class FloatRegisterRequest(BaseModel):
    machine_guid: str = Field(..., min_length=1)
    mac_address: str = Field(..., min_length=1)
    computer_name: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)


class FloatRegisterResponse(BaseModel):
    float_id: str
    registered_at: str


# ── Helper: Build config from DB ─────────────────────────────────────────

def _blob_to_base64(blob: Optional[bytes]) -> Optional[str]:
    """Convert BLOB to base64 string, or None if empty."""
    if blob is None:
        return None
    return base64.b64encode(blob).decode("utf-8")


def _build_float_config(
    config_db,
    float_row: Dict[str, Any],
    tenant_row: Dict[str, Any],
    user_row: Dict[str, Any],
) -> FloatConfigResponse:
    """Assemble the full Float config response from config.db data."""
    import json

    tenant_id = tenant_row["tenant_id"]

    # Bank accounts
    bank_rows = config_db.execute_query(
        "SELECT * FROM tenant_bank_accounts WHERE tenant_id = ? ORDER BY display_order",
        (tenant_id,),
    )

    # Service endpoints
    endpoint_rows = config_db.execute_query(
        "SELECT * FROM tenant_service_endpoints WHERE tenant_id = ?",
        (tenant_id,),
    )

    # Registrations
    reg_rows = config_db.execute_query(
        "SELECT * FROM tenant_registrations WHERE tenant_id = ?",
        (tenant_id,),
    )

    # Parse permissions JSON
    perms = []
    if user_row.get("permissions"):
        try:
            perms = json.loads(user_row["permissions"])
        except (json.JSONDecodeError, TypeError):
            perms = []

    # Build licence from tier_limits + license.db (stub for now)
    tier = tenant_row.get("tier", "standard")
    tier_limits = {}
    try:
        tier_rows = config_db.execute_query(
            "SELECT limit_key, limit_value FROM tier_limits WHERE tier = ?",
            (tier,),
        )
        tier_limits = {r["limit_key"]: r["limit_value"] for r in tier_rows}
    except Exception:
        pass

    # Build behaviour from tier_limits
    daily_limit = int(tier_limits.get("daily_upload_limit", 100))
    max_invoices = int(tier_limits.get("max_invoices_cached", 5000))

    # Parse registration metadata
    registrations = []
    for reg in reg_rows:
        meta = {}
        if reg.get("metadata"):
            try:
                meta = json.loads(reg["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        registrations.append(RegistrationModel(
            authority=reg["authority"],
            registration_id=reg.get("registration_id"),
            registration_date=reg.get("registration_date"),
            expiry_date=reg.get("expiry_date"),
            status=reg.get("status", "active"),
            metadata=meta,
        ))

    # Schema versions
    sync_db_version = "5.2"
    canonical_invoice_version = "2.1.3.0"

    return FloatConfigResponse(
        config_version="1.0.0",
        generated_at=datetime.now(timezone.utc).isoformat(),

        float_instance=FloatInstanceModel(
            float_id=float_row["float_id"],
            machine_guid=float_row.get("machine_guid"),
            mac_address=float_row.get("mac_address"),
            computer_name=float_row.get("computer_name"),
            registered_at=float_row.get("registered_at"),
            tenant_id=tenant_id,
        ),

        tenant=TenantModel(
            tenant_id=tenant_id,
            company_name=tenant_row["company_name"],
            trading_name=tenant_row.get("trading_name"),
            tin=tenant_row.get("tin"),
            rc_number=tenant_row.get("rc_number"),
            address=tenant_row.get("address"),
            city=tenant_row.get("city"),
            state_code=tenant_row.get("state_code"),
            country_code=tenant_row.get("country_code", "NG"),
            email=tenant_row.get("email"),
            phone=tenant_row.get("phone"),
            default_currency=tenant_row.get("default_currency", "NGN"),
            default_due_date_days=tenant_row.get("default_due_date_days", 30),
            invoice_prefix=tenant_row.get("invoice_prefix"),
        ),

        branding=BrandingModel(
            logo_base64=_blob_to_base64(tenant_row.get("logo_image")),
            logo_mime_type=tenant_row.get("logo_mime_type"),
            signature_enabled=bool(tenant_row.get("signature_enabled", 1)),
            signer_name=tenant_row.get("signer_name"),
            signer_title=tenant_row.get("signer_title"),
            signer_email=tenant_row.get("signer_email"),
            signature_image_base64=_blob_to_base64(tenant_row.get("signature_image")),
        ),

        user=UserModel(
            user_id=user_row["user_id"],
            display_name=user_row["display_name"],
            email=user_row["email"],
            role=user_row["role"],
            title=user_row.get("title"),
            phone=user_row.get("phone"),
            last_login_at=user_row.get("last_login_at"),
            avatar_base64=_blob_to_base64(user_row.get("avatar_image")),
            avatar_mime_type=user_row.get("avatar_mime_type"),
            permissions=perms,
        ),

        bank_accounts=[
            BankAccountModel(
                bank_name=b["bank_name"],
                account_name=b["account_name"],
                account_number=b["account_number"],
                bank_code=b.get("bank_code"),
                currency=b.get("currency", "NGN"),
                is_primary=bool(b.get("is_primary", 0)),
                display_order=b.get("display_order", 0),
            )
            for b in bank_rows
        ],

        service_endpoints=[
            ServiceEndpointModel(
                service_name=e["service_name"],
                api_url=e["api_url"],
                sse_url=e.get("sse_url"),
            )
            for e in endpoint_rows
        ],

        registrations=registrations,

        behaviour=BehaviourModel(
            security=SecurityBehaviourModel(
                require_2fa=bool(tenant_row.get("require_2fa", 0)),
            ),
            uploads=UploadBehaviourModel(
                daily_upload_limit=daily_limit,
            ),
            cache=CacheBehaviourModel(
                max_invoices=max_invoices,
            ),
        ),

        schema=SchemaModel(
            sync_db_version=sync_db_version,
            canonical_invoice_version=canonical_invoice_version,
        ),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get(
    "/api/v1/config/{float_id}",
    response_model=FloatConfigResponse,
    summary="Full tenant config for Float/SDK",
)
async def get_float_config(
    float_id: str,
    raw_token: Optional[str] = Depends(get_optional_user_token),
):
    """
    Returns complete tenant configuration for a registered Float instance.

    Called by SDK at startup and on-demand (config.updated SSE event).
    Response includes: tenant identity, branding, user, bank accounts,
    service endpoints, registrations, licence, behaviour, schema versions.
    """
    config_db = get_config_database()

    # Look up float instance
    float_rows = config_db.execute_query(
        "SELECT * FROM float_instances WHERE float_id = ?",
        (float_id,),
    )
    if not float_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "FLOAT_NOT_FOUND", "message": f"Float instance '{float_id}' not registered"},
        )
    float_row = float_rows[0]
    tenant_id = float_row["tenant_id"]

    # Look up tenant
    tenant_rows = config_db.execute_query(
        "SELECT * FROM tenant_config WHERE tenant_id = ?",
        (tenant_id,),
    )
    if not tenant_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TENANT_NOT_FOUND", "message": f"Tenant '{tenant_id}' not found"},
        )

    # Look up user (first active user for this float)
    user_rows = config_db.execute_query(
        "SELECT * FROM float_users WHERE tenant_id = ? AND is_active = 1 ORDER BY created_at LIMIT 1",
        (tenant_id,),
    )
    if not user_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "USER_NOT_FOUND", "message": f"No active user for tenant '{tenant_id}'"},
        )

    # Update last_seen_at
    now_iso = datetime.now(timezone.utc).isoformat()
    config_db.execute_update(
        "UPDATE float_instances SET last_seen_at = ?, updated_at = ? WHERE float_id = ?",
        (now_iso, now_iso, float_id),
    )

    return _build_float_config(config_db, float_row, tenant_rows[0], user_rows[0])


@router.post(
    "/api/v1/config/register",
    response_model=FloatRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new Float instance",
)
async def register_float(request: FloatRegisterRequest):
    """
    First-time Float registration. Called once per installation.

    If the same machine (machine_guid + mac_address) is already registered,
    returns the existing float_id (idempotent).
    """
    config_db = get_config_database()

    # Check if already registered (idempotent)
    existing = config_db.execute_query(
        "SELECT float_id, registered_at FROM float_instances WHERE machine_guid = ? AND mac_address = ?",
        (request.machine_guid, request.mac_address),
    )
    if existing:
        return FloatRegisterResponse(
            float_id=existing[0]["float_id"],
            registered_at=existing[0]["registered_at"],
        )

    # Verify tenant exists
    tenant = config_db.execute_query(
        "SELECT tenant_id FROM tenant_config WHERE tenant_id = ?",
        (request.tenant_id,),
    )
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "TENANT_NOT_FOUND", "message": f"Tenant '{request.tenant_id}' not found"},
        )

    # Generate float_id
    from uuid6 import uuid7
    float_id = f"float-{uuid7()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    config_db.execute_insert(
        """INSERT INTO float_instances
           (float_id, tenant_id, machine_guid, mac_address, computer_name,
            registered_at, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (
            float_id, request.tenant_id,
            request.machine_guid, request.mac_address, request.computer_name,
            now_iso, now_iso, now_iso,
        ),
    )

    logger.info(
        f"Float registered: float_id={float_id}, tenant={request.tenant_id}, "
        f"machine={request.computer_name}"
    )

    return FloatRegisterResponse(float_id=float_id, registered_at=now_iso)


@router.get(
    "/api/v1/heartbeat/config",
    summary="Full config for backend services (webhook contract)",
)
async def get_backend_config(
    authorization: Optional[str] = Header(None),
):
    """
    Returns full tenant configuration for backend services (Core, Edge, Relay).

    Per WEBHOOK_CONFIG_CONTRACT.md: services fetch this at startup, cache it,
    and re-fetch when they receive a POST /api/v1/webhook/config_changed call.

    Includes: tenant identity, FIRS config, SMTP, NAS, crypto keys,
    service endpoints, tier limits, feature flags.
    """
    config_db = get_config_database()

    # Get tenant config (single-tenant: first row)
    tenant_rows = config_db.execute_query("SELECT * FROM tenant_config LIMIT 1")
    if not tenant_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NO_TENANT", "message": "No tenant configured"},
        )

    tenant = tenant_rows[0]
    tenant_id = tenant["tenant_id"]
    tier = tenant.get("tier", "standard")

    # FIRS config
    firs_rows = config_db.execute_query(
        "SELECT * FROM tenant_firs_config WHERE tenant_id = ?", (tenant_id,)
    )
    firs = dict(firs_rows[0]) if firs_rows else {}

    # SMTP config
    smtp_rows = config_db.execute_query(
        "SELECT * FROM tenant_smtp_config WHERE tenant_id = ?", (tenant_id,)
    )

    # NAS config
    nas_rows = config_db.execute_query(
        "SELECT * FROM tenant_nas_config WHERE tenant_id = ?", (tenant_id,)
    )

    # Crypto keys
    crypto_rows = config_db.execute_query(
        "SELECT * FROM tenant_crypto_keys WHERE tenant_id = ?", (tenant_id,)
    )

    # Endpoints
    endpoint_rows = config_db.execute_query(
        "SELECT * FROM tenant_service_endpoints WHERE tenant_id = ?", (tenant_id,)
    )

    # Tier limits
    tier_rows = config_db.execute_query(
        "SELECT limit_key, limit_value FROM tier_limits WHERE tier = ?", (tier,)
    )

    # Feature flags
    flag_rows = config_db.execute_query(
        "SELECT flag_name, is_enabled FROM feature_flags"
    )

    # Registrations
    reg_rows = config_db.execute_query(
        "SELECT * FROM tenant_registrations WHERE tenant_id = ?", (tenant_id,)
    )

    return {
        "config_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant": {
            "tenant_id": tenant_id,
            "company_name": tenant["company_name"],
            "trading_name": tenant.get("trading_name"),
            "tin": tenant.get("tin"),
            "rc_number": tenant.get("rc_number"),
            "address": tenant.get("address"),
            "city": tenant.get("city"),
            "state_code": tenant.get("state_code"),
            "country_code": tenant.get("country_code", "NG"),
            "email": tenant.get("email"),
            "phone": tenant.get("phone"),
            "tier": tier,
            "default_currency": tenant.get("default_currency", "NGN"),
            "invoice_prefix": tenant.get("invoice_prefix"),
        },
        "firs": firs,
        "smtp": [dict(s) for s in smtp_rows],
        "nas": [dict(n) for n in nas_rows],
        "crypto_keys": [dict(c) for c in crypto_rows],
        "service_endpoints": [dict(e) for e in endpoint_rows],
        "registrations": [dict(r) for r in reg_rows],
        "tier_limits": {r["limit_key"]: r["limit_value"] for r in tier_rows},
        "feature_flags": {r["flag_name"]: bool(r["is_enabled"]) for r in flag_rows},
    }
