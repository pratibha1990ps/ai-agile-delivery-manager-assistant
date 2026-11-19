"""Unit tests for each rule on small hand-built fixtures."""

from datetime import date

import pandas as pd
import pytest

from adm.config import load_config
from adm.rules.dependencies import dependency_status
from adm.rules.health import programme_health, squad_health
from adm.rules.milestones import milestone_status
from adm.rules.models import MetricResult, MilestoneStatus
from adm.rules.pi import objective_achievement
from adm.rules.rag import RAG
from adm.rules.raid import raid_findings, similarity
from adm.rules.sprint import closed_sprint_ids, current_sprint_id, sprint_metrics

TH = load_config().thresholds
AS_OF = date(2026, 11, 19)


SPRINTS = pd.DataFrame([
    {"sprint_id": sid, "number": n, "start_date": s, "end_date": e}
    for n, (sid, s, e) in enumerate([
        ("S1", date(2026, 10, 5), date(2026, 10, 18)),
        ("S2", date(2026, 10, 19), date(2026, 11, 1)),
        ("S3", date(2026, 11, 2), date(2026, 11, 15)),
        ("S4", date(2026, 11, 16), date(2026, 11, 29)),     # in flight on AS_OF
    ], start=1)
])


def commitments(values, squad="X"):
    """values: list of (committed, completed, added, removed, carried) for S1.."""
    return pd.DataFrame([{
        "commitment_id": f"SC-S{i}-{squad}", "sprint_id": f"S{i}", "squad_id": squad,
        "committed_points": c, "completed_points": d, "added_points": a,
        "removed_points": r, "carried_over_points": co,
    } for i, (c, d, a, r, co) in enumerate(values, start=1)])


# --- sprint ---------------------------------------------------------------------------

def test_closed_and_current_sprints():
    assert closed_sprint_ids(SPRINTS, AS_OF) == ["S1", "S2", "S3"]
    assert current_sprint_id(SPRINTS, AS_OF) == "S4"


def test_say_do_excludes_in_flight_sprint_and_cites_sources():
    c = commitments([(40, 40, 0, 0, 0), (40, 36, 2, 0, 4), (40, 25, 2, 0, 15), (40, 1, 0, 0, 0)])
    m = sprint_metrics(c, SPRINTS, "X", AS_OF, TH)["say_do_ratio"]
    assert m.value == pytest.approx(101 / 120, abs=1e-4)   # S4 (in flight) excluded
    assert m.rag is RAG.AMBER
    assert m.source_ids == ("SC-S1-X", "SC-S2-X", "SC-S3-X")
    assert "101 vs 120" in m.explanation


def test_say_do_caps_each_sprint_at_its_commitment():
    # S1 over-delivers (45 of 40): counts as 40, so it cannot mask S2's shortfall.
    c = commitments([(40, 45, 8, 0, 0), (40, 30, 0, 0, 10), (40, 40, 0, 0, 0)])
    m = sprint_metrics(c, SPRINTS, "X", AS_OF, TH)
    assert m["say_do_ratio"].value == pytest.approx(110 / 120, abs=1e-4)
    assert "capped" in m["say_do_ratio"].explanation
    assert m["velocity_cv"].value is not None   # velocity keeps the uncapped 45


def test_say_do_never_exceeds_100_percent():
    c = commitments([(40, 48, 10, 0, 0)] * 3)
    assert sprint_metrics(c, SPRINTS, "X", AS_OF, TH)["say_do_ratio"].value == 1.0


def test_sprint_series_say_do_is_capped():
    from adm.rules.sprint import sprint_series
    c = commitments([(40, 48, 10, 0, 0), (40, 30, 0, 0, 10)])
    s = sprint_series(c, SPRINTS).set_index("sprint_id")["say_do_ratio"]
    assert s["S1"] == 1.0 and s["S2"] == pytest.approx(0.75)


def test_say_do_boundary_is_green():
    c = commitments([(40, 34, 0, 0, 6)] * 3)                  # exactly 85%
    assert sprint_metrics(c, SPRINTS, "X", AS_OF, TH)["say_do_ratio"].rag is RAG.GREEN


def test_scope_change_and_carry_over():
    c = commitments([(40, 40, 4, 0, 0), (40, 40, 4, 4, 0), (40, 40, 0, 0, 12)])
    m = sprint_metrics(c, SPRINTS, "X", AS_OF, TH)
    assert m["scope_change_pct"].value == pytest.approx(12 / 120, abs=1e-4)
    assert m["scope_change_pct"].rag is RAG.GREEN       # boundary 0.10 is green
    assert m["carry_over_pct"].value == pytest.approx(0.1, abs=1e-4)


def test_zero_commitment_gives_no_rag():
    c = commitments([(0, 0, 0, 0, 0)] * 3)
    m = sprint_metrics(c, SPRINTS, "X", AS_OF, TH)["say_do_ratio"]
    assert m.value is None and m.rag is None


def test_velocity_needs_three_sprints():
    c = commitments([(40, 40, 0, 0, 0), (40, 40, 0, 0, 0)])
    assert sprint_metrics(c, SPRINTS, "X", AS_OF, TH)["velocity_cv"].rag is None


def test_velocity_volatility():
    c = commitments([(40, 50, 0, 0, 0), (40, 20, 0, 0, 0), (40, 50, 0, 0, 0)])
    m = sprint_metrics(c, SPRINTS, "X", AS_OF, TH)["velocity_cv"]
    assert m.rag is RAG.RED and m.value > 0.3


# --- PI -------------------------------------------------------------------------------

PIS = pd.DataFrame([
    {"pi_id": "PI-1", "start_date": date(2026, 8, 1), "end_date": date(2026, 10, 18)},
    {"pi_id": "PI-2", "start_date": date(2026, 10, 19), "end_date": date(2026, 12, 27)},
])


def test_pi_achievement_counts_stretch_in_actual_only():
    obj = pd.DataFrame([
        {"objective_id": "O1", "pi_id": "PI-1", "squad_id": "X", "committed": 1,
         "planned_bv": 10, "actual_bv": 6.0},
        {"objective_id": "O2", "pi_id": "PI-1", "squad_id": "X", "committed": 0,
         "planned_bv": 5, "actual_bv": 2.0},
        {"objective_id": "O3", "pi_id": "PI-2", "squad_id": "X", "committed": 1,
         "planned_bv": 9, "actual_bv": None},
    ])
    m = objective_achievement(obj, PIS, "X", AS_OF, TH)
    assert m.value == pytest.approx(0.8)
    assert m.rag is RAG.GREEN
    assert m.source_ids == ("O1", "O2")


def test_pi_achievement_none_before_first_pi_closes():
    m = objective_achievement(pd.DataFrame(columns=["objective_id", "pi_id", "squad_id"]),
                              PIS, "X", date(2026, 9, 1), TH)
    assert m.rag is None


# --- RAID -----------------------------------------------------------------------------

def raid_row(**kw):
    base = dict(raid_id="R1", type="RISK", squad_id="X", title="Some risk", owner="A",
                probability=2, impact=2, status="Open", raised_date=date(2026, 11, 1),
                due_date=date(2026, 12, 1), last_updated=date(2026, 11, 15), mitigation="m")
    return {**base, **kw}


def rules_for(*rows):
    return sorted((f.rule, f.record_ids) for f in raid_findings(pd.DataFrame(rows), AS_OF, TH))


def test_clean_item_has_no_findings():
    assert rules_for(raid_row()) == []


def test_closed_items_are_ignored():
    assert rules_for(raid_row(owner=None, status="Closed")) == []


@pytest.mark.parametrize("override,rule", [
    (dict(probability=5, impact=3, mitigation=None), "high_risk_no_mitigation"),
    (dict(owner=None), "missing_owner"),
    (dict(type="ACTION", due_date=date(2026, 11, 18)), "overdue_action"),
    (dict(due_date=date(2026, 11, 18)), "risk_past_trigger"),
    (dict(last_updated=date(2026, 11, 4)), "stale_item"),
    (dict(type="ISSUE", raised_date=date(2026, 10, 1), probability=None), "ageing_issue"),
])
def test_each_hygiene_rule(override, rule):
    assert rules_for(raid_row(**override)) == [(rule, ("R1",))]


def test_boundaries_are_not_flagged():
    row = raid_row(probability=2, impact=7, mitigation=None,     # score 14 < 15
                   last_updated=date(2026, 11, 5))               # exactly 14 days
    assert rules_for(row) == []


def test_duplicate_detection_same_squad_and_type_only():
    a = raid_row(raid_id="R1", title="Third-party KYC vendor API rate limits")
    b = raid_row(raid_id="R2", title="KYC vendor API rate limiting under load")
    c = raid_row(raid_id="R3", title="KYC vendor API rate limiting under load", squad_id="Y")
    assert rules_for(a, b, c) == [("duplicate_candidate", ("R1", "R2"))]


def test_similarity_is_stemmed_and_ignores_stopwords():
    assert similarity("Rate limits", "rate limiting") == 1.0
    assert similarity("the vendor", "a vendor") == 1.0
    assert similarity("", "x") == 0.0


# --- dependencies ---------------------------------------------------------------------

def dep(**kw):
    base = dict(dependency_id="D1", requesting_squad_id="A", providing_squad_id="B",
                external_party=None, needed_by_date=date(2026, 12, 10), committed_date=None,
                delivered_date=None, status="Requested", blocks_milestone_id=None)
    return {**base, **kw}


def states(*rows):
    return [(d.state, d.days_late) for d in dependency_status(pd.DataFrame(rows), AS_OF, TH)]


def test_dependency_states():
    assert states(
        dep(needed_by_date=date(2026, 10, 29), status="Committed",
            committed_date=date(2026, 11, 26)),
        dep(committed_date=date(2026, 12, 12), status="Committed"),
        dep(needed_by_date=date(2026, 11, 25)),                    # unconfirmed, within 14d
        dep(needed_by_date=date(2027, 1, 30)),                     # far away, unconfirmed ok
        dep(status="Delivered", delivered_date=date(2026, 11, 1),
            needed_by_date=date(2026, 11, 2)),
    ) == [("late", 21), ("at_risk", 0), ("at_risk", 0), ("on_track", 0), ("delivered", 0)]


def test_due_today_is_not_late():
    assert states(dep(needed_by_date=AS_OF, committed_date=AS_OF, status="Committed")) == [
        ("on_track", 0)]


def test_critical_flag_and_external_provider():
    [d] = dependency_status(pd.DataFrame([dep(
        providing_squad_id=None, external_party="Vendor", needed_by_date=date(2026, 11, 1),
        blocks_milestone_id="MS-1")]), AS_OF, TH)
    assert d.is_critical and d.provider == "External: Vendor"


# --- milestones -----------------------------------------------------------------------

def ms(mid, base, fc, actual=None):
    return {"milestone_id": mid, "squad_id": "X", "name": mid, "baseline_date": base,
            "forecast_date": fc, "actual_date": actual,
            "status": "Complete" if actual else "Planned"}


def test_milestone_states():
    rows = pd.DataFrame([
        ms("M1", date(2026, 12, 1), date(2026, 12, 1)),
        ms("M2", date(2026, 12, 1), date(2026, 12, 2)),
        ms("M3", date(2026, 12, 1), date(2026, 12, 16)),
        ms("M4", date(2026, 11, 1), date(2026, 11, 10)),
        ms("M5", date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 5)),
    ])
    out = {m.milestone_id: (m.state, m.rag, m.slip_days)
           for m in milestone_status(rows, AS_OF, TH)}
    assert out == {
        "M1": ("on_track", RAG.GREEN, 0), "M2": ("at_risk", RAG.AMBER, 1),
        "M3": ("late", RAG.RED, 15), "M4": ("overdue", RAG.RED, 9),
        "M5": ("complete", None, 4),
    }


# --- composite health ---------------------------------------------------------------------

def metric(name, rag):
    return MetricResult(name, "X", 1.0, rag, ("S",), "")


def all_metrics(rag):
    return {k: metric(k, rag) for k in TH.health_weights}


def test_composite_score_bands():
    assert squad_health("X", "X", all_metrics(RAG.GREEN), [], TH).rag is RAG.GREEN
    assert squad_health("X", "X", all_metrics(RAG.AMBER), [], TH).rag is RAG.AMBER
    assert squad_health("X", "X", all_metrics(RAG.RED), [], TH).rag is RAG.RED


def test_missing_metrics_are_renormalised():
    m = all_metrics(RAG.GREEN)
    m["velocity_cv"] = MetricResult("velocity_cv", "X", None, None, (), "")
    h = squad_health("X", "X", m, [], TH)
    assert h.score == 1.0 and h.rag is RAG.GREEN


def test_red_milestone_overrides_squad_to_red():
    red_ms = MilestoneStatus("M", "X", "m", "late", RAG.RED, 20, (), "")
    h = squad_health("X", "X", all_metrics(RAG.GREEN), [red_ms], TH)
    assert h.rag is RAG.RED and any("Overridden" in r for r in h.reasons)


def test_programme_rules():
    def sq(rag):
        return squad_health("X", "X", all_metrics(rag), [], TH)
    assert programme_health([sq(RAG.GREEN)] * 3, [])[0] is RAG.GREEN
    assert programme_health([sq(RAG.GREEN), sq(RAG.AMBER)], [])[0] is RAG.AMBER
    assert programme_health([sq(RAG.RED), sq(RAG.GREEN)], [])[0] is RAG.AMBER
    assert programme_health([sq(RAG.RED), sq(RAG.RED)], [])[0] is RAG.RED
    red_ms = MilestoneStatus("M", None, "m", "late", RAG.RED, 20, (), "")
    assert programme_health([sq(RAG.GREEN)], [red_ms])[0] is RAG.RED
