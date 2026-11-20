"""A6: end-to-end weekly report: rules -> tools -> agent loop -> checks -> audit -> review."""

import json

import pytest

from adm.agent.demo import FAULTS, DemoAgentProvider, tool_results_in
from adm.agent.loop import RunStatus
from adm.agent.report import ReportStatus
from adm.ai.audit import (
    ReviewError,
    citations_for,
    get_output,
    record_review,
    review_complete,
    reviewable_recommendations,
)
from adm.ai.grounding import Verdict
from adm.ai.models import LLMResponse
from adm.ai.providers import ProviderError
from adm.config import load_config
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from adm.services.weekly_report import (
    ReportError,
    WeeklyReportAgent,
    create_report_agent,
)
from tests.conftest import read_files

CFG = load_config()


@pytest.fixture
def conn(demo_dir):
    c = connect(":memory:")
    init_db(c)
    assert import_files(c, read_files(demo_dir.glob("*.csv"))).status == "imported"
    yield c
    c.close()


def run_demo(conn, fault=None, note=""):
    return WeeklyReportAgent(conn, DemoAgentProvider(fault=fault), CFG).run(note)


# --- the normal run -----------------------------------------------------------------------

def test_demo_agent_produces_a_fully_verified_report(conn):
    result = run_demo(conn)
    report = result.report
    assert result.status is ReportStatus.VALID, report.validation.report()
    assert result.run.status is RunStatus.COMPLETED and result.run.tool_calls_used == 3
    assert report.tools_consulted == ("get_programme_health", "get_late_dependencies",
                                      "get_escalation_candidates")
    assert all(v.verdict is Verdict.VERIFIED for v in report.validation.items)
    assert report.findings_in("off_track") and report.findings_in("risks_dependencies")
    assert report.asks


def test_the_report_covers_the_planted_scenarios(conn):
    report = run_demo(conn).report
    text = " ".join(v.item.statement for v in report.validation.findings)
    for expected in ("Data Platform is RED", "DEP-012", "RAID-R-031"):
        assert expected in text
    assert [(r.squad_id, r.rag) for r in report.squad_table][:2] == [("DAT", "RED"),
                                                                    ("MOB", "RED")]
    assert next(r for r in report.squad_table if r.squad_id == "PAY").rag == "GREEN"


def test_the_demo_agent_is_repeatable(conn):
    a, b = run_demo(conn), run_demo(conn)
    assert a.report.pack.hash == b.report.pack.hash
    assert a.run.final_text == b.run.final_text


# --- the audit trail ----------------------------------------------------------------------

def test_the_run_is_audited_with_its_trace(conn):
    result = run_demo(conn, note="Board pack")
    row = get_output(conn, result.ai_output_id)
    assert row["use_case"] == "weekly_report" and row["prompt_version"] == "weekly_report@v1"
    assert row["validation_status"] == "valid" and row["provider"] == "mock"
    assert row["fact_pack_hash"] == result.report.pack.hash
    agent = row["parsed"]["agent"]
    assert agent["tool_calls_used"] == 3 and agent["run_status"] == "completed"
    assert [s["kind"] for s in agent["steps"]] == ["tool_call"] * 3 + ["final"]
    assert agent["squad_table"][0]["squad_id"] == "DAT"
    assert len(agent["finding_sections"]) == len(row["parsed"]["response"]["findings"])
    assert not citations_for(conn, result.ai_output_id).empty


def test_audit_records_the_prompt_fingerprint(conn):
    meta = get_output(conn, run_demo(conn).ai_output_id)["parsed"]["meta"]
    assert len(meta["prompt_sha256"]) == 64 and meta["fact_count"] > 0


# --- guardrails visible end to end --------------------------------------------------------

@pytest.mark.parametrize("fault,verdict", [
    ("invented_id", Verdict.REJECTED), ("rag_override", Verdict.REJECTED),
    ("uncited_claim", Verdict.REJECTED), ("wrong_number", Verdict.FLAGGED)])
def test_each_fault_is_caught_by_the_checker(conn, fault, verdict):
    result = run_demo(conn, fault=fault)
    last = result.report.validation.findings[-1]            # the faulty finding is appended
    assert last.verdict is verdict, last.issues
    others = result.report.validation.findings[:-1]
    assert all(v.verdict is Verdict.VERIFIED for v in others)
    expected = (ReportStatus.PARTIALLY_REJECTED if verdict is Verdict.REJECTED
                else ReportStatus.VALID_WITH_WARNINGS)
    assert result.status is expected
    assert get_output(conn, result.ai_output_id)["validation_status"] == expected.value


def test_an_agent_that_never_stops_is_cut_off_at_eight_tool_calls(conn):
    result = run_demo(conn, fault="never_stops")
    assert result.run.status is RunStatus.TOOL_LIMIT and result.run.tool_calls_used == 8
    assert result.status is ReportStatus.AGENT_INCOMPLETE and result.report.squad_table
    row = get_output(conn, result.ai_output_id)
    assert row["validation_status"] == "agent_incomplete"
    assert row["parsed"]["agent"]["run_status"] == "tool_limit"
    assert "more than 8" in row["parsed"]["error"]


def test_all_listed_faults_are_covered_by_the_tests_above():
    assert set(FAULTS) == {"invented_id", "rag_override", "uncited_claim", "wrong_number",
                           "never_stops"}


def test_provider_failure_is_audited_not_raised(conn):
    class Down:
        name, model = "down", "d-1"

        def complete(self, request):
            raise ProviderError("Could not reach the model server.")

    result = WeeklyReportAgent(conn, Down(), CFG).run()
    assert result.status is ReportStatus.AGENT_INCOMPLETE
    assert result.run.status is RunStatus.PROVIDER_ERROR
    assert get_output(conn, result.ai_output_id)["parsed"]["error"] == (
        "Could not reach the model server.")


def test_an_echoed_api_key_is_redacted_in_the_audit(conn, monkeypatch):
    key = "sk-test-ECHOED-KEY-12345"
    monkeypatch.setenv("ADM_TEST_KEY", key)
    cfg = CFG.model_copy(update={"llm": CFG.llm.model_copy(update={"api_key_env":
                                                                   "ADM_TEST_KEY"})})

    class Leaky:
        name, model = "leaky", "l-1"

        def complete(self, request):
            return LLMResponse(text=json.dumps({"action": "final", "answer": {
                "headline": f"my key is {key}"}}), provider="leaky", model="l-1")

    result = WeeklyReportAgent(conn, Leaky(), cfg).run()
    row = get_output(conn, result.ai_output_id)
    assert key not in row["raw_response"] and key not in row["parsed_json"]


def test_the_agent_only_sees_tool_results_through_the_prompt(conn):
    provider = DemoAgentProvider()
    WeeklyReportAgent(conn, provider, CFG).run()
    last = provider.calls[-1].user
    assert set(tool_results_in(last)) == {
        'get_programme_health:{}', 'get_late_dependencies:{}', 'get_escalation_candidates:{}'}
    assert "Tool calls left: 5" in last


# --- input problems stop before any AI call -----------------------------------------------

def test_note_too_long_is_refused_without_calling_ai(conn):
    provider = DemoAgentProvider()
    with pytest.raises(ReportError, match="limit is"):
        WeeklyReportAgent(conn, provider, CFG).run("x" * (CFG.settings.ai.max_note_chars + 1))
    assert not provider.calls
    assert conn.execute("SELECT COUNT(*) FROM ai_output").fetchone()[0] == 0


def test_empty_database_is_refused():
    c = connect(":memory:")
    init_db(c)
    with pytest.raises(ReportError, match="No programme data"):
        WeeklyReportAgent(c, DemoAgentProvider(), CFG).run()


def test_default_provider_is_the_free_demo_agent(conn):
    agent = create_report_agent(conn, CFG)
    assert isinstance(agent.provider, DemoAgentProvider)
    assert isinstance(create_report_agent(conn, CFG, "rag_override").provider,
                      DemoAgentProvider)


# --- human review reuses the RAID Analyst workflow ----------------------------------------

def test_human_review_of_the_asks_completes_the_report(conn):
    result = run_demo(conn)
    needed = reviewable_recommendations(conn, result.ai_output_id)
    assert needed and not review_complete(conn, result.ai_output_id)
    for index in needed:
        record_review(conn, result.ai_output_id, "recommendation", index, "approved",
                      "Priya Nair", "ok")
    assert review_complete(conn, result.ai_output_id)
    row = get_output(conn, result.ai_output_id)
    assert row["approved_by"] == "Priya Nair" and row["approved_at"]


def test_a_rejected_ask_cannot_be_approved(conn):
    result = run_demo(conn, fault="invented_id")
    assert all(i < len(result.report.asks) for i in
               reviewable_recommendations(conn, result.ai_output_id))
    with pytest.raises(ReviewError, match="name"):
        record_review(conn, result.ai_output_id, "recommendation", 0, "approved", " ")


def test_an_incomplete_run_has_nothing_to_review(conn):
    result = run_demo(conn, fault="never_stops")
    assert reviewable_recommendations(conn, result.ai_output_id) == []
    with pytest.raises(ReviewError, match="no reviewable items"):
        record_review(conn, result.ai_output_id, "recommendation", 0, "approved", "Priya")
    assert result.report.asks == [] and result.status is ReportStatus.AGENT_INCOMPLETE
    assert not any(v.verdict is Verdict.VERIFIED for v in result.report.asks)
