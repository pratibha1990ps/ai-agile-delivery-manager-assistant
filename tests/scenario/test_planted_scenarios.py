"""Golden tests: the rules engine must find every planted scenario, and nothing else."""

from collections import defaultdict
from datetime import date

import pytest

from adm.config import load_config
from adm.db import repositories as repo
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from adm.rules.rag import RAG
from adm.services.control_tower import build_health_report, save_snapshot
from adm.services.data import load_programme_data
from adm.synthetic.scenarios import AS_OF, SCENARIOS
from tests.conftest import read_files

CFG = load_config()
EXPECTED = {s.key: set(s.record_ids) for s in SCENARIOS}


@pytest.fixture(scope="module")
def loaded(demo_dir):
    conn = connect(":memory:")
    init_db(conn)
    result = import_files(conn, read_files(demo_dir.glob("*.csv")))
    assert result.status == "imported"
    report = build_health_report(load_programme_data(conn), AS_OF, CFG.thresholds)
    yield conn, report
    conn.close()


@pytest.fixture(scope="module")
def report(loaded):
    return loaded[1]


@pytest.fixture(scope="module")
def by_rule(report):
    out = defaultdict(set)
    for f in report.findings:
        out[f.rule].add(f.record_ids if len(f.record_ids) > 1 else f.record_ids[0])
    return out


def test_config_as_of_matches_scenarios():
    assert CFG.settings.app.as_of_date == AS_OF == date(2026, 11, 19)


def test_raid_hygiene_findings_are_exact(by_rule):
    assert by_rule["high_risk_no_mitigation"] == {"RAID-R-031", "RAID-R-033"}
    assert by_rule["missing_owner"] == EXPECTED["high_risk_no_owner"]
    assert by_rule["overdue_action"] == (EXPECTED["overdue_actions"]
                                         | EXPECTED["overdue_action_escalation"])
    assert by_rule["stale_item"] == EXPECTED["stale_raid_item"]
    assert by_rule["risk_past_trigger"] == EXPECTED["risk_past_trigger"]
    assert by_rule["ageing_issue"] == EXPECTED["ageing_issue"]
    assert by_rule["duplicate_candidate"] == {("RAID-R-007", "RAID-R-027")}
    assert set(by_rule) == {"high_risk_no_mitigation", "missing_owner", "overdue_action",
                            "stale_item", "risk_past_trigger", "ageing_issue",
                            "duplicate_candidate"}


def test_dependencies(report):
    late = {d.dependency_id for d in report.dependencies if d.state == "late"}
    at_risk = {d.dependency_id for d in report.dependencies if d.state == "at_risk"}
    assert late == EXPECTED["late_dependencies"]
    assert at_risk == EXPECTED["at_risk_dependencies"]
    dep12 = next(d for d in report.dependencies if d.dependency_id == "DEP-012")
    assert dep12.days_late == 21 and dep12.is_critical and dep12.blocks_milestone_id == "MS-07"


def test_milestones(report):
    ms = {m.milestone_id: m for m in report.milestones}
    assert ms["MS-07"].rag is RAG.RED and ms["MS-07"].slip_days == 20
    assert "DEP-012" in ms["MS-07"].blocking_dependencies
    assert {k for k, m in ms.items() if m.rag is RAG.AMBER} == EXPECTED["slipping_milestones"]
    assert {k for k, m in ms.items() if m.rag is RAG.RED} == {"MS-07"}


def test_squad_health_outcomes(report):
    rag = {s.squad_id: s.rag for s in report.squads}
    assert rag["PAY"] is RAG.GREEN
    assert rag["DAT"] is RAG.RED
    assert all(rag[s] is not RAG.GREEN for s in ("ONB", "MOB", "IDS"))
    assert report.programme_rag is RAG.RED


def test_control_group_is_green_on_every_metric(report):
    pay = report.squad("PAY")
    assert {k: m.rag for k, m in pay.metrics.items()} == dict.fromkeys(pay.metrics, RAG.GREEN)


def test_personality_metrics(report):
    def m(squad, metric):
        return report.squad(squad).metrics[metric]
    assert m("ONB", "scope_change_pct").rag is RAG.RED
    cvs = {s.squad_id: s.metrics["velocity_cv"].value for s in report.squads}
    assert max(cvs, key=cvs.get) == "MOB" and m("MOB", "velocity_cv").rag is RAG.RED
    pi = {s.squad_id: s.metrics["pi_objective_achievement"].value for s in report.squads}
    assert min(pi, key=pi.get) == "DAT"
    assert m("IDS", "raid_hygiene_findings").rag is RAG.RED
    assert m("DAT", "late_dependencies").value == 4


def test_every_metric_is_traceable_to_existing_records(loaded):
    conn, report = loaded
    for s in report.squads:
        for name, metric in s.metrics.items():
            if name in ("raid_hygiene_findings", "late_dependencies") and metric.value == 0:
                continue
            assert metric.source_ids, f"{s.squad_id}.{name} has no sources"
            for rid in metric.source_ids:
                assert repo.find_record(conn, rid), f"{rid} cited by {name} does not exist"


def test_snapshot_is_idempotent(loaded):
    conn, report = loaded
    n = save_snapshot(conn, report)
    save_snapshot(conn, report)
    rows = conn.execute("SELECT COUNT(*) FROM health_snapshot").fetchone()[0]
    assert rows == n == 5 * 8 + 1
    pay = conn.execute("SELECT rag, rule_version, source_ids FROM health_snapshot WHERE "
                       "scope_id='PAY' AND metric='composite_health'").fetchone()
    assert pay["rag"] == "GREEN" and pay["rule_version"] == CFG.thresholds.version
    assert "SC-S07-PAY" in pay["source_ids"]
