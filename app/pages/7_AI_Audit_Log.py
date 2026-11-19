"""AI Audit Log: every AI call, what it saw, what it said, what passed and who decided."""

import hashlib
import json

import streamlit as st

from adm.ai.audit import citations_for, get_output, list_outputs, review_history
from adm.config import load_config
from adm.db.connection import open_app_db, resolve_db_path

st.set_page_config(page_title="AI Audit Log", page_icon="🛰️", layout="wide")
cfg = load_config()
conn = open_app_db(resolve_db_path(cfg.settings.database.resolved_path()))

st.title("AI Audit Log")
st.caption("One row per AI call, including failures. Each row records the provider, model, "
           "prompt version, the exact fact pack the model saw (with its fingerprint), the raw "
           "answer, the validation outcome and the human decisions. No API keys are stored.")

runs = list_outputs(conn)
if runs.empty:
    st.info("No AI runs yet. Run an analysis on the **AI RAID Analyst** page.")
    st.stop()

# --- overview ------------------------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("AI runs", len(runs))
c2.metric("Valid (incl. warnings)",
          int(runs["validation_status"].isin(["valid", "valid_with_warnings"]).sum()))
c3.metric("Partially rejected / invalid / errors",
          int(runs["validation_status"].isin(["partially_rejected", "invalid",
                                              "provider_error"]).sum()))
c4.metric("Runs with human reviews", int((runs["reviews"] > 0).sum()))

statuses = sorted(runs["validation_status"].unique())
chosen = st.multiselect("Validation status", statuses, default=statuses, key="status-filter")
shown = runs[runs["validation_status"].isin(chosen)].copy()
shown["fact_pack_hash"] = shown["fact_pack_hash"].str[:12]
st.dataframe(shown, hide_index=True, width="stretch")

# --- one run in detail -----------------------------------------------------------------------
st.divider()
run_id = st.selectbox("Inspect run", list(shown["ai_output_id"]), key="run",
                      format_func=lambda i: f"#{i}")
if run_id is None:
    st.stop()
out = get_output(conn, int(run_id))
request = out["fact_pack"]["request"]
parsed = out["parsed"] or {}
meta = parsed.get("meta", {})

st.subheader(f"Run #{run_id}: {request['focus'].replace('_', ' ')} · "
             f"{'programme' if request['scope'] == 'programme' else 'squad ' + request['scope']}")
left, right = st.columns(2)
left.markdown(
    f"- **When (UTC):** {out['created_at']}\n"
    f"- **Provider / model:** `{out['provider']}` / `{out['model']}`\n"
    f"- **Prompt:** `{out['prompt_version']}` "
    f"(file sha256 `{str(meta.get('prompt_sha256'))[:12]}`)\n"
    f"- **Validation status:** `{out['validation_status']}`\n"
    f"- **Note from user:** {request.get('note') or '(none)'}"
)
recomputed = hashlib.sha256(out["fact_pack_json"].encode("utf-8")).hexdigest()
integrity = ("✅ matches the stored fact pack" if recomputed == out["fact_pack_hash"]
             else "⛔ does NOT match the stored fact pack")
right.markdown(
    f"- **Fact pack fingerprint:** `{out['fact_pack_hash'][:16]}…` {integrity}\n"
    f"- **Facts supplied:** {meta.get('fact_count')} "
    f"({meta.get('omitted_count')} lower-priority omitted)\n"
    f"- **Source records supplied:** {len(meta.get('source_ids', []))}\n"
    f"- **Latency:** {meta.get('latency_ms')} ms · tokens {meta.get('usage') or 'n/a'}\n"
    f"- **Review completed by:** {out['approved_by'] or 'not yet'}"
)

if "error" in parsed:
    st.error(f"Provider error: {parsed['error']}")
validation = parsed.get("validation")
if validation:
    counts = validation["counts"]
    st.caption(f"✅ {counts['verified']} verified · ⚠️ {counts['flagged']} flagged · "
               f"⛔ {counts['rejected']} rejected" + (f" · {validation['error']}"
                                                     if validation.get("error") else ""))
    issues = [{"Item": f"{i['kind']} {i['index'] + 1}", "Verdict": i["verdict"],
               "Check": x["code"], "Why": x["message"]}
              for i in validation["items"] for x in i["issues"]]
    if issues:
        st.dataframe(issues, hide_index=True, width="stretch")

t1, t2, t3, t4, t5 = st.tabs(["Human decisions", "Citations", "Fact pack (what the AI saw)",
                              "Raw answer", "Source IDs"])
with t1:
    history = review_history(conn, int(run_id))
    if history.empty:
        st.info("No human decisions for this run.")
    else:
        st.dataframe(history[["reviewed_at", "item_kind", "item_index", "decision",
                              "reviewer", "comment", "item_text"]],
                     hide_index=True, width="stretch")
with t2:
    cites = citations_for(conn, int(run_id))
    st.caption("finding_index is the item's position in the validation report (findings "
               "first, then recommendations). valid = 0 means the AI cited a fact that does "
               "not exist.")
    st.dataframe(cites, hide_index=True, width="stretch")
with t3:
    st.json(json.loads(out["fact_pack_json"]), expanded=1)
with t4:
    st.code(out["raw_response"] or "(no answer: provider error)", language="json")
with t5:
    st.write(", ".join(meta.get("source_ids", [])) or "(none)")
