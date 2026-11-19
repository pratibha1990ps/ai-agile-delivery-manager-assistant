"""Data Import: load synthetic or uploaded CSV/Excel data with validation and lineage."""

from pathlib import Path

import pandas as pd
import streamlit as st

from adm.config import load_config
from adm.db import repositories as repo
from adm.db.connection import open_app_db, reset_db, resolve_db_path
from adm.ingestion.importer import ImportResult, import_files
from adm.ingestion.schemas import ENTITIES, ENTITY_BY_NAME
from adm.synthetic.generate import DEFAULT_OUT, DIRTY_SAMPLE

st.set_page_config(page_title="Data Import", page_icon="📥", layout="wide")
cfg = load_config()
db_path = resolve_db_path(cfg.settings.database.resolved_path())
conn = open_app_db(db_path)

st.title("Data Import")
st.caption(
    "Every row is validated before it is stored. Each stored record keeps its source file, "
    "batch and row number, so any number or AI statement can be traced back to its origin."
)


def show_result(result: ImportResult) -> None:
    label = {
        "imported": ("success", "Import complete."),
        "partial": ("warning", "Partial import: valid rows stored, invalid rows skipped."),
        "rejected": ("error", "Import rejected: fix the errors below and re-upload. "
                              "Nothing was written (strict mode)."),
        "skipped": ("info", "Nothing to do: these files were already imported (same content)."),
        "validated": ("info", "Validation only: nothing was written."),
    }[result.status]
    getattr(st, label[0])(label[1])
    if result.inserted:
        st.write("Rows " + ("accepted" if result.status == "validated" else "upserted") + ":")
        st.dataframe(pd.DataFrame([result.inserted]), hide_index=True)
    if result.skipped_files:
        st.caption("Skipped (already imported): " + ", ".join(result.skipped_files))
    if result.issues:
        st.write(f"**{len(result.errors)} error(s), {len(result.warnings)} warning(s)**")
        st.dataframe(
            pd.DataFrame([i.__dict__ for i in result.issues]).rename(
                columns={"file_name": "file", "row": "source row"}
            ),
            hide_index=True, width="stretch",
        )


tab_demo, tab_upload, tab_data, tab_history = st.tabs(
    ["Demo data", "Upload files", "Browse records", "Import history"]
)

with tab_demo:
    st.markdown(
        "**Project Meridian** is a synthetic programme with five squads and planted delivery "
        "problems for the modules to find. Regenerate it with "
        "`python -m adm.synthetic.generate`."
    )
    c1, c2 = st.columns(2)
    if c1.button("Load demo dataset", type="primary"):
        files = [(p.name, p.read_bytes()) for p in sorted(DEFAULT_OUT.glob("*.csv"))]
        if not files:
            st.error("No demo files found. Run `python -m adm.synthetic.generate` first.")
        else:
            show_result(import_files(conn, files))
    if c2.button("Try the dirty sample file"):
        if DIRTY_SAMPLE.exists():
            show_result(import_files(conn, [(DIRTY_SAMPLE.name, DIRTY_SAMPLE.read_bytes())],
                                     force=True))
        else:
            st.error("Dirty sample not found. Run the generator first.")

with tab_upload:
    st.markdown(
        "Upload CSV files named after an entity (e.g. `raid_item.csv`) or one Excel workbook "
        "with one sheet per entity. Expected columns per entity:"
    )
    with st.expander("Entity reference"):
        for spec in ENTITIES:
            req = set(spec.required_columns)
            st.markdown(
                f"**{spec.name}** ({spec.label}): "
                + ", ".join(f"`{c}`" + ("*" if c in req else "") for c in spec.columns)
            )
        st.caption("* required")
    uploads = st.file_uploader("Files", type=["csv", "xlsx"], accept_multiple_files=True)
    strict = st.checkbox("Strict mode: reject the whole import if any row has an error",
                         value=True)
    force = st.checkbox("Re-import even if a file with the same content was imported before")
    b1, b2 = st.columns(2)
    if uploads:
        payload = [(u.name, u.getvalue()) for u in uploads]
        if b1.button("Validate only"):
            show_result(import_files(conn, payload, dry_run=True))
        if b2.button("Validate and import", type="primary"):
            show_result(import_files(conn, payload, strict=strict, force=force))

with tab_data:
    counts = repo.table_counts(conn)
    st.dataframe(
        pd.DataFrame(
            [{"entity": k, "label": ENTITY_BY_NAME[k].label, "rows": v} for k, v in counts.items()]
        ),
        hide_index=True,
    )
    entity = st.selectbox("Browse entity", [e.name for e in ENTITIES],
                          format_func=lambda n: ENTITY_BY_NAME[n].label)
    df = repo.fetch_df(conn, entity)
    st.caption("`import_batch_id` and `source_row` show where each record came from.")
    st.dataframe(df, hide_index=True, width="stretch")

    with st.expander("Danger zone"):
        confirm = st.checkbox("I understand this deletes all data in the local database")
        if st.button("Reset database", disabled=not confirm):
            reset_db(conn)
            st.success("Database reset.")
            st.rerun()

with tab_history:
    st.dataframe(repo.list_import_batches(conn), hide_index=True, width="stretch")
    st.caption(f"Database: `{Path(db_path)}`")
