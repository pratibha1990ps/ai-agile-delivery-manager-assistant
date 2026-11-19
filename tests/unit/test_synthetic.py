from collections import defaultdict
from statistics import mean, pstdev

from adm.ingestion.schemas import ENTITIES, ENTITY_BY_NAME
from adm.synthetic.generate import CURRENT_SPRINT, generate_dataset, sprint_id, to_frames
from adm.synthetic.scenarios import AS_OF, SCENARIOS


def by_id(dataset, entity):
    spec = ENTITY_BY_NAME[entity]
    return {r[spec.id_field]: r for r in dataset[entity]}


def test_generation_is_deterministic(dataset):
    again = to_frames(generate_dataset())
    for name, df in to_frames(dataset).items():
        assert df.equals(again[name]), name


def test_different_seed_changes_data(dataset):
    assert generate_dataset(seed=7)["work_item"] != dataset["work_item"]


def test_volumes(dataset):
    assert len(dataset["squad"]) == 5
    assert len(dataset["sprint"]) == 10
    assert len(dataset["feature"]) == 40
    assert len(dataset["pi_objective"]) == 50
    assert len(dataset["milestone"]) == 12
    assert len(dataset["dependency"]) == 35
    assert len(dataset["raid_item"]) == 90
    assert 600 <= len(dataset["work_item"]) <= 700


def test_entity_streams_are_independent():
    """Re-seeding one entity's stream must not change any other entity."""
    from adm.synthetic import generate as g

    base = generate_dataset()
    original = g.entity_rng
    try:
        g.entity_rng = lambda seed, entity: original(
            seed + (1 if entity == "raid_item" else 0), entity)
        changed = generate_dataset()
    finally:
        g.entity_rng = original
    assert changed["raid_item"] != base["raid_item"]
    for name in ("feature", "work_item", "pi_objective", "dependency"):
        assert changed[name] == base[name], name


def test_no_active_work_on_not_started_features(dataset):
    status = {f["feature_id"]: f["status"] for f in dataset["feature"]}
    current = {sprint_id(n) for n in range(1, CURRENT_SPRINT + 1)}
    for it in dataset["work_item"]:
        if status[it["feature_id"]] == "Not Started":
            assert it["sprint_id"] not in current, it["work_item_id"]
            assert it["status"] == "To Do", it["work_item_id"]


def test_done_features_have_no_unfinished_work_at_pi_end(dataset):
    status = {f["feature_id"]: f["status"] for f in dataset["feature"]}
    pi_last_sprints = {"S05"}   # only PI 1 has closed
    for it in dataset["work_item"]:
        if it["sprint_id"] in pi_last_sprints and status[it["feature_id"]] == "Done":
            assert it["status"] not in ("Not Done", "In Progress", "To Do"), it["work_item_id"]


def test_work_items_link_to_features_of_same_squad_and_pi(dataset):
    feats = {f["feature_id"]: f for f in dataset["feature"]}
    sprints = {s["sprint_id"]: s for s in dataset["sprint"]}
    s05_spillover_ok = {"F-PAY-05"}   # Payments' PI 1 carry-over moves to its PI 2 lead feature
    for it in dataset["work_item"]:
        f = feats[it["feature_id"]]
        assert f["squad_id"] == it["squad_id"], it["work_item_id"]
        if f["feature_id"] not in s05_spillover_ok:
            assert f["pi_id"] == sprints[it["sprint_id"]]["pi_id"], it["work_item_id"]


def test_future_sprints_are_planned_backlog_only(dataset):
    future = {sprint_id(n) for n in range(CURRENT_SPRINT + 1, 11)}
    future_items = [i for i in dataset["work_item"] if i["sprint_id"] in future]
    assert len(future_items) >= 50
    assert all(i["status"] == "To Do" and i["done_date"] is None for i in future_items)
    assert all(i["created_date"] <= AS_OF for i in future_items)
    assert not [c for c in dataset["sprint_squad_commitment"] if c["sprint_id"] in future]


def test_every_raid_item_has_a_description(dataset):
    for r in dataset["raid_item"]:
        assert r["description"] and len(r["description"]) > 40, r["raid_id"]


def test_every_row_passes_its_schema(dataset):
    for spec in ENTITIES:
        for row in dataset[spec.name]:
            spec.model.model_validate(row)


def test_as_of_date_is_in_current_sprint(dataset):
    s = by_id(dataset, "sprint")[sprint_id(CURRENT_SPRINT)]
    assert s["start_date"] <= AS_OF <= s["end_date"]


def test_commitments_reconcile_with_work_items(dataset):
    items = defaultdict(list)
    for it in dataset["work_item"]:
        items[(it["sprint_id"], it["squad_id"])].append(it)
    for c in dataset["sprint_squad_commitment"]:
        rows = items[(c["sprint_id"], c["squad_id"])]
        done = sum(i["story_points"] for i in rows if i["status"] == "Done")
        removed = sum(i["story_points"] for i in rows if i["status"] == "Removed")
        not_done = sum(i["story_points"] for i in rows if i["status"] == "Not Done")
        assert done == c["completed_points"], c["commitment_id"]
        assert removed == c["removed_points"], c["commitment_id"]
        assert not_done == c["carried_over_points"], c["commitment_id"]


def test_no_future_done_dates(dataset):
    for it in dataset["work_item"]:
        if it["done_date"]:
            assert it["done_date"] <= AS_OF


def test_planted_records_exist(dataset):
    known = set()
    for spec in ENTITIES:
        known |= set(by_id(dataset, spec.name))
    for sc in SCENARIOS:
        for rid in sc.record_ids:
            assert rid in known, f"{sc.key}: {rid} missing"


def test_planted_raid_attributes(dataset):
    raid = by_id(dataset, "raid_item")
    r31 = raid["RAID-R-031"]
    assert r31["probability"] * r31["impact"] == 20 and r31["mitigation"] is None
    assert raid["RAID-R-032"]["owner"] is None and raid["RAID-R-033"]["owner"] is None
    for aid in ("RAID-A-018", "RAID-A-019", "RAID-A-020", "RAID-A-022"):
        assert raid[aid]["due_date"] < AS_OF and raid[aid]["status"] != "Closed"
    assert (AS_OF - raid["RAID-R-012"]["last_updated"]).days > 14
    assert raid["RAID-R-020"]["due_date"] < AS_OF


def test_generic_raid_items_are_clean(dataset):
    """Only planted items may break hygiene rules, so rules can be tested precisely."""
    planted = {rid for sc in SCENARIOS for rid in sc.record_ids if rid.startswith("RAID-")}
    for r in dataset["raid_item"]:
        if r["raid_id"] in planted:
            continue
        assert r["owner"], r["raid_id"]
        assert (AS_OF - r["last_updated"]).days <= 14, r["raid_id"]
        if r["status"] != "Closed" and r["due_date"]:
            assert r["due_date"] > AS_OF, r["raid_id"]
        if r["type"] == "RISK":
            assert r["mitigation"], r["raid_id"]


def test_exactly_eight_late_dependencies(dataset):
    late = sorted(d["dependency_id"] for d in dataset["dependency"]
                  if d["needed_by_date"] < AS_OF and d["status"] != "Delivered")
    expected = next(s for s in SCENARIOS if s.key == "late_dependencies").record_ids
    assert late == sorted(expected)


def test_pay_has_no_late_or_at_risk_dependencies(dataset):
    for d in dataset["dependency"]:
        if d["requesting_squad_id"] == "PAY" and d["status"] != "Delivered":
            assert d["needed_by_date"] >= AS_OF
            assert d["committed_date"] is None or d["committed_date"] <= d["needed_by_date"]


def _closed_metric(dataset, squad, field, sprints):
    rows = [c for c in dataset["sprint_squad_commitment"]
            if c["squad_id"] == squad and c["sprint_id"] in sprints]
    return [c[field] for c in rows]


def test_squad_personalities(dataset):
    closed = {sprint_id(n) for n in range(1, CURRENT_SPRINT)}
    pi2_closed = {sprint_id(n) for n in range(6, CURRENT_SPRINT)}

    # ONB scope creep: added points well above the control squad.
    assert mean(_closed_metric(dataset, "ONB", "added_points", pi2_closed)) > 3 * max(
        1, mean(_closed_metric(dataset, "PAY", "added_points", pi2_closed)))

    # MOB has the most volatile completed points (coefficient of variation).
    def cv(sq):
        v = _closed_metric(dataset, sq, "completed_points", closed)
        return pstdev(v) / mean(v)
    cvs = {sq: cv(sq) for sq in ("PAY", "ONB", "DAT", "MOB", "IDS")}
    assert max(cvs, key=cvs.get) == "MOB"
    assert min(cvs, key=cvs.get) in ("PAY", "ONB")

    # MOB defect spike in PI 2.
    def defects(sprints):
        return sum(1 for i in dataset["work_item"]
                   if i["squad_id"] == "MOB" and i["type"] == "Defect"
                   and i["sprint_id"] in sprints)
    assert defects({"S06", "S07", "S08"}) > 2 * defects({"S01", "S02", "S03"})

    # DAT PI 1 objectives under-delivered versus PAY.
    def achievement(sq):
        objs = [o for o in dataset["pi_objective"] if o["squad_id"] == sq and o["pi_id"] == "PI-1"]
        return sum(o["actual_bv"] for o in objs) / sum(o["planned_bv"] for o in objs)
    assert achievement("DAT") < 0.65 < 0.9 < achievement("PAY")
