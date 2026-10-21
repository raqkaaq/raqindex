import importlib
from unittest.mock import AsyncMock

import httpx2
import pytest
from raqindex import OpenAlexClient

module = importlib.import_module("raqindex.client.client")
pytestmark = pytest.mark.asyncio


@pytest.fixture
def retry_setup(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(module.random, "uniform", lambda low, high: high)
    monkeypatch.setattr(module, "MAX_RETRIES", 3)
    return sleep


def response(status, headers=None):
    return httpx2.Response(status, headers=headers, request=httpx2.Request("GET", "https://api.openalex.org/works"))


async def test_success_does_not_retry(retry_setup):
    operation = AsyncMock(return_value=response(200))
    assert (await OpenAlexClient.retry(operation)(object())).status_code == 200
    operation.assert_awaited_once()
    retry_setup.assert_not_awaited()


@pytest.mark.parametrize("failure", [httpx2.ReadTimeout("timeout"), httpx2.ConnectError("offline"), response(503), response(429)])
async def test_transient_failure_recovers(retry_setup, failure):
    operation = AsyncMock(side_effect=[failure, response(200)])
    assert (await OpenAlexClient.retry(operation)(object())).status_code == 200
    assert operation.await_count == 2
    retry_setup.assert_awaited_once_with(2)


@pytest.mark.parametrize("status,headers", [(400, {}), (401, {}), (404, {}), (429, {"x-ratelimit-remaining": "0"}), (429, {"x-ratelimit-remaining": "2", "x-ratelimit-credits-required": "3"})])
async def test_permanent_failure_does_not_retry(retry_setup, status, headers):
    operation = AsyncMock(return_value=response(status, headers))
    with pytest.raises(httpx2.HTTPStatusError):
        await OpenAlexClient.retry(operation)(object())
    operation.assert_awaited_once()
    retry_setup.assert_not_awaited()

@pytest.mark.parametrize("failure", [httpx2.ConnectError("offline"), response(503)])
async def test_retry_exhaustion(retry_setup, failure):
    operation = AsyncMock(side_effect=failure) if isinstance(failure, Exception) else AsyncMock(return_value=failure)
    with pytest.raises((httpx2.ConnectError, httpx2.HTTPStatusError)):
        await OpenAlexClient.retry(operation)(object())
    assert operation.await_count == 3
    assert [call.args[0] for call in retry_setup.await_args_list] == [2, 4]


@pytest.mark.parametrize("header,delay", [("12", 12), ("invalid", 2), ("Wed, 01 Jan 2020 00:00:00 GMT", 2)])
async def test_retry_after(retry_setup, header, delay):
    operation = AsyncMock(side_effect=[response(503, {"Retry-After": header}), response(200)])
    await OpenAlexClient.retry(operation)(object())
    retry_setup.assert_awaited_once_with(delay)


async def test_unexpected_error_propagates(retry_setup):
    operation = AsyncMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError, match="bad input"):
        await OpenAlexClient.retry(operation)(object())
    operation.assert_awaited_once()
    retry_setup.assert_not_awaited()
