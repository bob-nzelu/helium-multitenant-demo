# PRACTICAL UX & ARCHITECTURE ANALYSIS

**Date:** 2026-02-01
**Status:** Analyzing practical user experience implications
**Based On:** pikwik-transforma-v1.0.py + your feedback

---

## 1. BETTER ALTERNATIVE: POST /api/v1/process_preview ANALYSIS

### Your Concern: "How does this fit practically with User Experience?"

Let me trace the user flow:

#### **SCENARIO: Relay Bulk Upload Flow (Practical Example)**

```
User (in Float UI)
  ↓
1. Upload CSV file (150 invoices)
  ↓
Relay Service
  ├─ Write to blob storage ✅
  ├─ Write to core_queue ✅
  └─ Call Core API...
      ├─ OPTION A (Current spec - /process_preview blocking):
      │   POST /api/v1/process_preview
      │   ├─ Relay waits up to 300 seconds
      │   ├─ If < 300s: Returns 200 OK with preview data
      │   └─ If > 300s: Returns 202 Accepted (user sees "queued")
      │
      └─ OPTION B (Your preferred - polling):
          POST /api/v1/enqueue
          ├─ Returns immediately with queue_id
          └─ Relay polls GET /api/v1/queue/{queue_id}/status
             ├─ Status: "queued"
             ├─ Status: "processing" (% complete)
             └─ Status: "processed" (returns preview data)
```

### The Real Problem with Both Approaches

**Looking at pikwik-transforma-v1.0.py:**
- 150 invoices from Till reports + B2B + Rebates
- Needs to process THREE STREAMS simultaneously
- Execution involves:
  - Data enrichment (customer TIN lookups)
  - Compliance scoring (batch_compliance_scoring.py)
  - Excel generation
  - Email dispatch (role-based templates)
  - Websocket events for progress tracking

**Time taken:** Likely 30-120 seconds for 150 invoices (depends on enrichment)

**Both blocking and polling fail the UX:**

#### **Option A (Blocking /process_preview):**
```
User waits...
├─ If 30s: Gets preview immediately ✓ Good UX
├─ If 90s: Waits 90s for response (bad UX, "is it frozen?")
└─ If 300s+: Times out, gets "queued" status (confusing - they thought they submitted)
```

#### **Option B (Polling):**
```
User waits...
├─ Relay polls every 2 seconds
├─ Shows progress bar ("Processing: 45% complete")
├─ User sees streaming updates
└─ Preview shown when ready
```

**Polling is MUCH BETTER UX** because:
1. ✅ Shows progress in real-time (via WebSocket or polling)
2. ✅ No timeout confusion
3. ✅ User never left hanging wondering if it worked
4. ✅ Can handle arbitrarily large batches

### **My Recommendation: HYBRID APPROACH**

**Don't use either pure blocking or pure polling. Instead:**

```
POST /api/v1/enqueue
  ├─ Response: queue_id, status="queued" (immediate)
  └─ Core starts processing in background

GET /api/v1/queue/{queue_id}/status
  ├─ Response: status="processing"
  ├─ Includes: % complete, current_phase, eta_seconds
  └─ Core emits WebSocket events as progress happens

Float UI gets:
  ├─ WebSocket events in real-time: "Parsing invoices (45/150)"
  ├─ Progress bar updates live
  ├─ No blocking, no polling, no timeouts
  └─ Preview shown when ready
```

**Practical flow (pikwik example):**
```
User uploads 150 invoices
  ↓
POST /api/v1/enqueue → queue_id="queue_123" (returns immediately)
  ↓
Core starts processing (background task)
  ├─ FETCH: 1 second
  ├─ PARSE: 5 seconds (emit: "Parsing: 30/150 invoices")
  ├─ TRANSFORM: 15 seconds (emit: "Transforming: 120/150")
  ├─ ENRICH: 20 seconds (emit: "Enriching: checking HSN codes...")
  ├─ RESOLVE: 10 seconds
  ├─ BRANCH: Generate preview (emit: "Preview ready")
  └─ Total: ~51 seconds

Float UI (real-time):
  ├─ Shows progress: "Parsing (30/150)"
  ├─ Shows progress: "Transforming (120/150)"
  ├─ Shows progress: "Enriching..."
  ├─ Shows preview when ready
  └─ User happy (never blocked, always informed)
```

---

## 2. AWS TEXTRACT QUESTION: "Doesn't Textract handle text-based PDFs?"

### The Answer: YES, but with caveats

**From AWS documentation:**
- Textract CAN process text-based PDFs
- BUT it's overkill and expensive
- Better: Use pdfplumber for text PDFs, Textract only for scanned

**Cost comparison (per 1000 pages):**
- pdfplumber (text PDF): Free (local processing)
- AWS Textract (scanned PDF): $1.50 per page = $1,500 for 1000 pages
- AWS Textract (text PDF): $1.50 per page = $1,500 for 1000 pages

**Practical approach (what pikwik does implicitly):**
```python
def extract_from_pdf(file_data):
    # Try fast path first (text-based)
    try:
        text = pdfplumber.extract(file_data)  # Free
        if text and len(text.strip()) > 100:
            return text  # Got enough text, use it
    except:
        pass

    # Fall back to Textract for scanned PDFs
    text = textract.extract(file_data)  # $1.50/page
    return text
```

**Updated Phase 2 Logic:**
```python
def parse_pdf(file_data, queue_id):
    # Dedup check
    file_hash = hashlib.sha256(file_data).hexdigest()
    if is_duplicate(file_hash):
        return duplicate_response()

    # Try text extraction first (free)
    text = try_text_extraction(file_data)

    if text and sufficient_content(text):
        # Text-based PDF - use pdfplumber
        extracted = parse_text_pdf(text)
    else:
        # Scanned PDF - use AWS Textract
        extracted = textract_client.extract_and_parse(file_data)

    # Structure and return
    return structure_data(extracted)
```

---

## 3. NOTIFICATIONS ARCHITECTURE (Your Preferred Model)

### Your Vision: "One Service exposes APIs for notifications"

**Your proposal:**
```
Core processing happens
  ↓
Core writes directly to notifications.db
  ├─ No queue
  ├─ No secondary parsing
  └─ Just insert notification record

Float SDK
  ├─ WebSocket connects to notifications.db
  ├─ Receives real-time notifications
  └─ Shows to user immediately
```

**I AGREE - This is MUCH simpler than my "shared responsibility queue" model.**

### Simplified Architecture

```
Core Service (owns notifications.db write API)
  ├─ POST /api/v1/notifications (internal Core API)
  │   └─ Request: {type, invoice_id, message, severity}
  └─ Writes directly to notifications.db
     └─ Notification now exists in database

Float SDK
  ├─ Connects WebSocket to Core /api/v1/events
  ├─ Receives notification.created events
  ├─ Shows notification to user
  └─ User marks as read (updates notifications.db)
```

**Who owns what:**

| Component | Owner | Responsibility |
|-----------|-------|-----------------|
| notifications.db | Core | Schema + storage |
| POST /api/v1/notifications | Core | Writing notifications |
| GET /api/v1/notifications | Core | Reading notifications |
| WebSocket events | Core | Broadcasting notification events |
| notification.read/dismissed | Core | State updates |
| Delivery logic | Float SDK | Showing to user |
| Expiry cleanup | Core | Delete after TTL |

**Example flow:**

```python
# During Phase 3 (TRANSFORM)
if invoice.missing_hsn_code:
    core.post_notification({
        "type": "approval_request",
        "invoice_id": "INV_001",
        "message": "HSN code missing. Approve auto-assignment or provide code.",
        "severity": "error",
        "requires_action": True,
        "target_user_id": "supervisor_123"
    })
    # This immediately writes to notifications.db
    # WebSocket broadcasts notification.created event
    # Float SDK shows it to user

# User approves or rejects
# Float calls: POST /api/v1/notification/{id}/approve or /reject
```

**Benefits:**
- ✅ No queue (simpler)
- ✅ No secondary parsing (no overhead)
- ✅ Direct database writes (faster)
- ✅ Notifications accessible via REST API if needed
- ✅ WebSocket for real-time, REST API for catch-up

---

## 4. TRANSFORMATION SCRIPTS: Shared Resource Architecture

### What I learned from pikwik-transforma-v1.0.py:

**Current state:**
```
Each customer has their own transformation script
  ├─ pikwik-transforma-v1.0.py (300KB)
  ├─ aramex-transforma-v5.1.py (also large)
  └─ [other customer scripts]

Problem: Code duplication
  ├─ Each script redefines common operations
  ├─ Three-stream adapter pattern (repeated across customers)
  ├─ Excel output logic (duplicated)
  ├─ Email dispatch (duplicated)
  └─ Error handling (duplicated)
```

### Your Vision: Modular Shared Architecture

**Future state (what you're building toward):**

```
Admin Packager manages modular libraries (in config.db)
  ├─ extract_module
  │   ├─ extract_pdf (text + scanned)
  │   ├─ extract_excel
  │   ├─ extract_csv
  │   └─ extract_xml
  │
  ├─ validate_module (shared across Relay + Core)
  │   ├─ validate_invoice_structure
  │   ├─ validate_tax_calculation
  │   └─ validate_party_details
  │
  ├─ enrich_module
  │   ├─ hsn_lookup (Prodeus API)
  │   ├─ tax_calculation
  │   └─ address_validation
  │
  ├─ format_module (FIRS compliance)
  │   ├─ format_to_ubl3_json
  │   ├─ generate_irn
  │   └─ generate_qr_code
  │
  └─ transport_module
      ├─ email_dispatch
      ├─ websocket_events
      └─ error_handling

Customer script (pikwik-transforma-v1.0.py) becomes:
  ├─ Stream adapter (Till/B2B/Rebate detection)
  ├─ Business logic (Pikwik-specific rules)
  └─ Module orchestration (call shared modules in right order)
```

**How Core accesses modules:**

```python
# In config.db:
{
    "customer_id": "pikwik",
    "script_type": "orchestrator",
    "script_content": """
    def transform(raw_data):
        # Use shared modules
        extracted = modules.extract.extract_pdf(raw_data.file)
        validated = modules.validate.validate_structure(extracted)
        enriched = modules.enrich.hsn_lookup(validated)
        formatted = modules.format.format_to_ubl3(enriched)
        return formatted
    """
}

# Core loads and executes:
script = load_transformation_script("pikwik")
modules = load_shared_modules_for_customer("pikwik")
result = script.transform(raw_data, modules)
```

**Benefits:**
- ✅ Less code duplication
- ✅ Easier updates (fix one module, benefits all customers)
- ✅ Better testing (modules tested independently)
- ✅ Faster script development (compose modules, not rewrite)
- ✅ Different services can access modules (Relay, Core, Edge)

**Implementation roadmap:**
```
MVP (Now): Store full scripts in config.db
Phase 1: Extract common modules from existing scripts
Phase 2: Refactor scripts to use modules
Phase 3: Build module marketplace (core, sync, enrichment, etc.)
Phase 4: Fine-grained access control (which service can use which modules)
```

---

## 5. CLARIFIED ENDPOINT QUESTION: To /process_preview or not?

### With WebSocket Progress Tracking:

**DO NOT use `/api/v1/process_preview` (blocking).**

**INSTEAD use:**
1. `POST /api/v1/enqueue` - Queue file (returns immediately)
2. `GET /api/v1/queue/{queue_id}/status` - Poll status
3. `WebSocket /api/v1/events` - Receive real-time progress updates

**Why:**
- ✅ No timeouts
- ✅ Real-time progress (not polling)
- ✅ Better UX (user always informed)
- ✅ Works for any batch size (10 or 30,000 invoices)
- ✅ Matches pikwik's architecture (which emits WebSocket events)

**Revised endpoint count: 18 endpoints (not 19 or 21)**

Remove:
- ❌ `POST /api/v1/process_preview` (blocking)

Keep:
- ✅ `POST /api/v1/enqueue`
- ✅ `GET /api/v1/queue/{queue_id}/status`
- ✅ `POST /api/v1/finalize`

---

## 6. EXTRACT IS ENCAPSULATED IN PARSE - CONFIRMED

**YES - Update all documentation:**

```
PARSE (Phase 2) = Detect format + EXTRACT data + Structure

Not: PARSE, then separate EXTRACT

Remains 8 phases (not 9)
```

---

## 📊 SUMMARY TABLE: What Changes

| Item | Old Understanding | New Understanding | Reason |
|------|-------------------|-------------------|--------|
| `/process_preview` blocking | Include | **REMOVE** | No timeouts, use WebSocket instead |
| Polling | Acceptable | **Unacceptable** | User experience worse than WebSocket |
| Notifications architecture | Queue + HeartBeat | **Direct API + WebSocket** | Simpler, no queue |
| Notifications.db owner | HeartBeat | **Core** | Core generates notifications |
| AWS Textract | Use for all PDFs | **Use only for scanned PDFs** | Cost optimization (free for text PDFs) |
| Transformation scripts | Individual per customer | **Modular + shared (future)** | Code reuse, easier maintenance |
| Extract phase | Separate | **Encapsulated in PARSE** | Simpler pipeline |
| Endpoint count | 21 | **18** | Removed blocking `/process_preview` |

---

## 🎯 REVISED 8-PHASE PIPELINE

```
PHASE 1: FETCH (HAIKU)
  Input: Queue entry → Output: File bytes

PHASE 2: PARSE (HAIKU) [INCLUDES EXTRACTION]
  Input: File bytes → Output: Raw structured data
  ├─ Detect file type
  ├─ Extract via pdfplumber (text PDF, free)
  ├─ Extract via AWS Textract (scanned PDF, cost)
  ├─ Check SHA256 deduplication
  └─ Structure invoice data

PHASE 3: TRANSFORM (SONNET)
  Input: Raw data → Output: FIRS-compliant data
  ├─ Load customer transformation script
  ├─ Call shared modules (if applicable)
  └─ Format to FIRS standard

PHASE 4: ENRICH (SONNET)
  Input: FIRS data → Output: FIRS data + enrichment
  ├─ HSN lookup
  ├─ Category classification
  ├─ Address validation
  └─ AI enrichment

PHASE 5: RESOLVE (SONNET)
  Input: Enriched data → Output: Linked to master data
  ├─ Match/merge customers
  ├─ Match/merge inventory
  └─ Update master data

PHASE 6: PORTO BELLO (OPUS)
  Input: Resolved data → Output: Invoice with status flag
  ├─ Check portoBello config
  └─ Route: sign only vs sign+transmit

PHASE 7: BRANCH (OPUS)
  Input: Invoice → Output: Preview data or continue
  ├─ If preview mode: Generate 6 files to blob
  ├─ If immediate: Continue to finalize
  └─ Emit WebSocket progress events

PHASE 8: FINALIZE (OPUS)
  Input: Invoice (± edits) → Output: Database records + Edge queue
  ├─ Apply user edits
  ├─ Create database records
  ├─ Queue to Edge
  ├─ Broadcast WebSocket events
  ├─ Schedule 24-hour cleanup
  └─ Log audit events
```

---

## ✅ CORRECTED UNDERSTANDING

**Core Service Architecture:**
- 8 phases (not 9)
- Extract encapsulated in PARSE
- No blocking `/process_preview` endpoint
- Use WebSocket for real-time progress (not polling or blocking)
- Core owns notifications.db (simple direct writes)
- Transformation scripts modular + shared (future roadmap)
- AWS Textract for scanned PDFs only (cost optimization)
- 18 API endpoints total

---

**Status:** Ready to update all documentation
**Next Step:** User approval of this revised architecture

---

**Document Version:** 1.0
**Created:** 2026-02-01
**Status:** PRACTICAL ARCHITECTURE CLARIFIED
