"""Version 1 wire contracts. Only data, never executable model artifacts."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..spec import Schema, StrictModel, Task


class Mutation(StrictModel):
    table: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=128)
    row: dict | None = None


class Edit(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    mutations: tuple[Mutation, ...] = Field(min_length=1, max_length=1000)


class ModelRecipe(StrictModel):
    task: Task
    training_cutoffs: tuple[str, ...] = Field(min_length=1, max_length=256)
    training_as_of: str
    backend: Literal["baseline", "histogram", "robust", "linear", "median", "adaptive", "persistence"] = "adaptive"


class ViewDefinition(StrictModel):
    model_id: str
    cutoff: str
    freshness_seconds: float = Field(default=60, gt=0, le=86400)


class Coverage(StrictModel):
    source_id: str
    state: Literal["fresh", "stale", "unknown"]
    revision: int
    current_revision: int
    source_as_of: str
    observed_at: str
    age_seconds: float | None
    reason: str | None = None


class Answer(StrictModel):
    api_version: Literal["v1"] = "v1"
    generation: str
    task: Task
    task_version: str
    model_id: str
    model_version: str
    preprocessing: str
    prediction_cutoff: str
    snapshot_id: str
    data_version: str
    coverage: Coverage
    published_at: str
    publication_time_semantics: Literal["transaction_clock_lower_bound"] = "transaction_clock_lower_bound"
    predictions: dict[str, float]


class Bootstrap(StrictModel):
    schema_: Schema = Field(alias="schema")
    tables: dict[str, list[dict]]


class EditReceipt(StrictModel):
    api_version: Literal['v1'] = 'v1'
    revision: int = Field(ge=0)


class ModelReceipt(StrictModel):
    api_version: Literal['v1'] = 'v1'
    model_id: str


class ViewReceipt(StrictModel):
    api_version: Literal['v1'] = 'v1'
    view_name: str


class ViewStatus(StrictModel):
    name: str
    definition: ViewDefinition
    generation: str | None


class JobCount(StrictModel):
    state: Literal['pending', 'running', 'done', 'superseded', 'failed', 'cancelled']
    count: int = Field(ge=0)


class Status(StrictModel):
    api_version: Literal['v1'] = 'v1'
    source_id: str
    revision: int = Field(ge=0)
    processed_revision: int = Field(ge=0)
    clock_unknown: bool
    purge_epoch: int = Field(ge=0)
    views: list[ViewStatus]
    jobs: list[JobCount]


class ErrorResponse(StrictModel):
    api_version: Literal['v1'] = 'v1'
    code: Literal['invalid_request', 'unavailable', 'conflict', 'source_coverage_unknown',
                  'service_unavailable', 'origin_rejected', 'body_too_large']
    detail: str | None = None


class EvaluationRequest(StrictModel):
    recipe: ModelRecipe
    backtest_cutoffs: tuple[str, ...] = Field(min_length=3, max_length=64)
    labels_as_of: str
    prediction_cutoff: str


class EvaluationReport(StrictModel):
    api_version: Literal['v1'] = 'v1'
    source_id: str
    revision: int
    snapshot_id: str
    data_version: str
    plan: dict
    report: dict
