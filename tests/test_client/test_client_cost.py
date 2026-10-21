from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from raqindex import OpenAlexClient


@pytest.mark.asyncio
async def test_cost_records_usage():
    response = httpx2.Response(200, headers={
        "x-ratelimit-limit": "100", "x-ratelimit-remaining": "95",
        "x-ratelimit-credits-used": "5", "x-ratelimit-reset": "60",
    })
    client = SimpleNamespace()
    result = await OpenAlexClient.cost(AsyncMock(return_value=response))(client)
    assert result is response
    assert client.usage == {
        "x-ratelimit-limit": "100", "x-ratelimit-remaining": "95",
        "x-ratelimit-credits-used": "5", "x-ratelimit-reset": "60",
    }


@pytest.mark.asyncio
async def test_cached_response_preserves_previous_usage():
    previous = {"x-ratelimit-remaining": "95"}
    client = SimpleNamespace(usage=previous)
    response = httpx2.Response(200, extensions={"hishel_from_cache": True})
    assert await OpenAlexClient.cost(AsyncMock(return_value=response))(client) is response
    assert client.usage is previous


@pytest.mark.asyncio
async def test_missing_usage_headers():
    client = SimpleNamespace()
    response = httpx2.Response(200)
    await OpenAlexClient.cost(AsyncMock(return_value=response))(client)
    assert len(client.usage) == 4
    assert all(value is None for value in client.usage.values())
