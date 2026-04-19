# HeartBeat — Alignment Blueprint

**Document:** ALIGNMENT_BLUEPRINT
**Version:** 1.0
**Date:** 2026-03-03
**Status:** AUTHORITATIVE — captures all conflict resolutions from March 2026 alignment session
**Audience:** All service teams, SDK team
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## Purpose

This document maps every conflict, gap, and stale reference found between HeartBeat documentation, SDK code, and the HEL-* specification documents. Each item has a resolution, an owner, and a priority.

---

## Part A — Resolved Conflicts

### A1. JWT Algorithm Mismatch

**Conflict:** HeartBeat jwt_manager.py signs JWTs with EdDSA (Ed25519). SDK AuthProvider (`ws5_auth/auth.py`) validates with HS256/RS256. These are incompatible.

**Resolution:** Ed25519 is canonical. SDK AuthProvider updated to verify EdDSA tokens.

**Changes required:**
- [x] SDK `ws5_auth/auth.py` — change `self.algorithm` default and logic to support EdDSA
- [x] SDK `ws5_auth/auth.py` — load Ed25519 public key instead of shared secret
- [ ] SDK `ws5_auth/models.py` — no change needed (AuthContext is algorithm-agnostic)
- [ ] HeartBeat jwt_manager.py — no change needed (already correct)

**Owner:** SDK team (AuthProvider update done in this session)

---

### A2. TOTP Replaced by SSE Cipher Text

**Conflict:** HEL-SDK-001 specifies a TOTP mechanism where SDK polls HeartBeat every 10 minutes for a time-window derived key. This has been replaced by SSE-pushed cipher text every ~9 minutes.

**Resolution:** TOTP is dead. SSE cipher text is the canonical mechanism.

**Changes required:**
- [ ] HEL-SDK-001 — Section 5 (TOTP Architecture) must be rewritten to describe SSE cipher text model
- [ ] HEL-SDK-001 — Section 5.3 (TOTP Refresh Cycle) → SSE delivery cycle
- [ ] HEL-SDK-001 — Section 5.4 (Full Access Flow) → updated flow diagram
- [ ] HeartBeat SSE producer — must implement `auth.cipher_refresh` event type
- [ ] SDK SSE consumer — must handle `auth.cipher_refresh` events

**Stale references to remove:**
- HEL-SDK-001 §5.1: "SDK requests a derived time-window key from HeartBeat" → push, not pull
- HEL-SDK-001 §5.3: "SDK silently requests a fresh TOTP" → SSE delivers proactively

**Owner:** Architecture team (doc update), HeartBeat team (SSE producer), SDK team (SSE consumer)

---

### A3. Two Auth Models — Local vs Entra

**Conflict:** Part 4 contract specifies local bcrypt auth. HEL-AUTH-001 specifies Entra/MSAL with AMR validation. SDK AuthProvider has neither MSAL nor local login code.

**Resolution:** Both paths coexist at different tiers. Local auth is current. Entra/MSAL is future (enterprise tier). Both produce the same HeartBeat JWT.

**Current state:**
- HeartBeat: `POST /api/auth/login` (local, bcrypt) — BUILT
- HeartBeat: Entra token exchange — NOT BUILT (future)
- SDK: AuthProvider validates JWT — BUILT (algorithm updated to Ed25519)
- SDK: MSAL login flow — NOT BUILT (future, Float App responsibility)

**No immediate changes required.** HEL-AUTH-001 remains a future reference spec.

---

### A4. PIN Ownership

**Conflict:** HEL-FLOAT-001 trigger #9 says "HeartBeat pushes a role update; last_typed_in cleared." HeartBeat Part 4 has `permissions_version` in auth.db. This implies HeartBeat manages PIN state.

**Resolution:** PIN is 100% SDK-local. HeartBeat never sees, stores, or validates PINs. HeartBeat's only involvement: pushing `permission.changed` events via SSE, which the SDK interprets internally as a PIN re-entry trigger.

**Changes required:**
- [ ] HEL-FLOAT-001 — Clarify trigger #9 wording: "HeartBeat pushes a permission change event via SSE. The SDK clears last_typed_in locally." Remove any implication that HeartBeat manages PIN state.
- [ ] HeartBeat Part 4 — Remove or clarify any PIN references. HeartBeat has no PIN awareness.

---

### A5. Server-Side Database Engine

**Conflict:** HeartBeat codebase uses SQLite (blob.db, registry.db, config.db, auth.db, license.db). HEL-INFRA-001 mandates PostgreSQL for all server-side databases.

**Resolution:** PostgreSQL for all tiers. One instance, one database (`heartbeat`), multiple schemas (`auth`, `blob`, `audit`, `registry`, `license`, `notifications`).

**Changes required:**
- [ ] Migrate all SQLite schemas to PostgreSQL DDL
- [ ] Replace sqlite3/sqlcipher3 imports with asyncpg or psycopg on HeartBeat server side
- [ ] Update connection management (connection pools instead of file paths)
- [ ] Update HeartBeat config.py — PostgreSQL connection string instead of file paths
- [ ] config.db content merges into `registry` schema (same domain)
- [ ] SQLite databases become dev/test artifacts only

**Migration sequence:**
1. Write PostgreSQL schemas for each component
2. Update connection layer (abstract DB interface)
3. Migrate data from SQLite seeds to PostgreSQL seeds
4. Update tests

**Owner:** HeartBeat team

---

### A6. SSE Producer Not Built

**Conflict:** SDK has a full SSE consumer (`ws3_sync/sse_client.py`) consuming 11 event types. HeartBeat's SSE producer is listed as NOT BUILT.

**Resolution:** SSE producer is a shared transport layer. One endpoint multiplexes all component events with server-side JWT permission filtering.

**Changes required:**
- [ ] Build HeartBeat SSE endpoint: `GET /api/sse/stream`
- [ ] Internal event bus (asyncio queue) for components to publish to
- [ ] JWT-based connection authentication
- [ ] Permission-scoped event filtering
- [ ] Event types: `auth.cipher_refresh`, `blob.uploaded`, `blob.status_changed`, `config.changed`, `notification.new`, `notification.updated`, `permission.changed`, `session.revoked`, `service.health_changed`
- [ ] Keepalive/heartbeat pings on the SSE stream

**Owner:** HeartBeat team

**Priority:** CRITICAL — blocks SDK real-time sync and cipher text delivery

---

### A7. Secrets Management

**Conflict:** HEL-INFRA-001 specifies HeartBeat as central secrets authority. SDK reads credentials from environment variables. HeartBeat contracts have no secrets API.

**Resolution:** Deferred. Services continue reading env vars. Abstracted secrets interface to be built when enterprise client is onboarded or when lightweight implementation proves feasible.

**No immediate changes required.** HEL-INFRA-001 remains the future target architecture.

---

### A8. E2EE — SDK Side Not Built

**Conflict:** HeartBeat Part 1 specifies NaCl E2EE. Relay decryption is built. SDK encryption is not built.

**Resolution:** Document the contract. SDK team implements when prioritized.

**Changes required:**
- [x] E2EE contract note for SDK team (written in this session — see `E2EE_SDK_TEAM_NOTE.md`)
- [ ] SDK team: implement NaCl encryption in relay_client.py

**Owner:** SDK team (implementation), Architecture team (contract note done)

---

## Part B — Gaps Found (Not Previously Documented)

### B1. Notifications Database

**Gap:** HeartBeat contracts mention notifications only in the context of blob reconciliation alerts. No comprehensive notification system is specified.

**Resolution:** HeartBeat owns a new `notifications` schema in PostgreSQL. All notification types: system, business, admin, approval requests, cadenced quarterly reports, platform announcements.

**New work required:**
- [ ] Design notification schema (notifications, delivery_log, templates, schedules)
- [ ] Design notification REST API (query historical, mark read, preferences)
- [ ] Implement notification event publishing to SSE bus
- [ ] Design permission-scoped filtering rules
- [ ] Design cadenced report generation (quarterly summaries)

**Owner:** HeartBeat team + Architecture team (schema design)

**Priority:** MEDIUM — required for Float notification panel, not blocking core upload flow

---

### B2. Step-Up Auth Endpoints

**Gap:** Part 4 specifies step-up authentication (per-operation re-auth windows). The SDK's HEARTBEAT_PENDING_CONTRACT_UPDATES.md lists `POST /api/auth/stepup` as needed by Float SDK. This endpoint is not built.

**Resolution:** Step-up auth is part of the Auth component.

**New work required:**
- [ ] Build `POST /api/auth/stepup` endpoint on HeartBeat
- [ ] Build `GET /api/auth/operations/{operation}/policy` endpoint
- [ ] SDK: Handle `STEP_UP_REQUIRED` responses from Core/Relay
- [ ] SDK: Present re-auth prompt (not full logout)

**Owner:** HeartBeat team (endpoints), SDK team (client handling)

**Priority:** HIGH — required for production invoice approval/finalization

---

### B3. Enrollment Tokens

**Gap:** Part 4 specifies single-use enrollment tokens for new Float/Relay/satellite instances. Not built.

**Resolution:** Part of Auth component. Needed for production deployments.

**New work required:**
- [ ] Build enrollment token generation endpoint
- [ ] Build enrollment token activation flow
- [ ] Build first-run bootstrap handler

**Owner:** HeartBeat team

**Priority:** MEDIUM — needed for multi-instance deployments

---

### B4. License Enforcement

**Gap:** license.db schema exists. Ed25519 signature verification is specified in Part 4. Verifier and enforcer are not built.

**Resolution:** Part of Platform Services component.

**New work required:**
- [ ] Build Ed25519 license signature verifier
- [ ] Build license enforcer (seat limits, feature gates, storage quotas)
- [ ] Integrate with HeartBeat startup (verify license before starting services)

**Owner:** HeartBeat team

**Priority:** MEDIUM — needed before any paid deployment

---

### B5. Reconciliation Job

**Gap:** Blob reconciliation (hourly job detecting orphaned blobs, missing files, stale processing) is specified in RECONCILIATION_KICKSTART.md but not built.

**Resolution:** Part of Blob Service component.

**Owner:** HeartBeat team

**Priority:** LOW — operational reliability feature, not blocking core flow

---

### B6. SDK JWT Claim Mapping

**Gap:** HeartBeat Part 4 JWT uses claim `sub` for user_id. SDK AuthProvider extracts `user_id` from payload (not `sub`). SDK also expects `email` and `scopes` directly in payload, while Part 4 JWT has `role` and `permissions` (not `scopes`).

**Resolution:** Align SDK AuthProvider with HeartBeat's actual JWT structure.

**Changes required:**
- [ ] SDK AuthProvider: extract `sub` (not `user_id`) for user identity
- [ ] SDK AuthProvider: map `permissions` to `scopes` (or rename to match)
- [ ] SDK AuthContext model: add `role`, `tenant_id`, `permissions_version`, `session_expires_at`
- [ ] SDK AuthContext: `is_admin` derived from `role == "admin" or role == "owner"` (not scope-based)

**Owner:** SDK team

**Priority:** HIGH — blocks JWT interop between HeartBeat and SDK

---

## Part C — Stale Documents to Archive

Move the following to `Documentation/archive/` with a header noting the superseding document:

| Document | Reason | Superseded By |
|---|---|---|
| `HEARTBEAT_OVERVIEW.md` | Replaced by v2.0 | `HEARTBEAT_OVERVIEW_V2.md` |
| `FLOAT_SDK_AUTH_INTEGRATION.md` | Superseded by Part 4 + Overview v2 | `HEARTBEAT_OVERVIEW_V2.md` §4 |
| `AUTH_IMPLEMENTATION_NOTES.md` | Implementation-specific, stale with Postgres migration | Part 4 contract |
| `PHASE_2_DEMO_PLAN.md` | Phase 2 complete, historical | Archive |
| `SDK_TEAM_RESPONSE.md` | One-time response, historical | Archive |
| `HEARTBEAT_API_CONTRACTS.md` | Will be replaced by per-component contracts | Component contracts |
| `HEARTBEAT_AUTH_DESIGN.md` | Design decisions captured in Part 4 + Overview v2 | Part 4 + Overview v2 |

**Do NOT archive yet:**
- Parts 1-4 — remain authoritative until per-component contracts are written
- `HEARTBEAT_LIFECYCLE_SPEC.md` — still authoritative for Keep Alive
- `HEARTBEAT_BACKUP_SPEC.md` — still authoritative
- `HEARTBEAT_UPDATE_SPEC.md` — still authoritative
- `RECONCILIATION_KICKSTART.md` — still the spec for reconciliation (not built yet)
- `RELAY_INTEGRATION_REQUIREMENTS.md` — still valid for Relay team
- `HEARTBEAT_BLOB_IMPLEMENTATION_NOTE.md` — still valid implementation reference
- `HEARTBEAT_PENDING_CONTRACT_UPDATES.md` — still a live tracker
- `TRACE_DECISION_NOTE_20260301.md` — recent decision, still valid

---

## Part D — Pending Contract Updates (from existing tracker)

The following items from `HEARTBEAT_PENDING_CONTRACT_UPDATES.md` are still open and must be addressed:

| # | Item | Status | Component |
|---|---|---|---|
| 1 | Relay contract — X-User-ID deprecation + JWT | NOT DONE | Auth / Relay |
| 2 | Core contract — JWT verification + step-up | NOT DONE | Auth / Core |
| 3 | Float/SDK contract — JWT acquisition flow | NOT DONE | Auth / SDK |
| 4 | Part 3 — audit schema actor model update | NOT DONE | Audit |
| 5 | Company name — WestMetro → Pronalytics | PARTIALLY DONE | All docs |
| 6 | All contracts — company name update | NOT DONE | All docs |
| 7 | HIS contract — JWT verification | NOT DONE | Auth / HIS |
| 8 | Edge contract — JWT + FIRS auth | NOT DONE | Auth / Edge |
| 9 | API contracts file — new auth sections | NOT DONE | Auth |
| 10 | Overview — reflect full scope | DONE (this session) | Overview v2 |

---

## Part E — Auth Decisions (March 2026 Deep-Dive)

Captured during exhaustive auth flow walkthrough:

| # | Decision | Detail |
|---|---|---|
| E1 | SDK calls HeartBeat directly for auth | Never through Relay. HeartBeat URL configured at install time. |
| E2 | Login response includes cipher_text | Zero latency to sync.db access. No waiting for SSE. |
| E3 | JWT stored in OS keyring | Windows Credential Manager (DPAPI) / libsecret. Python `keyring` package. |
| E4 | JWT expiry = 30 minutes (confirmed) | `HEARTBEAT_JWT_EXPIRY_MINUTES=30`. Silent refresh at 25-min mark. |
| E5 | Permission change = force re-auth | `PERMISSIONS_CHANGED` error on refresh. No silent permission updates. |
| E6 | Introspection: always cache, 30-60s TTL | Same TTL for routine and sensitive ops. Simple, predictable. |
| E7 | Step-up tiers: PIN-only (SDK) vs Auth-only (HeartBeat) | Re-auth resets both `last_auth_at` and `last_PIN_at`. |
| E8 | SDK pre-checks `last_auth_at` locally | Avoids wasted round-trip to Core. SSE pushes `last_auth_at`. |
| E9 | Cipher text = liveness with retry | 10 min without → retry re-auth APIs → N failures → lock + logout. |
| E10 | Fail closed on HeartBeat unreachable | No fallback to local JWT verification. Platform down. |
| E11 | Reconnection: auto polling (30s) + manual retry button | |
| E12 | Key rotation: only at re-auth/restart, 6-12 months | Key wrapper re-wrap only. No database rebuild. |
| E13 | Step-up includes cipher_text | Same as login — re-auth event produces fresh cipher text. |
| E14 | First-run: Pronalytics admin tool creates Owner | Bootstrap token (scope: "bootstrap") restricts to password change only. |
| E15 | Concurrent sessions: configurable per tenant | `max_concurrent_sessions` (default: 1). Oldest session revoked on exceed. |
| E16 | AUTH_SERVICE_CONTRACT.md written | 15 sections, 8 endpoints, full flow diagrams, PostgreSQL schema. |

---

## Part F — Implementation Priority Matrix

### Critical (blocks core functionality)
1. SSE producer on HeartBeat (blocks real-time sync + cipher text delivery)
2. SDK AuthProvider Ed25519 update (blocks JWT interop) — DONE this session
3. SDK JWT claim mapping alignment (B6)
4. Step-up auth endpoint: POST /api/auth/stepup (B2) — contract written
5. Login response: add cipher_text field
6. master_secret column in auth.users

### High (blocks production deployment)
7. PostgreSQL migration for HeartBeat databases (A5)
8. Relay/Core/Float contract updates for JWT (D1-D3)
9. Audit actor model update (D4)
10. License enforcement (B4)
11. Concurrent session enforcement
12. Step-up policy endpoint: GET /api/auth/operations/{op}/policy

### Medium (blocks feature completeness)
13. Notification system design + implementation (B1)
14. Enrollment tokens (B3)
15. HEL-SDK-001 rewrite for SSE cipher text (A2)
16. WestMetro → Pronalytics company name (D5-D6)
17. HIS/Edge JWT contracts (D7-D8)
18. SDK keyring integration (JWT persistence)
19. SDK health polling during outage + reconnection flow

### Low (operational reliability)
20. Blob reconciliation job (B5)
21. Secrets management abstraction (A7)
22. E2EE SDK encryption side (A8)
23. Master secret rotation implementation

---

*End of Alignment Blueprint*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-03-03*
