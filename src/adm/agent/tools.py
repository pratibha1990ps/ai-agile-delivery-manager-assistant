"""Read-only tools for the Weekly Delivery Report Agent.

A *tool* is a button the agent is allowed to press. Each one runs the existing deterministic
rules and hands back **facts** (with IDs and source records), never free text. So the agent can
only learn about the programme through the same verified evidence the RAID Analyst uses.

Safety by design:
* **Read-only.** The toolbox holds in-memory data and the rule results. It has no database
  connection, no file access and no network access, so a tool cannot change anything.
* **Closed list.** Only the tools below exist. An unknown tool or argument is an error.
* **Bounded.** Each call returns at most ``max_facts_per_call`` facts and says how many were left
  out, so the agent knows when the picture is partial.
* **Errors are messages, not crashes.** A bad call raises ``ToolError`` with a plain sentence the
  agent loop can feed back to the model so it can correct itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from adm.ai.models import PROGRAMME_SCOPE, Fact
from adm.facts.fact_pack import RuleBundle, collect_facts, scope_squads
from adm.services.data import ProgrammeData

DEFAULT_MAX_FACTS_PER_CALL = 25


class ToolError(Exception):
    """A tool call that cannot be run. The message is safe to show to the model."""


class _SquadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    squad: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """What a tool is: its name, what it tells the model, and which fact families it reads."""
    name: str
    description: str
    families: dict[str, int]            # fact family -> rank (lower = kept first)
    squad: str = "none"                 # "none" | "optional" | "required"


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "get_programme_health",
        "Rule-computed overall health (RED/AMBER/GREEN) for every squad. Start here.",
        {"health": 0}),
    ToolSpec(
        "get_squad_metrics",
        "One squad's health and its delivery metrics (say/do, velocity, scope change, "
        "carry-over, PI objectives, RAID hygiene, late dependencies), each with its rule-assigned "
        "status, plus counts. Needs a squad.",
        {"health": 0, "metric": 100, "aggregate": 200}, squad="required"),
    ToolSpec(
        "get_open_raid_items",
        "Open risks, issues and actions, plus rule findings such as overdue actions or risks "
        "without an owner. Optionally for one squad.",
        {"finding": 100, "raid_record": 200, "aggregate": 300}, squad="optional"),
    ToolSpec(
        "get_late_dependencies",
        "Dependencies that are late or at risk, the milestones they block, and the committed "
        "PI objectives of the waiting squad that are exposed. Optionally for one squad.",
        {"dependency": 100, "exposure": 150, "dep_record": 300}, squad="optional"),
    ToolSpec(
        "get_escalation_candidates",
        "Items the rules say should be considered for escalation, with the reasons. "
        "Optionally for one squad.",
        {"escalation": 100}, squad="optional"),
    ToolSpec(
        "get_milestone_status",
        "Milestone status (RED/AMBER/GREEN) and what drives it. Optionally for one squad.",
        {"milestone": 100}, squad="optional"),
)
_BY_NAME = {t.name: t for t in TOOLS}


@dataclass(frozen=True)
class ToolResult:
    """What a tool returns: verified facts, and how many were left out by the size cap."""
    tool: str
    scope: str
    facts: tuple[Fact, ...]
    omitted_count: int

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(f.fact_id for f in self.facts)

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe form to hand back to the model."""
        return {"tool": self.tool, "scope": self.scope, "fact_count": len(self.facts),
                "omitted_count": self.omitted_count,
                "facts": [f.model_dump(mode="json") for f in self.facts]}


class ToolBox:
    """The closed set of read-only tools, bound to one programme snapshot."""

    def __init__(self, data: ProgrammeData, bundle: RuleBundle,
                 max_facts_per_call: int = DEFAULT_MAX_FACTS_PER_CALL) -> None:
        self._data = data
        self._bundle = bundle
        self._max = max_facts_per_call
        self._squads = sorted(str(s) for s in data.squads["squad_id"])

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(t.name for t in TOOLS)

    def specs(self) -> list[dict[str, Any]]:
        """Tool definitions in the JSON-schema shape LLM tool-calling APIs expect."""
        out = []
        for t in TOOLS:
            props: dict[str, Any] = {}
            if t.squad != "none":
                props["squad"] = {"type": "string", "enum": self._squads,
                                  "description": "Squad ID to focus on."}
            out.append({
                "name": t.name, "description": t.description,
                "input_schema": {"type": "object", "properties": props,
                                 "required": ["squad"] if t.squad == "required" else [],
                                 "additionalProperties": False}})
        return out

    def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        spec = _BY_NAME.get(name)
        if spec is None:
            raise ToolError(f"Unknown tool '{name}'. Available tools: {', '.join(self.names)}.")
        scope = self._scope(spec, args or {})
        try:
            facts, omitted = collect_facts(self._data, self._bundle, scope, spec.families,
                                           self._max)
        except ValueError as exc:                       # defensive: scope already validated
            raise ToolError(str(exc)) from exc
        return ToolResult(tool=name, scope=scope, facts=tuple(facts), omitted_count=omitted)

    def _scope(self, spec: ToolSpec, args: dict[str, Any]) -> str:
        if spec.squad == "none":
            if args:
                raise ToolError(f"Tool '{spec.name}' takes no arguments, got {sorted(args)}.")
            return PROGRAMME_SCOPE
        try:
            squad = _SquadArgs(**args).squad
        except (ValidationError, TypeError) as exc:
            raise ToolError(f"Invalid arguments for '{spec.name}'. Only 'squad' is accepted "
                            f"(one of {self._squads}).") from exc
        if squad is None:
            if spec.squad == "required":
                raise ToolError(f"Tool '{spec.name}' needs a squad: one of {self._squads}.")
            return PROGRAMME_SCOPE
        try:
            if squad == PROGRAMME_SCOPE:        # 'programme' is a scope, not a squad ID
                raise ValueError(squad)
            scope_squads(self._data, squad)
        except ValueError as exc:
            raise ToolError(f"Unknown squad '{squad}'. Valid squads: {self._squads}.") from exc
        return squad
