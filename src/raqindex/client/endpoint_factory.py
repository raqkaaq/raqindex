"""OpenAlex authentication, JSON queries, and response metadata hooks."""

from typing import Any, cast

from pydantic import Field
from raq_helpers.client import EndpointFactory
from raq_helpers.models import Endpoint
from raq_helpers.models.requests import PreparedRequest


class OpenAlexJSONRequest(PreparedRequest):
    body: dict[str, Any] = Field(default_factory=dict)


class OpenAlexEndpointFactory(EndpointFactory):
    def prepare_request_values(self, endpoint: Endpoint, params: dict) -> dict:
        values = super().prepare_request_values(endpoint, params)
        model = endpoint.method.request.model
        if model is not None and "api_key" in model.model_fields:
            # Authentication is carried by the HTTP header. The schema still requires
            # this legacy field; never put the actual secret in validation diagnostics.
            values["api_key"] = "<redacted>"
        return values

    def build_endpoint_request(
        self, endpoint: Endpoint, params: dict
    ) -> PreparedRequest:
        if endpoint.method.payload != "json":
            request = super().build_endpoint_request(endpoint, params)
            request.params.pop("api_key", None)
            return request
        model = endpoint.method.request.model
        if model is None:
            raise ValueError("OpenAlex JSON queries require a request model.")
        validated = model.model_validate(
            self.prepare_request_values(endpoint, params), extra="forbid"
        )
        return OpenAlexJSONRequest(
            method=endpoint.method.method,
            url=endpoint.endpoint,
            validated_input=validated,
            body=validated.model_dump(
                mode="json", by_alias=True, exclude_none=True, exclude={"api_key"}
            ),
        )

    def decorate_api_call(self, client, call):
        return client.cost(call)

    def process_endpoint_response(self, response, request: PreparedRequest):
        response.extensions["openalex_request"] = request.validated_input
        return response

    def create_endpoint(self, client, path, method=None):
        endpoint = self.get_endpoint(path, method)
        if endpoint.method.payload != "json":
            return super().create_endpoint(client, path, method)
        responses = self.get_endpoint_responses(endpoint)

        async def call(owner, **params):
            request = cast(
                OpenAlexJSONRequest, self.build_endpoint_request(endpoint, params)
            )
            response = await owner.client.request(
                method=request.method,
                url=request.url,
                json=request.body,
                follow_redirects=True,
                extensions={"hishel_body_key": "true"},
            )
            return self.process_endpoint_response(response, request)

        return self.decorate_endpoint_call(client, call, responses).__get__(
            client, type(client)
        )
