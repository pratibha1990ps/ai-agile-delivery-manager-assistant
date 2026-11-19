"""Escalation candidates: which open RAID items meet the criteria for leadership attention.

Deciding *what is eligible for escalation* is a business rule, so it lives here, not in the
AI layer. Criteria (thresholds in ``thresholds.yaml`` -> ``escalation``):

* RISK  - score >= ``min_risk_score`` AND (no owner OR no mitigation OR linked to a critical
          dependency / milestone).
* ISSUE - impact >= ``issue_min_impact`` AND (open longer than ``raid.issue_ageing_days``
          OR linked to a critical dependency / milestone).
* ACTION - overdue AND linked to a critical dependency / milestone.

"Critical" means a late or at-risk dependency that blocks a milestone, or a milestone the
rules rate RED (and AMBER, if ``include_amber_milestones``).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import DependencyStatus, EscalationCandidate, MilestoneStatus
from adm.rules.rag import RAG
from adm.rules.raid import _blank, risk_score


def critical_items(dependencies: list[DependencyStatus], milestones: list[MilestoneStatus],
                   thresholds: Thresholds) -> set[str]:
    rags = {RAG.RED, RAG.AMBER} if thresholds.escalation.include_amber_milestones else {RAG.RED}
    return ({d.dependency_id for d in dependencies if d.is_critical}
            | {m.milestone_id for m in milestones if m.rag in rags})


def _linked(row: dict) -> list[str]:
    raw = row.get("linked_ids")
    return [] if _blank(raw) else [x.strip() for x in str(raw).split(";") if x.strip()]


def escalation_candidates(raid: pd.DataFrame, dependencies: list[DependencyStatus],
                          milestones: list[MilestoneStatus], as_of: date,
                          thresholds: Thresholds) -> list[EscalationCandidate]:
    cfg = thresholds.escalation
    critical = critical_items(dependencies, milestones, thresholds)
    out: list[EscalationCandidate] = []

    for r in raid.to_dict(orient="records"):
        if r["status"] == "Closed":
            continue
        rid, kind = r["raid_id"], r["type"]
        squad = None if _blank(r["squad_id"]) else r["squad_id"]
        score = risk_score(r)
        linked_critical = tuple(sorted(i for i in _linked(r) if i in critical))
        link_reason = (f"linked to critical item(s) {', '.join(linked_critical)}"
                       if linked_critical else None)
        reasons: list[str] = []

        if kind == "RISK" and score is not None and score >= cfg.min_risk_score:
            if _blank(r["owner"]):
                reasons.append("no owner")
            if _blank(r["mitigation"]):
                reasons.append("no mitigation")
            if link_reason:
                reasons.append(link_reason)
            if reasons:
                reasons.insert(0, f"risk score {score} (threshold {cfg.min_risk_score})")
        elif kind == "ISSUE" and not _blank(r.get("impact")) \
                and int(r["impact"]) >= cfg.issue_min_impact:
            age = (as_of - r["raised_date"]).days
            if age > thresholds.raid.issue_ageing_days:
                reasons.append(f"open {age} days")
            if link_reason:
                reasons.append(link_reason)
            if reasons:
                reasons.insert(0, f"impact {int(r['impact'])}")
        elif kind == "ACTION" and not _blank(r["due_date"]) and r["due_date"] < as_of \
                and link_reason:
            reasons = [f"overdue by {(as_of - r['due_date']).days} days", link_reason]

        if reasons:
            out.append(EscalationCandidate(
                rid, squad, kind, score, tuple(reasons), linked_critical,
                f"{rid} meets the escalation criteria: {'; '.join(reasons)}.",
            ))
    return sorted(out, key=lambda c: (-c.priority[0], -c.priority[1], c.raid_id))
