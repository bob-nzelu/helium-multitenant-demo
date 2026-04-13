"""
Stub HeartBeatClient for tests that don't need real HTTP.

All service/integration tests use this instead of the real HeartBeatClient
which now makes actual httpx calls to HeartBeat endpoints.

HeartBeatClient tests themselves use respx to mock HTTP responses.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from uuid6 import uuid7

from src.clients.heartbeat import HeartBeatClient


class StubHeartBeatClient(HeartBeatClient):
    """
    HeartBeatClient subclass that returns stub data without HTTP.

    Preserves _calls and _audit_events tracking for test assertions.
    """

    async def write_blob(self, blob_uuid, filename, file_data,
                         metadata=None, jwt_token=None):
        file_hash = hashlib.sha256(file_data).hexdigest()
        blob_path = f"/files_blob/{blob_uuid}-{filename}"
        self._calls.append(("write_blob", blob_uuid, filename))
        return {
            "blob_uuid": blob_uuid,
            "blob_path": blob_path,
            "file_size_bytes": len(file_data),
            "file_hash": file_hash,
            "status": "uploaded",
        }

    async def check_duplicate(self, file_hash):
        self._calls.append(("check_duplicate", file_hash))
        return {
            "is_duplicate": False,
            "file_hash": file_hash,
            "original_queue_id": None,
        }

    async def record_duplicate(self, file_hash, queue_id):
        self._calls.append(("record_duplicate", file_hash, queue_id))
        return {"file_hash": file_hash, "queue_id": queue_id, "status": "recorded"}

    async def check_daily_limit(self, company_id, file_count=1):
        self._calls.append(("check_daily_limit", company_id, file_count))
        return {
            "company_id": company_id,
            "files_today": 0,
            "daily_limit": 500,
            "limit_reached": False,
            "remaining": 500,
        }

    async def register_blob(self, blob_uuid, filename, file_size_bytes,
                            file_hash, api_key, metadata=None, jwt_token=None):
        self._calls.append(("register_blob", blob_uuid))
        return {"blob_uuid": blob_uuid, "status": "registered",
                "tracking_id": f"track_{uuid7()}"}

    async def health_check(self):
        return True

    async def audit_log(self, service, event_type, user_id=None, details=None):
        event = {
            "service": service,
            "event_type": event_type,
            "user_id": user_id,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
        }
        self._audit_events.append(event)
        self._calls.append(("audit_log", event_type))

    async def report_metrics(self, metric_type, values):
        self._calls.append(("report_metrics", metric_type))

    async def get_transforma_config(self):
        self._calls.append(("get_transforma_config",))
        return {
            "modules": [
                {
                    "module_name": "irn_generator",
                    "source_code": (
                        'def generate_irn(invoice_data: dict) -> str:\n'
                        '    """Generate Invoice Reference Number."""\n'
                        '    inv_num = invoice_data.get("invoice_number", "000")\n'
                        '    tin = invoice_data.get("tin", "0000000000")\n'
                        '    import hashlib, time\n'
                        '    ts = str(int(time.time()))\n'
                        '    hash_part = hashlib.sha256(f"{tin}{inv_num}{ts}".encode()).hexdigest()[:8].upper()\n'
                        '    return f"{tin[:4]}-{hash_part}-{ts[-6:]}"\n'
                    ),
                    "version": "1.0.0-stub",
                    "checksum": "sha256:stub_irn_checksum",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                {
                    "module_name": "qr_generator",
                    "source_code": (
                        'import base64, json\n'
                        'from datetime import datetime\n'
                        '\n'
                        'def generate_qr_data(irn: str, keys=None) -> str:\n'
                        '    """Generate QR code data (base64-encoded JSON)."""\n'
                        '    payload = {"irn": irn, "demo": True,\n'
                        '               "timestamp": datetime.now().isoformat()}\n'
                        '    return base64.b64encode(\n'
                        '        json.dumps(payload).encode()\n'
                        '    ).decode()\n'
                        '\n'
                        'def create_qr_image_bytes(qr_data: str) -> bytes:\n'
                        '    """Generate QR code image as PNG bytes (stub)."""\n'
                        '    return b"PNG_STUB_QR_IMAGE"\n'
                    ),
                    "version": "1.0.0-stub",
                    "checksum": "sha256:stub_qr_checksum",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            ],
            "service_keys": {
                "firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\nSTUB_KEY\n-----END PUBLIC KEY-----",
                "csid": "STUB-CSID-TOKEN",
                "csid_expires_at": "2030-01-01T00:00:00Z",
                "certificate": "c3R1Yl9jZXJ0",
            },
        }

    async def close(self):
        """No-op for stub."""
        pass
