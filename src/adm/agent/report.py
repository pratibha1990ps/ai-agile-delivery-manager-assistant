"""Check the agent's weekly report against the evidence the agent actually gathered (A4).

The agent's final answer is a report written by an AI, so it gets exactly the same checks as the
RAID Analyst: every statement must cite facts that exist, the IDs, numbers and RAG statuses in its
text must match those facts, and a suggested ask must be backed by a finding that passed.

What is reused, not rebuilt: ``adm.ai.grounding.validate``. This module only
1. reads the agent's final answer in *report* terms (headline, findings by section, asks);
2. turns the facts the agent collected into a ``FactPack`` (the agent can cite only what its tools
   returned, never anything it merely "knows");
3. runs the validator and maps each verdict back to its report section;
4. builds the **squad status table from rule results in code**, never from AI text.

Report shape the agent must produce (``answer`` of its ``final`` action)::

    {"headline": "...",
     "findings": [{"section": "off_track" | "risks_dependencies", "statement": "...",
                   "fact_ids": ["..."], "confidence": "high" | "medium" | "low"}],
     "leadership_asks": [{"action", "rationale", "owner_role", "urgency", "fact_ids",
                          "finding_refs"}],           # finding_refs are indexes into findings
     "data_gaps": ["..."]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from adm.agent.loop import AgentRun
from adm.agent.tools import TOOLS
from adm.ai.grounding import Status, ValidatedAnalysis, ValidatedItem, validate
from adm.ai.models import (
    PROGRAMME_SCOPE,
    AIFinding,
    AIRecommendation,
    AnalysisRequest,
    AnalysisResponse,
    FactPack,
    Focus,
)

USE_CASE = "weekly_report"
SECTIONS = ("off_track", "risks_dependencies")
SECTION_TITLES = {"off_track": "What is off track", "risks_dependencies": "Risks and dependencies"}
RAG_ORDER = {"RED": 0, "AMBER": 1, "GREEN": 2}


class ReportStatus(StrEnum):
    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    PARTIALLY_REJECTED = "partially_rejected"
    INVALID = "invalid"                   # the answer was not a readable report
    AGENT_INCOMPLETE = "agent_incomplete"  # the agent never produced a final answer


_FROM_GROUNDING = {
    Status.VALID: ReportStatus.VALID,
    Status.VALID_WITH_WARNINGS: ReportStatus.VALID_WITH_WARNINGS,
    Status.PARTIALLY_REJECTED: ReportStatus.PARTIALLY_REJECTED,
    Status.INVALID: ReportStatus.INVALID,
}


class ReportFinding(AIFinding):
    """A finding placed in one section of the report."""
    section: Literal["off_track", "risks_dependencies"]


class ReportAnswer(BaseModel):
    """The only shape accepted for the agent's final answer."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    headline: str = Field(min_length=10, max_length=1000)
    findings: list[ReportFinding] = Field(default_factory=list, max_length=15)
    leadership_asks: list[AIRecommendation] = Field(default_factory=list, max_length=10)
    data_gaps: list[str] = Field(default_factory=list, max_length=10)

    def to_analysis(self) -> AnalysisResponse:
        """The same content in the shape the grounding validator already understands."""
        return AnalysisResponse(
            summary=self.headline,
            findings=[AIFinding(**f.model_dump(exclude={"section"})) for f in self.findings],
            recommendations=list(self.leadership_asks), data_gaps=list(self.data_gaps))


@dataclass(frozen=True)
class SquadRow:
    """One row of the status table. Taken straight from a rule result (a HEALTH fact)."""
    squad_id: str
    name: str
    rag: str
    score: float | None
    reasons: tuple[str, ...]
    fact_id: str


@dataclass
class ValidatedReport:
    status: ReportStatus
    run: AgentRun
    pack: FactPack | None = None
    answer: ReportAnswer | None = None
    validation: ValidatedAnalysis | None = None
    squad_table: list[SquadRow] = field(default_factory=list)
    tools_consulted: tuple[str, ...] = ()
    coverage_notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (ReportStatus.VALID, ReportStatus.VALID_WITH_WARNINGS)

    @property
    def headline_shown(self) -> bool:
        return self.validation is not None and self.validation.summary_shown

    def findings_in(self, section: str) -> list[ValidatedItem]:
        """Validated findings for one section, including rejected ones (the page decides)."""
        if self.answer is None or self.validation is None:
            return []
        return [v for v, f in zip(self.validation.findings, self.answer.findings, strict=True)
                if f.section == section]

    @property
    def asks(self) -> list[ValidatedItem]:
        return self.validation.recommendations if self.validation else []


def build_pack(run: AgentRun, as_of: date, rule_version: str) -> FactPack:
    """The evidence the agent gathered, as a fact pack the validator can check against."""
    return FactPack(
        use_case=USE_CASE,
        request=AnalysisRequest(scope=PROGRAMME_SCOPE, focus=Focus.RTE_ACTIONS,
                                note="Weekly report agent: evidence is what its tools returned."),
        as_of=as_of, rule_version=rule_version, facts=run.evidence_facts, omitted_count=0)


def squad_table(run: AgentRun) -> list[SquadRow]:
    """Status table from rule results only. Worst squads first, then by ID."""
    rows = []
    for fact in run.evidence_facts:
        if not fact.fact_id.startswith("HEALTH:"):
            continue
        d = fact.data
        rows.append(SquadRow(
            squad_id=fact.fact_id.split(":", 1)[1], name=str(d.get("squad", "")),
            rag=str(d.get("rag", "")), score=d.get("score"),
            reasons=tuple(str(r) for r in d.get("reasons", ())), fact_id=fact.fact_id))
    return sorted(rows, key=lambda r: (RAG_ORDER.get(r.rag, 9), r.squad_id))


def validate_report(run: AgentRun, as_of: date, rule_version: str) -> ValidatedReport:
    """Validate a finished agent run. Never raises."""
    pack = build_pack(run, as_of, rule_version)
    used = tuple(dict.fromkeys(s.tool for s in run.steps if s.kind == "tool_call" and s.tool))
    table = squad_table(run)
    notes = []
    if "get_programme_health" not in used and not table:
        notes.append("The agent did not retrieve programme health, so there is no squad status "
                     "table.")
    unused = [t.name for t in TOOLS if t.name not in used]
    if unused:
        notes.append("Tools not consulted: " + ", ".join(unused) + ".")
    report = ValidatedReport(status=ReportStatus.AGENT_INCOMPLETE, run=run, pack=pack,
                             squad_table=table, tools_consulted=used, coverage_notes=notes)

    if run.final_answer is None:
        report.error = run.error or "The agent did not produce a final answer."
        return report
    try:
        report.answer = ReportAnswer.model_validate(run.final_answer)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'report'}: {e['msg']}"
                             for e in exc.errors()[:4])
        report.status, report.error = ReportStatus.INVALID, (
            f"The agent's answer is not a valid report: {problems}")
        return report

    report.validation = validate(report.answer.to_analysis().model_dump_json(), pack)
    report.status = _FROM_GROUNDING[report.validation.status]
    report.error = report.validation.error
    return report


def summarise(report: ValidatedReport) -> dict[str, Any]:
    """JSON-safe summary for the audit log (used when the review page is added in A6)."""
    return {"status": report.status.value, "error": report.error,
            "run_status": report.run.status.value, "tool_calls": report.run.tool_calls_used,
            "tools_consulted": list(report.tools_consulted),
            "validation": report.validation.report() if report.validation else None}
