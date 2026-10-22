import json

import pytest
from pydantic import ValidationError

from raqindex.models import OQO
from raqindex.models.endpoints import ReqRes, ResponseCollection
from raqindex.models.openalex import Error
from raqindex.models.query import QueryRequest, QueryResponse, QueryErrorResponse


@pytest.mark.parametrize('kind', ['parse_error', 'bad_request', 'invalid_entity', 'invalid_body'])
def test_documented_query_error_types(kind):
    result = QueryErrorResponse.model_validate({'validation': {
        'valid': False, 'errors': [{'type': kind, 'message': 'Fix the query'}], 'warnings': []}})
    assert result.validation.errors[0].type == kind
    assert result.validation.errors[0].location is None


def test_warning_strings_and_structured_diagnostics():
    result = QueryErrorResponse.model_validate({'validation': {'valid': False, 'errors': ['legacy error'],
        'warnings': [{'type': 'warning', 'message': 'Check this'}]}})
    assert result.validation.errors == ['legacy error']
    assert result.validation.warnings[0].message == 'Check this'


@pytest.mark.parametrize('per_page', [0, 101])
def test_retrieval_bounds_apply_to_query_requests(per_page):
    with pytest.raises(ValidationError):
        QueryRequest(oqo=OQO(get_rows='works'), per_page=per_page)


def test_optional_preflight_check_and_null_classic_url():
    result = QueryResponse.model_validate({'oql': 'get works', 'oqo': {'get_rows': 'works'},
        'oxurl': None, 'validation': {'valid': True, 'errors': [], 'warnings': []}})
    assert result.url is None
    assert result.check is None


def test_cost_and_estimate_are_typed_when_present():
    payload = {'oql': 'get works', 'oqo': {'get_rows': 'works'}, 'validation': {
        'valid': True, 'errors': [], 'warnings': []}, 'check': {'valid': True, 'limits': [],
        'cost': {'credits': 1, 'usd': 0.0001, 'steps': [{'what': 'starting set', 'credits': 1}]},
        'estimate': {'seconds': 3.3, 'es_calls': 1, 'budget_seconds': 10, 'within_budget': True}}}
    result = QueryResponse.model_validate(payload)
    assert result.check.cost.credits == 1
    assert result.check.estimate.within_budget is True


def test_endpoint_metadata_json_round_trip_resolves_generated_models():
    responses = ResponseCollection.model_validate({'200': ReqRes(ref='Error', model=Error)})
    serialized = responses.model_dump_json(by_alias=True, exclude_none=True)
    assert json.loads(serialized) == {'200': {'ref': 'Error', 'model': 'Error'}}
    restored = ResponseCollection.model_validate_json(serialized)
    assert restored.success.model is Error


def test_unrecognized_response_status_is_rejected():
    with pytest.raises(ValidationError):
        ResponseCollection.model_validate({'299': {'ref': 'Error', 'model': 'Error'}})


@pytest.mark.parametrize('body', [
    {'get_rows': 'not-an-entity'},
    {'get_rows': 'works', 'unknown_field': 3},
    {'get_rows': 'works', 'sample': 0},
    {'get_rows': 'works', 'group_by': [{'column_id': 'type'}] * 4},
    {'get_rows': 'works', 'filter_rows': [{'join': 'and', 'filters': []}]},
])
def test_oqo_rejects_invalid_query_structure(body):
    with pytest.raises(ValidationError):
        OQO.model_validate(body)


def test_nested_oqo_round_trip_preserves_values_and_polarity():
    body = {'get_rows': 'works', 'filter_rows': [{'join': 'or', 'filters': [
        {'column_id': 'publication_year', 'operator': '>=', 'value': 2020},
        {'column_id': 'open_access.is_oa', 'value': True, 'is_negated': True}]}]}
    first = OQO.model_validate(body)
    dumped = first.model_dump(mode='json', exclude_none=True)
    assert dumped['filter_rows'][0]['filters'][0]['value'] == 2020
    assert dumped['filter_rows'][0]['filters'][1]['value'] is True
    assert dumped['filter_rows'][0]['filters'][1]['is_negated'] is True
    assert OQO.model_validate_json(first.model_dump_json()).model_dump() == first.model_dump()
