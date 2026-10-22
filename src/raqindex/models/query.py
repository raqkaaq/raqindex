from pydantic import BaseModel, Field, model_validator
from .oqo import Model as OQO
from .openalex import RetrievalParameters


class XQuery(BaseModel):
    oql: str = Field(..., description="The OpenAlex Query Language (OQL) query string used to search for works")
    x_new_oql: str | None = Field(None, description="The new OQL query string if the original query was modified by the API")
    oqo: OQO | None = Field(None, description="The OpenAlex Query Object (OQO) representation of the query")
    url: str | None = Field(None, description="The URL of the OpenAlex API endpoint that was used to perform the query")

class QueryDiagnostic(BaseModel):
    type: str
    message: str
    location: object | None = None
    position: int | None = None
    context: str | None = None
class Validation(BaseModel):
    valid: bool = Field(..., description="Indicates whether the query is valid or not")
    errors: list[QueryDiagnostic | str] = Field(..., description="A list of error messages if the query is invalid")
    warnings: list[QueryDiagnostic | str] = Field(..., description="A list of warning messages if the query is valid but has potential issues")

class Estimate(BaseModel):
    seconds: float = Field(..., description="Estimated time in seconds for the query to execute")
    es_calls: int = Field(..., description="Estimated number of Elasticsearch calls required for the query")
    budget_seconds: float = Field(..., description="Estimated budget in seconds for the query execution")
    within_budget: bool = Field(..., description="Indicates whether the query is within the estimated budget or not")

class Cost(BaseModel):
    credits: int = Field(..., description="Estimated number of credits required for the query execution")
    usd: float = Field(..., description="Estimated cost in USD for the query execution")
    steps: list[dict] = Field(..., description="A list of steps involved in the query execution, each with its own cost and description")

class Check(BaseModel):
    valid: bool = Field(..., description="Indicates whether the query is valid or not")
    limits: list[str] = Field(..., description="A list containing any limits or constraints on the query")
    cost: Cost | None = Field(..., description="The cost of the query execution")
    estimate: Estimate | None = Field(..., description="An estimate of the query execution time and resources required")

class QueryResponse(XQuery):
    url:  str | None = Field(None, description="The URL of the OpenAlex API endpoint that was used to perform the query")
    validation: Validation = Field(..., description="The validation results for the query, indicating whether it is valid and any associated errors or warnings")
    check: Check | None = Field(None, description="The check results for the query, indicating its validity, limits, cost, and estimate")

    @model_validator(mode="before")
    @classmethod
    def adapt_input(cls, values):
        """rename the oxurl to url"""
        if "oxurl" in values:
            values["url"] = values.pop("oxurl")
        return values

class QueryRequest(RetrievalParameters):
    oqo: OQO | None = Field(None, description="The OpenAlex Query Object (OQO) representation of the query")
    oql: str | None = Field(None, description="The OpenAlex Query Language (OQL) query string used to search for works")

class QueryErrorResponse(BaseModel):
    validation: Validation
