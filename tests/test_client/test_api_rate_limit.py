from unittest.mock import AsyncMock

import httpx2
import pytest


@pytest.mark.asyncio
async def test_rate_limit_endpoint(client, monkeypatch):
    response = httpx2.Response(200, json={}, request=httpx2.Request("GET", "https://api.openalex.org/rate-limit"))
    get = AsyncMock(return_value=response)
    monkeypatch.setattr(client.client, "request", get)
    result = await client.rate_limit()
    assert result.model_extra["request"].api_key == "<redacted>"
    get.assert_awaited_once()
    sent = get.call_args.kwargs
    assert sent["method"] == "GET"
    assert sent["url"] == "/rate-limit"
    assert "api_key" not in sent["params"]
    assert sent["follow_redirects"] is True
