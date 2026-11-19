"""Milestone status from baseline, forecast and actual dates."""

from __future__ import annotations

from datetime import date

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import DependencyStatus, MilestoneStatus
from adm.rules.rag import RAG


def milestone_status(milestones: pd.DataFrame, as_of: date, thresholds: Thresholds,
                     dependencies: list[DependencyStatus] | None = None) -> list[MilestoneStatus]:
    cfg = thresholds.milestones
    blockers: dict[str, list[str]] = {}
    for dep in dependencies or []:
        if dep.blocks_milestone_id and dep.state in ("late", "at_risk"):
            blockers.setdefault(dep.blocks_milestone_id, []).append(dep.dependency_id)

    out = []
    for m in milestones.to_dict(orient="records"):
        mid = m["milestone_id"]
        base, fc, actual = m["baseline_date"], m["forecast_date"], m["actual_date"]
        squad = None if m["squad_id"] is None or pd.isna(m["squad_id"]) else m["squad_id"]
        blocked = tuple(sorted(blockers.get(mid, [])))
        block_note = f" Blocked by: {', '.join(blocked)}." if blocked else ""

        if m["status"] == "Complete":
            var = (actual - base).days
            timing = "on time" if var <= 0 else f"{var} days after baseline"
            out.append(MilestoneStatus(mid, squad, m["name"], "complete", None, var, blocked,
                                       f"Completed {actual.isoformat()} ({timing})."))
            continue

        slip = (fc - base).days
        if fc < as_of:
            state, rag = "overdue", RAG.RED
            why = f"Forecast date {fc.isoformat()} has passed without completion."
        elif slip >= cfg.red_slip_days:
            state, rag = "late", RAG.RED
            why = f"Forecast {fc.isoformat()} is {slip} days after baseline {base.isoformat()}."
        elif slip >= cfg.amber_slip_days:
            state, rag = "at_risk", RAG.AMBER
            why = f"Forecast {fc.isoformat()} is {slip} days after baseline {base.isoformat()}."
        else:
            state, rag = "on_track", RAG.GREEN
            why = f"Forecast {fc.isoformat()} matches baseline."
        out.append(MilestoneStatus(mid, squad, m["name"], state, rag, slip, blocked,
                                   why + block_note))
    return out
