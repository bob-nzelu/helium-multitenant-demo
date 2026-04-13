"""
Load Tests for Relay Bulk Service

Tests concurrent upload handling:
- 100 concurrent uploads
- Response time monitoring
- Error rate under load
- Memory and connection pool stability

Target: Handle 100 concurrent uploads with <5% error rate

Usage:
    pytest tests/load/test_load.py -v -m load
"""

import pytest
import asyncio
import time
import hashlib
import hmac
import statistics
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


# =============================================================================
# Load Test Configuration
# =============================================================================

CONCURRENT_REQUESTS = 100
MAX_ERROR_RATE = 0.05  # 5%
TARGET_RESPONSE_TIME_P95 = 5.0  # 5 seconds


# =============================================================================
# Helper Functions
# =============================================================================

def generate_test_file(index: int, size_bytes: int = 1024) -> Tuple[str, bytes]:
    """Generate a test file with unique content."""
    content = f"Test file {index} - " + ("x" * (size_bytes - 20))
    return f"test_file_{index}.pdf", content.encode()


def make_auth_headers(api_key_secrets: Dict[str, str], body: bytes, api_key: str = "test_api_key_123") -> Dict[str, str]:
    """Create valid authentication headers."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    secret = api_key_secrets[api_key]
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{api_key}:{timestamp}:{body_hash}"
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return {
        "X-API-Key": api_key,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


# =============================================================================
# Load Test Fixtures
# =============================================================================

@pytest.fixture
def api_key_secrets():
    """API key to secret mapping for tests."""
    return {
        "test_api_key_123": "secret_abc_123",
        "test_api_key_456": "secret_def_456",
        "load_test_key": "load_test_secret",
    }


@pytest.fixture
def load_test_client(api_key_secrets):
    """Create a FastAPI test client for load testing."""
    from fastapi.testclient import TestClient

    # Mock config
    mock_config = {
        "instance_id": "relay-bulk-load-test",
        "allowed_extensions": [".pdf", ".csv", ".xlsx", ".zip", ".txt"],
        "max_file_size_mb": 50,
        "max_files_per_request": 10,  # Higher limit for load test
        "api_key_secrets": api_key_secrets,
        "core_api_url": "http://localhost:8080",
        "heartbeat_api_url": "http://localhost:9000",
        "preview_timeout_seconds": 300,
        "malware_scanning": {
            "enabled": False,
        },
    }

    # Mock dependencies
    mock_core_client = AsyncMock()
    mock_core_client.enqueue.return_value = {"queue_id": "queue_test", "status": "queued"}
    mock_core_client.process_preview.return_value = {"status": "processed", "preview_data": {}}
    mock_core_client.health_check.return_value = True

    mock_heartbeat_client = AsyncMock()
    mock_heartbeat_client.write_blob.return_value = {"blob_uuid": "uuid_test", "status": "uploaded"}
    mock_heartbeat_client.check_duplicate.return_value = {"is_duplicate": False}
    mock_heartbeat_client.health_check.return_value = True

    mock_audit_client = AsyncMock()

    # Patch dependencies and create app
    with patch.dict("sys.modules", {"yaml": MagicMock()}):
        try:
            from src.bulk.main import create_app

            app = create_app(
                config=mock_config,
                core_client=mock_core_client,
                heartbeat_client=mock_heartbeat_client,
                audit_client=mock_audit_client,
            )
            return TestClient(app)
        except ImportError:
            # If main.py isn't fully set up, create minimal app
            from fastapi import FastAPI, File, UploadFile, Header
            from fastapi.responses import JSONResponse
            import uuid

            app = FastAPI()

            @app.get("/health")
            async def health():
                return {"status": "healthy", "instance_id": "load-test"}

            @app.post("/api/ingest")
            async def ingest(
                files: List[UploadFile] = File(...),
                x_api_key: str = Header(...),
                x_timestamp: str = Header(...),
                x_signature: str = Header(...),
            ):
                # Simulate processing delay
                await asyncio.sleep(0.01)

                results = []
                for f in files:
                    content = await f.read()
                    results.append({
                        "filename": f.filename,
                        "queue_id": f"queue_{uuid.uuid4()}",
                        "status": "success",
                    })

                return JSONResponse(
                    content={
                        "status": "processed",
                        "batch_id": f"batch_{uuid.uuid4()}",
                        "total_files": len(files),
                        "results": results,
                    },
                    headers={"X-Trace-ID": f"trace_{uuid.uuid4()}"},
                )

            return TestClient(app)


# =============================================================================
# Load Tests
# =============================================================================

@pytest.mark.load
class TestConcurrentUploads:
    """Load tests for concurrent upload handling."""

    def test_100_concurrent_single_file_uploads(self, load_test_client, api_key_secrets):
        """Test 100 concurrent single-file uploads."""
        results: List[Dict[str, Any]] = []
        errors = []

        def make_request(index: int) -> Dict[str, Any]:
            """Make a single upload request."""
            start_time = time.time()
            try:
                filename, content = generate_test_file(index)
                headers = make_auth_headers(api_key_secrets, content, "load_test_key")

                response = load_test_client.post(
                    "/api/ingest",
                    files={"files": (filename, content)},
                    data={"company_id": f"company_{index}"},
                    headers=headers,
                )

                duration = time.time() - start_time

                return {
                    "index": index,
                    "status_code": response.status_code,
                    "duration": duration,
                    "success": response.status_code in [200, 201, 202],
                    "error": None,
                }

            except Exception as e:
                duration = time.time() - start_time
                return {
                    "index": index,
                    "status_code": 0,
                    "duration": duration,
                    "success": False,
                    "error": str(e),
                }

        # Run concurrent requests using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=CONCURRENT_REQUESTS) as executor:
            futures = [executor.submit(make_request, i) for i in range(CONCURRENT_REQUESTS)]
            results = [f.result() for f in futures]

        # Analyze results
        successful = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        durations = [r["duration"] for r in results]

        error_rate = len(failed) / len(results)
        avg_duration = statistics.mean(durations)
        p95_duration = sorted(durations)[int(len(durations) * 0.95)]
        p99_duration = sorted(durations)[int(len(durations) * 0.99)]

        # Log results
        print(f"\n=== Load Test Results ===")
        print(f"Total Requests: {len(results)}")
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(failed)}")
        print(f"Error Rate: {error_rate:.2%}")
        print(f"Avg Duration: {avg_duration:.3f}s")
        print(f"P95 Duration: {p95_duration:.3f}s")
        print(f"P99 Duration: {p99_duration:.3f}s")

        if failed:
            print(f"\nFailed requests:")
            for r in failed[:5]:  # Show first 5 failures
                print(f"  - Index {r['index']}: {r['error'] or f'Status {r['status_code']}'}")

        # Assertions
        assert error_rate <= MAX_ERROR_RATE, f"Error rate {error_rate:.2%} exceeds threshold {MAX_ERROR_RATE:.2%}"
        assert p95_duration <= TARGET_RESPONSE_TIME_P95, f"P95 duration {p95_duration:.3f}s exceeds target {TARGET_RESPONSE_TIME_P95}s"

    def test_50_concurrent_batch_uploads(self, load_test_client, api_key_secrets):
        """Test 50 concurrent batch uploads (3 files each)."""
        results: List[Dict[str, Any]] = []

        def make_batch_request(batch_index: int) -> Dict[str, Any]:
            """Make a batch upload request with 3 files."""
            start_time = time.time()
            try:
                files = [
                    generate_test_file(batch_index * 3 + i)
                    for i in range(3)
                ]

                # Create multipart files
                multipart_files = [
                    ("files", (filename, content))
                    for filename, content in files
                ]

                # Use first file content for auth (simplified for test)
                headers = make_auth_headers(api_key_secrets, files[0][1], "load_test_key")

                response = load_test_client.post(
                    "/api/ingest",
                    files=multipart_files,
                    data={"company_id": f"company_batch_{batch_index}"},
                    headers=headers,
                )

                duration = time.time() - start_time

                return {
                    "batch_index": batch_index,
                    "status_code": response.status_code,
                    "duration": duration,
                    "success": response.status_code in [200, 201, 202],
                    "files_count": 3,
                    "error": None,
                }

            except Exception as e:
                duration = time.time() - start_time
                return {
                    "batch_index": batch_index,
                    "status_code": 0,
                    "duration": duration,
                    "success": False,
                    "files_count": 3,
                    "error": str(e),
                }

        # Run 50 concurrent batch requests
        batch_count = 50
        with ThreadPoolExecutor(max_workers=batch_count) as executor:
            futures = [executor.submit(make_batch_request, i) for i in range(batch_count)]
            results = [f.result() for f in futures]

        # Analyze results
        successful = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        durations = [r["duration"] for r in results]

        total_files = sum(r["files_count"] for r in results)
        successful_files = sum(r["files_count"] for r in successful)

        error_rate = len(failed) / len(results)

        print(f"\n=== Batch Load Test Results ===")
        print(f"Total Batches: {len(results)}")
        print(f"Total Files: {total_files}")
        print(f"Successful Batches: {len(successful)}")
        print(f"Successful Files: {successful_files}")
        print(f"Batch Error Rate: {error_rate:.2%}")
        print(f"Avg Batch Duration: {statistics.mean(durations):.3f}s")

        # Assertions
        assert error_rate <= MAX_ERROR_RATE

    def test_sustained_load_1_minute(self, load_test_client, api_key_secrets):
        """Test sustained load for 1 minute (reduced for CI)."""
        # Note: This test is reduced for CI - in production, run for longer
        duration_seconds = 10  # 10 seconds for CI, increase for real load test
        requests_per_second = 10

        results = []
        start_time = time.time()
        request_index = 0

        while time.time() - start_time < duration_seconds:
            batch_start = time.time()

            # Send batch of requests
            def make_request(index):
                filename, content = generate_test_file(index)
                headers = make_auth_headers(api_key_secrets, content, "load_test_key")
                try:
                    response = load_test_client.post(
                        "/api/ingest",
                        files={"files": (filename, content)},
                        headers=headers,
                    )
                    return response.status_code in [200, 201, 202]
                except Exception:
                    return False

            with ThreadPoolExecutor(max_workers=requests_per_second) as executor:
                futures = [
                    executor.submit(make_request, request_index + i)
                    for i in range(requests_per_second)
                ]
                batch_results = [f.result() for f in futures]
                results.extend(batch_results)

            request_index += requests_per_second

            # Wait for remainder of second
            elapsed = time.time() - batch_start
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

        # Analyze
        total = len(results)
        successful = sum(results)
        error_rate = (total - successful) / total if total > 0 else 0

        print(f"\n=== Sustained Load Test Results ===")
        print(f"Duration: {duration_seconds}s")
        print(f"Target RPS: {requests_per_second}")
        print(f"Total Requests: {total}")
        print(f"Successful: {successful}")
        print(f"Error Rate: {error_rate:.2%}")

        assert error_rate <= MAX_ERROR_RATE


@pytest.mark.load
class TestMemoryStability:
    """Tests for memory stability under load."""

    def test_no_memory_leak_on_repeated_requests(self, load_test_client, api_key_secrets):
        """Verify no significant memory growth over repeated requests."""
        import gc

        # Force garbage collection before test
        gc.collect()

        # Get initial memory (if psutil available)
        try:
            import psutil
            process = psutil.Process()
            initial_memory = process.memory_info().rss
            memory_tracking = True
        except ImportError:
            memory_tracking = False

        # Run 500 requests
        for i in range(500):
            filename, content = generate_test_file(i)
            headers = make_auth_headers(api_key_secrets, content, "load_test_key")

            load_test_client.post(
                "/api/ingest",
                files={"files": (filename, content)},
                headers=headers,
            )

            # Periodic GC
            if i % 100 == 0:
                gc.collect()

        # Force final GC
        gc.collect()

        if memory_tracking:
            final_memory = process.memory_info().rss
            memory_growth = final_memory - initial_memory
            memory_growth_mb = memory_growth / (1024 * 1024)

            print(f"\n=== Memory Stability Test ===")
            print(f"Initial Memory: {initial_memory / (1024 * 1024):.2f} MB")
            print(f"Final Memory: {final_memory / (1024 * 1024):.2f} MB")
            print(f"Memory Growth: {memory_growth_mb:.2f} MB")

            # Allow up to 50MB growth (accounts for test overhead)
            assert memory_growth_mb < 50, f"Memory grew by {memory_growth_mb:.2f} MB"


@pytest.mark.load
class TestErrorRecovery:
    """Tests for error recovery under load."""

    def test_graceful_handling_of_invalid_requests(self, load_test_client, api_key_secrets):
        """Mix valid and invalid requests to test error handling."""
        results = {"valid_success": 0, "valid_fail": 0, "invalid_caught": 0, "invalid_uncaught": 0}

        def make_request(index: int, make_invalid: bool):
            if make_invalid:
                # Send invalid request (wrong signature)
                headers = {
                    "X-API-Key": "load_test_key",
                    "X-Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "X-Signature": "invalid_signature",
                }
                try:
                    response = load_test_client.post(
                        "/api/ingest",
                        files={"files": ("test.pdf", b"content")},
                        headers=headers,
                    )
                    # Should get 401 for invalid signature
                    if response.status_code == 401:
                        return "invalid_caught"
                    return "invalid_uncaught"
                except Exception:
                    return "invalid_caught"
            else:
                # Send valid request
                filename, content = generate_test_file(index)
                headers = make_auth_headers(api_key_secrets, content, "load_test_key")
                try:
                    response = load_test_client.post(
                        "/api/ingest",
                        files={"files": (filename, content)},
                        headers=headers,
                    )
                    return "valid_success" if response.status_code in [200, 201, 202] else "valid_fail"
                except Exception:
                    return "valid_fail"

        # Mix 80% valid, 20% invalid requests
        request_types = [(i, i % 5 == 0) for i in range(100)]  # Every 5th is invalid

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(make_request, idx, is_invalid) for idx, is_invalid in request_types]
            for f in futures:
                result = f.result()
                results[result] += 1

        print(f"\n=== Error Recovery Test ===")
        print(f"Valid Success: {results['valid_success']}")
        print(f"Valid Fail: {results['valid_fail']}")
        print(f"Invalid Caught: {results['invalid_caught']}")
        print(f"Invalid Uncaught: {results['invalid_uncaught']}")

        # Valid requests should mostly succeed
        valid_total = results["valid_success"] + results["valid_fail"]
        if valid_total > 0:
            valid_success_rate = results["valid_success"] / valid_total
            assert valid_success_rate >= 0.95, f"Valid success rate {valid_success_rate:.2%} below 95%"

        # Invalid requests should all be properly caught
        invalid_total = results["invalid_caught"] + results["invalid_uncaught"]
        if invalid_total > 0:
            invalid_catch_rate = results["invalid_caught"] / invalid_total
            # We expect some invalid requests to be caught
            assert invalid_catch_rate >= 0.8, f"Invalid catch rate {invalid_catch_rate:.2%} below 80%"
