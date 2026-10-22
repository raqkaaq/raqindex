"""Exercise code generation in temporary directories; no upstream requests."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def generator():
    path = Path(__file__).parents[2] / 'scripts' / 'generate_schema.py'
    spec = importlib.util.spec_from_file_location('raqindex_codegen_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supplement_reuses_constraints_resolves_refs_and_is_idempotent(generator):
    schema = {'paths': {'/works': {'get': {'parameters': [
        {'$ref': '#/components/parameters/per_page'},
        {'name': 'sort', 'schema': {'type': 'string'}},
        {'name': 'filter', 'schema': {'type': 'string'}},
    ]}}}, 'components': {'parameters': {'per_page': {
        'name': 'per_page', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 25}}}}}
    original = deepcopy(schema)
    generator.supplement_schema(schema)
    shared = schema['components']['schemas']['RetrievalParameters']['properties']
    assert set(shared) == {'per_page', 'sort'}
    assert shared['per_page'] == original['components']['parameters']['per_page']['schema']
    assert schema['paths'] == original['paths']
    shared['per_page']['maximum'] = 50
    assert schema['components']['parameters']['per_page']['schema']['maximum'] == 100
    snapshot = deepcopy(schema)
    generator.supplement_schema(schema)
    assert schema == snapshot


@pytest.mark.parametrize('first', [True, False])
def test_factoring_preserves_model_contracts_and_endpoint_specific_fields(generator, tmp_path, first):
    base = 'class RetrievalParameters(BaseModel):\n    per_page: int = Field(25, ge=1, le=100)\n\n'
    child = ('class WorksParameters(BaseModel):\n    per_page: int = Field(25, ge=1, le=100)\n'
             '    search: str | None = None\n\n')
    text = 'from pydantic import BaseModel, Field\n\n' + (base + child if first else child + base)
    output = tmp_path / 'models.py'
    output.write_text(text)
    before = {}
    exec(compile(text, str(output), 'exec'), before)
    generator.share_retrieval_parameters(output)
    after = {}
    exec(compile(output.read_text(), str(output), 'exec'), after)
    assert issubclass(after['WorksParameters'], after['RetrievalParameters'])
    assert after['WorksParameters'].model_json_schema() == before['WorksParameters'].model_json_schema()
    assert after['WorksParameters'](search='climate').search == 'climate'


@pytest.mark.parametrize('field', [
    'per_page: int = Field(10, ge=1, le=100)',
    'per_page: int = Field(25, ge=1, le=50)',
    'per_page: str = "25"',
])
def test_factoring_does_not_replace_different_defaults_constraints_or_types(generator, tmp_path, field):
    output = tmp_path / 'models.py'
    output.write_text('from pydantic import BaseModel, Field\n\n'
        'class RetrievalParameters(BaseModel):\n    per_page: int = Field(25, ge=1, le=100)\n\n'
        f'class DifferentParameters(BaseModel):\n    {field}\n')
    generator.share_retrieval_parameters(output)
    namespace = {}
    exec(compile(output.read_text(), str(output), 'exec'), namespace)
    assert not issubclass(namespace['DifferentParameters'], namespace['RetrievalParameters'])


@pytest.mark.asyncio
async def test_generated_wrappers_route_list_and_id_without_exposing_auth(generator, tmp_path, monkeypatch):
    endpoint_file = tmp_path / 'endpoints.json'
    endpoint_file.write_text(json.dumps([
        {'endpoint': '/works', 'method': {'request': {'model': 'WorksGetParameters'}}},
        {'endpoint': '/works/{id}', 'method': {'request': {'model': 'WorksIdGetParameters'}}},
    ]))
    monkeypatch.setattr(generator, 'PROJECT_ROOT', tmp_path)
    generator.generate_wrappers(SimpleNamespace(endpoints_file_path=endpoint_file))
    namespace = {}
    exec(compile((tmp_path / 'src/raqindex/client/generated_endpoints.py').read_text(), '<wrappers>', 'exec'), namespace)
    calls = []

    class Client(namespace['GeneratedEndpoints']):
        def endpoint_factory(self, path):
            async def call(**params):
                calls.append((path, params))
                return 'ok'
            return call

    client = Client()
    assert await client.works(search='climate') == 'ok'
    assert await client.works(id='W1', select='id') == 'ok'
    assert calls == [('/works', {'search': 'climate'}), ('/works/{id}', {'id': 'W1', 'select': 'id'})]
    assert 'api_key' not in client.works.parameters
    assert 'mailto' not in client.works.parameters


@pytest.mark.parametrize('path', ['/invalid/{self}', '/invalid/{bad-name}', '/{value}'])
def test_wrapper_generation_rejects_invalid_python_names(generator, tmp_path, monkeypatch, path):
    endpoint_file = tmp_path / 'endpoints.json'
    endpoint_file.write_text(json.dumps([{'endpoint': path, 'method': {'request': {'model': 'WorksGetParameters'}}}]))
    monkeypatch.setattr(generator, 'PROJECT_ROOT', tmp_path)
    with pytest.raises(ValueError):
        generator.generate_wrappers(SimpleNamespace(endpoints_file_path=endpoint_file))


def test_generate_models_saves_the_same_supplemented_schema_it_generates(generator, tmp_path, monkeypatch):
    import httpx2
    from datamodel_code_generator import InputFileType

    schema = {'openapi': '3.1.0', 'info': {'title': 'Test', 'version': '1'},
        'paths': {'/works': {'get': {'operationId': 'listWorks', 'parameters': [
            {'name': 'per_page', 'in': 'query', 'schema': {'type': 'integer', 'default': 25, 'maximum': 100}}],
            'responses': {'200': {'description': 'ok', 'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/WorksListResponse'}}}}}}}},
        'components': {'schemas': {'WorksListResponse': {'type': 'object', 'properties': {
            'results': {'type': 'array', 'items': {'type': 'string'}}}}}}}
    monkeypatch.setattr(generator.httpx2, 'get', lambda *args, **kwargs: httpx2.Response(
        200, json=schema, request=httpx2.Request('GET', 'https://example.invalid/schema')))
    monkeypatch.setattr(generator, 'MODELS_DIR', tmp_path / 'models')
    spec_path, map_path = tmp_path / 'cache/spec.json', tmp_path / 'cache/map.json'
    generator.generate_models('https://example.invalid/schema', 'models.py', InputFileType.OpenAPI,
                              spec_path, map_path)
    saved = json.loads(spec_path.read_text())
    assert saved['components']['schemas']['RetrievalParameters']['properties']['per_page']['maximum'] == 100
    assert saved['paths'] == schema['paths']
    output = tmp_path / 'models/models.py'
    namespace = {}
    exec(compile(output.read_text(), str(output), 'exec'), namespace)
    assert namespace['RetrievalParameters']().per_page == 25
    assert 'WorksListResponse' in namespace
    assert json.loads(map_path.read_text())['models']


def test_failed_schema_fetch_does_not_overwrite_existing_files(generator, tmp_path, monkeypatch):
    import httpx2
    from datamodel_code_generator import InputFileType

    saved = tmp_path / 'spec.json'
    saved.write_text('original')
    monkeypatch.setattr(generator.httpx2, 'get', lambda *args, **kwargs: httpx2.Response(
        503, request=httpx2.Request('GET', 'https://example.invalid/schema')))
    with pytest.raises(httpx2.HTTPStatusError):
        generator.generate_models('https://example.invalid/schema', 'models.py', InputFileType.OpenAPI,
                                  saved, tmp_path / 'map.json')
    assert saved.read_text() == 'original'
    assert not (tmp_path / 'map.json').exists()


def test_mapping_resolves_success_and_error_refs_into_reloadable_metadata(generator, tmp_path, monkeypatch):
    import importlib
    from raqindex.models.endpoints import Endpoint
    from raqindex.models.openalex import WorksGetParameters, WorksListResponse, Error

    # Keep existing imported class identities stable during this unit test.
    monkeypatch.setattr(importlib, 'reload', lambda module: module)
    operation = {'operationId': 'listWorks', 'summary': 'List works', 'description': 'List works',
        'tags': ['Works'], 'responses': {
            '200': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/WorksListResponse'}}}},
            '400': {'$ref': '#/components/responses/BadRequest'}}}
    schema = {'paths': {'/works': {'get': operation}}, 'components': {'responses': {
        'BadRequest': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}}}}
    mapping = {'models': [
        {'source_path': ['paths', 'works', 'get', 'parameters'], 'class_name': 'WorksGetParameters', 'source_ref': '#/paths/works/get/parameters'},
        {'source_path': ['components', 'schemas', 'WorksListResponse'], 'class_name': 'WorksListResponse', 'source_ref': '#/components/schemas/WorksListResponse'},
    ]}
    env = SimpleNamespace(openalex_spec_file_path=tmp_path / 'spec.json',
                          openalex_model_map_file_path=tmp_path / 'map.json',
                          endpoints_file_path=tmp_path / 'generated/endpoints.json')
    env.openalex_spec_file_path.write_text(json.dumps(schema))
    env.openalex_model_map_file_path.write_text(json.dumps(mapping))
    generator.generate_mapping(env)
    metadata = json.loads(env.endpoints_file_path.read_text())
    assert len(metadata) == 1
    endpoint = Endpoint.model_validate(metadata[0])
    assert endpoint.endpoint == '/works'
    assert endpoint.method.request.model is WorksGetParameters
    assert endpoint.method.responses.success.model is WorksListResponse
    assert endpoint.method.responses.bad_request.model is Error
