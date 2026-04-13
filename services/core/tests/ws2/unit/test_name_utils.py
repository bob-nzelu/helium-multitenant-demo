"""Unit tests for name normalization and fuzzy matching."""

import pytest

from src.processing.name_utils import levenshtein_ratio, normalize_name


class TestNormalizeName:
    def test_uppercase(self):
        assert normalize_name("dangote cement") == "DANGOTE CEMENT"

    def test_strip_ltd(self):
        assert normalize_name("Dangote Cement Ltd") == "DANGOTE CEMENT"

    def test_strip_plc(self):
        assert normalize_name("Dangote Industries PLC") == "DANGOTE INDUSTRIES"

    def test_strip_limited(self):
        assert normalize_name("First Bank Limited") == "FIRST BANK"

    def test_strip_incorporated(self):
        assert normalize_name("Apple Incorporated") == "APPLE"

    def test_strip_nig(self):
        assert normalize_name("Nestle Nigeria") == "NESTLE"
        assert normalize_name("Nestle Nig") == "NESTLE"

    def test_replace_ampersand(self):
        assert normalize_name("Ernst & Young") == "ERNST AND YOUNG"

    def test_collapse_whitespace(self):
        assert normalize_name("  Dangote   Cement  ") == "DANGOTE CEMENT"

    def test_remove_punctuation(self):
        result = normalize_name("Dangote (Cement) Corp.")
        assert "(" not in result
        assert ")" not in result

    def test_preserve_hyphens(self):
        assert normalize_name("Coca-Cola") == "COCA-COLA"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_none_like(self):
        assert normalize_name("") == ""

    def test_multiple_suffixes(self):
        # Only trailing suffix stripped — "Industries" is part of name
        result = normalize_name("Dangote Industries Nigeria Limited")
        # "Limited" at end stripped, then "Nigeria" at end stripped
        assert "DANGOTE INDUSTRIES" in result

    def test_real_nigerian_companies(self):
        assert normalize_name("MTN Nigeria Communications PLC") == "MTN NIGERIA COMMUNICATIONS"
        assert normalize_name("Guaranty Trust Holding Company PLC") == "GUARANTY TRUST HOLDING COMPANY"
        assert normalize_name("BUA Cement PLC") == "BUA CEMENT"

    def test_enterprise_suffix(self):
        # "Enterprises" is a legal suffix — stripped at end
        assert normalize_name("Lagos Enterprises") == "LAGOS"

    def test_group_suffix(self):
        # "Group" is NOT in suffix list — preserved
        assert normalize_name("Oando Group") == "OANDO GROUP"


class TestLevenshteinRatio:
    def test_identical(self):
        assert levenshtein_ratio("DANGOTE CEMENT", "DANGOTE CEMENT") == 1.0

    def test_empty_strings(self):
        assert levenshtein_ratio("", "") == 0.0
        assert levenshtein_ratio("hello", "") == 0.0
        assert levenshtein_ratio("", "world") == 0.0

    def test_similar(self):
        score = levenshtein_ratio("DANGOTE CEMENT", "DANGOTE CMENT")
        assert 0.85 <= score <= 1.0

    def test_different(self):
        score = levenshtein_ratio("DANGOTE CEMENT", "NESTLE NIGERIA")
        assert score < 0.5

    def test_nigerian_company_variations(self):
        # These should score high after normalization
        n1 = normalize_name("Dangote Cement PLC")
        n2 = normalize_name("Dangote Cement Limited")
        score = levenshtein_ratio(n1, n2)
        assert score >= 0.85

    def test_close_names(self):
        n1 = normalize_name("First Bank of Nigeria")
        n2 = normalize_name("First Bank Nigeria PLC")
        score = levenshtein_ratio(n1, n2)
        assert score >= 0.70

    def test_threshold_boundary(self):
        # Test around 0.85 threshold
        n1 = normalize_name("Access Bank")
        n2 = normalize_name("Access Holdings")
        score = levenshtein_ratio(n1, n2)
        # These are different enough to be below threshold
        assert score < 0.85

    def test_minor_typo(self):
        score = levenshtein_ratio("PORTLAND CEMENT", "PORTLAN CEMENT")
        assert score >= 0.90
