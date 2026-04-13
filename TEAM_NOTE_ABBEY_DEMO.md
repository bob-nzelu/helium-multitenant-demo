# Team Note — Abbey Mortgage Demo Endpoints

**Date:** 13 April 2026
**Server:** `13.247.224.147` (t3.medium, Ubuntu 24.04)
**Status:** All services healthy and running

---

## Auth Endpoints (Mock — HEARTBEAT_MOCK_AUTH=true)

**Base URL:** `http://13.247.224.147:9000`

### User Credentials

| Field | Value |
|-------|-------|
| **Email** | `Charles.Omoakin@abbeymortgagebank.com` |
| **First-time password** | `123456` |
| **Role** | Owner |
| **Tenant** | `tenant-abbey-001` |
| **is_first_run** | `true` (forces password change on first login) |

---

### POST /api/auth/login

First-time login (returns bootstrap-scoped token, `is_first_run: true`):

```bash
curl -X POST http://13.247.224.147:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "Charles.Omoakin@abbeymortgagebank.com", "password": "123456"}'
```

**Response:** JWT with `scope: "bootstrap"`. Float MUST show password change dialog.

After password change, normal login:

```bash
curl -X POST http://13.247.224.147:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "Charles.Omoakin@abbeymortgagebank.com", "password": "<new_password>"}'
```

**Response:** Full JWT with `is_first_run: false`. Float proceeds to main window.

---

### POST /api/auth/password/change

Bootstrap mode (first-time, no current_password required):

```bash
curl -X POST http://13.247.224.147:9000/api/auth/password/change \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <bootstrap_token>" \
  -d '{"new_password": "MyNewSecurePass1"}'
```

Normal mode (requires current_password):

```bash
curl -X POST http://13.247.224.147:9000/api/auth/password/change \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"current_password": "MyNewSecurePass1", "new_password": "EvenBetterPass2"}'
```

**Response:** `{"status": "password_changed", "is_first_run": false}`
After this, user must log in again with new password.

---

### POST /api/auth/token/refresh

```bash
curl -X POST http://13.247.224.147:9000/api/auth/token/refresh \
  -H "Authorization: Bearer <current_token>"
```

**Response:** New JWT + cipher_text (30-min expiry, same session hard cap).

---

### POST /api/auth/logout

```bash
curl -X POST http://13.247.224.147:9000/api/auth/logout \
  -H "Authorization: Bearer <token>"
```

**Response:** `{"status": "logged_out"}`

---

### POST /api/auth/introspect

Service-to-service token verification:

```bash
curl -X POST http://13.247.224.147:9000/api/auth/introspect \
  -H "Content-Type: application/json" \
  -d '{"token": "<jwt>", "required_permission": "invoice.finalize", "required_within_seconds": 300}'
```

**Response:** `{"active": true, "user_id": "usr-abbey-owner-001", "role": "Owner", "permissions": ["*"], "step_up_satisfied": true}`

---

### POST /api/auth/stepup

Re-authenticate for sensitive operations:

```bash
curl -X POST http://13.247.224.147:9000/api/auth/stepup \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"password": "<current_password>"}'
```

**Response:** New JWT with fresh `last_auth_at`.

---

### GET /api/auth/operations/{operation}/policy

Query step-up freshness requirement:

```bash
curl http://13.247.224.147:9000/api/auth/operations/invoice.finalize/policy
```

**Response:** `{"operation": "invoice.finalize", "required_within_seconds": 300, "tier": "standard"}`

---

## Other Service Endpoints

| Service | Port | Health Check |
|---------|------|-------------|
| HeartBeat | 9000 | `curl http://13.247.224.147:9000/health` |
| Relay API | 8082 | `curl http://13.247.224.147:8082/health` |
| Core | 8080 | `curl http://13.247.224.147:8080/api/v1/health` |
| Simulator | 8090 | Not deployed yet (dedicated session) |

---

## Flow for Float Integration

1. Float config points HeartBeat URL to `http://13.247.224.147:9000`
2. User enters `Charles.Omoakin@abbeymortgagebank.com` / `123456`
3. HeartBeat returns `is_first_run: true` → Float shows password change dialog
4. User sets new password → HeartBeat confirms → Float shows login screen
5. User logs in with new password → `is_first_run: false` → Float opens main window
6. JWT refresh every 25 minutes (silent, via `/api/auth/token/refresh`)
7. cipher_text used for sync.db encryption key derivation

---

## Important Notes

- **Mock auth resets on HeartBeat restart** — password goes back to `123456`, `is_first_run` goes back to `true`
- **No real JWT crypto** — token looks like a JWT but isn't Ed25519-signed. Services that call `/introspect` will get mock responses.
- **Single user only** — only Charles Omoakin works. Other emails return 401.
- **Production auth** is fully built in HeartBeat's real auth system. This mock is toggled by `HEARTBEAT_MOCK_AUTH=true` env var.
