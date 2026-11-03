"""OpenAlex credit-budget overrides; generic retry policy lives in raq_helpers tests."""

import importlib
from unittest.mock import AsyncMock

import httpx2
import pytest
from raqindex import OpenAlexClient

module = importlib.import_module("raq_helpers.client.client")
pytestmark = pytest.mark.asyncio


@pytest.fixture
def retry_setup(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(module.random, "uniform", lambda low, high: high)
    monkeypatch.setattr(OpenAlexClient, "MAX_RETRIES", 3)
    return sleep


def response(status, headers=None):
    return httpx2.Response(
        status,
        headers=headers,
        request=httpx2.Request("GET", "https://api.openalex.org/works"),
    )






@pytest.mark.parametrize(
    "status,headers",
    [
        (429, {"x-ratelimit-remaining": "0"}),
        (429, {"x-ratelimit-remaining": "2", "x-ratelimit-credits-required": "3"}),
    ],
)
async def test_permanent_failure_does_not_retry(retry_setup, status, headers):
    operation = AsyncMock(return_value=response(status, headers))
    with pytest.raises(httpx2.HTTPStatusError):
        await OpenAlexClient.retry(operation)(OpenAlexClient.__new__(OpenAlexClient))
    operation.assert_awaited_once()
    retry_setup.assert_not_awaited()










@pytest.mark.parametrize(
    "headers",
    [
        {"x-ratelimit-remaining": "unknown"},
        {"x-ratelimit-remaining": "100", "x-ratelimit-credits-required": "unknown"},
        {"x-ratelimit-remaining": "10", "x-ratelimit-credits-required": "10"},
    ],
)
async def test_throttling_with_missing_or_affordable_budget_retries(
    retry_setup, headers
):
    operation = AsyncMock(side_effect=[response(429, headers), response(200)])
    result = await OpenAlexClient.retry(operation)(
        OpenAlexClient.__new__(OpenAlexClient)
    )
    assert result.status_code == 200
    assert operation.await_count == 2
    retry_setup.assert_awaited_once()
    assert 1 <= retry_setup.await_args.args[0] <= OpenAlexClient.CAP_SINGLE_RETRY_DELAY
