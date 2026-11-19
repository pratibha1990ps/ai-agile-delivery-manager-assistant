from datetime import date

import pandas as pd
import pytest
from pydantic import ValidationError

from adm.ingestion.loaders import LoadedFile, load_file
from adm.ingestion.schemas import DependencyRow, RaidItemRow, SprintRow, WorkItemRow
from adm.ingestion.validator import validate

NO_IDS: dict[str, set[str]] = {}


def raid(**kw):
    base = dict(raid_id="RAID-R-1", type="RISK", title="t", probability="2", impact="3",
                status="Open", raised_date="2026-11-01", last_updated="2026-11-02")
    return {**base, **kw}


def lf(entity: str, rows: list[dict], name: str = "f.csv") -> LoadedFile:
    df = pd.DataFrame(rows, dtype=object).fillna("").astype(str)
    return LoadedFile(file_name=name, file_hash="x", frames={entity: df})


# --- row models -------------------------------------------------------------------------

def test_blank_strings_become_none():
    r = RaidItemRow.model_validate(raid(owner="  ", mitigation=""))
    assert r.owner is None and r.mitigation is None


def test_sprint_end_before_start_rejected():
    with pytest.raises(ValidationError, match="end date is before start date"):
        SprintRow.model_validate(dict(sprint_id="S1", pi_id="P", name="n", number=1,
                                      start_date="2026-01-10", end_date="2026-01-01"))


@pytest.mark.parametrize("override,msg", [
    (dict(probability=""), "RISK requires probability and impact"),
    (dict(probability="6"), "less than or equal to 5"),
    (dict(type="ACTION", due_date=""), "ACTION requires due_date"),
    (dict(type="RISKY"), "Input should be"),
    (dict(last_updated="2026-10-01"), "last_updated is before raised_date"),
])
def test_raid_rules(override, msg):
    with pytest.raises(ValidationError, match=msg):
        RaidItemRow.model_validate(raid(**override))


def test_work_item_done_requires_done_date():
    with pytest.raises(ValidationError, match="done_date is missing"):
        WorkItemRow.model_validate(dict(work_item_id="X-1", squad_id="PAY", type="Story",
                                        title="t", story_points="3", status="Done",
                                        created_date="2026-11-01"))


def test_dependency_needs_provider_and_not_self():
    base = dict(dependency_id="D", requesting_squad_id="PAY", description="d",
                needed_by_date="2026-12-01", status="Requested")
    with pytest.raises(ValidationError, match="providing_squad_id or external_party"):
        DependencyRow.model_validate(base)
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        DependencyRow.model_validate({**base, "providing_squad_id": "PAY"})


def test_linked_id_list_parsing():
    assert RaidItemRow.model_validate(raid(linked_ids=" DEP-1 ; MS-2;")).linked_id_list() == [
        "DEP-1", "MS-2"]


# --- loader -----------------------------------------------------------------------------

def test_csv_with_unknown_entity_name_is_reported():
    f = load_file("risks.csv", b"a,b\n1,2\n")
    assert f.problems and "Unknown entity" in f.problems[0]


def test_unsupported_extension_is_reported():
    assert "Unsupported file type" in load_file("x.json", b"{}").problems[0]


def test_csv_headers_normalised():
    f = load_file("squad.csv", b" Squad_ID ,Name\nPAY,Payments\n")
    assert list(f.frames["squad"].columns) == ["squad_id", "name"]


# --- validator --------------------------------------------------------------------------

def test_missing_required_column_is_file_level_error():
    res = validate([lf("raid_item", [{"raid_id": "R1"}])], NO_IDS)
    assert any(i.row is None and "Missing required column" in i.message for i in res.errors)


def test_row_numbers_are_one_based_with_header():
    rows = [raid(raid_id="R1"), raid(raid_id="R2", probability="9")]
    res = validate([lf("raid_item", rows)], NO_IDS)
    assert [(i.row, i.field) for i in res.errors] == [(3, "probability")]
    assert [v.source_row for v in res.rows["raid_item"]] == [2]


def test_duplicate_ids_rejected():
    res = validate([lf("raid_item", [raid(), raid()])], NO_IDS)
    assert any("Duplicate ID" in i.message for i in res.errors)
    assert len(res.rows["raid_item"]) == 1


def test_unknown_foreign_key_rejected_but_known_accepted():
    rows = [raid(raid_id="R1", squad_id="PAY"), raid(raid_id="R2", squad_id="ZZZ")]
    res = validate([lf("raid_item", rows)], {"squad": {"PAY"}})
    assert [i.message for i in res.errors] == ["Unknown squad 'ZZZ'"]
    assert [v.record.raid_id for v in res.rows["raid_item"]] == ["R1"]


def test_children_checked_against_parents_in_same_import():
    squad = lf("squad", [dict(squad_id="NEW", programme_id="MER", name="n", team_size="5",
                              capacity_points_per_sprint="20")], "squad.csv")
    child = lf("raid_item", [raid(squad_id="NEW")], "raid_item.csv")
    res = validate([child, squad], {"programme": {"MER"}})
    assert not res.errors


def test_rejected_parent_cascades_to_children():
    squad = lf("squad", [dict(squad_id="NEW", programme_id="NOPE", name="n", team_size="5",
                              capacity_points_per_sprint="20")], "squad.csv")
    child = lf("raid_item", [raid(squad_id="NEW")], "raid_item.csv")
    res = validate([squad, child], {"programme": {"MER"}})
    assert {i.entity for i in res.errors} == {"squad", "raid_item"}


def test_unknown_linked_id_is_warning_only():
    res = validate([lf("raid_item", [raid(linked_ids="DEP-999")])], NO_IDS)
    assert not res.errors
    assert res.warnings[0].field == "linked_ids"


def test_dates_from_excel_cells_parse():
    r = RaidItemRow.model_validate(raid(raised_date=str(date(2026, 11, 1))))
    assert r.raised_date == date(2026, 11, 1)
