"""Interpretable deadline/slack batching. No learned policy or causal claim."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..spec import digest

Policy = Literal["full", "fifo", "debounce", "budget"]


@dataclass(frozen=True)
class Candidate:
    id: int
    view_name: str
    revision: int
    queued_at: datetime
    deadline: datetime
    context: str
    cost_seconds: float


def context_key(schema: dict, task: dict, definition: dict) -> str:
    from ..compiler import Compiler
    return digest([schema, task["history_days"], definition["cutoff"], Compiler.PREPROCESSING])


def choose(candidates: list[Candidate], now: datetime, policy: Policy,
           quiet_seconds: float = 0.05, fairness_seconds: float = 1.0) -> list[int]:
    if not candidates:
        return []
    if policy in {"full", "fifo"}:
        anchor = min(candidates, key=lambda c: c.id)
        fifo_selected: dict[str, int] = {}
        for candidate in sorted(candidates, key=lambda c: c.id):
            if candidate.revision == anchor.revision and candidate.context == anchor.context:
                fifo_selected.setdefault(candidate.view_name, candidate.id)
        return list(fifo_selected.values())
    if policy not in {"debounce", "budget"}:
        raise ValueError("Unknown refresh policy")
    # Preserve the earliest outstanding deadline/age even when a newer update
    # supersedes computation. Resetting the age on each edit would starve hot tasks.
    by_view: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_view.setdefault(candidate.view_name, []).append(candidate)
    groups = list(by_view.values())
    if policy == "debounce":
        eligible = [g for g in groups if (now - max(c.queued_at for c in g)).total_seconds() >= quiet_seconds
                    or (now - min(c.queued_at for c in g)).total_seconds() >= fairness_seconds]
        if not eligible:
            return []
        first = min(eligible, key=lambda g: min(c.id for c in g))
    else:
        # Earliest deadline minus conservative measured batch cost, then age.
        overdue = [g for g in groups if (now - min(c.queued_at for c in g)).total_seconds() >= fairness_seconds]
        first = (min(overdue, key=lambda g: min(c.id for c in g)) if overdue else
                 min(groups, key=lambda g: (min(c.deadline for c in g).timestamp()
                                           - max(c.cost_seconds for c in g), min(c.id for c in g))))
        age = (now - min(c.queued_at for c in first)).total_seconds()
        slack = (min(c.deadline for c in first) - now).total_seconds() - max(c.cost_seconds for c in first)
        latest_age = (now - max(c.queued_at for c in first)).total_seconds()
        if age < fairness_seconds and slack > quiet_seconds and latest_age < quiet_seconds:
            return []
        eligible = groups
    selected = max(first, key=lambda c: (c.revision, c.id))
    # Bundle only identical source boundaries and feature semantics. Each model
    # predicts independently; shared aggregates are not shared model activations.
    batch = [max(g, key=lambda c: (c.revision, c.id)) for g in eligible]
    return [c.id for c in batch if c.context == selected.context and c.revision == selected.revision]
