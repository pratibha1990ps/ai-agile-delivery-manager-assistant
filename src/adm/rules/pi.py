"""Program Increment (PI) objective achievement."""

from __future__ import annotations

from datetime import date

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import MetricResult
from adm.rules.rag import classify


def last_closed_pi(pis: pd.DataFrame, as_of: date) -> str | None:
    closed = pis[pis["end_date"] < as_of].sort_values("end_date")
    return None if closed.empty else str(closed.iloc[-1]["pi_id"])


def objective_achievement(objectives: pd.DataFrame, pis: pd.DataFrame, squad_id: str,
                          as_of: date, thresholds: Thresholds) -> MetricResult:
    """SAFe-style: actual business value of all objectives (incl. stretch) divided by
    planned business value of committed objectives, for the last closed PI."""
    metric = "pi_objective_achievement"
    pi_id = last_closed_pi(pis, as_of)
    if pi_id is None:
        return MetricResult(metric, squad_id, None, None, (), "No PI has closed yet.")
    rows = objectives[(objectives["squad_id"] == squad_id) & (objectives["pi_id"] == pi_id)]
    sources = tuple(rows["objective_id"])
    planned = int(rows.loc[rows["committed"].astype(bool), "planned_bv"].sum())
    if planned == 0:
        return MetricResult(metric, squad_id, None, None, sources,
                            f"No committed objectives with planned value in {pi_id}.")
    missing = int(rows["actual_bv"].isna().sum())
    actual = int(rows["actual_bv"].fillna(0).sum())
    value = actual / planned
    note = f" {missing} objective(s) have no actual value recorded." if missing else ""
    return MetricResult(
        metric, squad_id, round(value, 4), classify(value, thresholds.metrics[metric]), sources,
        f"{pi_id}: actual business value {actual} vs {planned} planned for committed "
        f"objectives ({value:.0%}).{note}",
    )
