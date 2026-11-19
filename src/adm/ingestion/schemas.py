"""Row-level schemas for every importable entity, plus the entity registry.

Each pydantic model validates one source row: types, enums, ranges and simple
cross-field rules. Referential integrity across entities is checked in ``validator.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Row(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                k: (None if isinstance(v, str) and v.strip() == "" else v)
                for k, v in data.items()
            }
        return data

    def to_db(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.model_dump().items():
            if isinstance(v, date):
                v = v.isoformat()
            elif isinstance(v, bool):
                v = int(v)
            out[k] = v
        return out


def _check_range(start: date | None, end: date | None, label: str) -> None:
    if start and end and end < start:
        raise ValueError(f"{label}: end date is before start date")


# --- Entities ------------------------------------------------------------------

class ProgrammeRow(Row):
    programme_id: str
    name: str
    description: str | None = None
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _dates(self) -> ProgrammeRow:
        _check_range(self.start_date, self.end_date, "programme")
        return self


class SquadRow(Row):
    squad_id: str
    programme_id: str
    name: str
    domain: str | None = None
    team_size: int = Field(ge=1, le=30)
    capacity_points_per_sprint: int = Field(ge=0)


class ProgramIncrementRow(Row):
    pi_id: str
    programme_id: str
    name: str
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _dates(self) -> ProgramIncrementRow:
        _check_range(self.start_date, self.end_date, "program increment")
        return self


class SprintRow(Row):
    sprint_id: str
    pi_id: str
    name: str
    number: int = Field(ge=1)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _dates(self) -> SprintRow:
        _check_range(self.start_date, self.end_date, "sprint")
        return self


class SprintSquadCommitmentRow(Row):
    commitment_id: str
    sprint_id: str
    squad_id: str
    committed_points: int = Field(ge=0)
    completed_points: int = Field(ge=0)
    added_points: int = Field(ge=0)
    removed_points: int = Field(ge=0)
    carried_over_points: int = Field(ge=0)
    available_capacity_days: float = Field(ge=0)


class FeatureRow(Row):
    feature_id: str
    squad_id: str
    pi_id: str
    title: str
    business_value_planned: int = Field(ge=0, le=10)
    business_value_actual: int | None = Field(default=None, ge=0, le=10)
    status: Literal["Not Started", "In Progress", "Done", "Descoped"]
    target_sprint_id: str | None = None


class WorkItemRow(Row):
    work_item_id: str
    feature_id: str | None = None
    squad_id: str
    sprint_id: str | None = None
    type: Literal["Story", "Defect", "Spike"]
    title: str
    story_points: int = Field(ge=0, le=40)
    status: Literal["Done", "Not Done", "In Progress", "To Do", "Removed"]
    created_date: date
    done_date: date | None = None
    blocked_days: int = Field(default=0, ge=0)
    carried_over_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _done(self) -> WorkItemRow:
        if self.status == "Done" and self.done_date is None:
            raise ValueError("status is Done but done_date is missing")
        if self.done_date and self.done_date < self.created_date:
            raise ValueError("done_date is before created_date")
        return self


class PIObjectiveRow(Row):
    objective_id: str
    pi_id: str
    squad_id: str
    description: str
    committed: bool
    planned_bv: int = Field(ge=0, le=10)
    actual_bv: int | None = Field(default=None, ge=0, le=10)


class MilestoneRow(Row):
    milestone_id: str
    programme_id: str
    squad_id: str | None = None
    name: str
    baseline_date: date
    forecast_date: date
    actual_date: date | None = None
    status: Literal["Planned", "Complete"]
    owner: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> MilestoneRow:
        if self.status == "Complete" and self.actual_date is None:
            raise ValueError("status is Complete but actual_date is missing")
        return self


class DependencyRow(Row):
    dependency_id: str
    requesting_squad_id: str
    providing_squad_id: str | None = None
    external_party: str | None = None
    description: str
    needed_by_date: date
    committed_date: date | None = None
    delivered_date: date | None = None
    status: Literal["Requested", "Committed", "Delivered"]
    blocks_milestone_id: str | None = None

    @model_validator(mode="after")
    def _provider(self) -> DependencyRow:
        if not self.providing_squad_id and not self.external_party:
            raise ValueError("either providing_squad_id or external_party is required")
        if self.providing_squad_id and self.providing_squad_id == self.requesting_squad_id:
            raise ValueError("a squad cannot depend on itself")
        if self.status == "Delivered" and self.delivered_date is None:
            raise ValueError("status is Delivered but delivered_date is missing")
        return self


class RaidItemRow(Row):
    raid_id: str
    type: Literal["RISK", "ASSUMPTION", "ISSUE", "ACTION", "DECISION"]
    squad_id: str | None = None
    title: str
    description: str | None = None
    owner: str | None = None
    probability: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    status: Literal["Open", "In Progress", "Closed"]
    raised_date: date
    due_date: date | None = None
    last_updated: date
    mitigation: str | None = None
    linked_ids: str | None = None

    @model_validator(mode="after")
    def _scoring(self) -> RaidItemRow:
        if self.type == "RISK" and (self.probability is None or self.impact is None):
            raise ValueError("RISK requires probability and impact")
        if self.type == "ISSUE" and self.impact is None:
            raise ValueError("ISSUE requires impact")
        if self.type == "ACTION" and self.due_date is None:
            raise ValueError("ACTION requires due_date")
        if self.last_updated < self.raised_date:
            raise ValueError("last_updated is before raised_date")
        return self

    def linked_id_list(self) -> list[str]:
        return [x.strip() for x in (self.linked_ids or "").split(";") if x.strip()]


# --- Registry ------------------------------------------------------------------

@dataclass(frozen=True)
class EntitySpec:
    name: str                     # file stem / sheet name
    table: str
    id_field: str
    model: type[Row]
    foreign_keys: dict[str, str] = field(default_factory=dict)   # field -> entity name
    label: str = ""

    @property
    def required_columns(self) -> list[str]:
        return [n for n, f in self.model.model_fields.items() if f.is_required()]

    @property
    def columns(self) -> list[str]:
        return list(self.model.model_fields)


# Order matters: parents are imported before children.
ENTITIES: list[EntitySpec] = [
    EntitySpec("programme", "programme", "programme_id", ProgrammeRow, label="Programme"),
    EntitySpec("squad", "squad", "squad_id", SquadRow,
               {"programme_id": "programme"}, "Squads"),
    EntitySpec("program_increment", "program_increment", "pi_id", ProgramIncrementRow,
               {"programme_id": "programme"}, "Program Increments"),
    EntitySpec("sprint", "sprint", "sprint_id", SprintRow,
               {"pi_id": "program_increment"}, "Sprints"),
    EntitySpec("sprint_squad_commitment", "sprint_squad_commitment", "commitment_id",
               SprintSquadCommitmentRow, {"sprint_id": "sprint", "squad_id": "squad"},
               "Sprint Commitments"),
    EntitySpec("feature", "feature", "feature_id", FeatureRow,
               {"squad_id": "squad", "pi_id": "program_increment",
                "target_sprint_id": "sprint"}, "Features"),
    EntitySpec("work_item", "work_item", "work_item_id", WorkItemRow,
               {"feature_id": "feature", "squad_id": "squad", "sprint_id": "sprint"},
               "Work Items"),
    EntitySpec("pi_objective", "pi_objective", "objective_id", PIObjectiveRow,
               {"pi_id": "program_increment", "squad_id": "squad"}, "PI Objectives"),
    EntitySpec("milestone", "milestone", "milestone_id", MilestoneRow,
               {"programme_id": "programme", "squad_id": "squad"}, "Milestones"),
    EntitySpec("dependency", "dependency", "dependency_id", DependencyRow,
               {"requesting_squad_id": "squad", "providing_squad_id": "squad",
                "blocks_milestone_id": "milestone"}, "Dependencies"),
    EntitySpec("raid_item", "raid_item", "raid_id", RaidItemRow,
               {"squad_id": "squad"}, "RAID Log"),
]

ENTITY_BY_NAME: dict[str, EntitySpec] = {e.name: e for e in ENTITIES}
