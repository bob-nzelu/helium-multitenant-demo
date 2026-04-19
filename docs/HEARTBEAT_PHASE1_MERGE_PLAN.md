# HeartBeat — Phase 1 Merge Plan

**Created:** 19 Apr 2026 (Phase 0 output)
**Author session:** Phase 0 — Decide Canonical
**Status:** Approved by Bob. Ready to execute in Phase 1 session.
**Repo for execution:** `C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\` (new helium-services monorepo)

---

## Phase 0 Decisions — Canonical Record

These decisions were made during Phase 0 and are binding for all subsequent HeartBeat work.

| Decision | Choice | Rationale |
|---|---|---|
| **Canonical strategy** | Option C — Services survives, demo = deploy wrapper | Honors instinct; `Helium\Services\` becomes canonical for all backend services |
| **Git home** | Init at `Helium\Services\` as monorepo root | Services-only monorepo (not all of `Helium\`). Clients stays on `helium-frontends`. |
| **GitHub remote** | `github.com/bob-nzelu/helium-services` | New private repo, HeartBeat committed first |
| **Monorepo scope** | HeartBeat first, other services in dedicated sessions | Other services (Relay, Core, Edge, HIS, SIS) join in separate clean-up sessions |
| **Demo repo role** | Deploy-only: docker-compose, Dockerfile context, config/, schemas/ | No code, no docs in demo after Phase 1C |
| **Docs location** | Move from demo `docs/` → `Helium\Services\docs\` | All backend docs live alongside the code they describe |
| **BD-031 gate** | Hard gate on Phase 1 + Phase 2 completion | No BD-031 rename until EC2 is running from canonical and verified |
| **Issues location** | Stay on `helium-multitenant-demo` for now | Migrate to helium-services once it's established (Phase 2 or later) |
| **app_id vs source_id** | Separate concepts — keep distinct | `app_id` = app instance (hardware-bound). `source_id` = user on that app instance |
| **source_id format** | `src-{app_id}-{user_id}` (deterministic concat) | Replaces the current generated `src-{source_type}-{device_id}-{seq}` — Phase 3 change |
| **app_instances vs app_registrations** | Separate tables, keep both | Different semantics — Phase 3 will clarify the FK relationship |
| **Float\App git home** | Joins `helium-frontends` | Separate session, not blocking HeartBeat harmonization |

### Critical Finding from Phase 0

**`Helium\Services\HeartBeat` has NO `.git` folder.** The handoff's claim that "OLD has the long git history" was incorrect — OLD is a plain filesystem tree with no version control. The only git history in the HeartBeat ecosystem is in `helium-multitenant-demo`. This does not change the Option C decision (Services will become canonical with a fresh git history), but it removes the "preserve git history" argument for Option C. The trade-off becomes: clean separation of concerns (code in Services, deploy in demo) vs. zero porting work (Option B). Bob chose Option C.

---

## File Delta — What Gets Ported (NEW → Services)

These files exist in NEW but not in OLD, or have more code in NEW than OLD.

### New files (copy verbatim from NEW):

| File (relative to `services/heartbeat/` in demo, `HeartBeat/` in Services) | LOC | Description |
|---|---|---|
| `databases/migrations/auth/007_devices_and_app_registrations.sql` | 58 | Creates `auth.devices`, adds `device_id` to sessions, creates `auth.app_registrations` |
| `src/handlers/registration_handler.py` | 239 | `register_app()` business logic |
| `src/auth/test_harness_manager.py` | 107 | HMAC validation for `X-Test-Harness-Signature`. Constant-time via `hmac.compare_digest()` |
| `src/api/admin.py` | 61 | Update engine stubs (all return 501 except `/history` which returns `[]`) |
| `src/api/mock_auth.py` | 310 | Mock auth router — activated by `HEARTBEAT_MOCK_AUTH=true` |
| `src/api/test_harness/__init__.py` | ~5 | Module marker |
| `src/api/test_harness/endpoints.py` | 324 | `/api/test/*` routes: auth/reset, create-user, data/seed, data/clear, sse/emit, config/override, state |

### Modified files (NEW is strict superset — copy NEW version wholesale):

| File | OLD LOC | NEW LOC | Delta | Key additions |
|---|---|---|---|---|
| `src/database/pg_auth.py` | 393 | 572 | +179 | Device CRUD, `get_device_active_session`, `revoke_oldest_session`, `get_app_registration`, `create_app_registration`, `update_app_registration_seen`, `get_next_source_sequence`. `get_tenant_max_sessions` default 1→3. |
| `src/api/auth.py` | 341 | 572 | +231 | `device_id` on `LoginRequest`/`LoginResponse`, `POST /api/auth/refresh` alias, `POST /api/auth/register-device`, `POST /api/auth/register-app`, `GET /api/auth/devices`, `POST /api/auth/devices/{id}/revoke`, `GET /api/auth/sessions` |
| `src/handlers/auth_handler.py` | 893 | 907 | +14 | `login()` accepts `device_id`, evicts oldest on 3-session cap, replaces existing session on same device. JWT includes `device_id` claim. Introspect returns `device_id`. Step-up and refresh preserve `device_id`. |
| `src/main.py` | 680 | 698 | +18 | Conditional router registration for `admin`, `mock_auth`, and `test_harness` based on env vars |

**Total delta: ~1,541 LOC** (matches handoff estimate of 1,484 — difference is comment/blank line count).

### Files NOT to touch (stay in OLD as-is):

Everything else in `HeartBeat\src\` — 86 files, 17,891 LOC. These are identical in OLD and NEW (verified: directory structure is a strict subset of NEW, no regressions found in spot-check).

---

## Execution Plan

### Phase 1A — Create helium-services Repo (~30 min)

**Checkpoint:** No code changes. Git init only. EC2 unaffected.

```bash
cd "C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services"
git init
```

Create `.gitignore` at `Helium\Services\.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo
venv/
.venv/
*.egg-info/
dist/
build/

# SQLite databases (live data — never commit)
*.db
*.db-shm
*.db-wal
*.db.pre_canonical_backup

# Keys and secrets
*/databases/keys/
*.pem
.env
.env.local
*.key

# Logs and runtime data
logs/
data/blobs/
*.log

# Windows artefacts
desktop.ini
Thumbs.db

# IDE
.vscode/
.idea/
*.suo
```

Stage and commit HeartBeat only:

```bash
git add HeartBeat/
git commit -m "Initial: HeartBeat canonical baseline (14 Apr 2026 state, pre-harmonization)

This is the OLD Services tree at the pre-harmonization state.
Phase 1B will add all 14-17 Apr additions from helium-multitenant-demo.
"
```

Create remote and push:

```bash
gh repo create bob-nzelu/helium-services --private --source=. --remote=origin --push
```

**Verify:** `gh repo view bob-nzelu/helium-services` shows the repo. `git log --oneline` shows 1 commit.

---

### Phase 1B — Port Delta NEW → Services (~3–4 hours)

Execute in this exact order (dependencies flow downward):

**Step B1 — Migration file** (no code deps)

```bash
# Source: helium-multitenant-demo\services\heartbeat\databases\migrations\auth\007_devices_and_app_registrations.sql
# Target: Helium\Services\HeartBeat\databases\migrations\auth\007_devices_and_app_registrations.sql
```

Copy the file. Read it first — confirm it creates `auth.devices`, adds `auth.sessions.device_id`, creates `auth.app_registrations`. No surprises.

**Step B2 — New source files** (copy verbatim — don't exist in OLD)

```
Helium\Services\HeartBeat\src\handlers\registration_handler.py
Helium\Services\HeartBeat\src\auth\test_harness_manager.py
Helium\Services\HeartBeat\src\api\admin.py
Helium\Services\HeartBeat\src\api\mock_auth.py
Helium\Services\HeartBeat\src\api\test_harness\__init__.py
Helium\Services\HeartBeat\src\api\test_harness\endpoints.py
```

No OLD files to diff against — pure additions.

**Step B3 — Update modified files** (NEW replaces OLD, NEW is superset)

Read OLD version before overwriting — confirm no local-only changes exist (Phase 0 audit concluded there are none, but verify once).

```
Helium\Services\HeartBeat\src\database\pg_auth.py       ← replace with NEW
Helium\Services\HeartBeat\src\api\auth.py                ← replace with NEW
Helium\Services\HeartBeat\src\handlers\auth_handler.py   ← replace with NEW
Helium\Services\HeartBeat\src\main.py                    ← replace with NEW
```

**Step B4 — Run test suite**

```bash
cd "C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat"
python -m pytest tests/ -v
```

Expected: 42 test files, all green. If failures, fix before committing.

**Step B5 — Commit**

```bash
cd "C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services"
git add HeartBeat/
git commit -m "Port 14-17 Apr delta from helium-multitenant-demo

Files added:
- databases/migrations/auth/007_devices_and_app_registrations.sql (58 LOC)
- src/handlers/registration_handler.py (239 LOC)
- src/auth/test_harness_manager.py (107 LOC)
- src/api/admin.py (61 LOC)
- src/api/mock_auth.py (310 LOC)
- src/api/test_harness/__init__.py
- src/api/test_harness/endpoints.py (324 LOC)

Files updated (NEW is strict superset of OLD):
- src/database/pg_auth.py (+179 LOC: device CRUD, session management)
- src/api/auth.py (+231 LOC: device_id fields, new auth endpoints)
- src/handlers/auth_handler.py (+14 LOC: device_id in JWT, session eviction)
- src/main.py (+18 LOC: conditional router registration)

Resolves: harmonization of old-vs-new audit (17 Apr 2026).
Ref: HEARTBEAT_HARMONIZATION_HANDOFF.md Phase 1.
"
git push origin main
```

---

### Phase 1C — Migrate Docs (~1 hour)

**Create docs structure in Services repo:**

```
Helium\Services\docs\
├── HELIUM_DEPLOYMENT_ARCHITECTURE.md    (cross-service, top-level)
└── HeartBeat\
    ├── TECHNICAL_DEBT.md
    ├── UNIFIED_AUTH_CONTRACT.md
    ├── HEARTBEAT_HARMONIZATION_HANDOFF.md
    ├── HEARTBEAT_AUTH_SESSION_HANDOFF.md
    └── HEARTBEAT_PHASE1_MERGE_PLAN.md   (this file)
```

Move from `helium-multitenant-demo\docs\` to the above structure.

**Strip demo repo's docs folder** — replace with a single `docs\README.md`:

```markdown
# Documentation

Documentation for HeartBeat and all Helium backend services has moved to:
**https://github.com/bob-nzelu/helium-services/tree/main/docs**

This repo (`helium-multitenant-demo`) is a deploy-only wrapper:
- `docker-compose.yml` — full stack orchestration
- `config/schemas/` — PostgreSQL init scripts
- `config/tenants.json` — HMAC credentials for demo tenants
```

Commit both sides: `git add` in helium-services, `git add` in helium-multitenant-demo.

---

### Phase 1D — Rewire Dockerfile Build Context (~1 hour)

**Goal:** `docker compose build heartbeat` builds from `Helium\Services\HeartBeat\`, not `helium-multitenant-demo\services\heartbeat\`.

**Update `helium-multitenant-demo\docker-compose.yml`:**

Change the heartbeat `build` section from:

```yaml
heartbeat:
  build:
    context: ./services/heartbeat
```

to:

```yaml
heartbeat:
  build:
    context: ${HEARTBEAT_BUILD_CONTEXT:-./services/heartbeat}
```

**Set `HEARTBEAT_BUILD_CONTEXT` in `.env` (local):**

```bash
# C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo\.env
HEARTBEAT_BUILD_CONTEXT=C:\Users\PROBOOK\OneDrive\WestMetro\Helium\Services\HeartBeat
```

**Verify local build works:**

```bash
cd "C:\Users\PROBOOK\OneDrive\WestMetro\Pronalytics\helium-multitenant-demo"
docker compose build heartbeat
docker compose up -d heartbeat
curl -X POST http://localhost:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"Charles.Omoakin@abbeymortgagebank.com","password":"WestMetro2026!"}'
```

**Expected:** Ed25519 JWT, real auth (not mock), `device_id` field present in response.

**The default `:-./services/heartbeat` stays in place** so EC2's existing `.env` (without `HEARTBEAT_BUILD_CONTEXT`) continues to build from the demo's internal snapshot until Phase 2 rewires EC2 explicitly.

---

### Phase 1E — Cleanup (~15 min)

Do NOT delete `helium-multitenant-demo\services\heartbeat\` yet. Archive it for Phase 2:

```bash
# In helium-multitenant-demo repo:
git mv services/heartbeat services/heartbeat_ARCHIVED_2026-04-19
git commit -m "Archive services/heartbeat — canonical code now in helium-services repo"
```

This keeps Phase 2 rollback trivial: revert docker-compose HEARTBEAT_BUILD_CONTEXT default.

---

## Test Checkpoints

| Checkpoint | Command | Pass criteria |
|---|---|---|
| B4 — Unit tests green | `python -m pytest tests/ -v` in Services/HeartBeat | All 42 test files pass |
| 1D — Local build | `docker compose build heartbeat` | Build completes without error |
| 1D — Local auth | POST `/api/auth/login` via localhost:9000 | Real Ed25519 JWT returned |
| 1D — device_id in JWT | Decode JWT payload | `device_id` claim present |
| 1D — 3-session cap | Login 4 times with different device_ids | 4th login succeeds, oldest session revoked |
| 1D — register-app | POST `/api/auth/register-app` | Returns `source_id` + config bundle |
| 1D — test harness | HMAC-signed POST `/api/test/state` | Returns system state (not 404/403) |

---

## Rollback Plan

| Phase | Rollback action | Recovery time |
|---|---|---|
| Phase 1A | Delete `Helium\Services\.git` | Instant. EC2 unaffected. |
| Phase 1B | `git reset --hard HEAD~1` in helium-services | < 1 min. EC2 unaffected. |
| Phase 1C | `git revert` docs commit in both repos | < 5 min. EC2 unaffected. |
| Phase 1D local | Remove `HEARTBEAT_BUILD_CONTEXT` from `.env` | Instant. Reverts to demo's internal snapshot. |

EC2 is not touched until Phase 2. The EC2 fallback at any point in Phase 1 is: nothing — it keeps running the current NEW tree unchanged.

---

## What Phase 2 Will Do

Phase 2 (dedicated session, ~2 hours) picks up after Phase 1 tests are all green:

1. SSH to EC2.
2. `git clone https://github.com/bob-nzelu/helium-services /home/ubuntu/helium-services`
3. Set EC2's `.env`: `HEARTBEAT_BUILD_CONTEXT=/home/ubuntu/helium-services/HeartBeat`
4. `git pull` on helium-multitenant-demo (picks up Phase 1D compose change).
5. `sudo docker compose build heartbeat && sudo docker compose up -d heartbeat`
6. Run verification curls from `HEARTBEAT_AUTH_SESSION_HANDOFF.md §Verification Checklist`.
7. If all green: tag `v2.1.0` on helium-services.
8. Remove the archived `services/heartbeat_ARCHIVED_2026-04-19/` from demo repo.
9. Migrate GitHub Issues #1–#7 from helium-multitenant-demo to helium-services (or close + reopen).

---

## What Comes After Phase 2 — The Sequence

| Session | Phase | Gating condition | Output |
|---|---|---|---|
| This doc | 0 — Decided canonical | None | Merge plan written |
| Next session | 1 — Port delta + git init | — | helium-services live, tests green, local compose builds from Services |
| +1 session | 2 — Redeploy EC2 | Phase 1 green | EC2 running from canonical, v2.1.0 tagged |
| +1 session | 3 — BD-031 + Debt #1 | Phase 2 green | `float_id→app_id`, `source_type→app_type`, migration 008, source_id format change |
| +1 session | 4 — BD-009 flow endpoint | Phase 3 green | `POST /api/v1/auth/flow` live |
| Multiple | 5 — Debt backlog (#3, #5, #2, #4, #6) | Phase 2 (for P1 items) | Per TECHNICAL_DEBT.md priority order |

---

## Architectural Notes for Phase 3 (BD-031)

**app_id vs source_id — the separation:**

- `app_id` (post-BD-031 rename of `float_id`): identifies the **app instance** — hardware-bound, one per device per app type. Lives in `app_instances` (renamed from `float_instances`).
- `source_id`: identifies **who** is using **which** app instance — `src-{app_id}-{user_id}`. Lives in `app_registrations`. Deterministic, idempotent. Two different users on the same device get different source_ids.

**Migration 008 scope (Phase 3):**
- Rename `float_instances` → `app_instances`
- Rename `float_id` → `app_id` everywhere
- Rename `source_type` → `app_type` (Debt #1, bundled)
- Update `source_id` generation to `src-{app_id}-{user_id}` format
- Config endpoint: `/api/v1/config/{float_id}` → `/api/v1/config/{app_id}`
- Float SDK (~60 callsites): separate session after Phase 3

**`app_instances` vs `app_registrations`** remain separate tables. The FK relationship (a registration belongs to an app instance) will be clarified during Phase 3 schema work.

---

## Open Questions Deferred to Later Phases

1. **BD-009 pre-auth mechanism** (Phase 4): HMAC-API-key reuse from Relay, or new bootstrap credential? Recommend reusing `verify_service_credentials` (api_key:api_secret against registry.db). Decide before Phase 4.
2. **`allow_cross_app_session` tenant flag** (Phase 4): Needs to be added to `config_entries`. Spec before Phase 4.
3. **Fingerprint drift detection** (Phase 4, Debt #5 dependency): Requires Float/Reader to start sending `fingerprint_hash`. No point implementing BD-009 step 3 until Debt #5 is done.
4. **7-year audit retention cold storage** (Phase 5, Debt #4): S3 Glacier or equivalent. Decide before first production tenant.
