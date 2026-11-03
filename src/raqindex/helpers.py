"""Small work-ID helpers built on the existing OpenAlex client calls."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .generated import AutocompleteResponse, Error, Work, WorksListResponse
from .models.query import QueryErrorResponse, QueryResponse
from .models.work_ids import WorkIDBatch, WorkIDLookup, WorkIDResolution

if TYPE_CHECKING:
    from .client.client import OpenAlexClient

# Matches the generated works/retrieval per_page maximum, not an OQL list limit.
WORK_ID_PAGE_SIZE = 100


def _bare_id(identifier: str) -> str:
    return identifier.rstrip("/").rsplit("/", 1)[-1]


async def find_work_ids(
    client: "OpenAlexClient", title: str
) -> AutocompleteResponse | Error:
    """Return work candidates with IDs and hints; never select a title match."""
    if not title.strip():
        raise ValueError("title must not be empty")
    return await client.autocomplete(entity_type="works", q=title)


async def resolve_work_ids(
    client: "OpenAlexClient", identifiers: Sequence[str]
) -> WorkIDLookup:
    """Resolve known DOIs, PMIDs, or OpenAlex IDs, preserving per-input failures."""
    if isinstance(identifiers, str) or any(
        not isinstance(value, str) for value in identifiers
    ):
        raise TypeError(
            "identifiers must be a sequence of strings, not a single string"
        )
    results: list[WorkIDResolution] = []
    resolved: dict[str, Work | Error] = {}
    for identifier in identifiers:
        lookup = identifier.strip()
        if lookup.startswith("10."):
            lookup = f"doi:{lookup}"
        if not lookup:
            response = Error(message="identifier must not be empty")
        else:
            if lookup not in resolved:
                resolved[lookup] = await client.works(
                    id=lookup, select="id,display_name,doi,publication_year"
                )
            response = resolved[lookup]
        if isinstance(response, Error):
            results.append(WorkIDResolution(identifier=identifier, error=response))
        elif response.id is None:
            results.append(
                WorkIDResolution(
                    identifier=identifier,
                    error=Error(message="Lookup returned no OpenAlex ID."),
                )
            )
        else:
            results.append(
                WorkIDResolution(
                    identifier=identifier, id=_bare_id(response.id), work=response
                )
            )
    return WorkIDLookup(results=results)


async def collect_work_ids(
    client: "OpenAlexClient", oql: str, *, limit: int = 100
) -> WorkIDBatch | Error | QueryErrorResponse:
    """Validate OQL and collect up to limit unique work IDs using cursor pages.

    API errors are returned unchanged, including errors on later pages. A bounded
    success explicitly reports whether all available pages were consumed.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    checked = await client.query(oql)
    if isinstance(checked, (Error, QueryErrorResponse)):
        return checked
    if not isinstance(checked, QueryResponse):
        return Error(message="OQL translation did not return an executable query.")
    if not checked.validation.valid:
        return QueryErrorResponse(validation=checked.validation)
    if checked.oqo is None:
        return Error(message="OQL translation did not return an executable query.")
    query = checked.oqo
    if query.get_rows.value != "works" or query.group_by or query.calculate:
        return Error(
            message="collect_work_ids requires an OQL query returning work records, not groups or calculations."
        )

    page = await client.oqo(
        query, select="id", per_page=min(limit, WORK_ID_PAGE_SIZE), cursor="*"
    )
    ids: list[str] = []
    seen: set[str] = set()
    cursors: set[str] = {"*"}
    total: int | None = None
    while True:
        if isinstance(page, (Error, QueryErrorResponse)):
            return page
        if not isinstance(page, WorksListResponse):
            return Error(message="Expected a works-list response while collecting IDs.")
        if total is None and page.meta is not None:
            total = page.meta.count
        rows = page.results or []
        for index, work in enumerate(rows):
            if work.id is None:
                return Error(message="A work in the results has no OpenAlex ID.")
            identifier = _bare_id(work.id)
            if identifier not in seen:
                seen.add(identifier)
                ids.append(identifier)
            if len(ids) == limit:
                cursor = client.get_next_cursor(page)
                return WorkIDBatch(
                    ids=ids, total=total, complete=not cursor and index == len(rows) - 1
                )
        cursor = client.get_next_cursor(page)
        if not cursor:
            return WorkIDBatch(ids=ids, total=total, complete=True)
        if cursor in cursors:
            return Error(
                message="OpenAlex returned a repeated pagination cursor; ID collection stopped."
            )
        cursors.add(cursor)
        page = await client.next_page(page)
