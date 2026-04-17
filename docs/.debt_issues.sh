#!/usr/bin/env bash
# One-shot script to create 7 tech debt issues, 3 labels, 2 milestones.
# Safe to re-run: label/milestone creation tolerates "already exists".
set -euo pipefail

REPO="bob-nzelu/helium-multitenant-demo"

# --- Labels ---
gh label create debt --repo "$REPO" --color 5319E7 --description "Technical debt tracked from 17 Apr audit" 2>/dev/null || true
gh label create P0 --repo "$REPO" --color B60205 --description "Blocks production / compliance" 2>/dev/null || true
gh label create P1 --repo "$REPO" --color D93F0B --description "Before 2nd tenant" 2>/dev/null || true
gh label create P2 --repo "$REPO" --color FBCA04 --description "Before production" 2>/dev/null || true
gh label create P3 --repo "$REPO" --color 0E8A16 --description "When needed" 2>/dev/null || true

# --- Milestones (via API — gh has no direct milestone-create command) ---
gh api "repos/$REPO/milestones" -f title="before-2nd-tenant" -f description="Must land before a second tenant joins the shared demo stack" 2>/dev/null || true
gh api "repos/$REPO/milestones" -f title="before-production" -f description="Must land before the first real production tenant deploys" 2>/dev/null || true

echo "Labels + milestones ready. Creating issues..."

# --- Issues ---

gh issue create --repo "$REPO" \
  --title "Debt #1: Rename HeartBeat source_type -> app_type" \
  --label debt,P2 \
  --milestone "before-production" \
  --body "**Why:** Collision with Core's \`invoices.source\` field.

- **HeartBeat \`source_type\`** = app identity (\`float\`, \`transforma_reader\`, \`monitoring\`)
- **Core \`source\`** = ingestion path (\`BULK_UPLOAD\`, \`MANUAL\`, \`API\`, \`POLLER\`, \`EMAIL\`)

Rename HeartBeat's field to \`app_type\`. Keep Core's \`source\` / \`source_id\` for data lineage.

**Affected:**
- \`auth.app_registrations.source_type\` column
- \`registration_handler.py\`, \`api/auth.py\` (RegisterAppRequest), \`pg_auth.py\`
- Docs: \`HELIUM_DEPLOYMENT_ARCHITECTURE.md\`, \`UNIFIED_AUTH_CONTRACT.md\`

**When:** Paired with Debt #2 (SQLite→Postgres) to avoid a second migration round-trip.

**Effort:** 1 hour when bundled.

**Source:** \`docs/TECHNICAL_DEBT.md\` §1"

gh issue create --repo "$REPO" \
  --title "Debt #2: Migrate blob.db / config.db / registry.db / license.db to PostgreSQL" \
  --label debt,P2 \
  --milestone "before-production" \
  --body "**Why:** SQLite is legacy from on-prem days. Now that Postgres is a hard dependency, parallel SQLite files fragment the data model:

- No cross-DB joins
- Backup/restore spans multiple engines
- \`audit.db\` doesn't exist in NEW yet — creating it straight in Postgres avoids another migration
- Per-tenant scoping is natural with \`tenant_id\` column, awkward with per-file SQLite

**Scope:**
| DB | Complexity |
|---|---|
| blob.db | High (heavy read/write) |
| config.db | Medium (lookup-heavy) |
| registry.db | Medium |
| license.db | Low |

**When:** Before first production tenant. Dedicated session — too big for piggyback.

**Effort:** 2-3 days.

**Source:** \`docs/TECHNICAL_DEBT.md\` §2"

gh issue create --repo "$REPO" \
  --title "Debt #3: Wire audit logging into auth events" \
  --label debt,P1 \
  --milestone "before-2nd-tenant" \
  --body "**Why:** \`audit_handler.py\` + \`audit_guard.py\` exist but NO auth event calls them. Login, logout, password change, session eviction, device revoke — all silent. Compliance risk.

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

**Call sites:** ~20, all in \`services/heartbeat/src/handlers/auth_handler.py\` and \`services/heartbeat/src/handlers/registration_handler.py\`.

**When:** This week. Before a second tenant joins.

**Effort:** Half day.

**Source:** \`docs/TECHNICAL_DEBT.md\` §3"

gh issue create --repo "$REPO" \
  --title "Debt #4: Enforce 7-year audit retention policy" \
  --label debt,P2 \
  --milestone "before-production" \
  --body "**Why:** FIRS compliance requires 7-year audit retention. Today enforced only by convention (\"don't run DELETE\"). No programmatic guard, no cold-storage archival.

**Scope:**
- Archival cron (monthly / quarterly) moving old events to cold storage
- Cold-storage target (S3 Glacier or equivalent)
- Restore-on-demand path for audits/investigations

**Depends on:** Debt #3 (no point enforcing retention on an empty log).

**When:** Before first production tenant.

**Effort:** 1 day.

**Source:** \`docs/TECHNICAL_DEBT.md\` §4"

gh issue create --repo "$REPO" \
  --title "Debt #5: Float + Reader compute device_id and call register-app" \
  --label debt,P1 \
  --milestone "before-2nd-tenant" \
  --body "**Why:** HeartBeat now accepts \`device_id\` in login and exposes \`register-device\` / \`register-app\`. Neither frontend uses them yet.

**Current state:**
- **Float:** collects \`machine_guid\` + MAC + \`computer_name\` in \`upload_manager._get_machine_fingerprint()\` but does NOT SHA256-hash to device_id and does NOT call HeartBeat registration.
- **Reader:** nothing — no machine fingerprinting, no device_id, no registration.

**Needed:**
1. Shared utility \`helium_common/device_id.py\` — cross-platform \`SHA256(machine_guid + \":\" + mac)[:16]\`
2. Float sends \`device_id\` on login + \`X-Device-Id\` header on every API call
3. Reader same
4. Both call \`POST /api/auth/register-app\` after first successful login
5. Store registration in \`~/.helium/float/registration.json\` and \`~/.transforma/registration.json\`

**Why now:** Until this lands, every session row has \`device_id=NULL\`. Per-device session replacement + eviction logic is built but can never trigger.

**When:** Next Float/Reader harmonization session. Before 2nd tenant.

**Effort:** 2 days.

**Source:** \`docs/TECHNICAL_DEBT.md\` §5"

gh issue create --repo "$REPO" \
  --title "Debt #6: Test coverage for 14-17 Apr HeartBeat additions" \
  --label debt,P2 \
  --milestone "before-production" \
  --body "**Why:** ~1,041 LOC added across these modules with zero tests:

- \`api/mock_auth.py\` (310 LOC)
- \`api/admin.py\` (61)
- \`api/test_harness/endpoints.py\` (324)
- \`auth/test_harness_manager.py\` (107)
- \`handlers/registration_handler.py\` (239)
- \`api/auth.py\` (+231)
- \`database/pg_auth.py\` (+179)

**Scope:** ~500 LOC of tests modeled on existing \`tests/\` fixtures. Focus on:
- Test harness HMAC verification (happy + tamper paths)
- Registration idempotency (same device_id + source_type returns existing source_id)
- 3-session FIFO eviction
- Refresh alias \`/api/auth/refresh\` parity with \`/token/refresh\`

**When:** Before production promotion.

**Effort:** 1 day.

**Source:** \`docs/TECHNICAL_DEBT.md\` §6"

gh issue create --repo "$REPO" \
  --title "Debt #7: Real update engine implementation" \
  --label debt,P3 \
  --body "**Why:** \`POST /api/admin/updates/{apply,rollback,status}\` return 501. \`history\` returns empty list. Stub only — matches OLD HeartBeat (pre-existing TODO, not a regression).

**Scope (from HEARTBEAT_UPDATE_SPEC.md):**
1. Package validation (Ed25519 signature from Pronalytics)
2. Pre-update backup (DB snapshot + Docker image tags)
3. Schema migration orchestration (forward-only, ordered)
4. Docker image pull (or load from offline package)
5. Health-checked rolling restart (60s auto-rollback)
6. Audit trail of who applied what, when

**Trigger:** When first tenant needs an actual version upgrade delivered. Deferred to dedicated session.

**Effort:** Multi-day.

**Source:** \`docs/TECHNICAL_DEBT.md\` §7"

echo ""
echo "All 7 issues created. Listing:"
gh issue list --repo "$REPO" --label debt --state open
