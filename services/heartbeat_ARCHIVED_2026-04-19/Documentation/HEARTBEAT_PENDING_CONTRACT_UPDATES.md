# HeartBeat — Pending Contract Updates Tracker

**Version:** 1.0
**Date:** 2026-02-23
**Status:** LIVE TRACKER — update as items are completed
**Audience:** All service teams, Pronalytics Engineering
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## Purpose

This document tracks every contract, specification, and documentation
file that requires updating as a result of architectural decisions made
in the February 2026 design session. Nothing should fall through the
cracks. Each item has a clear owner, what needs to change, and why.

**Rule:** When an item is completed, mark it ✅ and add the completion date.

---

## Critical — Must Be Done Before Any Service Teams Implement

These items are blockers. If service teams implement against the old
contracts, they will build the wrong thing.

---

### 1. Relay Service Contract — X-User-ID Deprecation + JWT + Actor Model

**File:** Relay service contract (locate and update)
**Status:** ❌ Not done
**Priority:** CRITICAL — blocks Relay auth implementation

**What to change:**

Every reference to `X-User-ID` as a user identity mechanism must be
updated. Relay now handles two call paths:

**Human actors (Float SDK calls):**
- Relay receives `Authorization: Bearer {user_jwt}` on all requests
- For sensitive operations, Relay calls HeartBeat introspection before processing:
  `POST /api/auth/introspect` with the user JWT
- For routine operations, Relay uses locally cached introspection result
  (cache refreshed every 30-60 seconds)
- `X-User-ID` header is deprecated — do not use for identity

**System actors (ERP / external system calls):**
- Relay receives `Authorization: Bearer {api_key}:{api_secret}`
- No JWT — system actors do not have user JWTs
- Relay checks the integration config for the calling API key:
  `GET /api/auth/integrations/{api_key_prefix}` to retrieve
  `user_id_enforced` flag
- If `user_id_enforced: true` and no `X-User-ID` header present:
  reject with `USER_ID_REQUIRED` error
- If `user_id_enforced: false`: proceed, log `asserted_user_id` if
  `X-User-ID` header is present

**Audit trail change:**
Old: `{ "user_id": "optional string" }`
New:
```json
{
  "actor_type": "human | system",
  "actor_id": "{user_id} | {api_key_prefix}",
  "actor_name": "john@abbey.com | SAP_GTBank_Production",
  "verified_user_id": "{user_id} | null",
  "asserted_user_id": "null | {X-User-ID header value}"
}
```

**Reference:** Part 4 §1 (JWT model), Part 4 §2 (actor types),
Part 4 §3 (external system user ID enforcement)

---

### 2. Core Service Contract — JWT Verification + Step-Up Auth Per Operation

**File:** Core service contract (locate and update)
**Status:** ❌ Not done
**Priority:** CRITICAL — blocks Core auth implementation

**What to change:**

Core must verify user JWTs before processing sensitive operations.
It must also enforce step-up authentication requirements per operation.

**JWT verification:**
- All user-initiated requests carry `Authorization: Bearer {user_jwt}`
- Core calls `POST /api/auth/introspect` for sensitive operations
  (invoice approval, finalisation, config changes)
- Introspection request includes `required_permission` and
  `required_within_seconds` for the specific operation
- If introspection returns `step_up_satisfied: false`, Core returns
  `STEP_UP_REQUIRED` — it does not process the request

**Finalisation flow — important:**
Float SDK calls Core directly for invoice finalisation.
Relay is NOT in this call path.
Core verifies the user JWT against HeartBeat before processing
any finalisation request.

**Step-up windows per Core operation:**
| Operation | Window |
|---|---|
| View invoices | 1 hour |
| Upload batch | 1 hour |
| Approve invoice | 5 minutes |
| Finalise batch | 5 minutes |
| Config changes | 10 minutes |

Core fetches operation policies from HeartBeat:
`GET /api/auth/operations/{operation}/policy`
Cache these for 5 minutes — do not hardcode.

**Audit trail:** Same actor model as Relay (see item 1 above).

**Reference:** Part 4 §1, Part 4 §7 (step-up auth), Part 4 §9
(introspection)

---

### 3. Float / SDK Contract Part 2 — JWT Acquisition and Downstream Attachment

**File:** Float/SDK contract Part 2 (locate and update)
**Status:** ❌ Not done
**Priority:** CRITICAL — blocks Float auth implementation

**What to change:**

Float SDK must acquire a JWT from HeartBeat at login and attach it
to all downstream service calls.

**Login flow:**
```
1. User enters credentials in Float
2. Float SDK: POST /api/auth/login → receives JWT
3. JWT stored securely:
   Windows: Windows Credential Manager (DPAPI)
   Linux: Secret Service API (libsecret)
4. All subsequent calls to Relay, Core, HIS, Edge:
   Authorization: Bearer {user_jwt}
5. Background refresh every 60 minutes:
   POST /api/auth/token/refresh
6. On app reopen within 8-hour window:
   Load persisted token — no re-login required
```

**Step-up auth handling:**
When a downstream service returns `STEP_UP_REQUIRED`:
```
1. Float SDK receives STEP_UP_REQUIRED response
2. Float presents re-authentication prompt (not a full logout)
3. User re-enters password (and MFA if Immediate tier)
4. Float SDK: POST /api/auth/stepup → receives step-up token
5. Float retries the original request with step-up token attached
6. No user action required beyond re-entering credentials
```

**SSE connection:**
Float opens SSE stream with user JWT:
```
GET /api/v1/events/blobs
Authorization: Bearer {user_jwt}
```
On JWT expiry (close code 4401): Float refreshes token and reconnects
seamlessly.

**Reference:** Part 4 §6 (auth policies), Part 4 §7 (step-up),
Part 4 §8 (SSE auth)

---

## Important — Should Be Done Before Integration Testing

---

### 4. HeartBeat Service Contract Part 3 — Audit Schema Update

**File:** `HEARTBEAT_SERVICE_CONTRACT_PART3.md`
**Status:** ❌ Not done
**Priority:** HIGH

**What to change:**

Section 1 (audit logging) still references the old `user_id` optional
field in audit events. This must be replaced with the full actor model.

Old audit event shape:
```json
{
  "service": "relay",
  "event_type": "file.uploaded",
  "details": {
    "user_id": "optional string",
    "filename": "invoice_batch.xml"
  }
}
```

New audit event shape — all services must conform:
```json
{
  "service": "relay",
  "event_type": "file.uploaded",
  "actor_type": "human",
  "actor_id": "usr-abc123",
  "actor_name": "john.adeyemi@abbey.com",
  "verified_user_id": "usr-abc123",
  "asserted_user_id": null,
  "details": {
    "filename": "invoice_batch.xml"
  }
}
```

`user_id` field in `details` is removed. Identity is always at the
top level of the audit event, never nested in details.

**Reference:** Part 4 §2 (actor types and audit schema)

---

### 5. HeartBeat Service Contract Part 1 — Company Name Update

**File:** `HEARTBEAT_SERVICE_CONTRACT_PART1.md`
**Status:** ⚠️ Partially done (footer note added in v3.2)
**Priority:** HIGH

**What to change:**

All remaining references to "WestMetro" must be replaced with
"Pronalytics Limited". The v3.2 update added a note but did not
do a full find-and-replace.

Search for: `WestMetro`
Replace with: `Pronalytics Limited`

Also update the file path references in the document header —
the document is stored in a WestMetro OneDrive path which should
eventually migrate to a Pronalytics path.

---

### 6. All Service Contracts — Company Name Update

**Files:** All contract and specification documents
**Status:** ❌ Not done
**Priority:** HIGH

All documentation must refer to Pronalytics Limited, not WestMetro.
Perform a find-and-replace across:
- All `HEARTBEAT_SERVICE_CONTRACT_PART*.md` files
- `HEARTBEAT_AUTH_DESIGN.md`
- `HEARTBEAT_BACKUP_SPEC.md`
- `HEARTBEAT_UPDATE_SPEC.md`
- `HEARTBEAT_LIFECYCLE_SPEC.md`
- Any Relay, Core, Float/SDK contract files
- Any installer documentation

---

## Near-Term — Before Production Deployment

---

### 7. HIS Service Contract — JWT Verification

**File:** HIS service contract (locate and update)
**Status:** ❌ Not done
**Priority:** MEDIUM

HIS must verify user JWTs for any operation that returns sensitive
business data. Same introspection pattern as Core. HIS-specific
step-up windows to be defined when HIS operations are finalised.

---

### 8. Edge Service Contract — JWT Verification + FIRS Submission Auth

**File:** Edge service contract (locate and update)
**Status:** ❌ Not done
**Priority:** MEDIUM

Edge submits to FIRS. Any Edge operation triggered by a user action
must carry a verified JWT. FIRS submission itself is a system-to-system
call (Edge to FIRS API) — this uses Edge's own FIRS credentials, not
the user JWT. But the trigger that initiates submission must be
authenticated and logged against the human actor who approved it.

---

### 9. HeartBeat API Contracts File — New Auth and Enrollment Sections

**File:** `HEARTBEAT_API_CONTRACTS.md` (if it exists separately)
**Status:** ❌ Not reviewed
**Priority:** MEDIUM

If a separate API contracts file exists, it needs new sections added
for all auth endpoints, enrollment endpoints, backup endpoints,
lifecycle endpoints, and update endpoints defined in:
- Part 4
- `HEARTBEAT_BACKUP_SPEC.md`
- `HEARTBEAT_UPDATE_SPEC.md`
- `HEARTBEAT_LIFECYCLE_SPEC.md`

---

### 10. HEARTBEAT_OVERVIEW.md — Update to Reflect Full Scope

**File:** `HEARTBEAT_OVERVIEW.md` (if it exists)
**Status:** ❌ Not reviewed
**Priority:** MEDIUM

The overview document should reflect HeartBeat's full current scope:
- Auth and tenancy governance (Part 4)
- License enforcement
- Enrollment
- Backup
- Software updates
- Service lifecycle management

---

## Future — Before Enterprise Tier Launch

---

### 11. Enterprise HA Contract — Warm Standby Design

**File:** New file — `HEARTBEAT_HA_SPEC.md`
**Status:** ❌ Stubbed only — dedicated design session required
**Priority:** ENTERPRISE

Warm standby HeartBeat promotion, split-brain prevention, database
replication (Litestream or shared storage), and promotion timing
are all undesigned. This requires a full dedicated session before
Enterprise HA is implemented.

---

### 12. Primary / Satellite Contract — Multi-Location Design

**File:** Existing scaffolding in codebase — contract not written
**Status:** ❌ Scaffolded — contract not written
**Priority:** ENTERPRISE

Parent/Satellite topology for multi-location Enterprise deployments
is scaffolded in the codebase but the contract and design are not
fully fleshed out. Dedicated session required.

---

### 13. Software Update Contract — Optional Pull Mechanism

**File:** `HEARTBEAT_UPDATE_SPEC.md` (future revision)
**Status:** ❌ Future option — not designed
**Priority:** ENTERPRISE / OPTIONAL

For cloud-hosted clients who want HeartBeat to pull updates from a
Pronalytics endpoint rather than using the file-drop model. This is
strictly opt-in and requires network policy agreement with the client.
Design when there is client demand.

---

## Completed Items

| Item | Completed | Notes |
|---|---|---|
| Part 1 §3.8 — auth stub replaced with redirect to Part 4 | ✅ 2026-02-23 | v3.2 |
| Part 4 written — full auth contract | ✅ 2026-02-23 | v1.0 |
| `HEARTBEAT_AUTH_DESIGN.md` — design decisions documented | ✅ 2026-02-23 | v0.4 |
| `HEARTBEAT_BACKUP_SPEC.md` — backup strategy documented | ✅ 2026-02-23 | v1.0 |
| `HEARTBEAT_UPDATE_SPEC.md` — software update documented | ✅ 2026-02-23 | v1.0 |
| `HEARTBEAT_LIFECYCLE_SPEC.md` — lifecycle documented | ✅ 2026-02-23 | v1.0 |
| Part 1 v3.2 — "HeartBeat does not authenticate users" corrected | ✅ 2026-02-23 | v3.2 |

---

*End of Pending Contract Updates Tracker.*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-02-23 | Version: 1.0*
