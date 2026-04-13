-- ============================================================================
-- BLOB STORAGE DATABASE SEED DATA — Canonical v1.4.0
-- Database: helium_blob.db
-- Purpose: Initialize reference + dev data for blob storage
-- Version: 3.0  (Canonical schema — file_entries + batch_display_id PKs)
-- Date: 2026-03-30
--
-- Blob UUIDs use a deterministic pattern for dev convenience:
--   dev00001-0000-0000-0000-000000000001  through  ...0014
--
-- Upload scenarios represented:
--   Batch upload via Float SDK   — Blobs 01, 02, 05 (3-file batch)
--   Single file via Relay bulk   — Blobs 08, 09, 11, 14
--   Single file via NAS watcher  — Blobs 03, 06, 10, 13
--   Single file via ERP API call — Blobs 04, 07
--   Single file via email relay  — Blob 12
-- ============================================================================

BEGIN TRANSACTION;

-- ============================================================================
-- 1. relay_services (Reference Data)
-- ============================================================================
INSERT OR IGNORE INTO relay_services (instance_id, relay_type, is_active, created_at) VALUES
    ('execujet-bulk-1', 'bulk', 1, datetime('now')),
    ('execujet-bulk-2', 'bulk', 1, datetime('now')),
    ('execujet-nas-1', 'nas', 1, datetime('now')),
    ('execujet-nas-2', 'nas', 1, datetime('now')),
    ('execujet-erp-1', 'erp', 1, datetime('now')),
    ('execujet-email-1', 'email', 1, datetime('now'));


-- ============================================================================
-- 2. blob_batches (canonical v1.4.0 — batch_display_id PK)
-- ============================================================================

-- Batch 1: 3-file batch from Float SDK
INSERT OR IGNORE INTO blob_batches (
    batch_display_id, batch_uuid, source, queue_mode,
    file_count, original_filename_pattern, total_size_bytes,
    status, pending_sync, upload_status, processing_time_seconds,
    uploaded_at_unix, uploaded_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    created_at, updated_at
) VALUES (
    'HBB-batch0001-0000-0000-0000-000000000001',
    'batch0001-0000-0000-0000-000000000001',
    'execujet-bulk-1', 'bulk',
    3, '*.pdf', 459800,
    'finalized', 0, 'finalized', 120.0,
    1739750400, '2025-02-17T00:00:00+00:00',
    1739750520, '2025-02-17T00:02:00+00:00',
    1960588800, '2032-02-17T00:00:00+00:00',
    datetime('now'), datetime('now')
);

-- Auto-created single-file batches for non-batch uploads
INSERT OR IGNORE INTO blob_batches (
    batch_display_id, batch_uuid, source, queue_mode,
    file_count, status, pending_sync, upload_status,
    uploaded_at_unix, uploaded_at_iso,
    retention_until_unix, retention_until_iso,
    created_at, updated_at
) VALUES
    ('HBB-dev00001-0000-0000-0000-000000000003', 'dev00001-0000-0000-0000-000000000003', 'execujet-nas-1', 'polling', 1, 'finalized', 0, 'finalized', 1739923200, '2025-02-19T00:00:00+00:00', 1960761600, '2032-02-19T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000004', 'dev00001-0000-0000-0000-000000000004', 'execujet-erp-1', 'api', 1, 'finalized', 0, 'finalized', 1740009600, '2025-02-20T00:00:00+00:00', 1960848000, '2032-02-20T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000006', 'dev00001-0000-0000-0000-000000000006', 'execujet-nas-1', 'polling', 1, 'processing', 0, 'processing', 1740182400, '2025-02-22T00:00:00+00:00', 1961020800, '2032-02-22T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000007', 'dev00001-0000-0000-0000-000000000007', 'execujet-erp-1', 'api', 1, 'processing', 0, 'processing', 1740268800, '2025-02-23T00:00:00+00:00', 1961107200, '2032-02-23T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000008', 'dev00001-0000-0000-0000-000000000008', 'execujet-bulk-2', 'bulk', 1, 'processing', 0, 'processing', 1740355200, '2025-02-24T00:00:00+00:00', 1961193600, '2032-02-24T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000009', 'dev00001-0000-0000-0000-000000000009', 'execujet-bulk-1', 'bulk', 1, 'preview_pending', 0, 'preview_pending', 1740441600, '2025-02-25T00:00:00+00:00', 1961280000, '2032-02-25T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000010', 'dev00001-0000-0000-0000-000000000010', 'execujet-nas-1', 'polling', 1, 'preview_pending', 0, 'preview_pending', 1740528000, '2025-02-26T00:00:00+00:00', 1961366400, '2032-02-26T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000011', 'dev00001-0000-0000-0000-000000000011', 'execujet-bulk-2', 'bulk', 1, 'uploaded', 0, 'uploaded', 1740614400, '2025-02-27T00:00:00+00:00', 1961452800, '2032-02-27T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000012', 'dev00001-0000-0000-0000-000000000012', 'execujet-email-1', 'email', 1, 'uploaded', 0, 'uploaded', 1740700800, '2025-02-28T00:00:00+00:00', 1961539200, '2032-02-28T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000013', 'dev00001-0000-0000-0000-000000000013', 'execujet-nas-2', 'watcher', 1, 'uploaded', 0, 'uploaded', 1740787200, '2025-03-01T00:00:00+00:00', 1961625600, '2032-03-01T00:00:00+00:00', datetime('now'), datetime('now')),
    ('HBB-dev00001-0000-0000-0000-000000000014', 'dev00001-0000-0000-0000-000000000014', 'execujet-bulk-1', 'bulk', 1, 'error', 0, 'error', 1740873600, '2025-03-02T00:00:00+00:00', 1961712000, '2032-03-02T00:00:00+00:00', datetime('now'), datetime('now'));


-- ============================================================================
-- 3. file_entries (canonical v1.4.0 — file_display_id PK)
-- ============================================================================

-- Blob 01: Finalized — batch upload (bulk-1) [BATCH 1 of 3]
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, content_type, file_size_bytes, file_hash,
    status, pending_sync, processing_stage,
    uploaded_at_unix, uploaded_at_iso,
    processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    metadata_path, has_processed_outputs,
    created_at, updated_at
) VALUES (
    'HB-dev00001-0000-0000-0000-000000000001', 'dev00001-0000-0000-0000-000000000001',
    '/files_blob/dev00001-0000-0000-0000-000000000001-WN42752.pdf', 'WN42752.pdf',
    'HBB-batch0001-0000-0000-0000-000000000001',
    'execujet-bulk-1', 'bulk', 'application/pdf', 204800,
    'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f60001',
    'finalized', 0, NULL,
    1739750400, '2025-02-17T00:00:00+00:00',
    1739750460, '2025-02-17T00:01:00+00:00',
    1739750520, '2025-02-17T00:02:00+00:00',
    1960588800, '2032-02-17T00:00:00+00:00',
    '/files_blob/dev00001-0000-0000-0000-000000000001-WN42752.pdf.metadata.json', 1,
    datetime('now'), datetime('now')
);

-- Blob 02: Finalized — batch upload (bulk-1) [BATCH 2 of 3]
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, content_type, file_size_bytes, file_hash,
    status, pending_sync,
    uploaded_at_unix, uploaded_at_iso, processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    metadata_path, has_processed_outputs, created_at, updated_at
) VALUES (
    'HB-dev00001-0000-0000-0000-000000000002', 'dev00001-0000-0000-0000-000000000002',
    '/files_blob/dev00001-0000-0000-0000-000000000002-INV-2025-0451.pdf', 'INV-2025-0451.pdf',
    'HBB-batch0001-0000-0000-0000-000000000001',
    'execujet-bulk-1', 'bulk', 'application/pdf', 156000,
    'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a10002',
    'finalized', 0,
    1739750400, '2025-02-17T00:00:00+00:00', 1739750460, '2025-02-17T00:01:00+00:00',
    1739750520, '2025-02-17T00:02:00+00:00',
    1960588800, '2032-02-17T00:00:00+00:00',
    '/files_blob/dev00001-0000-0000-0000-000000000002-INV-2025-0451.pdf.metadata.json', 1,
    datetime('now'), datetime('now')
);

-- Blob 05: Finalized — batch upload (bulk-1) [BATCH 3 of 3]
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, content_type, file_size_bytes, file_hash,
    status, pending_sync,
    uploaded_at_unix, uploaded_at_iso, processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    metadata_path, has_processed_outputs, created_at, updated_at
) VALUES (
    'HB-dev00001-0000-0000-0000-000000000005', 'dev00001-0000-0000-0000-000000000005',
    '/files_blob/dev00001-0000-0000-0000-000000000005-CN-2025-0088.pdf', 'CN-2025-0088.pdf',
    'HBB-batch0001-0000-0000-0000-000000000001',
    'execujet-bulk-1', 'bulk', 'application/pdf', 98000,
    'e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d40005',
    'finalized', 0,
    1739750400, '2025-02-17T00:00:00+00:00', 1739750460, '2025-02-17T00:01:00+00:00',
    1739750520, '2025-02-17T00:02:00+00:00',
    1960588800, '2032-02-17T00:00:00+00:00',
    '/files_blob/dev00001-0000-0000-0000-000000000005-CN-2025-0088.pdf.metadata.json', 1,
    datetime('now'), datetime('now')
);

-- Blob 03: Finalized — NAS watcher (nas-1)
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, content_type, file_size_bytes, file_hash,
    status, pending_sync,
    uploaded_at_unix, uploaded_at_iso, processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    metadata_path, has_processed_outputs, created_at, updated_at
) VALUES (
    'HB-dev00001-0000-0000-0000-000000000003', 'dev00001-0000-0000-0000-000000000003',
    '/files_blob/dev00001-0000-0000-0000-000000000003-FIRS-BIS-2025-0001.xml', 'FIRS-BIS-2025-0001.xml',
    'HBB-dev00001-0000-0000-0000-000000000003',
    'execujet-nas-1', 'nas', 'application/xml', 48000,
    'c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b20003',
    'finalized', 0,
    1739923200, '2025-02-19T00:00:00+00:00', 1739923260, '2025-02-19T00:01:00+00:00',
    1739923320, '2025-02-19T00:02:00+00:00',
    1960761600, '2032-02-19T00:00:00+00:00',
    '/files_blob/dev00001-0000-0000-0000-000000000003-FIRS-BIS-2025-0001.xml.metadata.json', 1,
    datetime('now'), datetime('now')
);

-- Blob 04: Finalized — ERP API (erp-1)
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, content_type, file_size_bytes, file_hash,
    status, pending_sync,
    uploaded_at_unix, uploaded_at_iso, processed_at_unix, processed_at_iso,
    finalized_at_unix, finalized_at_iso,
    retention_until_unix, retention_until_iso,
    metadata_path, has_processed_outputs, created_at, updated_at
) VALUES (
    'HB-dev00001-0000-0000-0000-000000000004', 'dev00001-0000-0000-0000-000000000004',
    '/files_blob/dev00001-0000-0000-0000-000000000004-SAP-Export-2025Q1.json', 'SAP-Export-2025Q1.json',
    'HBB-dev00001-0000-0000-0000-000000000004',
    'execujet-erp-1', 'erp', 'application/json', 320000,
    'd4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c30004',
    'finalized', 0,
    1740009600, '2025-02-20T00:00:00+00:00', 1740009660, '2025-02-20T00:01:00+00:00',
    1740009720, '2025-02-20T00:02:00+00:00',
    1960848000, '2032-02-20T00:00:00+00:00',
    '/files_blob/dev00001-0000-0000-0000-000000000004-SAP-Export-2025Q1.json.metadata.json', 1,
    datetime('now'), datetime('now')
);

-- Blob 06: Processing — NAS watcher (nas-1)
INSERT OR IGNORE INTO file_entries (
    file_display_id, blob_uuid, blob_path, original_filename, batch_display_id,
    source, source_type, content_type, file_size_bytes, file_hash,
    status, pending_sync, processing_stage,
    uploaded_at_unix, uploaded_at_iso,
    retention_until_unix, retention_until_iso,
    metadata_path, has_processed_outputs, created_at, updated_at
) VALUES (
    'HB-dev00001-0000-0000-0000-000000000006', 'dev00001-0000-0000-0000-000000000006',
    '/files_blob/dev00001-0000-0000-0000-000000000006-Dangote-INV-9812.pdf', 'Dangote-INV-9812.pdf',
    'HBB-dev00001-0000-0000-0000-000000000006',
    'execujet-nas-1', 'nas', 'application/pdf', 175000,
    'f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e50006',
    'processing', 0, 'extracting',
    1740182400, '2025-02-22T00:00:00+00:00',
    1961020800, '2032-02-22T00:00:00+00:00',
    '/files_blob/dev00001-0000-0000-0000-000000000006-Dangote-INV-9812.pdf.metadata.json', 0,
    datetime('now'), datetime('now')
);

-- Blob 07-14: Abbreviated for brevity — same pattern
INSERT OR IGNORE INTO file_entries (file_display_id, blob_uuid, blob_path, original_filename, batch_display_id, source, source_type, content_type, file_size_bytes, file_hash, status, pending_sync, processing_stage, uploaded_at_unix, uploaded_at_iso, retention_until_unix, retention_until_iso, metadata_path, has_processed_outputs, created_at, updated_at) VALUES
    ('HB-dev00001-0000-0000-0000-000000000007', 'dev00001-0000-0000-0000-000000000007', '/files_blob/dev00001-0000-0000-0000-000000000007-ERP-Batch-2025-02.xml', 'ERP-Batch-2025-02.xml', 'HBB-dev00001-0000-0000-0000-000000000007', 'execujet-erp-1', 'erp', 'application/xml', 512000, 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f60007', 'processing', 0, 'validating', 1740268800, '2025-02-23T00:00:00+00:00', 1961107200, '2032-02-23T00:00:00+00:00', NULL, 0, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000008', 'dev00001-0000-0000-0000-000000000008', '/files_blob/dev00001-0000-0000-0000-000000000008-PikWik-Sales-Dec.pdf', 'PikWik-Sales-Dec.pdf', 'HBB-dev00001-0000-0000-0000-000000000008', 'execujet-bulk-2', 'bulk', 'application/pdf', 890000, 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a10008', 'processing', 0, 'enriching', 1740355200, '2025-02-24T00:00:00+00:00', 1961193600, '2032-02-24T00:00:00+00:00', NULL, 0, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000009', 'dev00001-0000-0000-0000-000000000009', '/files_blob/dev00001-0000-0000-0000-000000000009-MTN-Airtime-Batch.pdf', 'MTN-Airtime-Batch.pdf', 'HBB-dev00001-0000-0000-0000-000000000009', 'execujet-bulk-1', 'bulk', 'application/pdf', 410000, 'c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b20009', 'preview_pending', 0, NULL, 1740441600, '2025-02-25T00:00:00+00:00', 1961280000, '2032-02-25T00:00:00+00:00', NULL, 1, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000010', 'dev00001-0000-0000-0000-000000000010', '/files_blob/dev00001-0000-0000-0000-000000000010-Customer-Report-FY24.xlsx', 'Customer-Report-FY24.xlsx', 'HBB-dev00001-0000-0000-0000-000000000010', 'execujet-nas-1', 'nas', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 1250000, 'd4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c30010', 'preview_pending', 0, NULL, 1740528000, '2025-02-26T00:00:00+00:00', 1961366400, '2032-02-26T00:00:00+00:00', NULL, 1, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000011', 'dev00001-0000-0000-0000-000000000011', '/files_blob/dev00001-0000-0000-0000-000000000011-Nestle-PO-33847.pdf', 'Nestle-PO-33847.pdf', 'HBB-dev00001-0000-0000-0000-000000000011', 'execujet-bulk-2', 'bulk', 'application/pdf', 267000, 'e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d40011', 'uploaded', 0, NULL, 1740614400, '2025-02-27T00:00:00+00:00', 1961452800, '2032-02-27T00:00:00+00:00', NULL, 0, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000012', 'dev00001-0000-0000-0000-000000000012', '/files_blob/dev00001-0000-0000-0000-000000000012-Vendor-Rebate-Q4.pdf', 'Vendor-Rebate-Q4.pdf', 'HBB-dev00001-0000-0000-0000-000000000012', 'execujet-email-1', 'email', 'application/pdf', 145000, 'f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e50012', 'uploaded', 0, NULL, 1740700800, '2025-02-28T00:00:00+00:00', 1961539200, '2032-02-28T00:00:00+00:00', NULL, 0, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000013', 'dev00001-0000-0000-0000-000000000013', '/files_blob/dev00001-0000-0000-0000-000000000013-February-Invoices.zip', 'February-Invoices.zip', 'HBB-dev00001-0000-0000-0000-000000000013', 'execujet-nas-2', 'nas', 'application/zip', 5400000, 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f60013', 'uploaded', 0, NULL, 1740787200, '2025-03-01T00:00:00+00:00', 1961625600, '2032-03-01T00:00:00+00:00', NULL, 0, datetime('now'), datetime('now')),
    ('HB-dev00001-0000-0000-0000-000000000014', 'dev00001-0000-0000-0000-000000000014', '/files_blob/dev00001-0000-0000-0000-000000000014-Corrupted-Scan.pdf', 'Corrupted-Scan.pdf', 'HBB-dev00001-0000-0000-0000-000000000014', 'execujet-bulk-1', 'bulk', 'application/pdf', 12000, 'b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a10014', 'error', 0, 'extracting', 1740873600, '2025-03-02T00:00:00+00:00', 1961712000, '2032-03-02T00:00:00+00:00', NULL, 0, datetime('now'), datetime('now'));


-- ============================================================================
-- 4. blob_batch_entries (canonical — display_id FKs)
-- ============================================================================
INSERT OR IGNORE INTO blob_batch_entries (batch_display_id, file_display_id, sequence_number, created_at) VALUES
    ('HBB-batch0001-0000-0000-0000-000000000001', 'HB-dev00001-0000-0000-0000-000000000001', 1, datetime('now')),
    ('HBB-batch0001-0000-0000-0000-000000000001', 'HB-dev00001-0000-0000-0000-000000000002', 2, datetime('now')),
    ('HBB-batch0001-0000-0000-0000-000000000001', 'HB-dev00001-0000-0000-0000-000000000005', 3, datetime('now'));


-- ============================================================================
-- 5. blob_deduplication
-- ============================================================================
INSERT OR IGNORE INTO blob_deduplication (
    file_hash, source_system, original_blob_uuid, original_filename,
    first_seen_at_unix, first_seen_iso, created_at, updated_at
) VALUES
    ('a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f60001', 'execujet-bulk-1', 'dev00001-0000-0000-0000-000000000001', 'WN42752.pdf', 1739750400, '2025-02-17T00:00:00+00:00', datetime('now'), datetime('now')),
    ('b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a10002', 'execujet-bulk-1', 'dev00001-0000-0000-0000-000000000002', 'INV-2025-0451.pdf', 1739750400, '2025-02-17T00:00:00+00:00', datetime('now'), datetime('now')),
    ('c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b20003', 'execujet-nas-1', 'dev00001-0000-0000-0000-000000000003', 'FIRS-BIS-2025-0001.xml', 1739923200, '2025-02-19T00:00:00+00:00', datetime('now'), datetime('now')),
    ('d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c30004', 'execujet-erp-1', 'dev00001-0000-0000-0000-000000000004', 'SAP-Export-2025Q1.json', 1740009600, '2025-02-20T00:00:00+00:00', datetime('now'), datetime('now'));


-- ============================================================================
-- 6. daily_usage
-- ============================================================================
INSERT OR IGNORE INTO daily_usage (company_id, usage_date, file_count, total_size_bytes, daily_limit, created_at, updated_at) VALUES
    ('execujet-bulk-1', '2025-02-17', 3, 459800, 1000, datetime('now'), datetime('now')),
    ('execujet-bulk-1', '2025-02-25', 1, 410000, 1000, datetime('now'), datetime('now')),
    ('execujet-nas-1', '2025-02-19', 1, 48000, 1000, datetime('now'), datetime('now')),
    ('execujet-erp-1', '2025-02-20', 1, 320000, 1000, datetime('now'), datetime('now')),
    ('execujet-bulk-2', '2025-02-24', 1, 890000, 1000, datetime('now'), datetime('now'));


-- ============================================================================
-- 7. audit_events
-- ============================================================================
INSERT OR IGNORE INTO audit_events (service, event_type, user_id, details, trace_id, created_at, created_at_unix) VALUES
    ('relay-bulk-1', 'batch.uploaded', NULL, '{"batch_display_id":"HBB-batch0001-0000-0000-0000-000000000001","file_count":3,"total_bytes":459800}', 'trace-dev-batch', datetime('now'), 1739750400),
    ('relay-bulk-1', 'file.ingested', NULL, '{"blob_uuid":"dev00001-0000-0000-0000-000000000001","filename":"WN42752.pdf","size":204800}', 'trace-dev-001', datetime('now'), 1739750400),
    ('core', 'file.processed', NULL, '{"blob_uuid":"dev00001-0000-0000-0000-000000000001","duration_ms":60000,"output_type":"firs_invoices"}', 'trace-dev-001', datetime('now'), 1739750460),
    ('core', 'file.error', NULL, '{"blob_uuid":"dev00001-0000-0000-0000-000000000014","error":"PDF extraction failed","stage":"extracting"}', 'trace-dev-014', datetime('now'), 1740873600),
    ('heartbeat', 'system.startup', NULL, '{"mode":"primary","version":"2.0.0"}', NULL, datetime('now'), 1739750000);


-- ============================================================================
-- 8. metrics_events
-- ============================================================================
INSERT OR IGNORE INTO metrics_events (metric_type, metric_values, reported_by, created_at, created_at_unix) VALUES
    ('ingestion', '{"files_today":5,"bytes_today":1048576,"avg_file_size":209715}', 'relay-bulk-1', datetime('now'), 1739750400),
    ('performance', '{"avg_extraction_ms":45000,"avg_validation_ms":8000,"avg_enrichment_ms":12000}', 'core', datetime('now'), 1739836800),
    ('error', '{"extraction_failures":1,"validation_failures":0,"total_processed":14}', 'core', datetime('now'), 1740873600);


COMMIT;

-- ============================================================================
-- SEED DATA COMPLETE (Canonical v1.4.0)
-- 6 relay services, 14 file_entries, 13 blob_batches, 3 batch junction rows
-- 4 dedup records, 5 daily usage, 5 audit events, 3 metrics events
-- ============================================================================
