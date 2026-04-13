# WS5 Supplementary: Fixed PDF — FIRS IRN+QR Stamping

**Date:** 2026-03-25 (Updated with Part 2 corrections)
**From:** Architecture session (Bob + Opus)
**To:** WS5 Part 2 team
**Status:** Stubbed in WS5 Part 1 — full build deferred (PDF ingestion is ~1% of tenant requests currently)

---

## WHAT THIS IS

After WS5 finalizes an invoice (generates IRN + QR code), if the source file was a PDF, stamp the IRN text and QR image onto the original PDF. The result = the "fixed" invoice — the original document with FIRS compliance markings.

This is ONLY for PDF-source invoices. Excel/CSV uploads do NOT produce a fixed PDF.

---

## KEY DESIGN DECISION: EIC-Driven Placement

**The stamping MUST use the tenant's EIC (Extraction Intelligence Config) for placement.**

EIC is the per-tenant invoice template fingerprint from IntelliCore. It knows:
- Where the invoice number sits on the PDF
- Where margins/whitespace exist
- Page layout (portrait/landscape)
- Safe zones for stamping (areas that won't overlap content)

**The stamping module reads EIC to decide WHERE on the PDF to place the IRN and QR.** If no EIC exists (tenant hasn't trained IntelliCore yet), fall back to default placement.

---

## SHARED MODULE: `helium_formats.pdf_stamper`

**This module lives in `helium_formats`, NOT in Core.**

Why: Both Core (server-side, after finalize) and Float SDK (client-side, real-time preview) need to stamp PDFs. Putting it in `helium_formats` makes it a shared dependency — same code, same output, guaranteed visual consistency.

```python
# helium_formats/pdf_stamper/
├── __init__.py
├── stamper.py          # FixedPDFStamper class
├── placement.py        # EIC-driven placement resolver
├── models.py           # StampPlacement, StampConfig
└── defaults.py         # Default placement when no EIC
```

### Core Usage (server-side, after finalize)

```python
from helium_formats.pdf_stamper import FixedPDFStamper

stamper = FixedPDFStamper()
fixed_pdf_bytes = await stamper.stamp(
    original_pdf_bytes=original_pdf,
    irn="INV-001-SVC001-20260325",
    qr_code_data=qr_base64,           # Base64-encoded PNG
    eic_config=tenant_eic,             # EIC placement config (or None for defaults)
)

# Store fixed PDF to HeartBeat blob
blob_uuid = await heartbeat_client.store_blob(fixed_pdf_bytes, filename=f"{irn}_fixed.pdf")

# Link to invoice record
await update_invoice(invoice_id, fixed_pdf_blob_uuid=blob_uuid)
```

### SDK Usage (client-side, real-time preview in ReviewPage)

```python
from helium_formats.pdf_stamper import FixedPDFStamper

# SDK caches the stamper module + tenant EIC (same pattern as Relay caching IQC)
stamper = FixedPDFStamper()
preview_pdf = await stamper.stamp(
    original_pdf_bytes=cached_original_pdf,
    irn=generated_irn,
    qr_code_data=generated_qr,
    eic_config=cached_eic,
)
# Render preview_pdf in ReviewPage PDF viewer
```

**The SDK caches:**
1. The original PDF (from HeartBeat blob)
2. The tenant's EIC config (from HeartBeat/IntelliCore)
3. The `helium_formats.pdf_stamper` module (installed as dependency)

This lets Float show a real-time preview of the fixed PDF as the user reviews invoices, without a round-trip to Core.

---

## FixedPDFStamper Class

```python
class FixedPDFStamper:
    """
    Stamp IRN text + QR code image onto a PDF.
    Uses EIC config for intelligent placement. Falls back to defaults.
    """

    async def stamp(
        self,
        original_pdf_bytes: bytes,
        irn: str,
        qr_code_data: str,             # Base64-encoded PNG
        eic_config: dict | None = None,  # Tenant EIC placement config
    ) -> bytes:
        """
        1. Resolve placement (EIC or defaults)
        2. Decode QR from base64 to PIL Image
        3. Open PDF with pypdf
        4. Create overlay page (reportlab):
           - IRN text at resolved position
           - QR image at resolved position
        5. Merge overlay onto target page(s)
        6. Return modified PDF bytes
        """

    def _resolve_placement(self, eic_config: dict | None) -> StampPlacement:
        """
        If EIC config provided:
          - Use EIC safe zones (areas with no content)
          - Place IRN near top-right safe zone
          - Place QR near bottom-right safe zone
          - Respect page orientation

        If no EIC config:
          - Use default placement (top-right IRN, bottom-right QR)
        """
```

### StampPlacement Model

```python
@dataclass
class StampPlacement:
    # IRN text placement
    irn_x: float            # Points from left edge
    irn_y: float            # Points from bottom edge
    irn_font_size: int = 8
    irn_font: str = "Helvetica"
    irn_color: tuple = (0, 0, 0)  # RGB black

    # QR code placement
    qr_x: float
    qr_y: float
    qr_width: float = 100   # Points (100pt ≈ 1.4 inches)
    qr_height: float = 100

    # Target page(s)
    target_pages: list[int] = field(default_factory=lambda: [0])  # First page only by default
```

### Default Placement (no EIC)

```python
DEFAULT_PLACEMENT = StampPlacement(
    irn_x=400,       # Near right edge
    irn_y=780,       # Near top
    irn_font_size=8,
    qr_x=450,        # Near right edge
    qr_y=50,         # Near bottom
    qr_width=100,
    qr_height=100,
    target_pages=[0],  # First page only
)
```

### EIC-Driven Placement

When EIC config is available, it provides:

```json
{
    "page_layout": "portrait",
    "page_width_pt": 595,
    "page_height_pt": 842,
    "safe_zones": [
        {"x": 400, "y": 750, "width": 150, "height": 80, "label": "top_right_margin"},
        {"x": 420, "y": 20, "width": 150, "height": 120, "label": "bottom_right_margin"}
    ],
    "stamp_preferences": {
        "irn_zone": "top_right_margin",
        "qr_zone": "bottom_right_margin",
        "irn_font_size": 7,
        "qr_size": 80
    }
}
```

The placement resolver maps `irn_zone` → safe zone coordinates, `qr_zone` → safe zone coordinates.

---

## FLOW IN WS5 (Post-Finalize)

```
WS5 finalize completes:
  ├── Invoice has IRN + QR code data
  ├── Check: was source file PDF? (metadata.content_type == "application/pdf")
  │
  ├── If YES (PDF source):
  │   ├── Fetch original PDF from HeartBeat (GET /api/blobs/{blob_uuid}/download)
  │   ├── Load tenant EIC config (from IntelliCore or HeartBeat cache)
  │   ├── Call FixedPDFStamper.stamp(original, irn, qr, eic)
  │   ├── Store fixed PDF to HeartBeat blob (POST /api/blob/write)
  │   │   └── HeartBeat manages storage in its blob files folder — Core does NOT store the PDF
  │   ├── HeartBeat returns blob_uuid for the fixed PDF
  │   └── Audit log: finalize.pdf_fixed (invoice_id, original_blob, fixed_blob)
  │
  └── If NO (Excel/CSV source):
      └── Skip. No fixed PDF.
```

**IMPORTANT — Stamped PDF does NOT go to Edge.** Edge only receives structured e-invoice data from `invoices.db`. The stamped PDF is stored in HeartBeat blob storage for user download/display only.

**Error handling:** If PDF stamping fails (corrupt PDF, missing EIC, reportlab error), **log the error and continue**. The invoice is already finalized — the fixed PDF is a nice-to-have, not a gate.

---

## EIC AVAILABILITY — CONFIG CACHING

WS0 (or the calling service) must verify EIC config availability before stamping:

1. **Check cache** — tenant EIC cached locally from previous fetch
2. **Hash checksum with HeartBeat** — verify cached EIC is still current
3. **If stale/missing** — fetch fresh EIC config from `config.db` via HeartBeat API

This is the standard tenant config caching pattern. EIC is just another config artifact.

---

## LIBRARIES

| Purpose | Library | Notes |
|---|---|---|
| PDF read/merge | `pypdf` (successor to PyPDF2) | Read original, merge overlay |
| PDF overlay creation | `reportlab` | Create page with IRN text + QR image |
| Image handling | `Pillow` | Decode QR from base64 to image |

These are lightweight — they work on both server (Core) and client (SDK/Float).

---

## DELIVERABLES

| # | Deliverable | Location | Priority |
|---|---|---|---|
| 1 | `FixedPDFStamper` class | `helium_formats/pdf_stamper/stamper.py` | P0 |
| 2 | `StampPlacement` model | `helium_formats/pdf_stamper/models.py` | P0 |
| 3 | EIC placement resolver | `helium_formats/pdf_stamper/placement.py` | P0 |
| 4 | Default placement | `helium_formats/pdf_stamper/defaults.py` | P0 |
| 5 | Wire into WS5 finalize pipeline | `Core/src/finalize/pipeline.py` | P1 (stubbed) |
| 6 | Tests (stamp, EIC placement, defaults, error cases) | `helium_formats/tests/test_pdf_stamper.py` | P1 |
| 7 | Sample fixed PDF in samples/ | `helium_formats/samples/sample_fixed.pdf` | P2 |

**NOTE:** Since this goes in `helium_formats`, coordinate with the WS-HLX team (they own the package). The package lives at `Helium/Services/helium_formats/` and is designed for modular additions.

**Current Status (WS5 Part 1):** The pipeline has a stub for PDF stamping that logs "PDF stamping deferred" and continues. The wiring and modalities are in place — the actual stamper class needs to be built in `helium_formats/pdf_stamper/` when PDF ingestion volume justifies it.

---

## SDK CACHING NOTE

The SDK should cache:

1. **Tenant EIC config** — fetched from HeartBeat on login, refreshed on `config.eic_changed` SSE event
2. **Original PDFs** — already cached in SDK local file cache (`data/files/`)
3. **`helium_formats` is installed** — `FixedPDFStamper` is available as an import

This allows Float's ReviewPage to show a **real-time preview** of the fixed PDF without calling Core. The user sees exactly what the stamped invoice will look like before finalizing.

---

**Last Updated:** 2026-03-25
