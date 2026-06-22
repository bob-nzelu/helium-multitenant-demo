#!/usr/bin/env bash
# ============================================================================
# deploy_sika.sh — one-shot bootstrap for the Sika dedicated single-tenant box
# ============================================================================
# Run on a FRESH Ubuntu 22.04/24.04 host. Brings up the full Sika demo stack:
# PG 16 + redis + rabbitmq + heartbeat + relay-api + legacy core + stub edge
# (sim-FIRS) + his/sis stubs + simulator.
#
#   curl -sSL <raw-url>/deploy_sika.sh -o deploy_sika.sh && bash deploy_sika.sh
#   # or, from an existing checkout of this repo:
#   ./deploy_sika.sh
#
# Demo/UAT phase: license-not-required, sim-FIRS, legacy Core, 666666 one-shot
# login. Real FIRS + production hardening are a later cutover (handoff §7/§9).
#
# This script closes the FOUR compose gaps from SIKA_NEW_TEAM_DEPLOY_HANDOFF.md:
#   Gap A — clones helium-services (sika/deploy-integration) side-by-side and
#           exports HEARTBEAT_BUILD_CONTEXT.
#   Gap B — HEARTBEAT_LICENSE_REQUIRED=false + DEMO_MODE=true + TIER=test (.env.sika).
#   Gap C — s2s keys: brings HB up first, surfaces the WARN-logged keys, and
#           (after you paste them into .env.sika) restarts the consumers.
#   Gap D — renders config/tenants.json (the `sika` entry) from the template +
#           .env.sika, with freshly-minted HMAC secrets (never committed).
# ----------------------------------------------------------------------------
set -euo pipefail

# ── Config (override via env if you fork the repos) ──────────────────────────
SERVICES_REPO="${SERVICES_REPO:-https://github.com/bob-nzelu/helium-services.git}"
SERVICES_BRANCH="${SERVICES_BRANCH:-sika/deploy-integration}"
SERVICES_DIR="${SERVICES_DIR:-../helium-services}"   # sibling to this repo
ENV_FILE=".env.sika"
TENANTS_TEMPLATE="config/tenants.sika.template.json"
TENANTS_LIVE="config/tenants.json"
COMPOSE="docker compose --env-file ${ENV_FILE}"

log() { printf '\n=== %s ===\n' "$*"; }

# ── 0. Sanity: must run from the demo repo root ──────────────────────────────
if [ ! -f docker-compose.yml ] || [ ! -f "${TENANTS_TEMPLATE}" ]; then
  echo "ERROR: run this from the helium-multitenant-demo (sika/deploy-config) repo root." >&2
  exit 1
fi
REPO_ROOT="$(pwd)"

# ── 1. System + Docker ───────────────────────────────────────────────────────
log "1/8 Installing Docker + compose plugin"
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$(whoami)" || true
  echo "Docker installed (you may need to re-login for the docker group to apply)."
fi
if ! docker compose version &>/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin
fi
# envsubst for Gap D rendering
if ! command -v envsubst &>/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq gettext-base
fi

# ── 2. Gap A: clone helium-services side-by-side ─────────────────────────────
log "2/8 Cloning helium-services (${SERVICES_BRANCH}) — Gap A build context"
if [ -d "${SERVICES_DIR}/.git" ]; then
  git -C "${SERVICES_DIR}" fetch origin "${SERVICES_BRANCH}" --quiet || true
  git -C "${SERVICES_DIR}" checkout "${SERVICES_BRANCH}" --quiet || true
  git -C "${SERVICES_DIR}" pull --ff-only origin "${SERVICES_BRANCH}" --quiet || true
else
  git clone --branch "${SERVICES_BRANCH}" "${SERVICES_REPO}" "${SERVICES_DIR}"
fi
if [ ! -d "${SERVICES_DIR}/HeartBeat" ]; then
  echo "ERROR: ${SERVICES_DIR}/HeartBeat not found — wrong branch or layout." >&2
  exit 1
fi

# ── 3. .env.sika ─────────────────────────────────────────────────────────────
log "3/8 Preparing ${ENV_FILE}"
if [ ! -f "${ENV_FILE}" ]; then
  cp .env.sika.example "${ENV_FILE}"
  echo "Created ${ENV_FILE} from template. EDIT IT NOW and replace every CHANGE_ME,"
  echo "then re-run ./deploy_sika.sh. (Aborting so you don't boot with placeholders.)"
  exit 2
fi
if grep -q "CHANGE_ME" "${ENV_FILE}"; then
  echo "ERROR: ${ENV_FILE} still has CHANGE_ME placeholders. Fill them, then re-run." >&2
  exit 2
fi
# Force Gap A context to the resolved sibling path (absolute, compose-friendly).
export HEARTBEAT_BUILD_CONTEXT="$(cd "${SERVICES_DIR}/HeartBeat" && pwd)"
# shellcheck disable=SC2046
export $(grep -v '^#' "${ENV_FILE}" | grep -v '^[[:space:]]*$' | sed 's/[[:space:]]*=[[:space:]]*/=/' | xargs -d '\n') 2>/dev/null || true

# ── 4. Gap D: render config/tenants.json (sika entry, fresh HMAC) ────────────
log "4/8 Rendering ${TENANTS_LIVE} from template — Gap D"
envsubst < "${TENANTS_TEMPLATE}" > "${TENANTS_LIVE}"
if grep -q '\${' "${TENANTS_LIVE}"; then
  echo "ERROR: unresolved \${...} in ${TENANTS_LIVE} — a SIKA_* var is missing from ${ENV_FILE}." >&2
  exit 2
fi
echo "Rendered ${TENANTS_LIVE} (single-tenant: sika). [gitignored — holds live secrets]"

# ── 5. Build + bring up HeartBeat first (Gap C: surface s2s keys) ────────────
log "5/8 Building images + starting HeartBeat first"
${COMPOSE} build
${COMPOSE} up -d postgres redis rabbitmq heartbeat

log "Waiting for HeartBeat to become healthy"
for i in $(seq 1 60); do
  if curl -fs http://localhost:9000/health >/dev/null 2>&1; then echo "HeartBeat healthy."; break; fi
  sleep 5
  [ "$i" = "60" ] && { echo "WARN: HeartBeat not healthy after 5m — check 'docker compose logs heartbeat'."; }
done

# ── 6. Gap C: s2s key paste gate ─────────────────────────────────────────────
log "6/8 Gap C — service-to-service signing keys"
if [ -z "${RELAY_S2S_SIGNING_KEY:-}" ]; then
  echo "HeartBeat auto-generated the s2s HMAC keys on first boot and logged each"
  echo "at WARN. Showing them now:"
  echo "----------------------------------------------------------------------"
  ${COMPOSE} logs heartbeat 2>/dev/null | grep -Ei "S2S_SIGNING_KEY|RELAY_S2S|CORE_HEARTBEAT_S2S|EDGE_S2S" || \
    echo "(no s2s WARN lines found yet — give HB a few more seconds and re-check 'docker compose logs heartbeat')"
  echo "----------------------------------------------------------------------"
  echo "ACTION REQUIRED (one-time):"
  echo "  1. Copy RELAY_S2S_SIGNING_KEY (and CORE_/EDGE_ if shown) from the log above"
  echo "     into ${ENV_FILE}."
  echo "  2. Re-run ./deploy_sika.sh — it will bring up the rest of the stack."
  echo "Note: in this single-tenant stack only Relay consumes its key on the"
  echo "request path; legacy Core (plain HTTP to HB) and the stub Edge need none."
  exit 3
fi

# ── 7. Bring up the rest of the stack ────────────────────────────────────────
log "7/8 Starting the full stack (relay/core/edge/his/sis/simulator)"
${COMPOSE} up -d

log "Waiting for the stack to settle"
for i in $(seq 1 36); do
  unhealthy=$(${COMPOSE} ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -Eic 'starting|unhealthy' || true)
  [ "${unhealthy}" = "0" ] && break
  sleep 5
done

# ── 8. Summary ───────────────────────────────────────────────────────────────
log "8/8 Done — stack status"
${COMPOSE} ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
cat <<'EOF'

=== Sika demo stack is up (demo/UAT phase) ===
Endpoints:
  HeartBeat : http://localhost:9000/health   JWKS: /.well-known/jwks.json
  Relay     : http://localhost:8082/health
  Core      : http://localhost:8080/api/v1/health   (legacy Core)
  Edge      : http://localhost:8085/health   (stub — sim-FIRS, STUB_ACCEPTED)
  Simulator : http://localhost:8090/health

NEXT (DB build — owned by the deploy operator, see SETUP_SIKA.md / handoff §5):
  Provision the Sika tenant + 10 users (666666 one-shot login) via
  ../helium-services/HeartBeat/scripts/provision_tenant.py, then re-apply
  auth migration 015 (per-user grants) AFTER the users exist (deploy-order hazard).

HeartBeat will log LICENSE_INVALID — EXPECTED in the demo phase (Gap B).
EOF
