"""T7: grounding validator. Hand-built cases for each rule, then the mock's faults end to end."""

import json
from datetime import date

import pytest

from adm.ai.grounding import Status, Verdict, numbers_in_text, validate
from adm.ai.models import AnalysisRequest, Fact, FactKind, FactPack, Focus, LLMRequest
from adm.ai.providers import MockProvider

F = FactKind
PACK = FactPack(
    use_case="raid_analyst", request=AnalysisRequest(focus=Focus.IMMEDIATE_ATTENTION),
    as_of=date(2026, 11, 19), rule_version="1.3",
    facts=(
        Fact(fact_id="CTX:programme", kind=F.CONTEXT, source_ids=("MER",),
             data={"programme": "Project Meridian"}),
        Fact(fact_id="RULE:overdue_action:RAID-A-018", kind=F.RULE_RESULT,
             source_ids=("RAID-A-018",), squad_id="IDS",
             data={"rule": "overdue_action", "severity": "high",
                   "message": "RAID-A-018 was due 2026-11-02 (17 days overdue)."}),
        Fact(fact_id="ESC:RAID-R-031", kind=F.RULE_RESULT, source_ids=("RAID-R-031",),
             squad_id="IDS", data={"rule": "escalation_candidate", "risk_score": 20}),
        Fact(fact_id="METRIC:say_do_ratio:DAT", kind=F.METRIC,
             source_ids=("SC-S05-DAT", "SC-S06-DAT", "SC-S07-DAT"), squad_id="DAT",
             data={"metric": "say_do_ratio", "value": 0.714, "rag": "AMBER"}),
        Fact(fact_id="MSSTATE:MS-07", kind=F.RULE_RESULT, source_ids=("MS-07", "DEP-012"),
             squad_id="DAT", data={"rag": "RED", "slip_days": 20}),
        Fact(fact_id="AGG:overdue_actions:IDS", kind=F.AGGREGATE,
             source_ids=("RAID-A-018", "RAID-A-019", "RAID-A-020"), squad_id="IDS",
             data={"label": "overdue actions for IDS", "count": 3}),
        Fact(fact_id="RAID-R-031", kind=F.RECORD, source_ids=("RAID-R-031",), squad_id="IDS",
             data={"title": "Legacy IAM certificates expire", "impact": 5}),
    ),
)


def finding(statement, fact_ids, confidence="high"):
    return {"statement": statement, "fact_ids": fact_ids, "confidence": confidence}


def rec(action, fact_ids, refs=(0,), rationale="Supported by the rule results.",
        owner="RTE"):
    return {"action": action, "rationale": rationale, "owner_role": owner,
            "urgency": "immediate", "fact_ids": fact_ids, "finding_refs": list(refs)}


def answer(findings=None, recommendations=None, summary="Identity & Security needs attention."):
    return json.dumps({"summary": summary, "findings": findings or [],
                       "recommendations": recommendations or [], "data_gaps": []})


def one(statement, fact_ids):
    return validate(answer([finding(statement, fact_ids)]), PACK).findings[0]


def codes(item):
    return {i.code for i in item.issues}


# --- the happy path ----------------------------------------------------------------------

def test_fully_grounded_answer_is_valid():
    r = validate(answer(
        [finding("RAID-A-018 is 17 days overdue (due 2026-11-02).",
                 ["RULE:overdue_action:RAID-A-018"]),
         finding("MS-07 is RED with a 20 day slip, blocked by DEP-012.", ["MSSTATE:MS-07"])],
        [rec("Agree a new due date for RAID-A-018", ["RULE:overdue_action:RAID-A-018"])],
    ), PACK)
    assert r.status is Status.VALID
    assert r.counts() == {"verified": 3, "flagged": 0, "rejected": 0}
    assert r.summary_shown


# --- whole-answer failures ---------------------------------------------------------------

def test_invalid_json_is_rejected_whole():
    r = validate('{"summary": "cut off', PACK)
    assert r.status is Status.INVALID and "not valid JSON" in r.error and r.response is None


def test_schema_mismatch_is_rejected_whole():
    assert validate(json.dumps({"findings": []}), PACK).status is Status.INVALID
    bad = json.loads(answer())
    bad["rag"] = "RED"
    r = validate(json.dumps(bad), PACK)
    assert r.status is Status.INVALID and "rag" in r.error


def test_markdown_wrapper_is_accepted_with_a_warning():
    r = validate("```json\n" + answer() + "\n```", PACK)
    assert r.status is Status.VALID_WITH_WARNINGS
    assert r.response_issues[0].code == "markdown_wrapper"


# --- citations and IDs -------------------------------------------------------------------

def test_uncited_finding_is_rejected():
    item = one("Identity & Security has too many overdue actions.", [])
    assert item.verdict is Verdict.REJECTED and "no_citation" in codes(item)


def test_invented_fact_id_is_rejected():
    r = validate(answer([finding("RAID-R-031 is unmitigated.", ["ESC:RAID-R-031", "ESC:X"])]),
                 PACK)
    assert r.findings[0].verdict is Verdict.REJECTED
    assert "unknown_fact_id" in codes(r.findings[0])
    assert r.status is Status.PARTIALLY_REJECTED
    assert [c.valid for c in r.findings[0].citations] == [True, False]


def test_invented_record_id_in_text_is_rejected():
    item = one("RAID-R-031 and RAID-R-999 share a cause.", ["ESC:RAID-R-031"])
    assert item.verdict is Verdict.REJECTED and "unknown_id_in_text" in codes(item)


def test_source_of_a_cited_fact_may_be_mentioned():
    item = one("RAID-A-018 drives the overdue count for IDS.", ["AGG:overdue_actions:IDS"])
    assert item.verdict is Verdict.VERIFIED


def test_known_but_uncited_id_is_flagged():
    item = one("RAID-R-031 is linked to RAID-A-019.", ["ESC:RAID-R-031"])
    assert item.verdict is Verdict.FLAGGED and "uncited_id_in_text" in codes(item)


def test_squad_codes_are_not_treated_as_record_ids():
    assert one("IDS and DAT both need support.", ["ESC:RAID-R-031"]).verdict is Verdict.VERIFIED


# --- numbers and dates -------------------------------------------------------------------

@pytest.mark.parametrize("statement,fact,ok", [
    ("RAID-A-018 is 17 days overdue.", "RULE:overdue_action:RAID-A-018", True),
    ("RAID-A-018 is 47 days overdue.", "RULE:overdue_action:RAID-A-018", False),
    ("Data Platform say/do is 71%.", "METRIC:say_do_ratio:DAT", True),
    ("Data Platform say/do is 85%.", "METRIC:say_do_ratio:DAT", False),
    ("There are 3 overdue actions in IDS.", "AGG:overdue_actions:IDS", True),
    ("RAID-A-018 was due 2026-11-02.", "RULE:overdue_action:RAID-A-018", True),
    ("RAID-A-018 was due 2026-11-09.", "RULE:overdue_action:RAID-A-018", False),
    ("RAID-R-031 scores 20.", "ESC:RAID-R-031", True),
])
def test_numbers_and_dates_must_match_cited_facts(statement, fact, ok):
    item = one(statement, [fact])
    assert (item.verdict is Verdict.VERIFIED) is ok
    if not ok:
        assert item.verdict is Verdict.FLAGGED      # flagged, never rejected
        assert codes(item) & {"unsupported_number", "unsupported_date"}


def test_numbers_must_come_from_the_cited_fact_not_elsewhere_in_the_pack():
    # 20 exists in the pack (MS-07 slip, R-031 score) but not in the cited overdue fact.
    assert one("RAID-A-018 has a 20 point problem.",
               ["RULE:overdue_action:RAID-A-018"]).verdict is Verdict.FLAGGED


def test_ids_and_dates_are_not_mistaken_for_numbers():
    assert numbers_in_text("RAID-R-031 due 2026-11-02 in S08 with DEP-012") == []
    assert numbers_in_text("71% of 120 points") == [(71.0, True), (120.0, False)]


# --- RAG statuses ------------------------------------------------------------------------

def test_repeating_a_rule_status_is_allowed():
    assert one("MS-07 is RED.", ["MSSTATE:MS-07"]).verdict is Verdict.VERIFIED


def test_stating_a_status_no_cited_fact_gives_is_rejected():
    item = one("Data Platform is RED on say/do.", ["METRIC:say_do_ratio:DAT"])  # rule: AMBER
    assert item.verdict is Verdict.REJECTED and "rag_unsupported" in codes(item)


@pytest.mark.parametrize("statement", [
    "Data Platform should be downgraded to RED.",
    "Mark Identity & Security as AMBER.",
    "MS-07 should be RED given the slip.",
])
def test_assigning_or_changing_a_status_is_rejected(statement):
    item = one(statement, ["MSSTATE:MS-07", "METRIC:say_do_ratio:DAT"])
    assert item.verdict is Verdict.REJECTED and "rag_override" in codes(item)


def test_change_words_far_from_a_status_are_not_an_override():
    item = one("MS-07 is RED, so the release train engineer should be informed today.",
               ["MSSTATE:MS-07"])
    assert item.verdict is Verdict.VERIFIED


def test_words_containing_red_are_not_statuses():
    assert one("RAID-R-031 was recorded and ordered by score.",
               ["ESC:RAID-R-031"]).verdict is Verdict.VERIFIED


# --- recommendations ----------------------------------------------------------------------

def test_recommendation_linked_to_a_rejected_finding_is_flagged():
    r = validate(answer([finding("Uncited claim about IDS.", [])],
                        [rec("Assign an owner to RAID-R-031", ["ESC:RAID-R-031"])]), PACK)
    assert r.recommendations[0].verdict is Verdict.FLAGGED
    assert "no_supporting_finding" in codes(r.recommendations[0])


def test_recommendation_with_a_bad_finding_reference_is_flagged():
    r = validate(answer([finding("RAID-R-031 scores 20.", ["ESC:RAID-R-031"])],
                        [rec("Assign an owner to RAID-R-031", ["ESC:RAID-R-031"],
                             refs=(0, 5))]), PACK)
    assert "bad_finding_ref" in codes(r.recommendations[0])


def test_recommendation_backed_only_by_context_is_flagged():
    r = validate(answer([finding("RAID-R-031 scores 20.", ["ESC:RAID-R-031"])],
                        [rec("Hold a programme review", ["CTX:programme"])]), PACK)
    assert "context_only_evidence" in codes(r.recommendations[0])


def test_uncited_recommendation_is_rejected():
    r = validate(answer([finding("RAID-R-031 scores 20.", ["ESC:RAID-R-031"])],
                        [rec("Restructure the squads", [])]), PACK)
    assert r.recommendations[0].verdict is Verdict.REJECTED


# --- summary -------------------------------------------------------------------------------

def test_summary_with_an_invented_id_is_hidden():
    r = validate(answer(summary="RAID-R-777 is the biggest risk."), PACK)
    assert not r.summary_shown and r.status is Status.PARTIALLY_REJECTED


def test_summary_numbers_are_checked_against_the_whole_pack():
    assert validate(answer(summary="Three items, one 20 day slip."), PACK).status is Status.VALID
    assert validate(answer(summary="Nine of 99 items are late."),
                    PACK).status is Status.VALID_WITH_WARNINGS


def test_report_is_json_serialisable():
    r = validate(answer([finding("RAID-R-031 scores 47.", ["ESC:RAID-R-031"])]), PACK)
    data = json.loads(json.dumps(r.report()))
    assert data["counts"]["flagged"] == 1 and data["items"][0]["issues"][0]["severity"] == "flag"


# --- end to end with the mock provider on the real dataset ----------------------------------

@pytest.fixture(scope="module")
def real_pack(demo_dir):
    from adm.config import load_config
    from adm.db.connection import connect, init_db
    from adm.facts.fact_pack import build_fact_pack, compute_rule_bundle
    from adm.ingestion.importer import import_files
    from adm.services.data import load_programme_data
    from adm.synthetic.scenarios import AS_OF
    from tests.conftest import read_files

    conn = connect(":memory:")
    init_db(conn)
    import_files(conn, read_files(demo_dir.glob("*.csv")))
    data = load_programme_data(conn)
    bundle = compute_rule_bundle(data, AS_OF, load_config().thresholds)
    yield build_fact_pack(data, bundle, AnalysisRequest(focus=Focus.IMMEDIATE_ATTENTION))
    conn.close()


def run_mock(pack, fault=None):
    text = MockProvider(fault=fault).complete(LLMRequest(system="s", user="u",
                                                         fact_pack=pack)).text
    return validate(text, pack)


@pytest.mark.parametrize("focus", list(Focus))
def test_grounded_mock_answers_pass_cleanly_for_every_focus(real_pack, focus):
    pack = real_pack.model_copy(update={"request": AnalysisRequest(focus=focus)})
    r = run_mock(pack)
    assert r.status is Status.VALID, r.report()


@pytest.mark.parametrize("fault,status,code", [
    ("invented_id", Status.PARTIALLY_REJECTED, "unknown_fact_id"),
    ("wrong_number", Status.VALID_WITH_WARNINGS, "unsupported_number"),
    ("rag_override", Status.PARTIALLY_REJECTED, "rag_override"),
    ("uncited_claim", Status.PARTIALLY_REJECTED, "no_citation"),
])
def test_every_mock_fault_is_caught(real_pack, fault, status, code):
    r = run_mock(real_pack, fault)
    assert r.status is status
    assert code in codes(r.findings[0])
    assert all(v.verdict is Verdict.VERIFIED for v in r.findings[1:])   # damage is contained


def test_malformed_mock_answer_is_invalid(real_pack):
    assert run_mock(real_pack, "malformed_json").status is Status.INVALID
