import importlib

import httpx2
import pytest
from raqindex import OpenAlexClient

module = importlib.import_module("raqindex.client.client")


@pytest.mark.asyncio
@pytest.mark.parametrize("path,expected_calls", [("/works", 1), ("/rate-limit", 2)])
async def test_cache_reuse_and_bypass(environment_variables, monkeypatch, path, expected_calls):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(200, json={"results": []}, headers={"Cache-Control": "public, max-age=3600"})

    monkeypatch.setattr(module.httpx2, "AsyncHTTPTransport", lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        first = await client.client.get(path)
        second = await client.client.get(path)
        assert first.json() == second.json()
        assert bool(second.extensions.get("hishel_from_cache")) == (expected_calls == 1)
    assert len(calls) == expected_calls


@pytest.mark.asyncio
async def test_query_parameters_separate_cache_entries(environment_variables, monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(200, json={"results": [], "query": request.url.params["search"]}, headers={"Cache-Control": "public, max-age=3600"})

    monkeypatch.setattr(module.httpx2, "AsyncHTTPTransport", lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        for search in ["climate", "biology", "climate"]:
            result = await client.works(search=search)
            assert result.model_extra["query"] == search
    assert len(calls) == 2
