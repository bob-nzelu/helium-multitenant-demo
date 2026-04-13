-- Invoices Schema (PostgreSQL) — translated from canonical SQLite v2.1.1.0
SET search_path TO invoices;


-- ============================================================================
-- SHARED TRIGGER FUNCTION: auto-update updated_at on modification
-- ============================================================================

CREATE OR REPLACE FUNCTION invoices.fn_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- TABLE 0: SCHEMA VERSION
-- Purpose: Tracks schema migrations. Audit: invoices.schema_version_applied
--          records which version was active when each invoice was created.
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version     TEXT PRIMARY KEY NOT NULL,
    applied_at  TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_version (version, description)
VALUES ('2.1.0.1', 'Seller/buyer party split, city_name separation, FIRS tax categories (25), date lifecycle fields, IRN/QR ownership fix, notes standardization')
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version, description)
VALUES ('2.1.0.2', 'WHT tax category (26 values), allowance_charge table, wht_amount/discount_amount/adjustment_type columns')
ON CONFLICT DO NOTHING;

INSERT INTO schema_version (version, description)
VALUES ('2.1.1.0', 'Security spec alignment: 3-level trace model, composite machine fingerprint, HeartBeat identity fields')
ON CONFLICT DO NOTHING;


-- ============================================================================
-- TABLE 1: invoices (115 fields)
-- Owner:  Core (authoritative writer)
-- Reads:  Edge (transmission_status updates via contract), Float SDK (read-only)
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoices (

    -- A. Primary identification (10 fields)

    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  TEXT UNIQUE NOT NULL,

    helium_invoice_no           TEXT UNIQUE NOT NULL,

    invoice_number              TEXT NOT NULL,

    irn                         TEXT UNIQUE NOT NULL,

    csid                        TEXT,
    csid_status                 TEXT CHECK (csid_status IN ('PENDING', 'ISSUED', 'FAILED')),

    invoice_trace_id            TEXT,

    user_trace_id               TEXT,

    x_trace_id                  TEXT,

    config_version_id           TEXT,

    schema_version_applied      TEXT,

    -- B. Three independent classifiers + FIRS type code (4 fields)

    direction                   TEXT NOT NULL DEFAULT 'OUTBOUND'
                                    CHECK (direction IN ('OUTBOUND', 'INBOUND')),

    document_type               TEXT NOT NULL DEFAULT 'COMMERCIAL_INVOICE'
                                    CHECK (document_type IN (
                                        'COMMERCIAL_INVOICE',
                                        'CREDIT_NOTE',
                                        'DEBIT_NOTE',
                                        'SELF_BILLED_INVOICE',
                                        'SELF_BILLED_CREDIT'
                                    )),

    firs_invoice_type_code      TEXT,

    transaction_type            TEXT NOT NULL DEFAULT 'B2B'
                                    CHECK (transaction_type IN ('B2B', 'B2G', 'B2C')),

    -- C. Dates (7 fields)

    issue_date                  TEXT NOT NULL,

    issue_time                  TEXT,

    due_date                    TEXT,

    payment_due_date            TEXT,

    sign_date                   TEXT,

    transmission_date           TEXT,

    acknowledgement_date        TEXT,

    -- D. Financial (7 fields)

    document_currency_code      TEXT NOT NULL DEFAULT 'NGN',

    tax_currency_code           TEXT NOT NULL DEFAULT 'NGN',

    subtotal                    NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    tax_amount                  NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    total_amount                NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    exchange_rate               DOUBLE PRECISION,

    has_discount                INTEGER NOT NULL DEFAULT 0,

    wht_amount                  NUMERIC(18,4),

    discount_amount             NUMERIC(18,4),

    adjustment_type             TEXT,

    -- E. Payment means + FIRS code (2 fields)

    payment_means               TEXT CHECK (payment_means IS NULL OR payment_means IN (
                                    'CASH', 'CHEQUE', 'BANK_TRANSFER', 'CARD',
                                    'MOBILE_MONEY', 'DIGITAL_WALLET', 'OFFSET', 'OTHER'
                                )),

    firs_payment_means_code     TEXT,

    -- F. Delivery / fulfilment (2 fields)

    delivery_date               TEXT,
    delivery_address            TEXT,

    -- G. Commercial references (2 fields)

    purchase_order_number       TEXT,

    contract_number             TEXT,

    -- H. Three-status model (4 fields)

    workflow_status             TEXT NOT NULL DEFAULT 'COMMITTED'
                                    CHECK (workflow_status IN (
                                        'COMMITTED',
                                        'QUEUED',
                                        'TRANSMITTING',
                                        'TRANSMITTED',
                                        'VALIDATED',
                                        'ERROR',
                                        'ARCHIVED'
                                    )),

    transmission_status         TEXT NOT NULL DEFAULT 'NOT_REQUIRED'
                                    CHECK (transmission_status IN (
                                        'NOT_REQUIRED',
                                        'PENDING_PRECHECK',
                                        'PRECHECK_PASSED',
                                        'BLOCKED_COUNTERPARTY',
                                        'SIGNING',
                                        'SIGNED',
                                        'TRANSMIT_PENDING',
                                        'TRANSMITTING',
                                        'TRANSMITTED',
                                        'ACCEPTED',
                                        'REJECTED',
                                        'FAILED_RETRYABLE',
                                        'FAILED_TERMINAL'
                                    )),

    transmission_status_error   TEXT,

    payment_status              TEXT NOT NULL DEFAULT 'UNPAID'
                                    CHECK (payment_status IN (
                                        'UNPAID',
                                        'PAID',
                                        'PARTIAL',
                                        'DISPUTED',
                                        'CANCELLED'
                                    )),

    -- I. Retry mechanics (3 fields)

    retry_count                 INTEGER NOT NULL DEFAULT 0,

    last_retry_at               TEXT,

    next_retry_at               TEXT,

    -- J. FIRS audit artefacts (3 fields)

    firs_confirmation           TEXT,

    firs_response_data          JSONB,

    qr_code_data                JSONB,

    -- K. Company / seller party (14 fields)

    company_id                  TEXT NOT NULL,

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

    -- M. User (4 fields)

    helium_user_id              TEXT,

    user_email                  TEXT,

    user_name                   TEXT,

    created_by                  TEXT,

    -- N. Queue / batch / blob source references (5 fields)

    queue_id                    TEXT UNIQUE,

    batch_id                    TEXT,

    file_id                     TEXT,

    blob_uuid                   TEXT,

    original_filename           TEXT,

    -- O. Source system context (2 fields)

    source                      TEXT,

    source_id                   TEXT,

    -- P. Display / SWDB fields (4 fields)

    reference                   TEXT,

    category                    TEXT,

    terms                       TEXT,

    attachment_count            INTEGER NOT NULL DEFAULT 0,

    -- Q. Notes (2 fields)

    notes_to_firs               TEXT,

    payment_terms_note          TEXT,

    -- R. Inbound invoice fields (7 fields)

    inbound_received_at         TEXT,

    inbound_status              TEXT CHECK (inbound_status IN (
                                    'PENDING_REVIEW',
                                    'ACCEPTED',
                                    'REJECTED',
                                    'EXPIRED'
                                )),

    inbound_action_at           TEXT,

    inbound_action_by_user_id   TEXT,

    inbound_action_by_user_email TEXT,

    inbound_action_reason       TEXT,

    inbound_payload_json        JSONB,

    reminder_count              INTEGER NOT NULL DEFAULT 0,

    -- S. Processing telemetry (4 fields)

    finalized_at                TEXT,
    processing_started_at       TEXT,
    processing_completed_at     TEXT,
    processing_duration_ms      INTEGER,

    -- T. Audit (4 fields)

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    deleted_at                  TIMESTAMPTZ,
    deleted_by                  TEXT,

    -- U. Machine & Session Context (5 fields)

    machine_guid                TEXT,

    mac_address                 TEXT,

    computer_name               TEXT,

    float_id                    TEXT,

    session_id                  TEXT
);

-- Indexes for invoices

-- Primary lookups
CREATE INDEX IF NOT EXISTS idx_inv_invoice_id         ON invoices(invoice_id);
CREATE INDEX IF NOT EXISTS idx_inv_helium_no          ON invoices(helium_invoice_no);
CREATE INDEX IF NOT EXISTS idx_inv_invoice_number     ON invoices(invoice_number);
CREATE INDEX IF NOT EXISTS idx_inv_irn                ON invoices(irn) WHERE irn IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_queue_id           ON invoices(queue_id) WHERE queue_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_batch_id           ON invoices(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_blob_uuid          ON invoices(blob_uuid) WHERE blob_uuid IS NOT NULL;

-- Party lookups
CREATE INDEX IF NOT EXISTS idx_inv_company_id         ON invoices(company_id);
CREATE INDEX IF NOT EXISTS idx_inv_seller_id          ON invoices(seller_id) WHERE seller_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_buyer_id           ON invoices(buyer_id) WHERE buyer_id IS NOT NULL;

-- Status lookups
CREATE INDEX IF NOT EXISTS idx_inv_workflow_status    ON invoices(workflow_status);
CREATE INDEX IF NOT EXISTS idx_inv_transmission_status ON invoices(transmission_status);
CREATE INDEX IF NOT EXISTS idx_inv_payment_status     ON invoices(payment_status);

-- Classifier lookups
CREATE INDEX IF NOT EXISTS idx_inv_direction          ON invoices(direction);
CREATE INDEX IF NOT EXISTS idx_inv_document_type      ON invoices(document_type);
CREATE INDEX IF NOT EXISTS idx_inv_transaction_type   ON invoices(transaction_type);

-- Date lookups
CREATE INDEX IF NOT EXISTS idx_inv_issue_date         ON invoices(issue_date);
CREATE INDEX IF NOT EXISTS idx_inv_due_date           ON invoices(due_date) WHERE due_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_payment_due_date   ON invoices(payment_due_date) WHERE payment_due_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_sign_date          ON invoices(sign_date) WHERE sign_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_transmission_date  ON invoices(transmission_date) WHERE transmission_date IS NOT NULL;

-- Audit / chronology
CREATE INDEX IF NOT EXISTS idx_inv_created_at         ON invoices(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_updated_at         ON invoices(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_deleted_at         ON invoices(deleted_at) WHERE deleted_at IS NOT NULL;

-- Composite indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_inv_company_status     ON invoices(company_id, workflow_status);
CREATE INDEX IF NOT EXISTS idx_inv_company_direction  ON invoices(company_id, direction);
CREATE INDEX IF NOT EXISTS idx_inv_status_date        ON invoices(workflow_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inv_payment_due        ON invoices(payment_status, payment_due_date) WHERE payment_due_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_inbound_review     ON invoices(direction, inbound_status) WHERE direction = 'INBOUND';

-- v2.2 party / IBN indexes
CREATE INDEX IF NOT EXISTS idx_inv_seller_state       ON invoices(seller_state_code) WHERE seller_state_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_seller_lga         ON invoices(seller_lga_code) WHERE seller_lga_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_buyer_state        ON invoices(buyer_state_code) WHERE buyer_state_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_buyer_lga          ON invoices(buyer_lga_code) WHERE buyer_lga_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_has_discount       ON invoices(has_discount) WHERE has_discount = 1;
CREATE INDEX IF NOT EXISTS idx_inv_wht_amount         ON invoices(wht_amount) WHERE wht_amount IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_payment_means      ON invoices(payment_means) WHERE payment_means IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_inv_po_number          ON invoices(purchase_order_number) WHERE purchase_order_number IS NOT NULL;

-- v2.1.1.0 Security spec: trace chain, identity, session indexes
CREATE INDEX IF NOT EXISTS idx_invoices_invoice_trace ON invoices (invoice_trace_id);
CREATE INDEX IF NOT EXISTS idx_invoices_x_trace       ON invoices (x_trace_id);
CREATE INDEX IF NOT EXISTS idx_invoices_helium_user   ON invoices (helium_user_id);
CREATE INDEX IF NOT EXISTS idx_invoices_machine       ON invoices (machine_guid);
CREATE INDEX IF NOT EXISTS idx_invoices_float         ON invoices (float_id);


-- Trigger: auto-update updated_at on modification
DROP TRIGGER IF EXISTS trg_invoices_updated_at ON invoices;
CREATE TRIGGER trg_invoices_updated_at
BEFORE UPDATE ON invoices
FOR EACH ROW EXECUTE FUNCTION invoices.fn_updated_at();


-- ============================================================================
-- TABLE 2: invoice_line_items (21 fields)
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id                          BIGSERIAL PRIMARY KEY,

    line_id                     TEXT UNIQUE,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    line_number                 INTEGER NOT NULL,

    line_item_type              TEXT NOT NULL DEFAULT 'GOODS'
                                    CHECK (line_item_type IN ('GOODS', 'SERVICE')),

    -- Common fields
    description                 TEXT NOT NULL,
    quantity                    NUMERIC(18,4) NOT NULL CHECK (quantity > 0),
    unit_price                  NUMERIC(18,4) NOT NULL CHECK (unit_price >= 0),
    line_total                  NUMERIC(18,4) NOT NULL,
    tax_rate                    DOUBLE PRECISION DEFAULT 0.0,
    tax_amount                  NUMERIC(18,4) DEFAULT 0.0,

    -- Goods classification
    hsn_code                    TEXT,
    product_category            TEXT,

    -- Service classification
    service_code                TEXT,
    service_category            TEXT,

    -- Product reference
    product_id                  TEXT,
    product_code                TEXT,
    product_name                TEXT,

    -- PDP classification intelligence
    classification_confidence   DOUBLE PRECISION,
    classification_source       TEXT,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (invoice_id, line_number)
);

CREATE INDEX IF NOT EXISTS idx_lines_invoice_id   ON invoice_line_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_lines_product_id   ON invoice_line_items(product_id) WHERE product_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lines_hsn_code     ON invoice_line_items(hsn_code) WHERE hsn_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lines_type         ON invoice_line_items(line_item_type);


-- ============================================================================
-- TABLE 3: invoice_references (7 fields)
-- Purpose: Links invoice to prior invoices (credit/debit note chains, origin refs).
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_references (
    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    reference_type              TEXT NOT NULL
                                    CHECK (reference_type IN (
                                        'CREDIT_NOTE', 'DEBIT_NOTE', 'ORIGIN', 'ORDER'
                                    )),

    reference_invoice_id        TEXT,

    reference_irn               TEXT,

    reference_issue_date        TEXT,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refs_invoice_id        ON invoice_references(invoice_id);
CREATE INDEX IF NOT EXISTS idx_refs_ref_invoice_id    ON invoice_references(reference_invoice_id) WHERE reference_invoice_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_refs_ref_irn           ON invoice_references(reference_irn) WHERE reference_irn IS NOT NULL;


-- ============================================================================
-- TABLE 4: invoice_tax_categories (7 fields)
-- Purpose: One invoice can have MULTIPLE tax categories applied across its
--          line items. This table stores the per-category tax breakdown.
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_tax_categories (
    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    tax_category                TEXT NOT NULL
                                    CHECK (tax_category IN (
                                        'STANDARD_GST',
                                        'REDUCED_GST',
                                        'ZERO_GST',
                                        'STANDARD_VAT',
                                        'REDUCED_VAT',
                                        'ZERO_VAT',
                                        'STATE_SALES_TAX',
                                        'LOCAL_SALES_TAX',
                                        'ALCOHOL_EXCISE_TAX',
                                        'TOBACCO_EXCISE_TAX',
                                        'FUEL_EXCISE_TAX',
                                        'CORPORATE_INCOME_TAX',
                                        'PERSONAL_INCOME_TAX',
                                        'SOCIAL_SECURITY_TAX',
                                        'MEDICARE_TAX',
                                        'REAL_ESTATE_TAX',
                                        'PERSONAL_PROPERTY_TAX',
                                        'CARBON_TAX',
                                        'PLASTIC_TAX',
                                        'IMPORT_DUTY',
                                        'EXPORT_DUTY',
                                        'LUXURY_TAX',
                                        'SERVICE_TAX',
                                        'TOURISM_TAX',
                                        'NEPZA',
                                        'WITHHOLDING_TAX'
                                    )),

    tax_rate                    DOUBLE PRECISION NOT NULL DEFAULT 0.075,

    taxable_amount              NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    tax_amount                  NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (invoice_id, tax_category)
);

CREATE INDEX IF NOT EXISTS idx_tax_cat_invoice_id     ON invoice_tax_categories(invoice_id);
CREATE INDEX IF NOT EXISTS idx_tax_cat_category       ON invoice_tax_categories(tax_category);


-- ============================================================================
-- TABLE 5: invoice_attachments (9 fields)
-- Purpose: Pointers to supplementary files. Actual bytes live in object storage.
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_attachments (
    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    attachment_type             TEXT,
    filename                    TEXT NOT NULL,
    blob_uuid                   TEXT NOT NULL,
    file_size_bytes             INTEGER,
    mime_type                   TEXT,

    uploaded_by                 TEXT,
    uploaded_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attach_invoice_id  ON invoice_attachments(invoice_id);
CREATE INDEX IF NOT EXISTS idx_attach_blob_uuid   ON invoice_attachments(blob_uuid);


-- Trigger function: maintain attachment_count on invoices (increment)
CREATE OR REPLACE FUNCTION invoices.fn_attach_count_insert()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE invoices SET attachment_count = attachment_count + 1 WHERE id = NEW.invoice_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_attach_count_insert ON invoice_attachments;
CREATE TRIGGER trg_attach_count_insert
AFTER INSERT ON invoice_attachments
FOR EACH ROW EXECUTE FUNCTION invoices.fn_attach_count_insert();


-- Trigger function: maintain attachment_count on invoices (decrement)
CREATE OR REPLACE FUNCTION invoices.fn_attach_count_delete()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE invoices SET attachment_count = GREATEST(0, attachment_count - 1) WHERE id = OLD.invoice_id;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_attach_count_delete ON invoice_attachments;
CREATE TRIGGER trg_attach_count_delete
AFTER DELETE ON invoice_attachments
FOR EACH ROW EXECUTE FUNCTION invoices.fn_attach_count_delete();


-- ============================================================================
-- TABLE 6: invoice_transmission_attempts (10 fields)
-- Purpose: Structured per-attempt telemetry for retry analytics and SLA tracking.
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_transmission_attempts (
    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    attempt_no                  INTEGER NOT NULL,

    attempted_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    result_status               TEXT NOT NULL
                                    CHECK (result_status IN (
                                        'SUCCESS',
                                        'RETRYABLE_FAILURE',
                                        'TERMINAL_FAILURE'
                                    )),

    error_category              TEXT CHECK (error_category IN (
                                    'NETWORK', 'AUTH', 'VALIDATION',
                                    'PROVIDER', 'TIMEOUT', 'UNKNOWN'
                                )),

    response_code               TEXT,

    response_message            TEXT,

    trace_id                    TEXT,

    payload_json                JSONB,

    UNIQUE (invoice_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_attempts_invoice_id    ON invoice_transmission_attempts(invoice_id);
CREATE INDEX IF NOT EXISTS idx_attempts_result        ON invoice_transmission_attempts(result_status);
CREATE INDEX IF NOT EXISTS idx_attempts_attempted_at  ON invoice_transmission_attempts(attempted_at DESC);


-- ============================================================================
-- TABLE 7: invoice_allowance_charges (10 fields)
-- Purpose: Stores FIRS allowance_charge[] entries for audit round-tripping.
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_allowance_charges (
    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    line_item_id                BIGINT REFERENCES invoice_line_items(id) ON DELETE CASCADE,

    charge_indicator            INTEGER NOT NULL DEFAULT 0,

    reason                      TEXT,

    amount                      NUMERIC(18,4) NOT NULL DEFAULT 0.0,

    base_amount                 NUMERIC(18,4),

    tax_category                TEXT,

    tax_rate                    DOUBLE PRECISION,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ac_invoice_id          ON invoice_allowance_charges(invoice_id);
CREATE INDEX IF NOT EXISTS idx_ac_line_item           ON invoice_allowance_charges(line_item_id) WHERE line_item_id IS NOT NULL;


-- ============================================================================
-- TABLE 8: invoice_history (9 fields)
-- Purpose: Field-level audit trail. One row per changed field per change event.
-- ============================================================================

CREATE TABLE IF NOT EXISTS invoice_history (
    id                          BIGSERIAL PRIMARY KEY,

    invoice_id                  BIGINT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,

    change_type                 TEXT NOT NULL,

    changed_field               TEXT,

    old_value                   TEXT,

    new_value                   TEXT,

    changed_by                  TEXT,
    changed_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_reason               TEXT
);

CREATE INDEX IF NOT EXISTS idx_hist_invoice_id    ON invoice_history(invoice_id);
CREATE INDEX IF NOT EXISTS idx_hist_changed_at    ON invoice_history(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_hist_field         ON invoice_history(changed_field) WHERE changed_field IS NOT NULL;


-- ============================================================================
-- TRIGGERS: audit log on invoices
-- ============================================================================

-- Trigger function: auto-create audit log on INSERT
CREATE OR REPLACE FUNCTION invoices.fn_audit_invoice_insert()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO invoice_history (
        invoice_id, change_type, changed_by, change_reason, new_value
    ) VALUES (
        NEW.id,
        'CREATED',
        COALESCE(NEW.helium_user_id, NEW.created_by),
        'Invoice committed',
        jsonb_build_object(
            'invoice_id',        NEW.invoice_id,
            'helium_invoice_no', NEW.helium_invoice_no,
            'invoice_number',    NEW.invoice_number,
            'direction',         NEW.direction,
            'document_type',     NEW.document_type,
            'transaction_type',  NEW.transaction_type,
            'total_amount',      NEW.total_amount,
            'workflow_status',   NEW.workflow_status,
            'seller_name',       NEW.seller_name,
            'buyer_name',        NEW.buyer_name
        )::TEXT
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_invoice_insert ON invoices;
CREATE TRIGGER trg_audit_invoice_insert
AFTER INSERT ON invoices
FOR EACH ROW EXECUTE FUNCTION invoices.fn_audit_invoice_insert();


-- Trigger function: workflow_status change tracking
CREATE OR REPLACE FUNCTION invoices.fn_audit_workflow_status()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.workflow_status IS DISTINCT FROM NEW.workflow_status THEN
        INSERT INTO invoice_history (
            invoice_id, change_type, changed_field, old_value, new_value,
            changed_by, change_reason
        ) VALUES (
            NEW.id,
            'STATUS_CHANGE',
            'workflow_status',
            OLD.workflow_status,
            NEW.workflow_status,
            COALESCE(NEW.helium_user_id, NEW.created_by),
            NULL
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_workflow_status ON invoices;
CREATE TRIGGER trg_audit_workflow_status
AFTER UPDATE ON invoices
FOR EACH ROW
WHEN (OLD.workflow_status IS DISTINCT FROM NEW.workflow_status)
EXECUTE FUNCTION invoices.fn_audit_workflow_status();


-- Trigger function: payment_status change tracking
CREATE OR REPLACE FUNCTION invoices.fn_audit_payment_status()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.payment_status IS DISTINCT FROM NEW.payment_status THEN
        INSERT INTO invoice_history (
            invoice_id, change_type, changed_field, old_value, new_value,
            changed_by, change_reason
        ) VALUES (
            NEW.id,
            'STATUS_CHANGE',
            'payment_status',
            OLD.payment_status,
            NEW.payment_status,
            COALESCE(NEW.helium_user_id, NEW.created_by),
            NULL
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_payment_status ON invoices;
CREATE TRIGGER trg_audit_payment_status
AFTER UPDATE ON invoices
FOR EACH ROW
WHEN (OLD.payment_status IS DISTINCT FROM NEW.payment_status)
EXECUTE FUNCTION invoices.fn_audit_payment_status();


-- Trigger function: inbound_status change tracking
CREATE OR REPLACE FUNCTION invoices.fn_audit_inbound_status()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.inbound_status IS DISTINCT FROM NEW.inbound_status THEN
        INSERT INTO invoice_history (
            invoice_id, change_type, changed_field, old_value, new_value,
            changed_by, change_reason
        ) VALUES (
            NEW.id,
            'STATUS_CHANGE',
            'inbound_status',
            OLD.inbound_status,
            NEW.inbound_status,
            NEW.inbound_action_by_user_id,
            NEW.inbound_action_reason
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_inbound_status ON invoices;
CREATE TRIGGER trg_audit_inbound_status
AFTER UPDATE ON invoices
FOR EACH ROW
WHEN (OLD.inbound_status IS DISTINCT FROM NEW.inbound_status)
EXECUTE FUNCTION invoices.fn_audit_inbound_status();


-- Trigger function: soft-delete tracking
CREATE OR REPLACE FUNCTION invoices.fn_audit_soft_delete()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN
        INSERT INTO invoice_history (
            invoice_id, change_type, changed_by, old_value
        ) VALUES (
            NEW.id,
            'SOFT_DELETED',
            NEW.deleted_by,
            jsonb_build_object('workflow_status', OLD.workflow_status, 'total_amount', OLD.total_amount)::TEXT
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_soft_delete ON invoices;
CREATE TRIGGER trg_audit_soft_delete
AFTER UPDATE ON invoices
FOR EACH ROW
WHEN (NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL)
EXECUTE FUNCTION invoices.fn_audit_soft_delete();


-- ============================================================================
-- VIEW: vw_invoices_summary
-- Purpose: Basic statistics for SWDB stats bar.
-- ============================================================================

CREATE OR REPLACE VIEW vw_invoices_summary AS
SELECT
    COUNT(*)                                                        AS total_count,
    SUM(CASE WHEN workflow_status = 'COMMITTED'    THEN 1 ELSE 0 END) AS committed_count,
    SUM(CASE WHEN workflow_status = 'QUEUED'       THEN 1 ELSE 0 END) AS queued_count,
    SUM(CASE WHEN workflow_status = 'TRANSMITTING' THEN 1 ELSE 0 END) AS transmitting_count,
    SUM(CASE WHEN workflow_status = 'TRANSMITTED'  THEN 1 ELSE 0 END) AS transmitted_count,
    SUM(CASE WHEN workflow_status = 'VALIDATED'    THEN 1 ELSE 0 END) AS validated_count,
    SUM(CASE WHEN workflow_status = 'ERROR'        THEN 1 ELSE 0 END) AS error_count,
    SUM(CASE WHEN workflow_status = 'ARCHIVED'     THEN 1 ELSE 0 END) AS archived_count,
    SUM(total_amount)                                               AS total_amount,
    AVG(total_amount)                                               AS average_amount,
    MIN(created_at)                                                 AS oldest_invoice_date,
    MAX(created_at)                                                 AS latest_invoice_date,
    COUNT(DISTINCT buyer_id)                                        AS unique_buyers,
    COUNT(DISTINCT seller_id)                                       AS unique_sellers
FROM invoices
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_invoice_metrics
-- Purpose: Time-windowed analytics for SWDB stats bar and report engine.
-- ============================================================================

CREATE OR REPLACE VIEW vw_invoice_metrics AS
SELECT
    -- Totals by direction
    COUNT(*)                                                            AS total_invoices,
    SUM(CASE WHEN direction = 'OUTBOUND' THEN 1 ELSE 0 END)            AS outbound_count,
    SUM(CASE WHEN direction = 'INBOUND'  THEN 1 ELSE 0 END)            AS inbound_count,

    -- Time windows
    SUM(CASE WHEN created_at >= date_trunc('week', NOW())
             THEN 1 ELSE 0 END)                                        AS created_this_week,

    SUM(CASE WHEN date_trunc('month', created_at) = date_trunc('month', NOW())
             THEN 1 ELSE 0 END)                                        AS created_this_month,

    SUM(CASE WHEN date_trunc('quarter', created_at) = date_trunc('quarter', NOW())
             THEN 1 ELSE 0 END)                                        AS created_this_quarter,

    -- Financial aggregates
    COALESCE(SUM(total_amount), 0.0)                                   AS total_gross_amount,
    COALESCE(SUM(tax_amount),   0.0)                                   AS total_tax_amount,

    COALESCE(SUM(CASE WHEN transmission_status IN ('TRANSMITTED', 'ACCEPTED')
                      THEN total_amount ELSE 0.0 END), 0.0)            AS total_transmitted_amount,

    COALESCE(SUM(CASE WHEN workflow_status = 'VALIDATED'
                      THEN total_amount ELSE 0.0 END), 0.0)            AS total_validated_amount,

    -- WHT & discount aggregates (v2.1.0.2)
    COALESCE(SUM(wht_amount), 0.0)                                     AS total_wht_amount,
    SUM(CASE WHEN wht_amount IS NOT NULL AND wht_amount > 0
             THEN 1 ELSE 0 END)                                        AS wht_invoice_count,
    COALESCE(SUM(discount_amount), 0.0)                                AS total_discount_amount,

    -- Payment health
    SUM(CASE WHEN payment_status = 'UNPAID' THEN 1 ELSE 0 END)         AS unpaid_count,
    SUM(CASE WHEN payment_status = 'PAID'   THEN 1 ELSE 0 END)         AS paid_count,
    SUM(CASE WHEN payment_status = 'PARTIAL' THEN 1 ELSE 0 END)        AS partial_count,
    SUM(CASE WHEN payment_status = 'DISPUTED' THEN 1 ELSE 0 END)       AS disputed_count,

    -- Display-derived OVERDUE count (not persisted, computed here)
    SUM(CASE WHEN payment_status = 'UNPAID'
              AND due_date IS NOT NULL
              AND due_date::DATE < CURRENT_DATE
             THEN 1 ELSE 0 END)                                        AS overdue_count,

    -- Transmission health
    SUM(CASE WHEN transmission_status IN ('FAILED_RETRYABLE', 'FAILED_TERMINAL')
             THEN 1 ELSE 0 END)                                        AS failed_transmission_count,

    CASE WHEN COUNT(*) > 0
         THEN ROUND(100.0 * SUM(CASE WHEN transmission_status IN ('FAILED_RETRYABLE', 'FAILED_TERMINAL')
                                     THEN 1 ELSE 0 END) / COUNT(*), 1)
         ELSE 0.0 END                                                  AS failure_rate_pct,

    -- Inbound pending review
    SUM(CASE WHEN direction = 'INBOUND' AND inbound_status = 'PENDING_REVIEW'
             THEN 1 ELSE 0 END)                                        AS inbound_pending_count,

    -- Inbound approaching 72h deadline (received > 48h ago, still pending)
    SUM(CASE WHEN direction = 'INBOUND'
              AND inbound_status = 'PENDING_REVIEW'
              AND inbound_received_at IS NOT NULL
              AND (inbound_received_at::TIMESTAMPTZ + INTERVAL '48 hours') < NOW()
             THEN 1 ELSE 0 END)                                        AS inbound_urgent_count

FROM invoices
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_payment_variances
-- Purpose: Track discrepancies between document due_date and calculated payment_due_date.
-- ============================================================================

CREATE OR REPLACE VIEW vw_payment_variances AS
SELECT
    id,
    invoice_id,
    helium_invoice_no,
    invoice_number,
    buyer_name,
    total_amount,
    due_date,
    payment_due_date,
    (due_date::DATE - payment_due_date::DATE) AS variance_days,
    CASE
        WHEN due_date IS NULL OR payment_due_date IS NULL THEN 'MISSING_DATE'
        WHEN due_date = payment_due_date THEN 'ALIGNED'
        WHEN due_date::DATE > payment_due_date::DATE THEN 'DOCUMENT_LATER'
        ELSE 'DOCUMENT_EARLIER'
    END AS variance_type
FROM invoices
WHERE deleted_at IS NULL
  AND (due_date IS NOT NULL OR payment_due_date IS NOT NULL);


-- ============================================================================
-- SCOPED ACCESS VIEWS (v2.1.2.0 — permission-ready field grouping)
-- ============================================================================


-- ============================================================================
-- VIEW: vw_invoice_firs
-- Purpose: 32 FIRS-mandatory fields for payload construction and audit.
-- Access:  Core (rw), Edge (r), SDK (r)
-- ============================================================================

CREATE OR REPLACE VIEW vw_invoice_firs AS
SELECT
    id,
    invoice_id,
    company_id,

    irn,

    direction,
    document_type,
    firs_invoice_type_code,
    transaction_type,

    issue_date,
    issue_time,

    document_currency_code,
    tax_currency_code,

    subtotal,
    tax_amount,
    total_amount,

    payment_means,
    firs_payment_means_code,

    seller_name,
    seller_tin,
    seller_address,
    seller_city,
    seller_state_code,
    seller_lga_code,
    seller_country_code,

    buyer_name,
    buyer_tin,
    buyer_address,
    buyer_city,

    buyer_state_code,
    buyer_lga_code,
    buyer_country_code,
    purchase_order_number,
    contract_number

FROM invoices
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_invoice_user_editable
-- Purpose: Permission-gated user-editable status fields.
-- Access:  SDK (SELECT + UPDATE payment_status, inbound_status only)
-- ============================================================================

CREATE OR REPLACE VIEW vw_invoice_user_editable AS
SELECT
    id,
    invoice_id,
    helium_invoice_no,

    payment_status,
    inbound_status,

    inbound_action_at,
    inbound_action_by_user_id,
    inbound_action_by_user_email,
    inbound_action_reason

FROM invoices
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_invoice_system
-- Purpose: System-controlled lifecycle, retry mechanics, trace chain, telemetry.
-- Access:  Core (rw), Edge (rw: transmission fields only), SDK (r)
-- ============================================================================

CREATE OR REPLACE VIEW vw_invoice_system AS
SELECT
    id,
    invoice_id,
    helium_invoice_no,

    invoice_trace_id,
    user_trace_id,
    x_trace_id,

    workflow_status,

    finalized_at,
    processing_started_at,
    processing_completed_at,
    processing_duration_ms,

    transmission_status,
    transmission_status_error,
    transmission_date,
    acknowledgement_date,

    retry_count,
    last_retry_at,
    next_retry_at,

    sign_date,
    csid,
    csid_status,
    firs_confirmation,
    firs_response_data,
    qr_code_data,

    machine_guid,
    mac_address,
    computer_name,
    float_id,
    session_id,

    created_at,
    updated_at

FROM invoices
WHERE deleted_at IS NULL;


-- ============================================================================
-- VIEW: vw_invoice_display
-- Purpose: UI-priority columns for SWDB eInvoices grid (fast grid render).
-- Access:  SDK (r)
-- ============================================================================

CREATE OR REPLACE VIEW vw_invoice_display AS
SELECT
    id,
    invoice_id,
    helium_invoice_no,
    invoice_number,
    issue_date,
    direction,
    document_type,
    transaction_type,

    -- Direction-aware counterparty
    CASE WHEN direction = 'OUTBOUND'
         THEN buyer_name
         ELSE seller_name
    END AS counterparty_name,

    workflow_status,
    transmission_status,
    payment_status,

    total_amount,
    document_currency_code,

    payment_due_date,

    inbound_status,

    attachment_count

FROM invoices
WHERE deleted_at IS NULL;


-- ============================================================================
-- POSTGRESQL ACCESS CONTROL
--
-- Execute these statements after schema creation on PostgreSQL deployments.
--
-- Roles assumed:
--   sdk_role  — Float SDK database user
--   core_role — Core service database user (full table ownership)
--   edge_role — Edge service database user (transmission contract)
-- ============================================================================

-- Revoke default PUBLIC access to base tables
-- REVOKE ALL ON invoices FROM PUBLIC;
-- REVOKE ALL ON invoice_line_items FROM PUBLIC;
-- REVOKE ALL ON invoice_references FROM PUBLIC;
-- REVOKE ALL ON invoice_tax_categories FROM PUBLIC;
-- REVOKE ALL ON invoice_attachments FROM PUBLIC;
-- REVOKE ALL ON invoice_transmission_attempts FROM PUBLIC;
-- REVOKE ALL ON invoice_allowance_charges FROM PUBLIC;
-- REVOKE ALL ON invoice_history FROM PUBLIC;

-- Revoke default PUBLIC access to views
-- REVOKE ALL ON vw_invoice_firs FROM PUBLIC;
-- REVOKE ALL ON vw_invoice_user_editable FROM PUBLIC;
-- REVOKE ALL ON vw_invoice_system FROM PUBLIC;
-- REVOKE ALL ON vw_invoice_display FROM PUBLIC;
-- REVOKE ALL ON vw_invoices_summary FROM PUBLIC;
-- REVOKE ALL ON vw_invoice_metrics FROM PUBLIC;
-- REVOKE ALL ON vw_payment_variances FROM PUBLIC;

-- Core: full ownership of all tables
-- GRANT ALL PRIVILEGES ON invoices TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_line_items TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_references TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_tax_categories TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_attachments TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_transmission_attempts TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_allowance_charges TO core_role;
-- GRANT ALL PRIVILEGES ON invoice_history TO core_role;

-- Edge: read all, write transmission fields through controlled contract
-- GRANT SELECT ON invoices TO edge_role;
-- GRANT UPDATE (transmission_status, transmission_status_error,
--               sign_date, transmission_date, acknowledgement_date,
--               csid, csid_status, firs_confirmation, firs_response_data,
--               qr_code_data, workflow_status,
--               retry_count, last_retry_at, next_retry_at,
--               updated_at) ON invoices TO edge_role;
-- GRANT SELECT ON vw_invoice_firs TO edge_role;
-- GRANT SELECT ON vw_invoice_system TO edge_role;
-- GRANT INSERT ON invoice_transmission_attempts TO edge_role;

-- SDK: read views, limited UPDATE on user-editable fields
-- GRANT SELECT ON vw_invoice_firs TO sdk_role;
-- GRANT SELECT ON vw_invoice_system TO sdk_role;
-- GRANT SELECT ON vw_invoice_display TO sdk_role;
-- GRANT SELECT ON vw_invoice_user_editable TO sdk_role;
-- GRANT SELECT ON vw_invoices_summary TO sdk_role;
-- GRANT SELECT ON vw_invoice_metrics TO sdk_role;
-- GRANT SELECT ON vw_payment_variances TO sdk_role;
-- GRANT UPDATE (payment_status, inbound_status,
--               inbound_action_at, inbound_action_by_user_id,
--               inbound_action_by_user_email, inbound_action_reason)
--   ON invoices TO sdk_role;

-- Row-level security for tenant isolation (PostgreSQL 9.5+)
-- ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_isolation ON invoices
--   USING (company_id = current_setting('app.company_id', true));
-- (Session setup: SET app.company_id = 'TENANT_XYZ';)


-- ============================================================================
-- VERSION RECORD
-- ============================================================================

INSERT INTO schema_version (version, description)
VALUES ('2.1.1.0', 'Initial PostgreSQL translation from canonical SQLite v2')
ON CONFLICT DO NOTHING;


-- ============================================================================
-- END OF SCHEMA
-- ============================================================================
