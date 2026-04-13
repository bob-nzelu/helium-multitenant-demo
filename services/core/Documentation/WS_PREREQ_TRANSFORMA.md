# WS-PREREQ: TRANSFORMA SCRIPT — What Core Needs

**Version:** 1.0
**Date:** 2026-03-18
**Status:** PREREQUISITE — Must be delivered before Core WS2 (PROCESSING) begins
**Owner:** Transforma team (separate session)
**Consumer:** Core Service (WS2 PROCESSING, WS3 ORCHESTRATOR)

---

## PURPOSE

Core is a **consumer** of the Transforma script system. Core does NOT define extraction logic — Transforma does. This document specifies exactly what Core needs from Transforma so that the Transforma build session knows what to deliver.

---

## 1. WHAT CORE EXPECTS

### 1.1 Script Executor Interface

Core will call a single function to execute a customer's transformation script:

```python
async def execute_transformation(
    script: TransformationScript,
    raw_data: RawFileData,
    enrichment_results: dict | None = None
) -> TransformationResult:
    """
    Execute a customer-specific transformation script against parsed file data.

    Args:
        script: The loaded transformation script (code + config)
        raw_data: Parsed file data (from Core's parsers)
        enrichment_results: Optional HIS/IntelliCore results (for enrich_module)

    Returns:
        TransformationResult containing 3 data classes:
        - invoices: list[TransformedInvoice]
        - customers: list[TransformedCustomer]
        - inventory: list[TransformedProduct]
        - red_flags: list[RedFlag]
        - metadata: TransformMetadata
    """
```

### 1.2 Script Storage Format

Scripts stored in `core.transformation_scripts` table:

```sql
CREATE TABLE core.transformation_scripts (
    script_id       TEXT PRIMARY KEY,           -- UUIDv7
    company_id      TEXT NOT NULL,              -- Tenant identifier
    script_name     TEXT NOT NULL,              -- Human name (e.g., "PikWik Till Report Transformer")
    script_version  TEXT NOT NULL DEFAULT '1.0',

    -- Module code (Python source as TEXT)
    extract_module  TEXT,                       -- Extraction logic (raw → structured)
    validate_module TEXT,                       -- Pre-validation (before enrichment)
    format_module   TEXT,                       -- FIRS compliance formatting
    enrich_module   TEXT,                       -- Data enrichment hooks

    -- Configuration (JSON)
    customer_profile TEXT NOT NULL,             -- CustomerProfileConfig as JSON

    -- Metadata
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    created_by      TEXT
);

CREATE INDEX idx_transform_scripts_company ON core.transformation_scripts(company_id, is_active);
```

### 1.3 CustomerProfileConfig (from config_mapping.py)

Core expects this JSON config per customer:

```json
{
    "supplier_identity": {
        "name": "PikWik Stores Ltd",
        "tin": "12345678-001",
        "email": "accounts@pikwik.ng",
        "address": "15 Broad Street, Lagos",
        "region": "Lagos"
    },
    "column_mapping": {
        "mapping_kind": "column",
        "journal_fields": {"invoice_number": "INV_NO", "date": "TRX_DATE", ...},
        "customer_fields": {"name": "CUST_NAME", "tin": "CUST_TIN", ...},
        "lookup_fields": {"category": "ACCT_CODE", ...}
    },
    "invoice_type_mapping": {
        "type_map": {"INV": "381", "CRN": "380", "DBN": "384"},
        "default_type_code": "381"
    },
    "acct_code_to_hsn": {
        "acct_hsn_map": {"4100": "1905.90", "4200": "2201.10"},
        "default_hsn": "9999.99"
    },
    "tax_rules": {
        "default_vat_rate": 7.5,
        "default_wht_rate": 0.0,
        "override_rules": [],
        "wht_affects_payable": true
    },
    "file_layout": {
        "input_formats": ["excel"],
        "journal_required": true,
        "customer_optional": true,
        "journal_sheet_hints": ["Sales", "Journal"],
        "customer_sheet_hints": ["Customers", "Master"]
    },
    "quota": {
        "max_files_per_run": 10,
        "max_invoices_per_run": 5000,
        "max_total_mb_per_run": 50.0
    },
    "b2b_config": {
        "b2b_candidates": ["customer_tin"],
        "b2b_classification_rules": [],
        "downgrade_label": "B2B_CANDIDATE_DOWNGRADED_TO_B2C"
    }
}
```

### 1.4 Module Contracts

Each module is a Python source code string that defines specific functions:

#### extract_module
```python
def extract(raw_data: dict, config: CustomerProfileConfig) -> ExtractionResult:
    """
    Extract 3 data classes from raw parsed file data.

    Must return:
        ExtractionResult(
            invoices=[...],    # list of raw invoice dicts
            customers=[...],   # list of raw customer dicts
            inventory=[...],   # list of raw product dicts
            metadata={...}     # extraction stats
        )
    """
```

#### validate_module
```python
def validate(data: ExtractionResult, config: CustomerProfileConfig) -> ValidationResult:
    """
    Pre-validate extracted data before enrichment.

    Must return:
        ValidationResult(
            valid_invoices=[...],
            invalid_invoices=[...],
            red_flags=[...],
            stats={...}
        )
    """
```

#### format_module
```python
def format_firs(data: ValidationResult, config: CustomerProfileConfig) -> FormattedResult:
    """
    Format data into FIRS-compliant structure.
    Maps ERP codes to FIRS codes, applies party model (seller/buyer),
    formats dates, amounts, tax categories.

    Must return data matching domain.model.Invoice structure.
    """
```

#### enrich_module
```python
def enrich(data: FormattedResult, enrichment_results: dict, config: CustomerProfileConfig) -> EnrichedResult:
    """
    Apply enrichment results from HIS/IntelliCore to the formatted data.
    Maps HSN codes, VAT treatments, address corrections.

    enrichment_results comes from Core's calls to HIS POST /enrich/batch.
    """
```

### 1.5 The 3 Data Classes Output

Every transformation MUST produce 3 data classes, regardless of input file type:

#### Invoices
```python
@dataclass
class TransformedInvoice:
    # Maps to canonical invoice schema (invoices table, 115 fields)
    invoice_number: str
    direction: str          # OUTBOUND | INBOUND
    document_type: str      # COMMERCIAL_INVOICE | CREDIT_NOTE | DEBIT_NOTE | ...
    transaction_type: str   # B2B | B2G | B2C
    firs_invoice_type_code: str  # 380 | 381 | 383 | 389 | 261
    issue_date: str
    due_date: str | None
    seller: PartyData       # Snapshot of seller details
    buyer: PartyData        # Snapshot of buyer details
    currency: str           # Default NGN
    subtotal: float
    tax_amount: float
    total_amount: float
    line_items: list[LineItemData]
    tax_categories: list[TaxCategoryData]
    allowance_charges: list[AllowanceChargeData]
    raw_data: dict          # Full extracted data for audit
```

#### Customers
```python
@dataclass
class TransformedCustomer:
    # Maps to canonical customer schema (customers table, 54 fields)
    company_name: str
    tin: str | None
    rc_number: str | None
    tax_id: str | None
    primary_identifier: str  # TIN | RC_NUMBER
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    state: str | None
    customer_type: str | None  # B2B | B2G
    tax_classification: str | None  # STANDARD | EXEMPT
    source: str             # 'invoice_extraction' | 'manual' | 'bulk_import'
```

#### Inventory
```python
@dataclass
class TransformedProduct:
    # Maps to canonical inventory schema (inventory table, 35 fields)
    product_name: str
    description: str | None
    type: str               # GOODS | SERVICE
    hsn_code: str | None    # For GOODS (XXXX.XX format)
    service_code: str | None  # For SERVICE
    product_category: str | None
    service_category: str | None
    unit_of_measure: str | None
    vat_treatment: str      # STANDARD | ZERO_RATED | EXEMPT
    vat_rate: float         # Default 7.5
    source: str             # 'invoice_extraction' | 'manual' | 'bulk_import'
```

---

## 2. WHAT CORE DOES NOT BUILD

- Core does NOT build the script executor — Transforma provides it as a library
- Core does NOT define extraction logic — each customer's extract_module does
- Core does NOT store scripts — Transforma provides a CLI/API to upload scripts to core.transformation_scripts
- Core does NOT validate script safety at runtime — Transforma validates via AST at upload time

---

## 3. WHAT TRANSFORMA MUST DELIVER

| Deliverable | Description | Priority |
|------------|-------------|----------|
| `transforma` Python package | Installable library with executor, config loader, AST validator | P0 |
| `execute_transformation()` function | Main entry point matching interface in §1.1 | P0 |
| 4 module contracts | extract, validate, format, enrich with clear signatures | P0 |
| `CustomerProfileConfig` dataclass | From config_mapping.py, with JSON serialization | P0 |
| Domain model classes | TransformedInvoice, TransformedCustomer, TransformedProduct | P0 |
| AST validator | Block dangerous operations (os, subprocess, eval, open, __import__) | P0 |
| CLI tool | Upload/manage scripts in core.transformation_scripts table | P1 |
| PikWik transformation script | First real customer script (extract Till Report → 3 data classes) | P1 |
| Default transformation script | Generic fallback for customers without custom scripts | P1 |

---

## 4. INTEGRATION POINT

Core integrates Transforma at Phase 3 (TRANSFORM) of the pipeline:

```
Phase 1: FETCH (blob from HeartBeat)
Phase 2: PARSE (raw file → structured data)
    ↓
Phase 3: TRANSFORM ← Transforma script executed here
    ↓
Phase 4: ENRICH (HIS enrichment applied via enrich_module)
Phase 5: RESOLVE (entity matching)
Phase 6: PORTO BELLO (business logic gate)
Phase 7: BRANCH (preview generation)
Phase 8: FINALIZE (record creation)
```

---

## 5. TIMELINE DEPENDENCY

```
Transforma build session ──→ Core WS2 (PROCESSING) can begin
                             Core WS3 (ORCHESTRATOR) can begin
```

Core WS0 (FOUNDATION) and WS1 (INGESTION) can proceed without Transforma.
Core WS4 (ENTITY CRUD) and WS6 (OBSERVABILITY) can proceed without Transforma.

Only WS2 and WS3 are blocked on Transforma delivery.

---

## 6. TRANSFORMA DELIVERY STATUS (Updated 2026-03-25)

**Transforma v2.0.1 is DELIVERED.** Located at `Helium/Services/Transforma/`.

### How Core imports Transforma

```python
from transforma import execute_transformation
from transforma.callbacks import (
    IRNChecker, FileHashChecker, CustomerLookup,
    InventoryLookup, HISLookup, EventEmitter,
)
from transforma.config import TenantConfig
from transforma.models import TransformationResult
```

Add to Core's `pyproject.toml`:
```toml
dependencies = [
    "transforma @ file:///${PROJECT_ROOT}/../Transforma",
]
```

### IQC (IRN/QR/CSID) is NOT in Transforma

IQC functions live in `helium_formats.iqc` (shared with Relay):

```python
from helium_formats.iqc import generate_irn, compute_irn_hash   # IRN
from helium_formats.iqc import generate_qr_data                  # QR
from helium_formats.iqc import generate_csid_request             # CSID
```

Core uses BOTH: `transforma` for transformation + `helium_formats.iqc` for IQC.

### Script Category Access Control

HeartBeat enforces access:
- `script_category="TRANSFORMA"` → Core only (403 for Relay)
- `script_category="IQC"` → Core + Relay (shared)

Core must validate SHA-256 hash on TRANSFORMA scripts. No hash needed for IQC.

### EventEmitter Callback

Transforma emits `processing.log` and `processing.progress` events. Core must:
1. Implement `EventEmitter` protocol from `transforma.callbacks`
2. Relay events to Core's SSE endpoint for ReviewPage live updates

---

**Last Updated:** 2026-03-25
