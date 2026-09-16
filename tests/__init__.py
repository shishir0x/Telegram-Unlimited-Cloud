"""
TG Drive Automated Test Suite Package
Ensures project root is added to sys.path for test discovery and direct execution.
"""
import sys
import os
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
