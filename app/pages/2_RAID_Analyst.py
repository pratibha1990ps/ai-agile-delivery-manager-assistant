"""AI RAID Analyst: rules compute, AI interprets, humans decide."""

import streamlit as st

from adm.ai.audit import ReviewError, current_reviews, get_output, record_review, review_history
from adm.ai.grounding import Status, Verdict
from adm.ai.models import FOCUS_QUESTIONS, PROGRAMME_SCOPE, AnalysisRequest, Focus
from adm.ai.providers.mock import FAULTS
from adm.config import load_config
from adm.db.connection import open_app_db, resolve_db_path
from adm.services.data import load_programme_data
from adm.services.raid_analyst import AnalysisError, create_analyst
from adm.ui import analysis as ui
from adm.ui.records import show_records

st.set_page_config(page_title="AI RAID Analyst", page_icon="🛰️", layout="wide")
cfg = load_config()
conn = open_app_db(resolve_db_path(cfg.settings.database.resolved_path()))
data = load_programme_data(conn)

st.title("AI RAID Analyst")
st.caption("Rules compute · AI interprets · Humans decide. Every AI statement is checked "
           "against the facts it cites before it is shown.")
if data.is_empty:
    st.info("No programme data yet. Go to **Data Import** and load the demo dataset.")
    st.stop()

if cfg.llm.provider == "mock":
    st.info("AI provider: **mock** (deterministic templates, not a language model). It shows "
            "the full pipeline at no cost. Configure `config/llm.yaml` to use a real model.",
            icon="🧪")
else:
    st.caption(f"AI provider: **{cfg.llm.provider}** · model `{cfg.llm.model}`")

# --- request ----------------------------------------------------------------------------------
squads = dict(zip(data.squads["squad_id"], data.squads["name"], strict=True))
scope_options = [PROGRAMME_SCOPE, *squads]
c1, c2 = st.columns([1, 2])
scope = c1.selectbox("Scope", scope_options, key="scope",
                     format_func=lambda s: "Whole programme" if s == PROGRAMME_SCOPE
                     else f"{squads[s]} ({s})")
focus = c2.selectbox("Question", list(Focus), key="focus",
                     format_func=lambda f: FOCUS_QUESTIONS[f])
note = st.text_area("Optional note for the analyst (context only; it cannot change the rules)",
                    max_chars=cfg.settings.ai.max_note_chars, height=70)
with st.expander("Demo: simulate a faulty AI response"):
    st.caption("Replaces the configured provider with the mock in a fault mode, to show the "
               "validator catching bad AI output. The run is audited like any other.")
    fault = st.selectbox("Fault", [None, *FAULTS], key="fault",
                         format_func=lambda f: "None (normal run)" if f is None else f)

if st.button("Analyse", type="primary"):
    try:
        with st.spinner("Running rules, building the fact pack and asking the AI..."):
            result = create_analyst(conn, cfg, simulate_fault=fault).analyse(
                AnalysisRequest(scope=scope, focus=focus, note=note))
        st.session_state["raid_result"] = result
    except AnalysisError as exc:
        st.error(str(exc))

result = st.session_state.get("raid_result")
if result is None:
    st.stop()

# --- result header -----------------------------------------------------------------------------
st.divider()
st.subheader(result.request.question)
kind, text = ui.banner(result.status)
getattr(st, kind)(text)
if result.error:
    st.error(result.error)
if result.validation is not None and result.validation.error:
    st.error(result.validation.error)
pack = result.pack
st.caption(
    f"{'Whole programme' if result.request.is_programme else 'Squad ' + result.request.scope}"
    f" · provider `{result.provider}` · model `{result.model}` · prompt `{result.prompt_ref}` · "
    f"{len(pack.facts)} facts ({pack.omitted_count} lower-priority omitted) · fact pack "
    f"`{pack.hash[:12]}` · audit #{result.ai_output_id}"
)

v = result.validation
reviews = current_reviews(conn, result.ai_output_id)
tab_facts, tab_ai, tab_actions, tab_decisions, tab_checks = st.tabs([
    "1 · Verified facts (rules)", "2 · AI interpretation", "3 · Suggested actions",
    "4 · Human decisions", "Validation details"])

# 1. Verified facts ----------------------------------------------------------------------------
with tab_facts:
    st.caption("Computed by deterministic rules, not AI. These are shown even if the AI fails.")
    facts = result.verified_facts()
    st.dataframe(ui.facts_table(facts), hide_index=True, width="stretch")
    ids = sorted({s for f in facts for s in f.source_ids})
    pick = st.selectbox("Open source records for", [None, *ids], key="facts-src",
                        format_func=lambda x: "Choose a record ID" if x is None else x)
    if pick:
        show_records(conn, [pick], key="facts-records")

# 2. AI interpretation -------------------------------------------------------------------------
with tab_ai:
    if v is None or v.response is None:
        st.info("No AI interpretation for this run.")
    else:
        st.caption("AI interpretation of the facts. ✅ passed every check · ⚠️ passed with a "
                   "warning (check the figure) · ⛔ rejected and hidden.")
        if v.summary_shown:
            st.markdown(f"**AI summary:** {v.response.summary}")
        for item in [i for i in v.findings if i.shown]:
            with st.container(border=True):
                st.markdown(f"**{ui.VERDICT_LABEL[item.verdict]}** · AI interpretation · "
                            f"confidence {item.item.confidence}")
                st.markdown(item.item.statement)
                st.caption("Evidence: " + ui.citation_chips(item))
                for issue in item.issues:
                    st.warning(issue.message, icon="⚠️")
                with st.expander("Show evidence records"):
                    show_records(conn, ui.item_sources(item),
                                 key=f"f-{result.ai_output_id}-{item.index}")
        rejected = [i for i in v.findings if not i.shown]
        if rejected:
            with st.expander(f"Rejected by validator ({len(rejected)})"):
                for item in rejected:
                    st.markdown(f"~~{item.item.statement}~~")
                    for issue in item.issues:
                        st.caption(f"⛔ {issue.message}")
        if v.response.data_gaps:
            st.markdown("**Data gaps the AI reported**")
            for gap in v.response.data_gaps:
                st.markdown(f"- {gap}")

# 3. Suggested actions -------------------------------------------------------------------------
with tab_actions:
    if v is None or v.response is None or not v.recommendations:
        st.info("No suggested actions for this run.")
    else:
        st.caption("Suggestions only. Nothing happens until a named person approves.")
        reviewer = st.text_input("Your name (recorded with each decision)", key="reviewer")
        for item in [i for i in v.recommendations if i.shown]:
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
        hidden = [i for i in v.recommendations if not i.shown]
        if hidden:
            with st.expander(f"Rejected by validator ({len(hidden)}), cannot be approved"):
                for item in hidden:
                    st.markdown(f"~~{item.item.action}~~")
                    for issue in item.issues:
                        st.caption(f"⛔ {issue.message}")

# 4. Human decisions ---------------------------------------------------------------------------
with tab_decisions:
    history = review_history(conn, result.ai_output_id)
    output = get_output(conn, result.ai_output_id)
    if output and output["approved_by"]:
        st.success(f"Review completed by {output['approved_by']} on "
                   f"{output['approved_at'][:10]}.")
    if history.empty:
        st.info("No human decisions yet. Approve or reject suggested actions in tab 3.")
    else:
        st.caption("Full history: the latest decision per item counts.")
        st.dataframe(history[["reviewed_at", "item_kind", "item_index", "decision", "reviewer",
                              "comment", "item_text"]], hide_index=True,
                     width="stretch")

# Validation details -----------------------------------------------------------------------------
with tab_checks:
    if v is None:
        st.info("No AI answer to validate (provider error).")
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
    if result.status is not Status.PROVIDER_ERROR:
        with st.expander("Raw AI response (as received)"):
            output = get_output(conn, result.ai_output_id)
            st.code(output["raw_response"] or "", language="json")
