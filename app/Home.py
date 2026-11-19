"""Landing page: programme overview and configuration status."""

from pathlib import Path

import streamlit as st

from adm import __version__
from adm.config import load_config

st.set_page_config(page_title="Delivery Manager Assistant", page_icon="🛰️", layout="wide")

cfg = load_config()
app = cfg.settings.app

st.title("AI Agile Delivery Manager Assistant")
st.caption(f"{app.programme_name} · {app.programme_description}")
st.warning(
    "Portfolio demo using **synthetic data only**. No real client, company or employer data.",
    icon="ℹ️",
)

st.markdown(
    "**Rules compute, AI interprets.** Every metric and RAG status comes from deterministic, "
    "tested rules. AI only explains and recommends from cited source records."
)

col1, col2, col3 = st.columns(3)
col1.metric("As-of date", app.as_of_date.isoformat())
col2.metric("Threshold rules version", cfg.thresholds.version)
col3.metric("LLM provider", cfg.llm.provider)
st.caption(
    f"LLM settings loaded from: `{cfg.llm_source}` · model `{cfg.llm.model}` · v{__version__}"
)

st.subheader("Modules")

PAGES_DIR = Path(__file__).parent / "pages"
PLACEHOLDER_MARKER = "Coming in Phase"
MODULES = [
    # name, page file, roadmap phase, what it does
    ("1. Delivery Control Tower", "1_Control_Tower.py", 2,
     "Programme and squad health, milestones, dependencies, RAID hygiene, trends"),
    ("2. AI RAID Analyst", "2_RAID_Analyst.py", 3, "AI themes and mitigations with citations"),
    ("3. Sprint & PI Health Analyser", "3_Sprint_PI_Health.py", 4,
     "Predictability metrics and grounded narrative"),
    ("4. AI Leadership Reporting", "4_Leadership_Report.py", 5,
     "Draft, approve and export a cited status report"),
    ("5. Delivery Decision Assistant", "5_Decision_Assistant.py", 6,
     "What-if impact plus AI option analysis"),
    ("Data Import", "6_Data_Import.py", 1, "Validated CSV/Excel import with lineage"),
    ("AI Audit Log", "7_AI_Audit_Log.py", 3, "Every AI call, its fact pack and citations"),
]


def is_live(page_file: str) -> bool:
    """A page is live once it no longer renders the 'Coming in Phase' placeholder."""
    path = PAGES_DIR / page_file
    return path.exists() and PLACEHOLDER_MARKER not in path.read_text(encoding="utf-8")


rows = ["| Module | Status | Roadmap | What it does |", "|---|---|---|---|"]
live_count = 0
for name, page_file, phase, summary in MODULES:
    live = is_live(page_file)
    live_count += live
    status = "✅ Live" if live else "⏳ Planned"
    rows.append(f"| {name} | {status} | Phase {phase} | {summary} |")
st.markdown("\n".join(rows))
st.caption(f"{live_count} of {len(MODULES)} pages live. Status is read from the pages themselves, "
           "so it stays current as modules are built.")
