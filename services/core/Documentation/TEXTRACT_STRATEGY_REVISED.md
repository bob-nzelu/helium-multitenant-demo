# AWS TEXTRACT STRATEGY - REVISED (NOT Just Text Extraction)

**Date:** 2026-02-01
**Status:** Understanding Textract's TRUE Value
**Based On:** Your feedback - Textract is ML-powered intelligence, not just OCR

---

## 🚨 MY MISTAKE: I Completely Misunderstood Textract's Purpose

I said: "Textract is just for extracting text from scanned PDFs"

**REALITY:** Textract is an ML-powered document intelligence service that:
- ✅ Extracts text (OCR)
- ✅ Detects and parses form fields (key-value pairs)
- ✅ Understands table structure (cell relationships, merged cells)
- ✅ Classifies documents by type
- ✅ Validates data consistency (amounts total, relationships make sense)
- ✅ Extracts handwriting
- ✅ Creates visual confidence markers

---

## THE ACTUAL USE CASE (From Your Feedback)

Pikwik receives:
- **Excel exports** (structured, easy to parse) → Use openpyxl, no Textract needed
- **PDF invoices** from vendors (unstructured, scanned) → Use Textract for ML intelligence

**What Textract does for PDFs:**

```
Raw scanned PDF (vendor invoice)
  ↓
AWS Textract Analysis
  ├─ OCR: Extract text from image
  ├─ Form Detection: Find "Invoice #", "Amount", "Date", etc.
  ├─ Table Recognition: Parse line items with structure
  ├─ Key-Value Extraction: Match "Total: $5,000" to correct field
  ├─ Confidence Scores: "Invoice # confidence: 98%"
  ├─ Relationships: "This tax amount applies to these items"
  └─ Document Classification: "This is an invoice from Vendor X"
```

**Example output:**

```json
{
  "document_type": "invoice",
  "confidence": 0.97,
  "fields": {
    "invoice_number": {
      "value": "INV-2026-001",
      "confidence": 0.99,
      "location": {"x": 100, "y": 50}
    },
    "invoice_date": {
      "value": "2026-01-31",
      "confidence": 0.98
    },
    "total_amount": {
      "value": "5000.00",
      "confidence": 0.96
    },
    "line_items": [
      {
        "description": "Flight services",
        "quantity": 1,
        "unit_price": 5000.00,
        "confidence": 0.95
      }
    ]
  },
  "validations": {
    "amount_totals_correctly": true,
    "all_required_fields_present": true,
    "document_acceptance_markers": ["valid_signature", "official_letterhead"]
  }
}
```

---

## WHERE PIKWIK WOULD USE TEXTRACT

**Pikwik's data sources:**

### **Stream 1: Till Reports (B2C)**
- Format: **Excel export** from POS system
- Parser: `openpyxl` (pandas)
- Textract: ❌ NOT needed (already structured)

### **Stream 2: B2B Customer Invoices**
- Format: **Mix of Excel + PDF**
  - From repeat customers: Excel exports → openpyxl
  - From new vendors: PDF invoices → **Textract**
- Textract: ✅ YES for PDFs
  - Extract form fields (invoice #, date, amount)
  - Parse line items (description, qty, price)
  - Validate: amounts total, tax calculated correctly
  - Classify: which vendor, which document type
  - Confidence scores: accept if >95%, flag if <95%

### **Stream 3: Vendor Rebates**
- Format: **Excel export** from vendor management system
- Parser: `openpyxl` (pandas)
- Textract: ❌ NOT needed (structured data)

---

## TEXTRACT INTELLIGENCE YOU NEED

Based on your feedback, you want Textract to:

### **1. Form Field Detection (Key-Value Extraction)**
```
"Find me the invoice number, date, total, and tax"
→ Textract ML identifies these fields automatically
→ Returns: {invoice_number: "INV-001", date: "2026-01-31", total: 5000}
```

**Use case:** Different vendors format invoices differently. Textract learns to find the right values regardless of layout.

### **2. Table Structure Recognition**
```
"Parse these line items with their descriptions, quantities, and prices"
→ Textract understands table structure
→ Correctly pairs product description with its quantity and price
→ Handles merged cells, complex headers, etc.
```

**Use case:** Vendor invoices often have complex tables. Textract keeps relationships intact.

### **3. Data Validation & Acceptance Thresholds**
```
"Make sure the amounts add up and tax is calculated correctly"
→ Textract (or Core) validates:
  - Sum of line items = subtotal? ✓/✗
  - Subtotal + tax = total? ✓/✗
  - Tax rate is reasonable? ✓/✗
→ Only accept if all checks pass OR confidence > 95%
```

**Use case:** Catch OCR errors or fraudulent invoices before processing.

### **4. Document Classification**
```
"What type of document is this? From which vendor?"
→ Textract ML classifies: "This is an invoice from Vendor X"
→ Can route to different processing logic based on type
```

**Use case:** Different document types (invoice vs credit note vs receipt) need different handling.

### **5. Visual Markers for Acceptance**
```
"Are there official markers? (letterhead, signature, watermark?)"
→ Textract detects visual confidence markers
→ "This document has official letterhead: confidence 98%"
→ Can use as acceptance threshold
```

**Use case:** Filter out suspicious or forged documents.

---

## REVISED PHASE 2: PARSE (With Textract Intelligence)

```python
def parse_file(file_data, file_type, source, queue_id):
    """
    Parse file and extract structured data with validation.

    For Excel: Simple structured extraction
    For PDF: ML-powered intelligence extraction
    """

    # Deduplication check
    file_hash = hashlib.sha256(file_data).hexdigest()
    if is_duplicate(file_hash):
        return duplicate_response()

    # Route by file type
    if file_type == "excel":
        # Simple extraction - structured data
        extracted = parse_excel(file_data)
        confidence = 0.99  # Excel is high confidence

    elif file_type == "pdf":
        # ML-powered extraction - unstructured data
        textract_response = textract_client.analyze_document(
            Document={'Bytes': file_data},
            FeatureTypes=['TABLES', 'FORMS']
        )

        # Extract key-value pairs (form fields)
        extracted = {
            "invoice_number": get_value(textract_response, "Invoice Number"),
            "invoice_date": get_value(textract_response, "Date"),
            "total_amount": get_value(textract_response, "Total"),
            "tax_amount": get_value(textract_response, "Tax"),
            "line_items": parse_table(textract_response, "items"),
            "confidence": get_average_confidence(textract_response)
        }

        confidence = extracted.get("confidence", 0.0)

    else:
        # CSV, XML, JSON: use appropriate parsers
        extracted = parse_other_format(file_data, file_type)
        confidence = 0.95

    # Validate extracted data
    validation = validate_extracted_data(extracted, confidence)
    if not validation["accepted"]:
        return {
            "status": "failed",
            "error": validation["reason"],
            "confidence": confidence,
            "visual_markers": validation.get("visual_markers", [])
        }

    # Record in processed_files
    db.insert("processed_files", {
        "sha256": file_hash,
        "queue_id": queue_id,
        "file_type": file_type,
        "confidence": confidence,
        "parsed_at": now()
    })

    return {
        "extracted_data": extracted,
        "confidence": confidence,
        "validations": validation
    }


def validate_extracted_data(extracted, confidence):
    """
    Validate that extracted data is acceptable.
    Uses both ML confidence and business rule checks.
    """
    issues = []
    visual_markers = []

    # Confidence threshold
    if confidence < 0.90:
        issues.append(f"Low OCR confidence ({confidence:.0%}). Manual review required.")

    # Amount validation
    calculated_subtotal = sum(item["quantity"] * item["unit_price"]
                            for item in extracted.get("line_items", []))
    stated_subtotal = float(extracted.get("subtotal", 0))

    if abs(calculated_subtotal - stated_subtotal) > 0.01:
        issues.append(
            f"Line item subtotal mismatch: "
            f"calculated={calculated_subtotal}, stated={stated_subtotal}"
        )

    # Tax validation
    expected_tax = calculated_subtotal * TAX_RATE
    stated_tax = float(extracted.get("tax_amount", 0))

    if abs(expected_tax - stated_tax) > 1.00:  # Allow $1 rounding difference
        issues.append(
            f"Tax mismatch: expected={expected_tax}, stated={stated_tax}"
        )

    # Total validation
    calculated_total = calculated_subtotal + stated_tax
    stated_total = float(extracted.get("total_amount", 0))

    if abs(calculated_total - stated_total) > 0.01:
        issues.append(
            f"Total mismatch: calculated={calculated_total}, stated={stated_total}"
        )

    # Required fields
    for field in ["invoice_number", "invoice_date", "total_amount"]:
        if not extracted.get(field):
            issues.append(f"Missing required field: {field}")

    # Determine acceptance
    if not issues and confidence >= 0.95:
        acceptance = "accepted"
    elif len(issues) <= 1 and confidence >= 0.90:
        acceptance = "accepted_with_flag"  # Mark for review
        visual_markers.append("low_confidence_flag")
    else:
        acceptance = "rejected"

    return {
        "accepted": acceptance == "accepted" or acceptance == "accepted_with_flag",
        "reason": " | ".join(issues) if issues else "Valid",
        "visual_markers": visual_markers,
        "requires_human_review": acceptance == "accepted_with_flag"
    }
```

---

## COST ANALYSIS: When to Use Textract

### **Excel Files (Pikwik's primary stream)**
- Cost: $0 (use openpyxl)
- Time: < 1 second
- Accuracy: 99.9% (structured data)
- Decision: ✅ **Always use openpyxl, never Textract**

### **PDF Files from Vendors**
- Cost: $1.50 per page (Textract)
- Time: 5-30 seconds (Textract API call)
- Accuracy: 85-99% (ML-based, depends on PDF quality)
- Decision: ✅ **Use Textract for form extraction + validation**

### **When to Skip Textract (Even for PDFs)**
- Simple text extraction only → Use pdfplumber (free, good for text PDFs)
- Structured tables only → Use pdfplumber + pandas (free)
- Decision: ❌ **If you only need raw text, pdfplumber is fine**

### **When Textract is Worth the Cost**
- Need form field extraction (invoice #, date, amount)
- Need table parsing with cell relationships
- Need confidence scores for validation
- Need document classification
- Need amount validation
- Decision: ✅ **Use Textract for unstructured PDFs**

---

## TEXTRACT IN PHASE 2 LOGIC

```python
def parse_pdf(file_data, queue_id):
    """
    Parse PDF file using AWS Textract.

    Returns:
    - extracted_data: Form fields, table data, confidence
    - visual_markers: Acceptance indicators
    - validation_status: Accepted, rejected, or flag for review
    """

    # Use Textract for FORM EXTRACTION + VALIDATION
    # (Not just OCR, but intelligent field detection + validation)

    textract_response = textract_client.analyze_document(
        Document={'Bytes': file_data},
        FeatureTypes=['TABLES', 'FORMS']  # Get forms + tables
    )

    # Extract and validate in one pass
    extracted = extract_and_validate(textract_response)

    if extracted["validation"]["requires_human_review"]:
        # Flag for human review, but continue processing
        log_flag("OCR confidence low", queue_id, extracted)

    if not extracted["validation"]["accepted"]:
        # Reject and return error
        return error_response(extracted)

    # Success - return extracted data
    return {
        "invoice_data": extracted["invoice_data"],
        "confidence": extracted["confidence"],
        "visual_markers": extracted["visual_markers"],
        "validation_status": "accepted"
    }
```

---

## UPDATED PHASE 2 RESPONSIBILITIES

**Phase 2: PARSE (HAIKU)**

File type detection:
- Excel → openpyxl (free)
- PDF → AWS Textract (ML intelligence, $1.50/page)
- CSV → pandas (free)
- XML → lxml (free)
- JSON → json module (free)

For PDFs specifically, Textract provides:
- ✅ Form field extraction (invoice #, date, amount)
- ✅ Table parsing (line items with quantities, prices)
- ✅ Key-value pairs (automatically finds "Total: $5000")
- ✅ Confidence scores (know how much to trust the data)
- ✅ Data validation (amounts total correctly)
- ✅ Visual markers (document authenticity)
- ✅ Document classification (invoice vs credit note vs receipt)

---

## COST ESTIMATE (Revised)

**Monthly volume (example: 1000 PDFs, 50 pages average)**

| Source | Format | Parser | Cost | Quality |
|--------|--------|--------|------|---------|
| Till reports | Excel | openpyxl | $0 | 99.9% |
| Repeat customers | Excel | openpyxl | $0 | 99.9% |
| New vendors | PDF | Textract | $75/month | 90-99% |
| **Total** | Mixed | Hybrid | **$75/month** | **High** |

**For Pikwik scale (assuming 10% of invoices are vendor PDFs):**
- 1000 B2B invoices/month
- 100 are vendor PDFs (1500 pages average)
- Cost: 1500 pages × $1.50 = **$2,250/month**
- Value: Automated intelligence on unstructured documents

---

## REVISED ARCHITECTURE DECISION

**Don't simplify away Textract. Instead:**

1. ✅ **Use openpyxl for Excel** (Pikwik's primary format)
2. ✅ **Use Textract for PDFs** (Vendor invoices)
   - For form extraction + validation
   - Not just text extraction
   - Accept based on confidence scores
   - Flag for human review if confidence low

3. ✅ **Fallback to pdfplumber** (Only for simple text extraction)
   - If Textract fails
   - If document is pure text PDF
   - For cost optimization on high-confidence documents

---

**Key Insight:** Textract is not a luxury; it's essential for handling unstructured vendor PDFs. The ML intelligence (form extraction, validation, classification) is what makes it valuable, not just the OCR.

---

**Document Version:** 1.0
**Created:** 2026-02-01
**Status:** TEXTRACT INTELLIGENCE CLARIFIED
