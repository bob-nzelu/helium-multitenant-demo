"""Tests for file type detection."""

import gzip
import io
import json

import pytest

from src.errors import ValidationError
from src.ingestion.file_detector import detect_file_type
from src.ingestion.models import FileType


class TestMagicByteDetection:
    def test_pdf(self):
        assert detect_file_type(b"%PDF-1.4 content", "doc.pdf") == FileType.PDF

    def test_pdf_ignores_extension(self):
        assert detect_file_type(b"%PDF-1.4 content", "doc.txt") == FileType.PDF

    def test_xlsx(self, sample_xlsx_bytes):
        assert detect_file_type(sample_xlsx_bytes, "data.xlsx") == FileType.EXCEL

    def test_xml(self):
        assert detect_file_type(b"<?xml version='1.0'?><root/>", "doc.xml") == FileType.XML

    def test_xml_without_declaration(self):
        assert detect_file_type(b"<Invoice><ID>1</ID></Invoice>", "inv.xml") == FileType.XML

    def test_json_object(self):
        assert detect_file_type(b'{"key": "val"}', "data.json") == FileType.JSON

    def test_json_array(self):
        assert detect_file_type(b'[{"a": 1}]', "data.json") == FileType.JSON

    def test_json_with_bom(self):
        content = b"\xef\xbb\xbf" + b'{"key": "val"}'
        assert detect_file_type(content, "data.json") == FileType.JSON

    def test_gzip_detected_as_hlmz(self):
        data = gzip.compress(b'{"hlm_version": "1.0"}')
        assert detect_file_type(data, "data.bin") == FileType.HLMZ


class TestExtensionFallback:
    def test_hlm_extension(self):
        content = json.dumps({"hlm_version": "1.0"}).encode()
        assert detect_file_type(content, "invoice.hlm") == FileType.HLM

    def test_hlmz_extension(self):
        data = gzip.compress(b"test")
        assert detect_file_type(data, "invoice.hlmz") == FileType.HLMZ

    def test_csv_extension(self):
        assert detect_file_type(b"a,b,c\n1,2,3\n", "data.csv") == FileType.CSV


class TestCSVHeuristic:
    def test_csv_without_extension(self):
        content = b"a,b,c\n1,2,3\n4,5,6\n"
        assert detect_file_type(content, "datafile") == FileType.CSV

    def test_semicolon_csv(self):
        content = b"a;b;c\n1;2;3\n4;5;6\n"
        assert detect_file_type(content, "datafile") == FileType.CSV


class TestErrors:
    def test_empty_content(self):
        with pytest.raises(ValidationError):
            detect_file_type(b"", "empty.txt")

    def test_unknown_format(self):
        with pytest.raises(ValidationError):
            detect_file_type(b"\x00\x01\x02\x03random", "mystery.bin")
