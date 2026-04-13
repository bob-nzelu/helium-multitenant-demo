"""
Edit Validator — Diff engine for .hlm vs preview .hlx.

Compares a finalized .hlm submission from Float SDK against the stored
preview .hlx in HeartBeat. Verifies that only editable fields were changed
based on per-field provenance metadata.

Rule: Source data is sacred. Only enriched, missing, or low-confidence
fields are editable.

See: HLX_FORMAT.md v1.1 Sections 10-11
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.finalize.provenance import (
    ALWAYS_EDITABLE_INVOICE_FIELDS,
    B2B_B2G_TYPES,
    B2C_UPGRADE_REQUIRED_FIELDS,
    EDITABLE_PROVENANCE,
    EDITABLE_REFERENCE_TYPES,
    LINE_ITEM_CLASSIFICATION_FIELDS,
    LOW_CONFIDENCE_THRESHOLD,
    NEVER_EDITABLE_INVOICE_FIELDS,
    NEVER_EDITABLE_LINE_ITEM_FIELDS,
    get_tenant_party_fields,
    is_field_editable,
)

logger = logging.getLogger(__name__)


# ── Result Types ─────────────────────────────────────────────────────────


@dataclass
class EditViolation:
    """A single illegal edit detected during diff."""

    field: str
    provenance: str | None
    preview_value: Any
    submitted_value: Any
    reason: str
    invoice_index: int | None = None
    line_item_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "field": self.field,
            "provenance": self.provenance,
            "preview_value": str(self.preview_value),
            "submitted_value": str(self.submitted_value),
            "reason": self.reason,
        }
        if self.invoice_index is not None:
            d["invoice_index"] = self.invoice_index
        if self.line_item_index is not None:
            d["line_item_index"] = self.line_item_index
        return d


@dataclass
class AcceptedChange:
    """A legal edit accepted during diff."""

    field: str
    old_value: Any
    new_value: Any
    provenance: str | None
    invoice_index: int | None = None
    line_item_index: int | None = None


@dataclass
class EditValidationResult:
    """Result of diffing submitted .hlm against preview .hlx."""

    is_valid: bool
    violations: list[EditViolation] = field(default_factory=list)
    accepted_changes: list[AcceptedChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Validator ────────────────────────────────────────────────────────────


class EditValidator:
    """Diff .hlm submission against preview .hlx, verify all changes are legal."""

    def validate(
        self,
        submitted_rows: list[dict[str, Any]],
        preview_rows: list[dict[str, Any]],
        direction: str = "OUTBOUND",
    ) -> EditValidationResult:
        """Compare submitted .hlm rows against preview .hlx rows.

        Args:
            submitted_rows: Rows from the finalized .hlm sent by Float SDK.
            preview_rows: Rows from the preview .hlx stored in HeartBeat.
                Each row may contain a ``__provenance__`` dict mapping
                field names to provenance values.
            direction: Invoice direction (OUTBOUND or INBOUND).

        Returns:
            EditValidationResult with violations, accepted changes, and warnings.
        """
        violations: list[EditViolation] = []
        accepted: list[AcceptedChange] = []
        warnings: list[str] = []

        if len(submitted_rows) != len(preview_rows):
            violations.append(
                EditViolation(
                    field="__row_count__",
                    provenance=None,
                    preview_value=len(preview_rows),
                    submitted_value=len(submitted_rows),
                    reason="row_count_mismatch",
                )
            )
            return EditValidationResult(
                is_valid=False, violations=violations, warnings=warnings
            )

        tenant_fields = get_tenant_party_fields(direction)
        provenance_missing_warned = False

        for idx, (submitted, preview) in enumerate(
            zip(submitted_rows, preview_rows)
        ):
            row_provenance = preview.get("__provenance__", {})

            if not row_provenance and not provenance_missing_warned:
                warnings.append(
                    "Preview data has no __provenance__ metadata. "
                    "All non-NEVER fields treated as editable (graceful degradation)."
                )
                provenance_missing_warned = True

            # Diff invoice-level fields
            self._diff_invoice_fields(
                idx,
                submitted,
                preview,
                row_provenance,
                direction,
                tenant_fields,
                violations,
                accepted,
            )

            # Diff line items if present
            submitted_items = submitted.get("line_items", [])
            preview_items = preview.get("line_items", [])
            if submitted_items or preview_items:
                self._diff_line_items(
                    idx,
                    submitted_items,
                    preview_items,
                    row_provenance,
                    violations,
                    accepted,
                )

            # Check transaction_type upgrade rules
            self._check_transaction_type_rules(
                idx, submitted, preview, direction, violations
            )

        return EditValidationResult(
            is_valid=len(violations) == 0,
            violations=violations,
            accepted_changes=accepted,
            warnings=warnings,
        )

    def _diff_invoice_fields(
        self,
        invoice_index: int,
        submitted: dict[str, Any],
        preview: dict[str, Any],
        provenance: dict[str, str],
        direction: str,
        tenant_fields: frozenset[str],
        violations: list[EditViolation],
        accepted: list[AcceptedChange],
    ) -> None:
        """Diff invoice-level fields (excluding line_items and __provenance__)."""
        skip_keys = {"line_items", "__provenance__", "__IS_NEW__", "_row_index"}

        all_keys = set(submitted.keys()) | set(preview.keys())
        for key in all_keys:
            if key in skip_keys:
                continue

            submitted_val = submitted.get(key)
            preview_val = preview.get(key)

            if self._values_equal(submitted_val, preview_val):
                continue

            # Value changed — check if allowed
            field_provenance = provenance.get(key)
            confidence = self._get_confidence(preview, key)

            if key in NEVER_EDITABLE_INVOICE_FIELDS:
                violations.append(
                    EditViolation(
                        field=key,
                        provenance=field_provenance,
                        preview_value=preview_val,
                        submitted_value=submitted_val,
                        reason="never_editable_field",
                        invoice_index=invoice_index,
                    )
                )
            elif key in tenant_fields:
                violations.append(
                    EditViolation(
                        field=key,
                        provenance=field_provenance,
                        preview_value=preview_val,
                        submitted_value=submitted_val,
                        reason="tenant_party_field",
                        invoice_index=invoice_index,
                    )
                )
            elif key in ALWAYS_EDITABLE_INVOICE_FIELDS:
                accepted.append(
                    AcceptedChange(
                        field=key,
                        old_value=preview_val,
                        new_value=submitted_val,
                        provenance=field_provenance,
                        invoice_index=invoice_index,
                    )
                )
            elif is_field_editable(
                key, field_provenance, confidence, direction
            ):
                accepted.append(
                    AcceptedChange(
                        field=key,
                        old_value=preview_val,
                        new_value=submitted_val,
                        provenance=field_provenance,
                        invoice_index=invoice_index,
                    )
                )
            else:
                violations.append(
                    EditViolation(
                        field=key,
                        provenance=field_provenance,
                        preview_value=preview_val,
                        submitted_value=submitted_val,
                        reason="non_editable_provenance",
                        invoice_index=invoice_index,
                    )
                )

    def _diff_line_items(
        self,
        invoice_index: int,
        submitted_items: list[dict[str, Any]],
        preview_items: list[dict[str, Any]],
        invoice_provenance: dict[str, str],
        violations: list[EditViolation],
        accepted: list[AcceptedChange],
    ) -> None:
        """Diff line items within an invoice."""
        if len(submitted_items) != len(preview_items):
            violations.append(
                EditViolation(
                    field="line_items.__count__",
                    provenance=None,
                    preview_value=len(preview_items),
                    submitted_value=len(submitted_items),
                    reason="line_item_count_mismatch",
                    invoice_index=invoice_index,
                )
            )
            return

        for li_idx, (sub_item, prev_item) in enumerate(
            zip(submitted_items, preview_items)
        ):
            item_provenance = prev_item.get("__provenance__", {})

            for key in set(sub_item.keys()) | set(prev_item.keys()):
                if key in ("__provenance__", "__IS_NEW__"):
                    continue

                sub_val = sub_item.get(key)
                prev_val = prev_item.get(key)

                if self._values_equal(sub_val, prev_val):
                    continue

                field_prov = item_provenance.get(key)
                confidence = self._get_confidence(prev_item, key)

                if key in NEVER_EDITABLE_LINE_ITEM_FIELDS:
                    violations.append(
                        EditViolation(
                            field=key,
                            provenance=field_prov,
                            preview_value=prev_val,
                            submitted_value=sub_val,
                            reason="never_editable_line_item_field",
                            invoice_index=invoice_index,
                            line_item_index=li_idx,
                        )
                    )
                elif key in LINE_ITEM_CLASSIFICATION_FIELDS:
                    if is_field_editable(
                        key, field_prov, confidence, "OUTBOUND",
                        is_line_item=True,
                    ):
                        accepted.append(
                            AcceptedChange(
                                field=key,
                                old_value=prev_val,
                                new_value=sub_val,
                                provenance=field_prov,
                                invoice_index=invoice_index,
                                line_item_index=li_idx,
                            )
                        )
                    else:
                        violations.append(
                            EditViolation(
                                field=key,
                                provenance=field_prov,
                                preview_value=prev_val,
                                submitted_value=sub_val,
                                reason="non_editable_provenance",
                                invoice_index=invoice_index,
                                line_item_index=li_idx,
                            )
                        )
                else:
                    # Unknown line item field changed
                    violations.append(
                        EditViolation(
                            field=key,
                            provenance=field_prov,
                            preview_value=prev_val,
                            submitted_value=sub_val,
                            reason="unknown_line_item_field",
                            invoice_index=invoice_index,
                            line_item_index=li_idx,
                        )
                    )

    def _check_transaction_type_rules(
        self,
        invoice_index: int,
        submitted: dict[str, Any],
        preview: dict[str, Any],
        direction: str,
        violations: list[EditViolation],
    ) -> None:
        """Enforce B2C -> B2B/B2G upgrade requires counterparty details.

        The counterparty depends on direction:
          OUTBOUND: counterparty is buyer  → check buyer_tin, buyer_name
          INBOUND:  counterparty is seller → check seller_tin, seller_name
        """
        old_type = preview.get("transaction_type", "")
        new_type = submitted.get("transaction_type", "")

        if old_type == new_type:
            return

        # B2B <-> B2G: free swap, no checks
        if old_type in B2B_B2G_TYPES and new_type in B2B_B2G_TYPES:
            return

        # B2C -> B2B or B2G: must have counterparty details
        if old_type == "B2C" and new_type in B2B_B2G_TYPES:
            # Counterparty is buyer on OUTBOUND, seller on INBOUND
            prefix = "buyer_" if direction == "OUTBOUND" else "seller_"
            required_fields = {f"{prefix}tin", f"{prefix}name"}

            for required_field in required_fields:
                val = submitted.get(required_field)
                if not val or (isinstance(val, str) and not val.strip()):
                    violations.append(
                        EditViolation(
                            field="transaction_type",
                            provenance=None,
                            preview_value=old_type,
                            submitted_value=new_type,
                            reason=f"b2c_upgrade_requires_{required_field}",
                            invoice_index=invoice_index,
                        )
                    )

    @staticmethod
    def _values_equal(a: Any, b: Any) -> bool:
        """Compare two values, treating None and empty string as equal."""
        if a is None and b is None:
            return True
        if a is None:
            a = ""
        if b is None:
            b = ""
        # Normalize numeric comparisons
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return float(a) == float(b)
        return str(a) == str(b)

    @staticmethod
    def _get_confidence(row: dict[str, Any], field_name: str) -> float | None:
        """Extract confidence score for a field if available."""
        # Check for field-specific confidence (e.g., hsn_code_confidence)
        conf_key = f"{field_name}_confidence"
        if conf_key in row:
            try:
                return float(row[conf_key])
            except (TypeError, ValueError):
                return None
        # Check generic classification_confidence for line items
        if "classification_confidence" in row:
            try:
                return float(row["classification_confidence"])
            except (TypeError, ValueError):
                return None
        return None
