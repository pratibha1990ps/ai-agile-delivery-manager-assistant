"""A2: the agent's read-only tools, on the full Project Meridian dataset."""

import json

import pytest

from adm.agent.tools import TOOLS, ToolBox, ToolError
from adm.ai.models import FactKind
from adm.config import load_config
from adm.db import repositories as repo
from adm.db.connection import connect, init_db
from adm.facts.fact_pack import compute_rule_bundle
from adm.ingestion.importer import import_files
from adm.services.data import load_programme_data
from adm.synthetic.scenarios import AS_OF
from tests.conftest import read_files

CFG = load_config()


@pytest.fixture(scope="module")
def env(demo_dir):
    conn = connect(":memory:")
    init_db(conn)
    assert import_files(conn, read_files(demo_dir.glob("*.csv"))).status == "imported"
    data = load_programme_data(conn)
    yield conn, ToolBox(data, compute_rule_bundle(data, AS_OF, CFG.thresholds))
    conn.close()


def box(env) -> ToolBox:
    return env[1]


# --- the tool list ------------------------------------------------------------------------

def test_the_toolbox_is_a_closed_list_of_six_read_only_tools(env):
    assert box(env).names == (
        "get_programme_health", "get_squad_metrics", "get_open_raid_items",
        "get_late_dependencies", "get_escalation_candidates", "get_milestone_status")
    assert len(TOOLS) == 6
    assert not any(n.startswith(("set_", "update_", "delete_", "send_", "create_"))
                   for n in box(env).names)


def test_toolbox_holds_no_database_connection(env):
    held = vars(box(env)).values()
    assert not any(type(v).__module__.startswith("sqlite3") for v in held)


def test_specs_are_valid_json_schemas_for_an_llm(env):
    specs = box(env).specs()
    json.dumps(specs)                                         # serialisable
    by_name = {s["name"]: s for s in specs}
    assert by_name["get_programme_health"]["input_schema"]["properties"] == {}
    metrics = by_name["get_squad_metrics"]["input_schema"]
    assert metrics["required"] == ["squad"]
    assert metrics["properties"]["squad"]["enum"] == ["DAT", "IDS", "MOB", "ONB", "PAY"]
    assert all(s["input_schema"]["additionalProperties"] is False for s in specs)


# --- what each tool returns ---------------------------------------------------------------

def test_programme_health_returns_one_fact_per_squad(env):
    r = box(env).call("get_programme_health")
    assert sorted(r.fact_ids) == [f"HEALTH:{s}" for s in ["DAT", "IDS", "MOB", "ONB", "PAY"]]
    assert r.scope == "programme" and r.omitted_count == 0


def test_squad_metrics_are_for_that_squad_only(env):
    r = box(env).call("get_squad_metrics", {"squad": "DAT"})
    assert r.scope == "DAT" and "HEALTH:DAT" in r.fact_ids
    assert any(i.startswith("METRIC:say_do_ratio:DAT") for i in r.fact_ids)
    assert all(f.squad_id in ("DAT", None) for f in r.facts)
    assert not any(i.endswith(":PAY") for i in r.fact_ids)


def test_escalation_candidates_match_the_golden_rule_result(env):
    ids = box(env).call("get_escalation_candidates").fact_ids
    for raid_id in ("R-031", "R-032", "R-033"):
        assert f"ESC:RAID-{raid_id}" in ids


def test_late_dependencies_include_the_planted_one_and_its_exposure(env):
    ids = box(env).call("get_late_dependencies").fact_ids
    assert "DEPSTATE:DEP-012" in ids and "EXPOSURE:DEP-012" in ids


def test_milestone_status_returns_milestone_facts(env):
    r = box(env).call("get_milestone_status")
    assert r.facts and all(i.startswith("MSSTATE:") for i in r.fact_ids)


def test_open_raid_items_for_a_squad_stay_in_that_squad(env):
    r = box(env).call("get_open_raid_items", {"squad": "IDS"})
    assert r.facts
    assert all(f.squad_id in ("IDS", None) for f in r.facts)


# --- traceability and bounds --------------------------------------------------------------

@pytest.mark.parametrize("name", [t.name for t in TOOLS])
def test_every_source_id_returned_exists_in_the_database(env, name):
    args = {"squad": "DAT"} if name == "get_squad_metrics" else {}
    result = box(env).call(name, args)
    missing = [s for f in result.facts for s in f.source_ids
               if repo.find_record(env[0], s) is None]
    assert not missing, missing


def test_facts_are_rule_results_records_metrics_or_aggregates_never_context(env):
    kinds = set()
    for t in TOOLS:
        args = {"squad": "DAT"} if t.squad == "required" else {}
        kinds |= {f.kind for f in box(env).call(t.name, args).facts}
    assert FactKind.CONTEXT not in kinds


def test_each_call_is_capped_and_reports_what_was_left_out(env):
    small = ToolBox(box(env)._data, box(env)._bundle, max_facts_per_call=3)
    full = box(env).call("get_open_raid_items")
    r = small.call("get_open_raid_items")
    assert len(r.facts) == 3
    assert r.omitted_count == len(full.facts) + full.omitted_count - 3


def test_same_call_gives_the_same_result(env):
    a = box(env).call("get_late_dependencies").to_payload()
    b = box(env).call("get_late_dependencies").to_payload()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_payload_is_json_safe(env):
    payload = box(env).call("get_escalation_candidates").to_payload()
    assert payload["fact_count"] == len(payload["facts"])
    json.dumps(payload)


# --- bad calls become messages the agent can read -----------------------------------------

@pytest.mark.parametrize("name,args,fragment", [
    ("delete_everything", {}, "Unknown tool"),
    ("get_squad_metrics", {}, "needs a squad"),
    ("get_squad_metrics", {"squad": "XYZ"}, "Unknown squad"),
    ("get_squad_metrics", {"squad": "programme"}, "Unknown squad"),
    ("get_open_raid_items", {"squad": "XYZ"}, "Unknown squad"),
    ("get_programme_health", {"squad": "DAT"}, "takes no arguments"),
    ("get_open_raid_items", {"limit": 5}, "Invalid arguments"),
    ("get_open_raid_items", {"squad": 5}, "Invalid arguments"),
])
def test_bad_calls_raise_a_plain_tool_error(env, name, args, fragment):
    with pytest.raises(ToolError, match=fragment):
        box(env).call(name, args)
