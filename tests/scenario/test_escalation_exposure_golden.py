"""T4 golden tests: the new rules on the full Project Meridian dataset."""

import pytest

from adm.config import load_config
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from adm.rules.dependencies import dependency_status
from adm.rules.escalation import escalation_candidates
from adm.rules.exposure import objective_exposures
from adm.rules.milestones import milestone_status
from adm.services.data import load_programme_data
from adm.synthetic.scenarios import AS_OF
from tests.conftest import read_files

TH = load_config().thresholds


@pytest.fixture(scope="module")
def rules_output(demo_dir):
    conn = connect(":memory:")
    init_db(conn)
    assert import_files(conn, read_files(demo_dir.glob("*.csv"))).status == "imported"
    data = load_programme_data(conn)
    deps = dependency_status(data.dependencies, AS_OF, TH)
    milestones = milestone_status(data.milestones, AS_OF, TH, deps)
    yield {
        "escalations": escalation_candidates(data.raid, deps, milestones, AS_OF, TH),
        "exposures": objective_exposures(deps, data.objectives, data.pis, AS_OF, TH),
    }
    conn.close()


def test_escalation_candidates_are_exactly_the_planted_ones(rules_output):
    got = {c.raid_id: c for c in rules_output["escalations"]}
    assert set(got) == {
        "RAID-R-031",   # score 20, no mitigation
        "RAID-R-032",   # score 16, no owner
        "RAID-R-033",   # score 15, no owner, no mitigation, linked to late DEP-028 (blocks MS-11)
        "RAID-R-020",   # score 16, linked to late DEP-021 (blocks MS-09)
        "RAID-I-005",   # impact 5, open 65 days, linked to DEP-012 and MS-07
        "RAID-A-018",   # overdue, linked to DEP-028
        "RAID-A-022",   # overdue escalation action, linked to DEP-012
    }
    assert got["RAID-I-005"].linked_critical == ("DEP-012", "MS-07")


def test_near_misses_are_not_escalated(rules_output):
    ids = {c.raid_id for c in rules_output["escalations"]}
    # R-012 links an AMBER milestone but scores 12; R-007/R-027 are duplicates scoring 12;
    # A-019/A-020 are overdue but not linked to a critical dependency or milestone.
    assert not ids & {"RAID-R-012", "RAID-R-007", "RAID-R-027", "RAID-A-019", "RAID-A-020"}


def test_control_squad_has_no_escalations_or_exposures(rules_output):
    assert not [c for c in rules_output["escalations"] if c.squad_id == "PAY"]
    assert not [e for e in rules_output["exposures"] if e.requesting_squad_id == "PAY"]


def test_dep_012_exposes_data_platform_committed_pi2_objectives(rules_output):
    e = next(x for x in rules_output["exposures"] if x.dependency_id == "DEP-012")
    assert e.state == "late" and e.days_late == 21 and e.blocks_milestone_id == "MS-07"
    assert e.pi_id == "PI-2"
    assert e.objective_ids == tuple(f"OBJ-PI2-DAT-{k}" for k in range(1, 5))  # not stretch #5


def test_every_late_or_at_risk_dependency_has_an_exposure(rules_output):
    assert {e.dependency_id for e in rules_output["exposures"]} == {
        "DEP-005", "DEP-009", "DEP-012", "DEP-017", "DEP-021", "DEP-024", "DEP-028",
        "DEP-030", "DEP-014", "DEP-033"}
    assert all(e.basis == "squad-level" for e in rules_output["exposures"])
