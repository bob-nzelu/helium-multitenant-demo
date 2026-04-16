# HeartBeat Technical Debt Log

Tracked items from the 17 Apr 2026 old-vs-new audit session. Each item has a clear "when to resolve" trigger so nothing drifts indefinitely.

---

## 1. Rename `source_type` → `app_type` in HeartBeat

**Why:** Collision with Core's `invoices.source` field. Two different concepts share overlapping names:

- **HeartBeat `source_type`** = app identity (`float`, `transforma_reader`, `monitoring`)
- **Core `source`** = ingestion path (`BULK_UPLOAD`, `MANUAL`, `API`, `POLLER`, `EMAIL`)

Developers will confuse them. Rename HeartBeat's field to `app_type` (it IS an app); keep Core's `source` / `source_id` for data lineage.

**Affected:**
- `auth.app_registrations.source_type` column
- `source_type` fields in `registration_handler.py`, `api/auth.py` (RegisterAppRequest), `pg_auth.py`
- Documentation: `HELIUM_DEPLOYMENT_ARCHITECTURE.md`, `UNIFIED_AUTH_CONTRACT.md`

**When to resolve:** Paired with debt item #2 below (SQLite → Postgres migration session). Avoids a second schema migration just for a rename.

**Estimated effort:** 1 hour (migration + code rename + doc update).

---

## 2. Harmonize on PostgreSQL — migrate blob.db, config.db, license.db, audit.db

**Why:** We run Postgres for auth. Maintaining parallel SQLite files for blob/config/license fragments the data model:

- No cross-DB joins (can't query "all blobs for tenant X with config Y")
- Backup/restore requires handling 4+ storage engines
- Per-tenant scoping is more natural in Postgres (one DB, tenant_id column) than per-tenant SQLite files
- `audit.db` doesn't even exist yet in NEW — adding it straight to Postgres avoids another migration later

**Current SQLite databases:**
| DB | Purpose | Migration complexity |
|---|---|---|
| `blob.db` | File tracking, dedup, audit logs, limits, metrics | High — tables have heavy use |
| `config.db` | config_entries, tier_limits, feature_flags, database_catalog | Medium — lookup-heavy, few writes |
| `registry.db` | Service discovery, API credentials | Medium |
| `license.db` | License entitlements (immutable post-install) | Low — tiny, rarely changes |

**When to resolve:** Dedicated session — too big to piggyback on auth work. Trigger: before second tenant deploys to shared demo infrastructure, OR before first real production tenant.

**Estimated effort:** 2-3 days. Needs: new migrations per DB, handler rewrite to use `pg_connection` instead of SQLite, test parity, rollback plan.

---

## 3. Add audit logging to auth events

**Why:** `audit_handler.py` + `audit_guard.py` exist in both old and new HeartBeat, but neither wires them to auth events. Login, logout, password change, session eviction, device revocation — all silent. Compliance risk.

**Events that should emit audit rows:**
- Login success (with device_id, IP)
- Login failure (rate-limiting signal)
- Logout
- Password change (bootstrap vs normal)
- Session evicted (FIFO cap)
- Session revoked (logout / device revoke / permissions change)
- Device registration
- Device revocation
- App registration
- Step-up authentication

**When to resolve:** Next dedicated auth/audit session. Do it BEFORE any second tenant joins the demo — multi-tenant compliance requires this.

**Estimated effort:** Half day. Infrastructure exists; just need call sites.

---

## 4. 7-year retention policy for audit data

**Why:** FIRS compliance requires 7-year audit retention. Today enforced only by convention ("don't run DELETE"). No programmatic guard, no cold-storage archival.

**When to resolve:** After #3 (no point enforcing retention on an empty audit log). Before first production tenant.

**Estimated effort:** 1 day — archival cron + cold-storage target (S3 Glacier or equivalent) + restore-on-demand path.

---

## 5. Float + Reader: compute device_id and call register-app

**Why:** HeartBeat now accepts device_id in login and offers `register-device` / `register-app` endpoints. Neither Float nor Reader uses them yet.

**Current state:**
- **Float:** collects `machine_guid` + `mac_address` + `computer_name` in `upload_manager.py::_get_machine_fingerprint()` but does NOT compute the SHA256 device_id and does NOT call register-device or register-app.
- **Reader:** nothing — no machine fingerprint collection, no device_id, no HeartBeat registration calls.

**Needed:**
1. Shared utility: `helium_common/device_id.py` that computes `SHA256(machine_guid + ":" + mac)[:16]` cross-platform (Win registry / macOS IOKit / Linux /etc/machine-id).
2. Float `upload_manager` calls device_id util and sends it on login + every API call header (`X-Device-Id`).
3. Reader does same on startup.
4. Both apps call `POST /api/auth/register-app` after first successful login (stored in `~/.transforma/registration.json` and `~/.helium/float/registration.json` respectively).

**When to resolve:** Next Float + Reader harmonization session. Without it, HeartBeat's device tracking is empty — sessions have NULL device_id, can't enforce per-device replacement.

**Estimated effort:** 2 days (shared utility + Float wiring + Reader wiring + tests).

---

## 6. Test coverage for this session's additions

**Why:** ~1,041 LOC added in the 14 Apr HeartBeat session (mock_auth, admin stubs, test harness, registration_handler, auth.py + pg_auth.py extensions) with zero test files. Old HeartBeat had 42 test files / 11,025 LOC; new has identical 42 / 11,025.

**When to resolve:** Before production promotion. Demo can proceed.

**Estimated effort:** 1 day — ~500 LOC of tests modeled on existing fixtures.

---

## 7. Real update engine implementation

**Why:** `POST /api/admin/updates/apply|rollback|status` return 501. History returns empty list. Full spec exists in `HEARTBEAT_UPDATE_SPEC.md`. Not a regression — same stub state as OLD.

**When to resolve:** Dedicated session per the original handoff. Trigger: when first tenant needs an actual version upgrade delivered.

**Estimated effort:** Multi-day — package validation (Ed25519 signature), backup/rollback, schema migration orchestration, health-checked rolling restart.

---

## Debt Resolution Order (Recommended)

1. **This week:** #3 (audit logging on auth events) — compliance, small.
2. **Before 2nd demo tenant:** #5 (Float/Reader device_id) — otherwise device tracking is cosmetic.
3. **Before 1st production tenant:** #2 (SQLite → Postgres) + #1 (source_type → app_type, piggybacked) + #4 (7-year retention) + #6 (tests).
4. **When needed:** #7 (update engine) — triggered by first real upgrade.
