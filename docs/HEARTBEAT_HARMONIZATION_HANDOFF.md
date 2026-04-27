# HeartBeat Harmonization + Consolidation + Upgrade — Session Handoff

**Created:** 17 Apr 2026 (end of HEARTBEAT_AUTH session)
**Status:** Blocker for BD-031, BD-009, Debt #1–#7, and any further HeartBeat work
**For:** Dedicated multi-session program. **Not** a one-session task.

---

## Absolute Paths — Single Source of Truth

Every path below is absolute. Do not abbreviate or relativize.

### Codebases

| Role | Absolute path |
|---|---|
| **HeartBeat — OLD (Services tree, git history root)** | `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat` |
| **HeartBeat — NEW (AWS deploy, strict superset of OLD)** | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\services\heartbeat` |
| **helium-multitenant-demo repo root** | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo` |
| **Float (consumer, has partial device_id fingerprinting)** | `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Float\App` |
| **Transforma Reader (consumer)** | `C:\Users\PROBOOK\OneDrive\WestMetro\Transforma\Reader` |
| **Float SDK (client lib with `float_id`, ~60 callsites — BD-031 follow-on)** | `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Clients\float\src\sdk` |
| **Helium Frontend Standard (canonical spec)** | `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Clients\docs\standard\HELIUM_FRONTEND_STANDARD.md` |

### Documents to read before any action

| Doc | Absolute path |
|---|---|
| This handoff | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HEARTBEAT_HARMONIZATION_HANDOFF.md` |
| Technical debt log (7 items, linked to GitHub Issues #1–#7) | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\TECHNICAL_DEBT.md` |
| Deployment architecture (canonical reference) | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HELIUM_DEPLOYMENT_ARCHITECTURE.md` |
| Unified auth contract | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\UNIFIED_AUTH_CONTRACT.md` |
| HeartBeat auth session handoff (14 Apr) | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HEARTBEAT_AUTH_SESSION_HANDOFF.md` |
| Helium Frontend Standard (BD-031 context — §3, §16, §23) | `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Clients\docs\standard\HELIUM_FRONTEND_STANDARD.md` |

### Live infrastructure

| Item | Value |
|---|---|
| EC2 host | `13.247.224.147` |
| SSH key | `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\AB Microfinance\helium-key.pem` |
| SSH command | `ssh -i "C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\AB Microfinance\helium-key.pem" ubuntu@13.247.224.147` |
| HeartBeat URL | `http://13.247.224.147:9000` |
| Test harness key (local, used for HMAC auth) | `C:\Users\PROBOOK\.helium\test_harness_key` |

### GitHub

| Item | Value |
|---|---|
| Repo | `https://github.com/bob-nzelu/helium-multitenant-demo` |
| Debt Issues | `https://github.com/bob-nzelu/helium-multitenant-demo/issues?q=is:open+label:debt` |
| Milestones | `before-2nd-tenant`, `before-production` |
| List debt (start every session with this) | `gh issue list --repo bob-nzelu/helium-multitenant-demo --label debt --state open` |

---

## Why This Handoff Exists

At the end of 17 Apr, an old-vs-new audit concluded:

> **NEW is a strict superset of OLD.** 92 files vs 86, 19,375 LOC vs 17,891. Every module in OLD exists in NEW with equal or greater LOC. No regressions. The ~1,484 extra LOC in NEW are all additions from 14 Apr and 17 Apr sessions (mock_auth, devices migration, app_registrations table, test harness, admin stubs, auth.py + pg_auth.py extensions).

**Audit agent result saved at:** (embedded in conversation transcript from 17 Apr session; re-run by reading both trees if needed)

**Therefore** there are two HeartBeat trees drifting — OLD has the long git history, NEW has the AWS-running deployment and the last 4 days of work (devices, app_registrations, test harness, 007 migration, TECHNICAL_DEBT.md, GitHub Issues #1–#7).

Bob's instinct: `Helium\Services\HeartBeat` (OLD) should be canonical.
Audit reality: `Pronalytics\helium-multitenant-demo\services\heartbeat` (NEW) is what's actually running and has more code.

**Any further HeartBeat work (BD-031, BD-009, Debt #1 source_type→app_type, Debt #3 audit logging, Debt #5 device_id, etc.) lands in whichever tree is chosen as canonical. Running work in both trees multiplies the drift.** This handoff is the blocker.

---

## The Strategic Fork — Four Options

| # | Option | Canonical tree | Deploy target | Cost | Recommended? |
|---|---|---|---|---|---|
| A | Services canonical | `Helium\Services\HeartBeat` | demo repo pulls from Services | Port 1,484 LOC from NEW → OLD once | ⚠ Matches instinct; loses no history |
| B | Demo canonical | `helium-multitenant-demo\services\heartbeat` | same repo | Zero porting | Contradicts instinct; handoffs reference Services |
| C | Consolidate — Services survives, demo = deploy wrapper | `Helium\Services\HeartBeat` | demo repo has only Dockerfile/compose + configs, imports/vendors Services | Port NEW→OLD once + rewire demo build | ✅ **Clean end state, honors instinct** |
| D | Git submodule (Services as submodule of demo) | Services | demo submodule | Most tooling work; fragile on Windows | Usually overkill |

### Recommended: **Option C**

- Honors the "Services is canonical" instinct.
- Preserves all 1,484 LOC of NEW work (nothing thrown away).
- Collapses drift to zero — one tree for code, one tree for deploy config.
- After consolidation, BD-031 + BD-009 + all debt items land in one place with one migration.
- The demo repo becomes pure deployment artefact: `docker-compose.yml`, `config/schemas/*.sql`, `config/tenants.json`, Dockerfile context path → Services.

**But** this is a 3–5 session program, not one session. See phases below.

---

## Multi-Phase Plan

### Phase 0 — Decide canonical (Session 1, ~1 hour)

**Goal:** Pick Option A / B / C / D. No code changes.

**Input:** This handoff + the audit findings in conversation history.

**Output:**
- Decision committed to `TECHNICAL_DEBT.md` as item #0 (meta-decision)
- A new handoff for Phase 1 with concrete merge plan

**Do NOT do in this session:** any code move, any rename, any schema change.

---

### Phase 1 — Port the delta (Session 2, ~1 day)

**Goal:** Move the 14–17 Apr additions from NEW → OLD (assuming Option C chosen).

**Files/changes to port from NEW to OLD:**

1. **`services/heartbeat/databases/migrations/auth/007_devices_and_app_registrations.sql`** (58 lines) — new migration creating `auth.devices`, adding `auth.sessions.device_id`, creating `auth.app_registrations`.
2. **`services/heartbeat/src/database/pg_auth.py`** — +179 LOC: device CRUD, `get_device_active_session`, `revoke_oldest_session`, `get_app_registration`, `create_app_registration`, `update_app_registration_seen`, `get_next_source_sequence`. `get_tenant_max_sessions` default changed from 1 → 3.
3. **`services/heartbeat/src/handlers/auth_handler.py`** — login() accepts `device_id`, evicts oldest on 3-session cap, replaces existing session on same device. JWT now includes `device_id` claim. Introspect returns `device_id`. Step-up and refresh preserve `device_id`.
4. **`services/heartbeat/src/api/auth.py`** — +231 LOC: `device_id` field on `LoginRequest`/`LoginResponse`, refresh alias `POST /api/auth/refresh` (Reader compat), new endpoints `POST /api/auth/register-device`, `POST /api/auth/register-app`, `GET /api/auth/devices`, `POST /api/auth/devices/{id}/revoke`, `GET /api/auth/sessions`.
5. **`services/heartbeat/src/handlers/registration_handler.py`** (239 LOC, new file) — `register_app()` business logic.
6. **`services/heartbeat/src/auth/test_harness_manager.py`** (107 LOC, new file) — HMAC validation for `X-Test-Harness-Signature` header. Constant-time comparison via `hmac.compare_digest()`.
7. **`services/heartbeat/src/api/test_harness/endpoints.py`** (324 LOC, new file) — `/api/test/*` routes: auth/reset, auth/create-user, data/seed, data/clear, sse/emit, config/override, state. All audit-logged.
8. **`services/heartbeat/src/api/test_harness/__init__.py`** (new).
9. **`services/heartbeat/src/api/admin.py`** (61 LOC, new file) — update engine stubs returning 501 except history ([]).
10. **`services/heartbeat/src/api/mock_auth.py`** (310 LOC) — mock auth router, activated by `HEARTBEAT_MOCK_AUTH=true`.
11. **`services/heartbeat/src/main.py`** — conditional router registration block; gate is `HEARTBEAT_DEMO_MODE=true` AND `HEARTBEAT_TEST_HARNESS_KEY_HASH` set. (Earlier drafts of this doc said `HEARTBEAT_TEST_HARNESS_ENABLED` — that name was never wired into the code.)

**Deploy artefacts to keep in the demo repo (not ported):**

- `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docker-compose.yml` — deploy wiring only.
- `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\config\schemas\004_devices.sql` — postgres initdb seed (mirror of migration 007; postgres loads this on first volume creation).
- `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\config\tenants.json` — demo tenant credentials.
- `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\*` — handoffs, deployment architecture, this file.

**Rewire the demo repo's Dockerfile to build from the Services tree** (or copy Services into the Docker build context at image build time).

**Test:** Run OLD's existing test suite against the ported code. Bring up `docker-compose` locally, verify real auth works identically to EC2.

---

### Phase 2 — Redeploy EC2 from canonical (Session 3, ~2 hours)

**Goal:** EC2 pulls from the new canonical tree, not the demo snapshot.

- SSH to EC2, `cd helium-multitenant-demo`, `git pull` picks up the rewired compose
- `sudo docker compose build heartbeat && sudo docker compose up -d heartbeat`
- Run the verification curls from `HEARTBEAT_AUTH_SESSION_HANDOFF.md` §Verification Checklist (login, device_id in JWT, 3-session eviction, register-app, test harness, refresh alias).
- If all green: EC2 is now running from canonical. Tag this as release v2.1.0.

**Migrate GitHub Issues #1–#7 to the Services repo if a separate one exists**, or leave on the demo repo with a note that Services is canonical code but Issues remain here. Update `TECHNICAL_DEBT.md` to point at wherever they live.

---

### Phase 3 — Execute BD-031 (Session 4, ~1 day)

**Original brief** (from Bob's message, 17 Apr — preserved verbatim for traceability):

> **BD-031: Rename float_id → app_id**
>
> Table `float_instances` → `app_instances`
> Column/field `float_id` → `app_id` everywhere
> Config endpoint `/api/v1/config/{float_id}` → `/api/v1/config/{app_id}`
> Registration response field `float_id` → `app_id`
>
> Live code:
> - `src/api/internal/tenant_config.py` — endpoint definition, registration logic, config fetch
> - `src/database/connection.py` — identity fields written to blob records
> - `src/api/internal/blobs.py` — blob metadata uses `float_id`
> - `databases/` — schema SQL files
>
> Migration file: `databases/migrations/config/007_rename_float_to_app.sql` (or next sequence number after Phase 1 ports).
>
> Float SDK at `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Clients\float\src\sdk\` (~60 callsites) is a separate session — do not touch.

**Harmonization note added by this handoff:**
BD-031's `app_id` + `app_instances` collides conceptually with NEW's existing `source_id` + `app_registrations` (from 17 Apr session). Before coding BD-031, decide:
- Is `app_id` the same thing as `source_id`? (Probably yes — both identify a registered app instance.)
- Do `app_instances` and `app_registrations` become one table or two?
- Does Debt #1 (`source_type` → `app_type`) land in the same migration as BD-031?

Recommended: merge all three (BD-031 + Debt #1 + unify tables) into one migration `008_app_identity_harmonization.sql`. One rename wave, not three.

---

### Phase 4 — Execute BD-009 (Session 5, ~half day)

**Original brief** (preserved verbatim):

> **BD-009: Flow determination endpoint**
>
> `POST /api/v1/auth/flow` — called by every frontend at startup, before login.
>
> Request: `{ "app_id": "app-<uuid>", "device_id": "<machine_guid>", "fingerprint_hash": "<sha256>" }`
>
> Response directive ∈ `AUTH_ONLY | JOIN_SESSION | AUTH_AND_REGISTER | ADMIN_GATED`
>
> Logic (in order):
> 1. Look up `app_id` in `app_instances` → if not found, `AUTH_AND_REGISTER`
> 2. If status `SUSPENDED` or `RETIRED` → `ADMIN_GATED`
> 3. Fingerprint drift (MAC or MachineGuid changed) → `ADMIN_GATED`
> 4. Another app on same device has active session + tenant allows cross-app → `JOIN_SESSION`
> 5. Else → `AUTH_ONLY`
>
> Auth: called pre-JWT, MUST NOT require Bearer. Use HMAC-API-key or bootstrap credential. Read existing auth middleware before deciding.

**Harmonization note:**
BD-009 assumes post-BD-031 names (`app_id`, `app_instances`). Runs after Phase 3. Also depends on:
- `fingerprint_hash` — requires Debt #5 (Float/Reader device_id computation) for any real client to actually call it.
- `status` column — `app_instances` may not have this yet (current `auth.app_registrations` has no status field). Add in Phase 3 migration.
- `allow_cross_app_session` — tenant config flag, needs to be added to `config_entries`.

---

### Phase 5 — Resume debt items (multiple sessions)

Order per `TECHNICAL_DEBT.md`:

1. **Debt #3** — Wire audit logging into auth events (P1, before-2nd-tenant). Half day.
2. **Debt #5** — Float/Reader device_id + register-app (P1, before-2nd-tenant). 2 days. Enables BD-009's `fingerprint_hash`.
3. **Debt #2** — SQLite → PostgreSQL migration (P2, before-production). 2–3 days. Bundle Debt #1 and BD-031 naming into this if not done in Phase 3.
4. **Debt #4** — 7-year audit retention (P2, before-production). 1 day. Depends on #3.
5. **Debt #6** — Test coverage for 14–17 Apr additions (P2, before-production). 1 day.
6. **Debt #7** — Real update engine (P3). Multi-day when triggered.

---

## What the Next Session Must Do First (Phase 0, Session 1)

**Read, in order, before any decision or code:**

1. This file: `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HEARTBEAT_HARMONIZATION_HANDOFF.md`
2. `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\TECHNICAL_DEBT.md`
3. `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HELIUM_DEPLOYMENT_ARCHITECTURE.md`
4. `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HEARTBEAT_AUTH_SESSION_HANDOFF.md`
5. Walk the two trees side-by-side:
   - OLD: `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\src`
   - NEW: `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\services\heartbeat\src`
   - Confirm audit's "strict superset" claim independently (spot-check 3–4 files).
6. Read `gh issue list --repo bob-nzelu/helium-multitenant-demo --label debt --state open`.

**Ask Bob the following before choosing:**

1. Is there a separate Git repo for `Helium\Services\HeartBeat`, or is it inside a larger Helium monorepo? The answer changes Option C implementation.
2. Should GitHub Issues #1–#7 move with the canonical tree, or stay on `helium-multitenant-demo` regardless?
3. Priority — is Phase 0–2 (consolidate) a blocker for BD-031, or should BD-031 run in the chosen tree immediately after Phase 0 even if Phase 1 port isn't complete?
4. Is the Float SDK at `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Clients\float\src\sdk` (BD-031 follow-on, ~60 callsites) in the same Clients monorepo as the Frontend Standard doc? Naming clash impacts cross-repo PRs.

---

## What the Next Session Must NOT Do

- ❌ Execute BD-031 rename in either tree before Phase 0 decides canonical.
- ❌ Execute BD-009 before BD-031.
- ❌ Start Debt #3 audit logging before harmonization — it'd land in both trees and need reconciling.
- ❌ Delete anything from either tree — everything stays until Phase 1 port is verified green.
- ❌ Touch the Float SDK (`C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Clients\float\src\sdk`) — separate session, ~60 callsites.
- ❌ Change EC2 deployment before Phase 2.

---

## Open Architectural Questions (For Bob — Needed Before Phase 3)

1. **`app_id` vs `source_id` vs `float_id`** — one identifier or three? My recommendation: one (`app_id` per BD-031), aliased during transition. `source_id` becomes an alias deprecated after migration 008.
2. **`app_instances` vs `app_registrations`** — one table or two? My recommendation: one (`app_instances`), drop `app_registrations`.
3. **`source_type` vs `app_type`** (Debt #1) — bundle with BD-031? My recommendation: yes.
4. **Flow endpoint auth** (BD-009 pre-auth) — HMAC-API-key reuse from Relay, or a new bootstrap credential? HeartBeat currently has `verify_service_credentials` dependency using `api_key:api_secret` against `registry.db`. Reusing that is cleanest.
5. **Where do GitHub Issues live** after consolidation? If Services has its own repo, migrate there. If Helium is a monorepo with no HeartBeat-specific repo, stay on `helium-multitenant-demo` as the tracking location.

---

## Rollback

The current state is **safe**:
- EC2 is running the NEW tree with real auth verified green.
- Migration 007 exists and is applied.
- OLD tree is untouched — still at its 14 Apr state.
- All work since 14 Apr is in NEW + committed to `helium-multitenant-demo` main branch.

If Phase 1 port goes wrong, Phase 2 rollback is trivial: revert the Dockerfile context change, re-deploy NEW tree. No data loss risk because Postgres volume persists across container rebuilds.

---

## Session Sequencing Summary

| Session | Phase | Duration | Output |
|---|---|---|---|
| 1 | 0 — Decide canonical | ~1 hour | Option chosen, Phase 1 merge plan written |
| 2 | 1 — Port delta NEW→OLD | ~1 day | All 1,484 LOC in canonical, tests green locally |
| 3 | 2 — Redeploy EC2 | ~2 hours | Verification curls green, v2.1.0 tagged |
| 4 | 3 — BD-031 + Debt #1 bundled | ~1 day | `float_id→app_id`, `source_type→app_type`, migration 008 |
| 5 | 4 — BD-009 flow endpoint | ~half day | `POST /api/v1/auth/flow` live |
| 6+ | 5 — Debt backlog | multi-session | Per `TECHNICAL_DEBT.md` order |

**Total program:** ~5–8 sessions before HeartBeat is production-ready for a second tenant.

---

## First Message for Next Session (Copy-Paste)

> **HeartBeat Harmonization — Phase 0 (Decide Canonical)**
>
> **Required reading, in order:**
> 1. `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HEARTBEAT_HARMONIZATION_HANDOFF.md` (this file describes the whole program)
> 2. `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\TECHNICAL_DEBT.md`
> 3. `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HELIUM_DEPLOYMENT_ARCHITECTURE.md`
>
> **Your job this session:**
> - Read the handoff and debt log in full.
> - Do **not** write code, do **not** run BD-031, do **not** run BD-009, do **not** start any debt item.
> - Spot-check the "strict superset" claim by reading 3–4 matching files in both trees:
>   - OLD: `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat\src`
>   - NEW: `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\services\heartbeat\src`
> - Ask Bob the 4 open questions listed in the handoff's "Open Architectural Questions" section.
> - Propose Phase 1 merge plan (which files ported in what order, which tests, rollback).
> - End session with the merge plan committed as `C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\docs\HEARTBEAT_PHASE1_MERGE_PLAN.md`.
>
> **Live infra:** EC2 `13.247.224.147:9000` is running real auth. Do not touch it. Test harness key at `C:\Users\PROBOOK\.helium\test_harness_key` is provisioned and hash is live.
>
> **GitHub:** `gh issue list --repo bob-nzelu/helium-multitenant-demo --label debt --state open` shows 7 open debt items #1–#7.

Paste that block into the new chat as its first message. It has everything the cold brain needs.
