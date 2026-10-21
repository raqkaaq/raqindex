import json
import pytest, pytest_asyncio
from raqindex import OpenAlexClient
from raqindex.client.environment import Environment

import logging
logging.getLogger("raqindex").setLevel(logging.WARNING)

@pytest.fixture
def environment_variables(monkeypatch, tmp_path):
    monkeypatch.setattr(Environment, "instance", None)
    # Set environment variables for testing
    monkeypatch.setenv("OPENALEX_API_KEY", "test_api_key")
    monkeypatch.setenv("OPENALEX_API_URL", "https://api.openalex.org")
    monkeypatch.setenv("OPENALEX_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("OPENALEX_OQO_SPEC_FILE_PATH", "oqo_spec.json")
    monkeypatch.setenv("OPENALEX_CACHE_DB_PATH", str(tmp_path / "openalex_cache.db"))
    monkeypatch.setenv("OPENALEX_CACHE_TTL", "1d")
    monkeypatch.setenv("EMAIL", "tests@example.invalid")
    monkeypatch.setenv("OPENALEX_SCHEMA_PATH", "schemas")
    for name in ("OPENALEX_OPENALEX_SPEC_FILE_PATH", "OPENALEX_OPENALEX_MODEL_MAP_FILE_PATH",
                 "OPENALEX_OQO_MODEL_MAP_FILE_PATH", "OPENALEX_ENDPOINTS_FILE_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr("dotenv.dotenv_values", lambda **kwargs: {})

    # Supply local endpoint metadata rather than relying on the user's cache.
    endpoints = []
    for path, request, response in (
        ("/works", "WorksGetParameters", "WorksListResponse"),
        ("/rate-limit", "RateLimitGetParameters", "RateLimitResponse"),
    ):
        endpoints.append({"endpoint": path, "method": {
            "method": "GET", "operationId": path[1:], "summary": "Test endpoint",
            "description": "Test endpoint", "tags": [],
            "request": {"ref": request, "model": request},
            "responses": {"200": {"ref": response, "model": response}},
        }})
    schema_path = tmp_path / "schemas"
    schema_path.mkdir()
    (schema_path / "openalex_endpoints.json").write_text(json.dumps(endpoints))

@pytest_asyncio.fixture
async def client(environment_variables):
    async with OpenAlexClient() as client:        
        yield client
