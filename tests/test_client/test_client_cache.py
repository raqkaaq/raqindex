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


@pytest.mark.asyncio
async def test_json_cache_separates_bodies_urls_and_methods(environment_variables, monkeypatch):
    """Regression for the earlier works/authors POST cache collision."""
    import json
    calls = []

    def handle(request):
        identity = (request.method, str(request.url), request.content.decode())
        calls.append(identity)
        return httpx2.Response(200, json={'identity': identity})

    monkeypatch.setattr(module.httpx2, 'AsyncHTTPTransport', lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        combinations = [
            ('POST', '/', 'works'), ('POST', '/', 'authors'),
            ('POST', '/authors', 'works'), ('GET', '/', 'works'),
        ]
        for repetition in range(2):
            for method, url, entity in combinations:
                response = await client.client.request(method, url, json={'oqo': {'get_rows': entity}},
                                                       extensions={'hishel_body_key': True})
                actual = response.json()['identity']
                assert actual[0] == method
                assert httpx2.URL(actual[1]).path == url
                assert json.loads(actual[2])['oqo']['get_rows'] == entity
                assert bool(response.extensions.get('hishel_from_cache')) is bool(repetition)
    assert len(calls) == len(combinations)


@pytest.mark.parametrize('base', ['https://api.openalex.org', 'https://api.openalex.org/'])
@pytest.mark.parametrize('path,allowed', [
    ('/query', False), ('/query?oql=works', False), ('/query/oql/works', False),
    ('/rate-limit', False), ('/rate-limit?api_key=test', False),
    ('/querying', True), ('/rate-limited', True), ('/works', True), ('/', True),
])
def test_cache_exclusions_match_path_boundaries(base, path, allowed):
    import re
    assert bool(re.match(module.cache_url_pattern(base), base.rstrip('/') + path)) is allowed


def test_cache_pattern_does_not_match_another_host():
    import re
    pattern = module.cache_url_pattern('https://api.openalex.org')
    assert not re.match(pattern, 'https://api.openalex.org.example.com/works')
    assert not re.match(pattern, 'https://example.com/works')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 403, 404, 429, 500])
async def test_error_responses_are_not_cached(environment_variables, monkeypatch, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(status, json={'message': 'refused'})

    monkeypatch.setattr(module.httpx2, 'AsyncHTTPTransport', lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        # Underlying client avoids retry delays; this tests only the cache policy.
        for _ in range(2):
            result = await client.client.get('/works')
            assert result.status_code == status
            assert not result.extensions.get('hishel_from_cache', False)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_preflight_is_never_cached_even_with_body_keys(environment_variables, monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(200, json={'validation': {'valid': True}})

    monkeypatch.setattr(module.httpx2, 'AsyncHTTPTransport', lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        for _ in range(2):
            result = await client.client.post('/query', json={'oql': 'get works'},
                                              extensions={'hishel_body_key': True})
            assert not result.extensions.get('hishel_from_cache', False)
    assert len(calls) == 2
