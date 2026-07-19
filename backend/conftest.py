"""
conftest.py - pytest root configuration.

Adds the backend directory to sys.path so that all modules
(main, auth, database, models, etc.) are importable without
installing the package.
"""
from __future__ import annotations

import sys
import os

# Ensure backend root is on the path for all tests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
