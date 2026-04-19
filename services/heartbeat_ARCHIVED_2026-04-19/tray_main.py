"""
Entry point for Helium System Tray (Standard/Test tier).

Usage:
    python tray_main.py

The tray app monitors HeartBeat status via the readiness endpoint.
It does NOT manage HeartBeat's lifecycle — HeartBeat starts independently.
Closing the tray app does NOT stop HeartBeat.
"""

import sys
from src.tray.app import main

sys.exit(main())
