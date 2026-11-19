"""Contracts for the AI layer: what goes into the model and what must come back.

Three groups of models:

1. **Request** - ``AnalysisRequest``: what the user asked for (scope + focus + optional note).
2. **Evidence** - ``Fact`` / ``FactPack``: the only information the model is allowed to see.
   Every fact carries the source record IDs it was built from, and the whole pack has a
   SHA-256 hash so an audit can prove exactly what the model saw.
3. **Answer** - ``AnalysisResponse``: the JSON shape the model must return. It has no field
   for a RAG status on purpose: statuses come only from the rules engine.

``LLMRequest`` / ``LLMResponse`` are the provider-neutral envelope used by every provider.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- 1. Request ------------------------------------------------------------------------


class Focus(StrEnum):
    """Analysis presets. The focus decides deterministically which facts are retrieved."""
    IMMEDIATE_ATTENTION = "immediate_attention"
    DEPENDENCIES_VS_OBJECTIVES = "dependencies_vs_objectives"
    ESCALATION = "escalation"
    CROSS_SQUAD_PATTERNS = "cross_squad_patterns"
    RTE_ACTIONS = "rte_actions"


FOCUS_QUESTIONS: dict[Focus, str] = {
    Focus.IMMEDIATE_ATTENTION: "Which delivery risks need immediate attention?",
    Focus.DEPENDENCIES_VS_OBJECTIVES: "Which dependencies threaten PI objectives?",
    Focus.ESCALATION: "Which RAID items require leadership escalation?",
    Focus.CROSS_SQUAD_PATTERNS: "What patterns are emerging across squads?",
    Focus.RTE_ACTIONS: "What actions should the RTE consider?",
}

PROGRAMME_SCOPE = "programme"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    scope: str = PROGRAMME_SCOPE      # "programme" or a squad_id such as "DAT"
    focus: Focus
    note: str = Field(default="", max_length=2000)

    @field_validator("scope")
    @classmethod
    def _scope_not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("scope must be 'programme' or a squad ID")
        return v

    @property
    def is_programme(self) -> bool:
        return self.scope == PROGRAMME_SCOPE

    @property
    def question(self) -> str:
        return FOCUS_QUESTIONS[self.focus]


# --- 2. Evidence -----------------------------------------------------------------------


class FactKind(StrEnum):
    CONTEXT = "context"          # programme / squad / sprint context
    RECORD = "record"            # an imported record, e.g. RAID-R-031
    RULE_RESULT = "rule_result"  # a deterministic rule outcome, e.g. an overdue action
    METRIC = "metric"            # a computed metric with its rule-assigned RAG
    AGGREGATE = "aggregate"      # a computed count, so the AI can cite numbers, not invent them


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str = Field(pattern=r"^\S+$")
    kind: FactKind
    source_ids: tuple[str, ...] = Field(min_length=1)
    squad_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _record_traces_to_itself(self) -> Fact:
        if self.kind is FactKind.RECORD and self.fact_id not in self.source_ids:
            raise ValueError("a record fact must list its own ID as a source")
        return self


class FactPack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    use_case: str
    request: AnalysisRequest
    as_of: date
    rule_version: str
    facts: tuple[Fact, ...]
    omitted_count: int = Field(default=0, ge=0)   # facts dropped by the size cap

    @model_validator(mode="after")
    def _unique_ids(self) -> FactPack:
        ids = [f.fact_id for f in self.facts]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate fact IDs: {dupes}")
        return self

    def canonical_json(self) -> str:
        """Stable serialisation: same facts -> same text -> same hash."""
        return json.dumps(self.model_dump(mode="json"), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def fact_ids(self) -> set[str]:
        return {f.fact_id for f in self.facts}

    @property
    def by_id(self) -> dict[str, Fact]:
        return {f.fact_id: f for f in self.facts}

    @property
    def source_ids(self) -> list[str]:
        """Every source record ID supplied to the model (for the audit log)."""
        return sorted({s for f in self.facts for s in f.source_ids})


# --- 3. Answer (the model's required output) ---------------------------------------------


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Urgency(StrEnum):
    IMMEDIATE = "immediate"      # act before the next stand-up / within days
    THIS_SPRINT = "this_sprint"
    MONITOR = "monitor"          # watch, no action yet


class AIFinding(BaseModel):
    """An interpretation of the facts. Must cite the fact IDs it relies on."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    statement: str = Field(min_length=10, max_length=600)
    fact_ids: list[str] = Field(default_factory=list)
    confidence: Confidence


class AIRecommendation(BaseModel):
    """A suggested action for a human to approve or reject."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=5, max_length=300)
    rationale: str = Field(min_length=5, max_length=600)
    owner_role: str = Field(min_length=2, max_length=80)   # e.g. "RTE", "Squad lead (IDS)"
    urgency: Urgency
    fact_ids: list[str] = Field(default_factory=list)
    finding_refs: list[int] = Field(default_factory=list)  # indexes into findings

    @field_validator("finding_refs")
    @classmethod
    def _non_negative(cls, v: list[int]) -> list[int]:
        if any(i < 0 for i in v):
            raise ValueError("finding_refs must be zero-based indexes")
        return v


class AnalysisResponse(BaseModel):
    """The only shape accepted from a model. ``extra='forbid'`` means a model cannot sneak in
    fields such as ``rag`` or ``status``; missing citations are judged per item later, so one
    weak item does not discard the whole answer."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=10, max_length=1000)
    findings: list[AIFinding] = Field(default_factory=list, max_length=15)
    recommendations: list[AIRecommendation] = Field(default_factory=list, max_length=10)
    data_gaps: list[str] = Field(default_factory=list, max_length=10)

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """JSON Schema embedded in prompts, generated from this model so the prompt and the
        validator can never disagree about the expected shape."""
        return cls.model_json_schema()


# --- Provider envelope -----------------------------------------------------------------


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    user: str
    temperature: float = 0.0
    max_tokens: int = 1500
    timeout_seconds: float = 60.0
    response_schema: dict[str, Any] | None = None
    # Structured copy of the evidence. Only the mock provider reads it; real providers send
    # the rendered text prompt (which already contains the same fact pack as JSON).
    fact_pack: FactPack | None = None


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    provider: str
    model: str
    latency_ms: int | None = None
    usage: dict[str, int] = Field(default_factory=dict)
