"""
WS7: Statistics Service — Aggregate metrics for Float's Statistics mApp.

Queries PostgreSQL views + direct tables with date-windowed WHERE clauses.
5-minute in-memory TTL cache per (section, period, date_from, date_to, company_id).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog
from psycopg_pool import AsyncConnectionPool

from src.reports.models import StatisticsPeriod, StatisticsSection

logger = structlog.get_logger()

# Cache TTL: 5 minutes (per D-WS0-015 pattern, shorter than entity cache)
CACHE_TTL_SECONDS = 300


class StatisticsCache:
    """Simple in-memory TTL cache for statistics queries."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self._ttl = ttl
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return data

    def set(self, key: str, data: dict[str, Any]) -> None:
        self._store[key] = (time.monotonic(), data)

    def invalidate(self) -> None:
        self._store.clear()


# Module-level cache instance
_cache = StatisticsCache()


def resolve_period(
    period: StatisticsPeriod,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[date, date]:
    """
    Resolve a period enum to concrete (date_from, date_to).
    Explicit date_from/date_to override the period.
    """
    if date_from and date_to:
        return date_from, date_to

    today = date.today()

    if period == StatisticsPeriod.TODAY:
        return today, today
    elif period == StatisticsPeriod.WEEK:
        start = today - timedelta(days=today.weekday())  # Monday
        return start, today
    elif period == StatisticsPeriod.MONTH:
        start = today.replace(day=1)
        return start, today
    elif period == StatisticsPeriod.QUARTER:
        quarter_month = ((today.month - 1) // 3) * 3 + 1
        start = today.replace(month=quarter_month, day=1)
        return start, today
    elif period == StatisticsPeriod.YEAR:
        start = today.replace(month=1, day=1)
        return start, today
    else:  # ALL
        return date(2020, 1, 1), today


async def get_statistics(
    pool: AsyncConnectionPool,
    section: StatisticsSection,
    period: StatisticsPeriod,
    date_from: date | None = None,
    date_to: date | None = None,
    company_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch statistics for a given section and time period.

    Returns the data payload matching REPORT_ENGINE.md response structure.
    """
    resolved_from, resolved_to = resolve_period(period, date_from, date_to)

    # Cache lookup
    cache_key = f"{section.value}:{period.value}:{resolved_from}:{resolved_to}:{company_id or 'all'}"
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug("statistics_cache_hit", section=section.value, key=cache_key)
        return cached

    # Dispatch to section handler
    handler = _SECTION_HANDLERS.get(section)
    if handler is None:
        return {}

    data = await handler(pool, resolved_from, resolved_to, company_id)

    _cache.set(cache_key, data)
    logger.info(
        "statistics_computed",
        section=section.value,
        period=period.value,
        date_from=str(resolved_from),
        date_to=str(resolved_to),
    )
    return data


# ── Section Handlers ─────────────────────────────────────────────────────


async def _overview(
    pool: AsyncConnectionPool,
    date_from: date,
    date_to: date,
    company_id: str | None,
) -> dict[str, Any]:
    """Overview section: high-level aggregates across all entities."""
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO invoices, customers, inventory")

        # Invoice aggregates for the period
        company_filter = "AND company_id = %s" if company_id else ""
        params: list[Any] = [str(date_from), str(date_to)]
        if company_id:
            params.append(company_id)

        cur = await conn.execute(
            f"""
            SELECT
                COUNT(*)                                             AS total_invoices,
                COALESCE(SUM(total_amount), 0.0)                     AS total_value,
                COALESCE(SUM(tax_amount), 0.0)                       AS total_tax,
                SUM(CASE WHEN transmission_status IN ('TRANSMITTED','ACCEPTED')
                         THEN 1 ELSE 0 END)                         AS transmitted_count,
                SUM(CASE WHEN transmission_status = 'ACCEPTED'
                         THEN 1 ELSE 0 END)                         AS accepted_count,
                SUM(CASE WHEN transmission_status IN ('FAILED_RETRYABLE','FAILED_TERMINAL')
                         THEN 1 ELSE 0 END)                         AS rejected_count,
                SUM(CASE WHEN workflow_status = 'COMMITTED'
                         THEN 1 ELSE 0 END)                         AS pending_count,
                SUM(CASE WHEN payment_status = 'UNPAID'
                          AND due_date IS NOT NULL
                          AND due_date::DATE < CURRENT_DATE
                         THEN 1 ELSE 0 END)                         AS overdue_count
            FROM invoices
            WHERE deleted_at IS NULL
              AND issue_date::DATE >= %s::DATE
              AND issue_date::DATE <= %s::DATE
              {company_filter}
            """,
            params,
        )
        inv = await cur.fetchone()

        # Customer count
        c_params: list[Any] = []
        c_filter = ""
        if company_id:
            c_filter = "AND company_id = %s"
            c_params.append(company_id)

        cur = await conn.execute(
            f"SELECT COUNT(*) FROM customers WHERE deleted_at IS NULL {c_filter}",
            c_params,
        )
        customer_row = await cur.fetchone()

        # Inventory count
        cur = await conn.execute(
            f"SELECT COUNT(*) FROM inventory WHERE deleted_at IS NULL {c_filter}",
            c_params,
        )
        inventory_row = await cur.fetchone()

    total = inv[0] or 0
    transmitted = inv[3] or 0
    accepted = inv[4] or 0
    acceptance_rate = round(100.0 * accepted / transmitted, 2) if transmitted > 0 else 0.0

    return {
        "total_invoices": total,
        "total_value": float(inv[1] or 0),
        "total_tax": float(inv[2] or 0),
        "transmitted_count": transmitted,
        "accepted_count": accepted,
        "rejected_count": inv[5] or 0,
        "pending_count": inv[6] or 0,
        "overdue_count": inv[7] or 0,
        "acceptance_rate": acceptance_rate,
        "active_customers": customer_row[0] if customer_row else 0,
        "active_products": inventory_row[0] if inventory_row else 0,
    }


async def _invoices(
    pool: AsyncConnectionPool,
    date_from: date,
    date_to: date,
    company_id: str | None,
) -> dict[str, Any]:
    """Invoices section: breakdowns by status, direction, type, payment."""
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO invoices")

        company_filter = "AND company_id = %s" if company_id else ""
        params: list[Any] = [str(date_from), str(date_to)]
        if company_id:
            params.append(company_id)

        base_where = f"""
            deleted_at IS NULL
            AND issue_date::DATE >= %s::DATE
            AND issue_date::DATE <= %s::DATE
            {company_filter}
        """

        # By workflow_status
        cur = await conn.execute(
            f"""
            SELECT workflow_status, COUNT(*)
            FROM invoices WHERE {base_where}
            GROUP BY workflow_status
            """,
            params,
        )
        by_status = {r[0]: r[1] for r in await cur.fetchall()}

        # By direction
        cur = await conn.execute(
            f"""
            SELECT direction, COUNT(*)
            FROM invoices WHERE {base_where}
            GROUP BY direction
            """,
            params,
        )
        by_direction = {r[0].lower(): r[1] for r in await cur.fetchall()}

        # By document_type
        cur = await conn.execute(
            f"""
            SELECT document_type, COUNT(*)
            FROM invoices WHERE {base_where}
            GROUP BY document_type
            """,
            params,
        )
        by_type = {r[0]: r[1] for r in await cur.fetchall()}

        # By transaction_type
        cur = await conn.execute(
            f"""
            SELECT transaction_type, COUNT(*)
            FROM invoices WHERE {base_where}
            GROUP BY transaction_type
            """,
            params,
        )
        by_transaction = {r[0]: r[1] for r in await cur.fetchall()}

        # Payment health
        cur = await conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN payment_status = 'UNPAID'   THEN 1 ELSE 0 END) AS unpaid,
                SUM(CASE WHEN payment_status = 'PAID'     THEN 1 ELSE 0 END) AS paid,
                SUM(CASE WHEN payment_status = 'PARTIAL'  THEN 1 ELSE 0 END) AS partial,
                SUM(CASE WHEN payment_status = 'UNPAID'
                          AND due_date IS NOT NULL
                          AND due_date::DATE < CURRENT_DATE
                         THEN 1 ELSE 0 END)                                  AS overdue
            FROM invoices WHERE {base_where}
            """,
            params,
        )
        pay = await cur.fetchone()

        # Daily volumes
        cur = await conn.execute(
            f"""
            SELECT issue_date::DATE AS d, COUNT(*), COALESCE(SUM(total_amount), 0)
            FROM invoices WHERE {base_where}
            GROUP BY d ORDER BY d
            """,
            params,
        )
        daily = [
            {"date": str(r[0]), "count": r[1], "value": float(r[2])}
            for r in await cur.fetchall()
        ]

    return {
        "by_status": by_status,
        "by_direction": by_direction,
        "by_type": by_type,
        "by_transaction": by_transaction,
        "payment_health": {
            "unpaid": pay[0] or 0,
            "paid": pay[1] or 0,
            "partial": pay[2] or 0,
            "overdue": pay[3] or 0,
        },
        "daily_volumes": daily,
    }


async def _customers(
    pool: AsyncConnectionPool,
    date_from: date,
    date_to: date,
    company_id: str | None,
) -> dict[str, Any]:
    """Customers section: active/inactive counts, compliance distribution, top by value."""
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO customers")

        company_filter = "AND company_id = %s" if company_id else ""
        c_params: list[Any] = []
        if company_id:
            c_params.append(company_id)

        # Active/inactive
        cur = await conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END)   AS active,
                SUM(CASE WHEN status != 'ACTIVE' THEN 1 ELSE 0 END)  AS inactive,
                AVG(compliance_score)                                  AS avg_compliance
            FROM customers
            WHERE deleted_at IS NULL {company_filter}
            """,
            c_params,
        )
        summary = await cur.fetchone()

        # Compliance distribution
        cur = await conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN compliance_score >= 90 THEN 1 ELSE 0 END) AS excellent,
                SUM(CASE WHEN compliance_score >= 70 AND compliance_score < 90 THEN 1 ELSE 0 END) AS good,
                SUM(CASE WHEN compliance_score >= 50 AND compliance_score < 70 THEN 1 ELSE 0 END) AS fair,
                SUM(CASE WHEN compliance_score < 50 OR compliance_score IS NULL THEN 1 ELSE 0 END) AS poor
            FROM customers
            WHERE deleted_at IS NULL {company_filter}
            """,
            c_params,
        )
        compliance = await cur.fetchone()

        # Top by lifetime value
        cur = await conn.execute(
            f"""
            SELECT customer_id, company_name, total_lifetime_value, total_invoices
            FROM customers
            WHERE deleted_at IS NULL {company_filter}
            ORDER BY total_lifetime_value DESC NULLS LAST
            LIMIT 10
            """,
            c_params,
        )
        top_by_value = [
            {
                "customer_id": r[0],
                "name": r[1],
                "lifetime_value": float(r[2] or 0),
                "invoice_count": r[3] or 0,
            }
            for r in await cur.fetchall()
        ]

        # B2B / B2G breakdown
        cur = await conn.execute(
            f"""
            SELECT customer_type, COUNT(*)
            FROM customers
            WHERE deleted_at IS NULL {company_filter}
            GROUP BY customer_type
            """,
            c_params,
        )
        by_type = {r[0]: r[1] for r in await cur.fetchall()}

    return {
        "total_active": summary[0] or 0,
        "total_inactive": summary[1] or 0,
        "avg_compliance_score": round(float(summary[2] or 0), 1),
        "compliance_distribution": {
            "excellent": compliance[0] or 0,
            "good": compliance[1] or 0,
            "fair": compliance[2] or 0,
            "poor": compliance[3] or 0,
        },
        "top_by_lifetime_value": top_by_value,
        "by_type": by_type,
    }


async def _inventory(
    pool: AsyncConnectionPool,
    date_from: date,
    date_to: date,
    company_id: str | None,
) -> dict[str, Any]:
    """Inventory section: goods/services, classified/unclassified, top by revenue."""
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO inventory")

        company_filter = "AND company_id = %s" if company_id else ""
        params: list[Any] = []
        if company_id:
            params.append(company_id)

        cur = await conn.execute(
            f"""
            SELECT
                COUNT(*)                                                 AS total_products,
                SUM(CASE WHEN type = 'GOODS'   THEN 1 ELSE 0 END)       AS goods_count,
                SUM(CASE WHEN type = 'SERVICE'  THEN 1 ELSE 0 END)      AS services_count,
                SUM(CASE WHEN classification_confidence IS NOT NULL
                          AND classification_confidence > 0
                         THEN 1 ELSE 0 END)                              AS classified_count,
                SUM(CASE WHEN classification_confidence IS NULL
                          OR classification_confidence = 0
                         THEN 1 ELSE 0 END)                              AS unclassified_count
            FROM inventory
            WHERE deleted_at IS NULL {company_filter}
            """,
            params,
        )
        summary = await cur.fetchone()

        # Top by revenue
        cur = await conn.execute(
            f"""
            SELECT product_id, product_name, total_revenue, total_times_invoiced
            FROM inventory
            WHERE deleted_at IS NULL {company_filter}
            ORDER BY total_revenue DESC NULLS LAST
            LIMIT 10
            """,
            params,
        )
        top_by_revenue = [
            {
                "product_id": r[0],
                "name": r[1],
                "revenue": float(r[2] or 0),
                "times_invoiced": r[3] or 0,
            }
            for r in await cur.fetchall()
        ]

        # VAT treatment breakdown
        cur = await conn.execute(
            f"""
            SELECT vat_treatment, COUNT(*)
            FROM inventory
            WHERE deleted_at IS NULL {company_filter}
            GROUP BY vat_treatment
            """,
            params,
        )
        vat_breakdown = {r[0]: r[1] for r in await cur.fetchall()}

    return {
        "total_products": summary[0] or 0,
        "goods_count": summary[1] or 0,
        "services_count": summary[2] or 0,
        "classified_count": summary[3] or 0,
        "unclassified_count": summary[4] or 0,
        "top_by_revenue": top_by_revenue,
        "vat_treatment_breakdown": vat_breakdown,
    }


async def _compliance(
    pool: AsyncConnectionPool,
    date_from: date,
    date_to: date,
    company_id: str | None,
) -> dict[str, Any]:
    """Compliance section: FIRS acceptance, rejection reasons, monthly trend."""
    async with pool.connection() as conn:
        await conn.execute("SET search_path TO invoices")

        company_filter = "AND company_id = %s" if company_id else ""
        params: list[Any] = [str(date_from), str(date_to)]
        if company_id:
            params.append(company_id)

        base_where = f"""
            deleted_at IS NULL
            AND issue_date::DATE >= %s::DATE
            AND issue_date::DATE <= %s::DATE
            {company_filter}
        """

        # Acceptance rate
        cur = await conn.execute(
            f"""
            SELECT
                SUM(CASE WHEN transmission_status IN ('TRANSMITTED','ACCEPTED')
                         THEN 1 ELSE 0 END)                     AS submitted,
                SUM(CASE WHEN transmission_status = 'ACCEPTED'
                         THEN 1 ELSE 0 END)                     AS accepted,
                SUM(CASE WHEN transmission_status IN ('FAILED_RETRYABLE','FAILED_TERMINAL')
                         THEN 1 ELSE 0 END)                     AS rejected,
                SUM(CASE WHEN payment_status = 'UNPAID'
                          AND due_date IS NOT NULL
                          AND due_date::DATE < CURRENT_DATE
                         THEN 1 ELSE 0 END)                     AS overdue,
                SUM(CASE WHEN payment_status = 'DISPUTED'
                         THEN 1 ELSE 0 END)                     AS disputed
            FROM invoices WHERE {base_where}
            """,
            params,
        )
        row = await cur.fetchone()
        submitted = row[0] or 0
        accepted = row[1] or 0
        acceptance_rate = round(100.0 * accepted / submitted, 2) if submitted > 0 else 0.0

        # Common rejection reasons (from firs_rejection_reason field)
        cur = await conn.execute(
            f"""
            SELECT firs_rejection_reason, COUNT(*) AS cnt
            FROM invoices
            WHERE {base_where}
              AND firs_rejection_reason IS NOT NULL
            GROUP BY firs_rejection_reason
            ORDER BY cnt DESC
            LIMIT 10
            """,
            params,
        )
        rejection_reasons = [
            {"reason": r[0], "count": r[1]}
            for r in await cur.fetchall()
        ]

        # Monthly trend
        cur = await conn.execute(
            f"""
            SELECT
                to_char(issue_date::DATE, 'YYYY-MM') AS month,
                COUNT(*) AS total,
                SUM(CASE WHEN transmission_status = 'ACCEPTED' THEN 1 ELSE 0 END) AS accepted_count,
                SUM(CASE WHEN transmission_status IN ('TRANSMITTED','ACCEPTED')
                         THEN 1 ELSE 0 END) AS submitted_count
            FROM invoices
            WHERE {base_where}
            GROUP BY month
            ORDER BY month
            """,
            params,
        )
        monthly_trend = []
        for r in await cur.fetchall():
            sub = r[3] or 0
            acc = r[2] or 0
            score = round(100.0 * acc / sub, 1) if sub > 0 else 0.0
            monthly_trend.append({"month": r[0], "score": score})

    return {
        "firs_acceptance_rate": acceptance_rate,
        "submitted_count": submitted,
        "accepted_count": accepted,
        "rejected_count": row[2] or 0,
        "overdue_count": row[3] or 0,
        "disputed_count": row[4] or 0,
        "common_rejection_reasons": rejection_reasons,
        "monthly_trend": monthly_trend,
    }


# Handler dispatch table
_SECTION_HANDLERS = {
    StatisticsSection.OVERVIEW: _overview,
    StatisticsSection.INVOICES: _invoices,
    StatisticsSection.CUSTOMERS: _customers,
    StatisticsSection.INVENTORY: _inventory,
    StatisticsSection.COMPLIANCE: _compliance,
}
