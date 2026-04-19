# HeartBeat Service — Architecture Overview v2.0

**Document:** HEARTBEAT_OVERVIEW_V2
**Version:** 2.0
**Date:** 2026-03-03
**Status:** AUTHORITATIVE — supersedes HEARTBEAT_OVERVIEW.md
**Audience:** All Helium service teams, SDK team, Pronalytics Engineering
**Maintained by:** Pronalytics Limited — Helium Core Team

---

## 1. What HeartBeat Is

HeartBeat is Helium's central infrastructure service. It is the first process to start on any Helium deployment and the last to stop. Every other Helium service — Core, Relay, Edge — starts through HeartBeat and is kept alive by HeartBeat.

HeartBeat is a single deployable (one FastAPI process) but internally composed of six logical service components. These components share a process, a PostgreSQL instance, and an SSE transport layer, but are architecturally independent — each with its own API router, database schema, and documentation.

HeartBeat is deployed on the client's own infrastructure. Pronalytics has no runtime access to client HeartBeat instances.

---

## 2. The Six Components

| # | Component | Schema | Primary Responsibility |
|---|-----------|--------|----------------------|
| 1 | **Keep Alive** (Process Manager) | — | OS service registration, service startup sequencing, health monitoring, restart policies |
| 2 | **Auth** | `auth` | User authentication (local + Entra future), JWT issuance, sessions, SSE cipher text delivery |
| 3 | **Audit** | `audit` | Immutable event logging, checksum chain verification, OCSF/Wazuh integration |
| 4 | **Service Registry** | `registry` | Service registration, discovery, API credentials, per-service configuration, secrets (future) |
| 5 | **Blob Service** | `blob` | Blob metadata, file storage, deduplication, reconciliation, retention enforcement |
| 6 | **Platform Services** | `license`, `notifications` | License enforcement, notifications (all types), metrics/observability, database catalog |

---

## 3. Database Architecture

### 3.1 Server-Side — PostgreSQL (All Tiers)

All HeartBeat databases use PostgreSQL. SQLite is not used server-side in production.

**Single PostgreSQL instance, one database, multiple schemas:**

```
PostgreSQL Instance
└── Database: heartbeat
    ├── Schema: auth
    │   └── users, roles, permissions, role_permissions, user_permissions, sessions
    ├── Schema: blob
    │   └── blob_entries, blob_batches, blob_batch_entries, blob_outputs,
    │       blob_deduplication, blob_access_log, blob_cleanup_history
    ├── Schema: audit
    │   └── audit_events, security_events (immutable, INSERT-only with triggers)
    ├── Schema: registry
    │   └── service_instances, endpoint_catalog, api_credentials,
    │       key_rotation_log, service_config, satellite_registrations,
    │       schema_migrations
    ├── Schema: license
    │   └── license_document, verification_log
    └── Schema: notifications
        └── notifications, delivery_log, templates, schedules
```

**Why one instance, multiple schemas:**
- One backup target for small enterprise IT teams (`pg_dump heartbeat`)
- One connection pool, one set of credentials to rotate
- Cross-schema queries when needed (audit referencing auth.users)
- Logical isolation per component — no table name collisions
- Upgrade path: Enterprise can split schemas into separate databases for compliance

### 3.2 Client-Side — SQLite with SQLCipher (SDK)

Float's local `sync.db` uses SQLite with SQLCipher (AES-256 encryption at rest). This is appropriate for single-user, single-machine use. See HEL-SDK-001 for the sync.db security architecture.

---

## 4. Authentication Architecture

### 4.1 Current Implementation — Local Auth

HeartBeat owns user authentication. The current implementation uses local credential authentication:

```
Float login screen → POST /api/auth/login (email + bcrypt password)
  → HeartBeat validates credentials against auth.users table
  → HeartBeat issues JWT (EdDSA / Ed25519 signed)
  → Float SDK stores JWT securely (Windows Credential Manager / libsecret)
  → All downstream calls carry Authorization: Bearer {jwt}
```

**JWT format (canonical):**
- Algorithm: **EdDSA (Ed25519)** — faster, smaller than RSA, modern standard
- Lifetime: 30 minutes (silent refresh at 25-min mark)
- Session hard cap: 8 hours (immutable — refresh never extends)
- Claims: `sub` (user_id), `tenant_id`, `role`, `permissions`, `permissions_version`, `actor_type`, `last_auth_at`, `session_expires_at`, `jti`

### 4.2 Future Implementation — Entra ID / MSAL (Enterprise Tier)

Enterprise clients (Abbey Mortgage, MTN, Airtel) will authenticate via Microsoft Entra ID. See HEL-AUTH-001 for the full spec.

The flow changes at the front: Float uses MSAL to get an Entra token, then exchanges it with HeartBeat. HeartBeat validates the Entra token (including AMR claim for MFA enforcement) and issues its standard Helium JWT. All downstream behavior is identical — services see the same JWT format regardless of how the user authenticated.

Both auth paths coexist at different tiers. HeartBeat's JWT is the universal identity token.

### 4.3 SSE Cipher Text Delivery

HeartBeat pushes an encrypted cipher text to the SDK via the HeartBeat SSE stream every 9 minutes. This cipher text gates SDK access to the SQLCipher key that protects sync.db.

If the SSE stream drops and 9+ minutes pass without a new cipher text, the SDK purges the SQLCipher key from memory and sync.db becomes inaccessible. This replaces the earlier TOTP mechanism (HEL-SDK-001 is outdated on this point).

The cipher text event is delivered on the same HeartBeat SSE stream as blob events, notification events, and permission change events. It is filtered server-side — only the authenticated user's session receives their cipher text.

### 4.4 PIN — SDK-Managed, HeartBeat-Triggered

PIN is 100% SDK/Float-local. HeartBeat never sees, stores, or validates PINs.

HeartBeat's involvement is limited to pushing events that the SDK's internal policy interprets as PIN re-entry triggers:
- `permission.changed` → SDK clears `last_typed_in` → PIN required
- `session.revoked` → SDK forces full re-auth (implies PIN)

See HEL-FLOAT-001 for the full PIN architecture.

---

## 5. SSE Transport Layer

HeartBeat exposes a single authenticated SSE endpoint that multiplexes all component events with server-side permission filtering.

```
GET /api/sse/stream
Authorization: Bearer {user_jwt}
```

**Event types on the HeartBeat SSE stream:**

| Event Type | Publisher | Description |
|---|---|---|
| `auth.cipher_refresh` | Auth | SQLCipher key material (every ~9 min) |
| `permission.changed` | Auth | User role/permission updated |
| `session.revoked` | Auth | Session forcibly terminated |
| `blob.uploaded` | Blob Service | New blob registered |
| `blob.status_changed` | Blob Service | Blob processing status update |
| `config.changed` | Registry | Service configuration updated |
| `notification.new` | Platform Services | New notification for user |
| `notification.updated` | Platform Services | Notification status change |
| `service.health_changed` | Registry | Service health status change |

**Architecture:**
```
[Auth]──────────┐
[Blob Service]──┤
[Registry]──────┤──→ [Internal Event Bus] ──→ [SSE Router] ──→ per-client JWT filter ──→ SSE stream
[Platform Svc]──┘         (asyncio queue)          ↑
                                              user permissions
                                              from JWT claims
```

The SDK connects to exactly two SSE streams: Core (port 8080) for invoice/customer/product events, and HeartBeat (port 9000) for everything listed above.

---

## 6. Notification System

HeartBeat owns a notifications database (schema: `notifications`) and pushes permission-scoped notifications to SDK clients via SSE.

### 6.1 Notification Types

| Category | Examples |
|---|---|
| **System** | Service down, reconciliation anomaly, license expiring, storage quota warning |
| **Business** | Invoice approved/rejected, new file uploaded by colleague, bulk upload completed |
| **Admin** | User added/deactivated, role changed, security event, credential rotated |
| **Approval** | Pending approval requests requiring user action |
| **Cadenced Reports** | Quarterly summaries, monthly audit digests |
| **Platform** | Helium version update available, maintenance window scheduled |

### 6.2 Permission Scoping

Notifications are filtered by HeartBeat before delivery. An Operator sees business notifications relevant to their scope. An Admin additionally sees admin and system notifications. An Owner sees everything.

HeartBeat stores all notifications in PostgreSQL. The SDK receives real-time pushes via SSE and can also query historical notifications via REST.

---

## 7. Keep Alive / Process Manager

HeartBeat registers itself as an OS-level service (NSSM on Windows, systemd on Linux). It starts automatically on boot before any user logs in.

### 7.1 Responsibilities

1. **Self-registration** as an OS service at install time
2. **Service startup sequencing** — starts Core, Relay, Edge in a defined order
3. **Health monitoring** — periodic health checks on all managed services
4. **Restart policies** — automatic restart on crash with backoff
5. **Graceful shutdown** — ordered shutdown sequence on OS shutdown/reboot

### 7.2 Managed Services

| Service | Start Order | Health Endpoint | Restart Policy |
|---|---|---|---|
| HeartBeat (self) | 1 | `/health` | OS service manager handles |
| Core | 2 | `/health` | Auto-restart, max 3 attempts, then alert |
| Relay | 3 | `/health` | Auto-restart, max 3 attempts, then alert |
| Edge | 4 | `/health` | Auto-restart, max 3 attempts, then alert |

**Float and SDK are NOT managed services.** They are user-initiated desktop applications. If the user closes Float, that is intentional.

### 7.3 Deployment Modes

- **Dev/Test**: HeartBeat runs as a normal process. No OS service registration. Services started manually or via scripts.
- **Production (non-Docker)**: HeartBeat registered as Windows Service / systemd unit. Manages other services via OS service control APIs.
- **Production (Docker)**: Docker Compose manages all services with restart policies. HeartBeat's Keep Alive role is limited to health monitoring and reporting.

---

## 8. Inter-Service Authentication

### 8.1 Service-to-HeartBeat

All services authenticate to HeartBeat using API key + secret pairs:
```
Authorization: Bearer {api_key}:{api_secret}
```

API keys are generated via `POST /api/registry/credentials/generate`. Secrets are shown once at creation, never retrievable after. Rotation via `POST /api/registry/credentials/{id}/rotate`.

### 8.2 HeartBeat Token Introspection

Other services (Core, Relay, Edge) verify user JWTs by calling HeartBeat's introspection endpoint:
```
POST /api/auth/introspect
Authorization: Bearer {service_api_key}:{service_api_secret}
Body: {"token": "{user_jwt}", "required_permission": "...", "required_within_seconds": 300}
```

This is how step-up authentication is enforced across the platform.

### 8.3 Float SDK to Relay (HMAC-SHA256)

Float SDK authenticates to Relay using HMAC-SHA256 signatures:
```
X-API-Key: {float_api_key}
X-Timestamp: {iso8601}
X-Signature: hmac_sha256({api_key}:{timestamp}:{sha256(body)})
```

E2EE (NaCl X25519 + XSalsa20-Poly1305) encryption of payloads is specified but not yet built on the SDK side. See the E2EE alignment note for the SDK team.

---

## 9. Secrets Management (Deferred)

HEL-INFRA-001 specifies HeartBeat as the central secrets authority with an abstracted Get/Set/Rotate interface backed by tiered drivers (env vars → encrypted DB → Vault → client HSM).

This is architecturally correct but deferred. Current implementation: services read credentials from environment variables. The abstracted interface will be implemented when an enterprise client is onboarded, unless a lightweight implementation proves feasible earlier.

---

## 10. Encryption

### 10.1 At Rest — Server Side

Filesystem-level encryption (LUKS on Linux) on PostgreSQL data volumes and blob storage directories. HeartBeat manages all access through APIs — no direct filesystem access by other services.

### 10.2 At Rest — Client Side

SQLCipher (AES-256) on sync.db. Key material gated by SSE-delivered cipher text from HeartBeat (see Section 4.3).

### 10.3 In Transit — E2EE

NaCl X25519 + XSalsa20-Poly1305 for Float SDK → Relay payloads. Relay decryption is built. SDK encryption is specified but not yet implemented.

### 10.4 In Transit — TLS

All HTTP communication uses TLS. HMAC-SHA256 signatures provide additional integrity verification.

---

## 11. Document Index

This overview is the entry point. Per-component contracts provide detailed specifications:

| Document | Status | Scope |
|---|---|---|
| HEARTBEAT_OVERVIEW_V2.md (this file) | AUTHORITATIVE | Architecture, decomposition, cross-cutting concerns |
| AUTH_SERVICE_CONTRACT.md | AUTHORITATIVE | Auth component (complete: 8 endpoints, all flows, schema) |
| ALIGNMENT_BLUEPRINT.md | AUTHORITATIVE | All conflict resolutions, gap analysis, migration plan |
| HEARTBEAT_SERVICE_CONTRACT_PART4.md | REFERENCE | Superseded by AUTH_SERVICE_CONTRACT.md for auth; still valid for tenancy/license |
| HEARTBEAT_LIFECYCLE_SPEC.md | AUTHORITATIVE | Keep Alive / Process Manager |
| HEARTBEAT_BACKUP_SPEC.md | AUTHORITATIVE | Backup strategy |
| HEARTBEAT_UPDATE_SPEC.md | AUTHORITATIVE | Software update mechanism |
| HEL-AUTH-001 | REFERENCE | Entra/MSAL integration (future) |
| HEL-SDK-001 | REFERENCE (PARTIALLY STALE) | sync.db security — TOTP section replaced by SSE cipher text |
| HEL-INFRA-001 | REFERENCE | Data security, secrets management, key rotation |
| HEL-FLOAT-001 | REFERENCE | DataBox, PIN, Caller Context Bus |

### Per-component contracts:

| Document | Component | Status |
|---|---|---|
| AUTH_SERVICE_CONTRACT.md | Auth | WRITTEN — 15 sections, 8 endpoints, complete flow diagrams |
| BLOB_SERVICE_CONTRACT.md | Blob Service | HIGH — consolidates Part 1 blob sections + Part 2 upload flow |
| AUDIT_SERVICE_CONTRACT.md | Audit | MEDIUM — consolidates Part 3 audit sections |
| REGISTRY_SERVICE_CONTRACT.md | Service Registry | MEDIUM — consolidates Part 1 registry sections |
| PLATFORM_SERVICES_CONTRACT.md | Platform Services | MEDIUM — license, notifications, metrics, config |
| KEEP_ALIVE_CONTRACT.md | Keep Alive | LOW — HEARTBEAT_LIFECYCLE_SPEC.md already covers most of this |

### Documents to archive:

| Document | Reason |
|---|---|
| HEARTBEAT_OVERVIEW.md | Superseded by this document |
| HEARTBEAT_SERVICE_CONTRACT_PART1.md | Will be split into Auth, Blob, and Registry component contracts |
| HEARTBEAT_SERVICE_CONTRACT_PART2.md | Will be merged into Blob Service and SDK integration contracts |
| HEARTBEAT_SERVICE_CONTRACT_PART3.md | Will be split into Audit and Platform Services contracts |
| PHASE_2_DEMO_PLAN.md | Phase 2 complete, demo plan is historical |
| SDK_TEAM_RESPONSE.md | One-time response, historical context only |
| FLOAT_SDK_AUTH_INTEGRATION.md | Superseded by Part 4 + this overview |
| AUTH_IMPLEMENTATION_NOTES.md | Merged into Part 4, implementation-specific notes are stale |

**Do not delete archived documents.** Move them to `Documentation/archive/` with a header noting the superseding document.

---

## 12. Key Decisions (March 2026 Alignment Session)

| # | Decision | Rationale |
|---|---|---|
| 1 | Local auth is current; Entra/MSAL is future-tier | Entra requires client IT involvement. Local auth works for all tiers now. |
| 2 | TOTP replaced by SSE cipher text (~9 min) | Push model is simpler — no REST polling. SSE stream already exists. |
| 3 | Ed25519 (EdDSA) is the canonical JWT algorithm | Faster, smaller, modern. SDK AuthProvider updated to match. |
| 4 | One PostgreSQL instance, multiple schemas | One backup target, one credential set, cross-schema queries, upgrade path to split. |
| 5 | SSE is a shared transport layer with permission filtering | One HeartBeat SSE endpoint multiplexes all events. Server-side JWT filter. |
| 6 | PIN is 100% SDK-local | HeartBeat on customer infrastructure — PIN should not be on customer server. |
| 7 | Keep Alive = OS service management | HeartBeat is first to start, manages Core/Relay/Edge lifecycle. |
| 8 | Audit is a distinct component | Own router, own schema, own verification logic. Not just a utility function. |
| 9 | Secrets management deferred | Architecturally correct but not MVP. Services read env vars for now. |
| 10 | Notifications database owned by HeartBeat | All notification types (system, business, admin, approval, cadenced). |
| 11 | 6-component decomposition confirmed | Keep Alive, Auth, Audit, Registry, Blob Service, Platform Services. |
| 12 | Hybrid documentation structure | Overview + per-component contracts. |
| 13 | E2EE: document contract, SDK team implements | Relay decryption built. SDK encryption specified but deferred. |

---

*End of HeartBeat Architecture Overview v2.0*
*Maintained by: Pronalytics Limited — Helium Core Team*
*Last Updated: 2026-03-03*
