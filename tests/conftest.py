"""
tests/conftest.py
──────────────────
Shared pytest fixtures for ushanr tests.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.config import get_settings


@pytest.fixture
def settings():
    return get_settings()
