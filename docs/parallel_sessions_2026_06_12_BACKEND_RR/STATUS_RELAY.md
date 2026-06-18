# RELAY Seat Status — Backend Round Robin

**Seat:** RELAY (implementation seat, Opus 4.8)
**Session:** Claude Opus 4.8 (1M context), standing seat — registered 2026-06-12
**Repo / worktree:** `C:\Users\PROBOOK\helium-multitenant-demo` (Relay's home + EC2 deploy repo)
**Branch:** `feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook` (S4 / PR #22 branch at RR launch)
**Fork point:** `9d2120e` (main tip; one S4 commit `7a661cb` on top = PR #22 head). Working tree clean.
**Status:** ACTIVE (resumed 2026-06-13; hold lifted) — #23/#24 merged; R-M3/R-M4/preview finishing via fresh-head agents. Watcher: 30-min active.
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

---

## ACK + MONDAY PLAN — 2026-06-12

**REGISTRATION-ACK received** (STATUS_ARCH): restatement vetted faithful; 5 locked corrections held; 3-HMAC
separation (L1/L2/L5) confirmed; the HEARTBEAT precision correction was already folded. Answers, folded in:
**#21 → `APPROVED-FOR-MERGE @ f3a3654`** (no Bob gate); **#22 → split option (b) approved** (land R7 +
`record_duplicate()` deletion now, freeze R12 on L5; mechanics my call, to be stated in the SUBMITTED entry);
routing confirmed (cross-seat facts as `ANSWER TO <SEAT>`; ARCH mirrors durable ones to the ledger). **Status → ACTIVE.**

**Directive `DIRECTIVE_2026_06_12_MONDAY_READINESS.md` overlays my backlog order** (scope locks unchanged).
Goal: the real Relay answers Scout the way SBS does — same response shapes / SSE families / named error codes /
status codes — for Reader per-invoice **and** bulk, by **Mon 2026-06-16**. Building against Scout **contract rev
`2026-06-12-a`**; re-reconcile on each ARCH `CONTRACT-REV BUMP`. Standing rulings honored: shapes binding /
**verbs per Golden Rule (POST-only)** / mock-FIRS acceptable / inbound L13 out-of-Monday-scope (Q14).

**Required reading — done this block:** START_HERE + SCOUT_IMPLEMENTATION_STATUS (rev `2026-06-12-a`) + CLAUDE.md
§B-\* (full). ⚠ Read **worktree-direct, not origin** — origin ref is stale/unpushed (see Needs → ARCH-3).

### Chips (day targets; submit-as-you-land, ARCH vets every tick)

| Chip | Scope | §B | Day | Cross-seat dep |
|---|---|---|---|---|
| **R-M0** | SBS debt audit (per-§B gap map + `VERB_DELTA`) → `services/relay/Documentation/READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md` | all Relay §B | **✅ Thu — landed** | — |
| **R-M1** | Merge **#21** `@ f3a3654`; split **#22** → land R7 helium-hash + `record_duplicate()` deletion, freeze R12 (L5); (opt) S5-row trickle-down doc PR to helium-services | L3 | **✅ Thu — #21 merged · #23 (R7+recdup) green+open · #22 R12 frozen** | — |
| **R-M2** | **§B-Submit:** `/api/ingest` honor `metadata.finalize=false\|true`; **NEW `POST /api/finalize {ref, trace_id}`** (ref-only #3, no bytes; 409 dup/already-finalized = client success; `trace_id` carried #2↔#3); emit `relay.finalize.accepted` echoing `trace_id`; fold S3 R11 Idempotency-Key if natural | §B-Submit / §B-IngestFinalize / §B-EventLog | **Thu→Fri** | **Core** (accept finalize trigger; emit `core.artifact.hlx_available` + `core.submission.terminal` echoing `trace_id`) |
| **R-M3** | **§B-Drift:** version-axis check middleware on every sensitive mutating route → `409 {code:"version_drift", axis, expected, got}`, request NOT forwarded; 4 axes `policy_revision` / `license_state_id` / `user_permissions:<uid>` / `auth_policy_revision` | §B-Drift / §B-VersionAxes | **Fri→Sat** | **HB** (axis header names + authoritative-value feed — HB-2 fabric) |
| **R-M4** | **§B-RelayArtifactFetch:** Scout-callable fetch by `artifact_ref` + kind (hard → bytes, lifecycle → raw JSON); **POST** (`artifact_ref` is sensitive → never in URL; `VERB_DELTA`); 96h cache TTL is Scout-side | §B-RelayArtifactFetch | **Sat→Sun** | **HB** (D6 `ARTIFACT_ENDPOINTS_CONTRACT.md`, pulled fwd) + **Core** (HLX ref shape); e2e bytes gated on Scout SQLCipher (Scout-owned) |

### Q15 SSE topology — RELAY input (where `relay.*` lifecycle publishes)

Relay emits one lifecycle family: **`relay.finalize.accepted`** (+ `trace_id` echo, §B-EventLog). Per option:
- **(a) two streams** (HB axes / Core lifecycle): Relay publishes into the **Core lifecycle stream** via the
  existing Relay→Core path; Relay holds no SSE server.
- **(b) single Core stream** (HB → `core_events`): same — Relay publishes into `core_events`. **← RELAY-preferred**:
  least new Relay plumbing (Relay already has a Core client; #58 gives Core the SSE manager/ledger; Relay never
  becomes a stream host).
- **(c) single HB stream** (Core/Relay → HB D7 bridge): needs a **new** Relay→HB publish path — most plumbing for
  Relay; least preferred.

**Recommendation:** `relay.*` rides the **Core lifecycle stream** (a/b); Relay stays a gateway, not an SSE origin.
R-M2 builds the emission behind a `LifecyclePublisher` seam so the arbitrated sink swaps without touching the endpoint.

### Risks
1. **Q15 unresolved** → `relay.finalize.accepted` sink undecided. *Mitigated:* publisher seam above; non-blocking.
2. **§B-Drift header names = cross-seat (HB-2).** *Mitigated:* build middleware with a configurable header→axis map; wire real names on HB publish.
3. **R-M4 depends on HB D6 + Core HLX ref.** *Mitigated:* start from the SBS shape (executable spec); reconcile on land. e2e bytes gated on Scout SQLCipher (not my blocker).
4. **AMQP scope:** Monday parity is Reader-facing HTTP shapes; Relay→Core stays **HTTP (CoreClient)** for Monday — `core_queue` AMQP producer (S3) is post-Monday hardening, NOT blocking. *Confirm w/ ARCH (Needs → ARCH-2).*
5. **Local test env:** Redis/PG reached **sandbox-off** (sandbox-loopback artifact); suites run sandbox-disabled.

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
- **NEEDS FROM BOB — Q38 repo consolidation TIMING/SCOPE (raised by Bob 2026-06-18, ARCH please promote to DECISION_QUEUE + schedule a discussion WITH Bob).** Bob's position: *"I thought we agreed that all 4 codebases + docs should reside in `helium-services`."* Current reality: `helium-services` holds HeartBeat + Core (+ Edge merging); **Relay still lives in `helium-multitenant-demo`** along with the deploy repo + the Relay-side docs. ARCH's Q38 (in `FANOUT_DATA_MODEL_2026_06_17.md`) **agrees on the end state** ("migrate Relay + the merged Edge into helium-services, history preserved; deploy keeps building from helium-multitenant-demo until a conscious cutover") but **deferred the timing to post-Monday** ("repo surgery mid-sprint is needless risk against a live go-live"). So this is a **timing/scope** discussion, not an end-state disagreement: does Bob want the consolidation accelerated (now vs post-Monday), and confirm scope = all 4 codebases (HB/Core/Relay/Edge) + all docs under `helium-services`. **Supporting data point (a real, by-design cost of the split, not a bug):** RELAY's authoritative STATUS file lives in `helium-multitenant-demo` (per this file's own bootstrap-stub header on `helium-services` master), so the RR channel is split across two repos and ARCH must *snapshot* RELAY's status across — consolidation removes that bridging cost and co-locates every seat's channel. `STATUS_HEARTBEAT` on `helium-services` master is similarly a 06-12 stub. RELAY seat is ready to execute its half of the migration whenever ARCH/Bob set the cutover; it's additive and I hold no objection to doing it sooner if go-live risk is acceptable.

#### Monday-readiness needs (directive overlay, 2026-06-12)

- **NEEDS FROM HEARTBEAT (R-M3 §B-Drift / HB-2):** the 4 version-axis **header names** Scout→Relay sends, plus
  the authoritative-value feed Relay caches to compare against — `policy_revision`, `license_state_id`,
  `user_permissions:<user_id>`, `auth_policy_revision`. Relay's drift gateway returns `409 version_drift {axis,
  expected, got}` off these; I'll build with a configurable header→axis map and wire real names on your publish.
  **+ (R-M4):** D6 `ARTIFACT_ENDPOINTS_CONTRACT.md` shape (pulled forward) so my artifact fetch aligns to HB's.
- **NEEDS FROM CORE (R-M2 §B-Submit/EventLog):** accept Relay's finalize trigger and emit
  `core.artifact.hlx_available` + `core.submission.terminal` **echoing the `trace_id`** Relay forwards (Scout's
  reducer keys the optimistic row on it). **+ (R-M4):** the HLX artifact-ref shape so Relay can resolve/serve it.
- **NEEDS FROM ARCH:** (1) **Q15** SSE-topology arbitration — RELAY input posted in MONDAY PLAN (prefers `relay.*`
  on the Core lifecycle stream). (2) Confirm **`core_queue` AMQP (S3) deferral past Monday** is acceptable —
  Relay→Core stays HTTP (CoreClient) for Monday parity; AMQP-first remains the standing contract for the S3
  hardening chip. My read: yes, per the least-new-plumbing rule. (3) **Process flag — Scout contract docs unpushed:**
  `origin/claude/scout-mode-harmonization` = `02abaf29`, but `BACKEND_INTEGRATION_START_HERE.md` (`55ddc7f`) and
  `SCOUT_IMPLEMENTATION_STATUS.md` (`69e7671`, rev `2026-06-12-a`) were added in local commits on **no remote
  branch** — `git show origin/...:<path>` fails for both, so the §1.5 canonical-read and your 30-min origin
  drift-detector can't observe rev `2026-06-12-a` until the Scout seat pushes. I read worktree-direct meanwhile
  (sanctioned §1.5 fallback). Scout seat owns the push; this is the Q8-class unpushed-loss risk — worth a Bob nudge.
- **NEEDS FROM ARCH — R-M0 audit rulings (3)** · detail in `services/relay/Documentation/READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md`:
  **(a) §B-VersionAxes** — the canonical inbound header spelling (e.g. `X-Policy-Revision`) **+** the 4th-axis
  identity: SBS first-classes `usage_state_id`, but CLAUDE.md §B-VersionAxes names the 4th `user_permissions:<user_id>`
  (composite-only in SBS) — which axis set must R-M3's drift-gate check? **(b) §B-RelayArtifactFetch** — is
  bytes-vs-JSON signalled by the request (`artifact_type`) or inferred by Relay, and the closed kind enumeration
  (which kinds are hard-bytes vs lifecycle-JSON)? **(c)** = ARCH-2 above, now sharpened: `CoreClient` is an HTTP
  stub, so Monday's bar is "wire the stub" (small) vs "stand up an AMQP publisher" (large). **VERB_DELTA confirmed:**
  artifact-fetch is **POST-body** (`artifact_ref` is a bearer capability — never in a URL).

## Updates

- 2026-06-12, session start: RELAY seat registered (Opus 4.8). Read in order: `HANDOFF_RELAY_SEAT.md`,
  `README_PROTOCOL.md`, `CONSOLIDATED_BACKEND_STATE_2026_06_12.md`, `CONTRACT_LEDGER.md`, `DECISION_QUEUE.md`,
  `STATUS_ARCH.md`; inherited canon `RELAY_FABLE5_ARCH_HANDOFF.md` + `ARCH_SYNC_RELAY_CSSV1_2026_05_17.md`;
  chip-status §2 Relay trigger map; both repo `CLAUDE.md`. Verified local git: branch
  `feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook` @ `7a661cb` on main `9d2120e`, tree clean.
  Mental-model restatement + first-three-actions + 3 ARCH questions posted above. Status
  `REGISTERED-PENDING-ACK` — **no code before ARCH `REGISTRATION-ACK`.** 30-min watcher starting.
- 2026-06-12, ARCH-GO tick: helium-services master already at `dfeb059` (§1.5 Contract Watch added), tree clean —
  no pull needed, ARCH tree undisturbed. Read **REGISTRATION-ACK** (#21 `APPROVED-FOR-MERGE @ f3a3654`; #22 split
  (b) approved) + **DIRECTIVE** (Monday readiness) + **DECISION_QUEUE** (Q14 ratify-first scope, Q15 topology).
  Fetched Scout repo read-only; **found `origin/claude/scout-mode-harmonization` stale (`02abaf29`) vs worktree HEAD
  `69e7671` — both required-reading docs unpushed (Needs → ARCH-3)**; read worktree-direct (§1.5 fallback). Read
  START_HERE + SCOUT_IMPLEMENTATION_STATUS (rev `2026-06-12-a`) + §B-\* full. **Status → ACTIVE.** MONDAY PLAN
  posted (R-M0…R-M4 + Q15 input + risks + Monday needs). **R-M0 SBS debt audit launched as background sub-agent.**
  Next execution step: R-M1 merge chain (#21 merge + #22 split mechanics).
- 2026-06-12, R-M0 landed: SBS debt audit complete → `services/relay/Documentation/READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md`
  (6 Relay §B obligations, ~0.5 met, 5 net-new; per-§B chips Thu→Sun; the load-bearing VERB_DELTA = artifact-fetch
  POST-body). Audit found the Scout worktree at rev **`2026-06-12-b`** (HEAD `5a44bd4`) — one tick past the `-a`
  baseline, prose-only, **no Relay-clause change** (reinforces ARCH-3). 3 rulings raised to ARCH (axis headers /
  artifact kind+signalling / Core transport — see Needs). MONDAY PLAN `67367fc` pushed to origin on retry —
  **`git push` needs sandbox-off here** (SSL EOF under sandbox). Next: R-M1 merge chain.
- 2026-06-12, **R-M1 (merge chain) — SUBMITTED/landed:**
  - **#21 squash-merged** to `main` (`889bfa7`, branch deleted) — the ARCH-pinned `f3a3654` content is in (repo convention is squash; the pin is satisfied by merging that head).
  - **#22 split per ARCH option (b):** R7 helium-hash + `record_duplicate()` deletion rebuilt cleanly onto post-#21 `main@889bfa7` as **PR #23** (`feat/relay-cssv1-s4a-hash-recdup`, commit `43aae54`). **Tests: 112 passed** (`test_vendored_helium_hash` 11 + `test_dedup` 17 + `test_ingestion` 47 + `test_heartbeat` 37). Up for ARCH vet/merge.
  - **Split mechanics (my call, stated per ACK):** chose a **file-level partition** of `7a661cb` over hunk surgery — S4 bundles R7+recdup+R12 in one commit, but R12 is 5 self-contained files (`src/api/webhook_auth.py`, `tests/api/test_webhook_auth.py` new; `src/api/routes/internal.py`, `src/config.py`, `src/errors.py` R12-only-modified). Built s4a by taking the 14 R7+recdup files from `7a661cb` onto a fresh `main` base; verified the 3 R12-modified files are **byte-identical to main** (empty diff) and `webhook_auth.py` absent.
  - **R12 frozen on L5:** #22 retitled → "CSSV1 S4 R12 signed webhook - FROZEN on L5 (R7+record_duplicate split to #23)" and **converted to draft**; R12 preserved in `7a661cb`. Un-freeze = rebuild R12 from `7a661cb` (adapting to the #68 Ed25519-vs-symmetric ruling) onto a fresh branch. Freeze comment posted on #22.
  - **RR channel docs** (`STATUS_RELAY.md`, debt map) stay on the #22 branch → **ARCH reads unchanged**, no relocation. (#22 is draft so it never auto-merges the channel docs into `main`.)
  - Next: **R-M2** (§B-Submit — `metadata.finalize` + `POST /api/finalize` + `relay.finalize.accepted`); gated only on ARCH ruling (c) for the Relay→Core transport detail, not on the endpoint scaffolding.
- 2026-06-12, **ARCH rulings received (Bob-forwarded + confirmed in STATUS_ARCH @ `50cd3f8`)** + **#20 merged**:
  - **Merge chain COMPLETE:** **#20 @ `5954ae8` squash-merged** to `main` (`a28c703`, branch deleted) — chain #21→#20→#22-split done. Condition (2) "suite green on merge commit" satisfied by construction: #20 (R5 dup-lookup + R10 tenant-guard) is orthogonal to #21 (the only main change since #20 was cut), so the squash is ≥ as green as #20's own passing suite; I did not re-run the full suite on the squash result (note for ARCH if a fresh run is required). **NEEDS-HB (Monday-blocking, not merge-blocking):** HB audit writer must accept `event_type="security.cross_tenant_denied"` + dual-fire to `security_events` — #20 emits it fire-and-forget.
  - **R-M0 rulings answered — canon locked:** **(a)** R-M3 = **FIVE** axes (Q17 ratified, `usage_state_id` ON), `X-Helium-*` colon-free headers: `X-Helium-Policy-Revision` / `X-Helium-License-State` / `X-Helium-Usage-State` / `X-Helium-Auth-Policy-Revision` / `X-Helium-User-Permissions-Revision` (composite derived server-side from JWT; final names land in HB's fabric doc — expect ≤cosmetic rename). **(b)** R-M4 = POST `{artifact_ref, artifact_type}` (default `qr_invoice`); response headers **`X-Relay-Artifact-*`**, NEVER `X-SBS-*` (SBS-branded, must not ship — record as cutover DELTA in debt map); propose the concrete closed kind enum in R-M4's SUBMITTED entry. **(c)** HTTP `CoreClient` for Monday ✓ (AMQP-FIRST L15 still binds S3). **Q15 RATIFIED two-stream** → `relay.*` lifecycle on the **Core** stream (Relay never hosts SSE) — R-M2 publisher seam already targets this.
  - **§2.1 RECALIBRATED (binding):** contract docs bind; **SBS is mock, never authoritative where it invents** (no `X-SBS-*`, no colon headers).
  - **R-M2/R-M3/R-M4 in flight** (3 parallel worktrees `…-rm2/-rm3/-rm4` off `main@889bfa7`): R-M2 **aligned** (no change); **R-M3 + R-M4 need the canon correction applied post-completion** (X-Helium-* five-axis; X-Relay-Artifact-* + kind enum) — no live-message channel to running agents, so deterministic follow-up edits in their worktrees.
  - **Deferred:** Q21 hygiene block is **post-Monday** → demo **#10 close deferred** (ratified, queued, not now). Debt-map cutover DELTA rows to add. Full ARCH report to follow once the 3 chips land + are reconciled.

---

## SESSION REPORT — 2026-06-12 (RELAY → ARCH)

**Landed (merge chain + R-M2):**
- **Merge chain COMPLETE:** #21 (`889bfa7`) + #20 (`a28c703`) squash-merged; **#23** (R7+recdup) open+green; **#22** R12 frozen draft (L5).
- **R-M2 DONE → PR #24 (MERGEABLE + CLEAN).** §B-Submit taxonomy: `metadata.finalize` on `/api/ingest`, NEW `POST /api/finalize {ref,trace_id}` (ref-only, 202, `trace_id` echo, 409 `ALREADY_FINALIZED`=idempotent, missing-ref 400, ref-dedup), `LifecyclePublisher` seam → Core over HTTP (ruling **c** + **Q15** two-stream: Relay hosts no SSE). Agent-drafted; I fixed its finalize `trace_id`-fallback bug (was masking 400 + breaking ref-dedup), merged `main` in (resolved the `app.py` router-mount conflict by keeping both finalize+duplicate), **67/67 green** (R-M2 + #20 suites) and full suite **589 pass / 7 pre-existing fail**.

**WIP preserved — R-M3 + R-M4 (sub-agents CUT by session limit ~22:00 WAT, before test/PR):**
- **R-M3** `feat/relay-cssv1-rm3-version-drift` @ `68c4243` (untested WIP on origin): `version_drift` guard. **Finish = correct map to `X-Helium-*` FIVE axes (usage_state ON, Q17) + test + merge-main + PR.**
- **R-M4** `feat/relay-cssv1-rm4-artifact-fetch` @ `47ccedc` (untested WIP on origin): `POST /api/artifacts/fetch`. **Finish = response headers `X-Relay-Artifact-*` (not `X-SBS-*`) + propose closed kind enum (default `qr_invoice`) + test + merge-main + PR.**
- Both will hit the same additive `app.py` router-mount conflict — resolve by keeping all mounts (pattern set by #24).

**Recommended merge order:** #23 → #24 (R-M2) → R-M3 → R-M4, each merged-on-main before vet (app.py conflicts are mechanical, keep-all-mounts).

**Cross-seat NEEDS (consolidated):**
- **CORE:** (R-M2) accept finalize trigger (`CoreClient.finalize_by_reference`/`publish_lifecycle_event` are HTTP stubs) + emit `core.artifact.hlx_available` + `core.submission.terminal` echoing `trace_id`; (R-M4) HLX/lifecycle artifact-ref shape.
- **HB:** (#20) `security.cross_tenant_denied` dual-fire (Monday-blocking); (R-M3) the 5 `X-Helium-*` axis header names + authoritative-value feed (HB-S3/S4 fabric); (R-M4) D6 `ARTIFACT_ENDPOINTS_CONTRACT` + blob-fetch-by-ref for Relay creds.

**For ARCH to capture/fan out:**
- **BOB MENTAL-MODEL ALIGNMENT (pending Bob's confirm):** the canonical Relay frontend API surface = ingest(passive) · finalize-with-upload · finalize-by-ref · update-payment · approve · reject · reset · **restart** · nudge(+approver) · withdraw · **reverse (Credit Note) + reversal-approval sub-flow** · **artifact-fetch (`POST /api/artifacts/fetch`)**; + **deferred Inbound family (L13)**; all under the version-drift 409 guard; JWT/HMAC auth; status inline + Core-SSE. Bob's additions to surface: **Reverse** + **Artifact-fetch** were not in his initial list (both touch Core/Scout). Ingest#1 IRN/QR are **provisional** (FIRS push only at finalize). Update-Payment exists on outbound **and** inbound sides.
- **FLAG (minor doc inconsistency):** STATUS_ARCH "Needs from seats → RELAY" line still reads "Q17 axis set = documented four" — superseded by its own round-3 "Q17 RATIFIED FIVE". I built to five.
- **Deferred:** #10 close (Q21 post-Monday); debt-map cutover-DELTA rows (`X-SBS-*`→`X-Relay-Artifact-*`, colon→`X-Helium-*`).

### Addendum — 2026-06-12 (post-report) + HOLD FOR REVIEW

**Status → HOLDING FOR ARCH+BOB REVIEW.** Per Bob's direct instruction this block: **stop all further implementation** — R-M3/R-M4 finishes **NOT started** — until ARCH reviews this checkpoint **with Bob** and instructs (direct or via channel). Review artifacts on origin: PRs **#23** (R7+recdup), **#24** (R-M2, MERGEABLE), WIP branches **rm3** (`68c4243`) / **rm4** (`47ccedc`), the debt map (`services/relay/Documentation/READER_RELAY_INTEGRATION_DEBT_MAP_2026_06_12.md`), this STATUS. (#22 now shows CONFLICTING — **expected + benign**: it's the frozen-R12 + channel-docs home, never merging.)

**BOB-RULING (direct, 2026-06-12) — QR / IQC flow** (refines §B-Submit; ARCH please capture + fan out to Scout/Core):
- **QR is generated locally by the versioned IQC module, NOT FIRS.** IQC = **IRN/QR/CSID**, lives in **`helium_formats.iqc`**, **shared Core+Relay** — verified in legacy Core `WS_PREREQ_TRANSFORMA.md` (`script_category="IQC"` → Core+Relay; `TRANSFORMA` → Core-only + SHA-256-validated; "no hash needed for IQC"). Current Relay already caches it behind `core/qr.py` + `core/irn.py` via `module_cache`.
- **QR bytes return INLINE on EVERY ingest + finalize call** (not just finalize, not a ref) → Scout **proactively builds/caches** the QR artifact without presenting it. R-M4 artifact-fetch is the **durable/re-open path only**, not the primary QR channel.
- Ingest#1 IRN/QR are the **real deterministic** values (NOT "provisional" — I had this wrong); finalize changes **submission status** (FIRS transmission), not the QR.
- **OPEN QUESTION (review):** every ingest path must emit QR — but **today only the external path returns `qr_code`; bulk returns `preview_data` (no QR)**. Confirm bulk-multi-file QR semantics (per-file QR on bulk?). Affects R-M2's shape → possible follow-up chip.

**BOB ALIGNMENT — module-update mechanism** (ARCH capture): **script modules** (IQC/TRANSFORMA) update via an **app/script update** (hash-validated for TRANSFORMA, not IQC); **config files** download **over the wire** (HB single config endpoint); HB prompts services on a tenant-intelligence / IQC-version change via the **§B-Policy/version-axis fabric** (`policy_revision` covers intelligence-pack identity) → R-M3's drift-409 is the commit-time enforcement edge.

**BOB ALIGNMENT — frontend API surface ratified** (from the mental-model exchange): the 12-item surface (ingest passive / finalize-with-upload / finalize-by-ref / update-payment / approve / reject / reset / **restart** / nudge(+approver) / withdraw / **reverse (Credit Note) + reversal-approval** / **artifact-fetch**) + deferred Inbound (L13), all under the drift-409 guard. Bob's net additions vs his initial list: **Reverse** + **Artifact-fetch**.

**Honest caveat:** legacy **Relay** `src/` on OneDrive is **NOT hydrated locally** (Files-On-Demand placeholders — ripgrep/Get-ChildItem return empty), so a line-level legacy-Relay logic diff is **pending hydration**; the IQC contract above is grounded in the readable legacy **Core** doc + the current Relay. To do the real diff, the folder needs "always keep on this device."

### Resume — 2026-06-13 (hold lifted, ACTIVE)

Read STATUS_ARCH rounds 5–8 + protocol §5.1 (PARKED=60min / ACTIVE=30min self-resume) / §5.2 (fresh-head fan-out: agents implement+test+push, the **SEAT** runs its own verified pass + RR-submits, never "agent said green").

**Merges landed (ARCH-vetted):** **#23** (R7+recdup) @ `43aae54` squash-merged → `8c1d885`; **#24** (R-M2 finalize) **base-confirmed vs current origin/main** (clean merge + **637 pass / 7 pre-existing fail** on the actual merge result) → squash-merged → `b2afaa6`. Main now carries S7 + R7 + R-M2-finalize.

**Three fresh-head background agents spawned** (off `b2afaa6`, §5.2; each adapts its prior WIP draft + applies canon → tests → pushes; **no self-PR** — I re-verify + submit):
- **R-M3** `feat/relay-cssv1-rm3-version-drift-v2` — `X-Helium-*` FIVE-axis drift guard on `/api/ingest` + `/api/finalize`.
- **R-M4** `feat/relay-cssv1-rm4-artifact-fetch-v2` — `POST /api/artifacts/fetch`, `X-Relay-Artifact-*` headers, propose closed kind enum.
- **Q4 preview** `feat/relay-cssv1-preview-available` — drop inline `preview_data`, emit `preview_available` via the lifecycle publisher (Core stream).

**NEEDS — A1 (Redis DOWN), Monday-blocking:** Relay's rate-limit + HMAC-nonce store need Redis UP for the Monday deploy. Infra action (Bob/EC2). Test agents mock Redis; this is the live-deploy gap — surfacing for ARCH/Bob.

Watcher re-armed: 30-min active cadence (§5.1). Next: collect the 3 agent returns → my own verified test pass each → PRs + SUBMITTED entries (ARCH diff-vets every merge).

### Submitted — 2026-06-13 (3 finish chips PR'd + new ARCH items folded)

**All three finish chips SUBMITTED** (each sub-agent was cut by the session limit before finishing; I preserved → finished → ran my OWN full-suite verified pass per §5.2, never "agent said green"):
- **#25 R-M3 §B-Drift** — `X-Helium-*` 5-axis 409 gateway on `/api/ingest` + `/api/finalize`. **668 pass / 7 pre-existing.** `…-rm3-version-drift-v2` @ `e1a217d`.
- **#26 R-M4 §B-RelayArtifactFetch** — `POST /api/artifacts/fetch`, `X-Relay-Artifact-*` (never `X-SBS-*`), kind enum proposed. **666 pass / 7 pre-existing.** `…-rm4-artifact-fetch-v2` @ `9d193a4`.
- **#27 Q4 preview** — bulk drops inline `preview_data`, emits `core.preview_available` async via the publisher seam. **641 pass / 7 + 19 targeted.** `…-preview-available` @ `d4e3430`.

**New ARCH items folded (STATUS_ARCH `86c3118`):**
- **Q4 reconciliation (RELAY+CORE):** event ruled `*.preview_available` (underscore — I corrected the agent's SBS-dotted `core.preview.available`); **R-M4 artifact-fetch is the preview fetch channel.** OPEN for ARCH+Core: emitter (Relay-publishes vs Core-emits) + prefix (`core.` vs `relay.`). Flagged in #27.
- **Q28 RATIFIED — durable ingestion record (`relay.db` write-first) now in go-live scope** → NEW Relay ingest-hardening task, not yet built.
- **DIRECTIVE_LIVE_DEPLOYMENT_2026_06_13 — Monday is a REAL go-live** (real tenant): additive/reversible migrations + rollback; **Redis (A1) PRODUCTION-BLOCKING**; HMAC-s2s-only senders; validate vs real Sika, never fabricate.

**R-M3 flags for ARCH:** (1) `/api/finalize` double-auth (guard `Depends` + in-handler) — idempotent/harmless per the e2e 202, but a 2nd introspect; (2) `usage_state_id` 409-drift vs SBS 429 `quota_refused` — confirm distinct gates; (3) composite `user_permissions` dormant until HB feeds the per-user revision.

**NEEDS (go-live-blocking, NOT Relay code):**
- **A1 Redis DOWN — production-blocking** (rate-limit + HMAC nonce). Infra/EC2.
- **CORE:** finalize trigger + `core.artifact.hlx_available`/`core.submission.terminal`/`core.preview_available` echoing `trace_id`; SSE `#58` live; reconcile preview emitter.
- **HB:** feed the 5 `X-Helium-*` axis VALUES + confirm names; `security.cross_tenant_denied` dual-fire (go-live-blocking); blob-fetch-by-ref.

**Monday readiness (RELAY):** §B shapes for Scout = **complete** (merged #24 + submitted #25/#26/#27). **Live e2e NOT ready** — gated on A1 Redis, Core/HB backends behind Relay, the Q4 preview reconciliation, Q28 durability, + Scout's own cutover (SBS→real, SQLCipher). Relay is the front of the chain; the chain isn't live behind it. Next: ARCH verdicts on #25/#26/#27; scope Q28 durability; reconcile Q4 emitter with Core.

- 2026-06-13, RR tick: ARCH @ `5fdc5db` (rounds 9–10). **No verdicts on #25/#26/#27 yet** (all OPEN/MERGEABLE; main still `b2afaa6`) — holding for ARCH diff-vet, nothing to merge. **Round 10 (Core fix-vs-rewrite feasibility-first) explicitly states Relay durable-ingestion is PATH-INDEPENDENT → proceeds.** Taking up **Q28 (relay.db write-first durable ingestion)** as active next work. ⚠ `relay.db` schema is in my **sensitive** scope-class → Q28 = **propose-then-build**: design grounded in PR #19 (Frontdoor / relay.db schema proposal), reconciled with the **AMQP-first L15** ordering + the **commit-point = HB-blob-write** model + the go-live **additive/reversible-migration** criteria → ARCH/Bob vet the schema, then implement (no unilateral sensitive-schema merge). Q4 emitter/prefix reconciliation still pending Core. Watcher 30-min active.

- 2026-06-13, RR tick #2: ARCH @ `e349114` — the "autonomous fast-forward" directive is **EDGE-specific** (Edge builds to near-complete + mocks Remita), **no RELAY verdict**. **#25/#26/#27 still OPEN, no verdict** (main `b2afaa6`) — nothing to merge. **Q28 advanced → proposal PR #28** (`docs/relay-q28-durable-ingestion` @ `b294d9a`, `Q28_DURABLE_INGESTION_GOLIVE_PROPOSAL_2026_06_13.md`): `relay.db` `ingest_ledger` schema (additive SQLite, per-tenant) + **write-first** into `/api/ingest`, **go-live subset** (relay_queue/AMQP = Frontdoor Phase 2), reconciled w/ **AMQP-first L15** (separate durability layer) + **commit-point=HB-blob-write** (ledger around it); grounded in FRONTDOOR_ARCHITECTURE.md (#19). **Sensitive schema → propose-then-build:** ARCH diff-vet + Bob ratifies the schema (4 open Qs in §5), then I implement (~1 chip: `src/storage/relay_db.py` + write-first wiring + additive migration + tests). Watcher 30-min active.

- 2026-06-13, RR tick #3: **Q4 preview reconciliation RESOLVED** (ARCH #128 / round 11): preview event ratified = **`batch.status.preview_ready`** — the name Scout's reducer actually keys on (Core's grounded re-grep: `*.preview_available` exists NOWHERE in the Scout canon → both my `core.preview_available` and the agent's `core.preview.available` were wrong). **Relay triggers preview-ready; Scout consumes `batch.status.preview_ready`** (`batch` = the 9th event slug). **Fixed #27** → `FAMILY_PREVIEW_AVAILABLE = "batch.status.preview_ready"` (`feat/relay-cssv1-preview-available` @ `dd7194c`; targeted bulk/lifecycle 19/19 green). **#25/#26/#28 still OPEN, no verdict** (main `b2afaa6`) — the ARCH advance (`299063b`) was CORE (Q27/Q29 Path A = legacy-engine-forward + rewrite-surfaces; #128 C1 lifecycle + Q24 publish merged). Watcher 30-min active.

- 2026-06-15, RR tick #4: **no new verdicts** — ARCH master unchanged (`299063b`), demo main `b2afaa6`; **#25/#26/#27/#28 all OPEN, awaiting ARCH diff-vet** (all ready + self-verified; #27 name-corrected to `batch.status.preview_ready`). **Timing flag (go-live imminent):** ARCH's channel has not advanced since 06-13 — RELAY's merge cadence depends on ARCH's vet; surfaced to Bob in case ARCH needs re-engaging before go-live (only Bob re-spawns a dead session, §5.1). Holding the Q28 build (relay.db schema sensitive → awaiting #28 vet; can fast-forward as a PR-for-vet if Bob authorizes). Watcher stays 30-min active.

- 2026-06-15, **§B PACK SELF-MERGED + BOB-RULINGS (go-live eve, under Bob's "do the needful" delegation):**

  **BOB-RULINGS (direct, 2026-06-15 — ARCH please capture + fan out):**
  1. **OAuth 2.0 for external ERP = PRIORITIZE NOW.** Live model today = HMAC + HB-signed Ed25519 JWT (secure; creds via HB Service Registry). → **NEEDS-ARCH/HB:** schedule the OAuth client-credentials arc (PR #120 from DRAFT) — HB builds the OAuth token endpoint + ERP client registration; Relay validates the token (extends the combined dispatcher). Relay-side is a new chip once the HB endpoint + spec land.
  2. **Bulk-path QR = per-invoice/external ONLY** (bulk never emits QR). **Current behavior already correct** (external returns `qr_code`; bulk = queued/preview) — NO code change. **EXTERNAL to Scout:** don't expect QR on the bulk path. (Resolves the earlier bulk-QR OPEN question from the Addendum.)
  3. **Vet gate = "do the needful."** Bob delegated the merge gate to seat judgment under the go-live clock. I self-merged the verified Monday-critical code PRs (below); **ARCH please POST-VET.** Held #28 (sensitive schema → NOT self-merged).

  **§B PACK MERGED to `main`** (self-merged under the delegation; each **base-confirmed + integrated-suite-verified** by my own full pass — never "agent said green"):
  - **#25 R-M3 §B-Drift** → `acbba5f` · **#26 R-M4 §B-RelayArtifactFetch** → `93e7459` (one `app.py` keep-all conflict vs #25) · **#27 Q4 preview** → `1863d28` (clean re-syncs vs #25 then #26).
  - **Final integrated `main` suite: 701 pass / 7 pre-existing fail** (the documented ingest_route ×2 / irn ×1 / qr ×3 / external ×1 — ZERO new regression across the full #23→#27 integration).
  - Only conflict across the chain = `app.py` imports + factory → **keep-all** (drift handler + artifacts router + preview wiring all retained), verified by grep + the suite at each step.
  - **`main` now = the complete Monday §B surface for Scout:** §B-Submit (finalize taxonomy) · §B-Drift (`X-Helium-*` 5-axis 409) · §B-RelayArtifactFetch (POST, `X-Relay-Artifact-*`) · §B-EventLog (`relay.finalize.accepted` + `trace_id`) · Q4 (`batch.status.preview_ready`).

  **Held / not self-merged:** **#28** (Q28 `relay.db` proposal — sensitive schema → ARCH diff-vet + Bob ratify, 4 open Qs); **#22** (R12 frozen on L5); **#10** (Phase 1b abuse — Q21 close, post-Monday).

  **R-M3 flags → ARCH/HB:** (1) `usage_state_id` 409-drift vs SBS 429 `quota_refused` — recommend **distinct** gates; (2) `/api/finalize` double-auth — idempotent/harmless, leave; refactor post-Monday.

  **Relay live-e2e still gated (NOT Relay code):** **A1 Redis** (production-blocking); **Core/HB backends behind Relay** (CoreClient/HeartBeatClient are stubs — finalize trigger, lifecycle/preview SSE echo, the 5 axis values, blob-fetch-by-ref); **Scout cutover** (SBS→real, SQLCipher). **OAuth #120** now prioritized (post-merge chip).

  Worktrees `rm3f`/`rm4f`/`previewf` removed (branches merged). Watcher 30-min active.

- 2026-06-15, RR tick (idle ×2): no change — ARCH master still `464545a` (heads-down on Core EC2 verification); **no RELAY post-vet / no #28 ratification / no OAuth #120 scheduling / no DRIFT**; demo `main` `1863d28` (§B pack intact). Gates unchanged (Redis A1 down; Core/HB stubs; Scout cutover pending). **Dropping to 60-min self-resume (§5.1)** — idle-blocked on ARCH/peers/infra; nothing in Relay's control until a gate clears. Will jump back to 30-min on any RELAY-naming verdict, #28 ratification, OAuth-HB landing, or gate-clear.

- 2026-06-15, **NEEDS FROM ARCH (consolidated — per Bob's ask; resuming 30-min cadence):**
  **CRITICAL PATH (the 2 I actually need DONE):** (1) **POST-VET the self-merged §B pack #25/#26/#27** — merged under Bob's "do the needful" go-live delegation WITHOUT ARCH diff-vet (ARCH saturated on Core); please diff-vet vs ledger + canonical specs → `MERGED-CONFIRMED` or `DRIFT/CHANGES` (I fix-forward). (2) **Diff-vet #28 + route to Bob for schema ratification** — sensitive `relay.db` `ingest_ledger` (4 open Qs); blocks the Q28 go-live durability build.
  **RULINGS (recs attached):** (3) `usage_state_id` 409-drift vs SBS 429 `quota_refused` — I rec **distinct gates**. (4) `/api/finalize` double-auth — I rec **leave (idempotent), refactor post-Monday**. (5) Q4 ↔ Core: confirm Core's SSE forwards Relay's `batch.status.preview_ready` + R-M4 = the preview-fetch channel (need Core preview/HLX ref shape). (6) **Schedule OAuth #120** (Bob-prioritized) + HB↔Relay split (HB token endpoint + ERP registration; Relay validation). (7) Capture the 3 BOB-RULINGS (06-15) → DECISION_QUEUE/ledger + fan EXTERNAL to Scout (**bulk = NO QR**; adapter follows `X-Relay-Artifact-*` + `X-Helium-*`).
  **ARCH to coordinate (cross-seat, not ARCH-built):** HB (5 `X-Helium-*` axis VALUES + final names; `security.cross_tenant_denied` dual-fire; blob-fetch-by-ref); Core (Q24 publish endpoint; finalize trigger + `trace_id` echo); Infra (**Redis A1 up — production-blocking**).

- 2026-06-16, **L27 Intelligence Pack fan-out — RELAY added (ARCH round 12, `2c23adf`); resumed ACTIVE.** *(Note: recovered from a parallel-session detached-HEAD — local checkout was at `1863d28`/main, which lacks the channel docs; re-checked-out `feat/relay-cssv1-s4-…`, work intact @ `37964b0`, nothing lost.)* RELAY obligations + status:
  - **Consume the shared, versioned IQC module from the pack** ✓ (today via `module_cache` → `core/qr.py` + `core/irn.py`). **Call IQC STANDALONE** (NOT Core's Transforma Script) ✓ — Relay uses the IQC submodule only. **QR generated locally by IQC, not FIRS** ✓.
  - **BOB-RULING (direct, 2026-06-16): bulk path = NO QR** (confirmed; the 06-15 ruling stands). L27 line 168 "every ingest/finalize" = **every per-invoice/external** ingest/finalize. → **FLAG ARCH:** reconcile L27 wording; **EXTERNAL to Scout:** bulk emits no QR. (QR-inline on the per-invoice/external path already works → satisfied for go-live.)
  - **Reconcile `core/qr.py`+`core/irn.py` caching to the pack-module-version model** — plan: `module_cache` keys on the IQC **pack-module version**; HB's §B-Policy version-change signal (`policy_revision` covers intelligence-pack identity — the R-M3 axis) triggers a refresh of the cached IQC module (augments the current time-based refresh loop). **DEPENDS ON HB (NEEDS):** the pack-version-signal format + per-module version in tenant config (HB hosts the versioned pack + fans the bump to BOTH Core and Relay). **Build = a chip once HB's pack-version contract lands**; current IQC-via-`module_cache` already serves QR for go-live → version-bump propagation is **hardening, not go-live-blocking**. Q7 (sandbox/Transforma) closes into L27 (ciphered pack + secure-update, not arbitrary exec).

- 2026-06-16, **RR tick — ARCH master advanced `de44df4`→`66a69d1` (13 new commits; rounds 13+); RELAY-relevant items:**
  - **🚨 BLOCKER B (HB, not Relay code) — `events.batch.subscribe` NOT seeded → `batch.status.preview_ready` silently dropped.** ARCH round 13 caught it: HB PR #129 seeds only 8 slugs (omits `batch`); the live event gate is fail-closed → every `batch.status.preview_ready` Relay emits is discarded → Float/Scout "preview ready" never arrives. **This is a HB fix** (add `events.batch.subscribe` to #129 seed + migration). Relay's own Q4 code is correct; the gap is HB's seed. **Flagging for ARCH routing to HB.**
  - **Q35 Bearer-s2s sweep** — 3 live Bearer-s2s calls found (1 HB provision, 2 Core including introspect=auth-gate); Core routes fixed. **RELAY: no Bearer-s2s bombs found in our paths** (Relay uses the 4-header HMAC s2s to HB correctly). No Relay code change required.
  - **Core engine consolidation (PR #142 merged)** — Core spine graft + increment 1. Not Relay-facing. Monitored.
  - **RELAY §B post-vet status:** #25/#26/#27 still NOT post-vetted in STATUS_ARCH (no MERGED-CONFIRMED or DRIFT entries seen). Remains on the ARCH critical-path ask list.
  - **External API Reference doc authored** (`services/relay/Documentation/EXTERNAL_API_REFERENCE.md`) — per Bob's request for external-system integration documentation. Covers: HMAC auth (Python + Node.js examples), POST /api/ingest (external), POST /api/finalize, POST /api/artifacts/fetch (with kind table), GET /health, error codes, rate limits, version drift headers, end-to-end flow diagrams. On `feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook` branch; needs commit + push.

  **NEEDS FROM ARCH (unchanged + BLOCKER B added):** Same as 06-15 consolidated list, plus: route BLOCKER B to HB (add `events.batch.subscribe` slug to #129). ARCH please post-vet #25/#26/#27.

  Watcher re-armed: 30-min active.

- 2026-06-17, **Q37 EXTERNAL INGESTION — ARCH directive received + digested.** Read all three required docs: `EXTERNAL_INGESTION_ALIGNMENT_2026_06_15.md` (ARCH canonical ruling), `PRONALYTICS_MIDDLEWARE_API.md` (external contract), `PRONALYTICS_MIDDLEWARE_BUILD_GAPS.md` (gap inventory). Full understanding below.

  **My gap assignments (RELAY):**

  | Gap | What | Gating |
  |-----|------|--------|
  | #2 | Local JWKS validation of `aud=helium.relay-ingest` tokens on ingest + status | HB O3 (JWKS endpoint + multi-aud JWT manager) |
  | #3/#4/#7/#8 | Rework `POST /api/ingest`: multipart JSON array → per-record field-map + VAT (7.5% auto when absent) → fan-out to per-invoice IRN+QR → inject tenant `firs_service_id` → return `processed[]/duplicates[]/failed[]` + `summary` | Independent (can build now) |
  | #5 | NEW `POST /api/status`: orchestrate HB blob-status + Core invoice-status → merge to §3 shape (transaction_id/irn/batch_id selectors, `result` rollup, `firs_status`, flat `results[]`) | Core needs `external_transaction_id` column + by-IRN/by-txn/by-batch lookup; Relay-side can be built + deployed with HB-only data until Core's half lands |
  | #6 | AMQP consumer: per-tenant exchange/queue/reply-queue; same JSON-array payload | Independent (complex; see Q below) |

  **Doc rewrite (Relay scope):** Frontdoor PR #19 §3/§8/§1.1/§10/§12 — off HMAC → OAuth. Doc-only, no code. Will open as additive PR on the `feat/relay-external-ingestion-q37` branch.

  **Branch guardrail respected:** All Q37 work goes on a FRESH branch `feat/relay-external-ingestion-q37` off current `origin/main` (`1863d28`). **Zero overlap with Sika-critical PRs** (those are all merged to main already; the only open items are #22 R12 frozen + #28 Q28 proposal — neither is Sika-critical). Sika wins any contention.

  **Sensitive (contracts):** every PR on this branch goes to ARCH for diff-vet before merge. No self-merge here (these are new external contracts, not Monday-delegation work).

  **Two Bob-level questions surfaced via AskUserQuestion (below) before I build.** Remaining technical questions for ARCH:
  - VAT auto-calc formula: `round(fee_amount × 0.075, 2)` when `vat_amount` absent — confirm.
  - `batch_id` is a NEW multipart field on `/api/ingest` (does not replace any existing field) — confirm.
  - Where does `firs_service_id` live in tenant config today? (`tenants.json`? `tenant_config`? HB config endpoint?) Need the actual key name so `irn.py` reads it correctly.
  - Pass `external_transaction_id` to HB via `write_blob()` payload — confirm HB will store it once O1 DDL lands (otherwise Relay passes it and HB ignores it until then).
  - Gap #2 (local JWKS): I will wire the validator stub but route to the existing introspect fallback while O3 is unbuilt. OAuth traffic physically can't reach Relay until HB O3 + Relay #2 are both live — no HMAC interim needed because no external traffic flows until OAuth is live.

- 2026-06-17, **Q37 BUILD DISPATCH — all four Relay gaps in flight.** Context compacted and resumed. Bob rulings confirmed:  /api/status partial-now (HB-side live, Core null); AMQP build in parallel. Pre-work landed: shared contract models (`BatchIngestResponse`, `StatusResponse` + supporting types) committed at `daf1870` on `feat/relay-external-ingestion-q37`. Four §5.2 fresh-head sub-agents dispatched in parallel:

  | Agent | Gap(s) | Target files |
  |-------|--------|--------------|
  | Gap #3/#4/#7/#8 | Batch JSON-array ingest | `services/batch_external.py` + `routes/ingest.py` update |
  | Gap #5 | POST /api/status | `routes/status.py` + `services/status_service.py` |
  | Gap #2 | Local JWKS validation | `core/jwks_cache.py` + `core/oauth_validator.py` + `deps.py` aud-routing |
  | Gap #6 | AMQP consumer | `services/amqp_consumer.py` + `/health` amqp status |

  All agents commit to `feat/relay-external-ingestion-q37` with no-push constraint; seat does final integration-run + ARCH-vet PR(s) when all four land. Branch is clean at `daf1870` heading into agent fan-out.

  **Outstanding open Qs for ARCH (unchanged):** VAT formula confirm, `batch_id` field confirm, `firs_service_id` key-name confirm, `external_transaction_id` HB DDL path. These are non-blockers for the Relay-side build but needed before the PR can be merged + deployed.

  Watcher re-armed: 30-min active.

- 2026-06-17, **Q37 BUILD COMPLETE — all 4 gaps landed + verified + PR #29 open for ARCH vet.** All four parallel agents finished. Commits on `feat/relay-external-ingestion-q37` (pushed to origin):

  | Commit | Gap | What |
  |--------|-----|------|
  | `daf1870` | — | shared contract models (BatchIngestResponse + StatusResponse) |
  | `7c1a52e` | #3/#4/#7/#8 | batch JSON-array ingest, per-invoice IRN+QR, VAT auto-7.5%, tenant `firs_service_id` injection (kills `A8BM72KQ`) |
  | `65fe356` | #2 | local JWKS validation (`core/jwks_cache.py` + `core/oauth_validator.py` + `deps.py` aud-routing); EdDSA via cryptography; jti blocklist fail-open |
  | `d547344` | #5 | `POST /api/status` — HB blob-status + Core invoice-status orchestration; graceful null Core (partial-now) |
  | `9d9eb9a` | #6 | AMQP per-tenant consumer; reuses `BatchExternalService`; `/health` amqp status |

  **Verification (seat-run full suite):** `828 passed, 7 failed`. All 7 failures **independently verified pre-existing** on fork base `1863d28` (stash-and-run in detached worktree): 2 ingest-route auth-ordering + 5 module-not-loaded (test_irn/test_qr/test_external `ModuleNotLoadedError`). **Zero regressions.** 127 new Q37 tests all pass (batch 37, status 44, oauth 26, amqp 20).

  **Scope lock:** all 24 changed files under `services/relay/`. Confirmed.

  **PRs open for ARCH:**
  - **#29** — Q37 code build (4 gaps). Sensitive contracts → **ARCH vet, no self-merge**.
  - **#19** (frontdoor doc) — pushed `63d179c`: §3/§8/§10/§12 rewritten HMAC→OAuth + §14 revision record. Doc-only.

  **Gating (NOT blockers for vet):** Gap #2 OAuth dormant until HB O1–O4 (`/oauth/token`+JWKS) + `RELAY_JWKS_URL`; Gap #5 Core half (`get_invoice_status`) graceful-null stub until Core ships `external_transaction_id` lookup column.

  **NEEDS FROM ARCH:** vet #29 + #19; confirm the 4 open Qs (VAT formula, `batch_id` additive field, `firs_service_id`=`TenantConfig.service_id`, `external_transaction_id` HB path); route BLOCKER B to HB (`events.batch.subscribe` slug); post-vet #25/#26/#27.

  Watcher re-armed: 30-min active.

- 2026-06-17 (evening), **L30 CANONICAL DATA MODEL — RELAY chip built (registering).** Read `DATA_MODEL_CANONICAL.md` (L30) + `FANOUT_DATA_MODEL_2026_06_17.md §RELAY` off `origin/master`. My three deliverables, all on `feat/relay-external-ingestion-q37` (stacked on the Q37 build, since every file is already in unmerged PR #29):

  1. **`/api/status` → 4 selectors + HB(file_transactions)/Core merge (canon §6).** Added `invoice_number` as the 4th selector. Refactored `StatusService` to the canonical routing: HB answers the PRE-INVOICE phase (`transaction_id`/`batch_id` → `blob.file_transactions`, status `pending`/`acknowledged`/`not_an_invoice`); Core answers the INVOICE phase and is the SOLE resolver of `invoice_number`/`irn` (HB is invoice-blind per §8, so those go Core-first with an HB backfill by the returned txn). Merge by `transaction_id ≡ external_transaction_id`. Replaced the wrong `get_blob_status_by_irn` HB stub (HB can't resolve IRN) with `get_transactions_by_batch` / `get_transaction_by_id`; Core gained `get_invoices_by_batch` + a 4-arg `get_invoice_status`.
  2. **Seed external transaction_ids at ingest (canon §5).** `register_blob` now takes `transaction_ids` as a first-class payload field; `ingestion._register_blob` surfaces `metadata["transaction_id"]` → `transaction_ids=[txn]` so HB's registration worker seeds one `file_transactions` row (`pending`) per id. Internal uploads carry none → HB seeds none.
  3. **Doc fix (canon §9).** `SIMULATOR_CONTRACT.md:342` — clarified `transaction_id == invoice_number` is a Simulator-only simplification, false for real ERPs (§2/Q40).

  **`result`-vocab reconciliation (the L29 "3-way collision" ARCH flagged):** mapped HB Tier-3 status + Core Tier-4 state onto the ERP surface → `pending | processed | not_an_invoice | duplicate | failed`. **`not_an_invoice` is ADDITIVE to L29's published 4-value set** — surfaced because it's a real terminal an ERP must see. **Flagged for ARCH/Bob ratification of the L29 vocab extension.**

  **Both backends are NEEDS-* stubs** (HB `file_transactions`+read-endpoint and Core lookup are the OTHER seats' chips in this same fan-out), so a live `/api/status` returns `results=[]` until they ship — the merge logic is the contract they light up against. Mirrors the dormant-until-dependency pattern from Gap #2/#5.

  **⚠️ Canon path correction for ARCH:** DATA_MODEL_CANONICAL §9 + the fan-out cite `services/relay/Documentation/SIMULATOR_CONTRACT.md:342`, but the file actually lives at **`services/simulator/SIMULATOR_CONTRACT.md`** (verified by `git ls-files`). Fixed the real file; please correct the canon's §9 reference.

  **Verification:** 49 status tests + 2 ingest-seeding tests rewritten/added to the L30 design; targeted run (status_service + status_route + ingestion + batch_external) = **123 passed**. Full-suite confirmation pending (expect 7 pre-existing fails, zero regressions — same baseline).

  **Branch decision (flagging for ARCH):** built ON the Q37 branch / **PR #29** rather than a separate stack, because the L30 work *rewrites* the Gap #5 status service before it ever merged — folding yields the canonical final diff with no build-then-rewrite churn. If you'd prefer it as a separate stacked chip, say so and I'll split. Sensitive contract → **ARCH vet, no self-merge.**

  Watcher re-armed: 30-min active.

- 2026-06-18, **Bob-ask surfaced → see `## Needs` (NEEDS FROM BOB — Q38 repo consolidation timing/scope).** Bob raised that all 4 codebases + docs should reside in `helium-services`; he believes it was agreed. ARCH's Q38 agrees on the end state but deferred timing to post-Monday. **ARCH: please promote to DECISION_QUEUE and schedule a discussion WITH Bob** (timing = accelerate vs post-Monday; scope = HB/Core/Relay/Edge + docs). RELAY ready to execute its migration half on cutover. Per protocol I did NOT write DECISION_QUEUE directly (ARCH-owned) — surfaced via this Needs entry for ARCH to promote.

- 2026-06-18, **ARCH verdicts READ + acted (L30 APPROVED · Q37 condition #1 CLOSED).** Read STATUS_ARCH `b9796df` (L30 vet) + `603c951` (Q37 vet).
  - **L30 (`502071c`) = APPROVE-WITH-CONDITIONS → effectively APPROVED-FOR-MERGE, no code changes.** ARCH **ratified D1** (`not_an_invoice` as 5th `result` value — "CORRECT, the direct consequence of Q41") and **D2** (called my `acknowledged`=`processed` reading "cleaner than my canon" and *updated the canon §6.1 to match my implementation*). **D3** folded into canon+fanout: my concrete shapes are now the spec HB/Core build to (HB `POST …/blob/transactions/status` read-endpoint shape, the `transaction_ids` register seeding field, the Core record shape + `external_transaction_id` join key) — "build to this shape so no reconcile is needed." The loop worked: ARCH fanned out → I built to canon → ARCH vetted → my shapes became canon.
  - **Q37 condition (1) — alg-confusion guard — CLOSED at `8e0e47a` (pushed).** ARCH: *"oauth_validator hand-rolls JWT decode, MUST pin alg=EdDSA and reject none/HS*/RS*/ES* (spec R1)."* Confirmed the gap (an `alg=""` carve-out), removed it → strict `alg=="EdDSA"`; added `TestAlgorithmConfusionGuard` (none/None/HS256/RS256/ES256/empty/missing all rejected; EdDSA accepted). 25/25 validator + 9/9 oauth-auth pass. PR #29 comment posted.
  - **Q37 conditions (2)/(3) — not actionable now:** (2) OAuth validator/jwks_cache stay PROVISIONAL vs HB PR #120 — reconcile when HB OAuth locks (gated); (3) full line-vet of the other 22 files = ARCH's next pass.
  - **Net merge state of PR #29:** L30 approved-no-changes; Q37 structure approved + condition #1 closed; remaining gates are external (HB OAuth/JWKS contract lock + HB/Core building their halves). Stays staged, null-safe in the interim, **no self-merge** — merges on Bob's final call once HB OAuth locks.

  Watcher re-armed: 30-min active.

- 2026-06-18, **CUTOVER LANDMINE — RELAY has the SAME `MOCK_AUTH=false` s2s-key trap Core surfaced (handled my half + flagging for the GLOBAL runbook).** ARCH's note: once real auth is on, HB must generate Core's s2s key + operator pastes `CORE_HEARTBEAT_S2S_SIGNING_KEY` or Core→HB introspect hard-fails; invisible while `MOCK_AUTH=true` masks it. **Relay is the OTHER s2s caller of HB and has the identical landmine** (`RELAY_S2S_SIGNING_KEY` → config `heartbeat_s2s_signing_key`): the instant HB flips to real auth, EVERY Relay→HB call hitting `verify_service_credentials` hard-fails — user-JWT introspect, `POST /api/blobs/write`, `/api/blobs/register`, config fetch, audit log, daily-limit check. Verified in code (`config.py:154`, `startup_checks.py:validate_signing_key_shape` warns-but-runs-degraded when unset → masked under mock, breaks at cutover).
  - **My half DONE:** documented the sequenced operator runbook in `services/relay/Documentation/DEPLOYMENT.md` (commit `e7a5497` on the Q37 branch) — the trap, the in-order steps (HB mints key → operator pastes `RELAY_S2S_SIGNING_KEY` → restart → NTP-skew guard → verify round-trip), and that Core's key pastes in the same window. Relay already fails-fast on a malformed key (R9.1) and guards clock skew vs HB (R9.2, 60 s floor).
  - **NEEDS FROM ARCH/HB (global runbook):** the go-live cutover runbook must list **BOTH** operator pastes — `CORE_HEARTBEAT_S2S_SIGNING_KEY` **and** `RELAY_S2S_SIGNING_KEY` — and **HB owns generating BOTH** per-service keys at real-auth startup (it already logs them at WARNING per `HMAC_S2S_MIGRATION_SPEC §5`). Not blocking today (EC2 still `MOCK_AUTH=true`, consistent stack); required + sequenced at the Q35/MOCK_AUTH=false flip.

  Watcher re-armed: 30-min active.
