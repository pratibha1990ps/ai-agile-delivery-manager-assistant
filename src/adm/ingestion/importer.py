"""Import validated rows into SQLite with lineage.

Policy:
- Files already imported (same SHA-256) are skipped unless ``force=True``: idempotent re-import.
- ``strict=True`` (default): any error rejects the whole import; nothing is written except
  an audit record of the rejected attempt.
- ``strict=False``: valid rows are imported, invalid rows are reported.
- Rows are upserted by primary key, so a corrected file updates existing records.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from adm.db import repositories as repo
from adm.ingestion.loaders import LoadedFile, load_file
from adm.ingestion.schemas import ENTITIES
from adm.ingestion.validator import Issue, ValidationResult, validate


@dataclass
class ImportResult:
    status: str  # imported | partial | rejected | skipped | validated
    issues: list[Issue] = field(default_factory=list)
    inserted: dict[str, int] = field(default_factory=dict)
    skipped_files: list[str] = field(default_factory=list)
    batch_ids: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _already_imported(conn: sqlite3.Connection, hash_: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM import_batch WHERE file_hash = ? AND status IN ('imported', 'partial')",
        (hash_,),
    ).fetchone()
    return row is not None


def _record_batch(conn, f: LoadedFile, status: str, issues: list[Issue]) -> int:
    mine = [i for i in issues if i.file_name == f.file_name]
    cur = conn.execute(
        """INSERT INTO import_batch
           (file_name, file_hash, entities, row_count, error_count, warning_count, status,
            imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            f.file_name, f.file_hash, ",".join(f.frames) or "-", f.row_count,
            sum(i.severity == "error" for i in mine),
            sum(i.severity == "warning" for i in mine),
            status, _now(),
        ),
    )
    return int(cur.lastrowid)


def _upsert(conn, table: str, id_field: str, data: dict, batch_id: int, source_row: int, ts: str):
    cols = list(data) + ["import_batch_id", "source_row", "created_at", "updated_at"]
    values = list(data.values()) + [batch_id, source_row, ts, ts]
    updates = [c for c in cols if c not in (id_field, "created_at")]
    sql = (
        f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))}) '
        f'ON CONFLICT("{id_field}") DO UPDATE SET '
        + ", ".join(f"{c} = excluded.{c}" for c in updates)
    )
    conn.execute(sql, values)


def existing_ids(conn: sqlite3.Connection) -> dict[str, set[str]]:
    return {spec.name: repo.all_ids(conn, spec.name) for spec in ENTITIES}


def import_files(
    conn: sqlite3.Connection,
    files: list[tuple[str, bytes]],
    *,
    strict: bool = True,
    dry_run: bool = False,
    force: bool = False,
) -> ImportResult:
    loaded: list[LoadedFile] = []
    skipped: list[str] = []
    for name, data in files:
        f = load_file(name, data)
        if not force and not dry_run and _already_imported(conn, f.file_hash):
            skipped.append(name)
        else:
            loaded.append(f)

    if not loaded:
        return ImportResult(status="skipped", skipped_files=skipped)

    vr: ValidationResult = validate(loaded, existing_ids(conn))
    result = ImportResult(status="validated", issues=vr.issues, skipped_files=skipped)
    if dry_run:
        result.inserted = {k: len(v) for k, v in vr.rows.items() if v}
        return result

    has_errors = bool(vr.errors)
    if strict and has_errors:
        for f in loaded:
            result.batch_ids[f.file_name] = _record_batch(conn, f, "rejected", vr.issues)
        conn.commit()
        result.status = "rejected"
        return result

    status = "partial" if has_errors else "imported"
    ts = _now()
    try:
        with conn:  # one transaction for the whole import
            for f in loaded:
                result.batch_ids[f.file_name] = _record_batch(conn, f, status, vr.issues)
            for spec in ENTITIES:
                rows = vr.rows.get(spec.name, [])
                for v in rows:
                    _upsert(conn, spec.table, spec.id_field, v.record.to_db(),
                            result.batch_ids[v.file_name], v.source_row, ts)
                if rows:
                    result.inserted[spec.name] = len(rows)
    except sqlite3.IntegrityError as exc:
        result.issues.append(Issue("-", "-", None, None, f"Database integrity error: {exc}"))
        result.status = "rejected"
        result.inserted = {}
        return result

    result.status = status
    return result
