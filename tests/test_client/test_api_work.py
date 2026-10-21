from unittest.mock import AsyncMock

import httpx2
import pytest


@pytest.mark.asyncio
async def test_works_forwards_parameters(client, monkeypatch):
    response = httpx2.Response(200, json={"results": []}, request=httpx2.Request("GET", "https://api.openalex.org/works"))
    get = AsyncMock(return_value=response)
    monkeypatch.setattr(client.client, "request", get)
    result = await client.works(search="climate", per_page=5)
    assert result.results == []
    get.assert_awaited_once()
    sent = get.call_args.kwargs
    assert sent["method"] == "GET"
    assert sent["url"] == "/works"
    assert sent["params"]["search"] == "climate"
    assert sent["params"]["per_page"] == 5
    assert "api_key" not in sent["params"]
    assert sent["follow_redirects"] is True
