"""Planted scenarios: deliberate, known facts in the synthetic data.

They give every module something real to find, and they are the golden expected results
for tests: Phase 1 checks they exist in the data; Phase 2+ checks the rules detect them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

AS_OF = date(2026, 11, 19)


@dataclass(frozen=True)
class Scenario:
    key: str
    squad_id: str | None
    record_ids: tuple[str, ...]
    expectation: str


SQUAD_PERSONALITIES = {
    "PAY": "High performer with stable velocity. Control group; expected GREEN.",
    "ONB": "Scope creep: high mid-sprint additions and a falling say/do ratio.",
    "DAT": "Blocked by cross-squad and external dependencies; late milestone.",
    "MOB": "Volatile velocity, high carry-over and a defect spike in PI 2.",
    "IDS": "Risk-heavy: high-score risks without owner/mitigation; overdue actions.",
}

SCENARIOS: list[Scenario] = [
    Scenario("high_risk_no_mitigation", "IDS", ("RAID-R-031",),
             "Risk scored 20 (4x5) with no mitigation recorded."),
    Scenario("high_risk_no_owner", "IDS", ("RAID-R-032", "RAID-R-033"),
             "High-score risks (16, 15) with no owner."),
    Scenario("overdue_actions", "IDS", ("RAID-A-018", "RAID-A-019", "RAID-A-020"),
             "Open actions past their due date."),
    Scenario("overdue_action_escalation", "DAT", ("RAID-A-022",),
             "Overdue escalation action linked to DEP-012."),
    Scenario("stale_raid_item", "MOB", ("RAID-R-012",),
             "Open risk not updated since 2026-10-10 (>14 days)."),
    Scenario("ageing_issue", "DAT", ("RAID-I-005",),
             "Issue open since 2026-09-15, linked to DEP-012 and MS-07."),
    Scenario("risk_past_trigger", "ONB", ("RAID-R-020",),
             "Open risk whose trigger date (2026-11-05) has passed; should become an issue."),
    Scenario("duplicate_raid_candidates", "ONB", ("RAID-R-007", "RAID-R-027"),
             "Two near-identical KYC vendor rate-limit risks."),
    Scenario("late_critical_dependency", "DAT", ("DEP-012", "MS-07"),
             "Dependency needed 2026-10-29, still undelivered (21 days late), blocks MS-07."),
    Scenario("late_dependencies", None,
             ("DEP-005", "DEP-009", "DEP-012", "DEP-017", "DEP-021", "DEP-024", "DEP-028",
              "DEP-030"),
             "Exactly eight dependencies are past needed-by date and undelivered."),
    Scenario("at_risk_dependencies", None, ("DEP-014", "DEP-033"),
             "Committed date is after the needed-by date (not yet due)."),
    Scenario("late_milestone", "DAT", ("MS-07",),
             "Forecast 2026-12-24 vs baseline 2026-12-04: three weeks late."),
    Scenario("slipping_milestones", None, ("MS-06", "MS-09"),
             "Forecast one week after baseline."),
    Scenario("scope_creep", "ONB", (),
             "ONB added points in PI 2 sprints are far above other squads."),
    Scenario("velocity_volatility", "MOB", (),
             "MOB completed points swing sprint to sprint (highest coefficient of variation)."),
    Scenario("defect_spike", "MOB", (),
             "MOB defects in sprints 6-8 are much higher than in PI 1."),
    Scenario("low_pi1_objective_achievement", "DAT", (),
             "DAT PI 1 actual business value is well below plan."),
    Scenario("control_group_clean", "PAY", (),
             "PAY has no planted RAID hygiene defects and no late dependencies."),
]
