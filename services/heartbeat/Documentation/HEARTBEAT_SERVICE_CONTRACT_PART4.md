# HeartBeat Service Contract — Part 4: Authentication, Tenancy Governance & License Enforcement

**Version:** 1.0
**Date:** 2026-02-23
**Status:** AUTHORITATIVE — supersedes stub at Part 1 §3.8
**Audience:** All service teams (Core, Relay, HIS, Float/SDK, Edge), Pronalytics Engineering
**Changelog:** v1.0 — Initial release. Full auth ownership by HeartBeat. JWT replaces
X-User-ID platform-wide. Tenancy governance, license enforcement, enrollment, and
all-Father key model introduced.

---

## Overview

HeartBeat owns **all authentication and tenancy governance** for the Helium platform.
This covers:

| Domain | What HeartBeat Manages |
|---|---|
| **User Identity** | JWT issuance, validation, refresh, step-up auth |
| **Roles & Permissions** | Owner, Admin, Operator, Support — assignment and enforcement |
| **Session Management** | Token lifecycle, local persistence, expiry |
| **SSO Integration** | Microsoft Entra / Azure AD adapter (Enterprise tier) |
| **Tenancy Authority** | Every new component must enroll through HeartBeat |
| **License Enforcement** | Ed25519-signed license governs all tenancy limits offline |
| **Approval Workflows** | Admin creation, feature assignment — pending state with Owner approval |
| **Elevated Access** | Time-bound grants with configurable scope and expiry notifications |
| **First-Run Bootstrap** | Initial Owner setup ceremony on first application start |
| **All-Father Override** | Pronalytics-signed emergency commands for extraordinary actions |
| **Software Updates** | HeartBeat verifies and applies Pronalytics-signed platform updates |
| **Service Lifecycle** | HeartBeat starts with OS, supervises and restarts all child services |

HeartBeat does **NOT**:
- Validate invoice content (Core)
- Submit to FIRS (Edge)
- Ingest files from users (Relay)
- Run business logic (Core + HIS)
- Store ERP data (Core)
- Track queue processing status (Core)

---

## Table of Contents

1. [The JWT Model — X-User-ID Is Deprecated](#1-the-jwt-model--x-user-id-is-deprecated)
2. [Actor Types](#2-actor-types)
3. [External System Calls — User ID Enforcement](#3-external-system-calls--user-id-enforcement)
4. [Role Hierarchy](#4-role-hierarchy)
5. [First-Run Bootstrap](#5-first-run-bootstrap)
6. [Authentication Policies](#6-authentication-policies)
7. [Step-Up Authentication](#7-step-up-authentication)
8. [SSE Authentication & Stream Filtering](#8-sse-authentication--stream-filtering)
9. [Token Introspection — Internal Service Verification](#9-token-introspection--internal-service-verification)
10. [Approval Workflows](#10-approval-workflows)
11. [Time-Bound Elevated Access](#11-time-bound-elevated-access)
12. [Tenancy Governance — Enrollment](#12-tenancy-governance--enrollment)
13. [License Enforcement](#13-license-enforcement)
14. [All-Father Key — Pronalytics Override](#14-all-father-key--pronalytics-override)
15. [Service Lifecycle Management](#15-service-lifecycle-management)
16. [auth.db Schema](#16-authdb-schema)
17. [Auth API Endpoint Reference](#17-auth-api-endpoint-reference)
18. [Error Codes](#18-error-codes)
19. [Environment Variables](#19-environment-variables)
20. [Implementation Status](#20-implementation-status)

---

## 1. The JWT Model — X-User-ID Is Deprecated

### Previous Model (Deprecated)
Services previously propagated user identity via an unverified `X-User-ID` header.
Any caller could claim any identity. HeartBeat stored it without verification.
**This model is deprecated and will be removed.**

### Current Model
Every human-initiated request across Helium carries a **signed JWT issued by
HeartBeat**. Services verify the JWT — they do not trust assertions.

When Float makes a call to any downstream service (Relay, Core, HIS, Edge) on
behalf of a logged-in user, it attaches the user's JWT:

```
Authorization: Bearer {user_jwt}
```

Downstream services verify the JWT against HeartBeat's introspection endpoint
before processing sensitive operations. Routine operations use a local cache
refreshed every 30-60 seconds.

### JWT Structure
HeartBeat issues JWTs signed with its **Ed25519 private key**. Every service
has HeartBeat's public key and can verify the signature locally.

```json
{
  "sub": "usr-abc123",
  "tenant_id": "abbey-001",
  "role": "admin",
  "permissions": [
    "invoice.view",
    "invoice.approve",
    "user.create.operator",
    "user.create.support"
  ],
  "last_auth_at": "2026-02-23T10:00:00Z",
  "issued_at": "2026-02-23T10:00:00Z",
  "expires_at": "2026-02-23T18:00:00Z",
  "jti": "tok-9f8e7d6c5b4a3e2d"
}
```

### Migration Path
`X-User-ID` headers are accepted during the migration period but generate a
deprecation warning in HeartBeat's audit log. Once all services are updated
to pass JWTs, `X-User-ID` support will be removed.

---

## 2. Actor Types

Every action in the Helium platform is attributed to one of two actor types.
The audit trail is never blank.

### Human Actors
Users authenticated through Float with a valid HeartBeat JWT. Identity is
cryptographically verified.

### System Actors
ERPs, automated pipelines, and machine-to-machine integrations. They have no
human behind them. They authenticate using the existing API key + secret model
(Bearer `key:secret`). They do NOT carry a user JWT.

### Audit Event Identity Fields

Every audit event carries:

```json
{
  "actor_type": "human",
  "actor_id": "usr-abc123",
  "actor_name": "john.adeyemi@abbey.com",
  "verified_user_id": "usr-abc123",
  "asserted_user_id": null
}
```

For system actors:
```json
{
  "actor_type": "system",
  "actor_id": "rl_prod_a1b2c3d4...",
  "actor_name": "SAP_GTBank_Production",
  "verified_user_id": null,
  "asserted_user_id": "john.adeyemi@gtbank.com"
}
```

`verified_user_id` — comes from a validated JWT. Cryptographically confirmed.
`asserted_user_id` — comes from `X-User-ID` header on system actor calls.
Recorded but not verified. Auditors can distinguish the two.

---

## 3. External System Calls — User ID Enforcement

### The Requirement
When an external system calls into Helium, it should attempt to supply a
user ID — the human on whose behalf the system is acting. This is not always
possible. HeartBeat governs whether absence of a user ID is acceptable per
integration via an `enforced` flag.

### Integration Config
HeartBeat stores per-integration configuration in config.db:

```json
{
  "integration_id": "sap_gtbank_production",
  "integration_name": "SAP GTBank Production",
  "api_key_prefix": "sy_prod_a1b2c3",
  "actor_type": "system",
  "user_id_enforced": false
}
```

**`user_id_enforced: false` (default):**
- System may supply `X-User-ID` header — if present, recorded as `asserted_user_id`
- If absent, call proceeds — logged as pure system actor event
- No request rejected for missing user ID

**`user_id_enforced: true`:**
- Helium will **not honour the request** if no user ID is found
- Relay (or receiving service) checks integration config for the calling API key
- If enforced and no `X-User-ID` header present, request is rejected:

```json
{
  "error_code": "USER_ID_REQUIRED",
  "message": "This integration requires a user identity on every request",
  "integration_id": "sap_gtbank_production"
}
```

- Rejection is still audit-logged with the system actor's identity

### Use Case
Regulated clients (e.g. Abbey Mortgage Bank) may require full human
accountability on all ERP-initiated flows for CBN audit compliance.
The `enforced` flag is configurable by Owner or Admin. All changes are
audit-logged.

### How External Systems Supply User ID
```
X-User-ID: john.adeyemi@abbey.com
```

This is an assertion, not a verified identity. HeartBeat records it as
`asserted_user_id` in the audit trail — distinct from `verified_user_id`
which only comes from a validated JWT.

---

## 4. Role Hierarchy

All four roles exist within the client environment. There is no cross-tenant
role. Pronalytics access is handled at the provisioning layer via the
all-Father key (see Section 14), entirely outside this role system.

### Owner
**Authority:** Foundational. The packaging-time Owner (Owner A) is the
ultimate authority within the tenancy.

- Maximum **2 per tenant** — hard system constraint
- Owner A (provisioned at packaging time) cannot be deactivated by Owner B
- Owner A can deactivate Owner B
- Owner A self-deactivation requires Pronalytics intervention via the
  all-Father key
- Sees and accesses everything by default — no restrictions
- Creates and deactivates Owner B
- Must approve all Admin creation requests
- Must approve all special Admin feature assignments
- Can grant time-bound elevated access to any user
- To create a third Owner, an existing Owner must first be deactivated
- Intended for: CEO, CFO, IT Director

### Admin
**Authority:** Full view, scoped write.

- Full view rights by default across the platform
- Write rights are not automatic — assigned by Owner
- Can create Operators and Support users
- Cannot create another Admin without Owner approval:
  - Creation request enters `pending_approval` state
  - Visible to initiating Admin as "Pending Owner Approval"
  - Owner receives notification and must act before account activates
- Special administrative feature assignments also require Owner approval
  (same pending flow)
- Can assign specific view and write rights to Operators
- Can grant time-bound elevated access to any user
- Different Admins can have different administrative features
- Intended for: Finance managers, compliance officers, senior IT staff

### Operator
**Authority:** Explicitly scoped only.

- No default view rights — every view and write right must be explicitly
  granted by an Admin
- Cannot create any users
- Cannot approve any workflows
- Works within a specific lane defined by their assigned Admin
- Intended for: Accountants, front desk staff, finance operators

### Support
**Authority:** Diagnostic view only by default.

- Default view rights limited to:
  - Health endpoints and service status
  - IP addresses and ports
  - Integration diagnostics
  - No invoice data, no business data, no financial records
- Elevated access only via time-bound grant from Admin or Owner
- Scope of elevated grant is configurable — may or may not include
  financial/invoice data, determined by the granting user
- Intended for: Pronalytics support staff diagnosing client deployments

### Permission Model
Permissions are atomic and bundled into roles. Roles are the default
bundle — individual permissions can be added to or removed from a user
beyond their role defaults where the role system allows it.

```
Permission naming convention: {resource}.{action}

Examples:
  invoice.view
  invoice.approve
  invoice.upload
  user.create.operator
  user.create.support
  user.create.admin          ← requires Owner approval to assign
  user.deactivate
  config.view
  config.write
  audit.view
  health.read
  integration.config.write
```

---

## 5. First-Run Bootstrap

### Context
HeartBeat does not exist when the first Owner is created. The installer
creates HeartBeat and seeds the first Owner. HeartBeat takes over from
first application start.

### Installer Responsibilities
During installation the installer:
1. Creates HeartBeat and bootstraps all databases including auth.db
   and license.db
2. Seeds Owner A account into auth.db:
   - Password: bcrypt hash of the temporary password
   - `must_reset_password: true`
   - `mfa_configured: false`
   - `is_first_run: true`
   - `owner_sequence: "A"`
3. Writes the signed license file into license.db
4. Clears the temporary password from all installer artefacts after seeding

### First Application Start — Setup Ceremony
HeartBeat detects `is_first_run: true` on the Owner record and enters
bootstrap mode.

```
Step 1: Owner presents temporary password
  → HeartBeat validates against bcrypt hash in auth.db
  → Issues restricted bootstrap token (setup screens only)

Step 2: Owner sets new permanent password
  → bcrypt hashed, stored in auth.db
  → must_reset_password: false

Step 3: Notification channel configuration
  → Owner configures SMTP, SMS, or WhatsApp
  → HeartBeat sends a test message to confirm channel works
  → At least one channel must be confirmed before proceeding

Step 4: MFA setup (if mfa_enabled in license)
  → HeartBeat sends verification code via configured channel
  → Owner confirms receipt and enters code
  → mfa_configured: true

Step 5: Setup complete
  → is_first_run: false
  → Bootstrap token invalidated
  → Full auth policies activate
  → Owner issued full session JWT
  → Taken to dashboard
```

If any step fails, Owner can retry from that step — they are not
locked out. The bootstrap token remains valid for 1 hour before
requiring re-entry of the temporary password.

### Temporary Password Expiry
If the temporary password expires (14-day window from packaging):
- Installation is blocked — seeded credential invalidated
- Owner cannot progress past Step 1
- Pronalytics team runs re-provisioning: generates fresh temporary
  password and dispatches via email
- This is a Pronalytics-side admin operation outside HeartBeat's scope

---

## 6. Authentication Policies

Auth policies activate only after first-run setup is complete.

### Session Token
- Issued at login — signed Ed25519 JWT
- Stored in auth.db (server side) and persisted securely on the client:
  - **Windows:** Windows Credential Manager (DPAPI)
  - **Linux:** Secret Service API (libsecret/keyring)
- Valid for **8 hours** (one working day)
- Silently refreshed every **1 hour** in background — user never notices
- If Float app is closed and reopened within the 8-hour window,
  the persisted token is reused — user does not need to log in again
- If the 8-hour window has expired, user must log in again

### Identity Providers
| Provider | Tier | Requirement |
|---|---|---|
| Local credentials (Authlib) | All tiers | Default, always available |
| Microsoft Entra / Azure AD SSO | Enterprise | License feature flag `sso_entra: true` |

All providers normalise to the same JWT shape. HeartBeat is always
the JWT issuer regardless of upstream identity provider.

**Entra SSO and step-up:** When a user re-authenticates via Entra for
a step-up operation, HeartBeat trusts Microsoft's re-auth timestamp
as `last_auth_at`. No duplicate re-verification by HeartBeat is required.

### MFA Policy
- Configured at tenant level via license feature flag `mfa_enabled`
- Required at initial login
- Required on every Immediate re-auth step-up (Owner-level actions)
- NOT required on routine step-ups (10 min, 5 min windows)
- MFA code delivered via tenant's configured notification channel

### Token Refresh
```
POST /api/auth/token/refresh
Authorization: Bearer {current_jwt}
```

Response:
```json
{
  "access_token": "{new_jwt}",
  "expires_at": "2026-02-23T20:00:00Z",
  "last_auth_at": "2026-02-23T10:00:00Z"
}
```

If HeartBeat is unreachable during background refresh, the current
session continues until expiry, then prompts login.

---

## 7. Step-Up Authentication

### What It Is
Certain operations require the user to have authenticated within a
specific recent window, regardless of when the session token was issued.
If the window has expired, Float prompts lightweight re-verification
without logging the user out.

Re-verification produces a short-lived **step-up token** scoped to
that operation class. After re-auth, the original operation retries
automatically.

### Step-Up Tiers

| Window | Operations |
|---|---|
| **1 hour** | Routine reads, uploading files, viewing invoices, dashboard navigation. Standard Operator day-to-day. |
| **10 minutes** | Admin operations — configuration changes, viewing audit logs, managing permissions. |
| **5 minutes** | High-stakes writes — approval workflows, granting elevated access, sensitive config changes. |
| **Immediate re-auth** | Owner-level actions — creating an Admin, deactivating an Owner, approving pending elevated grants. MFA required if enabled. |

### How It Works
The JWT carries `last_auth_at`. When a service receives a request for
a sensitive operation, it checks `last_auth_at` against the required
window for that operation (fetched from HeartBeat's operation config).

If the window is not satisfied, the service returns:
```json
{
  "error_code": "STEP_UP_REQUIRED",
  "required_within_seconds": 300,
  "last_auth_at": "2026-02-23T09:00:00Z",
  "operation": "invoice.approve"
}
```

Float SDK handles `STEP_UP_REQUIRED` by presenting a re-authentication
prompt. The user re-enters credentials (and MFA if required). HeartBeat
issues a step-up token:

```
POST /api/auth/stepup
Authorization: Bearer {current_jwt}
Body: {
  "credential": "{password}",
  "mfa_code": "{code}",     ← only if MFA enabled and Immediate tier
  "operation": "invoice.approve"
}
```

Response:
```json
{
  "step_up_token": "{short_lived_jwt}",
  "valid_for_seconds": 300,
  "operation_scope": "invoice.approve",
  "last_auth_at": "2026-02-23T10:05:00Z"
}
```

The step-up token is attached to the retried request alongside the
session JWT. It expires after the defined window.

### Step-Up Configuration
Step-up windows are **per-operation configuration** stored in HeartBeat's
config.db. Services do not hardcode these values. They query:

```
GET /api/auth/operations/{operation_name}/policy
Authorization: Bearer {service_api_key}:{service_api_secret}
```

Response:
```json
{
  "operation": "invoice.approve",
  "required_permission": "invoice.approve",
  "required_within_seconds": 300,
  "mfa_required": false
}
```

Services cache operation policies for 5 minutes.

---

## 8. SSE Authentication & Stream Filtering

### Connection Authentication
Float SDK opens the SSE stream with the user's JWT:
```
GET /api/v1/events/blobs
Authorization: Bearer {user_jwt}
```

HeartBeat validates the JWT at connection time, identifies role and
permissions, and filters the event stream before emitting. The stream
is personalised server-side.

### Stream Filtering by Role

| Role | SSE Events Received |
|---|---|
| Owner | All events across the tenant |
| Admin | All events within their administrative scope |
| Operator | Only events within their explicitly granted view permissions |
| Support | Health and status events only — no business data events |

### JWT Expiry During Active Connection
On JWT expiry, HeartBeat closes the stream with close code `4401`
(Authentication Expired). Float SDK:
1. Attempts background token refresh
2. If successful — reconnects with new JWT (seamless to user)
3. If session expired — prompts user to log in, reconnects after

---

## 9. Token Introspection — Internal Service Verification

### When Services Call Introspection

| Operation Type | Verification Mode |
|---|---|
| Sensitive operations (finalisation, approvals, config changes) | Always verify live against HeartBeat |
| Routine operations | Local cache, refresh every 30-60 seconds |
| SSE connection | Verify at connection time, re-verify on reconnect |

**Note on finalisation:** Float SDK calls Core directly for invoice
finalisation. Relay is not involved. Core verifies the user JWT
against HeartBeat before processing any finalisation request.

### Introspection Endpoint

```
POST /api/auth/introspect
Authorization: Bearer {service_api_key}:{service_api_secret}
```

Request:
```json
{
  "token": "{user_jwt}",
  "required_permission": "invoice.approve",
  "required_within_seconds": 300
}
```

Response — valid, step-up satisfied:
```json
{
  "active": true,
  "actor_type": "human",
  "user_id": "usr-abc123",
  "role": "admin",
  "permissions": ["invoice.view", "invoice.approve", "user.create.operator"],
  "tenant_id": "abbey-001",
  "last_auth_at": "2026-02-23T10:00:00Z",
  "step_up_satisfied": true,
  "expires_at": "2026-02-23T18:00:00Z"
}
```

Response — step-up required:
```json
{
  "active": true,
  "actor_type": "human",
  "user_id": "usr-abc123",
  "role": "admin",
  "step_up_satisfied": false,
  "error_code": "STEP_UP_REQUIRED",
  "required_within_seconds": 300,
  "last_auth_at": "2026-02-23T09:00:00Z"
}
```

Response — invalid or expired:
```json
{
  "active": false,
  "error_code": "TOKEN_INVALID",
  "message": "Token expired or not recognised"
}
```

### Local Signature Verification
Services with HeartBeat's Ed25519 public key can verify JWT signatures
locally without an introspection call. This is appropriate for:
- Confirming a token is genuine and unexpired
- Extracting role and basic permissions from the payload

Local verification does NOT check:
- Whether the token has been explicitly revoked (server-side only)
- Whether step-up requirements are satisfied for the operation

For sensitive operations, always call introspection — do not rely on
local verification alone.

---

## 10. Approval Workflows

### Admin Creation

```
1. Existing Admin calls: POST /api/auth/users (role: admin)
2. HeartBeat creates request in pending_approvals table
   → status: pending_owner_approval
3. HeartBeat notifies Owner(s) via configured channel
4. Initiating Admin sees: GET /api/auth/approvals/{request_id}
   → { status: "pending_owner_approval", initiated_by: "...", target_role: "admin" }
5. Owner acts: POST /api/auth/approvals/{request_id}/approve
            or POST /api/auth/approvals/{request_id}/reject
6. On approve: Admin account created and activated
              HeartBeat notifies initiating Admin
7. On reject:  Request marked rejected with reason
              HeartBeat notifies initiating Admin
```

### Special Feature Assignment
Identical flow to Admin creation. Owner must approve before the
feature is active on the Admin's account.

### Pending Approval States

| Status | Meaning |
|---|---|
| `pending_owner_approval` | Awaiting Owner action |
| `approved` | Owner approved, action executed |
| `rejected` | Owner rejected, reason recorded |
| `expired` | Request not acted on within 7 days — auto-expires |

Expired requests are audit-logged. A new request must be initiated.

---

## 11. Time-Bound Elevated Access

### Granting Elevated Access
Owner or Admin calls:
```
POST /api/auth/users/{user_id}/elevated-access
Authorization: Bearer {jwt}   ← must satisfy 5-minute step-up
```

Request:
```json
{
  "duration_hours": 4,
  "scope": {
    "include_financial_data": true,
    "include_invoice_data": true,
    "include_audit_logs": true,
    "include_technical_data": true
  },
  "reason": "Forensic audit of invoice batch 2026-02-23"
}
```

Response:
```json
{
  "grant_id": "grant-abc123",
  "user_id": "usr-support-001",
  "granted_by": "usr-admin-007",
  "scope": { ... },
  "valid_from": "2026-02-23T10:00:00Z",
  "valid_until": "2026-02-23T14:00:00Z",
  "reason": "Forensic audit of invoice batch 2026-02-23"
}
```

### Notifications
| Event | Recipient |
|---|---|
| Grant issued | Recipient user |
| 30 minutes before expiry | Recipient user |
| Grant expired | Recipient user |

### Expiry Behaviour
On expiry, the elevated permissions silently leave the user's effective
permission set. The session continues without interruption. The user
receives a notification confirming access has ended.

All grants are logged in the audit trail with full context: granting
actor, recipient, scope, duration, and reason.

---

## 12. Tenancy Governance — Enrollment

### The Principle
After first installation, HeartBeat is the **sole authority for everything
that joins the Helium ecosystem on that tenancy.** Nothing activates
without HeartBeat's verification and registration.

### Who Can Generate Enrollment Tokens

| Component | Who Can Initiate |
|---|---|
| New Float App seat | Owner or Admin (within license limits) |
| New Relay instance | Owner only |
| New satellite location | Owner only |
| New support tool or integration | Admin (within license limits) |
| New database registration | Automatic on component enrollment |

### Enrollment Flow
```
1. Owner/Admin: POST /api/enrollment/generate
   → HeartBeat checks license — is this permitted?
   → Generates signed enrollment token with full component config
   → Sends token to Owner/Admin via email

2. Owner/Admin forwards token to installer

3. Installer runs on new machine, presents token:
   POST /api/enrollment/activate
   Body: { "enrollment_token": "enr_abbey_float_a1b2c3..." }

4. HeartBeat validates token:
   - Not used, not expired
   - Component type permitted by license
   - License limits not breached
   - Tenant ID matches this HeartBeat

5. On success:
   - Registers component in service registry
   - Registers its databases in database catalog
   - Returns full initial config to installer
   - Revokes token immediately

6. Audit event written:
   - Who requested the token
   - What component was enrolled
   - When it was activated
   - From which machine (IP)
```

### Enrollment Token Structure
```json
{
  "enrollment_token": "enr_abbey_float_a1b2c3d4...",
  "tenant_id": "abbey-001",
  "heartbeat_url": "http://10.0.1.5:9000",
  "component_type": "float_app",
  "instance_id": "float-abbey-workstation-7",
  "initial_config": {
    "tier": "enterprise",
    "relay_url": "http://10.0.1.6:8082",
    "data_base_path": "C:\\HeliumData\\abbey-001",
    "allowed_modules": ["relay_bulk", "relay_nas"]
  },
  "expires_at": "2026-02-25T10:00:00Z",
  "issued_by": "heartbeat-primary",
  "issued_at": "2026-02-23T10:00:00Z"
}
```

### Enrollment Error Codes

| Code | Meaning |
|---|---|
| `TOKEN_EXPIRED` | Token past its expiry — request a new one |
| `TOKEN_ALREADY_USED` | Token was already consumed by a previous activation |
| `TENANT_MISMATCH` | Token belongs to a different tenant |
| `LICENSE_LIMIT_REACHED` | Seat or instance limit already at maximum |
| `MODULE_NOT_LICENSED` | Requested module not in tenant's license |
| `COMPONENT_NOT_PERMITTED` | Tier does not allow this component type |

---

## 13. License Enforcement

### The Model
Pronalytics generates a **cryptographically signed license** at client
onboarding. HeartBeat enforces it locally — no external calls to
Pronalytics are ever required.

**Signature algorithm:** Ed25519
**Key:** Pronalytics' Ed25519 private key (held securely by Pronalytics)
**Verification:** Pronalytics' Ed25519 public key baked into the HeartBeat
binary at build time

Any modification to the license file breaks the signature. HeartBeat
rejects tampered licenses and enters restricted mode.

### License Structure
```json
{
  "license_id": "lic_abbey_ent_001",
  "tenant_id": "abbey-001",
  "tenant_name": "Abbey Mortgage Bank",
  "issued_by": "Pronalytics Limited",
  "tier": "enterprise",
  "issued_at": "2026-01-01T00:00:00Z",
  "expires_at": "2027-01-01T00:00:00Z",
  "limits": {
    "float_seats": 10,
    "relay_instances": 3,
    "satellite_locations": 5,
    "daily_invoice_limit": 10000,
    "blob_storage_gb": 500,
    "max_owners": 2,
    "max_admins": 20,
    "max_operators": 100,
    "max_support_users": 5
  },
  "modules": [
    "relay_bulk", "relay_nas", "relay_erp",
    "core_processing", "edge_analytics",
    "float_ui", "his_local"
  ],
  "features": {
    "mfa_enabled": true,
    "sso_entra": true,
    "satellite_mode": true,
    "wazuh_siem": true,
    "e2ee_encryption": true
  },
  "signature": "ed25519_signature_of_above_fields"
}
```

### Enforcement Points
HeartBeat checks the license before:
- Generating any enrollment token
- Activating any new component
- Enabling any feature (SSO, MFA, satellite mode)
- Processing any request after license expiry grace period ends

### License Lifecycle

**Expiry:**
- HeartBeat sends expiry warnings at 60, 30, 14, and 7 days to Owner(s)
- On expiry: grace period begins (default 30 days)
  - Existing components continue working
  - No new enrollments permitted
- After grace period: HeartBeat restricts new operations
- Resolution: Pronalytics delivers a new signed license file
  - File dropped into the installation
  - HeartBeat reads, verifies, and activates immediately

**Upgrade:**
- Pronalytics delivers a new signed license with updated tier/limits
- File drop only — no API call, no downtime, no restart
- HeartBeat detects the new file, verifies signature, reflects new limits

**Tamper:**
- Modified license fails signature check
- HeartBeat enters restricted mode: no new enrollments
- Owner notified immediately
- Only a valid Pronalytics-signed license resolves this

---

## 14. All-Father Key — Pronalytics Override

### What It Is
The all-Father key is Pronalytics' mechanism for extraordinary actions
that cannot be performed through the normal role system. It uses the
same Ed25519 infrastructure as the license.

**Pronalytics holds:** Ed25519 all-Father private key
**HeartBeat holds:** Corresponding public key, baked into binary at build time
**Only Pronalytics can change it:** Through a signed software update

### What the All-Father Key Can Do
- Re-appoint Owner A (when Owner A self-deactivates or is unreachable)
- Force-deactivate any user including Owner A
- Reset the entire first-run ceremony
- Override a tampered or corrupt license

### How It Works
Pronalytics engineers generate a signed command:
```json
{
  "command": "reappoint_owner_a",
  "tenant_id": "abbey-001",
  "new_owner_user_id": "usr-new-001",
  "issued_at": "2026-02-23T10:00:00Z",
  "expires_at": "2026-02-23T11:00:00Z",
  "nonce": "one-time-random-value",
  "signature": "ed25519_all_father_signature"
}
```

This signed command file is delivered to the client (out-of-band —
secure channel, on-site if necessary). The new Owner or a Pronalytics
engineer installs it via:

```
POST /api/auth/all-father/execute
Body: { "command_file": "{base64_encoded_signed_command}" }
```

HeartBeat:
1. Verifies the Ed25519 signature against the all-Father public key
2. Checks the nonce has not been used before (prevents replay)
3. Confirms the command has not expired
4. Confirms `tenant_id` matches this instance
5. Executes the command
6. Writes an immutable audit event recording the all-Father intervention

### Security Properties
- Commands are single-use (nonce prevents replay)
- Commands expire (short validity window)
- Every execution is permanently audit-logged
- The all-Father public key cannot be changed except through a Pronalytics
  signed software update verified by the same key

---

## 15. Service Lifecycle Management

### HeartBeat's Original Mandate
HeartBeat's name reflects its core purpose — keeping the Helium platform
alive. HeartBeat starts with the OS and supervises all other services.

### OS-Level Presence

**Windows:**
- HeartBeat registers as a **Windows Service** via NSSM
  (Non-Sucking Service Manager)
- Starts automatically on OS boot
- NSSM handles HeartBeat's own restart if it crashes
- Child services (Core, Relay, Edge, HIS) are managed by HeartBeat's
  internal process manager — NOT as individual Windows Services

**Linux:**
- HeartBeat registers as a **systemd unit**
- `[Install] WantedBy=multi-user.target` — starts on OS boot
- systemd handles HeartBeat's own restart
- Child services managed by HeartBeat's internal process manager
  — NOT as individual systemd units

### Internal Process Manager
HeartBeat's process manager knows how to start each service (command,
args, working directory, environment variables) and monitors them by PID.

**Service priority tiers:**

| Service | Priority | Restart Policy |
|---|---|---|
| Core | P0 | Restart immediately, max 3 attempts, then alert Owner |
| Relay | P0 | Restart immediately, max 3 attempts, then alert Owner |
| Auth (HeartBeat internal) | P1 | Restart immediately, max 3 attempts, then alert Owner |
| HIS | P2 | Restart after 30s backoff, max 3 attempts |
| Edge | P2 | Restart after 30s backoff, max 3 attempts |
| Float (if embedded) | P3 | Do not auto-restart — user-facing application |

On repeated failures beyond max attempts:
- Owner notified via configured channel
- HeartBeat continues attempting at increasing intervals (exponential backoff)
- Service marked `unhealthy` in registry

### Startup Order
HeartBeat starts child services in dependency order:
```
1. HeartBeat (self — already running)
2. Core (other services depend on it)
3. Relay (depends on Core being reachable)
4. HIS (depends on Core)
5. Edge (depends on Core + Relay)
6. Float (if embedded — user-facing, starts last)
```

### Current Implementation Status
| Capability | Status |
|---|---|
| Health reporting (services → HeartBeat) | ✅ Built |
| Health polling (HeartBeat → services) | ✅ Built |
| Health status tracking | ✅ Built |
| Failure notifications | ✅ Built |
| **Process restart** | ❌ Not yet built |
| **OS service registration (NSSM/systemd)** | ❌ Not yet built |
| **Startup order management** | ❌ Not yet built |

### Enterprise High Availability
For Enterprise tier, HeartBeat itself must not be a single point of failure.
A warm standby HeartBeat monitors the Primary and promotes itself on failure.

**Status: Stubbed — dedicated design session required.**
This is distinct from the Primary/Satellite topology (multi-location
deployments). HA redundancy and Parent/Satellite are separate concerns.

---

## 16. auth.db Schema

Dedicated database for all authentication and session data.
**Encrypted at rest with SQLCipher.**

```sql
-- Users
CREATE TABLE users (
    user_id         TEXT PRIMARY KEY,          -- usr-{uuid4}
    tenant_id       TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    password_hash   TEXT,                      -- bcrypt, null if SSO-only
    role_id         TEXT NOT NULL,             -- FK to roles
    owner_sequence  TEXT,                      -- 'A' | 'B' | null
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    must_reset_password BOOLEAN NOT NULL DEFAULT 0,
    mfa_configured  BOOLEAN NOT NULL DEFAULT 0,
    is_first_run    BOOLEAN NOT NULL DEFAULT 0,
    created_by      TEXT,                      -- user_id of creator
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_login_at   TEXT,
    deactivated_at  TEXT,
    deactivated_by  TEXT
);

-- Roles
CREATE TABLE roles (
    role_id     TEXT PRIMARY KEY,    -- owner | admin | operator | support
    role_name   TEXT NOT NULL,
    description TEXT
);

-- Permissions
CREATE TABLE permissions (
    permission_id   TEXT PRIMARY KEY,  -- invoice.approve
    description     TEXT
);

-- Role default permissions
CREATE TABLE role_permissions (
    role_id         TEXT NOT NULL,
    permission_id   TEXT NOT NULL,
    PRIMARY KEY (role_id, permission_id)
);

-- Per-user permission overrides (additions beyond role defaults)
CREATE TABLE user_permissions (
    user_id         TEXT NOT NULL,
    permission_id   TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    expires_at      TEXT,              -- null = permanent
    PRIMARY KEY (user_id, permission_id)
);

-- Active sessions
CREATE TABLE sessions (
    session_id      TEXT PRIMARY KEY,   -- tok-{uuid4}
    user_id         TEXT NOT NULL,
    jwt_jti         TEXT NOT NULL UNIQUE,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    last_refreshed  TEXT,
    last_auth_at    TEXT NOT NULL,
    is_revoked      BOOLEAN NOT NULL DEFAULT 0,
    revoked_at      TEXT,
    revoked_reason  TEXT
);

-- Step-up tokens (short-lived, operation-scoped)
CREATE TABLE stepup_tokens (
    token_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    operation_scope TEXT NOT NULL,     -- invoice.approve
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    is_used         BOOLEAN NOT NULL DEFAULT 0
);

-- Pending approvals (Admin creation, feature assignment)
CREATE TABLE pending_approvals (
    approval_id     TEXT PRIMARY KEY,
    approval_type   TEXT NOT NULL,     -- create_admin | assign_feature
    requested_by    TEXT NOT NULL,     -- user_id
    target_user_id  TEXT,
    target_role     TEXT,
    target_feature  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending_owner_approval',
    requested_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,     -- auto-expire after 7 days
    acted_by        TEXT,
    acted_at        TEXT,
    rejection_reason TEXT
);

-- Time-bound elevated access grants
CREATE TABLE elevated_access_grants (
    grant_id        TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    granted_by      TEXT NOT NULL,
    scope           TEXT NOT NULL,     -- JSON: include_financial_data, etc.
    reason          TEXT NOT NULL,
    valid_from      TEXT NOT NULL,
    valid_until     TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    revoked_at      TEXT,
    revoked_by      TEXT,
    warning_sent    BOOLEAN NOT NULL DEFAULT 0,
    expiry_notified BOOLEAN NOT NULL DEFAULT 0
);

-- Enrollment tokens
CREATE TABLE enrollment_tokens (
    token_id        TEXT PRIMARY KEY,   -- enr_{uuid4}
    tenant_id       TEXT NOT NULL,
    component_type  TEXT NOT NULL,
    instance_id     TEXT NOT NULL,
    initial_config  TEXT NOT NULL,      -- JSON
    requested_by    TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    is_used         BOOLEAN NOT NULL DEFAULT 0,
    used_at         TEXT,
    used_by_ip      TEXT
);

-- All-Father command nonces (replay prevention)
CREATE TABLE all_father_nonces (
    nonce           TEXT PRIMARY KEY,
    command_type    TEXT NOT NULL,
    executed_at     TEXT NOT NULL
);

-- Schema migrations tracking
-- NOTE: Harmonized with existing HeartBeat migrator pattern (blob.db, registry.db).
-- Uses INTEGER version (not TEXT), filename+description (not name), execution_time_ms.
CREATE TABLE schema_migrations (
    version             INTEGER PRIMARY KEY,
    filename            TEXT NOT NULL,
    description         TEXT NOT NULL,
    checksum            TEXT NOT NULL,
    applied_at          TEXT NOT NULL,
    execution_time_ms   INTEGER NOT NULL DEFAULT 0
);
```

---

## 17. Auth API Endpoint Reference

### Authentication Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| POST | `/api/auth/login` | Login with credentials | None |
| POST | `/api/auth/login/sso/entra` | Initiate Entra SSO flow | None |
| GET | `/api/auth/login/sso/entra/callback` | Entra SSO callback | None |
| POST | `/api/auth/token/refresh` | Refresh session token | Session JWT |
| POST | `/api/auth/logout` | Invalidate session | Session JWT |
| POST | `/api/auth/stepup` | Re-authenticate for step-up | Session JWT |
| POST | `/api/auth/introspect` | Verify JWT (service-to-service) | Service API key |
| GET | `/api/auth/operations/{op}/policy` | Get step-up policy for operation | Service API key |

### User Management Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| GET | `/api/auth/users` | List users | Admin+ |
| POST | `/api/auth/users` | Create user | Admin+ |
| GET | `/api/auth/users/{user_id}` | Get user | Admin+ |
| PUT | `/api/auth/users/{user_id}` | Update user | Admin+ |
| POST | `/api/auth/users/{user_id}/deactivate` | Deactivate user | Owner/Admin |
| GET | `/api/auth/users/me` | Current user profile | Session JWT |

### Role & Permission Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| GET | `/api/auth/roles` | List roles and permissions | Admin+ |
| POST | `/api/auth/users/{user_id}/permissions` | Grant permission to user | Admin+ |
| DELETE | `/api/auth/users/{user_id}/permissions/{perm}` | Revoke permission from user | Admin+ |
| POST | `/api/auth/users/{user_id}/elevated-access` | Grant time-bound elevated access | Admin+ (5-min step-up) |
| DELETE | `/api/auth/users/{user_id}/elevated-access/{grant_id}` | Revoke elevated access | Admin+ |

### Approval Workflow Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| GET | `/api/auth/approvals` | List pending approvals | Owner |
| GET | `/api/auth/approvals/{approval_id}` | Get specific approval | Owner/Initiating Admin |
| POST | `/api/auth/approvals/{approval_id}/approve` | Approve pending request | Owner (Immediate re-auth) |
| POST | `/api/auth/approvals/{approval_id}/reject` | Reject pending request | Owner |

### Enrollment Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| POST | `/api/enrollment/generate` | Generate enrollment token | Owner/Admin |
| POST | `/api/enrollment/activate` | Activate new component | Enrollment token |
| GET | `/api/enrollment/tokens` | List issued enrollment tokens | Owner |
| DELETE | `/api/enrollment/tokens/{token_id}` | Revoke unused token | Owner |

### License Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| GET | `/api/auth/license` | Get current license info | Admin+ |
| GET | `/api/auth/license/usage` | Get usage vs limits | Admin+ |
| POST | `/api/auth/license/reload` | Reload license from license.db | Owner (Immediate re-auth) |

### Integration Config Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| GET | `/api/auth/integrations` | List system integrations | Admin+ |
| POST | `/api/auth/integrations` | Register new integration | Owner |
| PUT | `/api/auth/integrations/{id}` | Update integration config | Owner/Admin |
| PUT | `/api/auth/integrations/{id}/enforced` | Set user_id_enforced flag | Owner/Admin (5-min step-up) |

### All-Father Endpoint

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| POST | `/api/auth/all-father/execute` | Execute signed Pronalytics command | Signed command file only |

---

## 18. Error Codes

| Code | HTTP | Description |
|---|---|---|
| `TOKEN_INVALID` | 401 | JWT expired, malformed, or not recognised |
| `TOKEN_REVOKED` | 401 | JWT explicitly revoked |
| `STEP_UP_REQUIRED` | 403 | Operation requires more recent authentication |
| `PERMISSION_DENIED` | 403 | User lacks required permission |
| `USER_ID_REQUIRED` | 403 | Integration requires user ID — not supplied |
| `OWNER_LIMIT_REACHED` | 409 | Cannot create third Owner without deactivating one |
| `LICENSE_LIMIT_REACHED` | 409 | Seat or instance limit at maximum |
| `MODULE_NOT_LICENSED` | 403 | Module not in tenant license |
| `LICENSE_EXPIRED` | 402 | License has expired |
| `LICENSE_TAMPERED` | 403 | License signature verification failed |
| `TOKEN_EXPIRED` | 410 | Enrollment token past expiry |
| `TOKEN_ALREADY_USED` | 409 | Enrollment token already consumed |
| `TENANT_MISMATCH` | 403 | Token belongs to different tenant |
| `APPROVAL_PENDING` | 202 | Request accepted, awaiting Owner approval |
| `ALL_FATHER_INVALID` | 401 | All-Father command signature failed |
| `ALL_FATHER_REPLAYED` | 409 | All-Father nonce already used |
| `FIRST_RUN_REQUIRED` | 403 | First-run setup not complete |

---

## 19. Environment Variables

| Env Var | Default | Description |
|---|---|---|
| `HEARTBEAT_AUTH_DB_PATH` | auto-detect | Path to auth.db (SQLCipher encrypted) |
| `HEARTBEAT_AUTH_DB_KEY` | (required) | SQLCipher key for auth.db |
| `HEARTBEAT_JWT_PRIVATE_KEY_PATH` | auto-detect | Path to HeartBeat's Ed25519 JWT signing key |
| `HEARTBEAT_JWT_PUBLIC_KEY_PATH` | auto-detect | Path to HeartBeat's Ed25519 JWT verification key |
| `HEARTBEAT_PRONALYTICS_PUBLIC_KEY` | (baked in) | Pronalytics' Ed25519 public key for license + all-Father verification |
| `HEARTBEAT_MFA_ENABLED` | from license | Override MFA setting (dev/test only) |
| `HEARTBEAT_SESSION_HOURS` | `8` | Session token validity in hours |
| `HEARTBEAT_REFRESH_INTERVAL_MINUTES` | `60` | Background token refresh interval |
| `HEARTBEAT_STEPUP_CACHE_MINUTES` | `5` | Operation policy cache TTL for services |
| `HEARTBEAT_APPROVAL_EXPIRY_DAYS` | `7` | Pending approval auto-expiry in days |
| `HEARTBEAT_ELEVATED_ACCESS_WARNING_MINUTES` | `30` | Elevated access expiry warning lead time |
| `HEARTBEAT_LICENSE_GRACE_DAYS` | `30` | Grace period after license expiry |
| `HEARTBEAT_ENROLLMENT_TOKEN_EXPIRY_HOURS` | `48` | Enrollment token validity window |
| `HEARTBEAT_ENTRA_TENANT_ID` | (optional) | Azure AD tenant ID for Entra SSO |
| `HEARTBEAT_ENTRA_CLIENT_ID` | (optional) | Azure AD client ID for Entra SSO |
| `HEARTBEAT_ENTRA_CLIENT_SECRET` | (optional) | Azure AD client secret for Entra SSO |

---

## 20. Implementation Status

| Component | Status |
|---|---|
| auth.db schema | ❌ Not built |
| JWT issuance (Authlib + Ed25519) | ❌ Not built |
| Login endpoint (local credentials) | ❌ Not built |
| Entra SSO adapter | ❌ Not built |
| Token refresh | ❌ Not built |
| Token introspection | ❌ Not built |
| Step-up authentication | ❌ Not built |
| SSE auth filter | ❌ Not built (SSE transport exists) |
| User management CRUD | ❌ Not built |
| Role and permission assignment | ❌ Not built |
| Approval workflow engine | ❌ Not built |
| Time-bound elevated access | ❌ Not built |
| Enrollment token generation | ❌ Not built |
| Enrollment activation | ❌ Not built |
| License signature verification | ❌ Not built |
| License enforcement | ❌ Not built |
| License expiry monitoring | ❌ Not built |
| Integration config (user_id_enforced) | ❌ Not built |
| All-Father command execution | ❌ Not built |
| First-run bootstrap handler | ❌ Not built |
| Notification dispatcher | ❌ Not built |
| Process manager (service restart) | ❌ Not built |
| OS service registration (NSSM/systemd) | ❌ Not built |

---

## Superseded Documentation

| Document | Section | Status |
|---|---|---|
| HeartBeat Service Contract Part 1 §3.8 | User Auth Stub | **Superseded by this document** |
| All X-User-ID references across all contracts | Various | **Deprecated — being phased out** |

**Rule:** When this document conflicts with any other, this document wins.

---

*End of Part 4.*

*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-02-23*
*Version: 1.0*
