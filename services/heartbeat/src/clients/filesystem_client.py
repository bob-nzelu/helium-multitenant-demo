"""
Filesystem Blob Client

Async blob storage backed by local filesystem. Primary blob storage
backend for HeartBeat.

Path convention:
    object_name:        "files_blob/{blob_uuid}-{filename}"
    Filesystem path:    "{root}/files_blob/{blob_uuid}-{filename}"
    Metadata sidecar:   "{root}/files_blob/{blob_uuid}-{filename}.metadata.json"
"""

import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class FilesystemBlobClient:
    """
    Filesystem-backed blob storage client.

    All methods are async (wrapping sync I/O via asyncio.to_thread).
    """

    def __init__(self, root_path: str):
        """
        Initialize filesystem blob client.

        Args:
            root_path: Root directory for blob storage (e.g., "data/dev_blobs")
        """
        self.root = root_path
        os.makedirs(root_path, exist_ok=True)
        logger.info(f"Filesystem blob client initialized at {root_path}")

    def _object_path(self, object_name: str) -> str:
        """
        Convert object_name to filesystem path.

        Handles forward-slash → os.sep conversion for Windows.
        """
        safe_name = object_name.replace("/", os.sep)
        return os.path.join(self.root, safe_name)

    async def put_blob(
        self,
        object_name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Write blob data to filesystem.

        Creates parent directories as needed. Also writes a JSON metadata
        sidecar file alongside the blob.

        Returns the object_name (same as input).
        """
        def _put():
            path = self._object_path(object_name)
            os.makedirs(os.path.dirname(path), exist_ok=True)

            # Write blob data
            with open(path, "wb") as f:
                f.write(data)

            # Write metadata sidecar
            meta_path = path + ".metadata.json"
            meta = {
                "object_name": object_name,
                "content_type": content_type,
                "size_bytes": len(data),
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            return object_name

        return await asyncio.to_thread(_put)

    async def get_blob(self, object_name: str) -> bytes:
        """
        Read blob data from filesystem.

        Raises FileNotFoundError if blob does not exist.
        """
        def _get():
            path = self._object_path(object_name)
            if not os.path.exists(path):
                raise FileNotFoundError(f"Blob not found: {object_name}")
            with open(path, "rb") as f:
                return f.read()

        return await asyncio.to_thread(_get)

    async def delete_blob(self, object_name: str) -> None:
        """
        Delete blob and its metadata sidecar from filesystem.

        Silently succeeds if the blob does not exist.
        """
        def _delete():
            path = self._object_path(object_name)
            if os.path.exists(path):
                os.remove(path)
            meta_path = path + ".metadata.json"
            if os.path.exists(meta_path):
                os.remove(meta_path)

        await asyncio.to_thread(_delete)

    async def blob_exists(self, object_name: str) -> bool:
        """Check if a blob exists on the filesystem."""
        def _exists():
            return os.path.exists(self._object_path(object_name))

        return await asyncio.to_thread(_exists)

    async def is_healthy(self) -> bool:
        """
        Check if the filesystem storage is accessible.

        Returns True if the root directory exists and is writable.
        """
        return os.path.isdir(self.root)


# ── Singleton ──────────────────────────────────────────────────────────

_filesystem_instance: Optional[FilesystemBlobClient] = None


def get_filesystem_client(root_path: Optional[str] = None) -> Optional[FilesystemBlobClient]:
    """Get or create singleton FilesystemBlobClient."""
    global _filesystem_instance
    if _filesystem_instance is None and root_path:
        _filesystem_instance = FilesystemBlobClient(root_path)
    return _filesystem_instance


def set_filesystem_client(client: Optional[FilesystemBlobClient]) -> None:
    """Override filesystem client singleton (for testing)."""
    global _filesystem_instance
    _filesystem_instance = client


def reset_filesystem_client() -> None:
    """Reset filesystem client singleton (for testing)."""
    global _filesystem_instance
    _filesystem_instance = None
