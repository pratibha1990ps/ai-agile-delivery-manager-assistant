"""RAID log hygiene rules. Each finding names the records that triggered it."""

from __future__ import annotations

import re
from datetime import date
from itertools import combinations

import pandas as pd

from adm.config import Thresholds
from adm.rules.models import Finding

STOPWORDS = {"a", "an", "the", "of", "to", "for", "in", "on", "and", "or", "under", "with",
             "by", "at", "from", "during", "may", "be", "is", "not", "before", "after"}

RULE_LABELS = {
    "high_risk_no_mitigation": "High-score risk without mitigation",
    "missing_owner": "Open item without owner",
    "overdue_action": "Overdue action",
    "stale_item": "Stale item (not updated recently)",
    "risk_past_trigger": "Risk past its trigger date",
    "ageing_issue": "Ageing issue",
    "duplicate_candidate": "Possible duplicate",
}


def risk_score(row: dict) -> int | None:
    p, i = row.get("probability"), row.get("impact")
    if p is None or i is None or pd.isna(p) or pd.isna(i):
        return None
    return int(p) * int(i)


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == ""


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOPWORDS:
            continue
        for suffix in ("ing", "s"):
            if len(w) > 4 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.add(w)
    return out


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def raid_findings(raid: pd.DataFrame, as_of: date, thresholds: Thresholds) -> list[Finding]:
    cfg = thresholds.raid
    findings: list[Finding] = []
    open_items = [r for r in raid.to_dict(orient="records") if r["status"] != "Closed"]

    for r in open_items:
        rid, squad = r["raid_id"], (None if _blank(r["squad_id"]) else r["squad_id"])
        score = risk_score(r)

        if r["type"] == "RISK" and score is not None and score >= cfg.high_risk_score \
                and _blank(r["mitigation"]):
            findings.append(Finding("high_risk_no_mitigation", squad, (rid,), "high",
                                    f"{rid} scores {score} but has no mitigation recorded."))
        if _blank(r["owner"]):
            sev = "high" if (score or 0) >= cfg.high_risk_score else "medium"
            findings.append(Finding("missing_owner", squad, (rid,), sev,
                                    f"{rid} ({r['type'].lower()}) has no owner."))
        due = r["due_date"]
        if r["type"] == "ACTION" and not _blank(due) and due < as_of:
            days = (as_of - due).days
            findings.append(Finding("overdue_action", squad, (rid,),
                                    "high" if days > 7 else "medium",
                                    f"{rid} was due {due.isoformat()} ({days} days overdue)."))
        if r["type"] == "RISK" and not _blank(due) and due < as_of:
            findings.append(Finding("risk_past_trigger", squad, (rid,), "medium",
                                    f"{rid} passed its trigger date {due.isoformat()} and is "
                                    "still open; consider converting it to an issue."))
        stale_days = (as_of - r["last_updated"]).days
        if stale_days > cfg.stale_after_days:
            findings.append(Finding("stale_item", squad, (rid,), "medium",
                                    f"{rid} has not been updated for {stale_days} days."))
        age = (as_of - r["raised_date"]).days
        if r["type"] == "ISSUE" and age > cfg.issue_ageing_days:
            findings.append(Finding("ageing_issue", squad, (rid,),
                                    "high" if (r.get("impact") or 0) >= 4 else "medium",
                                    f"{rid} has been open for {age} days."))

    for a, b in combinations(open_items, 2):
        if a["type"] != b["type"] or a["squad_id"] != b["squad_id"]:
            continue
        sim = similarity(a["title"], b["title"])
        if sim >= cfg.duplicate_similarity:
            squad = None if _blank(a["squad_id"]) else a["squad_id"]
            findings.append(Finding(
                "duplicate_candidate", squad, (a["raid_id"], b["raid_id"]), "low",
                f"{a['raid_id']} and {b['raid_id']} look like duplicates "
                f"(title similarity {sim:.0%}).",
            ))
    return findings


def open_risks_by_score(raid: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    risks = raid[(raid["type"] == "RISK") & (raid["status"] != "Closed")].copy()
    risks["score"] = risks["probability"].astype(int) * risks["impact"].astype(int)
    return risks.sort_values(["score", "raid_id"], ascending=[False, True]).head(limit)
