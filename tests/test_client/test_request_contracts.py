"""Expected public behavior; see tests/README.md for sources and policy scope."""
import importlib
from unittest.mock import AsyncMock

import httpx2
import pytest

from raqindex import OpenAlexClient
from raqindex.models.openalex import Error

module = importlib.import_module('raqindex.client.client')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['retrieval', 'preflight'])
async def test_bearer_auth_survives_per_request_body_cache_settings(environment_variables, monkeypatch, kind):
    """Inspect the actual transport request, not just client configuration."""
    sent = []

    def handle(request):
        sent.append(request)
        if kind == 'preflight':
            payload = {'oql': 'get works', 'oqo': {'get_rows': 'works'},
                       'oxurl': '/works', 'validation': {'valid': True, 'errors': [], 'warnings': []}}
        else:
            payload = {'results': [], 'meta': {'count': 0}}
        return httpx2.Response(200, json=payload)

    monkeypatch.setattr(module.httpx2, 'AsyncHTTPTransport', lambda: httpx2.MockTransport(handle))
    async with OpenAlexClient() as client:
        if kind == 'preflight':
            await client.query('get works')
        else:
            await client.works(search='climate')
    assert len(sent) == 1
    request = sent[0]
    assert request.headers['Authorization'] == 'Bearer test_api_key'
    assert request.headers.get('User-Agent')
    assert 'api_key' not in request.url.params
    assert 'test_api_key' not in request.content.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize('parameter', ['per_pgae', 'serach'])
async def test_unknown_request_parameters_are_rejected_not_silently_dropped(client, monkeypatch, parameter):
    """Project TODO explicitly requires rejecting unknown request parameters."""
    request = AsyncMock(return_value=httpx2.Response(200, json={'results': []},
        request=httpx2.Request('GET', 'https://api.openalex.org/works')))
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.works(**{parameter: 'typo'})
    assert isinstance(result, Error), 'Unknown input must produce a validation error'
    assert parameter in result.message
    request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_input', ['out_of_range_page', 'missing_id'])
async def test_request_validation_does_not_disclose_authentication_secret(client, monkeypatch, caplog, invalid_input):
    """A local input error must not print the injected API key or return it."""
    secret = 'test-secret-which-must-never-appear-in-diagnostics'
    client.api_key = secret
    request = AsyncMock()
    monkeypatch.setattr(client.client, 'request', request)
    with caplog.at_level('ERROR', logger=module.__name__):
        if invalid_input == 'missing_id':
            from raqindex.models.endpoints import ReqRes
            from raqindex.models.openalex import WorksIdGetParameters
            endpoint = client.endpoints['/works'].model_copy(deep=True)
            endpoint.endpoint = '/works/{id}'
            endpoint.method.request = ReqRes(ref='WorksIdGetParameters', model=WorksIdGetParameters)
            client.endpoints['/works/{id}'] = endpoint
            result = await client.endpoint_factory('/works/{id}')()
        else:
            result = await client.works(per_page=0)
    assert isinstance(result, Error)
    request.assert_not_awaited()
    assert secret not in result.model_dump_json(), 'Validation output disclosed the API key'
    assert secret not in caplog.text, 'Validation logs disclosed the API key'
