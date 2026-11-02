"""OpenAlex behavior layered over the shared async client."""

import logging
import textwrap
from datetime import timedelta
from functools import wraps

from raq_helpers.client import Client
from raq_helpers.models import (
    Endpoint,
    EndpointOperation,
    ModelReference,
    ResponseCollection,
)

from .environment import Environment
from .endpoint_factory import OpenAlexEndpointFactory
from ..generated.endpoints import GeneratedEndpoints
from ..models.calculation import CalculationResponse
from ..generated.openalex_registry import MODEL_REGISTRY
from ..generated.openalex import Error
from ..models.query import QueryErrorResponse, QueryRequest, QueryResponse, XQuery
from ..generated.oqo import Model as OQO

logger = logging.getLogger(__name__)


class OpenAlexClient(Client, GeneratedEndpoints):
    CACHE_EXCLUDE_PATTERNS = ("rate-limit", "query")
    CACHE_METHODS = ("GET", "POST")

    def __init__(self):
        try:
            self.env = Environment()
        except ValueError as error:
            raise ValueError("Failed to initialize OpenAlexClient.") from error
        self.api_key = self.env.api_key
        registry = {
            **MODEL_REGISTRY,
            "QueryRequest": QueryRequest,
            "QueryResponse": QueryResponse,
            "QueryErrorResponse": QueryErrorResponse,
        }
        loaded = OpenAlexEndpointFactory.from_json(
            self.env.endpoints_file_path.read_text(encoding="utf-8"), registry=registry
        )
        endpoints = list(loaded._endpoints.values())
        for path, success in (("/query", QueryResponse), ("/", None)):
            endpoints = [
                item
                for item in endpoints
                if (item.endpoint, item.method.method) != (path, "POST")
            ]
            endpoints.append(
                Endpoint(
                    endpoint=path,
                    method=EndpointOperation(
                        method="POST",
                        operationId="query" if path == "/query" else "root",
                        summary="Validate a query"
                        if path == "/query"
                        else "Execute a query",
                        tags=["query"],
                        payload="json",
                        request=ModelReference(ref="QueryRequest", model=QueryRequest),
                        responses=ResponseCollection.model_validate(
                            {
                                "200": ModelReference(
                                    ref=success.__name__ if success else "",
                                    model=success,
                                ),
                                "400": ModelReference(
                                    ref="QueryErrorResponse"
                                    if path == "/query"
                                    else "Error",
                                    model=QueryErrorResponse
                                    if path == "/query"
                                    else Error,
                                ),
                                **{
                                    status: ModelReference(ref="Error", model=Error)
                                    for status in ("403", "404", "429")
                                },
                            }
                        ),
                    ),
                )
            )
        self.factory = OpenAlexEndpointFactory(endpoints, registry=registry)
        super().__init__(
            url=self.env.url,
            cache_db_path=self.env.cache_db_path,
            cache_ttl=self.env.cache_ttl,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "OpenAlexClient/1.0",
            },
            params={"mailto": self.env.email},
        )

    def endpoint_factory(
        self, path, model_name=None, response_model=None, *, method=None
    ):
        if model_name is None and response_model is None:
            return self.factory.create_endpoint(self, path, method)
        endpoint = self.factory.get_endpoint(path, method)
        if model_name is not None or response_model is not None:
            responses = (
                self.factory.get_endpoint(f"/{model_name}", "GET").method.responses
                if model_name is not None
                else endpoint.method.responses
            )
            if response_model is not None:
                responses = responses.model_copy(
                    update={
                        "success": ModelReference(
                            ref=response_model.__name__, model=response_model
                        )
                    }
                )
            endpoint = endpoint.model_copy(
                update={
                    "method": endpoint.method.model_copy(
                        update={"responses": responses}
                    )
                }
            )
        # Per-call factories keep response overrides independent during concurrent queries.
        factory = OpenAlexEndpointFactory([endpoint], registry=self.factory.registry)
        return factory.create_endpoint(self, path, method)

    def should_retry_response(self, response):
        try:
            remaining = int(response.headers.get("x-ratelimit-remaining", ""))
        except ValueError:
            remaining = None
        if remaining == 0:
            return False
        if response.status_code == 429 and remaining is not None:
            try:
                required = int(response.headers.get("x-ratelimit-credits-required", ""))
            except ValueError:
                required = None
            if required is not None and required > remaining:
                return False
        return super().should_retry_response(response)

    def handle_validation_error(self, error):
        # Input errors can contain the injected API key: keep those values out of diagnostics.
        details = error.errors(include_input=False, include_context=False)
        message = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in details
        )
        return Error(message=message)

    def process_parsed_response(self, parsed, response):
        request = response.extensions.get("openalex_request")
        if request is not None and parsed.model_extra is not None:
            parsed.model_extra["request"] = request.model_copy(
                update={"api_key": "<redacted>", "mailto": "<redacted>"}
            )
        meta = getattr(parsed, "meta", None)
        if (
            meta is not None
            and isinstance(getattr(meta, "x_query", None), dict)
            and meta.model_extra is not None
        ):
            meta.model_extra["x_query"] = XQuery.model_validate(meta.x_query)
        return parsed

    def get_next_cursor(self, page):
        return getattr(getattr(page, "meta", None), "next_cursor", None)

    def supports_pagination(self, page):
        return (
            isinstance(getattr(page, "results", None), list)
            and not getattr(page, "group_by", None)
            and not isinstance(page, CalculationResponse)
        )

    @staticmethod
    def cost(func):
        """
        Decorator to log the cost of API calls based on response headers.
        """

        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            response = await func(self, *args, **kwargs)
            stored = response.extensions.get("hishel_stored", False)
            if response.extensions.get("hishel_from_cache", False):
                logger.info("Response served from cache. No cost incurred.")
            else:
                self.usage = {
                    "x-ratelimit-limit": response.headers.get(
                        "x-ratelimit-limit", None
                    ),
                    "x-ratelimit-remaining": response.headers.get(
                        "x-ratelimit-remaining", None
                    ),
                    "x-ratelimit-credits-used": response.headers.get(
                        "x-ratelimit-credits-used", None
                    ),
                    "x-ratelimit-reset": response.headers.get(
                        "x-ratelimit-reset", None
                    ),
                }
                logger.info(
                    "API call cost: %s, Remaining budget: %s, Total limit: %s, Reset time: %s, Cached: %s",
                    self.usage["x-ratelimit-credits-used"],
                    self.usage["x-ratelimit-remaining"],
                    self.usage["x-ratelimit-limit"],
                    str(
                        timedelta(
                            seconds=int(self.usage["x-ratelimit-reset"])
                            if self.usage["x-ratelimit-reset"]
                            else 0
                        )
                    ),
                    stored,
                )
            return response

        return wrapper

    async def query(self, query: OQO | str):
        if isinstance(query, str):
            # assume is oql request
            response = await self.endpoint_factory("/query", method="POST")(oql=query)
        else:
            response = await self.endpoint_factory("/query", method="POST")(oqo=query)
        return response

    async def oqo(self, query: OQO, **params):
        if query.group_by or query.calculate:
            return await self.endpoint_factory(
                "/", response_model=CalculationResponse, method="POST"
            )(oqo=query, **params)
        model_name = query.get_rows.value
        return await self.endpoint_factory("/", model_name, method="POST")(
            oqo=query, **params
        )

    @staticmethod
    def reconstruct_and_wrap_abstract(inverted_index, max_width=80):
        if not inverted_index:
            return ""

        # 1. Find the total length of the abstract
        total_words = (
            max(pos for positions in inverted_index.values() for pos in positions) + 1
        )
        word_list = [""] * total_words

        # 2. Place each word in its correct position
        for word, positions in inverted_index.items():
            for pos in positions:
                word_list[pos] = word

        # 3. Join words into a single string
        full_text = " ".join(word_list)

        # 4. Wrap the text to the specified width
        return textwrap.fill(full_text, width=max_width)
