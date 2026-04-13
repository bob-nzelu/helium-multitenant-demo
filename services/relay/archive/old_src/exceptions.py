"""
Relay-Specific Exceptions

Exceptions specific to relay module operations (extends common errors).
"""

from .services.errors import RelayError


class RelayServiceError(RelayError):
    """Base class for relay service-specific errors"""

    pass
