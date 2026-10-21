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
    storage_factory = Mock(wraps=client_module.AsyncSqliteStorage)
    monkeypatch.setattr(client_module, "AsyncSqliteStorage", storage_factory)

    async with OpenAlexClient() as client:
        storage_factory.assert_called_once_with(
            database_path=tmp_path / "openalex_cache.db",
            default_ttl=86400,
        )
        assert client.storage is not None


def test_client_preserves_environment_failure(monkeypatch):
    original_error = ValueError("Missing API key")
    monkeypatch.setattr(
        client_module, "Environment", Mock(side_effect=original_error)
    )

    with pytest.raises(ValueError, match=r"^Failed to initialize OpenAlexClient\.$") as error:
        OpenAlexClient()

    assert error.value.__cause__ is original_error


@pytest.mark.asyncio
async def test_client_context_manager(environment_variables):
    client = OpenAlexClient()
    async with client as entered:
        assert entered is client
        assert not client.client.is_closed

    assert client.client.is_closed


@pytest.mark.asyncio
async def test_client_closes_when_context_raises(environment_variables):
    client = OpenAlexClient()
    original_error = RuntimeError("test failure")

    with pytest.raises(RuntimeError, match="test failure") as error:
        async with client:
            raise original_error

    assert error.value is original_error
    assert client.client.is_closed


@pytest.mark.asyncio
async def test_client_explicit_close(environment_variables):
    client = OpenAlexClient()
    try:
        assert not client.client.is_closed
        await client.aclose()
        assert client.client.is_closed
    finally:
        if not client.client.is_closed:
            await client.aclose()


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
