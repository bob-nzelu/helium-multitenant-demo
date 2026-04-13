-- Inventory Schema (PostgreSQL) — translated from canonical SQLite v1.0.0
SET search_path TO inventory;


-- ============================================================================
-- TABLE 1: inventory (PRIMARY -- 35 fields)
-- ============================================================================
-- Primary entity. One row per product or service.
-- Core-owned. SDK mirrors via SSE events.
-- Editable -- edit tracking via updated_by + inventory_edit_history.
--
-- Single identity:
--   product_id -- UUIDv7, Core-generated. PK. No display_id/uuid split.
--   SDK adds local `id INTEGER AUTOINCREMENT` as convenience column.
--
-- Type determination:
--   type = 'GOODS' -> hsn_code and product_category are relevant
--   type = 'SERVICE' -> service_code and service_category are relevant
--
-- Status: No status enum. Soft delete via deleted_at.
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory (
    -- A. Primary Identification (1 field)
    product_id                  TEXT PRIMARY KEY NOT NULL,   -- UUIDv7, Core-generated

    -- B. Identifiers (3 fields)
    helium_sku                  TEXT UNIQUE,                 -- Helium-generated: HLM-{TENANT}-{SEQ}. Platform-owned, stable
    customer_sku                TEXT,                        -- Customer/tenant's own product reference code (Tenant_SKU_ref)
    oem_sku                     TEXT,                        -- Original Equipment Manufacturer reference

    -- C. Product Info (4 fields)
    product_name                TEXT NOT NULL,               -- Canonical product or service name
    product_name_normalized     TEXT,                        -- Uppercased/stripped for fuzzy matching + dedup. Core-computed
    description                 TEXT,                        -- Extended description (max 500 chars for FIRS compatibility)
    unit_of_measure             TEXT,                        -- e.g., 'KG', 'LITRE', 'UNIT', 'HOUR', 'PACK'

    -- D. Classification Codes (4 fields)
    hsn_code                    TEXT,                        -- HS code in XXXX.XX format (goods). FIRS-mandatory for goods line items
    service_code                TEXT,                        -- Service classification code. FIRS-mandatory for service line items
    product_category            TEXT,                        -- Goods category (e.g., 'Food & Beverages', 'Building Materials')
    service_category            TEXT,                        -- Service category (e.g., 'Consulting', 'Logistics', 'Maintenance')

    -- E. Classification (1 field)
    type                        TEXT NOT NULL DEFAULT 'GOODS' -- Product type: GOODS or SERVICE
        CHECK(type IN ('GOODS', 'SERVICE')),

    -- F. Tax / VAT (3 fields)
    vat_treatment               TEXT DEFAULT 'STANDARD'      -- VAT treatment for this product
        CHECK(vat_treatment IS NULL OR vat_treatment IN (
            'STANDARD', 'ZERO_RATED', 'EXEMPT'
        )),
    vat_rate                    DOUBLE PRECISION DEFAULT 7.5,  -- VAT percentage (7.5% is Nigeria standard rate)
    is_tax_exempt               INTEGER DEFAULT 0,           -- Product-level tax exemption flag (0=no, 1=yes)

    -- G. Pricing (1 field)
    currency                    TEXT DEFAULT 'NGN',           -- Price currency (ISO 4217). Default Nigerian Naira

    -- H. PDP Classification Intelligence (6 fields -- JSON arrays store ranked alternatives)
    hs_codes                    JSONB,                       -- [{code, description, rank, confidence}, ...]. Selected top pick + alternatives
    service_codes               JSONB,                       -- [{code, description, rank, confidence}, ...]. For SERVICE type products
    product_categories          JSONB,                       -- [{category, rank}, ...]. PDP-assigned category rankings
    service_categories          JSONB,                       -- [{category, rank}, ...]. PDP-assigned service category rankings
    classification_confidence   DOUBLE PRECISION DEFAULT 0,  -- Overall PDP classification confidence (0.0 to 1.0)
    classification_source       TEXT,                        -- Who classified: 'PDP', 'HIS_LEARNING', 'MANUAL'

    -- I. Classification Metadata (2 fields)
    last_classified_at          TIMESTAMPTZ,                 -- Timestamp of last PDP/HIS classification run
    last_classified_by          TEXT,                        -- PDP pipeline version or user ID

    -- J. Aggregates -- minimal (5 fields, Core-updated on invoice events)
    total_times_invoiced        INTEGER DEFAULT 0,           -- How many invoice line items reference this product
    last_invoice_date           TEXT,                        -- ISO date of most recent invoice containing this product
    total_revenue               NUMERIC(18,4) DEFAULT 0,    -- Total revenue from this product across all invoices (NGN)
    avg_unit_price              NUMERIC(18,4) DEFAULT 0,    -- Average unit price across invoices (NGN). Core-computed
    top_customer                TEXT,                        -- Company name of highest-volume customer (Core-computed)

    -- L. Audit (5 fields)
    created_by                  TEXT,                        -- helium_user_id of creator
    updated_by                  TEXT,                        -- helium_user_id of last editor
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ                  -- Soft delete timestamp (NULL = active)
);


-- ============================================================================
-- TABLE 2: inventory_name_variants (DEDUP -- 9 fields)
-- ============================================================================
-- Tracks alternative names/descriptions for deduplication.
-- e.g., 'Indomie Instant Noodles' vs 'Indomie Noodles 70g' vs 'Indomie'
-- When the same product appears under different names across invoices or
-- imports, variants are recorded to prevent duplicate product creation.
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory_name_variants (
    id                          BIGSERIAL PRIMARY KEY,
    product_id                  TEXT NOT NULL,               -- FK -> inventory.product_id
    name_variant                TEXT NOT NULL,               -- Alternative name/description
    name_variant_normalized     TEXT,                        -- Lowercased/trimmed for matching
    variant_weight              INTEGER DEFAULT 1,           -- Relevance/confidence (higher = more relevant)
    source                      TEXT,                        -- Where found: 'invoice', 'import', 'manual', 'pdp'
    first_seen_at               TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at                TIMESTAMPTZ DEFAULT NOW(),
    occurrence_count            INTEGER DEFAULT 1,           -- How many times seen

    FOREIGN KEY (product_id) REFERENCES inventory(product_id) ON DELETE CASCADE,
    UNIQUE(product_id, name_variant_normalized)
);


-- ============================================================================
-- TABLE 3: inventory_transactions (AUDIT -- 12 fields)
-- ============================================================================
-- Stock movement audit log. Records quantity changes with before/after
-- snapshots. Retained for future stock management (v2) and current
-- audit trail when products are referenced in invoices.
--
-- transaction_type values: 'INVOICE_OUT' (sold), 'INVOICE_IN' (purchased),
-- 'ADJUSTMENT', 'RETURN', 'WRITE_OFF', 'INITIAL_STOCK'
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id                          BIGSERIAL PRIMARY KEY,
    product_id                  TEXT NOT NULL,               -- FK -> inventory.product_id
    transaction_type            TEXT NOT NULL,               -- Type of movement (see header)
    quantity                    DOUBLE PRECISION NOT NULL,   -- Quantity moved (positive = in, negative = out)
    quantity_before             DOUBLE PRECISION,            -- Stock level before transaction (nullable -- deferred stock)
    quantity_after              DOUBLE PRECISION,            -- Stock level after transaction (nullable -- deferred stock)
    reference_type              TEXT,                        -- Source document type: 'invoice', 'manual', 'bulk_import'
    reference_id                TEXT,                        -- Source document ID (e.g., invoice_id)
    transaction_date            TEXT NOT NULL,               -- ISO date of the transaction
    notes                       TEXT,                        -- Optional description
    created_by                  TEXT,                        -- helium_user_id
    created_at                  TIMESTAMPTZ DEFAULT NOW(),

    FOREIGN KEY (product_id) REFERENCES inventory(product_id) ON DELETE CASCADE
);


-- ============================================================================
-- TABLE 4: inventory_edit_history (AUDIT -- 8 fields)
-- ============================================================================
-- Full per-field edit audit trail. One row per field change.
-- If a user edits 3 fields in one action, that's 3 rows.
-- Provides complete before/after visibility for compliance.
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory_edit_history (
    id                          BIGSERIAL PRIMARY KEY,
    product_id                  TEXT NOT NULL,               -- FK -> inventory.product_id
    field_name                  TEXT NOT NULL,               -- Which field was changed (e.g., 'hsn_code', 'vat_treatment')
    old_value                   TEXT,                        -- Previous value (as text). NULL for initial creation
    new_value                   TEXT,                        -- New value (as text). NULL for field deletion/clearing
    changed_by                  TEXT NOT NULL,               -- helium_user_id of the editor
    changed_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_reason               TEXT,                        -- Optional note (e.g., 'HS code correction per customs audit')

    FOREIGN KEY (product_id) REFERENCES inventory(product_id) ON DELETE CASCADE
);


-- ============================================================================
-- TABLE 5: inventory_classification_candidates (PDP STAGING -- 13 fields)
-- ============================================================================
-- PDP classification staging table. When PDP classifies a product, it may
-- produce multiple candidate HS codes or service codes with varying
-- confidence scores. These are stored here for user selection.
--
-- The user picks the best match (is_selected = 1), and the chosen code
-- is written to the inventory record's primary hsn_code/service_code field
-- AND the JSON arrays (hs_codes/service_codes) are updated.
--
-- Non-selected candidates remain for audit and potential re-selection.
--
-- Complements (not replaces) the JSON arrays on inventory:
--   JSON arrays = selected top picks (fast read for UI/FIRS)
--   This table = full candidate history (audit + re-selection)
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory_classification_candidates (
    id                          BIGSERIAL PRIMARY KEY,
    product_id                  TEXT NOT NULL,               -- FK -> inventory.product_id
    candidate_type              TEXT NOT NULL                -- What kind of classification candidate
        CHECK(candidate_type IN ('GOODS_HS', 'SERVICE_CODE')),
    raw_description             TEXT NOT NULL,               -- The product description PDP was classifying
    resolved_code               TEXT,                        -- PDP-resolved code (e.g., '1905.90' for HS, 'SVC-001' for service)
    resolved_description        TEXT,                        -- Human-readable code description (e.g., 'Bread, pastry, cakes')
    rank                        INTEGER DEFAULT 1,           -- PDP rank (1 = best match, 2 = second, etc.)
    confidence                  DOUBLE PRECISION DEFAULT 0,  -- PDP confidence score (0.0 to 1.0)
    is_selected                 INTEGER DEFAULT 0,           -- 1 = user's chosen candidate
    resolved_at                 TIMESTAMPTZ,                 -- When PDP processed this candidate
    resolved_by                 TEXT,                        -- PDP pipeline version/identifier
    model_used                  TEXT,                        -- LLM model used for classification (e.g., 'gemini-2.5-flash')
    created_at                  TIMESTAMPTZ DEFAULT NOW(),

    FOREIGN KEY (product_id) REFERENCES inventory(product_id) ON DELETE CASCADE
);


-- ============================================================================
-- SCHEMA VERSION TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory_schema_version (
    version         TEXT PRIMARY KEY NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description     TEXT
);


-- ============================================================================
-- TRIGGERS: updated_at auto-update
-- ============================================================================

CREATE OR REPLACE FUNCTION inventory.fn_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_inventory_updated_at ON inventory;
CREATE TRIGGER trg_inventory_updated_at
    BEFORE UPDATE ON inventory
    FOR EACH ROW EXECUTE FUNCTION inventory.fn_updated_at();

DROP TRIGGER IF EXISTS trg_inventory_name_variants_updated_at ON inventory_name_variants;
CREATE TRIGGER trg_inventory_name_variants_updated_at
    BEFORE UPDATE ON inventory_name_variants
    FOR EACH ROW EXECUTE FUNCTION inventory.fn_updated_at();


-- ============================================================================
-- INDEXES
-- ============================================================================

-- inventory
CREATE INDEX IF NOT EXISTS idx_inventory_product_name
    ON inventory(product_name);
CREATE INDEX IF NOT EXISTS idx_inventory_name_normalized
    ON inventory(product_name_normalized)
    WHERE product_name_normalized IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_helium_sku
    ON inventory(helium_sku)
    WHERE helium_sku IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_hsn_code
    ON inventory(hsn_code)
    WHERE hsn_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_service_code
    ON inventory(service_code)
    WHERE service_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_type
    ON inventory(type);
CREATE INDEX IF NOT EXISTS idx_inventory_vat_treatment
    ON inventory(vat_treatment)
    WHERE vat_treatment IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_is_tax_exempt
    ON inventory(is_tax_exempt)
    WHERE is_tax_exempt = 1;
CREATE INDEX IF NOT EXISTS idx_inventory_classification_source
    ON inventory(classification_source)
    WHERE classification_source IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inventory_deleted_at
    ON inventory(deleted_at)
    WHERE deleted_at IS NOT NULL;

-- inventory_name_variants
CREATE INDEX IF NOT EXISTS idx_inv_variants_product_id
    ON inventory_name_variants(product_id);
CREATE INDEX IF NOT EXISTS idx_inv_variants_normalized
    ON inventory_name_variants(name_variant_normalized)
    WHERE name_variant_normalized IS NOT NULL;

-- inventory_transactions
CREATE INDEX IF NOT EXISTS idx_inv_transactions_product_id
    ON inventory_transactions(product_id);
CREATE INDEX IF NOT EXISTS idx_inv_transactions_date
    ON inventory_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_inv_transactions_type
    ON inventory_transactions(transaction_type);

-- inventory_edit_history
CREATE INDEX IF NOT EXISTS idx_inv_edit_history_product_id
    ON inventory_edit_history(product_id);
CREATE INDEX IF NOT EXISTS idx_inv_edit_history_changed_at
    ON inventory_edit_history(changed_at);
CREATE INDEX IF NOT EXISTS idx_inv_edit_history_changed_by
    ON inventory_edit_history(changed_by);

-- inventory_classification_candidates
CREATE INDEX IF NOT EXISTS idx_inv_class_candidates_product_id
    ON inventory_classification_candidates(product_id);
CREATE INDEX IF NOT EXISTS idx_inv_class_candidates_type
    ON inventory_classification_candidates(candidate_type);
CREATE INDEX IF NOT EXISTS idx_inv_class_candidates_selected
    ON inventory_classification_candidates(product_id, is_selected)
    WHERE is_selected = 1;


-- ============================================================================
-- VIEW: vw_inventory_display
-- ============================================================================
-- UI-priority columns for the Inventory tab in Float App.
-- Maps to PRODUCT_COLUMNS in column_config.py (Area A + B fields).
-- ============================================================================

CREATE OR REPLACE VIEW vw_inventory_display AS
SELECT
    product_id,
    product_name,
    hsn_code,
    vat_treatment,
    product_category,
    service_category,
    avg_unit_price,
    currency,
    vat_rate,
    type,
    helium_sku,
    description,
    created_at
FROM inventory
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_inventory_firs
-- ============================================================================
-- FIRS-mandatory fields. HS code (goods) and service code (services) for
-- FIRS invoice line item construction. Also includes tax treatment fields.
-- ============================================================================

CREATE OR REPLACE VIEW vw_inventory_firs AS
SELECT
    product_id,
    product_name,
    type,
    hsn_code,
    service_code,
    product_category,
    service_category,
    vat_treatment,
    vat_rate,
    is_tax_exempt,
    description,
    unit_of_measure,
    avg_unit_price,
    currency
FROM inventory
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_inventory_metrics
-- ============================================================================
-- Aggregate performance metrics for the Statistics mApp.
-- Minimal aggregates + classification metadata.
-- ============================================================================

CREATE OR REPLACE VIEW vw_inventory_metrics AS
SELECT
    product_id,
    product_name,
    type,
    -- Aggregates
    total_times_invoiced,
    last_invoice_date,
    total_revenue,
    avg_unit_price,
    -- Classification
    classification_confidence,
    classification_source,
    last_classified_at,
    -- Tax
    vat_treatment,
    is_tax_exempt,
    -- Context
    created_at
FROM inventory
WHERE deleted_at IS NULL;


-- ============================================================================
-- CATEGORY VIEWS (permission-ready field grouping)
-- ============================================================================


-- inventory: operational

CREATE OR REPLACE VIEW vw_inventory_operational AS
SELECT
    product_id,
    -- Product info
    product_name,
    description,
    unit_of_measure,
    -- Identifiers
    helium_sku,
    customer_sku,
    oem_sku,
    -- Classification codes
    hsn_code,
    service_code,
    product_category,
    service_category,
    -- Classification
    type,
    -- Tax
    vat_treatment,
    vat_rate,
    is_tax_exempt,
    -- Pricing
    avg_unit_price,
    currency
FROM inventory
WHERE deleted_at IS NULL;


-- inventory: identity

CREATE OR REPLACE VIEW vw_inventory_identity AS
SELECT
    product_id,
    product_name,
    -- Audit trail
    created_by,
    updated_by,
    created_at,
    updated_at,
    deleted_at
FROM inventory;


-- inventory: analytics

CREATE OR REPLACE VIEW vw_inventory_analytics AS
SELECT
    product_id,
    product_name,
    type,
    vat_treatment,
    -- Aggregates
    total_times_invoiced,
    last_invoice_date,
    total_revenue,
    avg_unit_price,
    -- Classification intelligence
    classification_confidence,
    classification_source,
    last_classified_at,
    -- Tax
    is_tax_exempt,
    -- Context
    created_at
FROM inventory
WHERE deleted_at IS NULL;


-- ============================================================================
-- SEED DATA
-- ============================================================================

INSERT INTO inventory_schema_version (version, description)
VALUES ('1.0.0', 'Initial PostgreSQL translation from canonical SQLite v1')
ON CONFLICT DO NOTHING;
