# SDK Team — E2EE Encryption Implementation Note

**From:** Architecture Team (March 2026 Alignment Session)
**To:** Float SDK Team
**Date:** 2026-03-03
**Priority:** Implement when scheduled — not blocking current sprint
**Reference:** HeartBeat Service Contract Part 1 §3.7

---

## What Needs Building

The SDK's `relay_client.py` currently sends payloads to Relay in plaintext with HMAC-SHA256 signatures (integrity + authentication). The E2EE specification requires the SDK to **encrypt** payloads before sending to Relay using NaCl (X25519 + XSalsa20-Poly1305) for end-to-end confidentiality.

**Relay already has the decryption side built** at `Helium/Services/Relay/src/crypto/envelope.py`. The SDK needs the corresponding encryption side.

---

## The Contract

### Wire Format

```
[1 byte: version (0x01)] [32 bytes: ephemeral_public_key] [N bytes: ciphertext]
```

### Encryption Flow (SDK Side — What You Build)

```python
# 1. Generate ephemeral X25519 keypair (new keypair per request)
ephemeral_private = nacl.public.PrivateKey.generate()
ephemeral_public = ephemeral_private.public_key

# 2. Load Relay's static public key
#    Source: HeartBeat config API → GET /api/registry/config/relay
#    Config key: "relay_e2ee_public_key" (base64-encoded 32-byte X25519 public key)
relay_public_key = nacl.public.PublicKey(base64.b64decode(relay_public_key_b64))

# 3. Create NaCl Box (X25519 key agreement + XSalsa20-Poly1305 AEAD)
box = nacl.public.Box(ephemeral_private, relay_public_key)

# 4. Encrypt the payload (JSON body bytes)
ciphertext = box.encrypt(payload_bytes)
# Note: nacl.public.Box.encrypt() prepends a random 24-byte nonce automatically

# 5. Build the wire-format envelope
envelope = bytes([0x01]) + bytes(ephemeral_public) + ciphertext

# 6. Send with header X-Encrypted: true
# HMAC-SHA256 signature is computed on the ENVELOPE bytes (not plaintext)
```

### Decryption Flow (Relay Side — Already Built)

```python
# Relay reads envelope:
# - version byte (0x01)
# - ephemeral_public_key (32 bytes)
# - ciphertext (remaining bytes)
# Relay loads its static private key
# Relay creates Box(relay_private, ephemeral_public) and decrypts
# Relay then validates HMAC on the decrypted plaintext
```

### Key Exchange

The SDK obtains Relay's static public key from HeartBeat's service config API:

```
GET /api/registry/config/relay
Authorization: Bearer {user_jwt}

Response:
{
    "relay_e2ee_public_key": "base64-encoded-32-byte-x25519-public-key"
}
```

Cache this key locally. It changes only on key rotation (HeartBeat pushes `config.changed` SSE event when this happens — re-fetch on that event).

### Headers

When E2EE is active, the request to Relay includes:

```
X-Encrypted: true
X-API-Key: {float_api_key}
X-Timestamp: {iso8601}
X-Signature: {hmac_sha256 of envelope bytes}
```

When E2EE is not active (dev/test mode):

```
X-Encrypted: false
X-API-Key: {float_api_key}
X-Timestamp: {iso8601}
X-Signature: {hmac_sha256 of plaintext body bytes}
```

Relay checks `X-Encrypted` header to determine whether to decrypt before processing.

---

## Dependencies

- **PyNaCl** (`pynacl>=1.5.0`) — Python bindings for libsodium
- Already in Relay's requirements. Add to SDK's `requirements-prod.txt`

---

## Files to Modify

1. `SDK/src/integrations/relay_client.py` — add encryption before HTTP call
2. `SDK/src/sdk/relay_client.py` — same (if this is a separate relay client)
3. `SDK/src/sdk/config.py` — add `relay_e2ee_public_key` config field
4. `SDK/requirements-prod.txt` — add `pynacl>=1.5.0`

---

## Testing

- Unit test: encrypt with test keypair, verify wire format
- Integration test: encrypt payload, send to Relay test server, verify Relay decrypts correctly
- Relay's `test_envelope.py` has the decryption test fixtures — use matching keypairs

---

## Timeline

Not blocking current sprint. Implement when security hardening phase begins. Current HMAC-SHA256 provides integrity and authentication. E2EE adds confidentiality (protects payload content from network-level interception between SDK and Relay).

---

*End of E2EE SDK Team Note*
