"""Composite squad and programme health. Pure functions over rule results."""

from __future__ import annotations

from adm.config import Thresholds
from adm.rules.models import (
    DependencyStatus,
    Finding,
    MetricResult,
    MilestoneStatus,
    SquadHealth,
)
from adm.rules.rag import RAG, classify

RAG_POINTS = {RAG.GREEN: 1.0, RAG.AMBER: 0.5, RAG.RED: 0.0}


def raid_metric(squad_id: str, findings: list[Finding], thresholds: Thresholds) -> MetricResult:
    mine = [f for f in findings if f.squad_id == squad_id]
    ids = tuple(dict.fromkeys(rid for f in mine for rid in f.record_ids))
    n = len(mine)
    return MetricResult(
        "raid_hygiene_findings", squad_id, float(n),
        classify(n, thresholds.metrics["raid_hygiene_findings"]), ids,
        f"{n} open RAID hygiene finding(s)" + (f" on {', '.join(ids)}." if ids else "."),
    )


def dependency_metric(squad_id: str, deps: list[DependencyStatus],
                      thresholds: Thresholds) -> MetricResult:
    late = [d for d in deps if d.requesting_squad_id == squad_id and d.state == "late"]
    ids = tuple(d.dependency_id for d in late)
    return MetricResult(
        "late_dependencies", squad_id, float(len(late)),
        classify(len(late), thresholds.metrics["late_dependencies"]), ids,
        f"{len(late)} dependency(ies) this squad needs are late"
        + (f": {', '.join(ids)}." if ids else "."),
    )


def squad_health(squad_id: str, name: str, metrics: dict[str, MetricResult],
                 milestones: list[MilestoneStatus], thresholds: Thresholds) -> SquadHealth:
    weights = thresholds.health_weights
    scored = {k: m for k, m in metrics.items() if k in weights and m.rag is not None}
    total_w = sum(weights[k] for k in scored)
    score = (sum(weights[k] * RAG_POINTS[m.rag] for k, m in scored.items()) / total_w
             if total_w else None)

    comp = thresholds.composite
    reasons: list[str] = []
    if score is None:
        rag = RAG.AMBER
        reasons.append("No scored metrics available; defaulting to AMBER.")
    else:
        rag = RAG.GREEN if score >= comp.green else RAG.AMBER if score >= comp.amber else RAG.RED
        reasons.append(f"Weighted score {score:.2f} (GREEN >= {comp.green}, "
                       f"AMBER >= {comp.amber}).")
        reds = [k for k, m in scored.items() if m.rag is RAG.RED]
        if reds:
            reasons.append("RED metrics: " + ", ".join(reds) + ".")

    if comp.red_if_owns_red_milestone:
        red_ms = [m.milestone_id for m in milestones if m.squad_id == squad_id and m.rag is RAG.RED]
        if red_ms and rag is not RAG.RED:
            reasons.append(f"Overridden to RED: owns RED milestone {', '.join(red_ms)}.")
            rag = RAG.RED
        elif red_ms:
            reasons.append(f"Owns RED milestone {', '.join(red_ms)}.")

    return SquadHealth(squad_id, name, metrics, None if score is None else round(score, 4),
                       rag, reasons)


def programme_health(squads: list[SquadHealth],
                     milestones: list[MilestoneStatus]) -> tuple[RAG, list[str]]:
    red = [s.squad_id for s in squads if s.rag is RAG.RED]
    amber = [s.squad_id for s in squads if s.rag is RAG.AMBER]
    red_ms = [m.milestone_id for m in milestones if m.rag is RAG.RED]
    reasons = []
    if red:
        reasons.append(f"RED squads: {', '.join(red)}.")
    if amber:
        reasons.append(f"AMBER squads: {', '.join(amber)}.")
    if red_ms:
        reasons.append(f"RED milestones: {', '.join(red_ms)}.")
    if len(red) >= 2 or red_ms:
        return RAG.RED, reasons + ["Rule: two or more RED squads, or any RED milestone."]
    if red or amber:
        return RAG.AMBER, reasons + ["Rule: at least one squad is not GREEN."]
    return RAG.GREEN, ["All squads GREEN and no RED milestones."]
