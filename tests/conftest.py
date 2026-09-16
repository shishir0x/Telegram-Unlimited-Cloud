"""
Pytest configuration for TG Drive.
Ensures project root is added to sys.path during test runs.
"""
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
