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
    retry_setup.assert_awaited_once()
    assert 1 <= retry_setup.await_args.args[0] <= module.CAP_SINGLE_RETRY_DELAY


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
    assert retry_setup.await_count == module.MAX_RETRIES - 1
    assert all(1 <= call.args[0] <= module.CAP_SINGLE_RETRY_DELAY
               for call in retry_setup.await_args_list)


@pytest.mark.parametrize("header,delay", [("12", 12), ("invalid", 2), ("Wed, 01 Jan 2020 00:00:00 GMT", 2)])
async def test_retry_after(retry_setup, header, delay):
    operation = AsyncMock(side_effect=[response(503, {"Retry-After": header}), response(200)])
    await OpenAlexClient.retry(operation)(object())
    retry_setup.assert_awaited_once()
    assert retry_setup.await_args.args[0] >= delay
    assert retry_setup.await_args.args[0] <= max(delay, module.CAP_SINGLE_RETRY_DELAY)


async def test_unexpected_error_propagates(retry_setup):
    operation = AsyncMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError, match="bad input"):
        await OpenAlexClient.retry(operation)(object())
    operation.assert_awaited_once()
    retry_setup.assert_not_awaited()


async def test_backoff_cap_and_last_attempt_have_no_extra_sleep(retry_setup, monkeypatch):
    monkeypatch.setattr(module, 'MAX_RETRIES', 8)
    operation = AsyncMock(return_value=response(503))
    with pytest.raises(httpx2.HTTPStatusError):
        await OpenAlexClient.retry(operation)(object())
    assert operation.await_count == 8
    waits = [call.args[0] for call in retry_setup.await_args_list]
    assert len(waits) == 7
    assert all(1 <= delay <= module.CAP_SINGLE_RETRY_DELAY for delay in waits)
    # The deterministic upper-bound sampler should grow to, then respect, the cap.
    assert waits == sorted(waits)
    assert waits[0] < waits[-1] == module.CAP_SINGLE_RETRY_DELAY


@pytest.mark.parametrize('headers', [
    {'x-ratelimit-remaining': 'unknown'},
    {'x-ratelimit-remaining': '100', 'x-ratelimit-credits-required': 'unknown'},
    {'x-ratelimit-remaining': '10', 'x-ratelimit-credits-required': '10'},
])
async def test_throttling_with_missing_or_affordable_budget_retries(retry_setup, headers):
    operation = AsyncMock(side_effect=[response(429, headers), response(200)])
    result = await OpenAlexClient.retry(operation)(object())
    assert result.status_code == 200
    assert operation.await_count == 2
    retry_setup.assert_awaited_once()
    assert 1 <= retry_setup.await_args.args[0] <= module.CAP_SINGLE_RETRY_DELAY


@pytest.mark.parametrize('header', ['0', '1', '40'])
async def test_retry_after_minimum_is_honored(retry_setup, header):
    operation = AsyncMock(side_effect=[response(503, {'Retry-After': header}), response(200)])
    await OpenAlexClient.retry(operation)(object())
    retry_setup.assert_awaited_once()
    assert retry_setup.await_args.args[0] >= max(1, int(header))
    assert retry_setup.await_args.args[0] <= max(int(header), module.CAP_SINGLE_RETRY_DELAY)


async def test_future_retry_after_date_uses_utc_difference(retry_setup, monkeypatch):
    from datetime import datetime, timezone

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(module, 'datetime', Clock)
    operation = AsyncMock(side_effect=[response(503, {'Retry-After': 'Thu, 01 Jan 2026 00:00:12 GMT'}), response(200)])
    await OpenAlexClient.retry(operation)(object())
    retry_setup.assert_awaited_once()
    assert retry_setup.await_args.args[0] >= 12


async def test_cancellation_is_propagated_without_retry(retry_setup):
    import asyncio
    operation = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await OpenAlexClient.retry(operation)(object())
    operation.assert_awaited_once()
    retry_setup.assert_not_awaited()


async def test_total_retry_deadline_cancels_inflight_operation(monkeypatch):
    import asyncio
    monkeypatch.setattr(module, 'MAX_RETRY_DELAY', 0.02)
    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()
    operation = AsyncMock(side_effect=blocked)
    with pytest.raises(TimeoutError):
        await OpenAlexClient.retry(operation)(object())
    operation.assert_awaited_once()
