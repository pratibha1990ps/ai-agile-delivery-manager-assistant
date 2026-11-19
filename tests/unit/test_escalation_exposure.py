"""T4 unit tests: escalation candidates and objective exposure on hand-built fixtures."""

from datetime import date

import pandas as pd

from adm.config import load_config
from adm.rules.escalation import critical_items, escalation_candidates
from adm.rules.exposure import objective_exposures
from adm.rules.models import DependencyStatus, MilestoneStatus
from adm.rules.pi import current_pi_id
from adm.rules.rag import RAG

TH = load_config().thresholds
AS_OF = date(2026, 11, 19)


def raid_row(rid, kind="RISK", p=4, i=4, owner="Ann", mitigation="Plan", status="Open",
             due=None, raised=date(2026, 11, 1), linked=None, squad="IDS"):
    return {"raid_id": rid, "type": kind, "squad_id": squad, "title": rid, "owner": owner,
            "probability": p, "impact": i, "status": status, "raised_date": raised,
            "due_date": due, "last_updated": date(2026, 11, 18), "mitigation": mitigation,
            "linked_ids": linked}


def dep(did, state="late", blocks=None, squad="DAT", days=10):
    return DependencyStatus(did, squad, "IDS", state, days if state == "late" else 0,
                            date(2026, 11, 1), None, blocks, "why")


def ms(mid, rag):
    return MilestoneStatus(mid, "DAT", mid, "late", rag, 20, (), "why")


DEPS = [dep("DEP-1", blocks="MS-1"), dep("DEP-2"), dep("DEP-3", state="on_track", blocks="MS-2")]
MILESTONES = [ms("MS-1", RAG.RED), ms("MS-2", RAG.GREEN), ms("MS-3", RAG.AMBER)]


def candidates(rows):
    return {c.raid_id: c for c in escalation_candidates(pd.DataFrame(rows), DEPS, MILESTONES,
                                                        AS_OF, TH)}


# --- critical items --------------------------------------------------------------------

def test_critical_items_are_blocking_deps_and_red_or_amber_milestones():
    assert critical_items(DEPS, MILESTONES, TH) == {"DEP-1", "MS-1", "MS-3"}
    strict = TH.model_copy(update={"escalation": TH.escalation.model_copy(
        update={"include_amber_milestones": False})})
    assert critical_items(DEPS, MILESTONES, strict) == {"DEP-1", "MS-1"}


# --- risks -----------------------------------------------------------------------------

def test_high_risk_needs_an_aggravating_condition():
    got = candidates([
        raid_row("R-clean"),                                  # score 16, owned, mitigated
        raid_row("R-noowner", owner=None),
        raid_row("R-nomit", mitigation=None),
        raid_row("R-linked", linked="DEP-1"),
        raid_row("R-linked-noncritical", linked="DEP-2;MS-2"),
    ])
    assert set(got) == {"R-noowner", "R-nomit", "R-linked"}
    assert "no owner" in got["R-noowner"].reasons
    assert got["R-linked"].linked_critical == ("DEP-1",)
    assert got["R-linked"].source_ids == ("R-linked", "DEP-1")


def test_risk_below_threshold_is_never_a_candidate():
    assert candidates([raid_row("R-low", p=3, i=4, owner=None, mitigation=None)]) == {}


def test_closed_items_are_ignored():
    assert candidates([raid_row("R-closed", owner=None, status="Closed")]) == {}


# --- issues and actions ----------------------------------------------------------------

def test_high_impact_issue_escalates_when_ageing_or_critically_linked():
    got = candidates([
        raid_row("I-old", kind="ISSUE", p=None, i=5, raised=date(2026, 9, 1)),
        raid_row("I-new", kind="ISSUE", p=None, i=5),
        raid_row("I-linked", kind="ISSUE", p=None, i=4, linked="MS-3"),
        raid_row("I-low", kind="ISSUE", p=None, i=3, raised=date(2026, 9, 1)),
    ])
    assert set(got) == {"I-old", "I-linked"}
    assert any(r.startswith("open 79 days") for r in got["I-old"].reasons)


def test_overdue_action_escalates_only_with_a_critical_link():
    got = candidates([
        raid_row("A-linked", kind="ACTION", p=None, i=None, due=date(2026, 11, 2),
                 linked="DEP-1"),
        raid_row("A-unlinked", kind="ACTION", p=None, i=None, due=date(2026, 11, 2)),
        raid_row("A-future", kind="ACTION", p=None, i=None, due=date(2026, 12, 1),
                 linked="DEP-1"),
    ])
    assert set(got) == {"A-linked"}
    assert got["A-linked"].reasons[0] == "overdue by 17 days"


def test_candidates_are_ordered_by_number_of_reasons_then_score():
    rows = [raid_row("R-one", owner=None, p=4, i=5),
            raid_row("R-three", owner=None, mitigation=None, linked="DEP-1", p=4, i=4)]
    order = [c.raid_id for c in escalation_candidates(pd.DataFrame(rows), DEPS, MILESTONES,
                                                      AS_OF, TH)]
    assert order == ["R-three", "R-one"]


# --- exposure --------------------------------------------------------------------------

PIS = pd.DataFrame([
    {"pi_id": "PI-1", "start_date": date(2026, 8, 10), "end_date": date(2026, 10, 18)},
    {"pi_id": "PI-2", "start_date": date(2026, 10, 19), "end_date": date(2026, 12, 27)},
])
OBJECTIVES = pd.DataFrame([
    {"objective_id": "O-DAT-1", "pi_id": "PI-2", "squad_id": "DAT", "committed": 1},
    {"objective_id": "O-DAT-2", "pi_id": "PI-2", "squad_id": "DAT", "committed": 1},
    {"objective_id": "O-DAT-S", "pi_id": "PI-2", "squad_id": "DAT", "committed": 0},
    {"objective_id": "O-DAT-OLD", "pi_id": "PI-1", "squad_id": "DAT", "committed": 1},
    {"objective_id": "O-PAY-1", "pi_id": "PI-2", "squad_id": "PAY", "committed": 1},
])


def test_current_pi_is_the_one_containing_as_of():
    assert current_pi_id(PIS, AS_OF) == "PI-2"
    assert current_pi_id(PIS, date(2027, 1, 1)) is None


def test_exposure_links_late_and_at_risk_deps_to_committed_current_pi_objectives():
    deps = [dep("DEP-1", blocks="MS-1", days=21), dep("DEP-2", state="at_risk"),
            dep("DEP-3", state="on_track"), dep("DEP-4", state="delivered")]
    got = {e.dependency_id: e for e in objective_exposures(deps, OBJECTIVES, PIS, AS_OF, TH)}
    assert set(got) == {"DEP-1", "DEP-2"}
    e = got["DEP-1"]
    assert e.objective_ids == ("O-DAT-1", "O-DAT-2")      # no stretch, no PI-1, no other squad
    assert e.pi_id == "PI-2" and e.basis == "squad-level"
    assert e.source_ids == ("DEP-1", "MS-1", "PI-2", "O-DAT-1", "O-DAT-2")
    assert "squad-level" in e.explanation


def test_exposure_lists_late_before_at_risk_and_most_late_first():
    deps = [dep("DEP-A", state="at_risk"), dep("DEP-B", days=5), dep("DEP-C", days=30)]
    order = [e.dependency_id for e in objective_exposures(deps, OBJECTIVES, PIS, AS_OF, TH)]
    assert order == ["DEP-C", "DEP-B", "DEP-A"]


def test_squad_with_no_objectives_still_reports_the_dependency():
    got = objective_exposures([dep("DEP-X", squad="MOB")], OBJECTIVES, PIS, AS_OF, TH)
    assert got[0].objective_ids == () and "no committed" in got[0].explanation
