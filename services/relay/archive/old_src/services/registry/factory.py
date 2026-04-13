"""
Service Registry Factory

Creates the appropriate service registry client based on deployment tier.
- Test/Standard: EurekaMockClient (in-memory, hardcoded localhost)
- Pro/Enterprise: EurekaClient (real Eureka server)
"""

import logging
from typing import Union, Dict, Any, Optional

from .eureka_client import EurekaClient
from .eureka_mock import EurekaMockClient


logger = logging.getLogger(__name__)


def create_registry_client(
    config: Dict[str, Any],
) -> Union[EurekaMockClient, EurekaClient]:
    """
    Factory function to create appropriate service registry client.

    Decision from RELAY_DECISIONS.md:
    - type='mock' → EurekaMockClient (Test/Standard)
    - type='eureka' → EurekaClient (Pro/Enterprise)

    Args:
        config: Configuration dict with 'service_registry' section
                Expected format:
                {
                    "service_registry": {
                        "type": "mock" or "eureka",
                        "eureka_url": "http://eureka:8761",  # Only for type='eureka'
                        "services": {
                            "core-api": {"url": "http://localhost:8080"},
                            ...
                        }
                    }
                }

    Returns:
        EurekaMockClient or EurekaClient instance

    Raises:
        ValueError: If config is invalid or missing required fields
    """

    if "service_registry" not in config:
        raise ValueError("Config missing 'service_registry' section")

    registry_config = config["service_registry"]
    registry_type = registry_config.get("type", "mock")

    logger.debug(f"Creating service registry client: type={registry_type}")

    if registry_type == "mock":
        logger.info("Using EurekaMockClient (Test/Standard tier)")
        return EurekaMockClient()

    elif registry_type == "eureka":
        eureka_url = registry_config.get("eureka_url")
        if not eureka_url:
            raise ValueError("Config missing 'eureka_url' for type='eureka'")

        # Extract fallback URLs from config (optional)
        fallback_urls = {}
        if "services" in registry_config:
            for service_name, service_info in registry_config["services"].items():
                if isinstance(service_info, dict) and "url" in service_info:
                    fallback_urls[service_name] = service_info["url"]

        logger.info(f"Using EurekaClient (Pro/Enterprise tier) - eureka_url={eureka_url}")

        return EurekaClient(
            eureka_url=eureka_url,
            fallback_urls=fallback_urls,
        )

    else:
        raise ValueError(f"Unknown service registry type: {registry_type}")


class ServiceRegistry:
    """
    Singleton wrapper for service registry client.

    Ensures only one registry client exists throughout the application.
    Provides global access to service discovery.

    Usage:
        registry = ServiceRegistry.get_instance(config)
        core_service = registry.get_service('core-api')
    """

    _instance: Optional[Union[EurekaMockClient, EurekaClient]] = None
    _config: Optional[Dict[str, Any]] = None

    @classmethod
    def initialize(cls, config: Dict[str, Any]) -> None:
        """
        Initialize the service registry singleton.

        Args:
            config: Configuration dict with 'service_registry' section

        Raises:
            ValueError: If config is invalid
        """

        if cls._instance is not None:
            logger.warning(
                "ServiceRegistry already initialized, creating new instance"
            )

        cls._config = config
        cls._instance = create_registry_client(config)

    @classmethod
    def get_instance(
        cls,
        config: Optional[Dict[str, Any]] = None,
    ) -> Union[EurekaMockClient, EurekaClient]:
        """
        Get the service registry singleton instance.

        If not yet initialized, creates instance using provided config.

        Args:
            config: Configuration dict (only used if not yet initialized)

        Returns:
            EurekaMockClient or EurekaClient instance

        Raises:
            ValueError: If not initialized and no config provided
        """

        if cls._instance is None:
            if config is None:
                raise ValueError(
                    "ServiceRegistry not initialized. "
                    "Call initialize(config) first or provide config to get_instance()."
                )
            cls.initialize(config)

        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)"""

        cls._instance = None
        cls._config = None
