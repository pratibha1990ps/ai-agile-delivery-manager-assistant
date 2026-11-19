"""Delivery Control Tower: programme and squad health from deterministic rules."""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from adm.config import load_config
from adm.db.connection import open_app_db, resolve_db_path
from adm.rules.rag import RAG
from adm.rules.raid import RULE_LABELS, risk_score
from adm.services.control_tower import (
    METRIC_LABELS,
    build_health_report,
    format_value,
    save_snapshot,
)
from adm.services.data import load_programme_data
from adm.ui import rag as ui
from adm.ui.records import record_lookup, show_records

st.set_page_config(page_title="Control Tower", page_icon="🛰️", layout="wide")
cfg = load_config()
conn = open_app_db(resolve_db_path(cfg.settings.database.resolved_path()))
data = load_programme_data(conn)

st.title("Delivery Control Tower")
if data.is_empty:
    st.info("No programme data yet. Go to **Data Import** and load the demo dataset.")
    st.stop()

as_of = cfg.settings.app.as_of_date
th = cfg.thresholds
report = build_health_report(data, as_of, th)
save_snapshot(conn, report)

st.caption(
    f"As of {as_of.isoformat()} · current sprint {report.current_sprint} · last closed "
    f"sprint {report.last_closed_sprint} · rules v{report.rule_version} · every status below "
    "is computed by deterministic rules, not AI"
)

# --- Programme headline -----------------------------------------------------------------
c0, c1, c2, c3, c4 = st.columns([1.4, 1, 1, 1, 1])
with c0:
    st.markdown("**Programme health**")
    st.markdown(ui.badge(report.programme_rag, size="1.6rem"), unsafe_allow_html=True)
    with st.popover("Why?"):
        for r in report.programme_reasons:
            st.write("- " + r)
counts = {r: sum(s.rag is r for s in report.squads) for r in RAG}
c1.metric("Squads G/A/R", f"{counts[RAG.GREEN]}/{counts[RAG.AMBER]}/{counts[RAG.RED]}",
          help="Squads GREEN / AMBER / RED")
late = [d for d in report.dependencies if d.state == "late"]
c2.metric("Late dependencies", len(late),
          help="Undelivered dependencies past their needed-by date")
open_ms = [m for m in report.milestones if m.rag in (RAG.AMBER, RAG.RED)]
c3.metric("Milestones at risk / late", len(open_ms))
c4.metric("RAID hygiene findings", len(report.findings))

# --- Squad grid ------------------------------------------------------------------------
st.subheader("Squad health")
metric_keys = list(METRIC_LABELS)
display_rows, rag_rows = [], []
for s in report.squads:
    delta = report.trend.get(s.squad_id)
    arrow = "" if delta is None else ("▲" if delta > 0.02 else "▼" if delta < -0.02 else "▶")
    display_rows.append({
        "Squad": s.name, "Overall": ui.label(s.rag),
        "Score": "n/a" if s.score is None else f"{s.score:.2f}",
        "Say/do trend": f"{arrow} {delta:+.0%}" if delta is not None else "n/a",
        **{METRIC_LABELS[k]: format_value(k, s.metrics[k].value) for k in metric_keys},
    })
    rag_rows.append({
        "Squad": None, "Overall": s.rag, "Score": s.rag, "Say/do trend": None,
        **{METRIC_LABELS[k]: s.metrics[k].rag for k in metric_keys},
    })
grid, grid_rags = pd.DataFrame(display_rows), pd.DataFrame(rag_rows)
st.dataframe(ui.style_by_rag(grid, grid_rags), hide_index=True, use_container_width=True)
st.caption(
    f"Ratios use the last {th.windows.say_do_sprints} closed sprints; velocity variability uses "
    f"the last {th.windows.velocity_sprints}. PI objectives use the last closed PI. "
    "Say/do trend compares the last closed sprint with the one before."
)

tabs = st.tabs(["Squad drill-down", "Milestones", "Dependencies", "Risks & RAID hygiene",
                "Trends", "Record lookup"])

# --- Drill-down -------------------------------------------------------------------------
with tabs[0]:
    names = {s.squad_id: s.name for s in report.squads}
    sid = st.selectbox("Squad", list(names), format_func=lambda k: f"{names[k]} ({k})")
    sq = report.squad(sid)
    st.markdown(f"### {sq.name} {ui.badge(sq.rag)}", unsafe_allow_html=True)
    for r in sq.reasons:
        st.write("- " + r)
    rows, rags = [], []
    for k in metric_keys:
        m = sq.metrics[k]
        t = th.metrics[k]
        rows.append({
            "Metric": METRIC_LABELS[k], "Value": format_value(k, m.value), "RAG": ui.label(m.rag),
            "Thresholds": f"{'≥' if t.direction == 'higher_is_better' else '≤'} "
                          f"{format_value(k, t.green)} green, {format_value(k, t.amber)} amber",
            "How it was calculated": m.explanation, "Sources": len(m.source_ids),
        })
        rags.append({"Metric": None, "Value": m.rag, "RAG": m.rag, "Thresholds": None,
                     "How it was calculated": None, "Sources": None})
    st.dataframe(ui.style_by_rag(pd.DataFrame(rows), pd.DataFrame(rags)), hide_index=True,
                 use_container_width=True)
    pick = st.selectbox("Show source records for", metric_keys,
                        format_func=lambda k: METRIC_LABELS[k], key="src-metric")
    show_records(conn, sq.metrics[pick].source_ids, key=f"src-{sid}-{pick}")

# --- Milestones -------------------------------------------------------------------------
with tabs[1]:
    rows, rags = [], []
    for m in report.milestones:
        rows.append({
            "ID": m.milestone_id, "Milestone": m.name, "Squad": m.squad_id or "Programme",
            "State": m.state.replace("_", " "), "RAG": ui.label(m.rag) if m.rag else "✓ Complete",
            "Slip (days)": m.slip_days, "Blocked by": ", ".join(m.blocking_dependencies),
            "Explanation": m.explanation,
        })
        rags.append({"ID": None, "Milestone": None, "Squad": None, "State": m.rag, "RAG": m.rag,
                     "Slip (days)": None, "Blocked by": None, "Explanation": None})
    st.dataframe(ui.style_by_rag(pd.DataFrame(rows), pd.DataFrame(rags)), hide_index=True,
                 use_container_width=True)
    st.caption(f"AMBER when forecast slips ≥ {th.milestones.amber_slip_days} day(s) past "
               f"baseline, RED at ≥ {th.milestones.red_slip_days} days or when the forecast "
               "date has passed.")

# --- Dependencies -----------------------------------------------------------------------
with tabs[2]:
    states = st.multiselect("Show", ["late", "at_risk", "on_track", "delivered"],
                            default=["late", "at_risk"])
    shown = [d for d in report.dependencies if d.state in states]
    state_rag = {"late": RAG.RED, "at_risk": RAG.AMBER, "on_track": RAG.GREEN}
    rows = [{
        "ID": d.dependency_id, "Needs (requester)": d.requesting_squad_id,
        "From (provider)": d.provider, "State": d.state.replace("_", " "),
        "Days late": d.days_late or "", "Critical": "⚠ blocks " + d.blocks_milestone_id
        if d.is_critical else "", "Explanation": d.explanation,
    } for d in shown]
    rags = [{"ID": None, "Needs (requester)": None, "From (provider)": None,
             "State": state_rag.get(d.state), "Days late": None, "Critical": None,
             "Explanation": None} for d in shown]
    if rows:
        st.dataframe(ui.style_by_rag(pd.DataFrame(rows), pd.DataFrame(rags)), hide_index=True,
                     use_container_width=True)
    else:
        st.info("No dependencies in the selected states.")

    open_deps = [d for d in report.dependencies if d.state != "delivered"]
    if open_deps:
        matrix = pd.crosstab(
            pd.Series([d.requesting_squad_id for d in open_deps], name="Needs"),
            pd.Series([d.provider for d in open_deps], name="Provider"),
        )
        fig = px.imshow(matrix, text_auto=True, color_continuous_scale="Oranges", aspect="auto",
                        title="Open dependencies: who waits on whom")
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)

# --- Risks & RAID -----------------------------------------------------------------------
with tabs[3]:
    left, right = st.columns([1, 1.3])
    with left:
        st.markdown("**Top open risks by score**")
        top = report.top_risks.assign(
            score=lambda df: [risk_score(r) for r in df.to_dict(orient="records")],
            mitigated=lambda df: df["mitigation"].notna().map({True: "yes", False: "NO"}),
        )[["raid_id", "squad_id", "title", "score", "owner", "mitigated"]]
        st.dataframe(top, hide_index=True, use_container_width=True)
    with right:
        st.markdown("**RAID hygiene findings**")
        if report.findings:
            st.dataframe(pd.DataFrame([{
                "Rule": RULE_LABELS[f.rule], "Squad": f.squad_id or "Programme",
                "Records": ", ".join(f.record_ids), "Severity": f.severity, "Finding": f.message,
            } for f in report.findings]), hide_index=True, use_container_width=True)
        else:
            st.success("No hygiene findings.")
        st.caption("AI interpretation of these findings arrives with the RAID Analyst "
                   "(Phase 3).")

# --- Trends -----------------------------------------------------------------------------
with tabs[4]:
    series = report.series.copy()
    series["sprint"] = series["sprint_id"]
    series["in_flight"] = series["sprint_id"] == report.current_sprint
    closed = series[~series["in_flight"]]
    metric = st.radio("Metric", ["say_do_ratio", "scope_change_pct", "carry_over_pct"],
                      format_func=lambda k: METRIC_LABELS[k], horizontal=True)
    fig = px.line(closed, x="sprint", y=metric, color="squad_id", markers=True,
                  labels={metric: METRIC_LABELS[metric], "squad_id": "Squad"})
    t = th.metrics[metric]
    for y, colour in ((t.green, ui.SOLID[RAG.GREEN]), (t.amber, ui.SOLID[RAG.AMBER])):
        fig.add_hline(y=y, line_dash="dot", line_color=colour, opacity=0.7)
    fig.update_layout(yaxis_tickformat=".0%", height=420, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Closed sprints only; the in-flight sprint {report.current_sprint} is excluded. "
               "Dotted lines show the green and amber thresholds.")

    vel = closed.pivot(index="sprint", columns="squad_id", values="completed_points")
    fig2 = go.Figure([go.Bar(name=c, x=vel.index, y=vel[c]) for c in vel.columns])
    fig2.update_layout(barmode="group", title="Completed points per sprint", height=360,
                       margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig2, use_container_width=True)

# --- Record lookup ----------------------------------------------------------------------
with tabs[5]:
    st.write("Look up any record cited on this page and see where it came from.")
    record_lookup(conn)
