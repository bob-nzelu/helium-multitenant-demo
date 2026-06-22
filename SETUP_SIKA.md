# Sika Dedicated Box — Setup (Demo / UAT Phase)

Single-tenant Sika backend on one Ubuntu host. Demo posture:
**license-not-required, sim-FIRS (stub Edge), legacy Core, `666666` one-shot
login.** Real FIRS + production hardening are a later cutover — see
`SIKA_NEW_TEAM_DEPLOY_HANDOFF.md` §7/§9 (out of scope here).

This repo (`helium-multitenant-demo` @ branch `sika/deploy-config`) is the
**deploy/infra + Relay + legacy Core + stub Edge**. HeartBeat builds from a
side-by-side checkout of `helium-services` (branch `sika/deploy-integration`).

---

## TL;DR

```bash
git clone -b sika/deploy-config https://github.com/bob-nzelu/helium-multitenant-demo.git
cd helium-multitenant-demo
cp .env.sika.example .env.sika      # then EDIT — replace every CHANGE_ME
./deploy_sika.sh                    # 1st run: brings up HB, prints s2s keys, stops
#   → paste RELAY_S2S_SIGNING_KEY from the HB log into .env.sika
./deploy_sika.sh                    # 2nd run: brings up the full stack
```

`deploy_sika.sh` installs Docker, clones `helium-services` side-by-side, closes
all four compose gaps, and runs `docker compose up -d`.

---

## The four compose gaps (and how they're closed)

### Gap A — HeartBeat build context
The demo's `./services/heartbeat` was **deleted**. HeartBeat now builds from a
side-by-side `helium-services/HeartBeat` checkout.
- `docker-compose.yml` default: `HEARTBEAT_BUILD_CONTEXT=../helium-services/HeartBeat`.
- `deploy_sika.sh` (and `scripts/setup_server.sh`) clone
  `bob-nzelu/helium-services` @ `sika/deploy-integration` as a sibling dir and
  export the resolved absolute path.

### Gap B — license blocks boot
Demo phase sets (in `.env.sika`):
```
HEARTBEAT_LICENSE_REQUIRED=false
HEARTBEAT_DEMO_MODE=true
HEARTBEAT_TIER=test
```
HeartBeat **boots and serves**, and logs `LICENSE_INVALID` at startup —
**this is expected** in the demo phase. The abbey/adansi/redeploy-probe license
**mounts were removed** (those files don't exist on a Sika box and would fail the
bind-mount). A commented `sika.license.json` mount is left for go-live.

### Gap C — s2s signing key paste (one-time, by hand)
On **first boot** HeartBeat **auto-generates** the service HMAC keys and **logs
each at WARN** with the exact env-var name:
`RELAY_S2S_SIGNING_KEY`, `CORE_HEARTBEAT_S2S_SIGNING_KEY`, `EDGE_S2S_SIGNING_KEY`.

Procedure (`deploy_sika.sh` automates the gating):
1. First `./deploy_sika.sh` brings up **HeartBeat only**, then prints the WARN
   lines (also: `docker compose --env-file .env.sika logs heartbeat | grep S2S`).
2. Copy `RELAY_S2S_SIGNING_KEY`'s value into `.env.sika`.
3. Re-run `./deploy_sika.sh` — it brings up the rest and restarts the consumers.

> In **this single-tenant stack only Relay consumes its s2s key on the request
> path.** Legacy Core talks to HB over plain HTTP (`CORE_HEARTBEAT_URL`) and the
> stub Edge needs no HB credential, so `CORE_HEARTBEAT_S2S_SIGNING_KEY` /
> `EDGE_S2S_SIGNING_KEY` are listed for completeness / go-live parity but are not
> wired into those services in the demo. Paste them anyway if HB logs them.

### Gap D — `tenants.json` (`sika` entry, fresh HMAC)
`config/tenants.json` was hardcoded to `abbey`+`abmfb`. For the Sika box:
- The committed file is now **`config/tenants.sika.template.json`** (placeholders).
- `deploy_sika.sh` renders the **live** `config/tenants.json` from the template +
  `.env.sika` via `envsubst`, using **freshly-minted** `SIKA_API_KEY` /
  `SIKA_API_SECRET` (never the abbey/abmfb committed secrets).
- The rendered `config/tenants.json` is **gitignored** (it holds live secrets).

---

## Secrets (`.env.sika` — gitignored)

Copy `.env.sika.example` → `.env.sika` and fill every `CHANGE_ME`. Generate
strong values, e.g.:

```bash
python3 - <<'PY'
import secrets, string
alnum = lambda n: ''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(n))
print("PG_PASSWORD               =", alnum(28))
print("RABBITMQ_PASSWORD         =", alnum(28))
print("HEARTBEAT_AUTH_DB_KEY     =", secrets.token_hex(32))
print("HEARTBEAT_ADMIN_API_KEY   =", "hb_admin_"+alnum(24))
print("HEARTBEAT_ADMIN_S2S_...   =", secrets.token_hex(32))
print("SIKA_API_KEY              =", "SIKA-2026-"+alnum(8).upper())
print("SIKA_API_SECRET           =", alnum(32))
print("SIKA_SERVICE_ID           =", alnum(8).upper())
PY
```

| Secret | Source |
|---|---|
| `PG_PASSWORD`, `RABBITMQ_PASSWORD` | you mint |
| `HEARTBEAT_AUTH_DB_KEY` (SQLCipher) | you mint |
| `HEARTBEAT_ADMIN_API_KEY` + `HEARTBEAT_ADMIN_S2S_SIGNING_KEY` | you mint — the provisioning runner authenticates with these |
| `HEARTBEAT_JWT_PRIVATE_KEY_PATH` | HB **auto-generates** at this path on first boot |
| `RELAY_S2S_SIGNING_KEY` (+ Core/Edge) | HB **auto-generates**, logs at WARN → paste (Gap C) |
| `SIKA_API_KEY` / `SIKA_API_SECRET` / `SIKA_SERVICE_ID` | you mint (Gap D) |
| `HEARTBEAT_TEST_HARNESS_KEY_HASH` | demo default kept (rotate at go-live) |
| L5 Ed25519 / OAuth / JWKS key | HB **auto-generates** into PG on first boot; `/.well-known/jwks.json` |
| `HEARTBEAT_KMS_KEY_ID` (encrypt-at-rest) | **leave UNSET** in demo (plaintext + WARN); go-live only |
| FIRS creds + RSA key | **NOT needed** in demo (sim-FIRS) |

**Never** reuse the committed `abbey`/`abmfb` secrets. **Never** commit `.env.sika`.

---

## DB build + provisioning (operator-owned — handoff §5)

After the stack is healthy:

1. **Provision the Sika tenant + 10 users** with the runner in the
   `helium-services` checkout:
   ```bash
   cd ../helium-services/HeartBeat
   python scripts/provision_tenant.py \
     --heartbeat-url http://localhost:9000 \
     --api-key "$HEARTBEAT_ADMIN_API_KEY" \
     --signing-key "$HEARTBEAT_ADMIN_S2S_SIGNING_KEY" \
     --config scripts/seed/sika_tenant_config.json
   ```
   The Sika manifest carries the `666666` one-shot password + `is_first_run=false`
   for all 10 users (Folashade Ojelade = Owner, Adebayo Salami = Admin, 8 Operators).

2. **Deploy-order hazard:** auth migration **`015`** (per-user grants) **silently
   no-ops if the users don't exist yet** (`WHERE EXISTS`). Apply order on the box:
   **provision users → THEN (re-)apply `015`.** Applying `015` first leaves
   Bola/Tunde/Francisca/Ibukun without payment/inbound grants — **no error**.

3. **Verify:** run the amended `verify_sika_seed.py` (asserts the demo posture,
   not `is_first_run=true ×10`), then log in as a Sika user with `666666` and
   confirm a **full token** (not a reset/bootstrap token) + correct tabs.

---

## Verify the stack

```bash
docker compose --env-file .env.sika ps
curl http://localhost:9000/health                 # HeartBeat (LICENSE_INVALID in log = OK)
curl http://localhost:9000/.well-known/jwks.json  # JWKS reachable
curl http://localhost:8082/health                 # Relay
curl http://localhost:8080/api/v1/health          # legacy Core
curl http://localhost:8085/health                 # stub Edge (sim-FIRS)
```

Push a document through → legacy Core processes → stub Edge returns
`STUB_ACCEPTED` (simulated FIRS) → status flows back. No real FIRS, no fabricated
IRNs.

---

## Go-live cutover (LATER — not a demo-phase task)

Per handoff §7/§9: force-reset the 10 users off `666666`; mount a KMS-signed
non-test `license.json` + set `HEARTBEAT_LICENSE_REQUIRED=true` /
`HEARTBEAT_DEMO_MODE=false`; swap stub Edge for real Edge + wire real FIRS creds;
set `HEARTBEAT_KMS_KEY_ID` for encrypt-at-rest.
