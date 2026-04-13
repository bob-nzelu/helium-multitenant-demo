"""
IRN (Invoice Reference Number) Generator — FIRS spec.

Format: {INVOICE_NUMBER}-{SERVICE_ID}-{YYYYMMDD}
Example: INV001-94ND90NR-20241024

Components:
  - Invoice number: alphanumeric, no special chars, supplier's internal ID
  - Service ID: 8-char FIRS-assigned code from tenant config.db
  - Datestamp: YYYYMMDD, issue date, must not be future-dated

See: Pronalytics FIRS Schema v1.1.3 (Appendix B: IRN Generation Algorithm)
See: project_irn_format_firs.md
"""

from __future__ import annotations

import re
from datetime import date, datetime


class IRNError(Exception):
    """Error during IRN generation."""


# Alphanumeric only (FIRS requirement — no special chars)
_ALNUM_PATTERN = re.compile(r"^[A-Za-z0-9]+$")


def generate_irn(
    invoice_number: str,
    service_id: str,
    issue_date: str | date,
) -> str:
    """Generate IRN per FIRS specification.

    Args:
        invoice_number: Supplier's internal invoice identifier.
            Must be alphanumeric (no special chars, no spaces).
        service_id: 8-character FIRS-assigned service code from tenant config.
        issue_date: Invoice issue date as ISO string (YYYY-MM-DD) or date object.

    Returns:
        IRN string in format ``{INVOICE_NUMBER}-{SERVICE_ID}-{YYYYMMDD}``.

    Raises:
        IRNError: If any input is invalid.
    """
    # Validate invoice_number
    if not invoice_number:
        raise IRNError("invoice_number is required")
    clean_inv = invoice_number.strip()
    if not _ALNUM_PATTERN.match(clean_inv):
        raise IRNError(
            f"invoice_number must be alphanumeric only, got: {invoice_number!r}"
        )

    # Validate service_id
    if not service_id:
        raise IRNError("service_id is required (8-char FIRS-assigned code)")
    clean_sid = service_id.strip()
    if len(clean_sid) != 8:
        raise IRNError(
            f"service_id must be exactly 8 characters, got {len(clean_sid)}: {clean_sid!r}"
        )
    if not _ALNUM_PATTERN.match(clean_sid):
        raise IRNError(
            f"service_id must be alphanumeric, got: {clean_sid!r}"
        )

    # Parse and validate issue_date
    if isinstance(issue_date, str):
        try:
            parsed_date = date.fromisoformat(issue_date)
        except ValueError as e:
            raise IRNError(
                f"issue_date must be ISO format (YYYY-MM-DD), got: {issue_date!r}"
            ) from e
    elif isinstance(issue_date, date):
        parsed_date = issue_date
    else:
        raise IRNError(f"issue_date must be str or date, got: {type(issue_date)}")

    # FIRS rule: must not be future-dated
    if parsed_date > date.today():
        raise IRNError(
            f"issue_date must not be future-dated, got: {parsed_date.isoformat()}"
        )

    date_str = parsed_date.strftime("%Y%m%d")
    return f"{clean_inv}-{clean_sid}-{date_str}"


def validate_irn(irn: str) -> bool:
    """Check if an IRN string matches the FIRS format.

    Returns True if the format is valid (does not verify content).
    """
    parts = irn.split("-")
    if len(parts) != 3:
        return False
    inv_no, service_id, datestamp = parts
    if not _ALNUM_PATTERN.match(inv_no):
        return False
    if len(service_id) != 8 or not _ALNUM_PATTERN.match(service_id):
        return False
    if len(datestamp) != 8 or not datestamp.isdigit():
        return False
    try:
        datetime.strptime(datestamp, "%Y%m%d")
    except ValueError:
        return False
    return True
