-- Customers Schema (PostgreSQL) — translated from canonical SQLite v1.2.0
SET search_path TO customers;


-- ============================================================================
-- SHARED TRIGGER FUNCTION: updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION customers.fn_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- TABLE 1: customers (PRIMARY — 54 fields)
-- ============================================================================
-- Primary entity. One row per customer (business entity).
-- Core-owned. SDK mirrors via SSE events.
-- Editable — edit tracking via updated_by + customer_edit_history.
--
-- Single identity:
--   customer_id — UUIDv7, Core-generated. PK. No display_id/uuid split.
--   SDK adds local `id INTEGER AUTOINCREMENT` as convenience column.
--
-- Status: No status enum. Soft delete via deleted_at.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customers (
    -- A. Primary Identification (1 field)
    customer_id                 TEXT PRIMARY KEY NOT NULL,   -- UUIDv7, Core-generated

    -- B. Identifiers (4 fields)
    tin                         TEXT UNIQUE,                 -- Tax Identification Number (legacy ########-####). Nullable for foreign/informal entities
    rc_number                   TEXT UNIQUE,                 -- Company Registration Number (13-digit CAC). Nullable
    tax_id                      TEXT,                        -- New 13-digit FIRS Tax ID (from NRS/JRB portal). FIRS `tin` field accepts all 3 formats interchangeably
    primary_identifier          TEXT NOT NULL                -- Which identifier is canonical for this customer
        CHECK(primary_identifier IN ('TIN', 'RC_NUMBER')),

    -- C. Company Info (9 fields)
    company_name                TEXT NOT NULL,               -- Registered company name
    company_name_normalized     TEXT,                        -- Lowercased/trimmed for fuzzy matching + dedup
    trading_name                TEXT,                        -- Brand/trading name (may differ from registered name)
    short_code                  TEXT,                        -- Internal reference code (e.g., 'ARM' for Aramex)
    customer_code               TEXT UNIQUE,                 -- Structured reference (e.g., 'CUST-0001'). Core-generated sequential
    email                       TEXT,                        -- Primary contact email
    phone                       TEXT,                        -- Primary contact phone
    website                     TEXT,                        -- Company website
    business_description        TEXT,                        -- Business activity description (optional FIRS field, max 500 chars)

    -- D. Address (9 fields — single `address` field, 1:1 with invoice buyer_address/seller_address)
    address                     TEXT,                        -- Street address. Maps to FIRS postal_address.street_name (max 200 chars)
    city                        TEXT,                        -- City name. Maps to FIRS postal_address.city_name (max 100 chars)
    state                       TEXT,                        -- State name (e.g., 'Lagos', 'Kano')
    postal_code                 TEXT,                        -- Postal/ZIP code
    country                     TEXT DEFAULT 'NGA',          -- Country name (ISO 3166-1 alpha-3)
    country_code                TEXT DEFAULT 'NG',           -- Country code (ISO 3166-1 alpha-2). See HIS/Seeds/Country_Codes.txt
    lga                         TEXT,                        -- Local Government Area name (resolved by PDP)
    lga_code                    TEXT,                        -- LGA code (FIRS format, 1-774). See HIS/Seeds/Local_Government_Codes.txt
    state_code                  TEXT,                        -- State code (FIRS format, 1-37). See HIS/Seeds/State_Codes.txt

    -- E. Classification (4 fields)
    customer_type               TEXT                         -- Transaction type for this customer
        CHECK(customer_type IS NULL OR customer_type IN ('B2B', 'B2G')),
    tax_classification          TEXT                         -- Tax treatment
        CHECK(tax_classification IS NULL OR tax_classification IN ('STANDARD', 'EXEMPT')),
    industry                    TEXT,                        -- Industry/sector description
    is_fze                      INTEGER DEFAULT 0,           -- Free Zone Enterprise status (0=no, 1=yes). Affects invoice tax treatment

    -- F. Tax & Compliance (3 fields)
    is_mbs_registered           INTEGER DEFAULT 0,           -- Registered on FIRS Merchant Buyer Solution (0=no, 1=yes)
    compliance_score            INTEGER DEFAULT 0,           -- Computed compliance score (0-100). Core-updated.
    compliance_details          JSONB,                       -- JSON: component scores. See compliance_details spec below.
    -- compliance_details JSON structure:
    -- {
    --   "tin_valid": 0-20,           -- TIN format and JTB verification
    --   "address_complete": 0-20,    -- address fields populated and verified
    --   "mbs_registered": 0-20,      -- registered on FIRS MBS platform
    --   "invoice_activity": 0-20,    -- active invoice history (not dormant)
    --   "rejection_rate": 0-20       -- low rejection rate on invoices
    -- }
    -- Each component scored 0-20, summing to 0-100.

    -- G. Operational (2 fields — non-FIRS organizational/commercial)
    business_unit               TEXT,                        -- Organizational unit within tenant (for reporting: "Top erring branch" analytics)
    default_due_date_days       INTEGER,                     -- Default payment terms in days from issue date (for compliance scoring)

    -- H. Denormalized Aggregates (15 fields — Core-updated on invoice events)
    total_invoices              INTEGER DEFAULT 0,           -- Total invoices involving this customer
    average_invoice_size        NUMERIC(18,4) DEFAULT 0,     -- Mean invoice amount (NGN)
    total_transmitted           INTEGER DEFAULT 0,           -- Invoices successfully transmitted to FIRS
    total_accepted              INTEGER DEFAULT 0,           -- Invoices accepted by FIRS
    receivables_rejected        INTEGER DEFAULT 0,           -- Outbound invoices rejected
    payable_rejected            INTEGER DEFAULT 0,           -- Inbound invoices rejected
    total_pending               INTEGER DEFAULT 0,           -- Invoices currently pending
    last_invoice_date           TEXT,                        -- ISO date of most recent invoice
    last_purchased_date         TEXT,                        -- ISO date of most recent outbound invoice (we sold to them)
    last_inbound_date           TEXT,                        -- ISO date of most recent inbound invoice (they invoiced us)
    last_active_date            TEXT,                        -- ISO date of most recent activity of any kind
    payable_frequency           TEXT                         -- Computed: how often they invoice us
        CHECK(payable_frequency IS NULL OR payable_frequency IN (
            'daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'annually', 'irregular'
        )),
    receivables_frequency       TEXT                         -- Computed: how often we invoice them
        CHECK(receivables_frequency IS NULL OR receivables_frequency IN (
            'daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'annually', 'irregular'
        )),
    total_lifetime_value        NUMERIC(18,4) DEFAULT 0,     -- Total invoice value over lifetime (NGN)
    total_lifetime_tax          NUMERIC(18,4) DEFAULT 0,     -- Total tax value over lifetime (NGN)

    -- I. Lifecycle (2 fields — optional, for future multi-tenancy)
    company_id                  TEXT,                        -- Tenant identifier (nullable — single-tenant Helium)
    pending_sync                INTEGER DEFAULT 0,           -- 0=confirmed, 1=local-only (no optimistic writes for customers)

    -- K. Audit (5 fields)
    created_by                  TEXT,                        -- helium_user_id of creator
    updated_by                  TEXT,                        -- helium_user_id of last editor
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ                  -- Soft delete timestamp (NULL = active)
);

DROP TRIGGER IF EXISTS trg_customers_updated_at ON customers;
CREATE TRIGGER trg_customers_updated_at
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION customers.fn_updated_at();


-- ============================================================================
-- TABLE 2: customer_branches (CHILD — 18 fields)
-- ============================================================================
-- Branch offices for a customer. Each customer can have multiple branches.
-- One branch should be flagged is_hq = 1 (advisory, not enforced).
-- Invoice creation should reference a branch_id or default to HQ.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_branches (
    -- Identity
    branch_id                   TEXT PRIMARY KEY NOT NULL,   -- UUIDv7
    customer_id                 TEXT NOT NULL,               -- FK → customers.customer_id

    -- Branch info
    branch_name                 TEXT NOT NULL,               -- e.g., 'Lagos Head Office', 'Kano Branch'
    is_hq                       INTEGER DEFAULT 0,           -- 1 = headquarters (advisory flag, not enforced unique)

    -- Address (single address field, same pattern as customers)
    address                     TEXT,                        -- Street address. Maps to FIRS postal_address.street_name
    city                        TEXT,
    state                       TEXT,
    postal_code                 TEXT,
    country                     TEXT DEFAULT 'NGA',
    lga                         TEXT,
    lga_code                    TEXT,
    state_code                  TEXT,

    -- Contact (optional person at this branch)
    contact_name                TEXT,
    contact_phone               TEXT,
    contact_email               TEXT,

    -- Audit
    created_by                  TEXT,                        -- helium_user_id of creator
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);

DROP TRIGGER IF EXISTS trg_customer_branches_updated_at ON customer_branches;
CREATE TRIGGER trg_customer_branches_updated_at
    BEFORE UPDATE ON customer_branches
    FOR EACH ROW EXECUTE FUNCTION customers.fn_updated_at();


-- ============================================================================
-- TABLE 3: customer_name_variants (DEDUP — 9 fields)
-- ============================================================================
-- Tracks alternative names/spellings for deduplication.
-- e.g., 'Dangote Industries Ltd' vs 'Dangote Industries Limited' vs 'Dangote'
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_name_variants (
    id                          BIGSERIAL PRIMARY KEY,
    customer_id                 TEXT NOT NULL,               -- FK → customers.customer_id
    name_variant                TEXT NOT NULL,               -- Alternative name
    name_variant_normalized     TEXT,                        -- Lowercased/trimmed for matching
    variant_weight              INTEGER DEFAULT 1,           -- Relevance/confidence score
    source                      TEXT,                        -- Where this variant was found (e.g., 'invoice', 'import', 'manual')
    first_seen_at               TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at                TIMESTAMPTZ DEFAULT NOW(),
    occurrence_count            INTEGER DEFAULT 1,           -- How many times seen

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE,
    UNIQUE(customer_id, name_variant_normalized)
);


-- ============================================================================
-- TABLE 4: customer_contacts (PEOPLE — 9 fields)
-- ============================================================================
-- Contact persons at a customer. One customer can have multiple contacts.
-- is_primary = 1 marks the default contact (advisory, not enforced unique).
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_contacts (
    id                          BIGSERIAL PRIMARY KEY,
    customer_id                 TEXT NOT NULL,               -- FK → customers.customer_id
    contact_name                TEXT NOT NULL,               -- Full name
    contact_role                TEXT,                        -- e.g., 'Accounts Payable', 'Procurement Manager'
    email                       TEXT,
    phone                       TEXT,
    is_primary                  INTEGER DEFAULT 0,           -- 1 = default contact for this customer
    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW(),

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);

DROP TRIGGER IF EXISTS trg_customer_contacts_updated_at ON customer_contacts;
CREATE TRIGGER trg_customer_contacts_updated_at
    BEFORE UPDATE ON customer_contacts
    FOR EACH ROW EXECUTE FUNCTION customers.fn_updated_at();


-- ============================================================================
-- TABLE 5: customer_edit_history (AUDIT — 8 fields)
-- ============================================================================
-- Full per-field edit audit trail. One row per field change.
-- If a user edits 3 fields in one action, that's 3 rows.
-- Provides complete before/after visibility for compliance.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_edit_history (
    id                          BIGSERIAL PRIMARY KEY,
    customer_id                 TEXT NOT NULL,               -- FK → customers.customer_id
    field_name                  TEXT NOT NULL,               -- Which field was changed (e.g., 'company_name', 'tin')
    old_value                   TEXT,                        -- Previous value (as text). NULL for initial creation.
    new_value                   TEXT,                        -- New value (as text). NULL for deletion.
    changed_by                  TEXT NOT NULL,               -- helium_user_id of the editor
    changed_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_reason               TEXT,                        -- Optional note explaining why the change was made

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);


-- ============================================================================
-- TABLE 6: customer_address_candidates (PDP STAGING — 14 fields)
-- ============================================================================
-- PDP resolution staging. When PDP resolves a raw address, multiple
-- candidate matches are stored here with confidence scores. The user
-- picks the best match (is_selected = 1), and the chosen address
-- fields are written to the customer or branch record.
--
-- After selection, non-selected candidates remain for audit/re-selection.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_address_candidates (
    id                          BIGSERIAL PRIMARY KEY,
    customer_id                 TEXT NOT NULL,               -- FK → customers.customer_id
    branch_id                   TEXT,                        -- FK → customer_branches.branch_id (NULL = customer HQ address)
    raw_input                   TEXT NOT NULL,               -- The unstructured address input (e.g., '15 Broad Street Victoria Island Lagos')

    -- Resolved address fields
    resolved_address            TEXT,                        -- PDP-resolved street address
    resolved_city               TEXT,                        -- PDP-resolved city
    resolved_state              TEXT,                        -- PDP-resolved state
    resolved_lga                TEXT,                        -- PDP-resolved LGA name
    resolved_lga_code           TEXT,                        -- PDP-resolved LGA code (FIRS format)
    resolved_state_code         TEXT,                        -- PDP-resolved state code (FIRS format)

    -- Scoring & selection
    confidence                  DOUBLE PRECISION DEFAULT 0,  -- PDP confidence score (0.0 to 1.0)
    is_selected                 INTEGER DEFAULT 0,           -- 1 = user's chosen candidate

    -- PDP metadata
    resolved_at                 TIMESTAMPTZ,                 -- When PDP processed this candidate
    resolved_by                 TEXT,                        -- PDP pipeline version/identifier

    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE,
    FOREIGN KEY (branch_id) REFERENCES customer_branches(branch_id) ON DELETE SET NULL
);


-- ============================================================================
-- INDEXES
-- ============================================================================

-- customers
CREATE INDEX IF NOT EXISTS idx_customers_company_name
    ON customers(company_name);
CREATE INDEX IF NOT EXISTS idx_customers_name_normalized
    ON customers(company_name_normalized)
    WHERE company_name_normalized IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_tin
    ON customers(tin)
    WHERE tin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_rc_number
    ON customers(rc_number)
    WHERE rc_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_email
    ON customers(email)
    WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_short_code
    ON customers(short_code)
    WHERE short_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_company_id
    ON customers(company_id)
    WHERE company_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_state
    ON customers(state)
    WHERE state IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_customer_type
    ON customers(customer_type)
    WHERE customer_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_tax_classification
    ON customers(tax_classification)
    WHERE tax_classification IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_is_mbs_registered
    ON customers(is_mbs_registered)
    WHERE is_mbs_registered = 1;
CREATE INDEX IF NOT EXISTS idx_customers_compliance_score
    ON customers(compliance_score);
CREATE INDEX IF NOT EXISTS idx_customers_pending_sync
    ON customers(pending_sync)
    WHERE pending_sync = 1;
CREATE INDEX IF NOT EXISTS idx_customers_tax_id
    ON customers(tax_id)
    WHERE tax_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_customer_code
    ON customers(customer_code)
    WHERE customer_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_customers_is_fze
    ON customers(is_fze)
    WHERE is_fze = 1;
CREATE INDEX IF NOT EXISTS idx_customers_deleted_at
    ON customers(deleted_at)
    WHERE deleted_at IS NOT NULL;

-- customer_branches
CREATE INDEX IF NOT EXISTS idx_customer_branches_customer_id
    ON customer_branches(customer_id);
CREATE INDEX IF NOT EXISTS idx_customer_branches_is_hq
    ON customer_branches(customer_id, is_hq)
    WHERE is_hq = 1;

-- customer_name_variants
CREATE INDEX IF NOT EXISTS idx_cust_variants_customer_id
    ON customer_name_variants(customer_id);
CREATE INDEX IF NOT EXISTS idx_cust_variants_normalized
    ON customer_name_variants(name_variant_normalized)
    WHERE name_variant_normalized IS NOT NULL;

-- customer_contacts
CREATE INDEX IF NOT EXISTS idx_cust_contacts_customer_id
    ON customer_contacts(customer_id);
CREATE INDEX IF NOT EXISTS idx_cust_contacts_primary
    ON customer_contacts(customer_id, is_primary)
    WHERE is_primary = 1;

-- customer_edit_history
CREATE INDEX IF NOT EXISTS idx_cust_edit_history_customer_id
    ON customer_edit_history(customer_id);
CREATE INDEX IF NOT EXISTS idx_cust_edit_history_changed_at
    ON customer_edit_history(changed_at);
CREATE INDEX IF NOT EXISTS idx_cust_edit_history_changed_by
    ON customer_edit_history(changed_by);

-- customer_address_candidates
CREATE INDEX IF NOT EXISTS idx_cust_addr_candidates_customer_id
    ON customer_address_candidates(customer_id);
CREATE INDEX IF NOT EXISTS idx_cust_addr_candidates_branch_id
    ON customer_address_candidates(branch_id)
    WHERE branch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cust_addr_candidates_selected
    ON customer_address_candidates(customer_id, is_selected)
    WHERE is_selected = 1;


-- ============================================================================
-- VIEW: vw_customer_display
-- ============================================================================
-- UI-priority columns for the Contact List tab in Float App.
-- Maps to the suggested SWDB column layout (Area A + B fields).
-- ============================================================================

CREATE OR REPLACE VIEW vw_customer_display AS
SELECT
    customer_id,
    company_name,
    customer_code,
    tin,
    rc_number,
    trading_name,
    short_code,
    customer_type,
    tax_classification,
    is_mbs_registered,
    is_fze,
    state,
    city,
    total_invoices,
    compliance_score,
    last_active_date,
    total_lifetime_value,
    created_at
FROM customers
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_customer_firs
-- ============================================================================
-- FIRS-mandatory fields. Minimal set for FIRS payload construction and
-- compliance reporting. Includes tax identifiers, MBS status, and
-- FIRS location codes.
-- ============================================================================

CREATE OR REPLACE VIEW vw_customer_firs AS
SELECT
    customer_id,
    company_name,
    tin,
    rc_number,
    tax_id,
    primary_identifier,
    is_mbs_registered,
    tax_classification,
    is_fze,
    address,
    city,
    state,
    state_code,
    lga,
    lga_code,
    country_code,
    email,
    phone,
    business_description
FROM customers
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_customer_metrics
-- ============================================================================
-- Aggregate performance metrics for the Statistics mApp.
-- All 15 denormalized aggregate fields + compliance score.
-- ============================================================================

CREATE OR REPLACE VIEW vw_customer_metrics AS
SELECT
    customer_id,
    company_name,
    customer_type,
    -- Compliance
    compliance_score,
    compliance_details,
    is_mbs_registered,
    -- Volume
    total_invoices,
    average_invoice_size,
    total_transmitted,
    total_accepted,
    receivables_rejected,
    payable_rejected,
    total_pending,
    -- Dates
    last_invoice_date,
    last_purchased_date,
    last_inbound_date,
    last_active_date,
    -- Frequency
    payable_frequency,
    receivables_frequency,
    -- Value
    total_lifetime_value,
    total_lifetime_tax
FROM customers
WHERE deleted_at IS NULL;


-- ============================================================================
-- CATEGORY VIEWS (permission-ready field grouping)
--
-- Views organized by DATA CATEGORY, not by role. The permission system
-- will layer access control dynamically by granting SELECT on specific
-- category views per role.
--
-- Categories:
--   operational  — user-facing business data
--   identity     — audit/edit trace fields
--   analytics    — performance aggregate fields
-- ============================================================================


-- ── customers: operational ──────────────────────────────────────────────

CREATE OR REPLACE VIEW vw_customers_operational AS
SELECT
    customer_id,
    -- Company info
    company_name,
    trading_name,
    short_code,
    customer_code,
    email,
    phone,
    website,
    business_description,
    -- Identifiers
    tin,
    rc_number,
    tax_id,
    primary_identifier,
    -- Address
    address,
    city,
    state,
    postal_code,
    country,
    country_code,
    lga,
    lga_code,
    state_code,
    -- Classification
    customer_type,
    tax_classification,
    industry,
    is_fze,
    -- Tax & compliance
    is_mbs_registered,
    compliance_score,
    -- Operational
    business_unit,
    default_due_date_days
FROM customers
WHERE deleted_at IS NULL;


-- ── customers: identity ─────────────────────────────────────────────────

CREATE OR REPLACE VIEW vw_customers_identity AS
SELECT
    customer_id,
    company_name,
    -- Audit trail
    created_by,
    updated_by,
    created_at,
    updated_at,
    deleted_at,
    -- Multi-tenancy
    company_id,
    pending_sync
FROM customers;


-- ── customers: analytics ────────────────────────────────────────────────

CREATE OR REPLACE VIEW vw_customers_analytics AS
SELECT
    customer_id,
    company_name,
    customer_type,
    tax_classification,
    -- Volume metrics
    total_invoices,
    average_invoice_size,
    total_transmitted,
    total_accepted,
    receivables_rejected,
    payable_rejected,
    total_pending,
    -- Dates
    last_invoice_date,
    last_purchased_date,
    last_inbound_date,
    last_active_date,
    -- Frequency
    payable_frequency,
    receivables_frequency,
    -- Value
    total_lifetime_value,
    total_lifetime_tax,
    -- Compliance
    compliance_score,
    compliance_details,
    is_mbs_registered,
    -- Context
    created_at
FROM customers
WHERE deleted_at IS NULL;


-- ============================================================================
-- SCHEMA VERSION TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS customer_schema_version (
    version         TEXT PRIMARY KEY NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description     TEXT
);

INSERT INTO customer_schema_version (version, description)
VALUES ('1.0.0', 'Initial canonical schema. Customer-centric model, single identity (customer_id UUIDv7), denormalized aggregates, compliance scoring, branch support. 7 tables, 111 fields.')
ON CONFLICT DO NOTHING;

INSERT INTO customer_schema_version (version, description)
VALUES ('1.1.0', 'Edit history + address candidates. customer_edit_history for per-field audit trail. customer_address_candidates for PDP resolution staging. 6 views (3 purpose + 3 category).')
ON CONFLICT DO NOTHING;

INSERT INTO customer_schema_version (version, description)
VALUES ('1.2.0', 'FIRS alignment + WhatsApp field review. Added: tax_id, customer_code, business_description, is_fze, business_unit, default_due_date_days. Merged address_line1+address_line2 into single address field. customers 49→54, branches 19→18. Total 111→115 fields.')
ON CONFLICT DO NOTHING;

INSERT INTO customer_schema_version (version, description)
VALUES ('1.2.0', 'Initial PostgreSQL translation from canonical SQLite v1')
ON CONFLICT DO NOTHING;
