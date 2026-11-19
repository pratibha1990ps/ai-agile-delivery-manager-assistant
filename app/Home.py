"""Landing page: programme overview and configuration status."""

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
st.markdown(
    """
| Module | Status |
|---|---|
| 1. Delivery Control Tower | Phase 2 |
| 2. AI RAID Analyst | Phase 3 |
| 3. Sprint & PI Health Analyser | Phase 4 |
| 4. AI Leadership Reporting | Phase 5 |
| 5. Delivery Decision Assistant | Phase 6 |
| Data Import | Phase 1 |
| AI Audit Log | Phase 3 |
"""
)
