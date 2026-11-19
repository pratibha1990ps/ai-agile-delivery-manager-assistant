"""Read-side data access. Domain-specific queries are added here as modules need them."""

from __future__ import annotations

import sqlite3

import pandas as pd

from adm.ingestion.schemas import ENTITIES, ENTITY_BY_NAME

LINEAGE_COLUMNS = ("import_batch_id", "source_row", "created_at", "updated_at")


def fetch_df(conn: sqlite3.Connection, entity: str, include_lineage: bool = True) -> pd.DataFrame:
    spec = ENTITY_BY_NAME[entity]
    df = pd.read_sql_query(f'SELECT * FROM "{spec.table}" ORDER BY "{spec.id_field}"', conn)
    if not include_lineage:
        df = df.drop(columns=[c for c in LINEAGE_COLUMNS if c in df.columns])
    return df


def get_record(conn: sqlite3.Connection, entity: str, record_id: str) -> dict | None:
    spec = ENTITY_BY_NAME[entity]
    row = conn.execute(
        f'SELECT * FROM "{spec.table}" WHERE "{spec.id_field}" = ?', (record_id,)
    ).fetchone()
    return dict(row) if row else None


def find_record(conn: sqlite3.Connection, record_id: str) -> tuple[str, dict] | None:
    """Locate any record by ID across all imported entities (used for citation drill-down)."""
    for spec in ENTITIES:
        rec = get_record(conn, spec.name, record_id)
        if rec:
            return spec.name, rec
    return None


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        spec.name: conn.execute(f'SELECT COUNT(*) FROM "{spec.table}"').fetchone()[0]
        for spec in ENTITIES
    }


def all_ids(conn: sqlite3.Connection, entity: str) -> set[str]:
    spec = ENTITY_BY_NAME[entity]
    return {r[0] for r in conn.execute(f'SELECT "{spec.id_field}" FROM "{spec.table}"')}


def list_import_batches(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM import_batch ORDER BY import_batch_id DESC", conn
    )
