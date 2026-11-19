"""Sprint delivery metrics per squad, computed from sprint commitment records."""

from __future__ import annotations

from datetime import date
from statistics import mean, pstdev

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import MetricResult
from adm.rules.rag import classify


def closed_sprint_ids(sprints: pd.DataFrame, as_of: date) -> list[str]:
    """Sprints that ended before the as-of date, oldest first."""
    closed = sprints[sprints["end_date"] < as_of].sort_values("number")
    return list(closed["sprint_id"])


def current_sprint_id(sprints: pd.DataFrame, as_of: date) -> str | None:
    cur = sprints[(sprints["start_date"] <= as_of) & (sprints["end_date"] >= as_of)]
    return None if cur.empty else str(cur.iloc[0]["sprint_id"])


def _window(commitments: pd.DataFrame, squad_id: str, sprint_ids: list[str]) -> pd.DataFrame:
    order = {s: i for i, s in enumerate(sprint_ids)}
    rows = commitments[(commitments["squad_id"] == squad_id)
                       & commitments["sprint_id"].isin(sprint_ids)].copy()
    rows["_order"] = rows["sprint_id"].map(order)
    return rows.sort_values("_order")


def _span(rows: pd.DataFrame) -> str:
    ids = list(rows["sprint_id"])
    return ids[0] if len(ids) == 1 else f"{ids[0]}-{ids[-1]}"


def _ratio_metric(metric: str, squad_id: str, rows: pd.DataFrame, numerator: int,
                  label: str, thresholds: Thresholds) -> MetricResult:
    committed = int(rows["committed_points"].sum())
    sources = tuple(rows["commitment_id"])
    if rows.empty or committed == 0:
        return MetricResult(metric, squad_id, None, None, sources,
                            "Not enough closed-sprint data to calculate.")
    value = numerator / committed
    return MetricResult(
        metric, squad_id, round(value, 4), classify(value, thresholds.metrics[metric]), sources,
        f"{label} {numerator} vs {committed} committed points over {_span(rows)} "
        f"({value:.0%}).",
    )


def sprint_metrics(commitments: pd.DataFrame, sprints: pd.DataFrame, squad_id: str,
                   as_of: date, thresholds: Thresholds) -> dict[str, MetricResult]:
    closed = closed_sprint_ids(sprints, as_of)
    win = _window(commitments, squad_id, closed[-thresholds.windows.say_do_sprints:])

    results = {
        "say_do_ratio": _ratio_metric(
            "say_do_ratio", squad_id, win, int(win["completed_points"].sum()),
            "Completed", thresholds),
        "scope_change_pct": _ratio_metric(
            "scope_change_pct", squad_id, win,
            int(win["added_points"].sum() + win["removed_points"].sum()),
            "Scope changed (added + removed)", thresholds),
        "carry_over_pct": _ratio_metric(
            "carry_over_pct", squad_id, win, int(win["carried_over_points"].sum()),
            "Carried over", thresholds),
    }

    vel = _window(commitments, squad_id, closed[-thresholds.windows.velocity_sprints:])
    values = [int(v) for v in vel["completed_points"]]
    sources = tuple(vel["commitment_id"])
    if len(values) < 3 or mean(values) == 0:
        results["velocity_cv"] = MetricResult(
            "velocity_cv", squad_id, None, None, sources,
            "At least three closed sprints are needed to measure velocity variability.")
    else:
        cv = pstdev(values) / mean(values)
        results["velocity_cv"] = MetricResult(
            "velocity_cv", squad_id, round(cv, 4),
            classify(cv, thresholds.metrics["velocity_cv"]), sources,
            f"Completed points {values} over {_span(vel)}: mean {mean(values):.1f}, "
            f"variation {cv:.0%}.",
        )
    return results


def sprint_series(commitments: pd.DataFrame, sprints: pd.DataFrame) -> pd.DataFrame:
    """Per sprint and squad ratios for charts (includes the in-flight sprint)."""
    df = commitments.merge(sprints[["sprint_id", "number", "end_date"]], on="sprint_id")
    committed = df["committed_points"].where(df["committed_points"] > 0)
    df["say_do_ratio"] = df["completed_points"] / committed
    df["scope_change_pct"] = (df["added_points"] + df["removed_points"]) / committed
    df["carry_over_pct"] = df["carried_over_points"] / committed
    return df.sort_values(["number", "squad_id"]).reset_index(drop=True)
