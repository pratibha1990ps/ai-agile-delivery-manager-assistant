"""Cross-squad and external dependency status."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import DependencyStatus


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT


def dependency_status(deps: pd.DataFrame, as_of: date,
                      thresholds: Thresholds) -> list[DependencyStatus]:
    cfg = thresholds.dependencies
    out = []
    for d in deps.to_dict(orient="records"):
        need, committed = d["needed_by_date"], d["committed_date"]
        committed = None if _blank(committed) else committed
        provider = d["providing_squad_id"] if not _blank(d["providing_squad_id"]) \
            else f"External: {d['external_party']}"
        blocks = None if _blank(d["blocks_milestone_id"]) else d["blocks_milestone_id"]
        days_late = 0

        if d["status"] == "Delivered":
            state, why = "delivered", f"Delivered {d['delivered_date']}."
        elif need + timedelta(days=cfg.late_tolerance_days) < as_of:
            days_late = (as_of - need).days
            state = "late"
            why = (f"Needed by {need.isoformat()}, not delivered: {days_late} days late"
                   + (f"; provider now commits {committed.isoformat()}." if committed else
                      "; no committed date from provider."))
        elif committed and committed > need:
            state = "at_risk"
            why = (f"Provider commits {committed.isoformat()}, after the needed-by date "
                   f"{need.isoformat()} ({(committed - need).days} days).")
        elif committed is None and (need - as_of).days <= cfg.unconfirmed_warning_days:
            state = "at_risk"
            why = f"Needed by {need.isoformat()} but the provider has not committed a date."
        else:
            state, why = "on_track", f"Needed by {need.isoformat()}; on track."
        if blocks:
            why += f" Blocks milestone {blocks}."
        out.append(DependencyStatus(
            d["dependency_id"], d["requesting_squad_id"], provider, state, days_late, need,
            committed, blocks, why,
        ))
    return out
