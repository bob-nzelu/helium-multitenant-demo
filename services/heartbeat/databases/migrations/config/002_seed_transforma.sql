-- ============================================================================
-- MIGRATION 002: Seed Transforma modules and FIRS service keys
-- Database: config.db
-- Date: 2026-03-04
--
-- Seeds config_entries with default/stub Transforma module source code
-- and FIRS service keys. These are served to Relay via
-- GET /api/platform/transforma/config.
--
-- In production, the Installer will replace these stubs with real modules.
-- This seed provides working defaults for development and testing.
-- ============================================================================

-- IRN Generator module (stub)
INSERT INTO config_entries (
    service_name, config_key, config_value, value_type,
    description, is_readonly, created_at, updated_at, updated_by
) VALUES (
    'transforma',
    'irn_generator',
    '{"module_name": "irn_generator", "source_code": "def generate_irn(invoice_data: dict) -> str:\n    \"\"\"Generate Invoice Reference Number (stub).\"\"\"\n    inv_num = invoice_data.get(\"invoice_number\", \"000\")\n    tin = invoice_data.get(\"tin\", \"0000000000\")\n    import hashlib, time\n    ts = str(int(time.time()))\n    hash_part = hashlib.sha256(f\"{tin}{inv_num}{ts}\".encode()).hexdigest()[:8].upper()\n    return f\"{tin[:4]}-{hash_part}-{ts[-6:]}\"\n", "version": "1.0.0-stub", "checksum": "sha256:stub_irn_checksum", "updated_at": "2026-03-04T00:00:00Z"}',
    'json',
    'Transforma IRN generator module source code (Python). Used by Relay to generate Invoice Reference Numbers.',
    0,
    datetime('now'),
    datetime('now'),
    'migration-002'
) ON CONFLICT(service_name, config_key) DO NOTHING;

-- QR Generator module (stub)
INSERT INTO config_entries (
    service_name, config_key, config_value, value_type,
    description, is_readonly, created_at, updated_at, updated_by
) VALUES (
    'transforma',
    'qr_generator',
    '{"module_name": "qr_generator", "source_code": "import base64, json\nfrom datetime import datetime\n\ndef generate_qr_data(irn: str, keys=None) -> str:\n    \"\"\"Generate QR code data (base64-encoded JSON) (stub).\"\"\"\n    payload = {\"irn\": irn, \"demo\": True, \"timestamp\": datetime.now().isoformat()}\n    return base64.b64encode(json.dumps(payload).encode()).decode()\n\ndef create_qr_image_bytes(qr_data: str) -> bytes:\n    \"\"\"Generate QR code image as PNG bytes (stub).\"\"\"\n    return b\"PNG_STUB_QR_IMAGE\"\n", "version": "1.0.0-stub", "checksum": "sha256:stub_qr_checksum", "updated_at": "2026-03-04T00:00:00Z"}',
    'json',
    'Transforma QR code generator module source code (Python). Used by Relay for QR code generation on invoices.',
    0,
    datetime('now'),
    datetime('now'),
    'migration-002'
) ON CONFLICT(service_name, config_key) DO NOTHING;

-- FIRS Service Keys (stub)
INSERT INTO config_entries (
    service_name, config_key, config_value, value_type,
    description, is_readonly, created_at, updated_at, updated_by
) VALUES (
    'transforma',
    'service_keys',
    '{"firs_public_key_pem": "-----BEGIN PUBLIC KEY-----\nSTUB_KEY_FOR_DEVELOPMENT\n-----END PUBLIC KEY-----", "csid": "STUB-CSID-TOKEN", "csid_expires_at": "2030-01-01T00:00:00Z", "certificate": "c3R1Yl9jZXJ0"}',
    'json',
    'FIRS service keys: public key PEM, CSID token, expiry, and certificate. Used by Relay for QR code encryption.',
    0,
    datetime('now'),
    datetime('now'),
    'migration-002'
) ON CONFLICT(service_name, config_key) DO NOTHING;
