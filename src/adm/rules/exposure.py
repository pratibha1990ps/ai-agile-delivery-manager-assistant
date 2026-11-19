"""Dependency -> PI objective exposure (squad level).

The data does not link a dependency to a specific objective. The defensible, deterministic
association is: a dependency that is late or at risk exposes the *requesting squad's committed
objectives in the current PI*. Every exposure is labelled ``basis="squad-level"`` so the UI
and the AI present it as an exposure to review, not as proof that an objective will fail.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import DependencyStatus, ObjectiveExposure
from adm.rules.pi import current_pi_id


def objective_exposures(dependencies: list[DependencyStatus], objectives: pd.DataFrame,
                        pis: pd.DataFrame, as_of: date,
                        thresholds: Thresholds) -> list[ObjectiveExposure]:
    cfg = thresholds.exposure
    pi_id = current_pi_id(pis, as_of)
    in_pi = objectives[objectives["pi_id"] == pi_id] if pi_id else objectives.iloc[0:0]
    if cfg.committed_only:
        in_pi = in_pi[in_pi["committed"].astype(bool)]

    out = []
    for d in dependencies:
        if d.state not in cfg.states:
            continue
        objs = tuple(sorted(in_pi.loc[in_pi["squad_id"] == d.requesting_squad_id,
                                      "objective_id"]))
        lateness = f"{d.days_late} days late" if d.state == "late" else "at risk"
        milestone = f", blocks {d.blocks_milestone_id}" if d.blocks_milestone_id else ""
        scope = (f"{len(objs)} committed {pi_id} objective(s) of {d.requesting_squad_id}"
                 if objs else f"no committed {pi_id} objectives of {d.requesting_squad_id}")
        out.append(ObjectiveExposure(
            d.dependency_id, d.requesting_squad_id, d.provider, d.state, d.days_late,
            d.blocks_milestone_id, pi_id, objs,
            f"{d.dependency_id} ({lateness}{milestone}) exposes {scope} (squad-level "
            "association, not a direct link).",
        ))
    return sorted(out, key=lambda e: (e.state != "late", -e.days_late, e.dependency_id))
