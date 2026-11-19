"""T5: Fact Pack builder on the full Project Meridian dataset."""

import pytest

from adm.ai.models import AnalysisRequest, FactKind, Focus
from adm.config import load_config
from adm.db import repositories as repo
from adm.db.connection import connect, init_db
from adm.facts.fact_pack import FOCUS_FAMILIES, build_fact_pack, clean, compute_rule_bundle
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
    yield conn, data, compute_rule_bundle(data, AS_OF, CFG.thresholds)
    conn.close()


def pack(env, scope="programme", focus=Focus.ESCALATION, max_facts=80):
    _, data, bundle = env
    return build_fact_pack(data, bundle, AnalysisRequest(scope=scope, focus=focus), max_facts)


# --- traceability ------------------------------------------------------------------------

@pytest.mark.parametrize("focus", list(Focus))
@pytest.mark.parametrize("scope", ["programme", "DAT", "PAY"])
def test_every_source_id_exists_in_the_database(env, scope, focus):
    conn = env[0]
    p = pack(env, scope, focus, max_facts=500)
    missing = [s for s in p.source_ids if repo.find_record(conn, s) is None]
    assert not missing, f"untraceable sources: {missing[:5]}"


def test_record_facts_are_the_records_themselves(env):
    conn = env[0]
    for f in pack(env, focus=Focus.DEPENDENCIES_VS_OBJECTIVES, max_facts=500).facts:
        if f.kind is FactKind.RECORD:
            assert f.source_ids == (f.fact_id,)
            assert repo.find_record(conn, f.fact_id) is not None


def test_pack_carries_request_rule_version_and_as_of(env):
    p = pack(env, "DAT", Focus.RTE_ACTIONS)
    assert p.request.scope == "DAT" and p.rule_version == CFG.thresholds.version
    assert p.as_of == AS_OF and p.use_case == "raid_analyst"
    ctx = p.by_id["CTX:programme"].data
    assert ctx["current_pi"] == "PI-2" and ctx["current_sprint"] == "S08"


# --- relevance: scope and focus ------------------------------------------------------------

@pytest.mark.parametrize("focus", list(Focus))
def test_squad_scope_excludes_other_squads(env, focus):
    p = pack(env, "DAT", focus, max_facts=500)
    others = [f.fact_id for f in p.facts if f.squad_id not in (None, "DAT")
              and not f.fact_id.startswith("DEPSTATE:") and f.kind is not FactKind.RECORD]
    assert not others, others
    # Dependencies may involve another squad only when DAT is requester or provider.
    for f in p.facts:
        if f.fact_id.startswith("DEPSTATE:"):
            assert "DAT" in (f.squad_id, f.data["provider"])


def test_programme_level_raid_is_left_out_of_squad_scope(env):
    p = pack(env, "IDS", Focus.IMMEDIATE_ATTENTION, max_facts=500)
    assert all(f.squad_id is not None for f in p.facts if f.kind is not FactKind.CONTEXT)


def test_escalation_focus_leads_with_all_escalation_candidates(env):
    p = pack(env, focus=Focus.ESCALATION)
    esc = [f.fact_id for f in p.facts if f.fact_id.startswith("ESC:")]
    assert set(esc) == {"ESC:RAID-R-031", "ESC:RAID-R-032", "ESC:RAID-R-033", "ESC:RAID-R-020",
                        "ESC:RAID-I-005", "ESC:RAID-A-018", "ESC:RAID-A-022"}
    assert [f.fact_id for f in p.facts][1].startswith("ESC:")   # right after context


def test_dependency_focus_includes_exposures_and_objectives(env):
    p = pack(env, focus=Focus.DEPENDENCIES_VS_OBJECTIVES, max_facts=500)
    exp = p.by_id["EXPOSURE:DEP-012"]
    assert exp.data["basis"] == "squad-level" and exp.data["exposed_objective_count"] == 4
    assert "OBJ-PI2-DAT-1" in p.fact_ids and p.by_id["OBJ-PI2-DAT-1"].data[
        "exposed_by_dependency"] is True
    assert not any(f.fact_id.startswith("ESC:") for f in p.facts)   # not this question


def test_focus_only_retrieves_its_fact_families(env):
    p = pack(env, focus=Focus.CROSS_SQUAD_PATTERNS, max_facts=500)
    assert not any(f.fact_id.startswith(("EXPOSURE:", "MSSTATE:")) for f in p.facts)
    assert {f"AGG:open_findings:{s}" for s in ("PAY", "ONB", "DAT", "MOB", "IDS")} <= p.fact_ids


def test_control_squad_pack_has_no_problem_facts(env):
    p = pack(env, "PAY", Focus.IMMEDIATE_ATTENTION, max_facts=500)
    assert not [f for f in p.facts if f.kind is FactKind.RULE_RESULT]
    assert all(f.data["count"] == 0 for f in p.facts if f.kind is FactKind.AGGREGATE)


def test_unknown_scope_is_rejected(env):
    with pytest.raises(ValueError, match="Unknown scope"):
        pack(env, "XYZ")


def test_every_focus_has_a_family_table():
    assert set(FOCUS_FAMILIES) == set(Focus)


# --- size cap ----------------------------------------------------------------------------

def test_cap_keeps_highest_priority_and_counts_the_rest(env):
    full = pack(env, focus=Focus.IMMEDIATE_ATTENTION, max_facts=500)
    capped = pack(env, focus=Focus.IMMEDIATE_ATTENTION, max_facts=20)
    assert len(capped.facts) == 20
    assert capped.omitted_count == len(full.facts) - 20
    assert full.omitted_count == 0
    assert "CTX:programme" in capped.fact_ids
    assert any(f.startswith("ESC:") for f in capped.fact_ids)        # escalations rank first


def test_healthy_metrics_rank_below_problems(env):
    p = pack(env, focus=Focus.IMMEDIATE_ATTENTION, max_facts=40)
    kept_rags = {f.data["rag"] for f in p.facts if f.fact_id.startswith("METRIC:")}
    assert "GREEN" not in kept_rags


# --- stability -----------------------------------------------------------------------------

def test_hash_is_stable_across_builds(env):
    assert pack(env).hash == pack(env).hash


def test_hash_changes_with_scope_focus_or_note(env):
    _, data, bundle = env
    base = pack(env).hash
    assert pack(env, "DAT").hash != base
    assert pack(env, focus=Focus.RTE_ACTIONS).hash != base
    noted = build_fact_pack(data, bundle, AnalysisRequest(focus=Focus.ESCALATION,
                                                          note="Steering committee Friday"))
    assert noted.hash != base


def test_facts_contain_only_json_safe_values(env):
    import json
    json.loads(pack(env, focus=Focus.RTE_ACTIONS).canonical_json())


def test_clean_normalises_values():
    import datetime

    import numpy as np
    assert clean(datetime.date(2026, 11, 19)) == "2026-11-19"
    assert clean(float("nan")) is None and clean(4.0) == 4 and clean(np.int64(3)) == 3
    assert clean([np.float64(2.5), None]) == [2.5, None]
