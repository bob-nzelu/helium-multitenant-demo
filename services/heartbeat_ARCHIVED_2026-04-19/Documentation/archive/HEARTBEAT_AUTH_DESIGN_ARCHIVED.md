# HeartBeat Authentication & Tenancy Governance Design

**Status:** IN PROGRESS — Design Phase
**Version:** 0.3
**Date:** 2026-02-23
**Author:** Helium Core Team (Pronalytics Limited)
**Purpose:** Captures all auth and tenancy governance design decisions before
formal contract is written. This document is the source of truth during design.

> **Company Note:** This platform is built and owned by **Pronalytics Limited**.
> All references to WestMetro in existing documentation and code comments
> must be updated to Pronalytics during the next documentation pass.

Once finalised, this document feeds into:
- HEARTBEAT_SERVICE_CONTRACT_PART1.md (§3.8 full rewrite)
- HEARTBEAT_SERVICE_CONTRACT_PART3.md (§1 audit schema update)
- HEARTBEAT_API_CONTRACTS.md (new auth + enrollment endpoint section)
- Relay Service Contract (JWT + system actor + enforced user ID section)
- Core Service Contract (JWT verification + step-up auth)
- Float/SDK Integration Contract Part 2 (JWT acquisition + downstream attachment)

---

## Table of Contents

1. [Core Decision — JWT Replaces X-User-ID](#1-core-decision--jwt-replaces-x-user-id)
2. [External System Calls — User ID Enforcement](#2-external-system-calls--user-id-enforcement)
3. [Role Hierarchy](#3-role-hierarchy)
4. [Provisioning Flow — First Owner Bootstrap](#4-provisioning-flow--first-owner-bootstrap)
5. [Tenancy Governance — HeartBeat as Authority](#5-tenancy-governance--heartbeat-as-authority)
6. [License-Embedded Governance Model](#6-license-embedded-governance-model)
7. [Enrollment — New Component Activation](#7-enrollment--new-component-activation)
8. [Authentication Policies](#8-authentication-policies)
9. [SSE Authentication](#9-sse-authentication)
10. [Internal Verification — Services Calling HeartBeat](#10-internal-verification--services-calling-heartbeat)
11. [Approval Workflow Model](#11-approval-workflow-model)
12. [Notification & Messaging Layer](#12-notification--messaging-layer)
13. [HeartBeat Core Mandate — Service Lifecycle Management](#13-heartbeat-core-mandate--service-lifecycle-management)
14. [New HeartBeat Components Required](#14-new-heartbeat-components-required)
15. [Resolved Design Decisions](#15-resolved-design-decisions)
16. [Contract Files That Must Be Updated](#16-contract-files-that-must-be-updated)
17. [Open Questions](#17-open-questions)

---

## 1. Core Decision — JWT Replaces X-User-ID

### The Problem With X-User-ID
The existing Helium service contracts (Part 1 §3.5, Part 3 §1) use an `X-User-ID`
header for identity propagation across services. This is unverified — any caller
can claim any identity. Relay passes it through without validation. HeartBeat stores
it in audit events without confirming it is genuine. This breaks the audit mantra
of knowing exactly who did what.

### The Decision
**Every human-initiated request across Helium carries a signed JWT issued by
HeartBeat.** Services do not trust X-User-ID. They verify the JWT.

When Float makes a call to Relay or Core on behalf of a logged-in user, it attaches
the user's HeartBeat JWT in the Authorization header. The receiving service calls
HeartBeat's token introspection endpoint to verify the token and extract confirmed
identity before proceeding.

`X-User-ID` is **deprecated** and will be removed from all service contracts
once the JWT model is fully implemented.

### What Replaces X-User-ID
```
Authorization: Bearer {user_jwt}
```

On sensitive operations, services call:
```
POST /api/auth/introspect
Authorization: Bearer {service_api_key}:{service_api_secret}
Body: { "token": "{user_jwt}" }
```

HeartBeat returns confirmed identity including role, permissions, and
`last_auth_at`. The service checks `last_auth_at` against the required
window for that operation.

### Actor Types in the Audit Trail
Every audit event carries a standardised identity regardless of who initiated it:

```json
{
  "actor_type": "human | system",
  "actor_id": "{user_id} | {api_key_prefix}",
  "actor_name": "john.adeyemi@abbey.com | SAP_GTBank_Production",
  "user_id": "{user_id} | null"
}
```

The audit trail is never blank. Every action is attributed — either to a
verified human or to a named system actor. No anonymous operations.

---

## 2. External System Calls — User ID Enforcement

### The Requirement
When an external system (ERP, automated pipeline, third-party integration)
calls into the Helium ecosystem, it should **attempt to supply a user ID**
— the human identity on whose behalf the system is acting, if one exists.

This is not always possible. A fully automated SAP batch job may have no
human actor behind it. A finance officer may have triggered an export in
their ERP that calls Relay without their identity being propagated.

### The Enforced Flag
HeartBeat maintains a per-integration configuration with an `enforced` flag:

```json
{
  "integration_id": "sap_gtbank_production",
  "integration_name": "SAP GTBank Production",
  "actor_type": "system",
  "user_id_enforced": true | false
}
```

**When `user_id_enforced: false` (default for most system integrations):**
- The external system may supply a user ID — if present, it is recorded
  in the audit trail alongside the system actor identity
- If absent, the call proceeds and is logged as a pure system actor event
- No request is rejected for missing user ID

**When `user_id_enforced: true`:**
- The Helium ecosystem **will not honour the request** if no user ID is
  found in the call
- Relay (or whichever service receives the call) checks HeartBeat's
  integration config for the calling system's API key
- If `user_id_enforced: true` and no user ID header is present,
  the request is rejected with:
  ```json
  {
    "error_code": "USER_ID_REQUIRED",
    "message": "This integration requires a user identity on every request",
    "integration_id": "sap_gtbank_production"
  }
  ```
- Audit event is still written — recording the rejection and the
  system actor that attempted the call

### Why This Matters
Some clients — particularly regulated financial institutions like Abbey
Mortgage Bank — may require full human accountability even on ERP-initiated
flows. The `enforced` flag is a **compliance tool** that HeartBeat exposes
per integration, configurable by Owner or Admin. CBN auditors can then
ask "who in your organisation triggered this invoice batch" and receive a
verified answer rather than "the system did it."

### How User ID Is Passed by External Systems
External systems that support user ID propagation include it as a header:
```
X-User-ID: john.adeyemi@abbey.com
```

This is NOT a JWT — it is an assertion by the external system. HeartBeat
records it as `asserted_user_id` in the audit trail, distinct from
`verified_user_id` which only comes from a validated JWT. The distinction
is visible in audit queries — auditors can see whether identity was
verified or merely asserted.

### Integration Config Management
- Owner and Admin can configure integration enforcement settings
- All changes to enforcement flags are audit-logged
- HeartBeat's integration registry stores these configs in config.db

---

## 3. Role Hierarchy

HeartBeat owns four roles. All four exist within the client environment.
There is no cross-tenant role — Pronalytics access is handled at the
provisioning layer, entirely outside this role system.

### Owner
- Maximum **2 per tenant** — hard system constraint, not a guideline
- Sees and accesses everything by default — no restrictions
- Assigns the second Owner (first Owner is provisioned by Pronalytics at tenancy setup)
- Must approve creation of any new Admin, even when an existing Admin initiates it
- Must approve any special administrative feature assignment to an Admin
- Can grant time-bound elevated access to any user
- To add a third Owner, an existing Owner must first be deactivated
- Deactivating an Owner requires the other active Owner to perform the action
- Intended for: CEO, CFO, IT Director — senior accountability roles

### Admin
- Full **view** rights by default across the platform
- Write rights are **not automatic** — specific write permissions assigned by Owner
- Can create Operators and Support users
- Cannot create another Admin without Owner approval
  - Creation request sits in **pending** state
  - Visible to the initiating Admin as "Pending Owner Approval"
  - Owner receives notification and must act before account is created
- Special administrative feature assignments also require Owner approval (same pending flow)
- Can assign specific view and write rights to Operators
- Can grant time-bound elevated access to any user
- Different Admins can have different administrative features — not all Admins are equal
- Intended for: Finance managers, compliance officers, senior IT staff

### Operator
- **No default view rights** — every view and write right must be explicitly
  granted by an Admin
- Cannot create any users
- Cannot approve any workflows
- Works within a specific, explicitly scoped lane defined by their Admin
- Intended for: Accountants, front desk staff, finance operators doing
  day-to-day work

### Support
- Very limited view rights by default:
  - Health endpoints, service status
  - IP addresses and ports
  - Integration diagnostics
  - No invoice data, no business data, no financial records
- Elevated access only via time-bound grant from Admin or Owner
- Intended for: Pronalytics support staff diagnosing client deployments

---

## 4. Provisioning Flow — First Owner Bootstrap

### The Core Constraint
HeartBeat does not exist at the point the first Owner needs to be created.
The installer creates HeartBeat. The first Owner must therefore be seeded
by the installer, not through HeartBeat's user management API.

### The Flow

**At packaging time (before deployment):**
1. Pronalytics generates the tenant record with a unique `tenant_id`
2. A cryptographically secure temporary password is generated —
   single use, 14-day expiry
3. Temporary password is dispatched to the client contact via **email**
   before the installer ships
4. The EXE is bound to `tenant_id` at build time — one client's installer
   cannot claim another's deployment

**During installation:**
1. Installer runs on client machine
2. Owner enters temporary password to claim and bind the deployment
3. Installer creates HeartBeat, bootstraps all databases including
   license.db and auth.db
4. Installer seeds the Owner account into auth.db:
   - Password stored as bcrypt hash
   - `must_reset_password: true`
   - `mfa_configured: false`
   - `is_first_run: true`
5. Signed license file is written into license.db (see Section 6)
6. Temporary password cleared from installer after seeding — not
   stored anywhere accessible after HeartBeat is running

**On first application start:**
1. HeartBeat detects `is_first_run: true` on the Owner record
2. Owner is issued a **restricted bootstrap token** — only grants access
   to setup screens, nothing else
3. Owner is required to set a new password
4. If MFA is enabled for the tenant (license feature flag), HeartBeat
   triggers MFA setup via configured channel
5. On completion:
   - `must_reset_password: false`
   - `mfa_configured: true`
   - `is_first_run: false`
6. Full auth policies activate — bootstrap token is invalidated
7. Owner receives full session token and is taken to the dashboard

**If temporary password expires (14 days):**
- Installation is blocked — the seeded credential is invalidated
- Pronalytics team runs a re-provisioning operation: generates a fresh
  temporary password and dispatches it via email
- This is a Pronalytics-side admin operation, outside HeartBeat's scope

---

## 5. Tenancy Governance — HeartBeat as Authority

### The Principle
After first installation, HeartBeat becomes the **sole gatekeeper for
everything that joins the Helium ecosystem on that tenancy.**

Nothing new activates without HeartBeat's blessing:
- A new Float App on a new workstation
- A new Relay instance
- A new service (Core, HIS, Edge)
- A new database
- A new queue
- A new satellite location

HeartBeat is the authority that says "yes, this belongs here and here is
what it is allowed to do."

### The Enrollment Token Mechanism
For every new component joining the tenancy, an **enrollment token** is
generated by HeartBeat. This token:

- Identifies which HeartBeat instance to contact (the tenancy's HeartBeat URL)
- Carries the `tenant_id`
- Specifies the installation type being activated (Float, Relay, Core, etc.)
- Contains initial configuration for that component
- Is **single-use** — revoked immediately after successful enrollment
- Has a defined expiry (default: 48 hours)
- Is **scoped** — cannot grant capabilities beyond what the license permits
- Is **delivered via email** to the requesting Owner or Admin

The installer for any new component carries this enrollment token. On first
run it contacts HeartBeat, presents the token, HeartBeat verifies and
activates the component, registers it in the service registry and database
catalog, and revokes the token. From that point the component is a fully
recognised tenancy member.

### What the Enrollment Config Script Contains
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

### HeartBeat's Enrollment API
```
POST /api/enrollment/activate
Body: { "enrollment_token": "enr_abbey_float_a1b2c3d4..." }
```

HeartBeat validates:
1. Token exists and has not been used
2. Token has not expired
3. Component type is permitted by the license
4. Activation would not breach any license limit (e.g. Float seat cap)
5. Tenant ID matches this HeartBeat instance

On success: registers component, returns full config, revokes token.
On failure: returns specific error, token is NOT consumed (can retry
before expiry unless it was a license limit failure).

---

## 6. License-Embedded Governance Model

### The Problem
How does HeartBeat know what it is allowed to generate without calling
home to Pronalytics? The answer must work fully offline — no external
API calls, no internet dependency.

### The Solution — Cryptographically Signed License
Pronalytics generates a **signed license document** at client onboarding.
The license is:
- Signed with Pronalytics' **Ed25519 private key** at issuance
- Written into license.db during installation (existing database in architecture)
- Verified by HeartBeat using Pronalytics' Ed25519 public key baked into
  the HeartBeat binary at build time
- Tamper-evident — any modification breaks the signature

HeartBeat never needs to call Pronalytics to enforce limits. The license
is the authority. HeartBeat reads it locally and enforces it locally.

**Signature algorithm: Ed25519** — faster than RSA-SHA256, smaller signature,
equally secure, increasingly standard in enterprise tooling.

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
    "relay_bulk",
    "relay_nas",
    "relay_erp",
    "core_processing",
    "edge_analytics",
    "float_ui",
    "his_local"
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

### How HeartBeat Enforces the License
Before generating any enrollment token, HeartBeat checks:

| Check | Example |
|---|---|
| Tier permits this component type | Standard tier cannot enroll a satellite |
| Seat/instance limit not breached | Cannot generate 11th Float enrollment if limit is 10 |
| Module is enabled | Cannot enroll relay_erp if module not in license |
| Feature is enabled | Cannot activate SSO if sso_entra is false |
| License is not expired | Expired license blocks new enrollments |
| Signature is valid | Tampered license is rejected entirely |

### License Lifecycle

**Expiry:**
- HeartBeat detects expiry on startup and periodically during operation
- Existing installed components continue working during grace period
  (configurable, default 30 days)
- No new enrollment tokens can be generated during grace period
- After grace period HeartBeat restricts new operations and notifies Owner
- Pronalytics delivers a new signed license file — dropped into the
  installation, HeartBeat reads and activates it immediately

**Upgrade (tier change):**
- Pronalytics generates a new signed license with updated limits
- Delivered as a file to the installation (no live API call needed)
- HeartBeat reads the new license, verifies signature, immediately
  reflects new limits — no downtime, no restart required

**Tamper attempt:**
- Modified license file fails signature check
- HeartBeat enters restricted mode — no new enrollments, Owner notified
- Only a valid Pronalytics-signed license resolves this

### license.db Integration
The existing license.db in the HeartBeat architecture provides the storage
layer. What needs to be added:

1. **Signature verification on startup** — HeartBeat reads license.db and
   verifies the Ed25519 signature before serving any requests
2. **License enforcement in enrollment API** — every enrollment token
   generation checks limits against the active license
3. **License expiry monitoring** — HeartBeat tracks days until expiry
   and notifies Owner at: 60, 30, 14, and 7 days before expiry
4. **License audit trail** — every license event (loaded, verified,
   expiry warning, renewal) is written to the audit log in auth.db

---

## 7. Enrollment — New Component Activation

### Who Can Generate Enrollment Tokens

| Action | Who Can Initiate |
|---|---|
| First installation (Owner + HeartBeat bootstrap) | Pronalytics (at packaging) |
| New Float App seat | Owner or Admin (if tier permits) |
| New Relay instance | Owner only |
| New satellite location | Owner only |
| New support tool | Admin (within license limits) |

All enrollment token generation is logged in the audit trail.

### Enrollment Flow
```
1. Owner/Admin requests new enrollment token in dashboard
2. HeartBeat checks license — is this permitted?
3. If yes: generates signed enrollment token with component config
4. Token delivered via email to the requesting Owner/Admin
5. Owner/Admin forwards token/config to installer
6. New component installer runs, presents token to HeartBeat
7. HeartBeat validates token, registers component, returns full config
8. Token revoked immediately
9. Component is live — registered in service registry + database catalog
10. Audit event written: who requested, what component, when activated
```

### Failed or Expired Tokens
- Expired: installer returns clear error, new token must be requested
- Already used: rejected with `TOKEN_ALREADY_USED`
- Wrong tenant: rejected with `TENANT_MISMATCH`
- License limit breached at activation (e.g. seat filled between
  token generation and activation): rejected with `LICENSE_LIMIT_REACHED`,
  Owner notified via email

---

## 8. Authentication Policies

Auth policies activate only after first-run setup is complete.

### Session Token
- Issued at login by HeartBeat (Authlib-based, signed with HeartBeat's key)
- Stored in auth.db — dedicated database for all auth data
- Valid for **8 hours** (one working day)
- Silently refreshed every **1 hour** in the background
- Carries:
  ```json
  {
    "user_id": "usr-abc123",
    "role": "admin",
    "permissions": ["invoice.view", "invoice.approve"],
    "tenant_id": "abbey-001",
    "last_auth_at": "2026-02-23T10:00:00Z",
    "issued_at": "2026-02-23T10:00:00Z",
    "expires_at": "2026-02-23T18:00:00Z"
  }
  ```

### Step-Up Authentication
Certain operations require the user to have authenticated within a specific
recent window, regardless of when the session token was issued. If the window
has expired, Float prompts lightweight re-verification (not a full
logout/login). Re-verification produces a short-lived step-up token
scoped to that operation class.

**Step-up tiers:**

| Window | Operations |
|---|---|
| **1 hour** | Routine reads, uploading files, viewing invoices, dashboard navigation |
| **10 minutes** | Admin operations — configuration changes, viewing audit logs, managing permissions |
| **5 minutes** | High-stakes writes — approval workflows, granting elevated access, sensitive config changes |
| **Immediate re-auth** | Owner-level actions — creating an Admin, deactivating an Owner, approving pending elevated grants |

The JWT carries `last_auth_at`. Services check this against the required
window for the operation. Insufficient recency returns:
```json
{
  "error_code": "STEP_UP_REQUIRED",
  "required_within_seconds": 300,
  "last_auth_at": "2026-02-23T09:00:00Z"
}
```

Float SDK recognises `STEP_UP_REQUIRED` and prompts re-authentication
without logging the user out. After re-auth, the original operation
retries automatically.

Step-up windows are **per-operation configuration** stored in HeartBeat —
not hardcoded in individual services.

### MFA Policy
- Configured at tenant level (controlled by license feature flag `mfa_enabled`)
- Required at initial login
- Required on every Immediate re-auth step-up (Owner-level actions)
- NOT required on routine step-ups (10 min, 5 min) — credential re-entry
  is sufficient
- MFA channel follows the tenant's configured notification channel

### Token Refresh
- Background refresh fires at the 1-hour mark — invisible to user
- If refresh fails (HeartBeat unreachable), session continues until
  expiry then prompts login
- Endpoint: `POST /api/auth/token/refresh`
- Returns: new access token + updated `last_auth_at`

### Identity Providers
- **Local credentials** (Authlib) — all tiers, Float standalone, default
- **Microsoft Entra / Azure AD SSO** — enterprise tier, requires license
  feature flag `sso_entra: true`
- Provider adapters normalise all identity sources to the same JWT shape
- HeartBeat is always the JWT issuer regardless of upstream identity provider

---

## 9. SSE Authentication

The SSE event stream is authenticated at connection time.

Float SDK opens the stream with the user's JWT:
```
GET /api/v1/events/blobs
Authorization: Bearer {user_jwt}
```

HeartBeat validates the JWT, identifies role and permissions, and filters
the event stream before emitting. The stream is personalised server-side —
HeartBeat does not broadcast everything and let the client filter.

| Role | SSE Events Received |
|---|---|
| Owner | All events across the tenant |
| Admin | All events within their administrative scope |
| Operator | Only events within their explicitly granted view permissions |
| Support | Health and status events only — no business data |

On JWT expiry during an active SSE connection, HeartBeat closes the stream
with a specific close code. Float SDK re-authenticates and reconnects.
If the session token is still valid (background refresh handled it),
reconnect is seamless. If the session has expired, the user is prompted
to log in.

---

## 10. Internal Verification — Services Calling HeartBeat

### When Services Verify

| Operation Type | Verification Mode |
|---|---|
| Sensitive operations (finalisation, approvals) | Always verify live against HeartBeat |
| Routine operations | Verify against local cache, refresh every 30-60 seconds |
| SSE connection | Verify at connection time, re-verify on reconnect |

**Note:** Finalisation is a Float SDK → Core direct call. Relay is not
involved in finalisation. Core verifies the user JWT against HeartBeat
before processing any finalisation request.

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

Response (valid, step-up satisfied):
```json
{
  "active": true,
  "actor_type": "human",
  "user_id": "usr-abc123",
  "role": "admin",
  "permissions": ["invoice.view", "invoice.approve", "user.create"],
  "tenant_id": "abbey-001",
  "last_auth_at": "2026-02-23T10:00:00Z",
  "step_up_satisfied": true,
  "expires_at": "2026-02-23T18:00:00Z"
}
```

Response (step-up required):
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

Response (invalid/expired):
```json
{
  "active": false,
  "error_code": "TOKEN_INVALID",
  "message": "Token expired or not recognised"
}
```

### System Actor Verification
System actors (ERPs, automated pipelines) carry no JWT. They use the API
key + secret model. Services receiving system actor calls verify via the
existing Bearer `key:secret` scheme. HeartBeat validates the API key at
the service call itself. Audit trail records `actor_type: "system"`.

For integrations with `user_id_enforced: true`, services check the
integration config before processing — see Section 2.

---

## 11. Approval Workflow Model

### Admin Creation Flow
1. Existing Admin initiates "Create Admin"
2. Request enters `pending_approval` state immediately
3. Initiating Admin sees status: "Pending Owner Approval"
4. Owner receives notification via configured channel
5. Owner approves or rejects from dashboard
6. On approval — Admin account created and activated, Admin notified
7. On rejection — request marked rejected, initiating Admin notified

### Special Feature Assignment Flow
Same pattern as Admin creation. Feature assignment sits pending until an
Owner approves. Both the initiating Admin and the approving Owner see
the specific feature being requested.

### Time-Bound Elevated Access
- Granted by Owner or Admin
- Recipient notified when grant is issued — told what access they have
  and until when
- Warning notification sent as expiry approaches (default: 30 minutes before)
- On expiry, access silently drops — session continues, elevated permissions
  leave without disruption
- Recipient notified confirming access has ended
- All grants logged in audit trail: granting actor, recipient, scope, duration

---

## 12. Notification & Messaging Layer

HeartBeat requires at least one configured notification channel per tenant.
This is not optional — MFA, password reset, approval workflows, and
elevated access expiry all depend on it. At least one channel must be
confirmed working before first-run setup can complete.

### Supported Channels
| Channel | Integration |
|---|---|
| **Email** | SMTP — configurable host, port, TLS, credentials |
| **SMS** | SMS gateway — configurable provider and API key |
| **WhatsApp** | WhatsApp Business API — preferred for Nigerian enterprise |

Channel is configured per tenant in HeartBeat's config.db.

### When HeartBeat Sends Notifications

| Event | Recipient |
|---|---|
| Temporary password dispatch | Owner contact (at packaging) |
| MFA verification code | Authenticating user |
| Password reset link | Requesting user |
| Pending Admin creation | Owner(s) |
| Pending feature assignment | Owner(s) |
| Elevated access granted | Recipient user |
| Elevated access expiry warning (30 min) | Recipient user |
| Elevated access expired | Recipient user |
| License expiry warning (60/30/14/7 days) | Owner(s) |
| License expired | Owner(s) |
| New enrollment token generated | Owner/Admin who requested it |
| New component enrolled | Owner(s) |
| Owner deactivated | Remaining Owner |
| Enrollment token expired unused | Owner who requested it |

---

## 13. HeartBeat Core Mandate — Service Lifecycle Management

### The Original Mandate
HeartBeat's name and original purpose was to **keep services alive** —
monitoring all Helium services and restarting them when they go down.
This is distinct from health *reporting* (services report to HeartBeat)
and health *polling* (HeartBeat checks services that stop reporting).

### Current Implementation Gap
The existing codebase implements:
- ✅ Health reporting — services POST to `/api/registry/health/{instance_id}`
- ✅ Health polling — HeartBeat actively checks services that stop reporting
- ✅ Health status tracking — marks services degraded/unhealthy after failures
- ✅ Notifications — generates alerts when services are unhealthy
- ❌ **Process restart** — HeartBeat does NOT actually restart failed services

This is a genuine gap in HeartBeat's original mandate that must be addressed.

### HeartBeat OS-Level Presence
HeartBeat itself must be the one process that is always alive:
- **HeartBeat starts with the OS** — registered as a system service
  (Windows Service on Windows, systemd unit on Linux)
- HeartBeat is the supervisor — it starts all other Helium services
  and keeps them running
- Other services do NOT start independently — they start because
  HeartBeat starts them

### Service Restart Mechanism (To Be Built)
HeartBeat needs a process management layer:

```
HeartBeat Process Manager:
  ├── Knows how to start each service (command, args, working dir, env)
  ├── Monitors each service process (PID tracking)
  ├── On crash: waits backoff period, restarts
  ├── On repeated failures: escalating alerts to Owner, reduces restart attempts
  └── On deliberate shutdown: orderly stop sequence (dependencies first)
```

**Restart policy per service:**
| Service | Priority | Restart Policy |
|---|---|---|
| HeartBeat | P0 — OS managed | Managed by OS service manager, not self |
| Core | P0 | Restart immediately, max 3 attempts, then alert |
| Relay | P0 | Restart immediately, max 3 attempts, then alert |
| Auth service | P1 | Restart immediately, max 3 attempts, then alert |
| HIS | P2 | Restart after 30s backoff, max 3 attempts |
| Edge | P2 | Restart after 30s backoff, max 3 attempts |
| Float (if embedded) | P3 | Do not auto-restart — user-facing |

### Enterprise Redundancy
For Enterprise tier clients, HeartBeat itself must not be a single point
of failure:
- **Primary HeartBeat** — the authoritative instance
- **Standby HeartBeat** — warm standby, monitors Primary
- On Primary failure: Standby promotes itself, takes over service management
- Promotion is automatic — no manual intervention required
- Both Primary and Standby share the same databases (or Standby has
  a replicated copy)

**This is a significant architectural item.** Primary/Satellite topology
(already designed in the service contract) partially addresses this but
is not the same as HA redundancy for HeartBeat itself. These are two
separate concerns and must be designed separately.

### Note on Parent/Satellite
The Parent/Satellite relationship (multi-location enterprise deployments)
has been scaffolded in the codebase but not fully fleshed out.
**Deferred — to be designed in a dedicated session.**

---

## 14. New HeartBeat Components Required

Summary of what needs to be built or completed:

| Component | Description | Database | Status |
|---|---|---|---|
| auth.db | Dedicated auth database — users, roles, permissions, sessions, step-up grants, pending approvals, elevated access grants, enrollment tokens | auth.db (new) | Not built |
| License verifier | Reads license.db, verifies Ed25519 signature on startup | license.db (exists) | Not built |
| License enforcer | Checks limits before generating enrollment tokens | license.db | Not built |
| JWT issuance | Authlib-based, signs tokens with HeartBeat's Ed25519 key | auth.db | Not built |
| Token introspection endpoint | Fast JWT verification for downstream services | auth.db | Not built |
| Step-up auth handler | Issues scoped short-lived tokens for sensitive operations | auth.db | Not built |
| Enrollment API | Generates, validates, and revokes enrollment tokens | auth.db + registry.db | Not built |
| SSE auth filter | Filters event stream per user role and permissions | auth.db | Partial (SSE exists, filter not built) |
| Approval workflow engine | Pending state management for Admin creation and feature assignments | auth.db | Not built |
| Notification dispatcher | Routes to SMTP, SMS, or WhatsApp per tenant config | config.db | Not built |
| Provider adapters | Normalises Entra/local credentials to standard identity object | auth.db | Not built |
| First-run bootstrap handler | Detects and manages initial Owner setup ceremony | auth.db | Not built |
| Integration config store | Per-integration user_id_enforced flag and system actor config | config.db | Not built |
| Process manager | Starts, monitors, and restarts Helium services | registry.db | **Not built — original HeartBeat mandate gap** |
| OS service registration | Registers HeartBeat as OS-level startup service | OS | Not built |
| Enterprise HA standby | Warm standby HeartBeat for enterprise redundancy | Shared/replicated DBs | Not designed yet |

---

## 15. Resolved Design Decisions

| Decision | Resolution |
|---|---|
| Auth database location | **Dedicated auth.db** — sensitivity warrants isolation. SQLCipher-encrypted at rest. |
| Enrollment token delivery | **Email** for now — Owner/Admin receives token via configured email |
| License signature algorithm | **Ed25519** — faster, smaller, equally secure to RSA-SHA256 |
| JWT vs X-User-ID | **JWT everywhere** — X-User-ID deprecated |
| External system user ID | **Enforced flag per integration** — configurable by Owner/Admin |
| HeartBeat startup | **OS-level service** — starts with OS, supervises all others |
| Step-up auth model | **Per-operation configuration in HeartBeat** — not hardcoded in services |
| SSE filtering | **Server-side per role** — not client-side |
| Actor types | **human \| system** — both fully attributed in audit trail |
| Company name | **Pronalytics Limited** — all WestMetro references to be updated |
| Entra SSO + step-up | **HeartBeat trusts Microsoft's re-auth timestamp** as `last_auth_at`. No duplicate re-verification. |
| Float session persistence | **Token persisted locally** — if Float is closed and reopened within the 8-hour window, user does not need to log in again. Token is stored securely on the local machine. |
| Support elevated access scope | **Configurable per grant** — when granting time-bound elevated access to Support, the granting Owner or Admin specifies the exact scope. May include financial/invoice data or be restricted to technical/operational data only. |
| Owner A authority | **Owner A (packaging-time Owner) is the foundational authority** — cannot be deactivated by Owner B. Only self-deactivation or Pronalytics intervention via the all-Father key can change this. |
| Owner deactivation | **Owner A cannot be deactivated by Owner B.** Owner A can deactivate Owner B. Owner A self-deactivation requires Pronalytics intervention. |
| All-Father key | **Pronalytics holds an Ed25519 all-Father private key.** The corresponding public key is baked into the HeartBeat binary at build time. Pronalytics can sign override commands (Owner re-appointment, forced deactivation) that HeartBeat verifies locally. Only Pronalytics can change the all-Father key — through signed software updates. |
| Process manager — Windows | **NSSM (Non-Sucking Service Manager)** — wraps HeartBeat and child services as Windows Services. Handles restart policies, captures logs, zero boilerplate. |
| Process manager — Linux | **Hybrid** — HeartBeat itself registered as a systemd unit (OS guarantee). Child services (Core, Relay, Edge, HIS) managed by HeartBeat's own internal process manager, not individual systemd units. HeartBeat owns restart logic, ordering, backoff, and audit logging. |
| Enterprise HA | **Stubbed for now** — Enterprise tier concern. Warm standby HeartBeat. Dedicated design session required. |
| auth.db encryption | **SQLCipher at rest** — auth.db contains the most sensitive data in the platform. SQLCipher encryption is not Phase 3 deferred — it is a requirement from the start. |
| Software updates | **HeartBeat owns update delivery** — Pronalytics-signed updates verified via all-Father public key before application. Update mechanism to be designed in a dedicated session. |

---

## 16. Contract Files That Must Be Updated

| Contract | Section | Nature of Change |
|---|---|---|
| HeartBeat Part 1 §3.8 | User Auth Stub | Complete rewrite — HeartBeat now owns auth fully |
| HeartBeat Part 3 §1 | Audit Logging | actor_type + actor_id + asserted_user_id replace optional user_id |
| HEARTBEAT_API_CONTRACTS.md | New sections | Auth endpoints, enrollment endpoints, integration config endpoints |
| HEARTBEAT_OVERVIEW.md | Executive summary | Add auth, tenancy governance, license enforcement, process management |
| Relay Service Contract | Auth section | Two call paths: human JWT vs system API key. user_id_enforced check. X-User-ID deprecated. |
| Core Service Contract | New section | JWT verification + step-up auth per operation. Finalisation is Float→Core direct. |
| Float/SDK Contract Part 2 | Credential flow | JWT acquisition at login + downstream attachment + step-up handling |

---

## 17. Open Questions

All previously listed questions have been resolved. See Section 15.

New open questions arising from resolutions:

| # | Question | Impact |
|---|---|---|
| 1 | **Float local token storage security** — How is the persisted session token stored securely on the local Windows machine? Options: Windows Credential Manager (DPAPI), encrypted file, or Windows Data Protection API directly. Must be protected against other local users and basic filesystem access. | Float implementation |
| 2 | **All-Father key override UX** — When Pronalytics uses the all-Father key to re-appoint an Owner, what is the exact delivery mechanism? A signed command file that the new Owner installs, a Pronalytics engineer on-site, or a secure out-of-band process? | Pronalytics ops process |
| 3 | **Software update mechanism** — HeartBeat owns update delivery and verifies Pronalytics-signed updates via the all-Father public key. How are updates packaged, how does HeartBeat check for them (pull vs push), and how does it apply them without downtime? Dedicated design session required. | HeartBeat update architecture |
| 4 | **Enterprise HA — split-brain prevention** — How does Standby HeartBeat detect Primary failure and promote itself without a split-brain scenario? Dedicated design session required. Enterprise tier only. | Enterprise deployment |

---

*This document is a working design record.*
*It will be superseded by the formal HEARTBEAT_SERVICE_CONTRACT updates*
*once design is finalised and approved by Pronalytics.*

*Last updated: 2026-02-23*
*Version: 0.4*
*Next step: Write formal HeartBeat auth contract section (Part 1 §3.8 rewrite)*
*based on this document, followed by API endpoint design.*
*Remaining open questions (Section 17) are implementation-detail level and*
*do not block contract writing or endpoint design.*
