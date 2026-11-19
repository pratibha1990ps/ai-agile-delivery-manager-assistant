"""Source-record drill-down: show the exact rows behind a number or statement."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import pandas as pd
import streamlit as st

from adm.db import repositories as repo
from adm.ingestion.schemas import ENTITY_BY_NAME


def show_records(conn: sqlite3.Connection, record_ids: Iterable[str], key: str) -> None:
    ids = list(dict.fromkeys(record_ids))
    if not ids:
        st.caption("No source records for this item.")
        return
    grouped: dict[str, list[dict]] = {}
    missing = []
    for rid in ids:
        hit = repo.find_record(conn, rid)
        if hit:
            grouped.setdefault(hit[0], []).append(hit[1])
        else:
            missing.append(rid)
    for entity, rows in grouped.items():
        st.markdown(f"**{ENTITY_BY_NAME[entity].label}** ({len(rows)})")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     key=f"{key}-{entity}")
    if missing:
        st.error("Not found in the database: " + ", ".join(missing))


def record_lookup(conn: sqlite3.Connection, key: str = "lookup") -> None:
    rid = st.text_input("Record ID (e.g. RAID-R-031, DEP-012, SC-S07-ONB, MS-07)", key=key)
    if not rid:
        return
    hit = repo.find_record(conn, rid.strip())
    if not hit:
        st.warning(f"No record with ID '{rid}'.")
        return
    entity, rec = hit
    st.markdown(f"**{ENTITY_BY_NAME[entity].label}** · `{rid}`")
    st.dataframe(pd.DataFrame([rec]).T.rename(columns={0: "value"}).astype(str),
                 width="stretch")
    batch = conn.execute("SELECT * FROM import_batch WHERE import_batch_id = ?",
                         (rec.get("import_batch_id"),)).fetchone()
    if batch:
        st.caption(
            f"Lineage: file `{batch['file_name']}` row {rec.get('source_row')}, batch "
            f"{batch['import_batch_id']} imported {batch['imported_at']} "
            f"(sha256 {batch['file_hash'][:12]}...)."
        )
