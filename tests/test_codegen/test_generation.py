"""Exercise code generation in temporary directories; no upstream requests."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def generator():
    from raqindex.codegen import SchemaGenerator

    return SchemaGenerator(env=SimpleNamespace())


def test_supplement_reuses_constraints_resolves_refs_and_is_idempotent(generator):
    schema = {
        "paths": {
            "/works": {
                "get": {
                    "parameters": [
                        {"$ref": "#/components/parameters/per_page"},
                        {"name": "sort", "schema": {"type": "string"}},
                        {"name": "filter", "schema": {"type": "string"}},
                    ]
                }
            }
        },
        "components": {
            "parameters": {
                "per_page": {
                    "name": "per_page",
                    "schema": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 25,
                    },
                }
            }
        },
    }
    original = deepcopy(schema)
    generator.supplement_schema(schema)
    shared = schema["components"]["schemas"]["RetrievalParameters"]["properties"]
    assert set(shared) == {"per_page", "sort"}
    assert (
        shared["per_page"] == original["components"]["parameters"]["per_page"]["schema"]
    )
    original_parameters = original["paths"]["/works"]["get"]["parameters"]
    assert (
        schema["paths"]["/works"]["get"]["parameters"][: len(original_parameters)]
        == original_parameters
    )
    shared["per_page"]["maximum"] = 50
    assert schema["components"]["parameters"]["per_page"]["schema"]["maximum"] == 100
    snapshot = deepcopy(schema)
    generator.supplement_schema(schema)
    assert schema == snapshot


@pytest.mark.parametrize("first", [True, False])
def test_factoring_preserves_model_contracts_and_endpoint_specific_fields(
    generator, tmp_path, first
):
    base = "class RetrievalParameters(BaseModel):\n    per_page: int = Field(25, ge=1, le=100)\n\n"
    child = (
        "class WorksParameters(BaseModel):\n    per_page: int = Field(25, ge=1, le=100)\n"
        "    search: str | None = None\n\n"
    )
    text = "from pydantic import BaseModel, Field\n\n" + (
        base + child if first else child + base
    )
    output = tmp_path / "models.py"
    output.write_text(text)
    before = {}
    exec(compile(text, str(output), "exec"), before)
    generator.share_retrieval_parameters(output)
    after = {}
    exec(compile(output.read_text(), str(output), "exec"), after)
    assert issubclass(after["WorksParameters"], after["RetrievalParameters"])
    assert (
        after["WorksParameters"].model_json_schema()
        == before["WorksParameters"].model_json_schema()
    )
    assert after["WorksParameters"](search="climate").search == "climate"


@pytest.mark.parametrize(
    "field",
    [
        "per_page: int = Field(10, ge=1, le=100)",
        "per_page: int = Field(25, ge=1, le=50)",
        'per_page: str = "25"',
    ],
)
def test_factoring_does_not_replace_different_defaults_constraints_or_types(
    generator, tmp_path, field
):
    output = tmp_path / "models.py"
    output.write_text(
        "from pydantic import BaseModel, Field\n\n"
        "class RetrievalParameters(BaseModel):\n    per_page: int = Field(25, ge=1, le=100)\n\n"
        f"class DifferentParameters(BaseModel):\n    {field}\n"
    )
    generator.share_retrieval_parameters(output)
    namespace = {}
    exec(compile(output.read_text(), str(output), "exec"), namespace)
    assert not issubclass(
        namespace["DifferentParameters"], namespace["RetrievalParameters"]
    )


@pytest.mark.asyncio
async def test_generated_wrappers_route_list_and_id_without_exposing_auth(
    generator, tmp_path, monkeypatch
):
    endpoint_file = tmp_path / "endpoints.json"
    endpoint_file.write_text(
        json.dumps(
            [
                {
                    "endpoint": "/works",
                    "method": {
                        "method": "GET",
                        "request": {
                            "ref": "WorksGetParameters",
                            "model": "WorksGetParameters",
                        },
                        "responses": {},
                    },
                },
                {
                    "endpoint": "/works/{id}",
                    "method": {
                        "method": "GET",
                        "request": {
                            "ref": "WorksIdGetParameters",
                            "model": "WorksIdGetParameters",
                        },
                        "responses": {},
                    },
                },
            ]
        )
    )
    monkeypatch.setattr(generator, "project_root", tmp_path)
    generator.env = SimpleNamespace(endpoints_file_path=endpoint_file)
    generator.generate_wrappers()
    namespace = {}
    exec(
        compile(
            (tmp_path / "src/raqindex/generated/endpoints.py").read_text(),
            "<wrappers>",
            "exec",
        ),
        namespace,
    )
    calls = []

    class Client(namespace["GeneratedEndpoints"]):
        def endpoint_factory(self, path, *, method=None):
            assert method == "GET"

            async def call(**params):
                calls.append((path, params))
                return "ok"

            return call

    client = Client()
    assert await client.works(search="climate") == "ok"
    assert await client.works(id="W1", select="id") == "ok"
    assert calls == [
        ("/works", {"search": "climate"}),
        ("/works/{id}", {"id": "W1", "select": "id"}),
    ]
    assert "api_key" not in client.works.parameters
    assert "mailto" not in client.works.parameters




def test_generate_models_saves_the_same_supplemented_schema_it_generates(
    generator, tmp_path, monkeypatch
):
    import httpx2
    from datamodel_code_generator import InputFileType

    schema = {
        "openapi": "3.1.0",
        "info": {"title": "Test", "version": "1"},
        "paths": {
            "/works": {
                "get": {
                    "operationId": "listWorks",
                    "parameters": [
                        {
                            "name": "per_page",
                            "in": "query",
                            "schema": {
                                "type": "integer",
                                "default": 25,
                                "maximum": 100,
                            },
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/WorksListResponse"
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "WorksListResponse": {
                    "type": "object",
                    "properties": {
                        "results": {"type": "array", "items": {"type": "string"}}
                    },
                }
            }
        },
    }
    monkeypatch.setattr(
        httpx2,
        "get",
        lambda *args, **kwargs: httpx2.Response(
            200,
            json=schema,
            request=httpx2.Request("GET", "https://example.invalid/schema"),
        ),
    )
    monkeypatch.setattr(generator, "models_dir", tmp_path / "models")
    spec_path, map_path = tmp_path / "cache/spec.json", tmp_path / "cache/map.json"
    generator.generate_models(
        "https://example.invalid/schema",
        "models.py",
        InputFileType.OpenAPI,
        spec_path,
        map_path,
    )
    saved = json.loads(spec_path.read_text())
    assert (
        saved["components"]["schemas"]["RetrievalParameters"]["properties"]["per_page"][
            "maximum"
        ]
        == 100
    )
    assert (
        saved["paths"]["/works"]["get"]["responses"]
        == schema["paths"]["/works"]["get"]["responses"]
    )
    output = tmp_path / "models/models.py"
    namespace = {}
    exec(compile(output.read_text(), str(output), "exec"), namespace)
    assert namespace["RetrievalParameters"]().per_page == 25
    works = namespace["WorksGetParameters"]
    request = works.model_validate({"search.semantic": "climate"})
    assert request.search_semantic == "climate"
    assert (
        request.model_dump(by_alias=True, exclude_none=True)["search.semantic"]
        == "climate"
    )
    assert works.model_fields["rerank"].annotation == bool | None
    assert "search_semantic" not in namespace["RetrievalParameters"].model_fields
    assert "WorksListResponse" in namespace
    assert json.loads(map_path.read_text())["models"]


def test_failed_schema_fetch_does_not_overwrite_existing_files(
    generator, tmp_path, monkeypatch
):
    import httpx2
    from datamodel_code_generator import InputFileType

    saved = tmp_path / "spec.json"
    saved.write_text("original")
    monkeypatch.setattr(
        httpx2,
        "get",
        lambda *args, **kwargs: httpx2.Response(
            503, request=httpx2.Request("GET", "https://example.invalid/schema")
        ),
    )
    with pytest.raises(httpx2.HTTPStatusError):
        generator.generate_models(
            "https://example.invalid/schema",
            "models.py",
            InputFileType.OpenAPI,
            saved,
            tmp_path / "map.json",
        )
    assert saved.read_text() == "original"
    assert not (tmp_path / "map.json").exists()


def test_mapping_resolves_success_and_error_refs_into_reloadable_metadata(
    generator, tmp_path, monkeypatch
):
    import importlib
    from raqindex.models.endpoints import Endpoint
    from raqindex.generated.openalex import WorksGetParameters, WorksListResponse, Error

    # Keep existing imported class identities stable during this unit test.
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    operation = {
        "operationId": "listWorks",
        "summary": "List works",
        "description": "List works",
        "tags": ["Works"],
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/WorksListResponse"}
                    }
                }
            },
            "400": {"$ref": "#/components/responses/BadRequest"},
        },
    }
    schema = {
        "paths": {"/works": {"get": operation}},
        "components": {
            "schemas": {
                "WorksListResponse": {"type": "object"},
                "Error": {"type": "object"},
            },
            "responses": {
                "BadRequest": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Error"}
                        }
                    }
                }
            },
        },
    }
    mapping = {
        "models": [
            {
                "source_path": ["paths", "/works", "get"],
                "class_name": "WorksGetParameters",
                "source_ref": "#/paths/~1works/get",
            },
            {"source_path": ["components", "schemas", "Error"], "class_name": "Error"},
            {
                "source_path": ["components", "schemas", "WorksListResponse"],
                "class_name": "WorksListResponse",
                "source_ref": "#/components/schemas/WorksListResponse",
            },
        ]
    }
    env = SimpleNamespace(
        openalex_spec_file_path=tmp_path / "spec.json",
        openalex_model_map_file_path=tmp_path / "map.json",
        endpoints_file_path=tmp_path / "generated/endpoints.json",
    )
    env.openalex_spec_file_path.write_text(json.dumps(schema))
    env.openalex_model_map_file_path.write_text(json.dumps(mapping))
    generator.env = env
    generator.generate_mapping()
    metadata = json.loads(env.endpoints_file_path.read_text())
    assert len(metadata) == 1
    endpoint = Endpoint.model_validate(
        metadata[0], context={"models": generator._registry()}
    )
    assert endpoint.endpoint == "/works"
    assert endpoint.method.request.model is WorksGetParameters
    assert endpoint.method.responses.success.model is WorksListResponse
    assert endpoint.method.responses.bad_request.model is Error

    # Later stages use the metadata and model registry produced by this instance.
    generator.project_root = tmp_path

    def unexpected_read(path):
        pytest.fail(f"Unexpected artifact reload: {path}")

    monkeypatch.setattr(generator, "_read_json", unexpected_read)
    generator.generate_wrappers()
    namespace = {}
    exec(compile(generator.wrappers_path.read_text(), "<wrappers>", "exec"), namespace)
    assert (
        "/works -> WorksGetParameters"
        == namespace["GeneratedEndpoints"].works.endpoints
    )


def test_run_generates_models_before_mapping_and_wrappers(tmp_path, monkeypatch):
    from unittest.mock import Mock, call
    from datamodel_code_generator import InputFileType
    from raqindex.codegen import SchemaGenerator

    env = SimpleNamespace(
        oqo_spec_file_path=tmp_path / "oqo.json",
        oqo_model_map_file_path=tmp_path / "oqo-map.json",
        openalex_spec_file_path=tmp_path / "openalex.json",
        openalex_model_map_file_path=tmp_path / "openalex-map.json",
    )
    generator = SchemaGenerator(env, project_root=tmp_path)
    steps = Mock()
    for name in ("generate_models", "generate_mapping", "generate_wrappers", "generate_package"):
        monkeypatch.setattr(generator, name, getattr(steps, name))
    generator.run()
    assert steps.mock_calls == [
        call.generate_models(
            "https://api.openalex.org/query/spec/oqo",
            "oqo.py",
            InputFileType.JsonSchema,
            env.oqo_spec_file_path,
            env.oqo_model_map_file_path,
        ),
        call.generate_models(
            "https://help.openalex.org/openapi.json",
            "openalex.py",
            InputFileType.OpenAPI,
            env.openalex_spec_file_path,
            env.openalex_model_map_file_path,
        ),
        call.generate_mapping(),
        call.generate_wrappers(),
        call.generate_package(),
    ]
    assert generator.models_dir == tmp_path / "src/raqindex/generated"


def test_search_supplements_are_operation_specific_and_preserve_upstream(generator):
    upstream = {
        "name": "search.exact",
        "in": "query",
        "schema": {"type": "string", "maxLength": 123},
        "description": "Upstream definition",
    }
    schema = {
        "paths": {
            "/works": {
                "parameters": [{"$ref": "#/components/parameters/exact"}],
                "get": {
                    "parameters": [
                        {
                            "name": "per_page",
                            "in": "query",
                            "schema": {"type": "integer"},
                        }
                    ]
                },
                "post": {"parameters": []},
            },
            "/authors": {
                "get": {
                    "parameters": [
                        {"name": "search", "in": "query", "schema": {"type": "string"}}
                    ]
                }
            },
        },
        "components": {
            "parameters": {"exact": upstream},
            "schemas": {"RetrievalParameters": {"type": "object", "properties": {}}},
        },
    }
    original = deepcopy(schema)
    generator.supplement_schema(schema)
    parameters = schema["paths"]["/works"]["get"]["parameters"]
    names = {parameter["name"] for parameter in parameters}
    assert names == {
        "per_page",
        "search.title",
        "search.title.exact",
        "search.title_and_abstract",
        "search.title_and_abstract.exact",
        "search.title_abstract_keywords",
        "search.title_abstract_keywords.exact",
        "search.semantic",
        "rerank",
    }
    assert schema["components"] == original["components"]
    author_parameters = schema["paths"]["/authors"]["get"]["parameters"]
    assert author_parameters[0] == original["paths"]["/authors"]["get"]["parameters"][0]
    assert [parameter["name"] for parameter in author_parameters] == [
        "search",
        "search.exact",
    ]
    assert schema["paths"]["/works"]["post"] == original["paths"]["/works"]["post"]
    assert (
        schema["paths"]["/works"]["parameters"]
        == original["paths"]["/works"]["parameters"]
    )
    snapshot = deepcopy(schema)
    generator.supplement_schema(schema)
    assert schema == snapshot


@pytest.mark.parametrize(
    "entity",
    [
        "authors",
        "sources",
        "institutions",
        "topics",
        "keywords",
        "publishers",
        "funders",
        "domains",
        "fields",
        "subfields",
        "sdgs",
        "countries",
        "continents",
        "languages",
        "awards",
        "concepts",
        "work-types",
        "source-types",
        "institution-types",
        "licenses",
        "indexes",
    ],
)
def test_exact_search_is_available_on_entity_lists_only(generator, entity):
    search = {"name": "search", "in": "query", "schema": {"type": "string"}}
    schema = {
        "paths": {
            "/works": {
                "get": {
                    "parameters": [
                        {
                            "name": "per_page",
                            "in": "query",
                            "schema": {"type": "integer"},
                        }
                    ]
                }
            },
            f"/{entity}": {"get": {"parameters": [deepcopy(search)]}},
            f"/{entity}/{{id}}": {"get": {"parameters": []}},
            "/autocomplete/{entity}": {
                "get": {
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}}
                    ]
                }
            },
            "/rate-limit": {"get": {"parameters": []}},
        }
    }
    untouched = deepcopy(schema["paths"])
    generator.supplement_schema(schema)
    parameters = schema["paths"][f"/{entity}"]["get"]["parameters"]
    assert [parameter["name"] for parameter in parameters] == ["search", "search.exact"]
    assert parameters[1]["schema"] == {"type": "string"}
    assert parameters[1]["required"] is False
    for path in (f"/{entity}/{{id}}", "/autocomplete/{entity}", "/rate-limit"):
        assert schema["paths"][path] == untouched[path]


@pytest.mark.asyncio
async def test_full_shared_generation_round_trip_and_multi_method_dispatch(
    tmp_path, monkeypatch
):
    """Generate real artifacts and execute them through the OpenAlex adapter."""
    from unittest.mock import AsyncMock
    import httpx2
    from raq_helpers.models import Endpoint
    from raqindex import OpenAlexClient
    from raqindex.client.endpoint_factory import OpenAlexEndpointFactory
    from raqindex.codegen import SchemaGenerator

    schema = {
        "openapi": "3.1.0",
        "info": {"title": "Offline", "version": "1"},
        "paths": {
            "/works": {
                "get": {
                    "parameters": [
                        {
                            "name": "api_key",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "per_page",
                            "in": "query",
                            "schema": {"type": "integer", "default": 25},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ReadResponse"
                                    }
                                }
                            },
                        }
                    },
                },
                "post": {
                    "parameters": [
                        {
                            "name": "label",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "201": {
                            "description": "created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/WriteResponse"
                                    }
                                }
                            },
                        }
                    },
                },
            }
        },
        "components": {
            "schemas": {
                "ReadResponse": {
                    "type": "object",
                    "properties": {
                        "results": {"type": "array", "items": {"type": "string"}}
                    },
                },
                "WriteResponse": {
                    "type": "object",
                    "properties": {"created": {"type": "boolean"}},
                },
            }
        },
    }
    schema["components"]["schemas"]["EntityType"] = {"type": "string", "enum": ["works", "authors"]}
    oqo_schema = {
        "type": "object",
        "properties": {"get_rows": {"$ref": "#/$defs/EntityType"}},
        "$defs": {"EntityType": {"type": "string", "enum": ["works"]}},
    }
    original = deepcopy(schema)
    env = SimpleNamespace(
        **{
            name: tmp_path / f"{name}.json"
            for name in (
                "oqo_spec_file_path",
                "oqo_model_map_file_path",
                "openalex_spec_file_path",
                "openalex_model_map_file_path",
                "endpoints_file_path",
            )
        }
    )
    generator = SchemaGenerator(env, project_root=tmp_path)
    monkeypatch.setattr(
        generator,
        "fetch_schema",
        lambda url: oqo_schema if url.endswith("/oqo") else schema,
    )
    generator.run()
    assert schema == original
    assert all(isinstance(endpoint, Endpoint) for endpoint in generator.endpoints)
    assert (generator.models_dir / "oqo_registry.py").is_file()
    assert (generator.models_dir / "openalex_registry.py").is_file()
    initializer = generator.models_dir / "__init__.py"
    assert initializer.is_file()
    package_exports = {"__package__": generator._models_package()}
    exec(compile(initializer.read_text(), str(initializer), "exec"), package_exports)
    assert package_exports["OQO"]().get_rows is None
    assert package_exports["GeneratedEndpoints"].works
    registry = generator._registry()
    assert package_exports["OPENALEX_MODEL_REGISTRY"]["ReadResponse"] is registry["ReadResponse"]
    assert all(package_exports[name] is model for name, model in registry.items())
    assert package_exports["OQOEntityType"] is package_exports["OQO_MODEL_REGISTRY"]["EntityType"]
    assert package_exports["EntityType"] is not package_exports["OQOEntityType"]
    factory = OpenAlexEndpointFactory.from_json(
        env.endpoints_file_path.read_text(), registry=registry
    )
    assert (
        factory.get_endpoint("/works", "GET").method.request.model
        is registry["WorksGetParameters"]
    )
    namespace = {}
    exec(compile(generator.wrappers_path.read_text(), "<wrappers>", "exec"), namespace)

    class GeneratedClient(namespace["GeneratedEndpoints"], OpenAlexClient):
        def __init__(self):
            self.factory = factory
            self.api_key = "secret"
            self.client = SimpleNamespace(
                request=AsyncMock(
                    side_effect=[
                        httpx2.Response(
                            200,
                            json={"results": ["one"]},
                            request=httpx2.Request(
                                "GET", "https://example.invalid/works"
                            ),
                        ),
                        httpx2.Response(
                            201,
                            json={"created": True},
                            request=httpx2.Request(
                                "POST", "https://example.invalid/works"
                            ),
                        ),
                    ]
                )
            )

    client = GeneratedClient()
    with pytest.raises(TypeError, match="Ambiguous HTTP method"):
        await client.works()
    result = await client.works(http_method="get", per_page=3)
    assert isinstance(result, registry["ReadResponse"])
    assert result.results == ["one"]
    assert client.client.request.call_args.kwargs["params"] == {"per_page": 3}
    result = await client.works(http_method="post", label="two")
    assert isinstance(result, registry["WriteResponse"])
    assert result.created is True
    assert client.client.request.call_args.kwargs["method"] == "POST"
    assert client.client.request.call_args.kwargs["params"] == {"label": "two"}
    assert "api_key" not in client.works.parameters


def test_regeneration_loads_new_modules_without_running_stale_initializer(tmp_path):
    import subprocess
    import sys

    output = tmp_path / 'src/raqindex/generated'
    output.mkdir(parents=True)
    (output / '__init__.py').write_text('raise RuntimeError("stale package exports")\n')
    (output / 'openalex.py').write_text('fresh_generation = True\n')
    subprocess.run(
        [sys.executable, '-B', '-c', '''
import sys
from pathlib import Path
from types import SimpleNamespace
import raqindex.codegen as codegen
codegen.PROJECT_ROOT = Path(sys.argv[1])
generator = codegen.SchemaGenerator(env=SimpleNamespace(), project_root=codegen.PROJECT_ROOT)
assert generator._load_models().fresh_generation
''', str(tmp_path)],
        check=True, capture_output=True, text=True, timeout=10,
    )
