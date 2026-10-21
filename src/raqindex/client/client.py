import asyncio
from curses import meta
import json
import random
from email.utils import parsedate_to_datetime

from raqindex.models.openalex import Error
from pydantic import ValidationError
from .environment import Environment
from .generated_endpoints import GeneratedEndpoints
from raqindex.models.endpoints import Endpoint, ReqRes, ResponseCollection, Method
from raqindex.models.query import QueryResponse, QueryRequest
import httpx2
from functools import wraps
from datetime import datetime, timedelta, timezone
import logging
from hishel import AsyncSqliteStorage, FilterPolicy 
from hishel.httpx2 import AsyncCacheClient, AsyncCacheTransport
from .cache_filters import URLPatternFilter, MethodFilter, StatusCodeFilter, ContentTypeFilter, HeaderFilter
from raqindex.models.endpoints import Endpoint
from string import Formatter
from urllib.parse import quote
from raqindex.models import XQuery, OQO

logger = logging.getLogger(__name__)

CACHE_EXCLUDE_PATTERNS = (
    "rate-limit",
    "query",
)

def cache_url_pattern(base_url:str)->str:
    import re
    base_url = base_url.rstrip("/")
    excluded = '|'.join(re.escape(path) for path in CACHE_EXCLUDE_PATTERNS)
    return (
        rf"^{re.escape(base_url)}/"
        rf"(?!(?:{excluded})(?:[/?#]|$))"
        rf".*$"
    )
URL_PATTERN = cache_url_pattern(Environment().get_url())
METHODS = ["GET", "POST"]
STATUS_CODES = [200, 201, 202, 204]
MAX_REDIRECTS = 10
MAX_RETRIES = 10
TIMEOUT = httpx2.Timeout(10.0, connect=60.0)  # 10 seconds for the read/write/pool, 60 seconds for connection
CAP_SINGLE_RETRY_DELAY = 30.0  # Base delay between retries in seconds
MAX_RETRY_DELAY = 100.0  # Maximum delay between retries in seconds

REQUEST_FILTERS = [
    URLPatternFilter(pattern=URL_PATTERN),
    MethodFilter(methods=METHODS)
]
RESPONSE_FILTERS = [
    StatusCodeFilter(status_codes=STATUS_CODES)
]

class OpenAlexClient(GeneratedEndpoints):
    def __init__(self):
        try:
            self.env = Environment()
        except ValueError as e:
            raise ValueError("Failed to initialize OpenAlexClient.") from e
        self.api_key = self.env.get_api_key()
        self.url = self.env.get_url()
        self.storage = AsyncSqliteStorage(
                    database_path=self.env.cache_db_path,
                    default_ttl=self.env.cache_ttl,
                    )
        logger.info("Cache storage initialized with database path and time-to-live: %s, %s", self.env.cache_db_path, self.env.cache_ttl)
        self.policy = FilterPolicy(
            request_filters = REQUEST_FILTERS 
            ,
            response_filters = RESPONSE_FILTERS
        )
        logger.info("Cache filter policy initialized with request filters: %s and response filters: %s", REQUEST_FILTERS, RESPONSE_FILTERS)
        self.transport = AsyncCacheTransport(
            next_transport=httpx2.AsyncHTTPTransport(),
            storage=self.storage,
            policy=self.policy
            )
        self.client = AsyncCacheClient(
                            base_url=self.url,
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "User-Agent": "OpenAlexClient/1.0",
                                },
                            params={"mailto": self.env.email},
                            transport=self.transport,
                            max_redirects=MAX_REDIRECTS,
                            timeout=TIMEOUT
                            )
        logger.info("OpenAlexClient initialized with URL: %s", self.url)
        #load the endpoints from the schema
        with self.env.endpoints_file_path.open() as f:
            self.endpoints = {
                item["endpoint"]: Endpoint.model_validate(item)
                for item in json.load(f)
            }
        # add other endpoints to the endpoints dictionary
        self.endpoints["/query"] = Endpoint(
            endpoint="/query",
            method=Method(
                method="POST",
                operationId="query",
                summary="Validate and translate a query OpenAlex API for OQL and OQO",
                description="Validate and translate a query OpenAlex API for OQL and OQO without executing to get validation and a cost estimate",
                tags=["query"],
                payload="json",
                request=ReqRes(
                    ref="QueryRequest",
                    model=QueryRequest
                ),
                responses=ResponseCollection.model_validate({
                    "200": ReqRes(ref="QueryResponse", model=QueryResponse),
                    "400": ReqRes(ref="Error", model=Error),
                    "403": ReqRes(ref="Error", model=Error),
                    "404": ReqRes(ref="Error", model=Error),
                    "429": ReqRes(ref="Error", model=Error),
                })
            )
        )
        self.endpoints["/"] = Endpoint(
            endpoint="/",
            method=Method(
                method="POST",
                operationId="root",
                summary="Root endpoint for OpenAlex API",
                description="The root endpoint for the OpenAlex API",
                tags=["root"],
                payload="json",
                request=ReqRes(
                    ref="QueryRequest",
                    model=QueryRequest
                ),
                responses=ResponseCollection.model_validate({
                    "200": ReqRes(ref="", model=None),
                    "400": ReqRes(ref="Error", model=Error),
                    "403": ReqRes(ref="Error", model=Error),
                    "404": ReqRes(ref="Error", model=Error),
                    "429": ReqRes(ref="Error", model=Error),
                })
            )
        )
        logger.info("Endpoints loaded: %s", list(self.endpoints.keys()))

    async  def __aenter__(self):
        return self

    async def aclose(self):
        await self.client.aclose()
        logger.debug("OpenAlexClient closed.")

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.aclose()

    @staticmethod
    def cache(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            response = await func(self, *args, **kwargs)
            logger.debug(
                "Cache metadata for %s: %s",
                response.request.url.copy_with(query=None),
                {
                    key: value
                    for key, value in response.extensions.items()
                    if key.startswith("hishel_")
                },
            )
            return response
        return wrapper

    @staticmethod
    def retry(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            async with asyncio.timeout(MAX_RETRY_DELAY):
                for attempt in range(1,MAX_RETRIES + 1):
                    # Exponential backoff with a cap of 30 seconds
                    delay = random.uniform(1, min(CAP_SINGLE_RETRY_DELAY, 2 ** attempt))  # Exponential backoff with a cap of 30 seconds
                    try:
                        response = await func(self, *args, **kwargs)
                        response.raise_for_status()
                        return response
                    except (httpx2.TimeoutException, httpx2.NetworkError) as e: #timeout or network error
                        if attempt == MAX_RETRIES:
                            logger.warning(f"Attempt {attempt} failed. Retry budget exceeded.")
                            raise
                        logger.warning(f"Attempt {attempt} failed with error: {type(e).__name__}. Retrying...")
                    except httpx2.HTTPStatusError as e: #http status error exception
                        response = e.response
                        status = response.status_code
                        #read remaining daily budget
                        try:
                            remaining_budget = int(response.headers.get("x-ratelimit-remaining", ""))
                        except ValueError:
                            remaining_budget = None  # Unknown remaining budget
                        if remaining_budget == 0:
                            logger.warning("Rate limit exceeded and no remaining budget.")
                            raise
                        if status == 429: #rate limit error
                            #extract request cost
                            try:
                                response_cost = int(response.headers.get("x-ratelimit-credits-required", ""))
                            except ValueError:
                                response_cost = None  # Unknown request cost

                            #if both are available, and cost exceeds remaining budget, raise exception                        
                            #if cost is unknown, but budget is 0, raise
                            #otherwise treat it as throttling and run exponential backoff
                            if remaining_budget is not None:
                                if response_cost is not None and response_cost > remaining_budget:
                                    logger.warning(f"Request cost {response_cost} exceeds remaining budget {remaining_budget}.")
                                    raise
                                logger.warning(f"Rate limit exceeded. Remaining budget: {remaining_budget}. Retrying...")
                            else:
                                logger.warning("Rate limit exceeded. Remaining budget unknown. Retrying...")
                        elif status >= 500: #server error
                            #run exponential backoff and retry
                            logger.warning(f"Server error {status}. Retrying...")
                        else:
                            raise
                        #use valid retry-after header if available, otherwise use exponential backoff
                        try:
                            retry_after = response.headers.get("Retry-After", "").strip()
                            if retry_after.isdigit():
                                retry_after = int(retry_after)
                            else:
                                try:
                                    retry_at = parsedate_to_datetime(retry_after)
                                    if retry_at.tzinfo is None:
                                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                                    retry_after = max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                                except (TypeError, ValueError, OverflowError):
                                    retry_after = None  # Invalid Retry-After header
                        except ValueError:
                            retry_after = -1  # Unknown retry-after value
                        if retry_after is not None and retry_after >= 0:
                            delay = max(delay, retry_after)
                        if attempt == MAX_RETRIES:
                            logger.warning(f"Attempt {attempt} failed. Retry budget exceeded.")
                            raise
                    logger.info(f"Waiting for {delay:.2f} seconds before retrying...")
                    await asyncio.sleep(delay)
                raise Exception(f"Failed to complete the request after {MAX_RETRIES} attempts.")
        return wrapper

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
                    "x-ratelimit-limit": response.headers.get("x-ratelimit-limit", None),
                    "x-ratelimit-remaining": response.headers.get("x-ratelimit-remaining", None),
                    "x-ratelimit-credits-used": response.headers.get("x-ratelimit-credits-used", None),
                    "x-ratelimit-reset": response.headers.get("x-ratelimit-reset", None)
                }
                logger.info(
                    "API call cost: %s, Remaining budget: %s, Total limit: %s, Reset time: %s, Cached: %s",
                    self.usage["x-ratelimit-credits-used"],
                    self.usage["x-ratelimit-remaining"],
                    self.usage["x-ratelimit-limit"],
                    str(timedelta(seconds=int(self.usage["x-ratelimit-reset"]) if self.usage["x-ratelimit-reset"] else 0)),
                    stored
                )
            return response
        return wrapper

    @staticmethod
    def parse_as(responses):
        models = {
            field.alias: getattr(responses, name)
            for name, field in type(responses).model_fields.items()
            if getattr(responses, name) is not None
        }

        def decorator(func):
            @wraps(func)
            async def wrapper(self, *args, **kwargs):
                try:
                    response = await func(self, *args, **kwargs)
                except httpx2.HTTPStatusError as error:
                    definition = models.get(str(error.response.status_code))
                    logger.error("HTTP error %s: %s", error.response.status_code, error.response.text)
                    if definition is None:
                        raise
                    return definition.model.model_validate_json(
                        error.response.content,
                        extra="allow"
                    )
                except ValidationError as error:
                    logger.error("Response validation error: %s", error)
                    return Error(message=str(error), details=error.errors())

                parsed =  models[str(response.status_code)].model.model_validate_json(
                    response.content,
                    extra = "allow"
                )
                parsed.model_extra["request"] = response.extensions[
                    "openalex_request"
                    ].model_copy(update={"api_key": '<redacted>', 'mailto': '<redacted>'})  # Remove the API key from the request model for security

                meta = getattr(parsed, "meta", None)
                x_query = getattr(meta, "x_query", None)
                if x_query is not None:
                    meta.model_extra["x_query"] = XQuery.model_validate(x_query)
                return parsed

            return wrapper
        return decorator

    def endpoint_factory(self, path, model_name=None):
        """A factory method to create endpoint methods for all endpoints defined in the OpenAlex API schema."""
        endpoint = self.endpoints.get(path)
        if not endpoint:
            raise ValueError(f"Endpoint '{path}' not found in the loaded endpoints.")
        @self.parse_as(self.endpoints.get(f"/{model_name}").method.responses if model_name else endpoint.method.responses)
        @self.cost
        @self.retry
        @self.cache
        async def call(client, **params):
            validated = endpoint.method.request.model.model_validate(
                {**params, "api_key": self.api_key}
            )
            query = validated.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
                exclude={"api_key"},
            )
            path_values = {
                name: quote(str(query.pop(name)), safe="")
                for _, name, _, _ in Formatter().parse(endpoint.endpoint)
                if name is not None
            }
            url = endpoint.endpoint.format(**path_values)
            payload = {
                endpoint.method.payload: query
            }
            logger.debug(
                "Making request to %s with payload: %s",
                url,
                {k: v for k, v in payload.items() if k != "json"},
            )
            response = await client.client.request(
                method=endpoint.method.method,
                url=url,
                **payload,
                follow_redirects=True,
                #adds a cache key to queries that have json body
                extensions={"hishel_body_key": "true"} if "json" in payload else {},
                # headers={"X-Hishel-Body-Key": "true"} if "json" in payload else {}
            )
            response.extensions["openalex_request"] = validated
            return response
        return call.__get__(self, type(self))

    async def query(self, query: OQO):
        return await self.endpoint_factory("/query")(oqo=query)    #query endpoint is not in the schema, defining manually

    async def oqo(self, query: OQO):
        model_name = query.get_rows.value
        return await self.endpoint_factory("/", model_name)(oqo=query)    #root endpoint is not in the schema, defining manually
    # @cost
    # @retry
    # async def query(self, query: OQO):
    #     """Send a query to the OpenAlex API."""
    #     validated = query.model_dump(mode="json", by_alias=True, exclude_none=True)
    #     response = await self.client.post(
    #         url="/query",
    #         json={"oqo": json.dumps(validated)},
    #         follow_redirects=True,
    #     )
    #     response.extensions["openalex_request"] = query
    #     return response
        
