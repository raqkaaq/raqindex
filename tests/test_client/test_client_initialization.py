import importlib
from unittest.mock import Mock

import pytest

from raqindex import OpenAlexClient

client_module = importlib.import_module("raqindex.client.client")


def test_client_http_configuration(client):
    assert client.api_key == "test_api_key"
    assert client.url == "https://api.openalex.org"
    assert str(client.client.base_url).rstrip("/") == client.url
    assert client.client.headers["Authorization"] == "Bearer test_api_key"
    assert client.client.max_redirects == 10
    assert client.client.timeout.connect == 60.0
    assert client.client.timeout.read == 10.0
    assert client.client.timeout.write == 10.0
    assert client.client.timeout.pool == 10.0


@pytest.mark.asyncio
async def test_client_cache_configuration(environment_variables, monkeypatch, tmp_path):
    from raq_helpers.client import client as shared_client

    storage_factory = Mock(wraps=shared_client.AsyncSqliteStorage)
    monkeypatch.setattr(shared_client, "AsyncSqliteStorage", storage_factory)

    async with OpenAlexClient() as client:
        storage_factory.assert_called_once_with(
            database_path=tmp_path / "openalex_cache.db",
            default_ttl=86400,
        )
        assert client.storage is not None


def test_client_preserves_environment_failure(monkeypatch):
    original_error = ValueError("Missing API key")
    monkeypatch.setattr(client_module, "Environment", Mock(side_effect=original_error))

    with pytest.raises(
        ValueError, match=r"^Failed to initialize OpenAlexClient\.$"
    ) as error:
        OpenAlexClient()

    assert error.value.__cause__ is original_error


@pytest.mark.asyncio
async def test_client_redis_configuration(environment_variables, monkeypatch, tmp_path):
    from raq_helpers.client import client as shared_client

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("OPENALEX_CACHE_KEY_PREFIX", "test-raqindex")
    monkeypatch.setenv("OPENALEX_CACHE_MAX_RESPONSE_BYTES", "67108864")
    storage_factory = Mock(wraps=shared_client.AsyncRedisStorage)
    sqlite_factory = Mock(side_effect=AssertionError("SQLite must not be opened"))
    monkeypatch.setattr(shared_client, "AsyncRedisStorage", storage_factory)
    monkeypatch.setattr(shared_client, "AsyncSqliteStorage", sqlite_factory)

    async with OpenAlexClient():
        kwargs = storage_factory.call_args.kwargs
        assert storage_factory.call_count == 1
        assert kwargs["ttl"] == 86400
        assert kwargs["key_prefix"] == "test-raqindex"
        assert kwargs["max_stream_size"] == 67108864
        sqlite_factory.assert_not_called()
        assert not (tmp_path / "openalex_cache.db").exists()








@pytest.mark.asyncio
async def test_client_independent_instances(environment_variables):
    async with OpenAlexClient() as first:
        async with OpenAlexClient() as second:
            assert first.env is second.env
            assert first.storage is not second.storage
            assert first.transport is not second.transport
            assert first.client is not second.client
            assert first.api_key == second.api_key
            assert first.url == second.url

        assert second.client.is_closed
        assert not first.client.is_closed

    assert first.client.is_closed
