# CORE SERVICE — REPORT ENGINE

**Version:** 1.0
**Date:** 2026-03-18
**Status:** DRAFT — Requires user review before implementation
**Owner:** Core Service
**Consumers:** Float SDK (Statistics mApp, Docs mApp), Admin Dashboard

---

## PURPOSE

Core's Report Engine generates three categories of output:

1. **Statistics mApp Data** — Live aggregate metrics for the Statistics mApp in Float
2. **Downloadable Reports** — On-demand PDF/Excel reports (compliance, audit, transmission)
3. **Scheduled Reports** — Periodic summaries (daily/weekly/monthly) delivered as notifications

---

## CATEGORY 1: STATISTICS MAPP DATA

### What It Is
Core computes aggregate statistics that feed the Statistics mApp's 5 sub-tabs: Overview, Invoices, Customers, Inventory, Compliance.

### Endpoint
```
GET /api/v1/statistics?section=overview|invoices|customers|inventory|compliance
    &period=today|week|month|quarter|year|all
    &date_from=2026-01-01
    &date_to=2026-03-18
```

### Response Structure

#### Overview Section
```json
{
    "section": "overview",
    "period": "month",
    "data": {
        "total_invoices": 1250,
        "total_value": 45000000.00,
        "total_tax": 3375000.00,
        "transmitted_count": 1200,
        "accepted_count": 1180,
        "rejected_count": 20,
        "pending_count": 50,
        "acceptance_rate": 98.33,
        "avg_processing_time_ms": 3200,
        "active_customers": 45,
        "active_products": 120,
        "compliance_score": 94.5,
        "trend": {
            "invoices_change_pct": 12.5,
            "value_change_pct": 8.3,
            "direction": "up"
        }
    }
}
```

#### Invoices Section
```json
{
    "section": "invoices",
    "data": {
        "by_status": {"committed": 50, "transmitted": 1100, "accepted": 1080, "rejected": 20},
        "by_direction": {"outbound": 1200, "inbound": 50},
        "by_type": {"commercial_invoice": 1100, "credit_note": 100, "debit_note": 50},
        "by_transaction": {"b2b": 900, "b2g": 100, "b2c": 250},
        "top_customers_by_value": [{"name": "Customer A", "value": 5000000}],
        "daily_volumes": [{"date": "2026-03-18", "count": 45, "value": 1200000}],
        "payment_health": {"unpaid": 200, "paid": 900, "partial": 100, "overdue": 50}
    }
}
```

#### Customers Section
```json
{
    "section": "customers",
    "data": {
        "total_active": 45,
        "total_inactive": 10,
        "avg_compliance_score": 78.5,
        "compliance_distribution": {"excellent": 15, "good": 20, "fair": 5, "poor": 5},
        "top_by_invoice_count": [...],
        "top_by_lifetime_value": [...],
        "new_this_period": 3,
        "b2b_count": 35,
        "b2g_count": 10
    }
}
```

#### Inventory Section
```json
{
    "section": "inventory",
    "data": {
        "total_products": 120,
        "goods_count": 85,
        "services_count": 35,
        "classified_count": 110,
        "unclassified_count": 10,
        "top_by_revenue": [...],
        "top_by_frequency": [...],
        "vat_treatment_breakdown": {"standard": 100, "zero_rated": 15, "exempt": 5}
    }
}
```

#### Compliance Section
```json
{
    "section": "compliance",
    "data": {
        "overall_score": 94.5,
        "firs_acceptance_rate": 98.33,
        "avg_submission_time_hours": 2.5,
        "overdue_count": 50,
        "disputed_count": 10,
        "customer_compliance_avg": 78.5,
        "common_rejection_reasons": [
            {"reason": "Invalid TIN", "count": 10},
            {"reason": "Missing HS code", "count": 5}
        ],
        "monthly_trend": [
            {"month": "2026-01", "score": 90.1},
            {"month": "2026-02", "score": 92.8},
            {"month": "2026-03", "score": 94.5}
        ]
    }
}
```

### Computation Strategy
- **Real-time aggregates**: Use PostgreSQL views (vw_invoice_metrics, vw_customer_metrics, vw_inventory_metrics)
- **Cached results**: Cache aggregate queries for 5 minutes (in-memory)
- **Denormalized fields**: Customer/inventory aggregate fields updated on invoice events (total_invoices, total_lifetime_value, etc.)

---

## CATEGORY 2: DOWNLOADABLE REPORTS

### Report Types

| Report | Format | Trigger | Content |
|--------|--------|---------|---------|
| Processing Report | PDF/Excel | After process_preview | Statistics + red flags + invoice details |
| Compliance Report | PDF | On-demand | FIRS compliance scorecard + rejection analysis |
| Transmission Report | Excel | On-demand | All transmission attempts + status |
| Customer Report | Excel | On-demand | Customer master data + compliance scores |
| Audit Trail Report | PDF | On-demand | All edit history + processing events |
| Monthly Summary | PDF | Scheduled (monthly) | Invoice volumes, values, compliance trend |

### Endpoint
```
POST /api/v1/reports/generate
{
    "report_type": "compliance|transmission|customer|audit|monthly_summary",
    "format": "pdf|excel",
    "filters": {
        "date_from": "2026-01-01",
        "date_to": "2026-03-18",
        "status": ["TRANSMITTED", "ACCEPTED"],
        "customer_id": null
    }
}

Response (202 Accepted):
{
    "report_id": "uuid",
    "status": "generating",
    "estimated_seconds": 30
}
```

### Report Download
```
GET /api/v1/reports/{report_id}/download

Response: Binary file (PDF or Excel)
Content-Type: application/pdf or application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Content-Disposition: attachment; filename="compliance_report_20260318.pdf"
```

### Report Status
```
GET /api/v1/reports/{report_id}/status

Response:
{
    "report_id": "uuid",
    "status": "generating|ready|failed|expired",
    "download_url": "/api/v1/reports/{id}/download",
    "generated_at": "ISO timestamp",
    "expires_at": "ISO timestamp (7 days)",
    "size_bytes": 125000
}
```

### Report Storage
- Generated reports stored as blob outputs in HeartBeat (`POST /api/blob/write`)
- 7-day retention (same as preview data)
- Report metadata tracked in `core.reports` table

### Report Table (core.reports)
```sql
CREATE TABLE core.reports (
    report_id       TEXT PRIMARY KEY,       -- UUIDv7
    report_type     TEXT NOT NULL,
    format          TEXT NOT NULL,           -- pdf | excel
    status          TEXT NOT NULL DEFAULT 'generating',
    blob_uuid       TEXT,                   -- HeartBeat blob reference (after generation)
    filters         JSONB,                  -- Generation filters
    generated_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    size_bytes      INTEGER,
    generated_by    TEXT,                   -- helium_user_id
    company_id      TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

---

## CATEGORY 3: SCHEDULED REPORTS

### Types

| Schedule | Report | Delivery |
|----------|--------|----------|
| Daily (6am) | Daily processing summary | Notification + .hlm file |
| Weekly (Monday 6am) | Weekly compliance scorecard | Notification + PDF |
| Monthly (1st, 6am) | Monthly summary report | Notification + PDF |

### Implementation
- APScheduler cron jobs (from WS0 scheduler)
- Generate report → Store as blob → Create notification → Emit SSE event
- Notifications appear in Float's Notifications tab

---

## WORKSTREAM MAPPING

| Component | Workstream |
|-----------|-----------|
| Statistics endpoint (`GET /statistics`) | WS4 (ENTITY CRUD) |
| Report generation endpoint (`POST /reports/generate`) | New — may become WS7 or part of WS4 |
| Scheduled report jobs | WS6 (OBSERVABILITY) |
| Report storage (core.reports table) | WS0 (FOUNDATION) |
| Preview report.json | WS3 (ORCHESTRATOR) |

---

## LIBRARIES

| Purpose | Library |
|---------|---------|
| PDF generation | `reportlab` or `weasyprint` |
| Excel generation | `openpyxl` |
| Chart generation | `matplotlib` (for PDF charts) |
| Template rendering | `jinja2` (for report templates) |

---

**Last Updated:** 2026-03-18
**Status:** DRAFT — Report types and formats need user confirmation
