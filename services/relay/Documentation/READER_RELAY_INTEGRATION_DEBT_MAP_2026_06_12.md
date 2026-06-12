# Reader/Scout ↔ Relay Integration Debt Map (SBS Gap Map)

**Seat:** RELAY (Backend Round Robin) · **Authored:** 2026-06-12 · **Chip:** R-M0 (directive §3.5, Monday-readiness)
**Contract baseline:** Scout rev `2026-06-12-a` (directive) — **note:** Scout worktree已 advanced to `2026-06-12-b`
(HEAD `5a44bd4`) during this audit; the `-b` bump is a prose correction with **no Relay-clause changes** (see
end note). All SBS line refs are against the **worktree copy** (origin `02abaf29` is stale/unpushed — read
worktree-direct per directive §1.5).
**Method:** read-only sub-agent audit of `<SCOUT>\reader\App\fresh_reader_app\scout_backend_simulator_{relay,events,core}.py`
+ `CLAUDE.md` §B-\* + `SCOUT_QUEUE_AND_INGEST_FINALIZE_CONTRACT_2026_06_04.md` §3, diffed against
`helium-multitenant-demo\services\relay\src`.
**Feeds:** Q12 (inbound phasing) · the R-M2/R-M3/R-M4 build chips · ARCH open questions (a/b/c, end of doc).

---

## Summary

Relay carries **6 §B obligations** in scope: §B-Submit (3-call taxonomy), §B-IngestFinalize, §B-Drift,
§B-VersionAxes, §B-RelayArtifactFetch, and the `relay.*` slice of §B-EventLog. Against the live tree
(`services/relay/src`), **~0.5 are met**: the only contractual call that exists is **ingest#1/#2
bytes-with-IRN/QR**, and even that is partial — it routes off a `call_type` form field, **does not honor
`metadata.finalize`**, and produces no lifecycle SSE. **Net-new for Monday: 5 obligations** —
(1) `POST /api/finalize` reference-only (#3) + honoring `metadata.finalize` on `/api/ingest`;
(2) version-drift gateway middleware → `409 version_drift` (§B-Drift);
(3) reading the version-axis headers (§B-VersionAxes);
(4) a **POST-body** artifact-fetch route returning bytes-or-JSON keyed by `artifact_ref` (§B-RelayArtifactFetch);
(5) the `relay.*` lifecycle `trace_id` propagation so the confirming SSE echoes it (§B-EventLog).
Mounted routes today are exactly `POST /api/ingest`, `GET /health`, `GET /metrics`,
`POST /api/v1/webhook/config_changed`, `POST /internal/refresh-cache` (`src/api/app.py:204-208`).

**Structural note that colors every gap:** the SBS Relay shim models a **per-document REST surface**
(`/api/relay/finalize`, `/api/relay/approve`, `/api/relay/status`, `/api/relay/artifacts/<ref>`, …) returning
rich `DocumentStatusSnapshot` bodies and **202-queued** envelopes. The real Relay models a **multipart
file-ingest gateway** with no per-document verbs. The SBS is the executable spec for *shapes*; but the
CLAUDE.md MUST-clauses are narrower than the SBS surface — only `/api/finalize`, drift-409, and artifact-fetch
are hard MUSTs. The other SBS verbs (approve/reject/reset/withdraw/reversal/nudge/payment) are forward-looking
and **not named as Relay MUSTs for Monday** (L13 inbound is explicitly out per Q14).

---

## Per-contract gap map

### §B-Submit — Ingest/finalize 3-call taxonomy + per-hash ingest marker

**SBS shape** (`scout_backend_simulator_relay.py` + contract §3):
- **#1 `ingest(finalize=false)`** / **#2 `ingest(finalize=true)`**: `POST /api/ingest`, `metadata.finalize` boolean,
  **PDF bytes** body. Returns status/QR refs + `doc_ref`/`irn`. #2 adds the FIRS push.
- **#3 `finalize(ref)`**: → **`POST /api/finalize { ref, trace_id }`**, **reference only (no PDF bytes)**;
  `ref` = file SHA-256 / `trace_id` / `doc_ref`.
- SBS `relay_finalize_document` (relay.py:328-394) returns **`202`** with `_queued_body` (relay.py:1652-1700).
  Top-level keys verbatim:
  ```python
  {"accepted": True, "queued": True, "operation": "finalize",
   "action_id": "finalize_invoice", "trace_id": <trace>,
   "queue_dedupe_key": <dedupe>, "document_id": <id>, "actor_user_id": <actor>,
   "queue_id": "relayq-…", "job_id": "relayjob-…", "queue_status": "queued",
   "queue_state": {…}, "event_id": <sse id>, "event_family": "core.submission.submitted",
   "document_status": {<full DocumentStatusSnapshot>}}
  ```
- Idempotency: caches by `(operation, document_id, trace_id|idempotency_key)`, replays with
  `idempotent_replay: True` (relay.py:1114-1136). Contract §3.2/§3.3: a duplicate/already-finalized `trace_id`
  returns **409 treated as success** client-side; same `trace_id` carried across the #2↔#3 switch.
- SSE on finalize: **`relay.finalize.accepted` + `core.artifact.hlx_available` + `core.submission.terminal`**,
  all echoing `trace_id`.

**Current Relay state:** PARTIAL/MISSING.
- `POST /api/ingest` exists (`routes/ingest.py:39-52`), takes `metadata`, but **`metadata.finalize` is never read**
  (`ingest.py:84-111` only injects `queue_mode`/`connection_type`; routing is by `call_type` form field). `finalize`
  appears nowhere in `ingestion.py`/`bulk.py`/`external.py`.
- `POST /api/finalize` **does NOT exist**. The only `finalize` is `CoreClient.finalize()` (`clients/core.py:165-191`)
  — a Relay→Core outbound stub, unmounted, unrelated to a Scout-callable inbound route.
- `ExternalService` (`services/external.py:55-118`) ≈ #2 (ingest→IRN→QR) but is **always bytes-in**, no
  reference-only mode. Returns `200 IngestResponse` with `irn`/`qr_code` (`routes/ingest.py:153-188`), not a
  `202`-queued envelope.

**Gap:** (a) `/api/ingest` must branch on `metadata.finalize` (true ⇒ FIRS-push path = today's `call_type=external`;
false ⇒ passive register/preview). (b) Build net-new **`POST /api/finalize { ref, trace_id }`** (no bytes; echo
`trace_id` on lifecycle SSE; 409→success on duplicate `trace_id`). (c) per-hash `backend_ingest_state` marker is
**Scout-side** (scout.py:7551/3636) — no Relay obligation, but Relay must be idempotent-per-hash.

**Chip `relay-finalize-ref-route` — Thu** (largest; do first). **Cross-seat:** **Core** (finalize#3 triggers Core's
HLX/submission lifecycle); **HB** (FIRS keys/IRN via `module_cache`, already wired).

### §B-IngestFinalize — Passive ingest, active finalize, shared update audience

**SBS shape** (relay.py `relay_document_status_snapshot` 200-325; `relay_ingest_register_ingester` 121-166):
`ingested` vs `finalized` on the document; register flips `unknown`/`pre_existing`→`ingested`; snapshot carries
```python
"ingester": {"ingesters": [...], "first_ingester": <id|null>, "current_actor_is_ingester": <bool>}
```
`document.state ∈ unknown|ingested|finalized|pre_existing`; `finalizer` set on finalize (relay.py:366);
`submission_report` block `{"available": bool, "hlx_blob_ref": "", "firs_returned_artifact_ref": ""}`
(relay.py:297-306). Identity keys: tenant/workspace, file SHA-256, safe invoice identity, `trace_id`, `irn`,
`artifact_ref` — filename alone insufficient.

**Current Relay state:** MISSING (no per-document state model). `ingestion.py` is a stateless pipeline; no
snapshot/ingester/finalizer fields on `IngestResponse`. The passive/active distinction is Scout-side §3.2
selection today.

**Gap:** mostly **covered by the §B-Submit chip** if finalize records `finalizer` and ingest records the opener;
full `ingesters[]` + a status-read endpoint are **deferrable** (Reader gets these via Scout projection from SSE).

**Chip `relay-ingest-finalize-actor-model` — Fri** (foldable into §B-Submit; same handler). **Cross-seat:** **Core**
owns authoritative `ingesters[]`/lifecycle persistence; Relay forwards the actor.

### §B-Drift — Version-drift error at the Relay gateway

**SBS shape** (relay.py `_drift_response_if_needed` 1182-1262 via `_relay_preflight` 1083-1111):
on stale axis → **HTTP `409`**, body verbatim `{"code": "version_drift", "axis": <axis>, "expected": <expected>,
"got": <got>}` (relay.py:1253-1262). Request **NOT forwarded**. Checked for actions in `MUTATING_RELAY_ACTIONS`
(relay.py:33-51). Demo primer `configure_relay_drift_on_next_call(axis=…)` (relay.py:76-86).

**Current Relay state:** MISSING entirely. Only `TraceIDMiddleware` + `BodyCacheMiddleware` (app.py:198-202); no
`version_drift` path; the auth dispatcher reads no axes.

**Gap:** pre-forward check on mutating route(s) reading the axis headers, comparing to Relay's authoritative
revisions (from `config_cache`, already in `app.state.config_cache`, app.py:132-140), returning the exact 409 and
not forwarding. Wire `policy_revision` first (config_cache already holds tenant config).

**Chip `relay-version-drift-gateway` — Fri** (gated on Open Q a header decision). **Cross-seat:** **HB** — the
`config_changed` webhook (`routes/internal.py:48`) already refreshes `config_cache`, so the freshness channel
exists; confirm HB emits `policy_revision` in the cached body.

### §B-VersionAxes — The axes the drift check reads from headers

**SBS shape** (relay.py `_normalise_axis_name` 1226-1250 + `relay_axis_headers_for_current_state` 1063-1080):
SBS first-class axes = **`policy_revision`, `license_state_id`, `auth_policy_revision`, `usage_state_id`**, plus
composite **`user_permissions:<user_id>`**. Header normaliser lowercases, strips leading `x-`, strips trailing
`-revision`, `-`→`_`, then alias-maps: `X-Policy-Revision`→`policy_revision`, `X-License-State[-Id]`→
`license_state_id`, `X-Auth-Policy-Revision`→`auth_policy_revision`, `X-Usage-State-Id`→`usage_state_id`.
**Inconsistency:** CLAUDE.md §B-VersionAxes table names the 4th axis `user_permissions:<user_id>` while the SBS
makes `usage_state_id` first-class and `user_permissions` composite-only → **Open Q a**.

**Current Relay state:** MISSING (no axis headers read).

**Gap:** pick the canonical inbound header spelling + the axis set; document as the §B-VersionAxes wire contract so
Scout and Relay agree. Folded into `relay-version-drift-gateway`; **header decision is a blocker (Open Q a).**
**Cross-seat:** **Scout** sends headers on every mutating call; **ARCH** rules on axis set + spellings.

### §B-RelayArtifactFetch — Artifact bytes + lifecycle JSON fetch

**SBS shape** (relay.py `relay_fetch_artifact` 1025-1060): keyed by `artifact_ref`, **kind inferred from the ref**:
`ref.startswith("manifest-")` → **JSON** `{"artifact_ref","document_id","artifacts":{<refs>}}`, 200,
`application/json`; else → bytes via `fetch_blob_from_sbs` (core.py:111-129) with headers `X-SBS-Relay-Artifact:true`,
`X-SBS-Artifact-Ref`, `X-SBS-Artifact-Version`, `ETag: sha256:…`; QR blobs add
`X-SBS-Durable-Invoice-Data: qr_bytes` + `Content-Type: application/vnd.helium.invoice-qr+json`; miss →
`404 {code:"ARTIFACT_NOT_FOUND", artifact_ref}`. Kind→mime via `_blob_payload` (core.py:710-750):
`hlx_blob_ref`/`firs_returned_artifact_ref`/`approval_lifecycle_ref`→JSON; `qr_blob_ref`→qr+json;
`signature_blob_ref`→octet-stream; default→pdf. Scout production adapter `ScoutRelayArtifactFetchAdapter`
(scout.py:477-584) calls with **`artifact_ref` AND `artifact_type`** (scout.py:573-579) — so the real route should
accept an explicit kind discriminator even though SBS infers it. Contract MUST: hard artifacts → bytes only,
lifecycle → raw JSON only; `raw_bytes_sent` stays false to Reader.

**Current Relay state:** MISSING entirely (no artifact route; `core/qr.py`+`core/irn.py` generate, don't serve).

**Gap:** build the fetch. **Critical (VERB_DELTA): `artifact_ref` MUST NOT be in the URL** — implement
**`POST /api/artifacts/fetch { artifact_ref, artifact_type }`** → bytes (hard) or JSON (lifecycle),
`404 ARTIFACT_NOT_FOUND` on miss.

**Chip `relay-artifact-fetch-post` — Sat.** **Cross-seat:** **HB** owns blob storage (Relay's fetch is a thin
authenticated proxy to HB blob + Core lifecycle JSON); confirm HB blob-fetch-by-ref for Relay's service creds.
**ARCH** rules kind enum + bytes-vs-JSON signalling (Open Q b).

### §B-EventLog — `relay.*` lifecycle SSE slice (trace_id echo)

**SBS shape** (events.py `_build_sse_frame` 488-534): Relay-originated families `relay.reversal.approval_requested`
(relay.py:727), `relay.reversal.approval_stage_updated` (831), `relay.nudge.sent` (921), `relay.inbound.invoice_arrived`
(core.py:555); §B-Submit names `relay.finalize.accepted` (finalize currently emits `core.submission.submitted` via
`emit_submission_event`, relay.py:367). Frame:
```python
{"id":"sbs-sse-000123","family":<family>,"event":<family>,"stream":<stream>,
 "source":"scout_backend_simulator","sequence":<int>,"timestamp":<ISO>,"data":{…}}
```
**`trace_id` echo (the MUST):** non-empty `data.trace_id` is lifted to top-level `frame["trace_id"]`
(events.py:530-532).

**Current Relay state:** MISSING (Relay emits no SSE; lifecycle is the synchronous `IngestResponse`).

**Gap:** per architecture (Scout connects to **Core** SSE, not Relay — memory `Scout as SSE Driver`), the real
`relay.finalize.accepted`-class confirmations likely flow over **Core's** stream. So Relay's Monday §B-EventLog
obligation is narrow: **propagate the client-supplied `trace_id` through to Core so Core's lifecycle SSE echoes it**.
A Relay-hosted SSE endpoint is probably NOT required (also keeps Relay a gateway, not a stream host — see
STATUS_RELAY Q15 input).

**Chip `relay-trace-id-propagation` — Sat** (pairs with the finalize chip). **Cross-seat:** **Core** owns SSE +
echo (`emit_submission_event` core.py:427-477); confirm Core accepts + echoes a Relay-forwarded `trace_id`.

---

## VERB_DELTA (ruling §2.1 — shapes binding, verbs per Golden Rule: POST-only except SSE + unauth health/metrics)

| SBS sketch | Sensitive id in URL | Real-Relay verb (Monday) | Notes |
|---|---|---|---|
| `relay_fetch_artifact` docstring **`GET /api/relay/artifacts/<ref>`** (relay.py:1030) | **`artifact_ref` in path** — a capability-bearing handle for raw signed-PDF/HLX/FIRS bytes | **`POST /api/artifacts/fetch` `{artifact_ref, artifact_type}`** | **Hard requirement, §B-RelayArtifactFetch.** `artifact_ref` is effectively a bearer capability — NEVER in a URL/proxy log/referrer. |
| `relay_fetch_document_status` **`GET/POST /api/relay/status`** (relay.py:177) | `document_id`/`invoice_number`/`file_sha256` as query | **`POST /api/status`** (or fold into finalize/ingest response) | Deferrable for Monday — Reader gets status via Scout projection. |
| `manifest-<document_id>` branch (relay.py:1035-1048) | `document_id` encoded in the `manifest-…` ref | same `POST /api/artifacts/fetch` body path | A manifest is just an `artifact_ref` whose value encodes a doc id — same POST rule. |
| Core `GET /api/invoices/<id>`, `/api/approvals/<id>`, `/api/submissions/<id>` (core.py:42/79/100) | `document_id` in path | **Core's** VERB_DELTA, not Relay | Listed so Relay doesn't build them by mistake. |

`GET /health` + `GET /metrics` are the only sanctioned unauth GETs (no identifiers) and are correctly GET
(`routes/health.py:20`, `routes/metrics.py:62`). `/api/ingest` and new `/api/finalize` are already POST. **The single
load-bearing VERB_DELTA for Monday: artifact-fetch is POST-body, never `artifact_ref`-in-URL.**

---

## Open questions for ARCH/peers

**(a) §B-Drift / §B-VersionAxes — canonical header names + the 4th-axis identity.** SBS `_normalise_axis_name`
(relay.py:1226-1250) is permissive (bare keys *and* `X-…`/`…-Revision` aliases). Two unknowns: (1) **which spelling
is the canonical wire contract** Relay should require so Scout sends matching headers? (2) SBS first-class axes are
`policy_revision`/`license_state_id`/`auth_policy_revision`/**`usage_state_id`**, but CLAUDE.md §B-VersionAxes lists
the 4th as **`user_permissions:<user_id>`** (composite-only in SBS). **Which axes must Relay's Monday drift-gate
check?** (`relay_axis_headers_for_current_state` sends all four + composite — suggesting all five matter.)

**(b) §B-RelayArtifactFetch — kind enumeration + bytes-vs-JSON signalling.** SBS **infers** kind from the ref, but
the Scout adapter passes an explicit **`artifact_type`** (scout.py:573-579). (1) **Is bytes-vs-JSON signalled by the
request (`artifact_type`) or inferred by Relay from stored kind?** (2) **Closed enumeration of kinds?** Observed:
`backend_pdf`, `original_pdf`, `fixed_pdf`, `signed_pdf`, `qr_invoice`/`qr_blob`, `hlx`, `firs_returned_artifact`,
`approval_lifecycle_json`, `manifest`, `signature` (relay.py:1453-1462, core.py:710-750). Which are **hard (bytes)**
vs **lifecycle (JSON)**?

**(c) Monday transport: Relay→Core over AMQP, or is HTTP-to-Core acceptable?** Finalize#3 + the FIRS-push must
trigger Core's lifecycle, and `trace_id` must reach Core for the SSE echo. `CoreClient` (clients/core.py) is an
**HTTP stub** (canned dicts, e.g. core.py:180-191). **For Monday, is HTTP-to-Core (filling these stubs in) the bar,
or must Relay→Core go over AMQP?** This decides whether the finalize chip is "wire up CoreClient HTTP" (small) or
"stand up an AMQP publisher" (large). AMQP-first remains the standing contract for the S3 hardening chip regardless;
the question is purely Monday's bar. (RELAY read: HTTP-to-Core for Monday per the least-new-plumbing rule.)

---

## End note — contract revision

Worktree HEAD at audit time = `5a44bd4`, `SCOUT_IMPLEMENTATION_STATUS.md` Contract revision **`2026-06-12-b`** (one
tick past the directive's `2026-06-12-a` baseline). The `-b` bump is a **prose correction with no Relay-clause
changes** — none of the six §B obligations above moved. Flagged to ARCH because the rev is on the **unpushed**
worktree (origin `02abaf29`), so the §1.5 origin drift-detector cannot see `-b` yet (STATUS_RELAY → Needs → ARCH-3).
