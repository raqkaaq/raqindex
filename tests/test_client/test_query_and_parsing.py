"""Offline coverage of request serialization and response/error dispatch."""
from unittest.mock import AsyncMock

import httpx2
import pytest

from raqindex.models import OQO, XQuery
from raqindex.models.endpoints import Endpoint, ReqRes, ResponseCollection
from raqindex.models.openalex import Error
from raqindex.models.query import QueryResponse, QueryErrorResponse, Validation


def mock_response(payload, status=200):
    return httpx2.Response(status, json=payload, request=httpx2.Request('POST', 'https://api.openalex.org/query'))


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['get works', OQO(get_rows='works')])
async def test_preflight_serialization_and_typed_success(client, monkeypatch, query):
    payload = {'oql': 'get works', 'oqo': {'get_rows': 'works'}, 'oxurl': '/works',
               'validation': {'valid': True, 'errors': [], 'warnings': []}}
    request = AsyncMock(return_value=mock_response(payload))
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.query(query)
    assert isinstance(result, QueryResponse)
    assert isinstance(result.validation, Validation)
    assert result.url == '/works'
    assert result.check is None
    sent = request.call_args.kwargs
    assert sent['method'] == 'POST' and sent['url'] == '/query'
    assert 'params' not in sent
    assert 'api_key' not in sent['json']
    if isinstance(query, str):
        assert sent['json']['oql'] == query
        assert 'oqo' not in sent['json']
    else:
        assert sent['json']['oqo']['get_rows'] == 'works'
        assert isinstance(sent['json']['oqo'], dict)
        assert 'oql' not in sent['json']
    assert result.model_extra['request'].api_key == '<redacted>'
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_400_produces_typed_diagnostic_without_retry(client, monkeypatch):
    request = AsyncMock(return_value=mock_response({'validation': {
        'valid': False, 'errors': [{'type': 'parse_error', 'message': 'Unknown entity',
                                  'position': 0, 'context': 'nonsense', 'location': None}], 'warnings': []}}, 400))
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.query('nonsense')
    assert isinstance(result, QueryErrorResponse)
    assert result.validation.valid is False
    assert result.validation.errors[0].type == 'parse_error'
    assert result.validation.errors[0].position == 0
    request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [403, 404, 429])
async def test_preflight_mapped_http_errors(client, monkeypatch, status):
    response = mock_response({'error': 'refused', 'message': 'No access'}, status)
    if status == 429:
        response.headers['x-ratelimit-remaining'] = '0'
    request = AsyncMock(return_value=response)
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.query('get works')
    assert isinstance(result, Error)
    assert result.message == 'No access'
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_unmapped_http_error_is_not_swallowed(client, monkeypatch):
    request = AsyncMock(return_value=mock_response({'message': 'unauthorized'}, 401))
    monkeypatch.setattr(client.client, 'request', request)
    with pytest.raises(httpx2.HTTPStatusError) as error:
        await client.query('get works')
    assert error.value.response.status_code == 401
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_extras_and_query_echo_are_preserved(client, monkeypatch):
    payload = {'meta': {'count': 0, 'future_meta': 123, 'x_query': {
        'oql': 'get works', 'oqo': {'get_rows': 'works'}, 'url': None}},
        'results': [], 'future_top_level': {'value': 42}}
    monkeypatch.setattr(client.client, 'request', AsyncMock(return_value=mock_response(payload)))
    result = await client.works()
    assert isinstance(result.meta.x_query, XQuery)
    assert result.meta.x_query.url is None
    assert result.meta.model_extra['future_meta'] == 123
    assert result.model_extra['future_top_level'] == {'value': 42}
    assert result.model_extra['request'].api_key == '<redacted>'


@pytest.mark.asyncio
async def test_path_values_are_encoded_and_removed_from_query_parameters(client, monkeypatch):
    from raqindex.models.openalex import WorksIdGetParameters, Work
    definition = client.endpoints['/works'].model_dump()
    definition['endpoint'] = '/works/{id}'
    definition['method']['request'] = ReqRes(ref='WorksIdGetParameters', model=WorksIdGetParameters)
    definition['method']['responses'] = ResponseCollection.model_validate({'200': ReqRes(ref='Work', model=Work)})
    client.endpoints['/works/{id}'] = Endpoint.model_validate(definition)
    request = AsyncMock(return_value=mock_response({'id': 'https://openalex.org/W1'}))
    monkeypatch.setattr(client.client, 'request', request)
    await client.works(id='https://doi.org/10.123/a?b=1#part', select='id')
    sent = request.call_args.kwargs
    assert sent['url'] == '/works/https%3A%2F%2Fdoi.org%2F10.123%2Fa%3Fb%3D1%23part'
    assert sent['params'] == {'select': 'id'}


def test_unknown_endpoint_fails_before_request(client):
    with pytest.raises(ValueError, match='not found'):
        client.endpoint_factory('/not-an-endpoint')


@pytest.mark.asyncio
async def test_dotted_request_alias_is_preserved_on_wire(client, monkeypatch):
    from pydantic import BaseModel, Field

    class Request(BaseModel):
        exact: str = Field(alias='search.exact')
        api_key: str

    endpoint = client.endpoints['/works'].model_copy(deep=True)
    endpoint.method.request = ReqRes(ref='Request', model=Request)
    client.endpoints['/works'] = endpoint
    request = AsyncMock(return_value=mock_response({'results': []}))
    monkeypatch.setattr(client.client, 'request', request)
    await client.endpoint_factory('/works')(**{'search.exact': 'climate change'})
    assert request.call_args.kwargs['params'] == {'search.exact': 'climate change'}


@pytest.mark.asyncio
async def test_invalid_request_stops_before_http(client, monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.works(per_page=0)
    assert isinstance(result, Error)
    assert 'per_page' in result.message
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_success_payload_is_not_silently_accepted(client, monkeypatch):
    from pydantic import ValidationError
    monkeypatch.setattr(client.client, 'request', AsyncMock(return_value=mock_response({
        'oql': 'get works', 'oqo': {'get_rows': 'works'},
        'validation': {'valid': 'not-a-boolean', 'errors': [], 'warnings': []}})))
    with pytest.raises(ValidationError) as error:
        await client.query('get works')
    assert error.value.errors()[0]['loc'] == ('validation', 'valid')


@pytest.mark.asyncio
async def test_malformed_400_payload_is_not_silently_accepted(client, monkeypatch):
    from pydantic import ValidationError
    monkeypatch.setattr(client.client, 'request', AsyncMock(return_value=mock_response({
        'validation': {'valid': False, 'errors': [{'type': 'parse_error'}], 'warnings': []}}, 400)))
    with pytest.raises(ValidationError) as error:
        await client.query('nonsense')
    assert any('message' in item['loc'] for item in error.value.errors())


@pytest.mark.asyncio
async def test_redacting_response_metadata_does_not_change_client_credentials(client, monkeypatch):
    monkeypatch.setattr(client.client, 'request', AsyncMock(return_value=mock_response({'results': []})))
    result = await client.works()
    assert result.model_extra['request'].api_key == '<redacted>'
    assert client.api_key == 'test_api_key'
    assert client.client.headers['Authorization'] == 'Bearer test_api_key'
