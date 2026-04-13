"""
Tests for FilesystemBlobClient (src/clients/filesystem_client.py)

Uses REAL filesystem via tmp_path — no mocking.

Covers:
- put_blob: writes file + metadata sidecar
- get_blob: reads file bytes
- delete_blob: removes file + sidecar
- blob_exists: checks existence
- is_healthy: directory health check
- Path conversion (forward slash → os.sep)
- Singleton lifecycle
"""

import json
import os
import pytest

from src.clients.filesystem_client import (
    FilesystemBlobClient,
    get_filesystem_client,
    set_filesystem_client,
    reset_filesystem_client,
)


@pytest.fixture
def fs_client(tmp_path):
    """Create a FilesystemBlobClient with a temp root directory."""
    return FilesystemBlobClient(str(tmp_path / "blob_root"))


@pytest.fixture
def sample_blob_name():
    """Standard test object name."""
    return "files_blob/abc123-invoice.pdf"


@pytest.fixture
def sample_data():
    """Sample file content."""
    return b"PDF file content here - this is a test document."


# ── put_blob Tests ───────────────────────────────────────────────────


class TestPutBlob:
    """Writing blobs to filesystem."""

    @pytest.mark.asyncio
    async def test_put_blob_creates_file(self, fs_client, sample_blob_name, sample_data):
        """put_blob creates the file on disk."""
        result = await fs_client.put_blob(sample_blob_name, sample_data, "application/pdf")
        assert result == sample_blob_name

        path = fs_client._object_path(sample_blob_name)
        assert os.path.exists(path)

        with open(path, "rb") as f:
            assert f.read() == sample_data

    @pytest.mark.asyncio
    async def test_put_blob_creates_metadata_sidecar(self, fs_client, sample_blob_name, sample_data):
        """put_blob also creates a .metadata.json sidecar."""
        await fs_client.put_blob(sample_blob_name, sample_data, "application/pdf")

        meta_path = fs_client._object_path(sample_blob_name) + ".metadata.json"
        assert os.path.exists(meta_path)

        with open(meta_path, "r") as f:
            meta = json.load(f)

        assert meta["object_name"] == sample_blob_name
        assert meta["content_type"] == "application/pdf"
        assert meta["size_bytes"] == len(sample_data)

    @pytest.mark.asyncio
    async def test_put_blob_creates_parent_directories(self, fs_client):
        """Nested object names create parent dirs automatically."""
        deep_name = "files_blob/subdir/nested/file.txt"
        await fs_client.put_blob(deep_name, b"data")

        path = fs_client._object_path(deep_name)
        assert os.path.exists(path)

    @pytest.mark.asyncio
    async def test_put_blob_overwrites_existing(self, fs_client, sample_blob_name):
        """Writing to same object_name overwrites the file."""
        await fs_client.put_blob(sample_blob_name, b"original")
        await fs_client.put_blob(sample_blob_name, b"updated")

        data = await fs_client.get_blob(sample_blob_name)
        assert data == b"updated"

    @pytest.mark.asyncio
    async def test_put_blob_default_content_type(self, fs_client, sample_blob_name):
        """Default content_type is application/octet-stream."""
        await fs_client.put_blob(sample_blob_name, b"data")

        meta_path = fs_client._object_path(sample_blob_name) + ".metadata.json"
        with open(meta_path, "r") as f:
            meta = json.load(f)
        assert meta["content_type"] == "application/octet-stream"


# ── get_blob Tests ───────────────────────────────────────────────────


class TestGetBlob:
    """Reading blobs from filesystem."""

    @pytest.mark.asyncio
    async def test_get_blob_returns_bytes(self, fs_client, sample_blob_name, sample_data):
        """get_blob returns the exact bytes written."""
        await fs_client.put_blob(sample_blob_name, sample_data)
        result = await fs_client.get_blob(sample_blob_name)
        assert result == sample_data

    @pytest.mark.asyncio
    async def test_get_blob_not_found_raises(self, fs_client):
        """get_blob raises FileNotFoundError for missing blob."""
        with pytest.raises(FileNotFoundError, match="Blob not found"):
            await fs_client.get_blob("files_blob/nonexistent.pdf")

    @pytest.mark.asyncio
    async def test_get_blob_empty_file(self, fs_client, sample_blob_name):
        """get_blob works with empty files."""
        await fs_client.put_blob(sample_blob_name, b"")
        result = await fs_client.get_blob(sample_blob_name)
        assert result == b""

    @pytest.mark.asyncio
    async def test_get_blob_large_file(self, fs_client, sample_blob_name):
        """get_blob handles large files."""
        large_data = b"x" * (1024 * 1024)  # 1MB
        await fs_client.put_blob(sample_blob_name, large_data)
        result = await fs_client.get_blob(sample_blob_name)
        assert len(result) == 1024 * 1024


# ── delete_blob Tests ────────────────────────────────────────────────


class TestDeleteBlob:
    """Deleting blobs from filesystem."""

    @pytest.mark.asyncio
    async def test_delete_blob_removes_file(self, fs_client, sample_blob_name, sample_data):
        """delete_blob removes the file from disk."""
        await fs_client.put_blob(sample_blob_name, sample_data)
        await fs_client.delete_blob(sample_blob_name)

        assert not os.path.exists(fs_client._object_path(sample_blob_name))

    @pytest.mark.asyncio
    async def test_delete_blob_removes_sidecar(self, fs_client, sample_blob_name, sample_data):
        """delete_blob also removes the .metadata.json sidecar."""
        await fs_client.put_blob(sample_blob_name, sample_data)
        await fs_client.delete_blob(sample_blob_name)

        meta_path = fs_client._object_path(sample_blob_name) + ".metadata.json"
        assert not os.path.exists(meta_path)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_blob_no_error(self, fs_client):
        """Deleting a nonexistent blob succeeds silently."""
        await fs_client.delete_blob("files_blob/ghost.pdf")
        # No exception raised

    @pytest.mark.asyncio
    async def test_delete_then_get_raises(self, fs_client, sample_blob_name, sample_data):
        """After deleting, get_blob raises FileNotFoundError."""
        await fs_client.put_blob(sample_blob_name, sample_data)
        await fs_client.delete_blob(sample_blob_name)

        with pytest.raises(FileNotFoundError):
            await fs_client.get_blob(sample_blob_name)


# ── blob_exists Tests ────────────────────────────────────────────────


class TestBlobExists:
    """Checking blob existence."""

    @pytest.mark.asyncio
    async def test_exists_after_put(self, fs_client, sample_blob_name, sample_data):
        """blob_exists returns True after put."""
        await fs_client.put_blob(sample_blob_name, sample_data)
        assert await fs_client.blob_exists(sample_blob_name) is True

    @pytest.mark.asyncio
    async def test_not_exists_before_put(self, fs_client, sample_blob_name):
        """blob_exists returns False for non-existent blob."""
        assert await fs_client.blob_exists(sample_blob_name) is False

    @pytest.mark.asyncio
    async def test_not_exists_after_delete(self, fs_client, sample_blob_name, sample_data):
        """blob_exists returns False after delete."""
        await fs_client.put_blob(sample_blob_name, sample_data)
        await fs_client.delete_blob(sample_blob_name)
        assert await fs_client.blob_exists(sample_blob_name) is False


# ── is_healthy Tests ─────────────────────────────────────────────────


class TestIsHealthy:
    """Filesystem health check."""

    @pytest.mark.asyncio
    async def test_healthy_with_valid_root(self, fs_client):
        """is_healthy returns True when root dir exists."""
        assert await fs_client.is_healthy() is True

    @pytest.mark.asyncio
    async def test_unhealthy_with_missing_root(self, tmp_path):
        """is_healthy returns False when root dir is gone."""
        root = str(tmp_path / "deleted_root")
        client = FilesystemBlobClient(root)
        os.rmdir(root)  # Remove the dir
        assert await client.is_healthy() is False


# ── Path Conversion Tests ────────────────────────────────────────────


class TestPathConversion:
    """Forward-slash to os.sep conversion for Windows compatibility."""

    def test_object_path_with_forward_slashes(self, fs_client):
        """Forward slashes are converted to os.sep."""
        path = fs_client._object_path("files_blob/uuid-file.pdf")
        expected = os.path.join(fs_client.root, "files_blob", "uuid-file.pdf")
        assert path == expected

    def test_object_path_preserves_native_sep(self, fs_client):
        """Native os.sep paths work too."""
        native_name = os.path.join("files_blob", "uuid-file.pdf")
        path = fs_client._object_path(native_name)
        assert os.path.basename(path) == "uuid-file.pdf"


# ── Singleton Tests ──────────────────────────────────────────────────


class TestSingleton:
    """Singleton lifecycle."""

    def test_reset_clears_singleton(self):
        """reset_filesystem_client clears the instance."""
        reset_filesystem_client()
        client = get_filesystem_client()
        assert client is None

    def test_get_creates_with_root(self, tmp_path):
        """get_filesystem_client creates instance when root is provided."""
        reset_filesystem_client()
        root = str(tmp_path / "singleton_test")
        client = get_filesystem_client(root)
        assert client is not None
        assert isinstance(client, FilesystemBlobClient)
        reset_filesystem_client()

    def test_set_overrides_singleton(self, fs_client):
        """set_filesystem_client overrides the singleton."""
        reset_filesystem_client()
        set_filesystem_client(fs_client)
        assert get_filesystem_client() is fs_client
        reset_filesystem_client()

    def test_get_without_root_returns_none(self):
        """get_filesystem_client without root returns None (not created)."""
        reset_filesystem_client()
        assert get_filesystem_client() is None
