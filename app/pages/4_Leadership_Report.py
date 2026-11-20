"""AI Leadership Reporting: an agent drafts the weekly report; a named human approves it."""

import pandas as pd
import streamlit as st

from adm.agent.demo import FAULTS
from adm.agent.loop import DEFAULT_MAX_TOOL_CALLS
from adm.agent.report import ReportStatus, ValidatedReport
from adm.ai.audit import ReviewError, current_reviews, get_output, record_review, review_history
from adm.ai.grounding import Verdict
from adm.config import load_config
from adm.db.connection import open_app_db, resolve_db_path
from adm.rules.models import RAG
from adm.services.data import load_programme_data
from adm.services.weekly_report import ReportError, create_report_agent
from adm.ui import analysis as ui
from adm.ui.rag import ICON, as_rag
from adm.ui.records import show_records

st.set_page_config(page_title="AI Leadership Reporting", page_icon="🛰️", layout="wide")
cfg = load_config()
conn = open_app_db(resolve_db_path(cfg.settings.database.resolved_path()))
data = load_programme_data(conn)

BANNER = {
    ReportStatus.VALID: ("success", "Every statement passed the checks."),
    ReportStatus.VALID_WITH_WARNINGS: ("warning", "Draft passed with warnings. Check the figures "
                                                  "marked ⚠️ before using it."),
    ReportStatus.PARTIALLY_REJECTED: ("warning", "Some statements were rejected by the checker "
                                                 "and are hidden. The rest is shown."),
    ReportStatus.INVALID: ("error", "The agent's answer was not a usable report."),
    ReportStatus.AGENT_INCOMPLETE: ("error", "The agent stopped before finishing, so there is "
                                             "no draft. The rule-computed table is still shown."),
}
RUN_LABEL = {"tool": "Used a tool", "tool_call": "Used a tool", "tool_error": "Tool call refused",
             "duplicate": "Repeat call refused", "bad_output": "Unreadable reply",
             "final": "Gave final answer"}

st.title("AI Leadership Reporting")
st.caption("The agent gathers evidence with read-only tools and drafts the weekly report. "
           "Status colours come from rules, every sentence is checked against what the agent "
           "retrieved, and nothing counts until a named person approves.")
if data.is_empty:
    st.info("No programme data yet. Go to **Data Import** and load the demo dataset.")
    st.stop()

if cfg.llm.provider == "mock":
    st.info("AI provider: **mock demo agent** (a fixed tool order and template wording, not a "
            "language model). It shows the full pipeline at no cost. Configure "
            "`config/llm.yaml` to use a real model.", icon="🧪")
else:
    st.caption(f"AI provider: **{cfg.llm.provider}** · model `{cfg.llm.model}`")

# --- request ----------------------------------------------------------------------------------
note = st.text_area("Optional note for the agent (context only; it cannot change the rules)",
                    max_chars=cfg.settings.ai.max_note_chars, height=70, key="note")
with st.expander("Demo: simulate a faulty agent"):
    st.caption("Replaces the configured provider with the demo agent in a fault mode, to show "
               "the checker and the step limit catching bad behaviour. Audited like any run.")
    fault = st.selectbox("Fault", [None, *FAULTS], key="fault",
                         format_func=lambda f: "None (normal run)" if f is None else f)

if st.button("Prepare weekly report", type="primary"):
    try:
        with st.spinner("The agent is gathering evidence and drafting the report..."):
            st.session_state["report_result"] = create_report_agent(
                conn, cfg, simulate_fault=fault).run(note)
    except ReportError as exc:
        st.error(str(exc))

result = st.session_state.get("report_result")
if result is None:
    st.stop()
report: ValidatedReport = result.report
run = report.run
v = report.validation

# --- header -----------------------------------------------------------------------------------
st.divider()
kind, text = BANNER[report.status]
getattr(st, kind)(text)
if report.error:
    st.error(report.error)
st.caption(f"Provider `{result.provider}` · model `{result.model}` · prompt `{result.prompt_ref}` "
           f"· {run.tool_calls_used} of {DEFAULT_MAX_TOOL_CALLS} tool calls · "
           f"{len(report.pack.facts)} facts retrieved · evidence fingerprint "
           f"`{report.pack.hash[:12]}` · audit #{result.ai_output_id}")

output = get_output(conn, result.ai_output_id)
if output and output["approved_by"]:
    st.success(f"Report approved by **{output['approved_by']}** on "
               f"{output['approved_at'][:10]}.")

tab_report, tab_asks, tab_decisions, tab_trace, tab_checks = st.tabs([
    "1 · Report", "2 · Asks for leadership", "3 · Human decisions", "Agent steps",
    "Validation details"])

# 1. Report -------------------------------------------------------------------------------------
with tab_report:
    st.subheader("Squad status")
    st.caption("Computed by deterministic rules. The AI cannot change this table.")
    if report.squad_table:
        rows = [{"Squad": f"{r.name} ({r.squad_id})",
                 "Status": f"{ICON.get(as_rag(r.rag) or RAG.GREEN, '')} {r.rag}",
                 "Score": r.score, "Why (rule reasons)": " ".join(r.reasons)}
                for r in report.squad_table]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("The agent did not retrieve programme health, so there is no table.")

    if v is None or v.response is None:
        st.info("No AI-written report for this run.")
    else:
        if v.summary_shown:
            st.markdown(f"**Headline:** {v.response.summary}")
        st.caption("✅ passed every check · ⚠️ passed with a warning (check the figure) · "
                   "⛔ rejected and hidden.")
        for section, title in (("off_track", "What is off track"),
                               ("risks_dependencies", "Risks and dependencies")):
            items = report.findings_in(section)
            st.subheader(title)
            shown = [i for i in items if i.shown]
            if not shown:
                st.caption("Nothing in this section passed the checks." if items
                           else "Nothing reported.")
            for item in shown:
                with st.container(border=True):
                    st.markdown(f"**{ui.VERDICT_LABEL[item.verdict]}** · "
                                f"confidence {item.item.confidence}")
                    st.markdown(item.item.statement)
                    st.caption("Evidence: " + ui.citation_chips(item))
                    for issue in item.issues:
                        st.warning(issue.message, icon="⚠️")
                    with st.expander("Show evidence records"):
                        show_records(conn, ui.item_sources(item),
                                     key=f"f-{result.ai_output_id}-{item.index}")
            rejected = [i for i in items if not i.shown]
            if rejected:
                with st.expander(f"Rejected by the checker in this section ({len(rejected)})"):
                    for item in rejected:
                        st.markdown(f"~~{item.item.statement}~~")
                        for issue in item.issues:
                            st.caption(f"⛔ {issue.message}")
        if v.response.data_gaps:
            st.subheader("Data gaps")
            for gap in v.response.data_gaps:
                st.markdown(f"- {gap}")
    if report.coverage_notes:
        st.caption("Coverage: " + " ".join(report.coverage_notes))

# 2. Asks ----------------------------------------------------------------------------------------
reviews = current_reviews(conn, result.ai_output_id)
with tab_asks:
    if not report.asks:
        st.info("No asks for leadership in this run.")
    else:
        st.caption("Suggestions only. Nothing counts until a named person approves.")
        reviewer = st.text_input("Your name (recorded with each decision)", key="reviewer")
        for item in [i for i in report.asks if i.shown]:
            r = item.item
            key = f"r-{result.ai_output_id}-{item.index}"
            with st.container(border=True):
                st.markdown(f"**{r.action}**")
                st.caption(f"{ui.VERDICT_LABEL[item.verdict]} · owner: {r.owner_role} · "
                           f"{ui.URGENCY_LABEL.get(r.urgency.value, r.urgency.value)}")
                st.markdown(r.rationale)
                st.caption("Evidence: " + ui.citation_chips(item))
                for issue in item.issues:
                    st.warning(issue.message, icon="⚠️")
                done = reviews.get(("recommendation", item.index))
                if done:
                    icon = "✅ Approved" if done["decision"] == "approved" else "❌ Rejected"
                    st.success(f"{icon} by {done['reviewer']} on {done['reviewed_at'][:10]}"
                               + (f": {done['comment']}" if done["comment"] else ""))
                comment = st.text_input("Comment (optional)", key=f"{key}-comment")
                b1, b2, _ = st.columns([1, 1, 4])
                for col, decision, label in ((b1, "approved", "Approve"),
                                             (b2, "rejected", "Reject")):
                    if col.button(label, key=f"{key}-{decision}"):
                        try:
                            record_review(conn, result.ai_output_id, "recommendation",
                                          item.index, decision, reviewer, comment)
                            st.rerun()
                        except ReviewError as exc:
                            st.error(str(exc))
        hidden = [i for i in report.asks if not i.shown]
        if hidden:
            with st.expander(f"Rejected by the checker ({len(hidden)}), cannot be approved"):
                for item in hidden:
                    st.markdown(f"~~{item.item.action}~~")

# 3. Human decisions ------------------------------------------------------------------------------
with tab_decisions:
    history = review_history(conn, result.ai_output_id)
    if history.empty:
        st.info("No human decisions yet. Approve or reject the asks in tab 2.")
    else:
        st.caption("Full history: the latest decision per item counts.")
        st.dataframe(history[["reviewed_at", "item_kind", "item_index", "decision", "reviewer",
                              "comment", "item_text"]], hide_index=True, width="stretch")

# Agent steps -------------------------------------------------------------------------------------
with tab_trace:
    st.caption("Every move the agent made. It can only press read-only tools, up to "
               f"{DEFAULT_MAX_TOOL_CALLS} times.")
    if run.error:
        st.warning(run.error)
    st.dataframe(pd.DataFrame([{
        "Turn": s.turn, "What happened": RUN_LABEL.get(s.kind, s.kind), "Tool": s.tool or "",
        "Arguments": ", ".join(f"{k}={val}" for k, val in s.args.items()),
        "Facts returned": len(s.fact_ids), "Left out": s.omitted_count,
        "Note": s.message} for s in run.steps]), hide_index=True, width="stretch")

# Validation details ---------------------------------------------------------------------------
with tab_checks:
    if v is None:
        st.info("There was no report to validate.")
    else:
        counts = v.counts()
        m1, m2, m3 = st.columns(3)
        m1.metric("✅ Verified", counts[Verdict.VERIFIED.value])
        m2.metric("⚠️ Flagged", counts[Verdict.FLAGGED.value])
        m3.metric("⛔ Rejected", counts[Verdict.REJECTED.value])
        table = ui.issues_table(v)
        if table.empty:
            st.success("No validation issues.")
        else:
            st.dataframe(table, hide_index=True, width="stretch")
    if output and output["raw_response"]:
        with st.expander("Raw final answer (as received)"):
            st.code(output["raw_response"], language="json")
