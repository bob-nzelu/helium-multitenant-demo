"""
Pytest Configuration for Helium Services Tests

Shared fixtures and configuration for all test files.
"""

import asyncio
import pytest
import os
import sys

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

# psycopg3 async requires SelectorEventLoop on Windows (not ProactorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Setup test environment before running tests"""

    # Set test environment variables
    os.environ["TESTING"] = "true"
    os.environ["LOG_LEVEL"] = "DEBUG"

    yield

    # Cleanup
    os.environ.pop("TESTING", None)
    os.environ.pop("LOG_LEVEL", None)


@pytest.fixture(scope="function")
def clean_environment(monkeypatch):
    """Provide clean environment for each test"""

    # Reset any cached singletons
    yield monkeypatch
