import importlib

import httpx2
import pytest
from raqindex import OpenAlexClient

module = importlib.import_module("raq_helpers.client.client")


@pytest.mark.asyncio
@pytest.mark.parametrize("path,expected_calls", [("/works", 1), ("/rate-limit", 2)])
async def test_cache_reuse_and_bypass(
    environment_variables, monkeypatch, path, expected_calls
):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(
            200, json={"results": []}, headers={"Cache-Control": "public, max-age=3600"}
        )

    monkeypatch.setattr(
        module.httpx2, "AsyncHTTPTransport", lambda: httpx2.MockTransport(handle)
    )
    async with OpenAlexClient() as client:
        first = await client.client.get(path)
        second = await client.client.get(path)
        assert first.json() == second.json()
        assert bool(second.extensions.get("hishel_from_cache")) == (expected_calls == 1)
    assert len(calls) == expected_calls






@pytest.mark.parametrize(
    "base", ["https://api.openalex.org", "https://api.openalex.org/"]
)
@pytest.mark.parametrize(
    "path,allowed",
    [
        ("/query", False),
        ("/query?oql=works", False),
        ("/query/oql/works", False),
        ("/rate-limit", False),
        ("/rate-limit?api_key=test", False),
        ("/querying", True),
        ("/rate-limited", True),
        ("/works", True),
        ("/", True),
    ],
)
def test_cache_exclusions_match_path_boundaries(base, path, allowed):
    import re

    assert (
        bool(re.match(OpenAlexClient.cache_url_pattern(base), base.rstrip("/") + path))
        is allowed
    )






@pytest.mark.asyncio
async def test_preflight_is_never_cached_even_with_body_keys(
    environment_variables, monkeypatch
):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(200, json={"validation": {"valid": True}})

    monkeypatch.setattr(
        module.httpx2, "AsyncHTTPTransport", lambda: httpx2.MockTransport(handle)
    )
    async with OpenAlexClient() as client:
        for _ in range(2):
            result = await client.client.post(
                "/query",
                json={"oql": "get works"},
                extensions={"hishel_body_key": True},
            )
            assert not result.extensions.get("hishel_from_cache", False)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_factory_json_requests_reuse_cache_without_colliding(environment_variables, monkeypatch):
    import json
    from raqindex.models import OQO

    calls = []

    def handle(request):
        sample = json.loads(request.content)['oqo']['sample']
        calls.append(sample)
        return httpx2.Response(200, json={'meta': {'count': sample}, 'results': []})

    monkeypatch.setattr(module.httpx2, 'AsyncHTTPTransport', lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        for sample in (1, 2, 1):
            result = await client.oqo(OQO(get_rows='works', sample=sample))
            assert result.meta.count == sample
    assert calls == [1, 2]
