"""
tests/unit/test_deps.py
───────────────────────
Unit tests for app/api/v1/deps.py dependencies.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.api.v1.deps import get_optional_company_id


@pytest.mark.asyncio
async def test_get_optional_company_id_existing():
    active_id = uuid.uuid4()
    mock_db = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = active_id
    mock_db.execute.return_value = mock_result

    res = await get_optional_company_id(company_id=active_id, db=mock_db)
    assert res == active_id


@pytest.mark.asyncio
async def test_get_optional_company_id_fallback_when_not_in_db():
    invalid_id = uuid.uuid4()
    fallback_id = uuid.uuid4()
    mock_db = AsyncMock()

    # First call checks if invalid_id exists -> returns None
    # Second call fetches active company -> returns fallback_id
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = None
    res2 = MagicMock()
    res2.scalar_one_or_none.return_value = fallback_id

    mock_db.execute.side_effect = [res1, res2]

    res = await get_optional_company_id(company_id=invalid_id, db=mock_db)
    assert res == fallback_id


@pytest.mark.asyncio
async def test_get_optional_company_id_none_passed():
    active_id = uuid.uuid4()
    mock_db = AsyncMock()

    res_mock = MagicMock()
    res_mock.scalar_one_or_none.return_value = active_id
    mock_db.execute.return_value = res_mock

    res = await get_optional_company_id(company_id=None, db=mock_db)
    assert res == active_id
