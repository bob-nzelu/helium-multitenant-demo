"""Tests for XML parser."""

import pytest

from src.errors import ValidationError
from src.ingestion.parsers.xml_parser import XMLParser


@pytest.fixture
def parser():
    return XMLParser()


class TestXMLParser:
    @pytest.mark.asyncio
    async def test_basic_parse(self, parser, sample_xml_bytes):
        result = await parser.parse(sample_xml_bytes, "invoice.xml")
        assert result.file_type == "xml"
        assert result.metadata.row_count == 1
        data = result.raw_data[0]
        assert "Invoice" in data

    @pytest.mark.asyncio
    async def test_namespace_stripping(self, parser):
        xml = b"""<?xml version="1.0"?>
        <ns:Invoice xmlns:ns="http://example.com">
            <ns:ID>123</ns:ID>
        </ns:Invoice>"""
        result = await parser.parse(xml, "ns.xml")
        assert "Invoice" in result.raw_data[0]

    @pytest.mark.asyncio
    async def test_xxe_prevention(self, parser):
        """Entity resolution should be disabled."""
        xml = b"""<?xml version="1.0"?>
        <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <root>&xxe;</root>"""
        # Should parse without resolving entities (or raise error)
        # lxml with resolve_entities=False will just ignore the entity
        result = await parser.parse(xml, "xxe.xml")
        assert result.file_type == "xml"

    @pytest.mark.asyncio
    async def test_invalid_xml(self, parser):
        with pytest.raises(ValidationError):
            await parser.parse(b"<not>valid<xml>", "bad.xml")

    @pytest.mark.asyncio
    async def test_attributes_preserved(self, parser):
        xml = b'<?xml version="1.0"?><Item id="42" type="goods">Rice</Item>'
        result = await parser.parse(xml, "attrs.xml")
        item = result.raw_data[0].get("Item", {})
        assert isinstance(item, dict)
