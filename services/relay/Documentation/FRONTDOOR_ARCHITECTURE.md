# Frontdoor Architecture (canonical)

**Owner:** Relay
**Status:** DRAFT — pending Bob review
**Date:** 2026-05-10
**Repo:** `helium-multitenant-demo`
**Doc location:** `services/relay/Documentation/FRONTDOOR_ARCHITECTURE.md`
**Supersedes:** the forward-looking §5 in `helium-services-phase3/HeartBeat/Documentation/RELAY_PHASE1_DESIGN_ALIGNMENT_2026_05_09.md` (HB-side stub) and the §5.4 Phase 2+ pointer in `RELAY_ARCH_HANDOFF_CSSV1_2026_05_10.md`. Those references will redirect here once this lands on master.

> **What this doc is.** The single canonical description of the **Frontdoor** — the always-on wing of a tenant Helium deployment. Covers what it is, why `relay.db` is intentionally SQLite, the two ingress paths (HTTP write-first and AMQP-direct), the convergence pipeline both share, the proposed `relay.db` schema, the security stack per path, mTLS / cert-rotation expectations, phasing, and deployment shapes.
>
> **What this doc is not.** A code spec. No producer/consumer code, no migrations, no AMQP wire-format reference. Those land as separate Relay-side chips in Phase 2.

---

## §1. What the Frontdoor is

The Frontdoor is the **always-on wing of a tenant Helium deployment**. It is the trinity:

| Component | Role | Footprint |
|---|---|---|
| **Relay container** | HTTP + AMQP ingress, validation, dispatch into the convergence pipeline | One container, stateless across requests except for in-flight transactions |
| **`relay.db`** | Local SQLite file. Per-tenant. Holds the durable idempotency / audit ledger of every message that crossed the Frontdoor. | One file per tenant container. Tens of MB at steady state; bounded by retention sweep. |
| **`relay_queue`** | RabbitMQ work queue (declared in the all-PG decision log, 2026-04-28) on the tenant's broker, per-tenant vhost. | One vhost + one queue + one DLQ per tenant. |

Together: "the Frontdoor."

The mental model: even when the rest of Helium is asleep — the processing core (Core, Edge, the per-tenant HeartBeat container, PG cluster) suspended for cost or maintenance — the Frontdoor stays up. ERP integrations keep landing messages. The durable record (`relay.db` row + queued message) preserves everything. The processing core wakes on demand and drains the queue. The ERP never knows the rest of the stack was asleep.

### §1.1 Sequence sketch — Frontdoor with sleeping core

```
T0  ERP                       (always sending)
T1  ─────────► Relay HTTP/AMQP   (always up)
T2                ├─ verify auth
T3                ├─ idempotency check (relay.db read)
T4                ├─ enqueue (relay_queue write)
T5                └─ 202 Accepted ─► ERP                          (~100ms p99)
                                                        ──── ERP turn ends ────

         (processing core wakes — scheduler, ops trigger,
          or the queue depth crossing a wakeup threshold)

T100  Relay consumer ─► drain relay_queue
T101                  ├─ run convergence pipeline (§5)
T102                  ├─ HB D2 /api/blobs/register (multipart)
T103                  ├─ Core E1 enqueue (core_queue.process)
T104                  ├─ relay.db row → processed_at = now
T105                  └─ ACK relay_queue message

         (eventual: Core processes async, SSE updates Float/Reader)
```

The Frontdoor's contract with the ERP is: **"You called us, we durably accepted it, you're done. We will process it; if we lose track, we will not lose your message."**

The processing core's contract with the Frontdoor is: **"We will drain `relay_queue` when we're up. We are idempotent on `message_id`. You can re-enqueue if you crashed mid-handoff."**

### §1.2 What the Frontdoor is *not*

- **Not a multi-tenant Helium-cloud Frontdoor with on-prem option.** The deployment model is locked customer-owned (§11). The "Frontdoor" branding refers to a *role* a Relay+queue+db trinity plays inside a tenant deployment, not a hosted product.
- **Not the same thing as `core_queue`.** `core_queue` is Core's work queue — Core consumes it. `relay_queue` is Relay's work queue — Relay consumes it. Both queues live on the same RabbitMQ broker (per-tenant vhost), but the producer/consumer split is different per L9.
- **Not stateful processing.** The Frontdoor only holds bytes long enough to durably record + dispatch. It does not run the FIRS pipeline, IRN/QR generation, or extraction. Those are the processing core's jobs.

---

## §2. The all-PG mantra exception (the lock)

Helium has an internal mantra: **"Core services are all-PG."** It was the locked decision of 2026-04-28 (`ALL_PG_PER_TENANT_DESIGN.md` §14 "All-PG, no SQLite"). The reason: dialect drift across services hurt portability, and PG gives schema-aware tooling, MVCC, and a single dump-format for tenant exports.

**The Frontdoor is the single intentional exception.** `relay.db` is **SQLite** — and stays SQLite.

### §2.1 Why SQLite for `relay.db`

| Property | Why it matters on the always-on perimeter |
|---|---|
| **Single file, zero ops** | No daemon, no port, no auth, no users, no roles. The file IS the database. |
| **No external dependency for Frontdoor uptime** | Relay does not need PG to be up to accept and durably record an inbound message. If pgbouncer or the tenant's HB-DB connection blips, the Frontdoor still ACKs the ERP. The processing core catches up later. |
| **Per-tenant isolation by file path** | One `relay.db` per tenant container, on a tenant-owned volume. No "current tenant" context, no cross-tenant connection pool, no leak surface. |
| **Crash-consistent by design** | SQLite WAL + fsync gives durable single-row writes within Relay's request budget. No WAL-on-network-storage gotchas because the file is local. |
| **Tiny operational surface** | Backup is `cp relay.db relay.db.bak`. Restore is the inverse. Fits the customer-owned, data-sovereign deployment posture. |

### §2.2 Why not PG for this

PG-on-the-Frontdoor would couple Frontdoor uptime to a second daemon, force connection-pool sizing decisions on a path that mostly does point-lookups + appends, and add a network hop on every idempotency check (~ms vs ~µs). Worth it for the processing core's relational state; **wrong** for a stateless ingress with a single-table append-mostly access pattern.

### §2.3 The lock — instruction to future PRs

> **Do not migrate `relay.db` to PostgreSQL.** This is by design, not technical debt. A future PR proposing to "fix" `relay.db` by aligning it with the all-PG mantra is rejecting the wrong tradeoff.
>
> If a future workload genuinely outgrows SQLite (e.g., write rate > tens of thousands per second per tenant on a single Relay container — far beyond any realistic ERP integration), the answer is to **shard `relay.db` per producer queue** or **front it with a small in-Relay cache**, not to migrate to PG.
>
> The all-PG mantra is for the **processing core** (Core, HB, Edge, blob, registry, audit). The Frontdoor is exempt.

This lock is referenced in:
- `ALL_PG_PER_TENANT_DESIGN.md` §3 (relay_queue listed under RabbitMQ; relay.db not listed under PG schemas — by design).
- This doc, §6 (schema proposal).
- (Future) `services/relay/src/db/README.md` once the schema lands as code.

---

## §3. Path A — HTTP write-first (Phase 2 primary)

Path A is the **primary Phase 2 ingress**: ERP integrations call Relay over HTTPS with the §3.3 ERP HMAC headers, Relay durably enqueues, and ACKs. The processing pipeline runs asynchronously off the queue.

### §3.1 The wire

```
ERP  ──── HTTPS POST /api/ingest ────►  Relay
         X-API-Key: <tenant ERP key>
         X-Timestamp: <ISO 8601 UTC>
         X-Signature: <HMAC-SHA256(secret, "{api_key}:{timestamp}:{sha256(body)}")>
         Body: <invoice payload, JSON or NaCl-encrypted envelope>
```

The combined auth dispatcher in `services/relay/src/api/deps.py::authenticate_request` (shipped in PR #9, hardened in PR #17) branches on header presence:
- HMAC headers (`X-API-Key` + `X-Timestamp` + `X-Signature` all present) → ERP path (this section).
- `Authorization: Bearer <jwt>` → frontend path (out of scope for Frontdoor).
- `Authorization: Bearer <api_key>:<api_secret>` → service-to-service path (out of scope for Frontdoor).

### §3.2 Write-first ordering (the "write-first" lock)

Once HMAC verification passes, Relay's `/api/ingest` handler runs in this order — **the queue write happens before any pipeline work**:

1. Verify HMAC (§3.3 headers, body-bytes discipline).
2. Verify timestamp window (5 min default per `core/auth.py::TIMESTAMP_WINDOW_S`).
3. Resolve tenant from API key registry.
4. (Optional, if `X-Encrypted: true`) decrypt NaCl envelope.
5. Read `message_id` from the request body. *Reject 400 if missing — this is the durable idempotency key.*
6. **Write the message envelope to `relay_queue`** (publish + publisher confirms).
7. **Insert a `relay.db` row** with `received_at = now`, `processed_at = NULL`, `result = NULL`. (Order with step 6 is "queue write commits, then DB insert" — see §3.4.)
8. Return **202 Accepted** to the ERP with a small body containing the `message_id` and `received_at`.

The 202 is the contract to the ERP: *"We have it durably; you do not need to retry."*

The pipeline (§5) runs in a separate consumer task, independent of the request lifecycle.

### §3.3 ERP HMAC headers — current shape

The current shipped contract (`services/relay/src/core/auth.py`, lines 92-111):

| Header | Format | Purpose |
|---|---|---|
| `X-API-Key` | Opaque tenant ERP key (e.g., `erp_abbey_001`) | Identifies the ERP integration; resolves tenant + secret |
| `X-Timestamp` | ISO 8601 UTC, `Z` suffix (e.g., `2026-01-31T10:00:00Z`) | 5-minute validity window; constant-time NTP-bound clock check |
| `X-Signature` | Hex-encoded HMAC-SHA256 over `"{api_key}:{timestamp}:{sha256(body)}"` | Body-bytes discipline; covers the encrypted envelope when `X-Encrypted: true` |

There is **no `X-Nonce` header in the current shipped shape**. Replay protection today relies on:
- The 5-minute timestamp window (limits the replay window).
- ERP-supplied `message_id` checked against `relay.db` (catches replay within and across the window).

Adding `X-Nonce` (with Redis SETNX dedup against the nonce, separate from `message_id`) is a known hardening — flagged as Q-Nonce in §12.

### §3.4 Queue-write-then-DB-row ordering

Two-step durable write:

```
1. AMQP publish to relay_queue with publisher confirms.
   On failure: 503 to ERP, no DB row written, ERP retries.

2. INSERT INTO relay.db with received_at = now.
   On failure: log + alarm, but do NOT 503 the ERP.
   The queue message is durable; the DB row is for idempotency
   on REDELIVERY. A missing DB row means the consumer will
   re-process the message — which is fine, the consumer also
   checks for duplicate downstream effects via Core/HB
   idempotency keys.
```

Why this order? The queue is the system-of-record for "did the message land?" The DB is the system-of-record for "did we already finish processing it?" If we write the DB first and the queue write fails, we have a phantom row pointing at no message. If we write the queue first and the DB write fails, we have a queued message that gets re-processed — already idempotent.

### §3.5 Sequence diagram — Path A end to end

```
   ERP                    Relay (HTTP)         relay_queue       relay.db        Pipeline (§5)
    │                          │                   │                │                  │
    │ POST /api/ingest         │                   │                │                  │
    │─────────────────────────►│                   │                │                  │
    │                          │ auth (HMAC)       │                │                  │
    │                          │───┐               │                │                  │
    │                          │◄──┘               │                │                  │
    │                          │ publish (msg_id)  │                │                  │
    │                          │──────────────────►│                │                  │
    │                          │ publisher-confirm │                │                  │
    │                          │◄──────────────────│                │                  │
    │                          │ INSERT received   │                │                  │
    │                          │───────────────────────────────────►│                  │
    │ 202 Accepted             │                   │                │                  │
    │◄─────────────────────────│                   │                │                  │
    │                          │                   │ deliver        │                  │
    │                          │                   │───────────────────────────────────►│
    │                          │                   │                │ run §5 pipeline  │
    │                          │                   │                │◄─────────────────│
    │                          │                   │                │ UPDATE processed │
    │                          │                   │                │◄─────────────────│
    │                          │                   │ ACK            │                  │
    │                          │                   │◄───────────────────────────────────│
```

### §3.6 What ships, what's deferred

| Item | Phase 2 (primary) | Notes |
|---|---|---|
| HTTP `/api/ingest` with HMAC auth | ✅ ALREADY SHIPPED | Live on EC2; auth dispatcher per `deps.py::authenticate_request` |
| Write-first to `relay_queue` | NOT YET BUILT | Phase 2 chip. Today's `/api/ingest` is synchronous all the way through (§10). |
| `relay.db` schema + writer | NOT YET BUILT | Phase 2 chip. Schema proposed in §6. |
| `relay_queue` consumer | NOT YET BUILT | Phase 2 chip. Drains queue, runs §5 pipeline, ACKs. |
| Combined auth dispatcher | ✅ ALREADY SHIPPED | PR #9; hardened in PR #17. |

---

## §4. Path B — AMQP direct (Phase 2+ future)

Path B builds after Path A is stable. It serves ERPs that cannot or do not want to make HTTP calls — typically because they already have a local message broker pattern (SAP IDoc, Oracle Advanced Queuing, custom AMQP fleets) and would rather publish to a queue than poll an HTTP endpoint.

Two sub-patterns are supported under Path B; both terminate at the same convergence pipeline (§5).

### §4.1 Direct producer (default)

The customer's ERP opens TCP+AMQP to the tenant's RabbitMQ broker, authenticates with mTLS using a Helium-issued tenant client cert (§9), and publishes to `relay_queue` on the tenant's vhost.

```
ERP ──── TCP+AMQP+mTLS ────► RabbitMQ (per-tenant vhost) ────► Relay consumer
        client cert                relay_queue
        (Helium-issued)
```

- **Why this is the default.** No broker required on the customer side. ERP just needs an AMQP client library + the cert. Operational simplicity.
- **Where it fits.** ERPs running in the same data center / VPC as the Helium deployment, or where the customer is comfortable with their ERP holding an outbound AMQP connection.
- **Where it does not.** ERPs that have hard "no outbound long-lived connections" rules or that need messages durable on the customer side **before** they leave the ERP's control. Those want federation (§4.2).

### §4.2 Federation

The customer runs their own RabbitMQ broker. A federation link (RabbitMQ's built-in `federation-upstream`) bridges messages from `customer-erp.queue` on their broker to `relay_queue` on Helium's tenant vhost.

```
ERP ──── pub ────► Customer RabbitMQ ──── federation ────► Helium RabbitMQ ──── Relay consumer
                   (customer-owned)                          (tenant vhost)        relay_queue
```

- **Why this exists.** ERP needs messages durable locally before forwarding (regulatory, audit, "must survive WAN partition" requirements).
- **Cost.** Two brokers to operate. Federation link health monitoring. Two sets of mTLS credentials. Worth it only when local durability matters.
- **Built when.** A real customer asks for it. Not speculatively.

### §4.3 What's the same regardless of sub-pattern

Both §4.1 and §4.2 deliver messages to `relay_queue` on the tenant's vhost. Once in the queue, the same Relay consumer drains them, the same convergence pipeline (§5) runs, the same `relay.db` records the outcome. **The only difference is the network/broker topology in front of the queue.**

---

## §5. Convergence layer — both paths share this

After a message lands in `relay_queue` (whether published by Relay's HTTP handler in Path A or by the ERP directly in Path B), exactly one Relay consumer pipeline drains and processes it. Five steps, in order:

1. **`relay.db` idempotency check.**
   `SELECT processed_at, result FROM relay_messages WHERE message_id = ?`
   - If row exists with `processed_at IS NOT NULL` and `result = 'processed'`: ACK the queue message, log `idempotent_skip`, return. *No downstream calls.*
   - If row exists with `processed_at IS NOT NULL` and `result = 'dlq'`: ACK, log `dlq_replay_blocked`, alarm. *Operator must explicitly un-DLQ.*
   - If no row exists, or `processed_at IS NULL`: continue to step 2.

2. **Redis daily-quota counter.**
   Per-tenant `INCR relay:daily_quota:<tenant_id>:<YYYY-MM-DD>` with TTL = 24h, compared against the tenant's tier limit. Same Redis as the rest of Relay (per `RELAY_PHASE1_DESIGN_ALIGNMENT §4.4` three-tier degrade pattern: Redis → HB `/api/daily_usage/check` → allow-all degraded).
   - On exceedance: write `relay.db` row with `result = 'error'`, `error_message = 'daily_quota_exceeded'`, ACK the queue (no replay value), emit a tenant-visible audit event.

3. **HB D2 multipart `POST /api/blobs/register`.**
   The byte path. Multipart body with `file` part(s) + `metadata` JSON part. HMAC s2s headers per `HMAC_S2S_MIGRATION_SPEC §1.4` (body-bytes discipline applies to the multipart body). On success, HB returns the `blob_uuid` + acknowledges the byte commit.
   - On 401 `BEARER_S2S_REMOVED`: alarm + DLQ. Cert/secret rotation issue, not a tenant problem.
   - On 413 / 422: `result = 'error'`, mark in `relay.db`, ACK. ERP-visible problem.
   - On 5xx: NACK + requeue with backoff, up to N retries (§5.5).

4. **Core E1 enqueue (`core_queue.process`).**
   AMQP publish to `core_queue.process` with the byte path's idempotency_key (per L9 split: byte-path producers are HB-outbox in CSSV1; Path A/B writes from Relay only ever go through HB D2, which writes the outbox row). Relay does NOT write `core_queue.process` directly — that's HB's job after the byte commit.
   - **Note:** the state-only Core routing keys (`core_queue.{approve,reject,reset,withdraw,finalize-no-edits}`) are Relay's job per L9, but they don't live on the Frontdoor ingress path — they're triggered by frontend user actions, not ERP messages. The Frontdoor is byte-path only.

5. **Success → write `relay.db`, then ACK source.**
   `UPDATE relay_messages SET processed_at = now, result = 'processed' WHERE message_id = ?`
   Then ACK the `relay_queue` message. Order is "DB update → AMQP ACK" so a crash between them is replayable: the next consumer sees `processed_at IS NOT NULL` in step 1 and short-circuits.

### §5.1 Idempotency boundary

The convergence pipeline is **idempotent on `message_id`**. If the same `message_id` arrives twice (ERP retry, broker redelivery, federation duplicate), exactly one pipeline run completes; subsequent runs short-circuit at step 1. This is the contract.

ERP-supplied `message_id` is the source of truth. If the ERP omits or reuses a `message_id`, that is an **ERP integration bug**, not a Frontdoor bug. We document the requirement; we do not paper over it with a Relay-generated fallback (a fallback would mask the bug and allow duplicate processing for the case it's trying to protect against).

### §5.2 What about `idempotency_key`s already in flight downstream?

HB and Core have their own idempotency keys for the `core_queue.*` routing keys (per L9 + `RELAY_PHASE1_DESIGN_ALIGNMENT §6`). Those are independent of `message_id`:
- `message_id` = ERP-supplied, scoped to the Frontdoor, drives `relay.db` short-circuit.
- HB outbox `idempotency_key` = HB-generated per outbox row, scoped to `core_queue.*` routing.
- Core's `QueueScanner` keys = scoped to Core's recovery loop.

Three keys, three layers, **do not merge**. The Frontdoor only owns the first.

### §5.3 DLQ — what it means and where it lives

After N retry failures (§5.5) on the same `message_id`, the message is moved to `relay_queue.dlq` (RabbitMQ DLX) **and** the `relay.db` row is updated to `result = 'dlq'`. The message stays on the DLQ until an operator explicitly inspects + drains.

DLQ semantics:
- DLQ is a human-review queue, not a retry queue. A message landing in DLQ means automation has given up on it.
- Operator un-DLQ flow: inspect message → determine fix (data, config, tenant communication) → either delete (`result` stays `dlq` permanently, audit trail preserved) or republish (Relay observes the same `message_id`, sees `result = 'dlq'` in step 1, refuses to re-process; operator must clear the row first via a documented `relay-cli unblock-message --id <message_id>`).

Open question: should DLQ entries also live in `relay.db` (as proposed) or only in RabbitMQ's DLX? Surfaced as Q-DLQ in §12.

### §5.4 Source ACK

The "ACK source" step depends on path:
- **Path A:** the ERP already received its 202 Accepted at step 8 of §3.2. There is no further ACK to send — the ERP's contract was already satisfied at queue-write time. The "ACK source" here is the AMQP ACK to `relay_queue` itself, which closes the consumer's hold on the message.
- **Path B:** the AMQP ACK to `relay_queue` is the source ACK. The ERP's broker (or federation link) sees the ACK and considers the message delivered.

### §5.5 Retry policy (Phase 2 default)

| Failure source | Retry behavior | Cap |
|---|---|---|
| Step 3 HB D2 5xx / network | Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s | 6 attempts (~1 min total) |
| Step 3 HB D2 4xx (not 401) | No retry; mark `result = 'error'` and ACK | n/a |
| Step 3 HB D2 401 `BEARER_S2S_REMOVED` | No retry; DLQ + ops alarm | n/a |
| Step 4 AMQP publish failure | Retry the AMQP publish 3x with 100ms backoff | 3 attempts |
| Step 5 SQLite write failure | Retry 3x with 50ms backoff; if still failing, NACK + requeue | 3 attempts |

After all retries are exhausted on a step, the message goes to DLQ with the failure reason recorded.

---

## §6. `relay.db` schema (proposed — Bob to approve)

This section proposes the `relay.db` schema. Bob's approval here unlocks the Phase 2 implementation chip.

### §6.1 Schema

```sql
-- relay.db — SQLite, one file per tenant container.
-- Created at Relay container startup if missing; owned by the Relay process.
-- WAL mode required (see §6.3); foreign-keys ON for safety.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;       -- WAL + NORMAL is the recommended durability/perf tradeoff
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;        -- 5s — covers the worst-case checkpoint pause

CREATE TABLE IF NOT EXISTS relay_messages (
    -- Primary identity
    message_id          TEXT NOT NULL,
    tenant_id           TEXT NOT NULL,

    -- Receive metadata
    received_at         TEXT NOT NULL,                  -- ISO 8601 UTC
    path                TEXT NOT NULL CHECK (path IN ('http', 'amqp')),
    source_id           TEXT,                           -- ERP integration identifier (e.g., "sap-prod-01")

    -- Processing outcome
    processed_at        TEXT,                           -- ISO 8601 UTC; NULL until pipeline completes
    result              TEXT CHECK (result IN ('processed', 'dlq', 'error')),
    error_message       TEXT,                           -- short, human-readable; full trace in audit log

    -- Retry / DLQ tracking
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_attempt_at     TEXT,                           -- ISO 8601 UTC

    -- Tamper-evidence (optional enrichment, see rationale)
    envelope_sha256     TEXT,                           -- hex SHA-256 of the canonical received body

    PRIMARY KEY (tenant_id, message_id)                 -- compound: see rationale
);

-- Retention sweep + per-tenant scans
CREATE INDEX IF NOT EXISTS idx_relay_messages_tenant_received
    ON relay_messages (tenant_id, received_at);

-- DLQ inspection
CREATE INDEX IF NOT EXISTS idx_relay_messages_dlq
    ON relay_messages (tenant_id, result, last_attempt_at)
    WHERE result = 'dlq';

-- Operational: find unprocessed (in-flight or stuck) messages
CREATE INDEX IF NOT EXISTS idx_relay_messages_inflight
    ON relay_messages (tenant_id, received_at)
    WHERE processed_at IS NULL;
```

### §6.2 Schema rationale

This subsection shows my work — why each column is shaped the way it is and what alternatives I considered.

**Why `(tenant_id, message_id)` as a compound PRIMARY KEY (not `message_id` alone):**

The brief listed `message_id TEXT, PRIMARY KEY` (globally unique). I'm proposing **compound PK** instead. Rationale:
- **Deployment reality.** On managed EC2 today, multiple tenants share a Relay binary (per `services/relay/src/api/deps.py::_tenant_id_for_api_key`). One `relay.db` file might hold messages for several tenants in the demo / pre-prod shape. A globally-unique `message_id` constraint would make ERP A and ERP B colliding on the same `message_id` value (totally legal — they are different ERPs, different organizations) into a hard error. With a compound PK, tenant_id segments the namespace.
- **Per-tenant container model (post-Phase 4).** Even after the per-tenant container split (`ALL_PG_PER_TENANT_DESIGN.md` §3 — one HeartBeat container per tenant on managed EC2), the Frontdoor's `relay.db` file is still tenant-scoped. The compound PK is **redundant in that shape but not harmful** — the tenant_id is constant within the file. Cost: a few bytes per row + one extra index column.
- **Cross-tenant isolation hygiene.** Even if we are confident `relay.db` is tenant-scoped today, every query in the Relay code must include `WHERE tenant_id = ?`. The compound PK forces that discipline at the DDL level. CLAUDE.md `helium-services-phase3/CLAUDE.md` "Tenant Isolation — Default Deny" rule applies to Relay's local store too.

Surfaced as Q-PK in §12: do we want compound PK (proposed), or does Bob prefer global-unique `message_id` with tenant_id as a NOT NULL non-key column?

**Why `path` (`'http' | 'amqp'`):**
- Operational diagnostics. "Is this tenant getting 100% of their traffic via Path A or Path B?" trivially answerable.
- Future tier-pricing or quota differentiation by ingress path stays cheap.
- A simple CHECK constraint catches drift if a third path ever sneaks in without a schema delta.

**Why `source_id` (nullable):**
- ERP fleets are not always one-key-per-ERP. A single tenant might have `sap-prod-01` and `sap-prod-02` both publishing under the same API key (load balancing on the ERP side). Capturing `source_id` from the `X-Source-Id` request header (already plumbed in `deps.py::_verify_hmac`) lets ops slice traffic by individual ERP instance.
- Nullable because Path B (federation, direct AMQP) may not carry a `source_id`. We capture it when present.

**Why `received_at` and `processed_at` are TEXT (ISO 8601), not REAL/INTEGER:**
- SQLite has no native datetime type. The two real options are TEXT (ISO 8601) or INTEGER (unix epoch).
- TEXT wins for human inspection (`sqlite3 relay.db "SELECT * FROM relay_messages LIMIT 1"` is readable) and matches the format Helium uses everywhere else (`HMAC_S2S_MIGRATION_SPEC` timestamps, `audit.audit_events` timestamps).
- Cost: `STRFTIME` or app-side parsing for date math. Acceptable — date math on `relay.db` is a retention sweep job, not a hot path.

**Why `result` is a CHECK constraint, not a separate ENUM table:**
- Three possible values, schema-stable. A CHECK is the simplest correct option. ENUM-as-table would be ceremony for no benefit.

**Why `error_message` is short (kept under ~256 chars in practice):**
- The full failure trace lives in the audit log (`POST /api/audit/log` to HB D6). `relay.db` is for "did this message succeed or fail, and roughly why," not for forensic debugging. Long error messages would also bloat the per-tenant DB file in the steady state.

**Why `attempt_count` and `last_attempt_at`:**
- Drives the retry policy in §5.5 (cap at N attempts before DLQ).
- `last_attempt_at` lets the DLQ queue inspector tell "this just failed for the first time 30s ago, give it the chance to retry" from "this has been stuck for 6 hours, escalate."

**Why `envelope_sha256` (optional):**
- Tamper-evidence. If a future PR wants to detect message corruption between ERP's signed envelope and Relay's queue write (broker bug, on-disk corruption, malicious mutation), the hash is precomputed on receive.
- NULL-ok because Phase 2 might not implement this. Schema room for a Phase 3 hardening without a migration.
- Surfaced as Q-Tamper in §12: should we mandate this from day one or defer?

**Why the partial indexes (`WHERE result = 'dlq'`, `WHERE processed_at IS NULL`):**
- DLQ inspection and "find stuck in-flight" are both small subsets of the total row count in steady state. Partial indexes keep the operational queries fast without bloating the index footprint.
- SQLite supports partial indexes since 3.8.0; no compatibility concern.

**Indexes I considered and rejected:**
- `INDEX (tenant_id, processed_at)` — covered by the partial inflight index; full covering would duplicate.
- `INDEX (tenant_id, source_id)` — diagnostic queries are rare enough that a full table scan within a tenant-scoped sweep is fine. Add if real ops need emerges.
- `UNIQUE INDEX (envelope_sha256)` — would catch duplicate bodies under different `message_id`s, but that's a different problem (ERP misconfig) and one I don't want to fail-closed on without Bob's explicit call.

### §6.3 Operational notes

- **WAL mode is mandatory.** The default rollback journal serializes readers and writers; WAL allows readers (e.g., a status-query helper) concurrent with the consumer's writes. Surfaced as Q-WAL in §12 only because it's worth Bob explicitly approving rather than it being a silent assumption — the answer is "yes, WAL," but I want it on the record.
- **`PRAGMA synchronous = NORMAL`** is the correct durability tradeoff for WAL mode. `FULL` doubles fsyncs for marginal real-world durability gain on consumer-class hardware. `OFF` risks losing the last few writes on a power loss — unacceptable for the durability claim we make to ERPs.
- **`busy_timeout = 5000`** covers the worst-case WAL checkpoint pause. Tested empirically; not a guess.
- **Backups.** SQLite backups should use the SQLite Online Backup API (`sqlite3.Connection.backup()`), not a raw file copy — a copy mid-write can be corrupt. Tooling for this lives outside this doc but should be referenced in §11 deployment shapes.
- **Retention.** `(tenant_id, received_at)` index supports a daily sweep that deletes rows where `processed_at < now - 30 days AND result = 'processed'`. DLQ rows are kept indefinitely (for audit). 30 days is the floor — see §7.

---

## §7. Idempotency separation (the other lock)

There are two idempotency stores in play, and they solve different problems. **They must not be merged.**

### §7.1 `relay.db` — long retention, audit shape

- **Keyed on:** ERP-supplied `message_id`.
- **Retention:** ≥ 30 days for `result = 'processed'`; indefinite for `result = 'dlq'` and `result = 'error'`.
- **Shape:** append-mostly. Single row per `message_id`. Row is updated once on completion (write `processed_at` + `result`).
- **Purpose:** durable idempotency on a business-domain key. Survives Relay restarts, container redeploys, queue redeliveries.
- **Where:** local SQLite file (per tenant), this doc § 6.

### §7.2 HTTP `Idempotency-Key` middleware (separate, future Relay chip)

- **Keyed on:** caller-supplied `Idempotency-Key` HTTP header (RFC 9110 / IETF idempotency-key draft style).
- **Retention:** ~5 minutes.
- **Shape:** in-memory or PG-backed; high-churn cache.
- **Purpose:** protect HTTP handlers from accidental retry storms (network blip → client retries → handler runs twice). This is a **client-network-glitch** concern, not a business-domain concern.
- **Where:** Relay HTTP middleware, separate chip from Frontdoor. Not in this doc's scope. *One-line pointer only, per the brief.*

### §7.3 Why these must not merge

- **Retention.** 5 min vs 30+ days. Single-store implementations either burn memory holding business data for a month or lose business data when the cache evicts after 5 min.
- **Granularity.** `message_id` is per-business-event. `Idempotency-Key` is per-HTTP-call. One ERP can do many HTTP calls per business event (e.g., chunked upload — a future shape) and conversely, one HTTP call can carry many business events (e.g., batch endpoint).
- **Purpose.** Audit trail vs anti-storm. Different SLAs, different blast radius on bugs.
- **Code path.** `relay.db` lookup is a SQL point-read inside the consumer pipeline. `Idempotency-Key` lookup is a middleware concern that runs before the handler. Mixing them creates ordering ambiguity.

### §7.4 The lock

> **Future PRs must not introduce a "unified idempotency store" or migrate `relay.db` rows into the `Idempotency-Key` cache (or vice versa).** They're different problems. Treat them separately.

---

## §8. Security stack per path

This is the canonical reference table for Frontdoor security layers, indexed by path.

| Layer | Path A (HTTP) | Path B (AMQP) |
|---|---|---|
| **Network auth** | TLS to Relay's HTTPS endpoint (TLS 1.2+; HSTS preferred) | mTLS at the broker — per-tenant client cert (Helium-issued, rooted at Helium CA) |
| **Application auth** | §3.3 ERP HMAC headers (`X-API-Key` + `X-Timestamp` + `X-Signature`); body-bytes discipline | Signed envelope (HMAC over canonical body + timestamp + `message_id`); broker-level vhost ACL is the first gate |
| **Tenant isolation** | tenant resolved from API key registry (`tenants.json` → `Tenant` dataclass → `tenant_id`) | tenant resolved from per-tenant vhost (broker-enforced; tenant cannot publish to another tenant's vhost) |
| **Replay protection** | 5-min timestamp window + ERP-supplied `message_id` checked against `relay.db` (no nonce in current shape — see Q-Nonce §12) | `message_id` in `relay.db` (signed envelope provides the timestamp; same window applies) |
| **Encryption at rest in transit** | NaCl X25519+XSalsa20-Poly1305 via `X-Encrypted: true` (optional; per `core/auth.py` doc) | TLS to broker; payload-level encryption can be layered if tenant requires |
| **Quota / rate limit** | Same Redis daily-counter (per-tenant key, per-day TTL); fallback to HB `/api/daily_usage/check` | Same Redis daily-counter; same fallback |
| **DLQ** | After N retries on the same `message_id`, move to `relay_queue.dlq` for human review | Same |
| **Audit emission** | Every accepted message → `POST /api/audit/log` to HB D6 (HMAC s2s) | Same |
| **Malware scan (optional)** | ClamAV via `RELAY_MALWARE_*` config when enabled; `MALWARE_ON_UNAVAILABLE=allow|block` policy | Same — runs in the consumer, not the producer side |
| **Cross-tenant default deny** | Every `SELECT` from `relay.db` filters on `tenant_id`; CHECK constraint at the data layer (see §6) | Same |

The two paths converge at the same security backstop: `relay.db` idempotency + Redis quota + HB audit. **Differences are in the front half (network + auth); the back half (durability + audit) is identical.**

---

## §9. mTLS / cert rotation (Path B operational concern)

Path B requires mTLS at the broker. mTLS introduces an operational concern Helium owns, not the customer.

### §9.1 Cert issuance

- **Helium runs a CA** (the "Helium CA") — root cert + intermediate(s). Operationally lives wherever the Helium signing infrastructure does (currently AWS KMS af-south-1, ECDSA-P256, per the Phase 3 license-signing infra).
- **Per-tenant client certs** are issued by Helium for each tenant ERP integration. The cert's CN/SAN encodes the tenant slug + integration ID (e.g., `CN=erp.abbey.helium.io, SAN=erp-abbey-001`).
- **Broker trust chain.** The tenant's RabbitMQ broker is configured with the Helium CA as a trust anchor. Any cert signed by Helium CA presenting at the broker passes the chain check; vhost ACL further constrains which tenant the cert is allowed to publish under.

### §9.2 Cert rotation cadence

- **Default:** 90 days. Configurable per tenant (some industries demand 30; some are happy with 365).
- **Two flows:**
  - **Tenant-initiated rolling rotation:** tenant requests rotation via the Helium operator interface; Helium issues a new cert; both old and new certs are valid during a configurable overlap window (default 7 days); tenant cuts ERP over to new cert; old cert is revoked at end of overlap.
  - **Helium-initiated revoke + reissue:** for cert compromise events. Old cert is added to the CRL immediately; new cert is issued; tenant has a defined SLA to swap (default 24h). During the SLA window, the broker-side enforcement is configurable: hard-fail (mTLS rejected immediately) or grace-period (logs warning, accepts). Default for non-compromise is grace-period; for compromise is hard-fail.

### §9.3 What Helium provides vs what the customer does

This is **infrastructure capability Helium provides** — not "deployment plumbing the customer figures out." Specifically:

| Item | Helium provides | Customer does |
|---|---|---|
| CA (root + intermediate) | Yes | — |
| Per-tenant client cert issuance | Yes (operator-driven) | — |
| Cert rotation tooling (CLI, operator UI) | Yes | Triggers rotation |
| Cert distribution to ERP | Helium delivers via the operator console | Customer installs into ERP's AMQP client config |
| Cert revocation (CRL / OCSP) | Yes | — |
| Broker-side trust config | Yes (in the deployed broker) | — |
| Monitoring of cert expiry | Yes (proactive alarm 30d before expiry) | Acts on the alarm |

### §9.4 Build expectation

The Path B mTLS / cert capability is a **deliverable in the same sprint that ships Path B**. Not a "we'll figure out cert rotation later" tail. If Path B ships without rotation tooling, the first tenant cert reaches expiry in 90 days and Path B silently breaks.

Surfaced as Q-CertRotation in §12: where does the cert-rotation operator UI live — Float Admin tab, a separate `helium-installer` subcommand, or both?

---

## §10. Phasing

| Phase | What ships | What does not | Status |
|---|---|---|---|
| **Phase 1 (current — CSSV1 cycle)** | Today's `/api/ingest` synchronous handler. ERP HMAC auth (§3.3). Synchronous HB D2 + Core E1 calls. **No queue. No `relay.db`.** | Path A write-first, Path B, `relay.db`, `relay_queue` consumer | Live on EC2; demo + early-customer scope |
| **Phase 2 (next)** | Path A — write-first HTTP → `relay_queue` write → 202 Accepted → consumer drains queue → §5 pipeline → `relay.db` records outcome | Path B (AMQP-direct) | Specified in this doc; chips not yet broken out |
| **Phase 2+** | Path B §4.1 direct producer (mTLS + signed envelope). Most ERPs that do not want HTTP. | Path B §4.2 federation (waits for a real customer ask) | Forward-looking; built on demand |
| **Phase 3** | Path B §4.2 federation — only when a real customer asks. | — | Demand-driven |
| **Phase 4+** | Frontdoor split-host deployment (§11.2) — Frontdoor on always-on tier, processing core on suspend-resume tier | Edge appliance shape (built on demand) | Forward-looking |

Phase 1 is acceptable for the current demo / pre-prod scope because traffic volumes are low and the synchronous path is functional. Phase 2 is the **first deployment shape that delivers on the "Frontdoor stays up while the core sleeps" promise** — it's a hard prerequisite for any tenant that wants the cost-saving suspend-resume processing tier.

---

## §11. Deployment shapes

**All shapes are customer-owned, tenant-controlled, data-sovereign.** None are Helium-cloud-hosted as a multi-tenant SaaS.

The "shape" is about how the Frontdoor is physically split from the rest of the stack on the customer's hardware. All three shapes use the same containers and same code; only the placement differs.

### §11.1 Co-located

The default for small tenants. Entire Helium stack on one VM or small cluster:

```
┌─────────────────────────────────────────────────┐
│ Customer VM / cluster                            │
│                                                  │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│   │  Relay   │  │   Core   │  │   Edge   │      │
│   │ (Frontdr)│  │          │  │          │      │
│   └──────────┘  └──────────┘  └──────────┘      │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│   │ HeartBeat│  │ RabbitMQ │  │   PG     │      │
│   │          │  │          │  │ (cluster)│      │
│   └──────────┘  └──────────┘  └──────────┘      │
│                                                  │
│   relay.db: local file on the VM                 │
└─────────────────────────────────────────────────┘
```

- **When to use.** Tenants whose volume fits comfortably on a single VM, no need for compute-cost optimization.
- **Operational story.** One VM to back up, one to monitor, one to update. Simplest possible.
- **Frontdoor uptime story.** Limited — if the VM goes down, the Frontdoor goes with it. Acceptable for many tenants; not for those who need the "stays up while core sleeps" promise.

### §11.2 Split-host

For tenants with bursty workloads where compute cost matters. Frontdoor on always-on tier; processing core on suspend-resume / spot tier.

```
┌──────────────────────────────────┐    ┌────────────────────────────────────────┐
│ Always-on tier (small VM, cheap) │    │ Suspend-resume / spot tier (big VM)    │
│                                   │    │                                         │
│   ┌──────────┐  ┌──────────┐     │    │   ┌──────────┐  ┌──────────┐           │
│   │  Relay   │  │ RabbitMQ │     │    │   │   Core   │  │   Edge   │           │
│   │ (Frontdr)│  │          │     │    │   │          │  │          │           │
│   └──────────┘  └──────────┘     │    │   └──────────┘  └──────────┘           │
│                                   │    │   ┌──────────┐  ┌──────────┐           │
│   relay.db (local)               │    │   │ HeartBeat│  │   PG     │           │
│                                   │    │   │          │  │          │           │
└──────────────────────────────────┘    │   └──────────┘  └──────────┘           │
                                         │                                         │
              ▲                          └────────────────────────────────────────┘
              │
              └── ERP HTTPS / AMQP                       ▲
                                                         │
                                                         (wakes on schedule, queue depth, or ops trigger)
```

- **When to use.** Tenants whose ERP traffic is steady but processing is bursty (end-of-month reconciliation, batch days). Spot/suspend-resume saves significant compute cost without affecting Frontdoor SLA.
- **Operational story.** Two tiers to manage. The always-on tier is small and cheap. The suspend-resume tier is sized for peak processing, runs only when needed.
- **Frontdoor uptime story.** This is what the Frontdoor architecture exists for. ERP integrations land into the Frontdoor 24/7. Processing core wakes when there's work.

The split is at the **broker** — `relay_queue` lives on the always-on RabbitMQ; `core_queue` *can* live on either tier (typically on the always-on tier alongside `relay_queue`, with Core consuming when it wakes).

### §11.3 Edge-appliance

For tenants with strict data-residency rules (must stay on customer premises) or air-gap requirements.

```
┌─────────────────────────────────────────────────┐    ┌──────────────────────────────────┐
│ Customer premises (data-residency boundary)     │    │ Customer DC / cloud account       │
│                                                  │    │                                   │
│   ┌──────────┐  ┌──────────┐                    │    │  ┌──────────┐  ┌──────────┐      │
│   │  Relay   │  │ RabbitMQ │ ← Frontdoor as a   │    │  │   Core   │  │   Edge   │      │
│   │ (Frontdr)│  │          │   single appliance │    │  │          │  │          │      │
│   └──────────┘  └──────────┘                    │    │  └──────────┘  └──────────┘      │
│   relay.db (local)                              │◄───┤  ┌──────────┐  ┌──────────┐      │
│                                                  │    │  │ HeartBeat│  │   PG     │      │
│   ┌─────────────────────────────────────────┐   │    │  │          │  │          │      │
│   │ Tenant-internal network (ERP)           │   │    │  └──────────┘  └──────────┘      │
│   └─────────────────────────────────────────┘   │    │                                   │
└─────────────────────────────────────────────────┘    └──────────────────────────────────┘
```

- **When to use.** Regulated industries where invoice data cannot leave a specific physical site without authorization, or air-gap deployments where the processing core is intentionally network-isolated.
- **Operational story.** The Frontdoor appliance is shipped as a single container or VM image. The customer drops it onto their hardware, points their ERP at it, and authenticates with the Helium-issued cert / API key. The appliance forwards bytes to the processing core over a controlled link (which may be a one-way diode, a scheduled batch transfer, or a normal mTLS-protected link, depending on the regulatory shape).
- **Frontdoor uptime story.** Appliance stays up on customer hardware. Processing core can be in a separate data center, a separate cloud account, or even on the same site behind an internal firewall.

---

## §12. Open questions / future work

Honest list of what I cannot resolve in this session and what Bob's input would change.

### §12.1 Schema-shape questions

- **Q-PK — compound vs global PK.** I proposed `PRIMARY KEY (tenant_id, message_id)` (compound). The brief listed `message_id PRIMARY KEY` (global). Compound is safer when one Relay binary serves multiple tenants (today's pre-prod shape on EC2); global is simpler when `relay.db` is strictly one-file-per-tenant-container (post-Phase 4 shape). **Bob's call.**
- **Q-WAL — `PRAGMA journal_mode = WAL`.** I assumed yes (default rollback journal serializes; WAL gives concurrent readers + the consumer writer). Asking explicitly because it's worth Bob ratifying rather than embedded as a silent default.
- **Q-Tamper — `envelope_sha256` mandatory or optional?** Proposed nullable. Alternative: NOT NULL + `CHECK (length(envelope_sha256) = 64)` from day one. Cost is one SHA-256 hash per receive (negligible). Benefit is that future tamper-detection tooling has data to work with from day one with no migration.
- **Q-DLQ — DLQ in `relay.db` or only in RabbitMQ DLX?** I proposed both (RabbitMQ DLX for the message bytes, `relay.db.result = 'dlq'` for the metadata index). Alternative: only RabbitMQ DLX (and `relay.db` row stays at last attempt + `result IS NULL`). Tradeoff is "DLQ inspection without reading the broker" (proposed) vs "single source of truth for DLQ state" (alternative).
- **Q-Idempotent-Window-Hint — per-message `idempotency_window_seconds` hint?** Some ERPs might want to say "this message is only idempotent for 24h, after that treat it as new." I did not propose adding this column. **Default position:** `relay.db` retention IS the idempotency window. Adding a per-message override is a footgun. But surfacing the question because the brief mentioned it as a possible enrichment.

### §12.2 Auth shape questions

- **Q-Nonce — should `X-Nonce` be a fourth HMAC header?** The current shipped shape is 3 headers (`X-API-Key` + `X-Timestamp` + `X-Signature`). The brief's §8 table referenced 4 headers (adding `X-Nonce`). Adding a nonce + Redis SETNX dedup tightens replay protection within the timestamp window — at the cost of a Redis dependency on every HMAC verify. I would defer this to a hardening chip; documentation should describe what we ship today, not what we plan. **Bob's call** on whether to ship it as part of Phase 2 or defer.
- **Q-AMQP-Envelope-Format — canonical signed envelope shape for Path B.** Phase 2+ work; not blocking. But the convergence pipeline (§5) relies on extracting `message_id` and `tenant_id` from the envelope — the format must be deterministic. Worth a sketch in this doc once Path B work begins.

### §12.3 Operational questions

- **Q-CertRotation — operator UI surface for Path B mTLS rotation.** §9.4. Float Admin tab? `helium-installer` CLI subcommand? Both? My read: both — Float Admin for the day-to-day case, CLI for ops automation. But this affects the Float scope, not just Frontdoor.
- **Q-Retention — 30 days or longer?** §6.2 uses 30 days as the floor for `result = 'processed'` rows. Audit-log retention is typically 7 years (S3 Object Lock Compliance per `ALL_PG_PER_TENANT_DESIGN.md` §2). Question: does `relay.db` need to align with audit-log retention, or is the audit log the long-tail record and `relay.db` is operational?
  - **My read:** `relay.db` is operational; audit log is the canonical long-tail. 30 days is right. But the answer depends on what regulators / customers expect to find in `relay.db` when they ask.
- **Q-Backup — SQLite Online Backup API tooling.** §6.3 mentions it. Where does this tool live? Helium-shipped (in `helium-installer` or similar)? Or a documented `sqlite3` recipe customers run themselves? My read: Helium-shipped, because customers should not be expected to know SQLite operational details on their own.

### §12.4 Boundary questions

- **Q-PathA-Conversion — what triggers the cutover from Phase 1 (synchronous) to Phase 2 (write-first)?** Per-tenant feature flag? Universal at-once cutover? Documented but currently unanswered. My read: per-tenant feature flag, default off, flipped to on per tenant after the consumer's been observed draining cleanly for that tenant for 24h. But this is operational-policy territory, not architecture.
- **Q-FederatedDeployment — Path B §4.2 federation timing.** "Built on demand" is fine, but worth confirming that the contract is "we will build it within X weeks of a real customer asking" not "we will build it in the next sprint." Affects the customer commitment language.

### §12.5 Things the references contradict (worth flagging)

- **PR attribution.** The brief (§3 of this doc's task brief) says the combined dispatcher is "already shipped in PR #17." Reality (from `git log` of `helium-multitenant-demo` on the docs branch): the dispatcher landed in **PR #9** (`2d791ac feat(relay): combined HMAC/JWT/service-creds auth dispatcher + HB introspect`). PR #17 (`fcf3086`) is the *defensive coding* hardening chip on top of the dispatcher. I wrote this doc reflecting the actual git history. Calling out so Bob is not surprised.
- **HMAC header count.** As above (Q-Nonce). The brief says 4 headers; reality is 3.
- **`relay.db` has not yet been built.** Multiple HB-side docs reference "the `relay.db` schema (when it ships)" — the schema in §6 is the proposal; nothing is built yet on the Relay side.

---

## §13. References

| Doc | Location | Why cited |
|---|---|---|
| Original design (Phase 1 alignment) | `helium-services-phase3/HeartBeat/Documentation/RELAY_PHASE1_DESIGN_ALIGNMENT_2026_05_09.md` §5 | The forward-looking dual-path design; this doc supersedes its §5 |
| CSSV1 handoff (Phase 2+ scope) | `helium-services-phase3/HeartBeat/Documentation/RELAY_ARCH_HANDOFF_CSSV1_2026_05_10.md` §5.4 | Pointer to Phase 2+ Frontdoor work |
| All-PG decision log | `helium-services-phase3/HeartBeat/Documentation/ALL_PG_PER_TENANT_DESIGN.md` §3 + §14 | `relay_queue` listed; SQLite exemption is the all-PG mantra exception |
| HMAC s2s spec | `helium-services-phase3/HeartBeat/Documentation/HMAC_S2S_MIGRATION_SPEC.md` | Body-bytes discipline for D2 multipart and ERP HMAC |
| Service call graph | `helium-services-phase3/docs/SERVICE_CALL_GRAPH_2026_05_08.md` | L9 producer/consumer split for `core_queue.*` routing keys |
| Tenant isolation rule | `helium-services-phase3/CLAUDE.md` "Tenant Isolation — Default Deny" | Drives the compound PK proposal in §6.2 |
| Current Relay config | `helium-multitenant-demo/services/relay/src/config.py` | Source of `RELAY_*` env shape |
| Current ERP HMAC validator | `helium-multitenant-demo/services/relay/src/core/auth.py` | 3-header HMAC, 5-min window, body-bytes signature |
| Combined auth dispatcher | `helium-multitenant-demo/services/relay/src/api/deps.py::authenticate_request` | PR #9 / hardened in PR #17 |

---

**End of doc.** Next step: Bob review. Pending Bob's calls on Q-PK, Q-WAL, Q-Tamper, Q-DLQ, Q-Nonce, Q-Retention, this doc lands as the canonical Frontdoor reference and unblocks the Phase 2 Relay-side implementation chips.
