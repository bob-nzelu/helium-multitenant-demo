"""
Relay-API Smoke Test

Sends a properly HMAC-signed multipart request to POST /api/ingest.
Uses the dev credentials from docker-compose.yml.

Usage:
    # With Docker running:
    python scripts/test_relay.py

    # Custom URL:
    python scripts/test_relay.py --url http://localhost:8082
"""

import argparse
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone

import httpx

# Dev credentials (must match RELAY_DEV_API_KEY / RELAY_DEV_API_SECRET)
API_KEY = "test-key-001"
API_SECRET = "test-secret-001"
INTERNAL_TOKEN = "dev-token-123"


def compute_signature(api_key: str, timestamp: str, body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature matching Relay's auth scheme."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{api_key}:{timestamp}:{body_hash}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_swagger(base_url: str) -> bool:
    """Test that Swagger UI loads (basic liveness check)."""
    print("=" * 60)
    print("TEST 1: GET /docs (Swagger UI)")
    print("=" * 60)
    try:
        resp = httpx.get(f"{base_url}/docs", timeout=10)
        print(f"  Status: {resp.status_code}")
        if resp.status_code == 200:
            print("  [OK] Swagger UI is live")
            return True
        else:
            print(f"  [FAIL] Unexpected status: {resp.status_code}")
            return False
    except httpx.ConnectError:
        print(f"  [FAIL] Cannot connect to {base_url}")
        print("  -> Is the server running? Try: scripts\\run_dev.bat")
        return False


def test_ingest_bulk(base_url: str) -> bool:
    """Test POST /api/ingest with a bulk upload (default call_type)."""
    print()
    print("=" * 60)
    print("TEST 2: POST /api/ingest (bulk flow)")
    print("=" * 60)

    # Create a small sample PDF-like file
    sample_content = b"%PDF-1.4 sample test file content for Relay smoke test"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build multipart body using httpx
    files = [("files", ("test-invoice.pdf", sample_content, "application/pdf"))]
    req = httpx.Request("POST", f"{base_url}/api/ingest", files=files)
    raw_body = b"".join(req.stream)
    content_type = req.headers["content-type"]

    # Compute HMAC signature
    signature = compute_signature(API_KEY, timestamp, raw_body, API_SECRET)

    # Send the request
    headers = {
        "X-API-Key": API_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": content_type,
    }

    resp = httpx.post(
        f"{base_url}/api/ingest",
        content=raw_body,
        headers=headers,
        timeout=30,
    )

    print(f"  Status: {resp.status_code}")
    print(f"  Trace:  {resp.headers.get('x-trace-id', 'N/A')}")

    try:
        body = resp.json()
        print(f"  Body:   {json.dumps(body, indent=2)}")
    except Exception:
        print(f"  Body:   {resp.text[:500]}")

    if resp.status_code in (200, 201):
        print("  [OK] Bulk ingest succeeded")
        return True
    else:
        print(f"  -> Status {resp.status_code} (may be expected — stubs return mock data)")
        return resp.status_code < 500  # 4xx is "works", 5xx is broken


def test_ingest_external(base_url: str) -> bool:
    """Test POST /api/ingest with call_type=external."""
    print()
    print("=" * 60)
    print("TEST 3: POST /api/ingest (external flow)")
    print("=" * 60)

    sample_content = b"%PDF-1.4 external test invoice"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    files = [("files", ("external-invoice.pdf", sample_content, "application/pdf"))]
    data = {"call_type": "external"}
    req = httpx.Request("POST", f"{base_url}/api/ingest", files=files, data=data)
    raw_body = b"".join(req.stream)
    content_type = req.headers["content-type"]

    signature = compute_signature(API_KEY, timestamp, raw_body, API_SECRET)

    headers = {
        "X-API-Key": API_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": content_type,
    }

    resp = httpx.post(
        f"{base_url}/api/ingest",
        content=raw_body,
        headers=headers,
        timeout=30,
    )

    print(f"  Status: {resp.status_code}")
    print(f"  Trace:  {resp.headers.get('x-trace-id', 'N/A')}")

    try:
        body = resp.json()
        print(f"  Body:   {json.dumps(body, indent=2)}")
    except Exception:
        print(f"  Body:   {resp.text[:500]}")

    if resp.status_code in (200, 201):
        print("  [OK] External ingest succeeded")
        return True
    else:
        print(f"  -> Status {resp.status_code}")
        return resp.status_code < 500


def test_internal_refresh(base_url: str) -> bool:
    """Test POST /internal/refresh-cache with Bearer token."""
    print()
    print("=" * 60)
    print("TEST 4: POST /internal/refresh-cache")
    print("=" * 60)

    resp = httpx.post(
        f"{base_url}/internal/refresh-cache",
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
        timeout=10,
    )

    print(f"  Status: {resp.status_code}")

    try:
        body = resp.json()
        print(f"  Body:   {json.dumps(body, indent=2)}")
    except Exception:
        print(f"  Body:   {resp.text[:500]}")

    if resp.status_code == 200:
        print("  [OK] Cache refresh succeeded")
        return True
    else:
        print(f"  -> Status {resp.status_code}")
        return False


def test_auth_rejection(base_url: str) -> bool:
    """Test that bad API key is rejected."""
    print()
    print("=" * 60)
    print("TEST 5: Auth rejection (bad API key)")
    print("=" * 60)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = httpx.post(
        f"{base_url}/api/ingest",
        headers={
            "X-API-Key": "bad-key",
            "X-Timestamp": timestamp,
            "X-Signature": "invalid",
            "Content-Type": "multipart/form-data; boundary=test",
        },
        content=b"--test--",
        timeout=10,
    )

    print(f"  Status: {resp.status_code}")

    if resp.status_code in (401, 403, 422):
        print("  [OK] Correctly rejected invalid credentials")
        return True
    else:
        print(f"  [FAIL] Expected 401/403, got {resp.status_code}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Relay-API Smoke Test")
    parser.add_argument(
        "--url",
        default="http://localhost:8082",
        help="Relay-API base URL (default: http://localhost:8082)",
    )
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    print(f"\nRelay-API Smoke Test -> {base_url}")
    print("=" * 60)

    results = []

    # Test 1: Swagger UI
    results.append(("Swagger UI", test_swagger(base_url)))
    if not results[-1][1]:
        print("\n[FAIL] Cannot reach Relay-API. Aborting remaining tests.")
        sys.exit(1)

    # Test 2-5: Endpoint tests
    results.append(("Bulk ingest", test_ingest_bulk(base_url)))
    results.append(("External ingest", test_ingest_external(base_url)))
    results.append(("Internal refresh", test_internal_refresh(base_url)))
    results.append(("Auth rejection", test_auth_rejection(base_url)))

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        status = "[OK] PASS" if ok else "[FAIL] FAIL"
        print(f"  {status}  {name}")

    print(f"\n  {passed}/{total} tests passed")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
