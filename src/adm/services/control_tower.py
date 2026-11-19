"""Control Tower service: runs every rule and assembles one traceable health report."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import pandas as pd

from adm.config import Thresholds
from adm.rules.dependencies import dependency_status
from adm.rules.health import dependency_metric, programme_health, raid_metric, squad_health
from adm.rules.milestones import milestone_status
from adm.rules.models import DependencyStatus, Finding, MilestoneStatus, SquadHealth
from adm.rules.pi import objective_achievement
from adm.rules.rag import RAG
from adm.rules.raid import open_risks_by_score, raid_findings
from adm.rules.sprint import closed_sprint_ids, current_sprint_id, sprint_metrics, sprint_series
from adm.services.data import ProgrammeData

METRIC_LABELS = {
    "say_do_ratio": "Say/do",
    "velocity_cv": "Velocity variability",
    "scope_change_pct": "Scope change",
    "carry_over_pct": "Carry-over",
    "pi_objective_achievement": "PI objectives",
    "raid_hygiene_findings": "RAID hygiene",
    "late_dependencies": "Late dependencies",
}
PERCENT_METRICS = {"say_do_ratio", "velocity_cv", "scope_change_pct", "carry_over_pct",
                   "pi_objective_achievement"}


@dataclass
class HealthReport:
    as_of: date
    rule_version: str
    current_sprint: str | None
    last_closed_sprint: str | None
    programme_rag: RAG
    programme_reasons: list[str]
    squads: list[SquadHealth]
    findings: list[Finding]
    dependencies: list[DependencyStatus]
    milestones: list[MilestoneStatus]
    top_risks: pd.DataFrame
    series: pd.DataFrame
    trend: dict[str, float | None] = field(default_factory=dict)  # squad -> say/do delta

    def squad(self, squad_id: str) -> SquadHealth:
        return next(s for s in self.squads if s.squad_id == squad_id)


def format_value(metric: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}" if metric in PERCENT_METRICS else f"{value:.0f}"


def build_health_report(data: ProgrammeData, as_of: date, thresholds: Thresholds) -> HealthReport:
    findings = raid_findings(data.raid, as_of, thresholds)
    deps = dependency_status(data.dependencies, as_of, thresholds)
    milestones = milestone_status(data.milestones, as_of, thresholds, deps)

    squads = []
    for sq in data.squads.to_dict(orient="records"):
        sid = sq["squad_id"]
        metrics = sprint_metrics(data.commitments, data.sprints, sid, as_of, thresholds)
        metrics["pi_objective_achievement"] = objective_achievement(
            data.objectives, data.pis, sid, as_of, thresholds)
        metrics["raid_hygiene_findings"] = raid_metric(sid, findings, thresholds)
        metrics["late_dependencies"] = dependency_metric(sid, deps, thresholds)
        squads.append(squad_health(sid, sq["name"], metrics, milestones, thresholds))

    prog_rag, prog_reasons = programme_health(squads, milestones)
    series = sprint_series(data.commitments, data.sprints)
    closed = closed_sprint_ids(data.sprints, as_of)

    trend: dict[str, float | None] = {}
    if len(closed) >= 2:
        last, prev = closed[-1], closed[-2]
        for s in squads:
            rows = series[series["squad_id"] == s.squad_id].set_index("sprint_id")["say_do_ratio"]
            trend[s.squad_id] = (float(rows[last] - rows[prev])
                                 if last in rows and prev in rows else None)

    return HealthReport(
        as_of=as_of, rule_version=thresholds.version,
        current_sprint=current_sprint_id(data.sprints, as_of),
        last_closed_sprint=closed[-1] if closed else None,
        programme_rag=prog_rag, programme_reasons=prog_reasons, squads=squads,
        findings=findings, dependencies=deps, milestones=milestones,
        top_risks=open_risks_by_score(data.raid), series=series, trend=trend,
    )


def save_snapshot(conn: sqlite3.Connection, report: HealthReport) -> int:
    """Persist metric results (value, RAG, rule version, source IDs). Replaces any snapshot
    for the same as-of date and rule version, so re-running is idempotent."""
    as_of, ver = report.as_of.isoformat(), report.rule_version
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for s in report.squads:
        for m in s.metrics.values():
            rows.append((as_of, "squad", s.squad_id, m.metric, m.value,
                         m.rag.value if m.rag else None, ver, json.dumps(list(m.source_ids)),
                         now))
        rows.append((as_of, "squad", s.squad_id, "composite_health", s.score, s.rag.value, ver,
                     json.dumps(list(s.source_ids)), now))
    rows.append((as_of, "programme", "programme", "programme_health", None,
                 report.programme_rag.value, ver,
                 json.dumps([s.squad_id for s in report.squads]), now))
    with conn:
        conn.execute("DELETE FROM health_snapshot WHERE as_of_date = ? AND rule_version = ?",
                     (as_of, ver))
        conn.executemany(
            """INSERT INTO health_snapshot (as_of_date, scope, scope_id, metric, value, rag,
               rule_version, source_ids, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return len(rows)
