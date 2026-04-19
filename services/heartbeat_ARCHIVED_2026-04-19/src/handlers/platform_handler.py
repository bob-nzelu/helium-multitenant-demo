"""
Platform Handler — Business logic for Platform Services endpoints.

Reads Transforma module source code and FIRS service keys from config.db.
These are served to Relay via GET /api/platform/transforma/config.

Access control:
    When a caller_service is identified (from service credentials), the handler
    filters modules by access_control rules in config.db:
        - Relay: qr_generator + service_keys only
        - Core: all modules (wildcard *)
        - SDK: no access (none)
    When no caller is identified (admin/internal): returns all modules.

Data model:
    config_entries table (config.db), service_name="transforma":
    - Each module is stored as a JSON blob with keys:
      module_name, source_code, version, checksum, updated_at
    - Service keys are stored as a JSON blob with keys:
      firs_public_key_pem, csid, csid_expires_at, certificate

Response matches Relay's TransformaModuleCache expected contract exactly.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ..database.config_db import get_config_database

logger = logging.getLogger(__name__)


async def get_transforma_config(
    caller_service: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read Transforma modules + FIRS service keys from config.db.

    Modules are stored as config_entries with service_name="transforma".
    Each entry's config_value is a JSON string containing module metadata
    or service keys.

    When caller_service is provided, filters modules by access_control rules:
        - Relay gets qr_generator + service_keys only
        - Core gets all modules (wildcard)
        - Unknown callers get nothing

    When caller_service is None (admin/internal), returns all modules.

    Returns:
        {
            "modules": [
                {"module_name": str, "source_code": str, "version": str,
                 "checksum": str, "updated_at": str},
                ...
            ],
            "service_keys": {
                "firs_public_key_pem": str,
                "csid": str,
                "csid_expires_at": str,
                "certificate": str
            }
        }
    """
    db = get_config_database()
    entries = db.get_all_config("transforma")

    # Determine allowed resources if caller_service is specified
    allowed_keys: Optional[List[str]] = None
    if caller_service:
        try:
            allowed_keys = db.get_allowed_resources(
                caller_service, "transforma_module"
            )
            logger.debug(
                f"Transforma access for {caller_service}: {allowed_keys}"
            )
        except Exception as e:
            logger.warning(
                f"Access control lookup failed for {caller_service}: {e}"
            )
            # Fail open if access_control table missing (pre-migration)
            allowed_keys = None

    modules: List[Dict[str, Any]] = []
    service_keys: Dict[str, Any] = {}

    for entry in entries:
        config_key = entry["config_key"]
        config_value = entry["config_value"]

        try:
            parsed = json.loads(config_value)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"Invalid JSON in transforma config entry '{config_key}': {e}"
            )
            continue

        # Apply access control filtering
        if allowed_keys is not None:
            has_wildcard = "*" in allowed_keys
            if not has_wildcard and config_key not in allowed_keys:
                logger.debug(
                    f"Filtered out '{config_key}' for {caller_service}"
                )
                continue

        if config_key == "service_keys":
            service_keys = parsed
        else:
            # Module entries: ensure module_name is set
            if "module_name" not in parsed:
                parsed["module_name"] = config_key
            modules.append(parsed)

    logger.debug(
        f"Transforma config loaded: {len(modules)} module(s), "
        f"keys={'present' if service_keys else 'absent'}"
        f"{f', caller={caller_service}' if caller_service else ''}"
    )

    return {
        "modules": modules,
        "service_keys": service_keys,
    }
