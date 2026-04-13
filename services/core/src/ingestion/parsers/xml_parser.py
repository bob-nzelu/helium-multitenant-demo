"""XML parser using lxml (XXE-safe)."""

from __future__ import annotations

import time

from src.errors import ValidationError
from src.ingestion.models import FileType, ParseMetadata, ParseResult
from src.ingestion.parsers.base import BaseParser


class XMLParser(BaseParser):
    """Parse XML files with namespace awareness and XXE prevention."""

    async def parse(self, content: bytes, filename: str) -> ParseResult:
        from lxml import etree

        start = time.monotonic()

        # XXE-safe parser
        parser = etree.XMLParser(resolve_entities=False, no_network=True)

        try:
            root = etree.fromstring(content, parser=parser)
        except etree.XMLSyntaxError as e:
            raise ValidationError(
                f"Invalid XML in '{filename}': {e}",
                details=[{"field": "content", "message": str(e)}],
            )

        rows = [_element_to_dict(root)]

        elapsed = (time.monotonic() - start) * 1000
        return ParseResult(
            file_type=FileType.XML.value,
            raw_data=rows,
            metadata=ParseMetadata(
                parser_type="xml",
                original_filename=filename,
                file_size_bytes=len(content),
                row_count=len(rows),
                duration_ms=elapsed,
            ),
        )


def _element_to_dict(elem) -> dict:
    """Recursively convert an lxml element to a dict, stripping namespaces."""
    tag = _strip_ns(elem.tag)
    result: dict = {}

    # Attributes
    if elem.attrib:
        result["@attributes"] = dict(elem.attrib)

    # Children
    children: dict[str, list] = {}
    for child in elem:
        # Skip non-element nodes (comments, processing instructions, entities)
        if not isinstance(child.tag, str):
            continue
        child_tag = _strip_ns(child.tag)
        child_data = _element_to_dict(child)
        children.setdefault(child_tag, []).append(child_data.get(child_tag, child_data))

    # Flatten single-element lists
    for key, vals in children.items():
        result[key] = vals[0] if len(vals) == 1 else vals

    # Text content
    if elem.text and elem.text.strip():
        if result:
            result["#text"] = elem.text.strip()
        else:
            return {tag: elem.text.strip()}

    return {tag: result} if result else {tag: None}


def _strip_ns(tag: str) -> str:
    """Strip XML namespace prefix: {http://...}LocalName → LocalName."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag
