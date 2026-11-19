import pytest

from adm.db import repositories as repo
from adm.db.connection import reset_db
from adm.ingestion.importer import import_files
from tests.conftest import read_files


@pytest.fixture
def demo_files(demo_dir):
    return read_files(demo_dir.glob("*.csv"))


def test_demo_csvs_import_cleanly(db, demo_files, dataset):
    result = import_files(db, demo_files)
    assert result.status == "imported", result.errors
    assert not result.errors
    counts = repo.table_counts(db)
    for entity, rows in dataset.items():
        assert counts[entity] == len(rows), entity


def test_demo_workbook_imports_same_counts(db, demo_dir, dataset):
    result = import_files(db, read_files([demo_dir / "programme_data.xlsx"]))
    assert result.status == "imported", result.errors
    assert repo.table_counts(db)["work_item"] == len(dataset["work_item"])
    rec = repo.get_record(db, "raid_item", "RAID-R-031")
    assert rec["probability"] == 4 and rec["raised_date"] == "2026-10-05"


def test_lineage_recorded(db, demo_files):
    result = import_files(db, demo_files)
    rec = repo.get_record(db, "raid_item", "RAID-R-031")
    assert rec["import_batch_id"] == result.batch_ids["raid_item.csv"]
    batch = db.execute("SELECT * FROM import_batch WHERE import_batch_id = ?",
                       (rec["import_batch_id"],)).fetchone()
    assert batch["file_name"] == "raid_item.csv" and batch["status"] == "imported"
    assert len(batch["file_hash"]) == 64
    assert rec["source_row"] >= 2


def test_reimport_same_files_is_skipped(db, demo_files):
    import_files(db, demo_files)
    again = import_files(db, demo_files)
    assert again.status == "skipped"
    assert len(again.skipped_files) == len(demo_files)
    assert len(repo.list_import_batches(db)) == len(demo_files)


def test_modified_file_upserts(db, demo_files):
    import_files(db, demo_files)
    name, data = next(f for f in demo_files if f[0] == "raid_item.csv")
    changed = data.replace(b"Legacy IAM certificates expire", b"Legacy IAM certs expire")
    result = import_files(db, [(name, changed)])
    assert result.status == "imported"
    assert repo.get_record(db, "raid_item", "RAID-R-031")["title"].startswith("Legacy IAM certs")
    assert repo.table_counts(db)["raid_item"] == 90


def test_dirty_file_strict_rejects_everything(db, demo_files, dirty_file):
    import_files(db, demo_files)
    before = repo.table_counts(db)
    result = import_files(db, read_files([dirty_file]))
    assert result.status == "rejected"
    assert repo.table_counts(db) == before
    found = {(i.entity, i.row, i.field) for i in result.errors}
    assert ("squad", 2, "programme_id") in found
    assert ("squad", 3, "team_size") in found
    assert ("raid_item", 2, "raised_date") in found
    assert ("raid_item", 3, "title") in found
    assert ("raid_item", 4, "probability") in found
    assert ("raid_item", 5, "squad_id") in found
    assert ("work_item", 2, None) in found
    assert any(i.field == "linked_ids" for i in result.warnings)
    assert any(i.entity == "notes" for i in result.warnings)
    batches = repo.list_import_batches(db)
    assert batches.iloc[0]["status"] == "rejected"


def test_dirty_file_partial_imports_valid_rows(db, demo_files, dirty_file):
    import_files(db, demo_files)
    result = import_files(db, read_files([dirty_file]), strict=False)
    assert result.status == "partial"
    assert result.inserted == {"raid_item": 1}
    assert repo.get_record(db, "raid_item", "RAID-R-906") is not None


def test_dry_run_writes_nothing(db, demo_files):
    result = import_files(db, demo_files, dry_run=True)
    assert result.status == "validated"
    assert sum(repo.table_counts(db).values()) == 0
    assert len(repo.list_import_batches(db)) == 0


def test_find_record_across_tables(db, demo_files):
    import_files(db, demo_files)
    entity, rec = repo.find_record(db, "DEP-012")
    assert entity == "dependency" and rec["blocks_milestone_id"] == "MS-07"
    assert repo.find_record(db, "NOPE-1") is None


def test_reset_db_clears_data(db, demo_files):
    import_files(db, demo_files)
    reset_db(db)
    assert sum(repo.table_counts(db).values()) == 0
