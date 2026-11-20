"""Fact Pack builder: turns deterministic rule output into the evidence an AI may see.

Three rules shape it:

1. **Only verified information.** Every fact comes from an imported record or a rule result,
   and lists the source record IDs it was built from. Nothing is summarised or guessed here.
2. **Only what is relevant.** The *scope* (programme or one squad) filters by squad; the
   *focus* (the user's question) decides which kinds of fact are included and in what order.
3. **Bounded size.** Facts are ranked and capped at ``max_facts``; the number left out is
   recorded so the AI (and the user) know the picture is partial.

Fact ID conventions (all traceable to records through ``source_ids``):
``RAID-R-031`` (a record), ``RULE:overdue_action:RAID-A-018`` (a hygiene finding),
``ESC:RAID-R-031`` (escalation candidate), ``DEPSTATE:DEP-012``, ``EXPOSURE:DEP-012``,
``MSSTATE:MS-07``, ``METRIC:say_do_ratio:DAT``, ``HEALTH:DAT``, ``AGG:overdue_actions:IDS``,
``CTX:programme`` / ``CTX:squad:DAT``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from adm.ai.models import PROGRAMME_SCOPE, AnalysisRequest, Fact, FactKind, FactPack, Focus
from adm.config import Thresholds
from adm.rules.escalation import escalation_candidates
from adm.rules.exposure import objective_exposures
from adm.rules.models import EscalationCandidate, ObjectiveExposure
from adm.rules.pi import current_pi_id
from adm.rules.raid import RULE_LABELS, risk_score
from adm.services.control_tower import METRIC_LABELS, HealthReport, build_health_report
from adm.services.data import ProgrammeData

USE_CASE = "raid_analyst"
DESCRIPTION_CHARS = 240
SEVERITY_RANK = {"high": 0, "medium": 10, "low": 20}

# Which fact families each focus needs, and how important they are (lower = kept first).
# Families missing from a focus are not retrieved at all.
FOCUS_FAMILIES: dict[Focus, dict[str, int]] = {
    Focus.IMMEDIATE_ATTENTION: {
        "escalation": 100, "finding": 200, "dependency": 300, "milestone": 350,
        "metric": 400, "raid_record": 500, "dep_record": 600, "aggregate": 700},
    Focus.DEPENDENCIES_VS_OBJECTIVES: {
        "exposure": 100, "dependency": 150, "milestone": 200, "objective_record": 300,
        "dep_record": 350, "feature_record": 450, "raid_record": 500, "aggregate": 600},
    Focus.ESCALATION: {
        "escalation": 100, "dependency": 200, "milestone": 250, "finding": 300,
        "raid_record": 400, "dep_record": 500, "aggregate": 600},
    Focus.CROSS_SQUAD_PATTERNS: {
        "aggregate": 100, "health": 150, "metric": 200, "finding": 300, "escalation": 350,
        "dependency": 400, "raid_record": 600},
    Focus.RTE_ACTIONS: {
        "escalation": 100, "dependency": 200, "milestone": 250, "exposure": 275,
        "finding": 300, "health": 350, "metric": 400, "aggregate": 450, "raid_record": 500,
        "dep_record": 600},
}


# --- deterministic rule bundle ------------------------------------------------------------

@dataclass
class RuleBundle:
    """Everything the rules engine says about the programme on the as-of date."""
    as_of: date
    rule_version: str
    report: HealthReport
    escalations: list[EscalationCandidate]
    exposures: list[ObjectiveExposure]


def compute_rule_bundle(data: ProgrammeData, as_of: date, thresholds: Thresholds) -> RuleBundle:
    report = build_health_report(data, as_of, thresholds)
    return RuleBundle(
        as_of=as_of, rule_version=thresholds.version, report=report,
        escalations=escalation_candidates(data.raid, report.dependencies, report.milestones,
                                          as_of, thresholds),
        exposures=objective_exposures(report.dependencies, data.objectives, data.pis, as_of,
                                      thresholds),
    )


# --- helpers ------------------------------------------------------------------------------

def clean(value: Any) -> Any:
    """Make a value JSON-safe and stable: dates -> ISO text, NaN -> None, numpy -> python."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, date)):   # numpy scalar
        value = value.item()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value) if value.is_integer() else value   # 4.0 -> 4 (pandas upcasts)
    return value


def _blank(value: Any) -> bool:
    return clean(value) in (None, "")


def _squad_of(value: Any) -> str | None:
    return None if _blank(value) else str(value)


def _linked(value: Any) -> list[str]:
    return [] if _blank(value) else [x.strip() for x in str(value).split(";") if x.strip()]


class _Collector:
    """Accumulates (priority, fact) pairs for the families the focus needs."""

    def __init__(self, families: dict[str, int], squads_in_scope: set[str] | None) -> None:
        self.families = families
        self.squads = squads_in_scope          # None = programme scope
        self.items: dict[str, tuple[int, Fact]] = {}

    def wants(self, family: str) -> bool:
        return family in self.families

    def in_scope(self, *squads: str | None) -> bool:
        if self.squads is None:
            return True
        return any(s in self.squads for s in squads if s)

    def add(self, family: str, fact: Fact, rank: int = 0) -> None:
        if family not in self.families:
            return
        priority = self.families[family] + rank
        current = self.items.get(fact.fact_id)
        if current is None or priority < current[0]:
            self.items[fact.fact_id] = (priority, fact)


# --- the builder --------------------------------------------------------------------------

def scope_squads(data: ProgrammeData, scope: str) -> set[str] | None:
    """The squads a scope covers: ``None`` for the whole programme, else that one squad."""
    squads = {str(s) for s in data.squads["squad_id"]}
    if scope == PROGRAMME_SCOPE:
        return None
    if scope not in squads:
        raise ValueError(f"Unknown scope '{scope}'. Use 'programme' or one of "
                         f"{sorted(squads)}.")
    return {scope}


def collect_facts(data: ProgrammeData, bundle: RuleBundle, scope: str,
                  families: dict[str, int], max_facts: int) -> tuple[list[Fact], int]:
    """Gather facts of the given families for a scope, best first, capped at ``max_facts``.

    ``families`` maps a family name to its rank (lower = kept first). Returns the kept facts and
    how many lower-ranked facts were left out. Used by the Fact Pack builder (one focus) and by
    the agent's read-only tools (one family set per tool).
    """
    squads = {str(s) for s in data.squads["squad_id"]}
    c = _Collector(families, scope_squads(data, scope))
    report = bundle.report
    _health_and_metrics(c, report)
    _findings(c, report)
    _escalations(c, bundle.escalations)
    _dependencies(c, report)
    _exposures(c, bundle.exposures)
    _milestones(c, report)
    _raid_records(c, data.raid)
    _dependency_records(c, data.dependencies, report)
    _objective_and_feature_records(c, data, bundle)
    _aggregates(c, data, bundle, squads)
    ranked = sorted(c.items.values(), key=lambda pf: (pf[0], pf[1].fact_id))
    room = max(max_facts, 0)
    return [f for _, f in ranked[:room]], max(len(ranked) - room, 0)


def build_fact_pack(data: ProgrammeData, bundle: RuleBundle, request: AnalysisRequest,
                    max_facts: int = 80) -> FactPack:
    scope_squads(data, request.scope)                       # fail early on an unknown scope
    context = _context_facts(data, bundle, request)
    kept, omitted = collect_facts(data, bundle, request.scope, FOCUS_FAMILIES[request.focus],
                                  max(max_facts - len(context), 0))
    return FactPack(
        use_case=USE_CASE, request=request, as_of=bundle.as_of,
        rule_version=bundle.rule_version, facts=tuple(context + kept),
        omitted_count=omitted,
    )


# --- fact families ------------------------------------------------------------------------

def _context_facts(data: ProgrammeData, bundle: RuleBundle,
                   request: AnalysisRequest) -> list[Fact]:
    prog = data.programme.iloc[0]
    pi_id = current_pi_id(data.pis, bundle.as_of)
    sprint = bundle.report.current_sprint
    sources = tuple(x for x in (str(prog["programme_id"]), pi_id, sprint) if x)
    facts = [Fact(
        fact_id="CTX:programme", kind=FactKind.CONTEXT, source_ids=sources,
        data={"programme": prog["name"], "as_of": bundle.as_of.isoformat(),
              "current_pi": pi_id, "current_sprint": sprint,
              "last_closed_sprint": bundle.report.last_closed_sprint,
              "rule_version": bundle.rule_version,
              "squads": sorted(str(s) for s in data.squads["squad_id"]),
              "scope": request.scope, "question": request.question,
              "note": "All statuses and metrics come from deterministic rules."},
    )]
    if not request.is_programme:
        sq = data.squads[data.squads["squad_id"] == request.scope].iloc[0]
        facts.append(Fact(
            fact_id=f"CTX:squad:{request.scope}", kind=FactKind.CONTEXT,
            source_ids=(request.scope,), squad_id=request.scope,
            data={"name": sq["name"], "domain": clean(sq["domain"]),
                  "team_size": clean(sq["team_size"]),
                  "capacity_points_per_sprint": clean(sq["capacity_points_per_sprint"])},
        ))
    return facts


def _health_and_metrics(c: _Collector, report: HealthReport) -> None:
    for s in report.squads:
        if not c.in_scope(s.squad_id):
            continue
        c.add("health", Fact(
            fact_id=f"HEALTH:{s.squad_id}", kind=FactKind.METRIC,
            source_ids=s.source_ids or (s.squad_id,), squad_id=s.squad_id,
            data={"metric": "composite_health", "squad": s.name, "rag": s.rag.value,
                  "score": s.score, "reasons": list(s.reasons)},
        ), rank={"RED": 0, "AMBER": 5, "GREEN": 10}[s.rag.value])   # keep all: needed to compare
        for m in s.metrics.values():
            rag = m.rag.value if m.rag else None
            c.add("metric", Fact(
                fact_id=f"METRIC:{m.metric}:{s.squad_id}", kind=FactKind.METRIC,
                source_ids=m.source_ids or (s.squad_id,), squad_id=s.squad_id,
                data={"metric": m.metric, "label": METRIC_LABELS.get(m.metric, m.metric),
                      "value": m.value, "rag": rag, "explanation": m.explanation},
            ), rank={"RED": 0, "AMBER": 20, "GREEN": 300, None: 350}[rag])   # healthy last


def _findings(c: _Collector, report: HealthReport) -> None:
    for f in report.findings:
        if not c.in_scope(f.squad_id) or (c.squads and f.squad_id is None):
            continue
        c.add("finding", Fact(
            fact_id=f"RULE:{f.rule}:{'+'.join(f.record_ids)}", kind=FactKind.RULE_RESULT,
            source_ids=f.record_ids, squad_id=f.squad_id,
            data={"rule": f.rule, "label": RULE_LABELS.get(f.rule, f.rule),
                  "severity": f.severity, "message": f.message},
        ), rank=SEVERITY_RANK.get(f.severity, 30))


def _escalations(c: _Collector, escalations: list[EscalationCandidate]) -> None:
    for i, e in enumerate(escalations):
        if not c.in_scope(e.squad_id) or (c.squads and e.squad_id is None):
            continue
        c.add("escalation", Fact(
            fact_id=f"ESC:{e.raid_id}", kind=FactKind.RULE_RESULT, source_ids=e.source_ids,
            squad_id=e.squad_id,
            data={"rule": "escalation_candidate", "label": "Escalation candidate",
                  "severity": "high", "message": e.explanation, "raid_type": e.type,
                  "risk_score": e.score, "reasons": list(e.reasons),
                  "linked_critical": list(e.linked_critical), "priority_order": i + 1},
        ), rank=i)


def _dependencies(c: _Collector, report: HealthReport) -> None:
    for d in report.dependencies:
        if d.state not in ("late", "at_risk"):
            continue
        provider_squad = None if d.provider.startswith("External:") else d.provider
        if not c.in_scope(d.requesting_squad_id, provider_squad):
            continue
        critical = d.is_critical
        c.add("dependency", Fact(
            fact_id=f"DEPSTATE:{d.dependency_id}", kind=FactKind.RULE_RESULT,
            source_ids=(d.dependency_id,) + ((d.blocks_milestone_id,)
                                             if d.blocks_milestone_id else ()),
            squad_id=d.requesting_squad_id,
            data={"rule": f"dependency_{d.state}",
                  "label": "Late dependency" if d.state == "late" else "Dependency at risk",
                  "severity": "high" if critical else "medium", "message": d.explanation,
                  "state": d.state, "days_late": d.days_late, "provider": d.provider,
                  "blocks_milestone": d.blocks_milestone_id, "critical": critical},
        ), rank=(0 if critical else 20) - min(d.days_late, 19))


def _exposures(c: _Collector, exposures: list[ObjectiveExposure]) -> None:
    for i, e in enumerate(exposures):
        if not c.in_scope(e.requesting_squad_id):
            continue
        c.add("exposure", Fact(
            fact_id=f"EXPOSURE:{e.dependency_id}", kind=FactKind.RULE_RESULT,
            source_ids=e.source_ids, squad_id=e.requesting_squad_id,
            data={"rule": "objective_exposure", "label": "PI objectives exposed",
                  "severity": "high" if e.state == "late" else "medium",
                  "message": e.explanation, "basis": e.basis, "pi": e.pi_id,
                  "objective_ids": list(e.objective_ids),
                  "exposed_objective_count": len(e.objective_ids)},
        ), rank=i)


def _milestones(c: _Collector, report: HealthReport) -> None:
    for m in report.milestones:
        if m.rag is None or (m.rag.value == "GREEN" and not m.blocking_dependencies):
            continue          # completed, or on track with nothing blocking it
        if not c.in_scope(m.squad_id):
            continue
        c.add("milestone", Fact(
            fact_id=f"MSSTATE:{m.milestone_id}", kind=FactKind.RULE_RESULT,
            source_ids=(m.milestone_id, *m.blocking_dependencies), squad_id=m.squad_id,
            data={"rule": f"milestone_{m.state}", "label": f"Milestone {m.state}",
                  "severity": "high" if m.rag.value == "RED" else "medium",
                  "message": m.explanation, "name": m.name, "rag": m.rag.value,
                  "slip_days": m.slip_days,
                  "blocking_dependencies": list(m.blocking_dependencies)},
        ), rank={"RED": 0, "AMBER": 10, "GREEN": 20}[m.rag.value])


def _raid_records(c: _Collector, raid: pd.DataFrame) -> None:
    for r in raid.to_dict(orient="records"):
        squad = _squad_of(r["squad_id"])
        if r["status"] == "Closed" or not c.in_scope(squad) or (c.squads and squad is None):
            continue
        score = risk_score(r)
        desc = clean(r.get("description")) or ""
        c.add("raid_record", Fact(
            fact_id=r["raid_id"], kind=FactKind.RECORD, source_ids=(r["raid_id"],),
            squad_id=squad,
            data={"type": r["type"], "title": r["title"],
                  "description": desc[:DESCRIPTION_CHARS], "owner": clean(r["owner"]),
                  "status": r["status"], "probability": clean(r["probability"]),
                  "impact": clean(r["impact"]), "risk_score": score,
                  "raised_date": clean(r["raised_date"]), "due_date": clean(r["due_date"]),
                  "last_updated": clean(r["last_updated"]),
                  "mitigation": clean(r["mitigation"]), "linked_ids": _linked(r["linked_ids"])},
        ), rank=25 - (score or clean(r["impact"]) or 0))


def _dependency_records(c: _Collector, deps: pd.DataFrame, report: HealthReport) -> None:
    states = {d.dependency_id: d.state for d in report.dependencies}
    for d in deps.to_dict(orient="records"):
        if states.get(d["dependency_id"]) not in ("late", "at_risk"):
            continue
        provider = _squad_of(d["providing_squad_id"])
        if not c.in_scope(d["requesting_squad_id"], provider):
            continue
        c.add("dep_record", Fact(
            fact_id=d["dependency_id"], kind=FactKind.RECORD, source_ids=(d["dependency_id"],),
            squad_id=d["requesting_squad_id"],
            data={"description": d["description"], "requesting_squad": d["requesting_squad_id"],
                  "providing_squad": provider, "external_party": clean(d["external_party"]),
                  "needed_by_date": clean(d["needed_by_date"]),
                  "committed_date": clean(d["committed_date"]), "status": d["status"],
                  "blocks_milestone": clean(d["blocks_milestone_id"])},
        ))


def _objective_and_feature_records(c: _Collector, data: ProgrammeData,
                                   bundle: RuleBundle) -> None:
    pi_id = current_pi_id(data.pis, bundle.as_of)
    exposed = {o for e in bundle.exposures for o in e.objective_ids}
    for o in data.objectives.to_dict(orient="records"):
        if o["pi_id"] != pi_id or not bool(o["committed"]) or not c.in_scope(o["squad_id"]):
            continue
        c.add("objective_record", Fact(
            fact_id=o["objective_id"], kind=FactKind.RECORD, source_ids=(o["objective_id"],),
            squad_id=o["squad_id"],
            data={"pi": o["pi_id"], "description": o["description"], "committed": True,
                  "planned_business_value": clean(o["planned_bv"]),
                  "exposed_by_dependency": o["objective_id"] in exposed},
        ), rank=0 if o["objective_id"] in exposed else 50)
    for f in data.features.to_dict(orient="records"):
        if f["pi_id"] != pi_id or f["status"] == "Done" or not c.in_scope(f["squad_id"]):
            continue
        c.add("feature_record", Fact(
            fact_id=f["feature_id"], kind=FactKind.RECORD, source_ids=(f["feature_id"],),
            squad_id=f["squad_id"],
            data={"pi": f["pi_id"], "title": f["title"], "status": f["status"],
                  "target_sprint": clean(f["target_sprint_id"]),
                  "planned_business_value": clean(f["business_value_planned"])},
        ))


def _count_fact(fact_id: str, label: str, squad: str | None, ids: Iterable[str],
                fallback: str) -> Fact:
    ids = tuple(dict.fromkeys(ids))
    return Fact(fact_id=fact_id, kind=FactKind.AGGREGATE, source_ids=ids or (fallback,),
                squad_id=squad, data={"label": label, "count": len(ids)})


def _aggregates(c: _Collector, data: ProgrammeData, bundle: RuleBundle,
                squads: set[str]) -> None:
    if not c.wants("aggregate"):
        return
    report = bundle.report
    for sq in sorted(squads):
        if not c.in_scope(sq):
            continue
        mine = [f for f in report.findings if f.squad_id == sq]
        c.add("aggregate", _count_fact(
            f"AGG:open_findings:{sq}", f"open RAID hygiene findings for {sq}", sq,
            (r for f in mine for r in f.record_ids), sq))
        c.add("aggregate", _count_fact(
            f"AGG:overdue_actions:{sq}", f"overdue actions for {sq}", sq,
            (r for f in mine if f.rule == "overdue_action" for r in f.record_ids), sq))
        c.add("aggregate", _count_fact(
            f"AGG:escalation_candidates:{sq}", f"escalation candidates for {sq}", sq,
            (e.raid_id for e in bundle.escalations if e.squad_id == sq), sq))
        c.add("aggregate", _count_fact(
            f"AGG:late_dependencies:{sq}", f"late dependencies needed by {sq}", sq,
            (d.dependency_id for d in report.dependencies
             if d.requesting_squad_id == sq and d.state == "late"), sq))
        c.add("aggregate", _count_fact(
            f"AGG:exposed_objectives:{sq}", f"committed current-PI objectives of {sq} exposed "
            "by late or at-risk dependencies", sq,
            (o for e in bundle.exposures if e.requesting_squad_id == sq
             for o in e.objective_ids), sq), rank=5)
