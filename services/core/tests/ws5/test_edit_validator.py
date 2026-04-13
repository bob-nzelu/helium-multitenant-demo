"""Tests for WS5 Edit Validator — diff engine for .hlm vs preview .hlx."""

import pytest

from src.finalize.edit_validator import EditValidator, EditViolation, AcceptedChange
from src.finalize.provenance import ORIGINAL, MISSING, HIS, TENANT, DERIVED, MANUAL


@pytest.fixture
def validator():
    return EditValidator()


def _make_row(overrides=None, provenance=None):
    """Build a minimal invoice row with optional provenance."""
    row = {
        "invoice_number": "INV-001",
        "issue_date": "2026-03-24",
        "total_amount": 53750.00,
        "subtotal": 50000.00,
        "tax_amount": 3750.00,
        "direction": "OUTBOUND",
        "transaction_type": "B2B",
        "seller_tin": "12345678-001",
        "buyer_tin": "87654321-001",
        "buyer_name": "Acme Ltd",
        "buyer_lga_code": "LGA-042",
        "reference": "PO-2026-001",
        "category": "Standard",
        "notes_to_firs": "",
        "hsn_code": "1905.90",
    }
    if overrides:
        row.update(overrides)
    if provenance is not None:
        row["__provenance__"] = provenance
    return row


# ── No Changes ───────────────────────────────────────────────────────────


class TestNoChanges:
    def test_identical_rows_valid(self, validator):
        preview = [_make_row()]
        submitted = [_make_row()]
        result = validator.validate(submitted, preview)
        assert result.is_valid
        assert len(result.violations) == 0
        assert len(result.accepted_changes) == 0

    def test_empty_rows_valid(self, validator):
        result = validator.validate([], [])
        assert result.is_valid


# ── Row Count Mismatch ───────────────────────────────────────────────────


class TestRowCountMismatch:
    def test_different_row_counts(self, validator):
        result = validator.validate([_make_row()], [_make_row(), _make_row()])
        assert not result.is_valid
        assert result.violations[0].reason == "row_count_mismatch"


# ── Never Editable Fields ────────────────────────────────────────────────


class TestNeverEditableFields:
    def test_change_invoice_number_rejected(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({"invoice_number": "INV-CHANGED"})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert result.violations[0].field == "invoice_number"
        assert result.violations[0].reason == "never_editable_field"

    def test_change_total_amount_rejected(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({"total_amount": 99999.99})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert result.violations[0].field == "total_amount"

    def test_change_issue_date_rejected(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({"issue_date": "2099-01-01"})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert result.violations[0].field == "issue_date"


# ── Always Editable Fields ───────────────────────────────────────────────


class TestAlwaysEditableFields:
    def test_change_reference_accepted(self, validator):
        prov = {"reference": ORIGINAL}
        preview = [_make_row(provenance=prov)]
        submitted = [_make_row({"reference": "PO-NEW"}, provenance=prov)]
        result = validator.validate(submitted, preview)
        assert result.is_valid
        assert len(result.accepted_changes) == 1
        assert result.accepted_changes[0].field == "reference"

    def test_change_category_accepted(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({"category": "Premium"})]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_change_notes_to_firs_accepted(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({"notes_to_firs": "Please process urgently"})]
        result = validator.validate(submitted, preview)
        assert result.is_valid


# ── Provenance-Gated Fields ──────────────────────────────────────────────


class TestProvenanceGatedFields:
    def test_change_his_field_accepted(self, validator):
        prov = {"buyer_lga_code": HIS}
        preview = [_make_row(provenance=prov)]
        submitted = [_make_row({"buyer_lga_code": "LGA-099"}, provenance=prov)]
        result = validator.validate(submitted, preview)
        assert result.is_valid
        assert result.accepted_changes[0].field == "buyer_lga_code"

    def test_change_missing_field_accepted(self, validator):
        prov = {"buyer_lga_code": MISSING}
        preview = [_make_row({"buyer_lga_code": ""}, provenance=prov)]
        submitted = [_make_row({"buyer_lga_code": "LGA-042"}, provenance=prov)]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_change_manual_field_accepted(self, validator):
        prov = {"buyer_lga_code": MANUAL}
        preview = [_make_row(provenance=prov)]
        submitted = [_make_row({"buyer_lga_code": "LGA-NEW"}, provenance=prov)]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_change_original_field_rejected(self, validator):
        prov = {"buyer_tin": ORIGINAL}
        preview = [_make_row(provenance=prov)]
        submitted = [_make_row({"buyer_tin": "99999999-999"}, provenance=prov)]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert result.violations[0].reason == "non_editable_provenance"

    def test_change_derived_field_rejected(self, validator):
        prov = {"hsn_code": DERIVED}
        preview = [_make_row(provenance=prov)]
        submitted = [_make_row({"hsn_code": "9999.99"}, provenance=prov)]
        result = validator.validate(submitted, preview)
        assert not result.is_valid

    def test_low_confidence_original_accepted(self, validator):
        """ORIGINAL field with low confidence should be editable."""
        prov = {"hsn_code": ORIGINAL}
        preview = [_make_row(
            {"hsn_code_confidence": 0.45},
            provenance=prov,
        )]
        submitted = [_make_row(
            {"hsn_code": "2106.90", "hsn_code_confidence": 0.45},
            provenance=prov,
        )]
        result = validator.validate(submitted, preview)
        assert result.is_valid


# ── Tenant Party Fields ──────────────────────────────────────────────────


class TestTenantPartyFields:
    def test_change_seller_tin_outbound_rejected(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({"seller_tin": "CHANGED-TIN"})]
        result = validator.validate(submitted, preview, direction="OUTBOUND")
        assert not result.is_valid
        assert result.violations[0].reason == "tenant_party_field"

    def test_change_buyer_tin_inbound_rejected(self, validator):
        """On INBOUND, tenant is the buyer — buyer fields locked."""
        preview = [_make_row()]
        submitted = [_make_row({"buyer_tin": "CHANGED-TIN"})]
        result = validator.validate(submitted, preview, direction="INBOUND")
        assert not result.is_valid
        assert result.violations[0].reason == "tenant_party_field"

    def test_change_buyer_tin_outbound_provenance_gated(self, validator):
        """On OUTBOUND, buyer is counterparty — provenance determines editability."""
        prov = {"buyer_tin": ORIGINAL}
        preview = [_make_row(provenance=prov)]
        submitted = [_make_row({"buyer_tin": "CHANGED"}, provenance=prov)]
        result = validator.validate(submitted, preview, direction="OUTBOUND")
        assert not result.is_valid  # ORIGINAL provenance blocks it


# ── Transaction Type Rules ───────────────────────────────────────────────


class TestTransactionTypeRules:
    def test_b2b_to_b2g_free_swap(self, validator):
        preview = [_make_row({"transaction_type": "B2B"})]
        submitted = [_make_row({"transaction_type": "B2G"})]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_b2g_to_b2b_free_swap(self, validator):
        preview = [_make_row({"transaction_type": "B2G"})]
        submitted = [_make_row({"transaction_type": "B2B"})]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_b2c_to_b2b_with_buyer_details_accepted(self, validator):
        preview = [_make_row({"transaction_type": "B2C"})]
        submitted = [_make_row({
            "transaction_type": "B2B",
            "buyer_tin": "12345678-001",
            "buyer_name": "Complete Corp",
        })]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_b2c_to_b2b_without_buyer_tin_rejected(self, validator):
        preview = [_make_row({"transaction_type": "B2C", "buyer_tin": ""})]
        submitted = [_make_row({
            "transaction_type": "B2B",
            "buyer_tin": "",
            "buyer_name": "Some Corp",
        })]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        violation = [v for v in result.violations if "b2c_upgrade" in v.reason]
        assert len(violation) > 0

    def test_b2c_to_b2g_without_buyer_name_rejected(self, validator):
        preview = [_make_row({"transaction_type": "B2C", "buyer_name": ""})]
        submitted = [_make_row({
            "transaction_type": "B2G",
            "buyer_tin": "12345678-001",
            "buyer_name": "",
        })]
        result = validator.validate(submitted, preview)
        assert not result.is_valid

    def test_inbound_b2c_to_b2b_checks_seller_not_buyer(self, validator):
        """On INBOUND, counterparty is seller. B2C upgrade must check seller details."""
        preview = [_make_row({"transaction_type": "B2C", "seller_tin": ""})]
        submitted = [_make_row({
            "transaction_type": "B2B",
            "seller_tin": "",  # Counterparty (seller) missing TIN
            "buyer_tin": "12345678-001",  # Tenant (buyer) has TIN — irrelevant
            "buyer_name": "Tenant Corp",
        })]
        result = validator.validate(submitted, preview, direction="INBOUND")
        assert not result.is_valid
        violation = [v for v in result.violations if "b2c_upgrade_requires_seller_tin" in v.reason]
        assert len(violation) > 0

    def test_inbound_b2c_to_b2b_with_seller_details_accepted(self, validator):
        """On INBOUND, counterparty is seller. B2C upgrade passes if seller has details."""
        preview = [_make_row({"transaction_type": "B2C"})]
        submitted = [_make_row({
            "transaction_type": "B2B",
            "seller_tin": "99999999-001",
            "seller_name": "Supplier Corp",
        })]
        result = validator.validate(submitted, preview, direction="INBOUND")
        assert result.is_valid


# ── Line Items ───────────────────────────────────────────────────────────


class TestLineItemDiffs:
    def _make_line_item(self, overrides=None, provenance=None):
        item = {
            "line_number": 1,
            "description": "Widget X",
            "quantity": 10,
            "unit_price": 500.00,
            "line_total": 5000.00,
            "tax_amount": 375.00,
            "hsn_code": "1905.90",
            "service_code": "",
            "product_category": "Biscuits",
        }
        if overrides:
            item.update(overrides)
        if provenance is not None:
            item["__provenance__"] = provenance
        return item

    def test_change_line_item_amount_rejected(self, validator):
        preview = [_make_row({"line_items": [self._make_line_item()]})]
        submitted = [_make_row({"line_items": [
            self._make_line_item({"quantity": 999})
        ]})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert result.violations[0].field == "quantity"

    def test_change_line_item_description_rejected(self, validator):
        preview = [_make_row({"line_items": [self._make_line_item()]})]
        submitted = [_make_row({"line_items": [
            self._make_line_item({"description": "Changed!"})
        ]})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid

    def test_change_hsn_code_his_provenance_accepted(self, validator):
        prov = {"hsn_code": HIS}
        preview = [_make_row({"line_items": [
            self._make_line_item(provenance=prov)
        ]})]
        submitted = [_make_row({"line_items": [
            self._make_line_item({"hsn_code": "2106.90"}, provenance=prov)
        ]})]
        result = validator.validate(submitted, preview)
        assert result.is_valid

    def test_change_hsn_code_original_provenance_rejected(self, validator):
        prov = {"hsn_code": ORIGINAL}
        preview = [_make_row({"line_items": [
            self._make_line_item(provenance=prov)
        ]})]
        submitted = [_make_row({"line_items": [
            self._make_line_item({"hsn_code": "9999.99"}, provenance=prov)
        ]})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid

    def test_line_item_count_mismatch_rejected(self, validator):
        preview = [_make_row({"line_items": [self._make_line_item()]})]
        submitted = [_make_row({"line_items": [
            self._make_line_item(),
            self._make_line_item({"line_number": 2}),
        ]})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert result.violations[0].reason == "line_item_count_mismatch"


# ── Graceful Degradation ─────────────────────────────────────────────────


class TestGracefulDegradation:
    def test_no_provenance_warns_and_allows_non_never_edits(self, validator):
        """When provenance metadata is absent, non-NEVER fields are editable."""
        preview = [_make_row()]  # No __provenance__
        submitted = [_make_row({"buyer_lga_code": "LGA-NEW"})]
        result = validator.validate(submitted, preview)
        assert result.is_valid
        assert len(result.warnings) > 0
        assert "no __provenance__" in result.warnings[0].lower()

    def test_no_provenance_still_blocks_never_fields(self, validator):
        """NEVER_EDITABLE fields are blocked even without provenance."""
        preview = [_make_row()]
        submitted = [_make_row({"total_amount": 99999})]
        result = validator.validate(submitted, preview)
        assert not result.is_valid


# ── Multiple Violations ──────────────────────────────────────────────────


class TestMultipleViolations:
    def test_aggregates_multiple_violations(self, validator):
        preview = [_make_row()]
        submitted = [_make_row({
            "invoice_number": "CHANGED",
            "total_amount": 99999,
            "issue_date": "2099-01-01",
        })]
        result = validator.validate(submitted, preview)
        assert not result.is_valid
        assert len(result.violations) == 3


# ── Violation Serialization ──────────────────────────────────────────────


class TestViolationSerialization:
    def test_to_dict(self):
        v = EditViolation(
            field="total_amount",
            provenance=ORIGINAL,
            preview_value=53750.00,
            submitted_value=99999.99,
            reason="never_editable_field",
            invoice_index=0,
        )
        d = v.to_dict()
        assert d["field"] == "total_amount"
        assert d["reason"] == "never_editable_field"
        assert d["invoice_index"] == 0
