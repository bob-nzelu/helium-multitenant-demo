-- ============================================================================
-- Abbey Mortgage Demo — Canonical Schema Migration (PostgreSQL)
-- ============================================================================
-- Derived from Helium Canonical Schemas:
--   invoices v2.1.3.0, customers v1.2.0, inventory v1.0.0
-- Adapted for PostgreSQL (multi-tenant, tenant_id scoping)
-- Idempotent: CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO NOTHING
-- ============================================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- SCHEMA VERSION TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_version (version, description) VALUES
    ('2.1.3.0', 'Canonical invoice schema (130 fields, 10 tables)')
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version, description) VALUES
    ('cust-1.2.0', 'Canonical customer schema (54 fields, 7 tables)')
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version, description) VALUES
    ('inv-1.0.0', 'Canonical inventory schema (36 fields, 6 tables)')
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version, description) VALUES
    ('users-1.0.0', 'Demo user schema with role-based access')
ON CONFLICT DO NOTHING;


-- ============================================================================
-- TABLE: invoices (Canonical v2.1.3.0 — adapted for PostgreSQL)
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoices (
    id                          SERIAL PRIMARY KEY,
    tenant_id                   TEXT NOT NULL DEFAULT 'abbey',

    -- A. Primary identification
    invoice_id                  TEXT UNIQUE NOT NULL,
    helium_invoice_no           TEXT UNIQUE NOT NULL,
    invoice_number              TEXT NOT NULL,
    irn                         TEXT UNIQUE NOT NULL,
    csid                        TEXT,
    csid_status                 TEXT CHECK (csid_status IN ('PENDING', 'ISSUED', 'FAILED')),
    invoice_trace_id            TEXT,
    user_trace_id               TEXT,
    x_trace_id                  TEXT,
    finalize_trace_id           TEXT,
    config_version_id           TEXT,
    schema_version_applied      TEXT DEFAULT '2.1.3.0',

    -- B. Classifiers
    direction                   TEXT NOT NULL DEFAULT 'OUTBOUND'
                                    CHECK (direction IN ('OUTBOUND', 'INBOUND')),
    document_type               TEXT NOT NULL DEFAULT 'COMMERCIAL_INVOICE'
                                    CHECK (document_type IN (
                                        'COMMERCIAL_INVOICE', 'CREDIT_NOTE', 'DEBIT_NOTE',
                                        'SELF_BILLED_INVOICE', 'SELF_BILLED_CREDIT'
                                    )),
    firs_invoice_type_code      TEXT,
    transaction_type            TEXT NOT NULL DEFAULT 'B2B'
                                    CHECK (transaction_type IN ('B2B', 'B2G', 'B2C')),

    -- C. Dates
    issue_date                  TEXT NOT NULL,
    issue_time                  TEXT,
    due_date                    TEXT,
    payment_due_date            TEXT,
    sign_date                   TEXT,
    transmission_date           TEXT,
    acknowledgement_date        TEXT,

    -- D. Financial
    document_currency_code      TEXT NOT NULL DEFAULT 'NGN',
    tax_currency_code           TEXT NOT NULL DEFAULT 'NGN',
    subtotal                    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tax_amount                  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_amount                DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    exchange_rate               DOUBLE PRECISION,
    has_discount                INTEGER NOT NULL DEFAULT 0,
    wht_amount                  DOUBLE PRECISION,
    discount_amount             DOUBLE PRECISION,
    adjustment_type             TEXT,

    -- E. Payment
    payment_means               TEXT CHECK (payment_means IS NULL OR payment_means IN (
                                    'CASH', 'CHEQUE', 'BANK_TRANSFER', 'CARD',
                                    'MOBILE_MONEY', 'DIGITAL_WALLET', 'OFFSET', 'OTHER'
                                )),
    firs_payment_means_code     TEXT,

    -- F. Delivery
    delivery_date               TEXT,
    delivery_address            TEXT,

    -- G. Commercial references
    purchase_order_number       TEXT,
    contract_number             TEXT,

    -- H. Three-status model
    workflow_status             TEXT NOT NULL DEFAULT 'COMMITTED'
                                    CHECK (workflow_status IN (
                                        'COMMITTED', 'QUEUED', 'TRANSMITTING', 'TRANSMITTED',
                                        'VALIDATED', 'ERROR', 'ARCHIVED'
                                    )),
    transmission_status         TEXT NOT NULL DEFAULT 'NOT_REQUIRED'
                                    CHECK (transmission_status IN (
                                        'NOT_REQUIRED', 'PENDING_PRECHECK', 'PRECHECK_PASSED',
                                        'BLOCKED_COUNTERPARTY', 'SIGNING', 'SIGNED',
                                        'TRANSMIT_PENDING', 'TRANSMITTING', 'TRANSMITTED',
                                        'ACCEPTED', 'REJECTED', 'FAILED_RETRYABLE', 'FAILED_TERMINAL'
                                    )),
    transmission_status_error   TEXT,
    status_notes                TEXT,
    payment_status              TEXT NOT NULL DEFAULT 'UNPAID'
                                    CHECK (payment_status IN ('UNPAID', 'PAID', 'PARTIAL', 'DISPUTED', 'CANCELLED')),
    payment_updated_at          TEXT,
    payment_updated_by          TEXT,

    -- I. Retry mechanics
    retry_count                 INTEGER NOT NULL DEFAULT 0,
    last_retry_at               TEXT,
    next_retry_at               TEXT,

    -- J. FIRS audit artefacts
    firs_confirmation           TEXT,
    firs_response_data          TEXT,
    firs_submitted_payload      TEXT,
    qr_code_data                TEXT,

    -- K. Seller party (14 fields)
    company_id                  TEXT NOT NULL DEFAULT 'abbey',
    seller_id                   TEXT,
    seller_business_id          TEXT,
    seller_name                 TEXT,
    seller_tin                  TEXT,
    seller_tax_id               TEXT,
    seller_rc_number            TEXT,
    seller_email                TEXT,
    seller_phone                TEXT,
    seller_address              TEXT,
    seller_city                 TEXT,
    seller_postal_code          TEXT,
    seller_lga_code             TEXT,
    seller_state_code           TEXT,
    seller_country_code         TEXT DEFAULT 'NG',

    -- L. Buyer party (13 fields)
    buyer_id                    TEXT,
    buyer_business_id           TEXT,
    buyer_name                  TEXT,
    buyer_tin                   TEXT,
    buyer_tax_id                TEXT,
    buyer_rc_number             TEXT,
    buyer_email                 TEXT,
    buyer_phone                 TEXT,
    buyer_address               TEXT,
    buyer_city                  TEXT,
    buyer_postal_code           TEXT,
    buyer_lga_code              TEXT,
    buyer_state_code            TEXT,
    buyer_country_code          TEXT DEFAULT 'NG',

    -- M. User
    helium_user_id              TEXT,
    user_email                  TEXT,
    user_name                   TEXT,
    created_by                  TEXT,

    -- N. Queue / batch / blob
    queue_id                    TEXT UNIQUE,
    batch_id                    TEXT,
    file_id                     TEXT,
    blob_uuid                   TEXT,
    fixed_invoice_blob_uuid     TEXT,
    original_filename           TEXT,

    -- O. Source
    source                      TEXT,
    source_id                   TEXT,

    -- P. Display / SWDB
    reference                   TEXT,
    terms                       TEXT,
    attachment_count            INTEGER NOT NULL DEFAULT 0,

    -- Q. Notes
    notes_to_firs               TEXT,
    payment_terms_note          TEXT,

    -- Q2. ReviewPage fields
    product_summary             TEXT,
    line_items_count            INTEGER DEFAULT 0,
    foc_line_count              INTEGER DEFAULT 0,
    document_source             TEXT,
    other_taxes                 DOUBLE PRECISION DEFAULT 0,
    custom_duties               DOUBLE PRECISION,

    -- Q2. Commit-time customer snapshots
    customer_total_invoices_at_commit   INTEGER,
    customer_lifetime_value_at_commit   DOUBLE PRECISION,
    customer_compliance_score_at_commit INTEGER,

    -- R. Inbound invoice fields
    inbound_received_at         TEXT,
    inbound_status              TEXT CHECK (inbound_status IS NULL OR inbound_status IN (
                                    'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', 'EXPIRED'
                                )),
    inbound_action_at           TEXT,
    inbound_action_by_user_id   TEXT,
    inbound_action_by_user_email TEXT,
    inbound_action_reason       TEXT,
    inbound_payload_json        TEXT,
    reminder_count              INTEGER NOT NULL DEFAULT 0,

    -- S. Processing telemetry
    finalized_at                TEXT,
    processing_started_at       TEXT,
    processing_completed_at     TEXT,
    processing_duration_ms      INTEGER,

    -- T. Audit
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ,
    deleted_by                  TEXT,

    -- U. Machine & Session
    machine_guid                TEXT,
    mac_address                 TEXT,
    computer_name               TEXT,
    float_id                    TEXT,
    session_id                  TEXT
);

-- Key indexes for invoices
CREATE INDEX IF NOT EXISTS idx_inv_tenant_id ON invoices(tenant_id);
CREATE INDEX IF NOT EXISTS idx_inv_direction ON invoices(direction);
CREATE INDEX IF NOT EXISTS idx_inv_workflow_status ON invoices(workflow_status);
CREATE INDEX IF NOT EXISTS idx_inv_issue_date ON invoices(issue_date);
CREATE INDEX IF NOT EXISTS idx_inv_company_id ON invoices(company_id);
CREATE INDEX IF NOT EXISTS idx_inv_created_at ON invoices(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_seller_name ON invoices(seller_name);
CREATE INDEX IF NOT EXISTS idx_inv_buyer_name ON invoices(buyer_name);


-- ============================================================================
-- TABLE: invoice_line_items
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id                          SERIAL PRIMARY KEY,
    line_id                     TEXT UNIQUE,
    invoice_id                  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    line_number                 INTEGER NOT NULL,
    line_item_type              TEXT NOT NULL DEFAULT 'SERVICE'
                                    CHECK (line_item_type IN ('GOODS', 'SERVICE')),
    description                 TEXT NOT NULL,
    quantity                    DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
    unit_price                  DOUBLE PRECISION NOT NULL CHECK (unit_price >= 0),
    line_total                  DOUBLE PRECISION NOT NULL,
    tax_rate                    DOUBLE PRECISION DEFAULT 0.075,
    tax_amount                  DOUBLE PRECISION DEFAULT 0.0,
    hsn_code                    TEXT,
    product_category            TEXT,
    service_code                TEXT,
    service_category            TEXT,
    product_id                  TEXT,
    product_code                TEXT,
    product_name                TEXT,
    customer_sku                TEXT,
    oem_sku                     TEXT,
    helium_sku                  TEXT,
    full_description            TEXT,
    vat_rate                    DOUBLE PRECISION,
    classification_confidence   DOUBLE PRECISION,
    classification_source       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (invoice_id, line_number)
);

CREATE INDEX IF NOT EXISTS idx_lines_invoice_id ON invoice_line_items(invoice_id);


-- ============================================================================
-- TABLE: invoice_tax_categories
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_tax_categories (
    id                  SERIAL PRIMARY KEY,
    invoice_id          INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    tax_category        TEXT NOT NULL,
    tax_rate            DOUBLE PRECISION NOT NULL DEFAULT 0.075,
    taxable_amount      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tax_amount          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (invoice_id, tax_category)
);


-- ============================================================================
-- TABLE: customers (Canonical v1.2.0)
-- ============================================================================

CREATE TABLE IF NOT EXISTS customers (
    customer_id                 TEXT PRIMARY KEY NOT NULL,
    tenant_id                   TEXT NOT NULL DEFAULT 'abbey',

    -- Identifiers
    tin                         TEXT,
    rc_number                   TEXT,
    tax_id                      TEXT,
    primary_identifier          TEXT NOT NULL CHECK(primary_identifier IN ('TIN', 'RC_NUMBER')),

    -- Company Info
    company_name                TEXT NOT NULL,
    company_name_normalized     TEXT,
    trading_name                TEXT,
    short_code                  TEXT,
    customer_code               TEXT UNIQUE,
    email                       TEXT,
    phone                       TEXT,
    website                     TEXT,
    business_description        TEXT,

    -- Address
    address                     TEXT,
    city                        TEXT,
    state                       TEXT,
    postal_code                 TEXT,
    country                     TEXT DEFAULT 'NGA',
    country_code                TEXT DEFAULT 'NG',
    lga                         TEXT,
    lga_code                    TEXT,
    state_code                  TEXT,

    -- Classification
    customer_type               TEXT CHECK(customer_type IS NULL OR customer_type IN ('B2B', 'B2G')),
    tax_classification          TEXT CHECK(tax_classification IS NULL OR tax_classification IN ('STANDARD', 'EXEMPT')),
    industry                    TEXT,
    is_fze                      INTEGER DEFAULT 0,

    -- Tax & Compliance
    is_mbs_registered           INTEGER DEFAULT 0,
    compliance_score            INTEGER DEFAULT 0,
    compliance_details          TEXT,

    -- Operational
    business_unit               TEXT,
    default_due_date_days       INTEGER,

    -- Denormalized Aggregates
    total_invoices              INTEGER DEFAULT 0,
    average_invoice_size        DOUBLE PRECISION DEFAULT 0,
    total_transmitted           INTEGER DEFAULT 0,
    total_accepted              INTEGER DEFAULT 0,
    receivables_rejected        INTEGER DEFAULT 0,
    payable_rejected            INTEGER DEFAULT 0,
    total_pending               INTEGER DEFAULT 0,
    last_invoice_date           TEXT,
    last_purchased_date         TEXT,
    last_inbound_date           TEXT,
    last_active_date            TEXT,
    payable_frequency           TEXT CHECK(payable_frequency IS NULL OR payable_frequency IN (
        'daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'annually', 'irregular'
    )),
    receivables_frequency       TEXT CHECK(receivables_frequency IS NULL OR receivables_frequency IN (
        'daily', 'weekly', 'biweekly', 'monthly', 'quarterly', 'annually', 'irregular'
    )),
    total_lifetime_value        DOUBLE PRECISION DEFAULT 0,
    total_lifetime_tax          DOUBLE PRECISION DEFAULT 0,

    -- Lifecycle
    company_id                  TEXT DEFAULT 'abbey',
    pending_sync                INTEGER DEFAULT 0,

    -- Audit
    created_by                  TEXT,
    updated_by                  TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cust_tenant_id ON customers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cust_company_name ON customers(company_name);
CREATE INDEX IF NOT EXISTS idx_cust_tin ON customers(tin) WHERE tin IS NOT NULL;


-- ============================================================================
-- TABLE: inventory (Canonical v1.0.0)
-- ============================================================================

CREATE TABLE IF NOT EXISTS inventory (
    product_id                  TEXT PRIMARY KEY NOT NULL,
    tenant_id                   TEXT NOT NULL DEFAULT 'abbey',

    -- Identifiers
    helium_sku                  TEXT UNIQUE,
    customer_sku                TEXT NOT NULL,
    oem_sku                     TEXT,

    -- Product Info
    product_name                TEXT NOT NULL,
    product_name_normalized     TEXT,
    description                 TEXT,
    unit_of_measure             TEXT,

    -- Classification
    hsn_code                    TEXT,
    service_code                TEXT,
    product_category            TEXT,
    service_category            TEXT,

    -- Type
    type                        TEXT NOT NULL DEFAULT 'SERVICE' CHECK(type IN ('GOODS', 'SERVICE')),

    -- Tax/VAT
    vat_treatment               TEXT DEFAULT 'STANDARD' CHECK(vat_treatment IS NULL OR vat_treatment IN ('STANDARD', 'ZERO_RATED', 'EXEMPT')),
    vat_rate                    DOUBLE PRECISION DEFAULT 7.5,
    is_tax_exempt               INTEGER DEFAULT 0,

    -- Pricing
    currency                    TEXT DEFAULT 'NGN',

    -- PDP Classification
    hs_codes                    TEXT,
    service_codes               TEXT,
    product_categories          TEXT,
    service_categories          TEXT,
    classification_confidence   DOUBLE PRECISION DEFAULT 0,
    classification_source       TEXT,
    last_classified_at          TEXT,
    last_classified_by          TEXT,

    -- Aggregates
    total_times_invoiced        INTEGER DEFAULT 0,
    last_invoice_date           TEXT,
    total_revenue               DOUBLE PRECISION DEFAULT 0,
    avg_unit_price              DOUBLE PRECISION DEFAULT 0,
    top_customer                TEXT,

    -- Notes
    notes                       TEXT,

    -- Audit
    created_by                  TEXT,
    updated_by                  TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at                  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_inv_prod_tenant_id ON inventory(tenant_id);
CREATE INDEX IF NOT EXISTS idx_inv_prod_name ON inventory(product_name);
CREATE INDEX IF NOT EXISTS idx_inv_prod_type ON inventory(type);


-- ============================================================================
-- TABLE: users (Demo schema)
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    user_id                     TEXT PRIMARY KEY NOT NULL,
    tenant_id                   TEXT NOT NULL DEFAULT 'abbey',
    username                    TEXT NOT NULL,
    email                       TEXT NOT NULL,
    display_name                TEXT NOT NULL,
    role                        TEXT NOT NULL CHECK(role IN ('Owner', 'Admin', 'Support', 'Operator')),
    permissions                 JSONB NOT NULL DEFAULT '[]',
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    pin_hash                    TEXT,
    last_login_at               TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, username),
    UNIQUE(tenant_id, email)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
