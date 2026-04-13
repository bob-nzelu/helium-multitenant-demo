"""
End-to-end encryption layer (X25519 + AES-256-GCM via PyNaCl).

Cloudflare (or any proxy) never sees raw invoice data.
All callers encrypt before sending; Relay decrypts on arrival.
"""
