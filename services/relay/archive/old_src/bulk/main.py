"""
Relay Bulk Upload - Main Entry Point

Starts the FastAPI application with uvicorn.

Usage:
    python -m helium.relay.bulk.main

Environment Variables:
    CONFIG_PATH: Path to relay-bulk config JSON file
    PORT: Override port from config (default: 8082)
    ENVIRONMENT: production|staging|test
"""

import logging
import sys
import os
import json
from pathlib import Path

import uvicorn

from .service import RelayBulkService
from .validation import BulkValidationPipeline
from .handlers import create_bulk_app
from ..services.clients import CoreAPIClient, HeartBeatClient, AuditAPIClient
from ..services.registry import get_service_registry


# ============================================================================
# Logging Setup
# ============================================================================


def setup_logging(environment: str = "production"):
    """
    Setup structured JSON logging to stdout.

    Args:
        environment: Environment name (production, staging, test)
    """
    import logging.config

    # TODO: Implement structured JSON logging
    # For Phase 1B, use basic logging

    logging.basicConfig(
        level=logging.INFO if environment == "production" else logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )

    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized - environment={environment}")


# ============================================================================
# Configuration Loading
# ============================================================================


def load_config() -> dict:
    """
    Load configuration from file or environment.

    Priority:
    1. Environment variables (highest)
    2. Config file (CONFIG_PATH)
    3. Default values (lowest)

    Returns:
        Configuration dict
    """
    config_path = os.getenv("CONFIG_PATH")

    if config_path and Path(config_path).exists():
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        # Default configuration (Test/Standard tier)
        config = {
            "instance_id": "relay-bulk-1",
            "type": "bulk",
            "tier": "standard",
            "core_api_url": "http://localhost:8080",
            "heartbeat_api_url": "http://localhost:9000",
            "audit_api_url": "http://localhost:9000",
            "type_config": {
                "port": 8082,
                "max_file_size_mb": 10,
                "max_files_per_request": 3,
                "max_total_size_mb": 30,
                "allowed_extensions": [".pdf", ".xml", ".json", ".csv", ".xlsx"],
                "daily_limit_per_company": 500,
                "malware_scan_enabled": False,
                "request_timeout_seconds": 300,
                "graceful_shutdown_timeout_seconds": 30,
            },
        }

    # Environment variable overrides
    if os.getenv("PORT"):
        config["type_config"]["port"] = int(os.getenv("PORT"))

    if os.getenv("MAX_FILE_SIZE_MB"):
        config["type_config"]["max_file_size_mb"] = int(os.getenv("MAX_FILE_SIZE_MB"))

    if os.getenv("MAX_FILES_PER_REQUEST"):
        config["type_config"]["max_files_per_request"] = int(os.getenv("MAX_FILES_PER_REQUEST"))

    if os.getenv("DAILY_LIMIT_PER_COMPANY"):
        config["type_config"]["daily_limit_per_company"] = int(os.getenv("DAILY_LIMIT_PER_COMPANY"))

    return config


def load_api_key_secrets() -> dict:
    """
    Load API key secrets for HMAC verification.

    In production, this comes from config.db or key vault.
    For Phase 1B, use environment variable or default.

    Returns:
        Dict mapping API keys to secrets
    """
    # TODO: Load from config.db or key vault
    # For Phase 1B, use placeholder

    secrets_env = os.getenv("API_KEY_SECRETS")
    if secrets_env:
        # Format: "key1:secret1,key2:secret2"
        secrets = {}
        for pair in secrets_env.split(","):
            key, secret = pair.split(":")
            secrets[key] = secret
        return secrets

    # Default test credentials
    return {
        "test_api_key": "test_secret_12345",
        "client_api_key_12345": "shared_secret_xyz",
    }


# ============================================================================
# Application Factory
# ============================================================================


def create_app():
    """
    Create and configure the FastAPI application.

    Returns:
        FastAPI application instance
    """
    environment = os.getenv("ENVIRONMENT", "production")

    # Setup logging
    setup_logging(environment)

    logger = logging.getLogger(__name__)
    logger.info("Starting Relay Bulk Upload Service")

    # Load configuration
    config = load_config()
    type_config = config["type_config"]
    instance_id = config["instance_id"]

    logger.info(f"Configuration loaded - instance_id={instance_id}, tier={config.get('tier')}")

    # Load API key secrets
    api_key_secrets = load_api_key_secrets()
    logger.info(f"Loaded {len(api_key_secrets)} API key secrets")

    # Initialize service registry (for future use)
    # registry = get_service_registry(config)

    # Initialize clients
    core_client = CoreAPIClient(
        core_api_url=config.get("core_api_url"),
        preview_timeout=type_config.get("request_timeout_seconds", 300),
    )

    heartbeat_client = HeartBeatClient(
        heartbeat_api_url=config.get("heartbeat_api_url"),
    )

    audit_client = AuditAPIClient(
        audit_api_url=config.get("audit_api_url"),
    )

    logger.info("Initialized service clients")

    # Initialize validation pipeline
    validation_pipeline = BulkValidationPipeline(
        heartbeat_client=heartbeat_client,
        config=type_config,
        api_key_secrets=api_key_secrets,
    )

    logger.info("Initialized validation pipeline")

    # Initialize bulk service
    bulk_service = RelayBulkService(
        core_client=core_client,
        heartbeat_client=heartbeat_client,
        audit_client=audit_client,
        validation_pipeline=validation_pipeline,
        config=type_config,
    )

    logger.info("Initialized bulk service")

    # Create FastAPI app
    app = create_bulk_app(
        bulk_service=bulk_service,
        validation_pipeline=validation_pipeline,
        instance_id=instance_id,
        config=type_config,
    )

    logger.info("FastAPI application created successfully")

    return app


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    """
    Main entry point for running the service.
    """
    app = create_app()
    config = load_config()
    type_config = config["type_config"]

    port = type_config.get("port", 8082)
    host = os.getenv("HOST", "0.0.0.0")

    logger = logging.getLogger(__name__)
    logger.info(f"Starting uvicorn server on {host}:{port}")

    # Run with uvicorn
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
        timeout_graceful_shutdown=type_config.get("graceful_shutdown_timeout_seconds", 30),
    )


if __name__ == "__main__":
    main()
