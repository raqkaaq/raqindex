from h11 import Request
from pydantic import BaseModel, Field, ValidationInfo, field_serializer, field_validator, model_validator
from typing import Literal


class Endpoint(BaseModel):
    endpoint: str = Field(..., description="The endpoint path for the API")
    method: Method = Field(..., description="The HTTP method for the endpoint (e.g., GET, POST)")
    
    def __repr_args__(self):
        codes = [
            field.alias
            for name, field in type(self.method.responses).model_fields.items()
            if getattr(self.method.responses, name) is not None
        ]
        return [
            ("endpoint", self.endpoint),
            ("id", self.method.operationId),
            ("method", self.method.method),
            ("codes", codes),
        ]

class Method(BaseModel):
    method: str = Field(..., description="The HTTP method for the endpoint (e.g., GET, POST)")
    operationId: str = Field(..., description="The unique identifier for the operation in the OpenAPI specification")
    summary: str = Field(..., description="A brief summary of the operation")
    description: str = Field(..., description="A detailed description of the operation")
    tags: list[str] = Field(..., description="A list of tags associated with the operation")
    request: ReqRes = Field(..., description="The request schema for the operation")
    responses: ResponseCollection = Field(..., description="A collection of possible responses for the operation")
    payload: Literal["json", "params"] = Field("params", description="The type of payload expected for the request (either JSON or URL parameters)")


class ResponseCollection(BaseModel):
    model_config = {
        "extra": "forbid",  # Allow extra fields to accommodate various HTTP status codes
    }
    success: ReqRes | None = Field(None, alias="200")
    bad_request: ReqRes | None = Field(None, alias="400")
    forbidden: ReqRes | None = Field(None, alias="403")
    not_found: ReqRes | None = Field(None, alias="404")
    rate_limited: ReqRes | None = Field(None, alias="429")


class ReqRes(BaseModel):
    ref: str = Field(..., description="A reference to the response schema in the OpenAPI specification")
    model: type[BaseModel] | None = Field(None, description="The Pydantic model representing the response schema")
    
    @field_serializer("model", when_used="json")
    def serialize_model(self, value):
        return value.__name__ if value is not None else None

    @field_validator("model", mode="before")
    @classmethod
    def resolve_model(cls, value):
        if isinstance(value, str):
            from . import openalex as generated_models
            return getattr(generated_models, value)
        return value