"""A4: the agent's report is checked against the evidence its tools returned."""

import json

import pytest

from adm.agent.loop import AgentLoop
from adm.agent.report import (
    ReportAnswer,
    ReportStatus,
    summarise,
    validate_report,
)
from adm.ai.grounding import Verdict
from adm.ai.providers import MockProvider
from adm.synthetic.scenarios import AS_OF

RULES = "1.3"
CALLS = ["get_programme_health", "get_late_dependencies", "get_escalation_candidates"]

GOOD = {
    "headline": "Data Platform and Mobile App need attention this week.",
    "findings": [
        {"section": "off_track",
         "statement": "Data Platform is RED because carry-over and late dependencies are RED.",
         "fact_ids": ["HEALTH:DAT"], "confidence": "high"},
        {"section": "risks_dependencies",
         "statement": "DEP-012 is 21 days late and blocks MS-07.",
         "fact_ids": ["DEPSTATE:DEP-012"], "confidence": "high"},
    ],
    "leadership_asks": [
        {"action": "Agree a recovery date for DEP-012 with the Identity & Security squad",
         "rationale": "DEP-012 is late and blocks MS-07.", "owner_role": "RTE",
         "urgency": "immediate", "fact_ids": ["DEPSTATE:DEP-012"], "finding_refs": [1]},
    ],
    "data_gaps": [],
}


def finished_run(toolbox, answer, calls=CALLS):
    script = [json.dumps({"action": "call_tool", "tool": t, "args": {}}) for t in calls]
    script.append(json.dumps({"action": "final", "answer": answer}))
    return AgentLoop(MockProvider(script=script), toolbox, "system").run("Weekly report")


def check(toolbox, answer, calls=CALLS):
    return validate_report(finished_run(toolbox, answer, calls), AS_OF, RULES)


def edited(**changes):
    return {**json.loads(json.dumps(GOOD)), **changes}


# --- a good report ------------------------------------------------------------------------

def test_a_well_cited_report_passes_every_check(toolbox):
    r = check(toolbox, GOOD)
    assert r.status is ReportStatus.VALID and r.ok, r.validation.report()
    assert r.headline_shown
    assert all(v.verdict is Verdict.VERIFIED for v in r.validation.items)


def test_findings_and_asks_are_grouped_by_section(toolbox):
    r = check(toolbox, GOOD)
    assert [v.item.statement[:13] for v in r.findings_in("off_track")] == ["Data Platform"]
    assert [v.item.statement[:7] for v in r.findings_in("risks_dependencies")] == ["DEP-012"]
    assert len(r.asks) == 1


def test_the_agent_can_cite_only_what_its_tools_returned(toolbox):
    # DEP-021 is a real late dependency, but this agent never asked for dependencies.
    answer = edited(findings=[{
        "section": "risks_dependencies", "confidence": "high",
        "statement": "DEP-021 is 17 days late and blocks MS-09.",
        "fact_ids": ["DEPSTATE:DEP-021"]}], leadership_asks=[])
    r = check(toolbox, answer, calls=["get_programme_health"])
    item = r.validation.findings[0]
    assert item.verdict is Verdict.REJECTED
    assert r.status is ReportStatus.PARTIALLY_REJECTED


# --- the checker catches bad reports ------------------------------------------------------

def test_invented_fact_id_is_rejected(toolbox):
    bad = edited()
    bad["findings"][1]["fact_ids"] = ["DEPSTATE:DEP-999"]
    r = check(toolbox, bad)
    assert r.validation.findings[1].verdict is Verdict.REJECTED
    assert r.status is ReportStatus.PARTIALLY_REJECTED


def test_a_wrong_number_is_flagged(toolbox):
    bad = edited()
    bad["findings"][1]["statement"] = "DEP-012 is 40 days late and blocks MS-07."
    item = check(toolbox, bad).validation.findings[1]
    assert item.verdict is Verdict.FLAGGED
    assert [i.code for i in item.issues] == ["unsupported_number"]


def test_the_agent_cannot_change_a_rag_status(toolbox):
    bad = edited()
    bad["findings"][0]["statement"] = "Data Platform should be downgraded to AMBER."
    item = check(toolbox, bad).validation.findings[0]
    assert item.verdict is Verdict.REJECTED


def test_an_uncited_finding_is_rejected(toolbox):
    bad = edited()
    bad["findings"][0]["fact_ids"] = []
    assert check(toolbox, bad).validation.findings[0].verdict is Verdict.REJECTED


def test_an_ask_not_backed_by_a_shown_finding_is_flagged(toolbox):
    bad = edited()
    bad["leadership_asks"][0]["finding_refs"] = []
    ask = check(toolbox, bad).asks[0]
    assert any(i.code == "no_supporting_finding" for i in ask.issues)


# --- unusable answers ---------------------------------------------------------------------

@pytest.mark.parametrize("answer,fragment", [
    ({"findings": []}, "headline"),
    (edited(findings=[{"section": "other", "statement": "Something long enough here.",
                       "fact_ids": ["HEALTH:DAT"], "confidence": "high"}]), "section"),
    (edited(rag="GREEN"), "rag"),
])
def test_answers_that_are_not_reports_are_invalid_in_report_terms(toolbox, answer, fragment):
    r = check(toolbox, answer)
    assert r.status is ReportStatus.INVALID and fragment in r.error
    assert r.validation is None


def test_report_answer_rejects_extra_fields():
    with pytest.raises(ValueError):
        ReportAnswer.model_validate({**GOOD, "overall_rag": "GREEN"})


def test_a_run_that_never_finished_has_no_report(toolbox):
    script = [json.dumps({"action": "call_tool", "tool": "get_programme_health", "args": {}})]
    run = AgentLoop(MockProvider(script=script), toolbox, "s").run("Weekly report")
    r = validate_report(run, AS_OF, RULES)
    assert r.status is ReportStatus.AGENT_INCOMPLETE and not r.ok
    assert "exhausted" in r.error and r.squad_table       # the table still shows what it has


# --- the squad table comes from rules, not from the AI ------------------------------------

def test_squad_table_is_built_from_rule_results(toolbox):
    r = check(toolbox, GOOD)
    assert [(x.squad_id, x.rag) for x in r.squad_table][:2] == [("DAT", "RED"), ("MOB", "RED")]
    assert {x.squad_id for x in r.squad_table} == {"DAT", "IDS", "MOB", "ONB", "PAY"}
    pay = next(x for x in r.squad_table if x.squad_id == "PAY")
    assert pay.rag == "GREEN" and pay.fact_id == "HEALTH:PAY"


def test_table_ignores_anything_the_ai_writes(toolbox):
    lie = edited(headline="Every squad is GREEN and all is well this week.")
    r = check(toolbox, lie)
    assert next(x for x in r.squad_table if x.squad_id == "DAT").rag == "RED"


def test_missing_health_means_no_table_and_a_note(toolbox):
    r = check(toolbox, edited(findings=[], leadership_asks=[]), calls=["get_milestone_status"])
    assert r.squad_table == []
    assert any("no squad status table" in n for n in r.coverage_notes)


def test_report_lists_the_tools_consulted_and_those_not(toolbox):
    r = check(toolbox, GOOD)
    assert r.tools_consulted == tuple(CALLS)
    assert any("get_milestone_status" in n for n in r.coverage_notes)


def test_summary_for_the_audit_log_is_json_safe(toolbox):
    s = summarise(check(toolbox, GOOD))
    json.dumps(s)
    assert s["status"] == "valid" and s["run_status"] == "completed" and s["tool_calls"] == 3
