"""User-facing work-ID helpers, exercised through the client and typed API models."""

from unittest.mock import AsyncMock

import httpx2
import pytest
from raq_helpers.models import ModelReference

from raqindex.generated import AutocompleteResponse, Error, Work
from raqindex.helpers import collect_work_ids, find_work_ids, resolve_work_ids
from raqindex.models.query import QueryErrorResponse
from raqindex.models.work_ids import WorkIDBatch


def response(payload, status=200):
    return httpx2.Response(
        status,
        json=payload,
        request=httpx2.Request("POST", "https://api.openalex.org/"),
    )


def translation(oqo=None):
    return response(
        {
            "oql": "get works about coral",
            "oqo": oqo or {"get_rows": "works"},
            "validation": {"valid": True, "errors": [], "warnings": []},
        }
    )


def page(ids, cursor=None, total=3):
    return response(
        {
            "meta": {"count": total, "next_cursor": cursor},
            "results": [{"id": f"https://openalex.org/{id}"} for id in ids],
        }
    )


@pytest.mark.asyncio
async def test_collect_validates_once_pages_deduplicates_and_returns_bare_ids(
    client, monkeypatch
):
    request = AsyncMock(
        side_effect=[translation(), page(["W1", "W1"], "next"), page(["W2"])]
    )
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "get works about coral")
    assert isinstance(result, WorkIDBatch)
    assert result.ids == ["W1", "W2"]
    assert result.complete is True
    assert result.total == 3
    assert [call.kwargs["url"] for call in request.await_args_list] == [
        "/query",
        "/",
        "/",
    ]
    first, second = [call.kwargs["json"] for call in request.await_args_list[1:]]
    assert first["select"] == second["select"] == "id"
    assert first["cursor"] == "*"
    assert second["cursor"] == "next"
    assert first["oqo"] == second["oqo"]


@pytest.mark.asyncio
async def test_collection_limit_is_explicitly_incomplete(client, monkeypatch):
    request = AsyncMock(side_effect=[translation(), page(["W1", "W2"], "next")])
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "get works about coral", limit=2)
    assert isinstance(result, WorkIDBatch)
    assert result.ids == ["W1", "W2"]
    assert result.complete is False
    assert request.await_count == 2
    assert request.await_args.kwargs["json"]["per_page"] == 2


@pytest.mark.asyncio
async def test_exact_limit_on_last_page_is_complete(client, monkeypatch):
    monkeypatch.setattr(
        client.client,
        "request",
        AsyncMock(side_effect=[translation(), page(["W1"], total=1)]),
    )
    result = await collect_work_ids(client, "get works about coral", limit=1)
    assert result.complete is True


@pytest.mark.asyncio
async def test_empty_search_is_complete_empty_batch(client, monkeypatch):
    monkeypatch.setattr(
        client.client,
        "request",
        AsyncMock(side_effect=[translation(), page([], total=0)]),
    )
    result = await collect_work_ids(client, "get works about coral")
    assert isinstance(result, WorkIDBatch)
    assert result.ids == []
    assert result.total == 0
    assert result.complete is True


@pytest.mark.asyncio
async def test_translation_failure_stops_before_execution(client, monkeypatch):
    request = AsyncMock(
        return_value=response(
            {
                "validation": {
                    "valid": False,
                    "errors": ["Unknown column"],
                    "warnings": [],
                }
            },
            400,
        )
    )
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "bad OQL")
    assert isinstance(result, QueryErrorResponse)
    assert result.validation.errors == ["Unknown column"]
    request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "oqo",
    [
        {"get_rows": "authors"},
        {"get_rows": "works", "group_by": [{"column_id": "publication_year"}]},
        {"get_rows": "works", "calculate": [{"measure": "count"}]},
    ],
)
async def test_work_collection_refuses_other_entities_and_calculations(
    client, monkeypatch, oqo
):
    request = AsyncMock(return_value=translation(oqo))
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "some OQL")
    assert type(result) is Error
    assert "work records" in result.message
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_later_page_error_is_not_silent_partial_success(client, monkeypatch):
    client.factory.get_endpoint(
        "/works", "GET"
    ).method.responses.forbidden = ModelReference(ref="Error", model=Error)
    request = AsyncMock(
        side_effect=[
            translation(),
            page(["W1"], "next"),
            response({"message": "Access denied"}, 403),
        ]
    )
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "get works about coral")
    assert type(result) is Error
    assert result.message == "Access denied"


@pytest.mark.asyncio
async def test_repeated_cursor_cannot_loop_indefinitely(client, monkeypatch):
    request = AsyncMock(
        side_effect=[translation(), page(["W1"], "next"), page(["W1"], "next")]
    )
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "get works about coral")
    assert type(result) is Error
    assert "repeated" in result.message
    assert request.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_invalid_collection_limit_makes_no_calls(client, monkeypatch, limit):
    query = AsyncMock()
    monkeypatch.setattr(client, "query", query)
    with pytest.raises(ValueError, match="positive integer"):
        await collect_work_ids(client, "get works", limit=limit)
    query.assert_not_awaited()


@pytest.mark.asyncio
async def test_identifier_resolution_preserves_inputs_failures_and_deduplicates_ids(
    client, monkeypatch
):
    work = Work(
        id="https://openalex.org/W1",
        display_name="A paper",
        doi="https://doi.org/10.1234/example",
    )
    missing = Error(message="Not found")
    lookup = AsyncMock(side_effect=[work, missing, work])
    monkeypatch.setattr(client, "works", lookup)
    inputs = ["10.1234/example", "pmid:123", "W1", "10.1234/example", ""]
    result = await resolve_work_ids(client, inputs)
    assert result.ids == ["W1"]
    assert [item.identifier for item in result.results] == inputs
    assert result.results[1].error is missing
    assert result.results[1].id is None
    assert result.results[3].work is work
    assert result.results[4].error.message == "identifier must not be empty"
    assert lookup.await_count == 3
    assert lookup.await_args_list[0].kwargs["id"] == "doi:10.1234/example"


@pytest.mark.asyncio
async def test_empty_identifier_batch_makes_no_calls(client, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(client, "works", lookup)
    result = await resolve_work_ids(client, [])
    assert result.ids == []
    assert result.results == []
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_identifier_batch_requires_sequence_not_string(client, monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(client, "works", lookup)
    with pytest.raises(TypeError, match="sequence"):
        await resolve_work_ids(client, "W1")
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_missing_response_id_is_a_failure(client, monkeypatch):
    monkeypatch.setattr(
        client, "works", AsyncMock(return_value=Work(display_name="Missing ID"))
    )
    result = await resolve_work_ids(client, ["W1"])
    assert result.ids == []
    assert result.results[0].error is not None


@pytest.mark.asyncio
async def test_title_lookup_returns_all_candidates_without_selecting_one(
    client, monkeypatch
):
    suggestions = AutocompleteResponse.model_validate(
        {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "display_name": "Same title",
                    "hint": "Author A",
                },
                {
                    "id": "https://openalex.org/W2",
                    "display_name": "Same title",
                    "hint": "Author B",
                },
            ]
        }
    )
    lookup = AsyncMock(return_value=suggestions)
    monkeypatch.setattr(client, "autocomplete", lookup)
    assert await find_work_ids(client, "Same title") is suggestions
    lookup.assert_awaited_once_with(entity_type="works", q="Same title")


@pytest.mark.asyncio
async def test_title_lookup_preserves_api_error(client, monkeypatch):
    error = Error(message="No access")
    monkeypatch.setattr(client, "autocomplete", AsyncMock(return_value=error))
    assert await find_work_ids(client, "Some title") is error


@pytest.mark.asyncio
async def test_collection_can_exceed_one_page_without_exceeding_request_bound(
    client, monkeypatch
):
    request = AsyncMock(
        side_effect=[
            translation(),
            page([f"W{i}" for i in range(1, 101)], "next", total=150),
            page([f"W{i}" for i in range(101, 151)], total=150),
        ]
    )
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "get works about coral", limit=150)
    assert isinstance(result, WorkIDBatch)
    assert len(result.ids) == 150
    assert result.complete is True
    assert all(
        call.kwargs["json"]["per_page"] <= 100 for call in request.await_args_list[1:]
    )


@pytest.mark.asyncio
async def test_missing_work_id_is_not_silently_omitted(client, monkeypatch):
    request = AsyncMock(
        side_effect=[translation(), response({"meta": {}, "results": [{}]})]
    )
    monkeypatch.setattr(client.client, "request", request)
    result = await collect_work_ids(client, "get works about coral")
    assert type(result) is Error
    assert "no OpenAlex ID" in result.message
