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


@dataclass(frozen=True)
class EscalationCandidate:
    """An open RAID item that meets the rule-based criteria for leadership attention.
    The rules decide *eligibility*; AI may explain or prioritise; a human decides."""
    raid_id: str
    squad_id: str | None
    type: str
    score: int | None
    reasons: tuple[str, ...]
    linked_critical: tuple[str, ...]   # critical dependencies / milestones it is linked to
    explanation: str

    @property
    def source_ids(self) -> tuple[str, ...]:
        return (self.raid_id, *self.linked_critical)

    @property
    def priority(self) -> tuple[int, int]:
        return (len(self.reasons), self.score or 0)


@dataclass(frozen=True)
class ObjectiveExposure:
    """Squad-level exposure: a late or at-risk dependency exposes the requesting squad's
    committed objectives in the current PI. This is an inferred association (the data has
    no direct dependency-to-objective link), not proof that a specific objective will fail."""
    dependency_id: str
    requesting_squad_id: str
    provider: str
    state: str                       # late | at_risk
    days_late: int
    blocks_milestone_id: str | None
    pi_id: str | None
    objective_ids: tuple[str, ...]
    explanation: str
    basis: str = "squad-level"

    @property
    def source_ids(self) -> tuple[str, ...]:
        # The PI is a source too: it decides which objectives count as "current".
        ms = (self.blocks_milestone_id,) if self.blocks_milestone_id else ()
        pi = (self.pi_id,) if self.pi_id else ()
        return (self.dependency_id, *ms, *pi, *self.objective_ids)


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
