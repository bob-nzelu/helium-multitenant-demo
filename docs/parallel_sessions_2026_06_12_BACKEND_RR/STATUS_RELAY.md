# RELAY Seat Status — Backend Round Robin

**Seat:** RELAY (implementation seat, Opus 4.8)
**Session:** Claude Opus 4.8 (1M context), standing seat — registered 2026-06-12
**Repo / worktree:** `C:\Users\PROBOOK\helium-multitenant-demo` (Relay's home + EC2 deploy repo)
**Branch:** `feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook` (S4 / PR #22 branch at RR launch)
**Fork point:** `9d2120e` (main tip; one S4 commit `7a661cb` on top = PR #22 head). Working tree clean.
**Status:** REGISTERED-PENDING-ACK
**Handoff:** `HANDOFF_RELAY_SEAT.md` (ARCH channel) + inherited `~/.claude/agent-briefs/RELAY_FABLE5_ARCH_HANDOFF.md`
**Watcher:** 30-min tick per protocol §5 (started this session)

---

## Registration entry — 2026-06-12

### Mental-model restatement (my own words)

**The estate.** Helium = Nigeria-focused FIRS e-invoicing, **tenant-deployed** (the customer runs the whole
stack on their own data-sovereign infra — not SaaS). Backend (helium-services): **HeartBeat** =
identity/config/blob/audit/permissions spine, source of truth for identity; **Core** = invoice pipeline +
Layer-4 CRUD + SSE + AMQP consumer + QueueScanner; **Edge** = FIRS submission downstream; **Relay (me)** =
the frontend-facing ingress that sits *between* frontends and Core/HB. Frontends (helium-frontends):
**Reader** = per-invoice PDF tool; **Scout** = Reader's SDK (owns SSE subscription, transforma.db,
file_status.json IPC); **Float/Keel** = bulk upload + approval queue; **SBS** = mocks HB/Relay/Core for
Reader+Scout dev. **SBS impersonates Relay today** — no real Relay calls fire from Scout/Reader yet; their
"Relay" references are forward contracts.

**My service's role.** Relay owns: inbound batch/file lifecycle (the 7-step bulk ingest pipeline, **HB blob
write = commit point**), action endpoints (approve/reject/reset/withdraw), duplicate lookup (R5),
cross-tenant audit guard (R10), status orchestration (R4). It does **not** front the whole HB surface —
frontends authenticate to Relay with JWT (or ERP with HMAC) for the **document lifecycle**, but they *also*
call HB **directly** with JWT for login/auth, tenant config, and the SSE stream. (Folding in ARCH's precision
correction issued in the HEARTBEAT ACK.)

**Three distinct HMAC schemes on my surface — never blur (ledger L1 / L2 / L5):**
1. **Relay→HB outbound s2s** — 4 headers (`X-API-Key`, `X-Timestamp`, `X-Nonce`, `X-Signature`);
   `HMAC_S2S_MIGRATION_SPEC.md`; built in `src/clients/_s2s_hmac.py`. (L1, LIVE)
2. **ERP→Relay inbound** — 3 headers (`X-API-Key`, `X-Timestamp`, `X-Signature`; **no nonce**);
   `src/core/auth.py`. (L2, PLANNED Phase 2; X-Nonce question open in Frontdoor §12)
3. **HB→Relay webhook (R12, S4)** — 2 headers (`X-HeartBeat-Timestamp`, `X-HeartBeat-Signature`; 5-min
   replay); `src/api/webhook_auth.py`. (L5, ⚠ UNHARMONIZED vs #68 Ed25519)

**Locked corrections I will not re-invert:**
- **AMQP-FIRST (L15):** write `core_queue.{approve,reject,reset}` to AMQP **BEFORE** the Core HTTP trigger.
  Durable record first, trigger second.
- `core_queue` (my outbound → Core) ≠ `relay_queue` (ERP inbound, Frontdoor Phase 2). Different queues,
  different directions.
- **Frontdoor = the always-on wing** (Relay + relay.db SQLite + relay_queue) of a fully tenant-deployable
  stack — *not* "the only deployable unit." **relay.db stays SQLite** (locked).
- PR **#9** shipped the combined auth dispatcher; **#17** added R9 defensive coverage; **#18** added the
  introspect cache (S1 closed).

**Invoice lifecycle as it touches Relay (outbound, the main flow):** Frontend (Float/Keel bulk, or Reader
per-invoice) → Relay `/api/ingest` (JWT or ERP HMAC) → 7-step pipeline, blob written to HB (commit point) +
dedup registered → for state actions, Relay writes `core_queue.{action}` to AMQP **first**, then
HTTP-triggers Core → Core processes → on approval/terminal-success, Edge submits to FIRS, IRN/FIRS-ref
written back → Scout (SSE driver on Core/HB) projects to file_status.json → Reader reacts. Side surfaces:
duplicate lookup (R5), status orchestration (R4/S5 calls F5 batch-state + Core E6 in parallel), lock client
(R8/S5 → HB D4), withdraw (R3/S6 → Core E7). **Forward debt (L13):** 4 inbound-invoice endpoints
(`/api/inbound/{accept,payment-status,transmit,nudge}`) for Reader's Inbound tab — not in CSSV1; Bob's phase
call (Q12).

**Scope lock (§2):** I write only `services/relay/*` + relay compose/.env/Dockerfile in helium-multitenant-demo
+ Relay rows in `CSSV1_CHIP_STATUS.md` + this channel file + routine `relay-api` EC2 redeploys of ratified
merges. Never HB/Core/Reader/Scout/Float/Keel/SBS code — document → `## Needs` → owning seat. Sensitive class:
ledger L2/L5/L12/L13/L15 wire shapes, relay.db schema (post-Frontdoor), EC2 beyond routine relay-api redeploys.

### First-three-actions plan (after ARCH `REGISTRATION-ACK`)

1. **Re-verify state + RR-submit #21.** `gh pr list` both repos + `gh pr view 19` comments + re-read
   DECISION_QUEUE (Q1/Q3 rulings) + chip-status §2 (D4 may have moved). Then **RR-submit PR #21**
   (bearer_removed alarm test-baseline fix; test-only, touches **no** ledger row, **no** Q1 dependency) —
   post `SUBMITTED` entry; merge on ARCH `APPROVED-FOR-MERGE`. Clears the suite baseline before the gated PRs.
2. **Spawn the SBS-vs-Relay integration debt audit** (handoff §4.3; READ-ONLY; background sub-agent).
   Catalogs every Relay surface SBS mocks vs CSSV1 R1–R12 + the 4 inbound endpoints (L13): request/response
   shape deltas, cutover seams. Output → `services/relay/Documentation/READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md`
   (Relay tree, per this handoff — supersedes the inherited brief's helium-frontends location). Feeds Q12.
3. **§2 S5-row trickle-down doc PR** to helium-services: chip-status §2 S5 row `D3+D4 | PLANNED + PLANNED` →
   `D3 EC2_DEPLOYED, D4 IN_PROGRESS` (PR #115 pattern; doc-only self-merge). In parallel, queue #20 (S7)
   then #22 (S4) for submission **pending Q1 ratification** — see question 2 re: #22's L5 freeze.

### Alignment questions for ARCH

1. **#21 pre-Q1 mergeability.** PR #21 is test-only (strips the `relay_introspect_cache_total` row from a
   bearer-alarm snapshot assertion), zero runtime impact, touches no `CONTRACT_LEDGER` row. I read it as
   **non-sensitive → `APPROVED-FOR-MERGE` on your vet alone** (not `APPROVED-PENDING-BOB`), mergeable ahead
   of Bob's Q1 ruling to clear the suite baseline. Confirm?

2. **#22 (S4): split vs monolithic, given the L5 freeze.** Ledger L5 is ⚠ UNHARMONIZED and says "freeze
   merges touching it until #68 ↔ #22 ↔ Core verifier resolve." #22 bundles three things: **R7 helium-hash
   adoption** + **`record_duplicate()` deletion** (both clean — no L5/Q1 touch) and **R12 signed webhook**
   (the L5-frozen part; ships a *symmetric 2-header* scheme while #68 proposes *Ed25519 asymmetric*).
   Options: **(a)** submit #22 whole → it sits `APPROVED-PENDING-BOB` **and** frozen-on-L5; **(b)** split S4
   → land R7 + record_duplicate-deletion now, hold R12 webhook for the L5 resolution. I lean **(b)** so the
   clean two-thirds isn't held hostage by the webhook-crypto harmonization. Your call (ledger-interpretation
   + merge-order)?

3. **L5 harmonization kickoff.** I've posted R12's **implemented** webhook shape under `## Needs` so
   harmonization task #1 runs on a real shape, not a sketch. Flag if you'd rather I route it differently
   (directly onto the L5 ledger row, or a chip-status note) instead of via `ANSWER TO HEARTBEAT`.

## Needs

- **NEEDS FROM HEARTBEAT:** L5 webhook-scheme harmonization (task #1). *(ANSWER TO HEARTBEAT:)* my R12 ships
  **symmetric 2-header HMAC** — `X-HeartBeat-Timestamp` + `X-HeartBeat-Signature`, 5-min replay window,
  verifier `src/api/webhook_auth.py::verify_webhook_request`, key via `RELAY_WEBHOOK_SIGNING_KEY`
  (HB mints + logs at WARNING on first boot). PR #68 proposes **Ed25519 asymmetric** (§3.5, "supersedes #56").
  Different crypto families — one scheme must win before #22's R12 portion or #68 merges. I can adapt
  `webhook_auth.py` to verify Ed25519 if that's the harmonized choice.
- **NEEDS FROM HEARTBEAT:** D4 (lock endpoint) EC2-deploy ETA — gates Relay S5 final (R8 lock client + R4
  status orchestration). Tracked as Q4/Q13 (PG blocker). No action needed now; surfacing for sequencing.
- **NEEDS FROM CORE:** (1) `HLX_CANONICAL_FORM.md` (ledger L4, UNWRITTEN) — S4 vendors `sha256_hlx` but it's
  **unused** in Relay today (no HLX call-sites); not blocking, but S4's HLX semantics stay inert until this
  lands. (2) Core **E7 withdraw handler** ETA — long pole for Relay S6 (R3 withdraw).
- **NEEDS FROM BOB** (via ARCH → DECISION_QUEUE): none beyond the already-queued Q1/Q3 that gate my merge chain.

## Updates

- 2026-06-12, session start: RELAY seat registered (Opus 4.8). Read in order: `HANDOFF_RELAY_SEAT.md`,
  `README_PROTOCOL.md`, `CONSOLIDATED_BACKEND_STATE_2026_06_12.md`, `CONTRACT_LEDGER.md`, `DECISION_QUEUE.md`,
  `STATUS_ARCH.md`; inherited canon `RELAY_FABLE5_ARCH_HANDOFF.md` + `ARCH_SYNC_RELAY_CSSV1_2026_05_17.md`;
  chip-status §2 Relay trigger map; both repo `CLAUDE.md`. Verified local git: branch
  `feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook` @ `7a661cb` on main `9d2120e`, tree clean.
  Mental-model restatement + first-three-actions + 3 ARCH questions posted above. Status
  `REGISTERED-PENDING-ACK` — **no code before ARCH `REGISTRATION-ACK`.** 30-min watcher starting.
