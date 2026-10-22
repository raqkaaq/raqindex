from unittest.mock import AsyncMock

import httpx2
import pytest

from raqindex.models.calculation import CalculationResponse
from raqindex.models.oqo import Model as OQO
from raqindex.models.openalex import WorksListResponse


@pytest.mark.asyncio
@pytest.mark.parametrize('grouped', [True, False])
async def test_oqo_calculation(client, monkeypatch, grouped):
    payload = {
        'meta': {'count': 12, 'measures': [{'key': 'count'}, {'key': 'mean_fwci'}]},
        'summary': {'all': {'count': 12, 'mean_fwci': None}},
    }
    if grouped:
        payload['group_by'] = [{'key': 2024, 'count': 8, 'mean_fwci': 1.5,
                                'groups': [{'key': 'article', 'count': 6}]}]
        payload['summary']['splits'] = [{'groups': [{'key': 2024, 'count': 8}]}]
    request = AsyncMock(return_value=httpx2.Response(
        200, json=payload, request=httpx2.Request('POST', 'https://api.openalex.org/')))
    monkeypatch.setattr(client.client, 'request', request)
    query = OQO.model_validate({
        'get_rows': 'works', 'calculate': [{'measure': 'count'}],
        'group_by': [{'column_id': 'publication_year'}] if grouped else [],
    })
    result = await client.oqo(query)
    assert isinstance(result, CalculationResponse)
    assert result.summary.all.measure_values(result.meta.measures) == {'count': 12, 'mean_fwci': None}
    assert result.model_extra['request'].api_key == '<redacted>'
    if grouped:
        assert result.group_by[0].groups[0].key == 'article'
        assert result.group_by[0].measure_values(result.meta.measures)['mean_fwci'] == 1.5
        assert result.summary.splits[0].groups[0].key == 2024


@pytest.mark.asyncio
async def test_oqo_entity_response(client, monkeypatch):
    monkeypatch.setattr(client.client, 'request', AsyncMock(return_value=httpx2.Response(
        200, json={'meta': {'count': 0}, 'results': []},
        request=httpx2.Request('POST', 'https://api.openalex.org/'))))
    result = await client.oqo(OQO(get_rows='works'))
    assert isinstance(result, WorksListResponse)
    assert result.results == []


@pytest.mark.asyncio
@pytest.mark.parametrize('fixture', ['crispr', 'nested'])
async def test_production_derived_calculations(client, monkeypatch, fixture):
    import json
    from pathlib import Path

    payload = json.loads((Path(__file__).parents[1] / 'fixtures' / 'calculation' / f'{fixture}.json').read_text())
    monkeypatch.setattr(client.client, 'request', AsyncMock(return_value=httpx2.Response(
        200, json=payload, request=httpx2.Request('POST', 'https://api.openalex.org/'))))
    result = await client.oqo(OQO.model_validate({
        'get_rows': 'works', 'group_by': [{'column_id': 'type'}],
    }))
    assert isinstance(result, CalculationResponse)
    assert result.meta.x_query.oql == payload['meta']['x_query']['oql']
    assert result.meta.x_query.oqo is None
    assert result.model_dump(include={'meta', 'summary', 'group_by'}, exclude_unset=True) == {
        key: payload[key] for key in ('meta', 'summary', 'group_by')
    }
    if fixture == 'nested':
        # A declared measure can be absent from the whole-set summary.
        assert result.summary.all.measure_values(result.meta.measures) == {'count': 125895}
        assert result.meta.splits[0].kind == 'conditions'
        assert result.group_by[0].groups[0].count == 295


def test_calculation_requires_metadata():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CalculationResponse.model_validate({})


@pytest.mark.parametrize('value', [0, 12, 1.849, '2026-10-21', True, None])
def test_calculated_scalar_preserves_type(value):
    from raqindex.models.calculation import CalculationRow, MeasureDescriptor

    row = CalculationRow.model_validate({'metric': value})
    actual = row.measure_values([MeasureDescriptor(key='metric')])['metric']
    assert actual == value
    assert type(actual) is type(value)
    assert row.model_dump()['metric'] == value


@pytest.mark.parametrize('value', [[], {}, [1, 2], {'value': 1}])
@pytest.mark.parametrize('location', ['group', 'nested', 'summary'])
def test_calculated_containers_are_rejected(value, location):
    from pydantic import ValidationError

    row = {'metric': value}
    payload = {'meta': {}}
    if location == 'summary':
        payload['summary'] = {'all': row}
    elif location == 'nested':
        payload['group_by'] = [{'key': 'outer', 'groups': [row]}]
    else:
        payload['group_by'] = [row]
    with pytest.raises(ValidationError):
        CalculationResponse.model_validate(payload)


@pytest.mark.asyncio
async def test_concurrent_entity_and_calculation_keep_response_models_separate(client, monkeypatch):
    import asyncio

    original_root = client.endpoints['/'].method.responses.success
    original_works = client.endpoints['/works'].method.responses.success
    both_started = asyncio.Event()
    bodies = []

    async def request(**kwargs):
        bodies.append(kwargs['json'])
        if len(bodies) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        body = kwargs['json']
        if body['oqo'].get('calculate'):
            payload = {'meta': {'count': 7}, 'summary': {'all': {'count': 7}}}
        else:
            payload = {'meta': {'count': 1}, 'results': [{'id': 'https://openalex.org/W1'}]}
        return httpx2.Response(
            200, json=payload,
            request=httpx2.Request('POST', 'https://api.openalex.org/', json=body),
        )

    monkeypatch.setattr(client.client, 'request', request)
    calculation, entity = await asyncio.gather(
        client.oqo(OQO.model_validate({'get_rows': 'works', 'calculate': [{'measure': 'count'}]})),
        client.oqo(OQO(get_rows='works')),
    )

    assert isinstance(calculation, CalculationResponse)
    assert calculation.summary.all.count == 7
    assert isinstance(entity, WorksListResponse)
    assert entity.results[0].id == 'https://openalex.org/W1'
    assert client.endpoints['/'].method.responses.success is original_root
    assert client.endpoints['/works'].method.responses.success is original_works
    assert original_root.model is None
    assert original_works.model is WorksListResponse
    assert len(bodies) == 2
    assert all('api_key' not in body for body in bodies)


@pytest.mark.parametrize('section', ['group_by', 'summary'])
def test_deep_invalid_measure_reports_exact_location(section):
    from pydantic import ValidationError

    invalid = {'key': 'leaf', 'metric': {'unexpected': 'object'}}
    payload = {'meta': {}}
    if section == 'group_by':
        payload['group_by'] = [{'groups': [{'groups': [invalid]}]}]
        prefix = ('group_by', 0, 'groups', 0, 'groups', 0, 'metric')
    else:
        payload['summary'] = {'splits': [{'groups': [invalid]}]}
        prefix = ('summary', 'splits', 0, 'groups', 0, 'metric')

    with pytest.raises(ValidationError) as error:
        CalculationResponse.model_validate(payload)
    assert all(item['loc'][:len(prefix)] == prefix for item in error.value.errors())


def test_measure_lookup_uses_keys_and_distinguishes_missing_from_null():
    from raqindex.models.calculation import CalculationRow, MeasureDescriptor

    row = CalculationRow.model_validate({'mean_fwci': None, 'count': 0, 'other': 42})
    measures = [MeasureDescriptor(key=key) for key in ['missing', 'count', 'mean_fwci']]
    assert row.measure_values(measures) == {'count': 0, 'mean_fwci': None}
    # Selecting declared measures must not discard undeclared scalar data.
    assert row.model_dump()['other'] == 42
