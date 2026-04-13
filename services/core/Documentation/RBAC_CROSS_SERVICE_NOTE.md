# RBAC — Cross-Service Implementation Note

**Date:** 2026-03-25
**From:** Architecture session (Bob + Opus)
**To:** Dedicated RBAC team (scope: ALL Helium services)
**Status:** EXTRACTED from WS6 — RBAC is a gateway concern, not observability

---

## WHY RBAC IS CROSS-SERVICE

RBAC was originally in WS6 (Observability). It's been pulled out because:

1. **RBAC is a front-door concern** — it gates requests before they reach business logic
2. **Every service needs it** — Core, HeartBeat, Relay, Edge, Float SDK
3. **HeartBeat is the authority** — it issues JWTs with permission claims
4. **Consistency matters** — all services must enforce the same permission model

---

## SCOPE

| Service | RBAC Role |
|---|---|
| **HeartBeat** | Issues JWTs with `permissions` claim. Manages roles/permissions tables. Admin UI for role assignment. |
| **Core** | Enforces permissions on CRUD, finalize, audit, report endpoints. FastAPI middleware/dependency. |
| **Relay** | Enforces upload permissions. Validates API key + HMAC (existing) + JWT permissions (new). |
| **Edge** | Enforces transmission permissions. Only authorized users can trigger FIRS submission. |
| **Float SDK** | Reads JWT `permissions` to show/hide UI elements. No enforcement — just UI gating. |

---

## PERMISSION MODEL

### Permissions

```
# Invoice
invoice.create      — Create invoices (via finalize)
invoice.read        — View invoices, search
invoice.update      — Edit invoice fields
invoice.delete      — Soft delete invoices

# Customer
customer.create     — Create customer records
customer.read       — View customers, search
customer.update     — Edit customer fields
customer.delete     — Soft delete customers

# Inventory
inventory.create    — Create inventory records
inventory.read      — View inventory, search
inventory.update    — Edit inventory fields
inventory.delete    — Soft delete inventory

# Upload
upload.create       — Upload files via Relay
upload.read         — View upload status/history

# Transmission
transmission.create — Submit invoices to FIRS via Edge
transmission.read   — View transmission status

# System
system.admin        — Grants ALL permissions
system.view_audit   — View audit logs
system.manage_users — Add/remove users, assign roles
system.manage_config — Update system configuration
```

### Default Roles

| Role | Permissions |
|---|---|
| `viewer` | `*.read` (all read permissions) |
| `operator` | `*.read`, `upload.create`, `invoice.update`, `customer.update`, `inventory.update` |
| `submitter` | All `operator` + `invoice.create`, `transmission.create` |
| `admin` | `system.admin` (all permissions) |

### HeartBeat Tables

```sql
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE auth.roles (
    role_id     TEXT PRIMARY KEY,
    role_name   TEXT NOT NULL UNIQUE,
    description TEXT,
    is_system   BOOLEAN DEFAULT false,  -- system roles can't be deleted
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE auth.permissions (
    permission_id   TEXT PRIMARY KEY,
    permission_key  TEXT NOT NULL UNIQUE,  -- 'invoice.read', 'system.admin'
    description     TEXT,
    category        TEXT NOT NULL          -- 'invoice', 'customer', 'inventory', 'upload', 'transmission', 'system'
);

CREATE TABLE auth.role_permissions (
    role_id         TEXT REFERENCES auth.roles(role_id),
    permission_id   TEXT REFERENCES auth.permissions(permission_id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE auth.user_roles (
    user_id     TEXT NOT NULL,          -- helium_user_id
    role_id     TEXT REFERENCES auth.roles(role_id),
    company_id  TEXT NOT NULL,
    assigned_by TEXT,
    assigned_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, role_id, company_id)
);
```

### JWT Claims

When HeartBeat issues a JWT, it resolves the user's roles → permissions and includes them:

```json
{
    "sub": "helium_user_123",
    "company_id": "tenant_001",
    "roles": ["operator", "submitter"],
    "permissions": [
        "invoice.read", "invoice.create", "invoice.update",
        "customer.read", "customer.update",
        "inventory.read", "inventory.update",
        "upload.create", "upload.read",
        "transmission.create", "transmission.read"
    ],
    "exp": 1711584000,
    "iss": "heartbeat",
    "iat": 1711555200
}
```

---

## PER-SERVICE IMPLEMENTATION

### Core — RBACChecker (FastAPI Dependency)

```python
class RBACChecker:
    def __init__(self, required_permission: str):
        self.required_permission = required_permission

    async def __call__(self, request: Request):
        if not config.rbac_enabled:
            return  # Dev mode bypass

        token = extract_bearer_token(request)
        claims = decode_jwt(token, heartbeat_public_key)  # Ed25519
        permissions = claims.get("permissions", [])

        if "system.admin" in permissions:
            return  # Admin bypasses all checks

        if self.required_permission not in permissions:
            raise PermissionDeniedError(
                f"Missing permission: {self.required_permission}"
            )

# Usage:
@router.get("/api/v1/invoices", dependencies=[Depends(RBACChecker("invoice.read"))])
```

### Core Endpoint → Permission Mapping

| Endpoint | Permission |
|---|---|
| GET /api/v1/invoices | `invoice.read` |
| GET /api/v1/invoice/{id} | `invoice.read` |
| PUT /api/v1/entity/invoice/{id} | `invoice.update` |
| DELETE /api/v1/entity/invoice/{id} | `invoice.delete` |
| GET /api/v1/customers | `customer.read` |
| PUT /api/v1/entity/customer/{id} | `customer.update` |
| DELETE /api/v1/entity/customer/{id} | `customer.delete` |
| GET /api/v1/inventories | `inventory.read` |
| PUT /api/v1/entity/inventory/{id} | `inventory.update` |
| DELETE /api/v1/entity/inventory/{id} | `inventory.delete` |
| POST /api/v1/search | `invoice.read` |
| POST /api/v1/finalize | `invoice.create` + `transmission.create` |
| GET /api/v1/audit | `system.view_audit` |
| GET /api/v1/notifications | (any authenticated) |
| POST /api/v1/reports/generate | `invoice.read` |
| GET /metrics | (no auth — Prometheus scrape) |

### Float SDK — UI Permission Gating

SDK reads JWT permissions and exposes them to Float:

```python
class PermissionManager:
    def __init__(self, jwt_claims: dict):
        self.permissions = set(jwt_claims.get("permissions", []))

    def can(self, permission: str) -> bool:
        return "system.admin" in self.permissions or permission in self.permissions

# Float UI uses this:
# if sdk.permissions.can("invoice.delete"):
#     show_delete_button()
# else:
#     hide_delete_button()
```

---

## DEV MODE

All services default to `RBAC_ENABLED=false` during development. When false:
- No JWT required on requests
- All permissions granted implicitly
- RBACChecker returns immediately

Production deployment sets `RBAC_ENABLED=true`.

---

## BUILD ORDER

1. **HeartBeat:** roles/permissions tables, seed default roles, include permissions in JWT
2. **Core:** RBACChecker middleware, apply to all endpoints
3. **Relay:** Add JWT verification alongside existing HMAC auth
4. **Edge:** Add JWT verification for transmission endpoints
5. **Float SDK:** PermissionManager, UI gating

Steps 2-4 can run in parallel after step 1.

---

**Last Updated:** 2026-03-25
