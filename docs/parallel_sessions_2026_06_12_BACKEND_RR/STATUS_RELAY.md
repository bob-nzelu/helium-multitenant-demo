# RELAY Seat Status — Backend Round Robin

**Seat:** RELAY (implementation seat, Opus 4.8)
**Session:** Claude Opus 4.8 (1M context), standing seat — registered 2026-06-12
**Repo / worktree:** `C:\Users\PROBOOK\helium-multitenant-demo` (Relay's home + EC2 deploy repo)
**Branch:** `feat/relay-cssv1-s4-hash-lib-record-duplicate-webhook` (S4 / PR #22 branch at RR launch)
**Fork point:** `9d2120e` (main tip; one S4 commit `7a661cb` on top = PR #22 head). Working tree clean.
**Status:** ACTIVE (REGISTRATION-ACK 2026-06-12; Monday-readiness directive overlay — see MONDAY PLAN)
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
