"""
Test Runner for HeartBeat Blob Storage Tests

Runs all tests and reports coverage.

Usage:
    python run_tests.py
"""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

import pytest

if __name__ == "__main__":
    # Run pytest with coverage
    exit_code = pytest.main([
        "unit/test_heartbeat_register.py",
        "-v",                              # Verbose output
        "--cov=heartbeat",                 # Coverage for heartbeat module
        "--cov-report=term-missing",       # Show missing lines
        "--cov-report=html",               # Generate HTML report
        "--tb=short",                      # Shorter tracebacks
        "-x",                              # Stop on first failure
    ])

    print("\n" + "="*70)
    if exit_code == 0:
        print("✅ ALL TESTS PASSED!")
    else:
        print("❌ SOME TESTS FAILED")
    print("="*70)

    sys.exit(exit_code)
