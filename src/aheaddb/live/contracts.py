"""Version 1 wire contracts. Only data, never executable model artifacts."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

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


def publication_boundary(answer: Answer) -> tuple[str, str, str, str, int, str, str]:
    """Facts shared by one worker publication, before per-view freshness checks."""
    return (answer.generation, answer.snapshot_id, answer.data_version, answer.coverage.source_id,
            answer.coverage.revision, answer.coverage.observed_at, answer.published_at)


class GroupRead(StrictModel):
    names: tuple[Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")], ...] = Field(
        min_length=2, max_length=8,
    )
    generation: str | None = Field(default=None, min_length=1, max_length=128)
    max_age_seconds: float | None = Field(default=None, gt=0, le=86400)
    allow_stale: bool = False

    @model_validator(mode="after")
    def distinct_names(self) -> GroupRead:
        if len(set(self.names)) != len(self.names):
            raise ValueError("A group requires distinct view names")
        return self


class GroupAnswer(StrictModel):
    api_version: Literal["v1"] = "v1"
    generation: str
    answers: dict[str, Answer] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def coherent_answers(self) -> GroupAnswer:
        if (len({publication_boundary(a) for a in self.answers.values()}) != 1
                or len({a.coverage.current_revision for a in self.answers.values()}) != 1
                or any(a.generation != self.generation or a.task.name != name
                       for name, a in self.answers.items())):
            raise ValueError("Grouped answers must describe the same generation and source observation")
        return self


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
