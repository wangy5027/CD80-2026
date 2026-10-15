"""Request models shared by query and mutation endpoints."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "isnull"]


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    op: FilterOperator
    value: Any = None


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    after_id: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    sort_by: str = "_row_id"
    order: Literal["asc", "desc"] = "asc"
    columns: str | list[str] | None = None
    filters: list[FilterSpec] = Field(default_factory=list, max_length=20)
    include_total: bool = False


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]


class BatchWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
