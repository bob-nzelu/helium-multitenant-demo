"""
WS7: Report generators — one module per report type.

Each generator exposes:
    async def generate(pool, filters, company_id) -> tuple[bytes, str]
        Returns (file_bytes, content_type).
"""
