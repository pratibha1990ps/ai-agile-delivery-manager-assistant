"""Presentation helpers for AI analysis pages (pure functions, no Streamlit calls)."""

from __future__ import annotations

import pandas as pd

from adm.ai.grounding import Status, ValidatedAnalysis, ValidatedItem, Verdict
from adm.ai.models import Fact

VERDICT_LABEL = {
    Verdict.VERIFIED: "✅ Verified",
    Verdict.FLAGGED: "⚠️ Check",
    Verdict.REJECTED: "⛔ Rejected",
}
URGENCY_LABEL = {"immediate": "🔥 Immediate", "this_sprint": "📅 This sprint",
                 "monitor": "👀 Monitor"}

STATUS_BANNER = {
    Status.VALID: ("success", "Every AI statement passed validation against the fact pack."),
    Status.VALID_WITH_WARNINGS: ("warning", "AI answer accepted with warnings: check the "
                                 "items marked ⚠️."),
    Status.PARTIALLY_REJECTED: ("warning", "Some AI statements failed validation and are "
                                "hidden. See 'Rejected by validator' and Validation details."),
    Status.INVALID: ("error", "The AI answer could not be used. Only the verified facts from "
                     "the rules are shown."),
    Status.PROVIDER_ERROR: ("error", "The AI provider did not return an answer. Only the "
                            "verified facts from the rules are shown."),
}


def banner(status: Status) -> tuple[str, str]:
    return STATUS_BANNER[status]


def citation_chips(item: ValidatedItem) -> str:
    return " ".join(f"`{c.fact_id}`" if c.valid else f"~~`{c.fact_id}`~~"
                    for c in item.citations) or "_no citations_"


def item_sources(item: ValidatedItem) -> list[str]:
    """Record IDs behind an item's valid citations (for the drill-down)."""
    return list(dict.fromkeys(s for c in item.citations if c.valid for s in c.source_ids))


def facts_table(facts: list[Fact]) -> pd.DataFrame:
    rows = []
    for f in facts:
        d = f.data
        rows.append({
            "Fact ID": f.fact_id,
            "Squad": f.squad_id or "Programme",
            "What": d.get("label") or d.get("metric") or f.kind.value,
            "Detail": d.get("message") or d.get("explanation") or
            (f"{d.get('count')}" if "count" in d else ""),
            "Rule RAG": d.get("rag") or "",
            "Sources": ", ".join(f.source_ids),
        })
    return pd.DataFrame(rows)


def issues_table(validation: ValidatedAnalysis) -> pd.DataFrame:
    rows = []
    for v in validation.items:
        text = getattr(v.item, "statement", None) or getattr(v.item, "action", "")
        for i in v.issues:
            rows.append({"Item": f"{v.kind} {v.index + 1}", "Verdict": v.verdict.value,
                         "Check": i.code, "Outcome": i.severity, "Why": i.message,
                         "Text": text[:120]})
    for i in validation.summary_issues:
        rows.append({"Item": "summary", "Verdict": "", "Check": i.code, "Outcome": i.severity,
                     "Why": i.message, "Text": ""})
    for i in validation.response_issues:
        rows.append({"Item": "answer", "Verdict": "", "Check": i.code, "Outcome": i.severity,
                     "Why": i.message, "Text": ""})
    return pd.DataFrame(rows, columns=["Item", "Verdict", "Check", "Outcome", "Why", "Text"])
