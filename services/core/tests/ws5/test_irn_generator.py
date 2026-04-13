"""Tests for WS5 IRN Generator."""

import pytest
from datetime import date

from src.finalize.irn_generator import generate_irn, validate_irn, IRNError


class TestGenerateIRN:
    def test_basic_generation(self):
        irn = generate_irn("INV001", "94ND90NR", "2024-10-24")
        assert irn == "INV001-94ND90NR-20241024"

    def test_with_date_object(self):
        irn = generate_irn("INV001", "94ND90NR", date(2024, 10, 24))
        assert irn == "INV001-94ND90NR-20241024"

    def test_strips_whitespace(self):
        irn = generate_irn(" INV001 ", " 94ND90NR ", "2024-10-24")
        assert irn == "INV001-94ND90NR-20241024"


class TestGenerateIRNValidation:
    def test_empty_invoice_number_raises(self):
        with pytest.raises(IRNError, match="invoice_number is required"):
            generate_irn("", "94ND90NR", "2024-10-24")

    def test_special_chars_in_invoice_number_raises(self):
        with pytest.raises(IRNError, match="alphanumeric"):
            generate_irn("INV-001", "94ND90NR", "2024-10-24")

    def test_spaces_in_invoice_number_raises(self):
        with pytest.raises(IRNError, match="alphanumeric"):
            generate_irn("INV 001", "94ND90NR", "2024-10-24")

    def test_empty_service_id_raises(self):
        with pytest.raises(IRNError, match="service_id is required"):
            generate_irn("INV001", "", "2024-10-24")

    def test_short_service_id_raises(self):
        with pytest.raises(IRNError, match="exactly 8 characters"):
            generate_irn("INV001", "SHORT", "2024-10-24")

    def test_long_service_id_raises(self):
        with pytest.raises(IRNError, match="exactly 8 characters"):
            generate_irn("INV001", "TOOLONGID", "2024-10-24")

    def test_bad_date_format_raises(self):
        with pytest.raises(IRNError, match="ISO format"):
            generate_irn("INV001", "94ND90NR", "24-10-2024")

    def test_future_date_raises(self):
        with pytest.raises(IRNError, match="future-dated"):
            generate_irn("INV001", "94ND90NR", "2099-01-01")

    def test_invalid_date_type_raises(self):
        with pytest.raises(IRNError, match="must be str or date"):
            generate_irn("INV001", "94ND90NR", 12345)


class TestValidateIRN:
    def test_valid_irn(self):
        assert validate_irn("INV001-94ND90NR-20241024") is True

    def test_too_few_parts(self):
        assert validate_irn("INV001-94ND90NR") is False

    def test_too_many_parts(self):
        assert validate_irn("INV-001-94ND90NR-20241024") is False

    def test_bad_service_id_length(self):
        assert validate_irn("INV001-SHORT-20241024") is False

    def test_bad_datestamp(self):
        assert validate_irn("INV001-94ND90NR-99999999") is False

    def test_non_numeric_datestamp(self):
        assert validate_irn("INV001-94ND90NR-2024ABCD") is False
