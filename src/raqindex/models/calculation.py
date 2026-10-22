"""Raw OQO aggregation responses (before MCP presentation shaping).

Calculated columns are dynamic extras, identified by ``meta.measures[*].key``.
Calculated values accept JSON scalars without coercion; measure-specific types
are not yet enforced. Undeclared row fields follow the same scalar restriction.
Structure checked against production-derived fixtures in:
https://github.com/ourresearch/openalex-mcp-server/blob/main/test/calculate.test.ts
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from .openalex import Meta
from .query import Cost, XQuery


CalculationValue = StrictInt | StrictFloat | StrictStr | StrictBool | None


class CalculationModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class MeasureDescriptor(CalculationModel):
    key: str
    measure: str | None = None
    column_id: str | None = None
    oql: str | None = None


class SplitDescriptor(CalculationModel):
    kind: str | None = None
    column_id: str | None = None
    oql: str | None = None


class CalculationRow(CalculationModel):
    __pydantic_extra__: dict[str, CalculationValue] = Field(init=False)

    key: Any = None
    key_display_name: str | None = None
    groups: list[CalculationRow] | None = None

    def measure_values(self, measures: list[MeasureDescriptor]) -> dict[str, CalculationValue]:
        """Read declared measures, retaining nulls and omitting absent values."""
        extras = self.model_extra or {}
        return {m.key: extras[m.key] for m in measures if m.key in extras}


class SplitSummary(CalculationModel):
    groups: list[CalculationRow] = Field(default_factory=list)
    more_groups: bool | None = None


class CalculationSummary(CalculationModel):
    all: CalculationRow | None = None
    splits: list[SplitSummary] = Field(default_factory=list)


class CalculationMeta(Meta):
    model_config = ConfigDict(extra="allow")

    measures: list[MeasureDescriptor] = Field(default_factory=list)
    splits: list[SplitDescriptor] = Field(default_factory=list)
    more_groups: bool | None = None
    elapsed_ms: float | None = None
    cost: Cost | None = None
    x_query: XQuery | None = None


class CalculationResponse(CalculationModel):
    meta: CalculationMeta
    group_by: list[CalculationRow] = Field(default_factory=list)
    summary: CalculationSummary | None = None
