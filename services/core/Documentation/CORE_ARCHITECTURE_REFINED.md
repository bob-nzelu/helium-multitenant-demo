# CORE SERVICE - REFINED ARCHITECTURE (Your Vision)

**Date:** 2026-02-01
**Status:** Clarified Architecture Based on Actual Requirements
**Based On:** Your feedback - NOT my assumptions

---

## CORE SERVICE MISSION (Refined)

Transform raw files into FIRS-compliant invoices while:
1. **Extracting structured data** from multiple formats (Excel, PDF, CSV, XML, JSON)
2. **Validating accuracy** using both Textract ML intelligence and Core's built-in business logic
3. **Providing preview + edit flow** where users can review and correct before finalization
4. **Managing three master databases** (invoices, customers, inventory)
5. **Broadcasting real-time progress** via WebSocket to Float UI

---

## KEY ARCHITECTURAL PRINCIPLES

### **Principle 1: Multi-Layer Intelligence**

**Layer 1: Textract ML Intelligence (External)**
- Extract form fields from PDFs
- Parse table structure
- Provide confidence scores
- Classify documents
- Cost: $1.50 per page (only for PDFs)

**Layer 2: Core's Built-in Intelligence (Internal)**
- Business rule validation (amounts total, tax is correct)
- Customer/inventory master data matching
- FIRS compliance formatting
- Red flag classification

**Both layers work together:**
```
Textract: "Found Invoice #INV-001, Amount $5000, Confidence 92%"
  ↓
Core Layer 1 Validation: "Amount looks suspicious (100x usual), Flag for review"
  ↓
Core Layer 2 Processing: "Customer TIN incomplete, Can't finalize yet"
  ↓
Result: Preview returned to user for edits
```

---

### **Principle 2: Preview + Finalize Flow**

**NOT:** Try to complete everything in one pass

**Instead:** Two explicit stages:

```
STAGE 1: PREVIEW (Extract + Validate + Return Preview)
  ├─ Phase 2: PARSE (Extract with Textract intelligence)
  ├─ Phase 3: TRANSFORM (Apply Core business logic)
  ├─ Phase 4: ENRICH (Add master data)
  ├─ Phase 5: RESOLVE (Link to customers/inventory)
  ├─ Phase 7: BRANCH (Generate preview data)
  └─ Return to user: "Here's what we found. Anything need fixing?"

STAGE 2: FINALIZE (Create Records + Queue to Edge)
  ├─ User provides optional edits
  ├─ Phase 8: FINALIZE (Apply edits + create database records)
  ├─ Queue to Edge for FIRS submission
  └─ Return to user: "Done! Queued to FIRS"
```

This is NOT a bug. This is the intended design.

---

### **Principle 3: Confidence Thresholds (Not One-Size-Fits-All)**

Different confidence thresholds for different decision points:

```
Textract Confidence (from PDF extraction):
├─ >= 95%: "Likely accurate, proceed with caution"
├─ 90-95%: "Possibly accurate, flag for review"
└─ < 90%: "Low confidence, requires user confirmation"

Core Validation Confidence (business rules):
├─ All amounts verify: "Ready to finalize"
├─ Some red flags: "Flag for user review before finalize"
└─ Critical issues: "Cannot finalize, requires manual correction"

Combined Decision:
├─ Textract >= 95% AND All Core validations pass
│   └─ Return preview, user can finalize immediately
├─ Textract 90-95% OR Some Core flags
│   └─ Return preview WITH FLAGS, require user review before finalize
└─ Textract < 90% OR Critical Core issues
│   └─ Return preview WITH WARNINGS, user MUST edit and re-submit before finalize
```

---

## 8-PHASE PIPELINE (Refined)

### **PHASE 1: FETCH (HAIKU)**

```
Input: core_queue entry (queue_id, blob_path)
Output: Raw file bytes + metadata

Process:
1. Read core_queue entry
2. Fetch file from blob storage
3. Return file bytes + metadata
```

---

### **PHASE 2: PARSE (HAIKU) - Extract + Validate in One Step**

```
Input: Raw file bytes + file type
Output: Extracted structured data + confidence scores + validation status

Process for Excel files:
1. Use openpyxl to extract rows
2. Structure into invoice/customer/inventory format
3. Deduplication check (SHA256)
4. Confidence: 99% (structured data is reliable)
5. Return extracted data

Process for PDF files:
1. Call AWS Textract (ML intelligence)
   ├─ FeatureTypes: ['TABLES', 'FORMS']
   ├─ Extract: Form fields, table structure, confidence scores
   └─ Cost: $1.50 per page
2. VALIDATE IN THIS PHASE:
   ├─ Required fields present? (invoice #, date, amount)
   ├─ Amounts total correctly? (line items + tax = total)
   ├─ Tax rate reasonable? (compare to expected)
   ├─ Confidence scores acceptable? (>= 90% threshold)
   └─ Document looks authentic? (visual markers)
3. Deduplication check (SHA256)
4. Return: extracted data + confidence + validation status

Process for CSV/XML/JSON:
1. Use appropriate parser (pandas, lxml, json)
2. Structure into standard format
3. Deduplication check
4. Confidence: 95-98%
5. Return extracted data

Validation Output (attached to extracted data):
{
  "extracted_data": {...},
  "confidence": 0.92,
  "validation_status": "accepted" | "accepted_with_flags" | "rejected",
  "red_flags": [
    {
      "type": "low_ocr_confidence",
      "invoice_id": "INV_001",
      "severity": "warning",
      "message": "OCR confidence is 92%. Human review recommended.",
      "from_textract": true
    },
    {
      "type": "amount_mismatch",
      "invoice_id": "INV_001",
      "severity": "error",
      "message": "Line items total $4900 but document says $5000",
      "from_core": true
    }
  ]
}
```

---

### **PHASE 3: TRANSFORM (SONNET)**

```
Input: Extracted data + validation status + red_flags
Output: FIRS-compliant structured data + additional Core intelligence

Process:
1. Load customer-specific transformation script
2. Access shared modules (future roadmap):
   ├─ extract_module (already happened in Phase 2)
   ├─ validate_module (already happened in Phase 2)
   ├─ format_module (FIRS compliance)
   └─ enrich_module (data enrichment)
3. Apply business logic:
   ├─ Customer-specific rules (Pikwik vs Aramex vs others)
   ├─ Stream detection (Till report vs B2B vs Rebate)
   ├─ Amount normalization
   ├─ Payment terms (due dates, status)
   └─ Document classification refinement
4. Add Core intelligence to red_flags:
   ├─ Business rule violations
   ├─ Data quality issues
   ├─ Missing mandatory fields for this customer
   └─ Suspect patterns (duplicate invoice #, unusual amounts)
5. Return: FIRS-compliant data + enhanced red_flags
```

---

### **PHASE 4: ENRICH (SONNET)**

```
Input: FIRS-compliant data + red_flags
Output: FIRS-compliant data + enrichment metadata + updated red_flags

Process:
1. Call Prodeus certified APIs (parallel):
   ├─ HSN code lookup
   ├─ Product category classification
   ├─ Postal code validation
   └─ AI enrichment
2. Add enrichment quality to red_flags:
   ├─ If HSN not found: red_flag (type: missing_hsn_code, severity: error)
   ├─ If address invalid: red_flag (type: invalid_address, severity: warning)
   └─ If confidence low: red_flag (type: enrichment_uncertain, severity: warning)
3. Graceful degradation:
   ├─ If Textract API fails: Continue with what we have, flag as incomplete
   ├─ If Prodeus API fails: Continue without enrichment, flag as missing
   └─ Circuit breaker: After 5 failures, mark as "enrichment unavailable"
4. Return: Enriched data + red_flags
```

---

### **PHASE 5: RESOLVE (SONNET)**

```
Input: Enriched data + red_flags
Output: Data linked to master data (customers, inventory) + updated red_flags

Process:
1. Customer resolution:
   ├─ Match by TIN (exact)
   ├─ Match by name (fuzzy if TIN not found)
   ├─ Create new customer if no match
   ├─ Add red_flag if TIN mismatch detected
   └─ Add red_flag if customer marked "incomplete" (Porto Bello scenario)

2. Inventory resolution:
   ├─ Match by SKU (exact)
   ├─ Match by product name (fuzzy if SKU not found)
   ├─ Create new inventory item if no match
   └─ Add red_flag if product details ambiguous

3. Return: Data with customer_id + inventory_ids + red_flags
```

---

### **PHASE 6: PORTO BELLO (OPUS)**

```
Input: Resolved data + red_flags
Output: Invoice with Porto Bello status flag

Process:
1. Check customer config: portoBello_enabled?
2. If yes:
   ├─ Check if customer details complete (TIN + address + email)
   ├─ If complete: Proceed normally
   ├─ If incomplete: Mark as "pending_counterparty_details"
   │   └─ Add red_flag (type: pending_counterparty, severity: warning)
   └─ Set queue to Edge: SIGN only (not TRANSMIT)
3. If no: Proceed normally
4. Return: Invoice with status flag + red_flags
```

---

### **PHASE 7: BRANCH (OPUS)**

```
Input: Invoice data + red_flags
Output: Preview data OR continue to finalize

Process:
1. Check: immediate_processing flag?

2. If false (PREVIEW MODE):
   ├─ Generate 6 preview files:
   │   ├─ firs_invoices.json (FIRS-compliant invoice data)
   │   ├─ report.json (statistics + red_flags)
   │   ├─ customers.json (extracted customer data)
   │   ├─ inventory.json (extracted inventory data)
   │   ├─ failed_invoices.xlsx (list of invoices with red_flags)
   │   └─ fixed.pdf (original + visual markers highlighting issues)
   ├─ Append preview files to blob (7-day retention)
   ├─ Update core_queue: status="preview_ready"
   ├─ Emit WebSocket: "Preview ready. User review required before finalize."
   └─ STOP here - wait for user to review and optionally edit

3. If true (IMMEDIATE MODE):
   ├─ Check red_flags severity
   ├─ If critical flags exist: Emit warning, may not auto-finalize
   └─ Continue to Phase 8 (FINALIZE)

4. Emit WebSocket progress events throughout:
   ├─ "Parsing invoices (45/150)"
   ├─ "Transforming data..."
   ├─ "Enriching with Prodeus APIs..."
   ├─ "Generating preview (67% complete)"
   └─ "Preview ready"
```

---

### **PHASE 8: FINALIZE (OPUS)**

```
Input: Queue_id + user edits (optional)
Output: Database records + Edge queue entries + WebSocket broadcast

Process:
1. Fetch semi-processed data from Phase 7
2. Apply user edits (if provided):
   ├─ invoice_edits: Update fields
   ├─ customer_edits: Update customer data
   ├─ inventory_edits: Update inventory data
   └─ Re-validate after each edit

3. Re-check red_flags after edits:
   ├─ If critical flags still present AND confidence below threshold:
   │   └─ Return error: "Cannot finalize. Issues must be resolved."
   ├─ If flags resolved:
   │   └─ Proceed to create records
   └─ If minor flags remain:
       └─ Proceed but note in audit log

4. Generate IRN + QR code (if not already done)

5. Create database records:
   ├─ Insert into invoices.db
   ├─ Insert/update customers.db
   ├─ Insert/update inventory.db
   ├─ Insert into notifications.db (if approval needed)
   └─ Record in processed_files (deduplication)

6. Queue to Edge:
   ├─ Determine task type:
   │   ├─ If portoBello: SIGN only
   │   └─ If normal: SIGN_AND_TRANSMIT
   └─ Create edge_queue entry

7. Call Edge API:
   └─ Notify Edge to process queued invoices

8. Schedule 24-hour cleanup:
   ├─ Use APScheduler
   ├─ Schedule deletion of core_queue entry
   └─ Keep entry visible for HeartBeat reconciliation for 24h

9. Broadcast WebSocket events:
   ├─ invoice.created
   ├─ customer.created/updated
   ├─ inventory.created/updated
   └─ notification.created (if approval request)

10. Update core_queue:
    ├─ status="processed"
    ├─ processed_at=now()
    └─ updated_at=now()

11. Log audit events:
    ├─ core.processing.completed
    ├─ core.finalization.completed
    ├─ Include red_flags that were resolved by user
    └─ Include edits that were applied
```

---

## RED FLAGS TAXONOMY (Complete)

### **Red Flags Generated in Each Phase:**

**Phase 2 (PARSE) - Textract Intelligence Flags:**
- `low_ocr_confidence` - Textract confidence < 95%
- `missing_required_field` - Invoice #, date, or amount not found
- `amount_mismatch` - Line items don't add up to stated total
- `tax_calculation_error` - Tax doesn't match expected rate
- `unusual_document_type` - Document type unclear (not a standard invoice)

**Phase 3 (TRANSFORM) - Core Business Logic Flags:**
- `duplicate_invoice_number` - Invoice # already in system
- `suspicious_amount` - Amount 10x+ usual for this customer
- `unsupported_currency` - Currency not NGN
- `invalid_date_format` - Date can't be parsed
- `customer_risk_profile` - Customer flagged as high-risk

**Phase 4 (ENRICH) - API Integration Flags:**
- `missing_hsn_code` - HSN mapping failed
- `invalid_address` - Postal code validation failed
- `enrichment_uncertain` - Low confidence on enrichment APIs

**Phase 5 (RESOLVE) - Master Data Flags:**
- `customer_tin_mismatch` - TIN doesn't match existing customer
- `customer_incomplete` - Customer missing required details
- `ambiguous_product_match` - Product name matches multiple SKUs

**Phase 6 (PORTO BELLO) - Business Logic Flags:**
- `pending_counterparty_details` - Waiting for customer to complete details
- `awaiting_approval` - Requires supervisor/manager approval

**Phase 7 (BRANCH) - Preview Generation Flags:**
- (Inherited from previous phases, no new flags)

**Phase 8 (FINALIZE) - Resolution Flags:**
- (User edits may resolve previous flags, noted in audit log)

---

## CONFIDENCE THRESHOLDS & DECISION LOGIC

### **When to Return Preview (Not Finalize Immediately):**

```
IF immediate_processing == false:
  ALWAYS return preview (this is the whole point of preview mode)

IF immediate_processing == true:
  Check severity of red_flags:

  IF critical_flags exist (severity: error) AND confidence < 90%:
    → Return preview with flags (do NOT auto-finalize)
    → User MUST review and fix before finalize

  ELIF critical_flags exist (severity: error) AND confidence >= 90%:
    → Return preview with flags (optional finalize)
    → User can finalize if they accept the risk

  ELIF only warning_flags exist:
    → Return preview with warnings
    → User can finalize immediately if desired

  ELIF no flags:
    → Auto-finalize (proceed immediately)
    → User receives confirmation
```

### **What "Critical" Flags Are:**

```
CRITICAL (severity: error):
├─ Textract confidence < 85%
├─ Amount mismatch (>= 1% difference)
├─ Tax calculation error
├─ Missing required field
├─ Duplicate invoice number
├─ Customer incomplete (Porto Bello case)
└─ Invalid address (can't validate)

WARNING (severity: warning):
├─ Textract confidence 85-95%
├─ Suspicious amount (but plausible)
├─ Customer TIN uncertain (fuzzy match)
├─ Enrichment low confidence
└─ Document type unclear
```

---

## FLOW EXAMPLES

### **Example 1: Pikwik Excel Export (High Confidence)**

```
User uploads: Daily Till Report (Excel)
  ↓
Phase 2 (PARSE):
├─ openpyxl extracts rows
├─ Dedup check: Not duplicate
├─ Validation: All amounts verify ✓
├─ Confidence: 99%
├─ Red flags: NONE
  ↓
Phase 3-7: Processing continues, no flags added
  ↓
Phase 7 (BRANCH):
├─ immediate_processing=false (preview mode)
├─ Preview generated
├─ WebSocket: "Preview ready"
  ↓
User views preview:
├─ Sees: 150 invoices, 0 errors
├─ No edits needed
├─ Clicks: "Finalize"
  ↓
Phase 8 (FINALIZE):
├─ Creates database records (150 invoices)
├─ Queues to Edge (SIGN_AND_TRANSMIT)
├─ WebSocket: "Done! 150 invoices queued to FIRS"
```

---

### **Example 2: Vendor PDF with Textract Uncertainty**

```
User uploads: Vendor Invoice PDF
  ↓
Phase 2 (PARSE):
├─ AWS Textract extracts form fields
├─ Textract confidence: 88%
├─ Validation: Amounts verify, but confidence low
├─ Red flag added: {type: low_ocr_confidence, severity: warning}
  ↓
Phase 3-5: Processing continues
  ↓
Phase 7 (BRANCH):
├─ immediate_processing=false (preview mode)
├─ Preview generated
├─ WebSocket: "Preview ready (with 1 warning)"
  ↓
User views preview:
├─ Sees: Invoice from Vendor X, Amount $5000
├─ Sees: "OCR confidence 88% - Recommend review"
├─ Can view: Original PDF with highlights
├─ Options:
│   ├─ Accept as-is (click Finalize)
│   ├─ Edit amount (click Edit, enter $5000)
│   └─ Reject and re-upload (discard)
  ↓
User clicks: "Finalize" (accepts with warning noted)
  ↓
Phase 8 (FINALIZE):
├─ Red flag resolved (user accepted risk)
├─ Creates database record
├─ Audit log: "Finalized with OCR confidence warning 88%"
├─ Queues to Edge
├─ WebSocket: "Done! Queued to FIRS"
```

---

### **Example 3: PDF with Critical Issues (Can't Finalize)**

```
User uploads: Scanned PDF (poor quality)
  ↓
Phase 2 (PARSE):
├─ AWS Textract extracts data
├─ Textract confidence: 72%
├─ Amount extraction uncertain
├─ Red flags:
│   ├─ {type: low_ocr_confidence, severity: error, confidence: 72%}
│   ├─ {type: amount_uncertain, severity: error}
│   └─ {type: document_unclear, severity: error}
  ↓
Phase 7 (BRANCH):
├─ Preview generated with RED FLAGS
├─ WebSocket: "Preview ready (3 critical issues)"
  ↓
User views preview:
├─ Sees: PDF marked with red highlights
├─ Sees: "This document has critical OCR errors"
├─ Sees: "Invoice amount is uncertain: could be $5000 or $50,000"
├─ Options:
│   ├─ Edit fields manually (Click Edit, enter correct values)
│   └─ Reject and re-upload (discard)
  ↓
User clicks: "Edit Amount: $5000"
  ↓
Phase 8 (FINALIZE):
├─ Red flags re-checked
├─ After edit: Confidence increases
├─ Proceeds to create records
├─ Audit log: "User corrected OCR uncertainty. Finalized with edits."
```

---

## API ENDPOINTS (18 Total)

### **Processing Flow:**

1. `POST /api/v1/enqueue` - Queue file (returns immediately)
2. `GET /api/v1/queue/{queue_id}/status` - Poll status + progress
3. `POST /api/v1/finalize` - Apply edits + finalize (after preview)

### **Real-Time Updates:**

4. `WS /api/v1/events` - WebSocket for progress events

### **Entity Operations:**

5. `PUT /api/v1/entity/{type}/{id}` - Update (invoice, customer, inventory)
6. `DELETE /api/v1/entity/{type}/{id}` - Delete (soft delete)

### **Generic Update:**

7. `POST /api/v1/update` - Update from Edge or SDK

### **B2B Invoice Management:**

8. `POST /api/v1/invoice/{id}/accept` - Accept inbound B2B invoice
9. `POST /api/v1/invoice/{id}/reject` - Reject inbound B2B invoice

### **Retry/Retransmit:**

10. `POST /api/v1/retry` - Retry FIRS submission (SIGN_AND_TRANSMIT)
11. `POST /api/v1/retransmit` - Retransmit signed invoice (TRANSMIT only)

### **SDK Data Access (WS1):**

12. `GET /api/v1/invoice/{id}` - Fetch single invoice
13. `GET /api/v1/invoices` - List invoices

### **SDK Search (WS2):**

14. `POST /api/v1/search` - Full-text search

### **Notifications (Core-owned):**

15. `POST /api/v1/notifications` - Create notification (internal)
16. `GET /api/v1/notifications` - List notifications

### **Monitoring:**

17. `GET /api/v1/core_queue/status` - Queue status for HeartBeat
18. `GET /api/v1/health` - Health check

---

## NOTIFICATIONS DATABASE (Core-owned)

**Who writes to it:** Core Service (directly via API)

**When it gets written:**
- Approval request needed (red_flags critical)
- Processing complete
- Invoice rejected (critical issues)
- Customer details completed (Porto Bello notification)

**Who reads it:** Float SDK (via `/api/v1/notifications` + WebSocket)

**What it contains:**
```json
{
  "notification_id": "notif_123",
  "type": "approval_request" | "processing_complete" | "error_alert",
  "severity": "info" | "warning" | "error",
  "invoice_id": "INV_001",
  "message": "Invoice needs approval. OCR confidence is 88%.",
  "requires_action": true,
  "target_user_id": "supervisor_123",
  "created_at": "2026-02-01T10:00:00Z",
  "read": false,
  "read_at": null
}
```

---

## SUMMARY: YOUR ARCHITECTURE vs MY ASSUMPTIONS

| Aspect | My Assumption | Your Architecture | Resolution |
|--------|---------------|-------------------|-----------|
| **Textract** | Just text extraction | ML intelligence + validation | Phase 2 validates with Textract in one step |
| **Phase 2 Output** | Raw data only | Raw data + confidence + flags | Added validation to Phase 2 |
| **Low Confidence** | Always block | Depends on threshold | Different thresholds per severity |
| **Preview Mode** | Optional feature | MANDATORY for review flow | Always return preview, user decides finalize |
| **Notifications** | HeartBeat-owned queue | Core-owned direct API | Core writes directly to notifications.db |
| **Transformation** | Single phase | Multi-layer (Textract + Core) | Textract in Phase 2, Core logic in Phase 3 |
| **Red Flags** | Generic severity | Threshold-based decisions | Critical vs warning, affects finalize logic |
| **Endpoints** | `/process_preview` blocking | `/enqueue` + polling + WebSocket | Removed blocking, added real-time progress |

---

**Status:** Refined Architecture Documented
**Next:** Ready for Phase-specific documentation and variant assignments

---

**Document Version:** 1.0
**Created:** 2026-02-01
**Status:** YOUR VISION CAPTURED
