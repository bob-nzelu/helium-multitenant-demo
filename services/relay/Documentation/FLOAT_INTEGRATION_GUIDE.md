## FLOAT UI INTEGRATION GUIDE - RELAY BULK UPLOAD

**Version**: 1.0
**Last Updated**: 2026-01-31
**Status**: INTEGRATION GUIDE FOR FLOAT TEAM
**Target Audience**: Float UI developers

---

## DOCUMENT PURPOSE

This document provides **step-by-step instructions** for integrating Relay Bulk Upload into the Float UI.

Relay Phase 1B is complete. Float UI must:
1. Start Relay as subprocess on Test/Standard tier
2. Implement upload button handler with HMAC signing
3. Display preview data to user
4. Send finalize request with user edits

---

## ARCHITECTURE OVERVIEW

### Test/Standard Tier (Same Machine)

```
┌────────────────────────────────────────────────────────────┐
│ FloatWindow (PySide6 Application)                          │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ On Startup:                                          │ │
│  │ 1. Start Relay subprocess (localhost:8082)          │ │
│  │ 2. Wait for /health (max 6 seconds)                 │ │
│  │ 3. Enable upload button if healthy                  │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Upload Button Clicked:                               │ │
│  │ 1. Show overlay_2 + loader                          │ │
│  │ 2. Call RelayClient.upload_files()                  │ │
│  │ 3. Wait for response (max 5 minutes)                │ │
│  │ 4. Display preview or "queued" message              │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ On Close:                                            │ │
│  │ 1. Stop Relay subprocess (graceful shutdown)        │ │
│  └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### Pro/Enterprise Tier (Remote Relay)

```
┌────────────────────────────────────────────────────────────┐
│ FloatWindow (PySide6 Application)                          │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ Upload Button Clicked:                               │ │
│  │ 1. Show overlay_2 + loader                          │ │
│  │ 2. Call RelayClient.upload_files()                  │ │
│  │    → https://relay-bulk.company.com/api/ingest      │ │
│  │ 3. Wait for response (max 5 minutes)                │ │
│  │ 4. Display preview or "queued" message              │ │
│  └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

---

## STEP 1: START RELAY SUBPROCESS (TEST/STANDARD ONLY)

### 1.1 Import Relay Launcher

```python
# In float.py
from helium.relay.bulk.launcher import RelayBulkLauncher
```

### 1.2 Initialize in FloatWindow.__init__()

```python
class FloatWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # ... existing initialization ...

        # Initialize Relay launcher (Test/Standard tier only)
        if self.config.get("tier") in ["test", "standard"]:
            self.relay_launcher = RelayBulkLauncher(
                port=8082,
                blob_path="/var/helium/files_blob",
                log_file="/var/helium/logs/relay-bulk.log",
            )

            # Start Relay subprocess
            logger.info("Starting Relay Bulk service...")
            success = self.relay_launcher.start(timeout=6)

            if success:
                logger.info("✓ Relay Bulk service started successfully")
                self.relay_available = True
            else:
                logger.warning("✗ Relay Bulk service failed to start - bulk upload disabled")
                self.relay_available = False
                # Show warning to user (optional)
                # QMessageBox.warning(self, "Relay Service Unavailable",
                #     "Bulk upload feature is temporarily unavailable.")
        else:
            # Pro/Enterprise: Relay runs remotely, no subprocess needed
            self.relay_available = True  # Assume remote Relay is available
```

### 1.3 Stop on Float Close

```python
class FloatWindow(QMainWindow):
    def closeEvent(self, event):
        """Handle Float window close event."""

        # Stop Relay subprocess (Test/Standard only)
        if hasattr(self, "relay_launcher") and self.relay_launcher:
            logger.info("Stopping Relay Bulk service...")
            self.relay_launcher.stop(timeout=5)
            logger.info("✓ Relay Bulk service stopped")

        # ... existing cleanup ...

        event.accept()
```

---

## STEP 2: IMPLEMENT HMAC SIGNING

### 2.1 Create HMAC Signer Class

```python
# In src/float/utils/hmac_signer.py

import hashlib
import hmac
from datetime import datetime, timezone


class HMACSigner:
    """
    HMAC-SHA256 signer for Relay API authentication.

    Implements the signature algorithm required by Relay Bulk Upload.
    """

    def __init__(self, api_key: str, secret: str):
        """
        Initialize HMAC signer.

        Args:
            api_key: Client API key (from config.db)
            secret: Shared secret for HMAC (from config.db)
        """
        self.api_key = api_key
        self.secret = secret

    def sign_request(self, body: bytes) -> tuple[str, str]:
        """
        Generate HMAC signature for request.

        Args:
            body: Raw request body bytes

        Returns:
            Tuple of (timestamp, signature)
        """
        # 1. Generate timestamp (ISO 8601 with Z suffix)
        timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

        # 2. Compute body hash
        body_hash = hashlib.sha256(body).hexdigest()

        # 3. Create message
        message = f"{self.api_key}:{timestamp}:{body_hash}"

        # 4. Compute HMAC-SHA256 signature
        signature = hmac.new(
            self.secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return timestamp, signature
```

### 2.2 Load API Credentials from config.db

```python
# In float.py or config.py

def load_relay_credentials() -> tuple[str, str]:
    """
    Load Relay API credentials from config.db.

    Returns:
        Tuple of (api_key, secret)
    """
    # TODO: Query config.db provisioned_users table or relay_credentials table
    # For Phase 1B, use placeholder credentials

    return ("client_api_key_12345", "shared_secret_xyz")
```

---

## STEP 3: IMPLEMENT RELAY CLIENT

### 3.1 Create RelayClient Class

```python
# In src/float/clients/relay_client.py

import logging
import aiohttp
import asyncio
from typing import List, Optional, Dict, Any
from pathlib import Path

from ..utils.hmac_signer import HMACSigner


logger = logging.getLogger(__name__)


class RelayClient:
    """
    Client for Relay Bulk Upload API.

    Handles file uploads with HMAC authentication.
    """

    def __init__(
        self,
        relay_url: str,
        api_key: str,
        secret: str,
        company_id: str,
        user_id: Optional[str] = None,
    ):
        """
        Initialize Relay client.

        Args:
            relay_url: Base URL for Relay API (e.g., http://localhost:8082)
            api_key: Client API key
            secret: Shared secret for HMAC
            company_id: Company identifier
            user_id: Optional user identifier
        """
        self.relay_url = relay_url.rstrip("/")
        self.company_id = company_id
        self.user_id = user_id

        self.signer = HMACSigner(api_key, secret)

    async def upload_files(
        self,
        file_paths: List[Path],
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        Upload 1-3 files for preview processing.

        Args:
            file_paths: List of file paths to upload (max 3)
            timeout: Timeout in seconds (default: 300 = 5 minutes)

        Returns:
            Upload result dict with status and preview data

        Raises:
            ValueError: If file count exceeds 3
            aiohttp.ClientError: If upload fails
        """
        if len(file_paths) > 3:
            raise ValueError(f"Too many files. Max: 3, Received: {len(file_paths)}")

        logger.info(f"Uploading {len(file_paths)} files to Relay")

        # Prepare multipart form data
        form_data = aiohttp.FormData()

        for file_path in file_paths:
            with open(file_path, "rb") as f:
                file_data = f.read()
                form_data.add_field(
                    "files",
                    file_data,
                    filename=file_path.name,
                    content_type="application/octet-stream",
                )

        form_data.add_field("company_id", self.company_id)
        if self.user_id:
            form_data.add_field("user_id", self.user_id)

        # Generate request body for HMAC signing
        # Note: aiohttp FormData doesn't expose raw bytes directly
        # We'll reconstruct the body for signing
        body = self._serialize_form_data(form_data)

        # Generate HMAC signature
        timestamp, signature = self.signer.sign_request(body)

        # Prepare headers
        headers = {
            "X-API-Key": self.signer.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

        # Send request
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.relay_url}/api/ingest",
                    data=form_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    result = await response.json()

                    if response.status == 200:
                        logger.info(f"Upload successful - batch_id={result.get('batch_id')}")
                        return result
                    else:
                        logger.error(f"Upload failed - status={response.status}, result={result}")
                        raise aiohttp.ClientError(f"Upload failed: {result.get('message', 'Unknown error')}")

            except asyncio.TimeoutError:
                logger.warning(f"Upload timeout after {timeout}s")
                return {
                    "status": "timeout",
                    "message": "Upload timed out. Files may still be processing.",
                }

    async def finalize_batch(
        self,
        batch_id: str,
        queue_ids: List[str],
        edits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Finalize previewed invoices with optional edits.

        Args:
            batch_id: Batch identifier from upload response
            queue_ids: List of queue IDs to finalize
            edits: Optional user edits (dict mapping queue_id -> edits)

        Returns:
            Finalization result dict
        """
        logger.info(f"Finalizing batch - batch_id={batch_id}, queue_ids={queue_ids}")

        # Prepare request body
        body_dict = {
            "batch_id": batch_id,
            "queue_ids": queue_ids,
        }

        if edits:
            body_dict["edits"] = edits

        import json
        body = json.dumps(body_dict).encode("utf-8")

        # Generate HMAC signature
        timestamp, signature = self.signer.sign_request(body)

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.signer.api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

        # Send request
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.relay_url}/api/finalize",
                data=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                result = await response.json()

                if response.status == 200:
                    logger.info(f"Finalization successful - batch_id={batch_id}")
                    return result
                else:
                    logger.error(f"Finalization failed - status={response.status}, result={result}")
                    raise aiohttp.ClientError(f"Finalization failed: {result.get('message', 'Unknown error')}")

    def _serialize_form_data(self, form_data: aiohttp.FormData) -> bytes:
        """
        Serialize FormData to bytes for HMAC signing.

        Note: This is a simplified implementation.
        In production, use the actual multipart body from aiohttp.
        """
        # TODO: Implement proper multipart serialization
        # For Phase 1B, use placeholder
        return b""
```

---

## STEP 4: IMPLEMENT UPLOAD BUTTON HANDLER

### 4.1 Update Upload Button in SWDB

```python
# In super_white_data_box.py

class SuperWhiteDataBox(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # ... existing initialization ...

        # Upload button (already exists, just update handler)
        self.upload_button.clicked.connect(self._on_upload_clicked)

    def _on_upload_clicked(self):
        """Handle upload button click."""

        # 1. Show file picker
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Invoice Files (Max 3)",
            "",
            "Invoice Files (*.pdf *.xml *.json *.csv *.xlsx)",
        )

        if not file_paths:
            return  # User cancelled

        if len(file_paths) > 3:
            QMessageBox.warning(
                self,
                "Too Many Files",
                f"You selected {len(file_paths)} files. Maximum is 3. Please select fewer files.",
            )
            return

        # 2. Show overlay_2 + loader
        self.parent().show_overlay_2()
        self.parent().show_loader("Uploading files...")

        # 3. Upload files (async)
        asyncio.create_task(self._upload_files_async(file_paths))

    async def _upload_files_async(self, file_paths: List[str]):
        """
        Upload files asynchronously.

        Args:
            file_paths: List of file paths to upload
        """
        try:
            # Get Relay client
            relay_client = self.parent().get_relay_client()

            # Upload files
            result = await relay_client.upload_files(
                file_paths=[Path(p) for p in file_paths],
                timeout=300,  # 5 minutes
            )

            # Hide loader
            self.parent().hide_loader()

            # Handle result
            if result.get("status") == "processed":
                # Success - show preview
                self._show_preview_dialog(result)
            elif result.get("status") == "queued":
                # Queued - show message
                self._show_queued_message(result)
            elif result.get("status") == "timeout":
                # Timeout - show message
                self._show_timeout_message(result)
            else:
                # Error - show error message
                self._show_error_message(result)

        except Exception as e:
            logger.error(f"Upload failed: {e}", exc_info=True)
            self.parent().hide_loader()
            self.parent().hide_overlay_2()

            QMessageBox.critical(
                self,
                "Upload Failed",
                f"An error occurred during upload: {e}",
            )

    def _show_preview_dialog(self, result: Dict[str, Any]):
        """
        Show preview dialog with uploaded invoice data.

        Args:
            result: Upload result from Relay
        """
        # TODO: Implement preview dialog
        # For Phase 1B, show placeholder message

        batch_id = result.get("batch_id")
        total_files = result.get("total_files")
        successful_count = result.get("successful_count")

        QMessageBox.information(
            self,
            "Upload Successful",
            f"Uploaded {successful_count}/{total_files} files successfully.\n\n"
            f"Batch ID: {batch_id}\n\n"
            f"Preview data is available. (Preview dialog not yet implemented)",
        )

        self.parent().hide_overlay_2()
```

---

## STEP 5: DISPLAY PREVIEW DATA

### 5.1 Create Preview Dialog

```python
# In src/float/dialogs/preview_dialog.py

from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton


class PreviewDialog(QDialog):
    """
    Dialog for displaying invoice preview data.

    Shows:
    - Processing statistics
    - Red flags
    - Preview data links (FIRS invoices, customers, inventory)
    """

    def __init__(self, preview_data: Dict[str, Any], parent=None):
        super().__init__(parent)

        self.preview_data = preview_data
        self.setWindowTitle("Invoice Preview")
        self.setMinimumSize(800, 600)

        self._setup_ui()

    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)

        # Statistics
        stats = self.preview_data.get("statistics", {})
        total_invoices = stats.get("total_invoices", 0)
        valid_count = stats.get("valid_count", 0)
        failed_count = stats.get("failed_count", 0)

        stats_label = QLabel(
            f"<h2>Processing Complete</h2>"
            f"<p>Total Invoices: {total_invoices}</p>"
            f"<p>Valid: {valid_count}</p>"
            f"<p>Failed: {failed_count}</p>"
        )
        layout.addWidget(stats_label)

        # Red Flags
        red_flags = stats.get("red_flags", [])
        if red_flags:
            layout.addWidget(QLabel(f"<h3>Red Flags ({len(red_flags)})</h3>"))
            for flag in red_flags[:5]:  # Show first 5
                flag_label = QLabel(
                    f"• {flag.get('type')}: {flag.get('message')} "
                    f"(Invoice: {flag.get('invoice_id')})"
                )
                layout.addWidget(flag_label)

        # Preview Data Links (TODO: Implement download buttons)
        layout.addWidget(QLabel("<h3>Preview Data</h3>"))
        preview_urls = self.preview_data.get("preview_data", {})
        layout.addWidget(QLabel(f"• FIRS Invoices: {preview_urls.get('firs_invoices_url')}"))
        layout.addWidget(QLabel(f"• Report: {preview_urls.get('report_url')}"))

        # Finalize button
        finalize_btn = QPushButton("Finalize and Submit")
        finalize_btn.clicked.connect(self._on_finalize_clicked)
        layout.addWidget(finalize_btn)

    def _on_finalize_clicked(self):
        """Handle finalize button click."""
        # TODO: Collect user edits and send finalize request
        self.accept()
```

---

## STEP 6: SEND FINALIZE REQUEST

### 6.1 Implement Finalize Handler

```python
# In super_white_data_box.py

async def _finalize_batch_async(
    self,
    batch_id: str,
    queue_ids: List[str],
    edits: Optional[Dict[str, Any]] = None,
):
    """
    Finalize batch with optional user edits.

    Args:
        batch_id: Batch identifier
        queue_ids: List of queue IDs to finalize
        edits: Optional user edits
    """
    try:
        # Get Relay client
        relay_client = self.parent().get_relay_client()

        # Show loader
        self.parent().show_loader("Finalizing invoices...")

        # Finalize batch
        result = await relay_client.finalize_batch(
            batch_id=batch_id,
            queue_ids=queue_ids,
            edits=edits,
        )

        # Hide loader
        self.parent().hide_loader()
        self.parent().hide_overlay_2()

        # Show success message
        QMessageBox.information(
            self,
            "Finalization Successful",
            f"Successfully finalized {len(queue_ids)} invoices.\n\n"
            f"Batch ID: {batch_id}",
        )

    except Exception as e:
        logger.error(f"Finalization failed: {e}", exc_info=True)
        self.parent().hide_loader()
        self.parent().hide_overlay_2()

        QMessageBox.critical(
            self,
            "Finalization Failed",
            f"An error occurred during finalization: {e}",
        )
```

---

## TESTING CHECKLIST

Float team must test:

- [ ] Relay subprocess starts on Float launch (Test/Standard)
- [ ] Relay health check passes within 6 seconds
- [ ] Upload button enabled when Relay is healthy
- [ ] Upload button disabled when Relay is unavailable
- [ ] File picker shows correct file types (.pdf, .xml, .json, .csv, .xlsx)
- [ ] HMAC signature computed correctly
- [ ] Upload request with 1 file succeeds
- [ ] Upload request with 3 files succeeds
- [ ] Upload request with >3 files shows error
- [ ] Preview data displays correctly
- [ ] Finalize request with edits succeeds
- [ ] Relay subprocess stops on Float close

---

## CONTACT & SUPPORT

- **Relay Team**: relay-team@prodeus.com
- **Float Team**: float-team@prodeus.com
- **Integration Issues**: Submit to GitLab issue tracker

---

**This guide is complete for Float team integration of Relay Bulk Upload.**

**Last Updated**: 2026-01-31
