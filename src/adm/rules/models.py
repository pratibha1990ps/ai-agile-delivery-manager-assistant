"""Result types returned by the rules engine. Every result carries the IDs it was built from."""

from __future__ import annotations

from dataclasses import dataclass, field

from adm.rules.rag import RAG


@dataclass(frozen=True)
class MetricResult:
    metric: str
    scope_id: str
    value: float | None
    rag: RAG | None
    source_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True)
class Finding:
    rule: str
    squad_id: str | None
    record_ids: tuple[str, ...]
    severity: str            # high | medium | low
    message: str


@dataclass(frozen=True)
class DependencyStatus:
    dependency_id: str
    requesting_squad_id: str
    provider: str            # squad id or external party name
    state: str               # late | at_risk | on_track | delivered
    days_late: int
    needed_by_date: object
    committed_date: object
    blocks_milestone_id: str | None
    explanation: str

    @property
    def is_critical(self) -> bool:
        return self.blocks_milestone_id is not None and self.state in ("late", "at_risk")


@dataclass(frozen=True)
class MilestoneStatus:
    milestone_id: str
    squad_id: str | None
    name: str
    state: str               # complete | overdue | late | at_risk | on_track
    rag: RAG | None          # None for completed milestones
    slip_days: int
    blocking_dependencies: tuple[str, ...]
    explanation: str


@dataclass
class SquadHealth:
    squad_id: str
    name: str
    metrics: dict[str, MetricResult]
    score: float | None
    rag: RAG
    reasons: list[str] = field(default_factory=list)

    @property
    def source_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        for m in self.metrics.values():
            ids.extend(m.source_ids)
        return tuple(dict.fromkeys(ids))
