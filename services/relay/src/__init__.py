"""
Relay-API Service — Helium's FrontDoor

Unified ingestion service handling both Float bulk uploads (synchronous,
waits for Core preview) and external API calls (fire-and-forget, returns
IRN + QR immediately). All traffic is end-to-end encrypted via
X25519 + AES-256-GCM.

Production: Runs behind Cloudflare tunnel on a shared multi-tenant instance.
Development: Float launches as subprocess on localhost:8082.
"""
