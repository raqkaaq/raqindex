"""Explicit one-page cursor continuation; no automatic traversal.

Protocol: https://help.openalex.org/api/paging/
"""
from unittest.mock import AsyncMock

import httpx2
import pytest

from raqindex.models import OQO
from raqindex.models.openalex import Error, WorksListResponse
from raqindex.models.endpoints import ReqRes


def response(cursor, identifier='W1', status=200):
    payload = ({'meta': {'next_cursor': cursor}, 'results': [{'id': f'https://openalex.org/{identifier}'}]}
               if status == 200 else {'error': 'bad_request', 'message': 'Invalid cursor'})
    return httpx2.Response(status, json=payload,
                          request=httpx2.Request('GET', 'https://api.openalex.org/works'))


@pytest.mark.asyncio
async def test_next_page_fetches_one_page_at_a_time_preserving_query(client, monkeypatch):
    request = AsyncMock(side_effect=[response('opaque+/=cursor'), response('cursor-3', 'W2'), response(None, 'W3')])
    monkeypatch.setattr(client.client, 'request', request)
    first = await client.works(cursor='*', filter='publication_year:2025', select='id', per_page=5)
    assert isinstance(first, WorksListResponse)
    assert request.await_count == 1  # No prefetch.
    assert first.meta.next_cursor == 'opaque+/=cursor'
    # The private continuation must not become a serialized API field.
    assert '_raqindex_next_page' not in first.model_dump()
    assert '_raqindex_next_page' not in first.model_dump_json()
    assert '_raqindex_next_page' not in first.model_extra
    second = await client.next_page(first)
    assert isinstance(second, WorksListResponse)
    assert second.results[0].id == 'https://openalex.org/W2'
    assert request.await_count == 2
    sent = request.call_args.kwargs['params']
    assert sent == {'cursor': 'opaque+/=cursor', 'filter': 'publication_year:2025', 'select': 'id', 'per_page': 5}
    third = await client.next_page(second)
    assert third.results[0].id == 'https://openalex.org/W3'
    assert request.await_count == 3
    assert await client.next_page(third) is None
    assert request.await_count == 3
    assert first.meta.next_cursor == 'opaque+/=cursor'


@pytest.mark.asyncio
async def test_existing_non_cursor_requests_are_not_changed(client, monkeypatch):
    request = AsyncMock(return_value=response('server-cursor'))
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.works(page=2)
    assert 'cursor' not in request.call_args.kwargs['params']
    assert await client.next_page(result) is None
    assert request.await_count == 1


@pytest.mark.asyncio
async def test_can_resume_from_saved_cursor(client, monkeypatch):
    request = AsyncMock(side_effect=[response('next'), response(None)])
    monkeypatch.setattr(client.client, 'request', request)
    page = await client.works(cursor='saved-checkpoint')
    assert request.call_args.kwargs['params']['cursor'] == 'saved-checkpoint'
    await client.next_page(page)
    assert request.call_args.kwargs['params']['cursor'] == 'next'


@pytest.mark.asyncio
async def test_separate_result_sets_do_not_overwrite_continuations(client, monkeypatch):
    request = AsyncMock(side_effect=[response('climate-2'), response('biology-2'), response(None), response(None)])
    monkeypatch.setattr(client.client, 'request', request)
    climate = await client.works(cursor='*', search='climate')
    biology = await client.works(cursor='*', search='biology')
    await client.next_page(climate)
    assert request.call_args.kwargs['params']['search'] == 'climate'
    assert request.call_args.kwargs['params']['cursor'] == 'climate-2'
    await client.next_page(biology)
    assert request.call_args.kwargs['params']['search'] == 'biology'
    assert request.call_args.kwargs['params']['cursor'] == 'biology-2'


@pytest.mark.asyncio
async def test_continuation_surfaces_errors_instead_of_treating_as_exhaustion(client, monkeypatch):
    client.endpoints['/works'].method.responses.bad_request = ReqRes(ref='Error', model=Error)
    request = AsyncMock(side_effect=[response('next'), response(None, status=400)])
    monkeypatch.setattr(client.client, 'request', request)
    page = await client.works(cursor='*')
    result = await client.next_page(page)
    assert isinstance(result, Error)
    assert result.message == 'Invalid cursor'
    assert await client.next_page(result) is None
    assert request.await_count == 2


@pytest.mark.asyncio
async def test_oqo_continuation_preserves_json_body_and_original_query(client, monkeypatch):
    query = OQO.model_validate({'get_rows': 'works', 'filter_rows': [{'column_id': 'publication_year', 'value': 2025}]})
    request = AsyncMock(side_effect=[response('next'), response(None)])
    monkeypatch.setattr(client.client, 'request', request)
    page = await client.oqo(query, cursor='*', per_page=10, select='id')
    original_body = request.call_args.kwargs['json']
    query.filter_rows.clear()  # Caller edits must not alter this page's continuation.
    await client.next_page(page)
    sent = request.call_args.kwargs
    assert 'params' not in sent
    assert sent['json']['oqo'] == original_body['oqo']
    assert sent['json']['cursor'] == 'next'
    assert sent['json']['per_page'] == 10
    assert sent['json']['select'] == 'id'
    assert original_body['cursor'] == '*'


@pytest.mark.asyncio
async def test_calculations_remain_unmodified(client, monkeypatch):
    from raqindex.models.calculation import CalculationResponse
    request = AsyncMock(return_value=httpx2.Response(200, json={
        'meta': {'next_cursor': 'not-an-entity-page'}, 'results': [],
        'summary': {'all': {'count': 1}}}, request=httpx2.Request('POST', 'https://api.openalex.org/')))
    monkeypatch.setattr(client.client, 'request', request)
    result = await client.oqo(OQO.model_validate({'get_rows': 'works', 'calculate': [{'measure': 'count'}]}), cursor='*')
    assert isinstance(result, CalculationResponse)
    assert await client.next_page(result) is None
    assert request.await_count == 1
