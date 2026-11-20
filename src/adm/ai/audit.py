"""AI audit log and human review storage.

Every AI call, successful or not, leaves one ``ai_output`` row:

| column             | holds                                                              |
|--------------------|--------------------------------------------------------------------|
| provider, model    | the provider family and the model that actually answered          |
| prompt_version     | e.g. ``raid_analyst@v1`` (the template file hash is in parsed_json)|
| fact_pack_hash     | SHA-256 of exactly what the model was shown                        |
| fact_pack_json     | the fact pack itself, including the request (scope, focus, note)  |
| raw_response       | the model's text, unedited (secrets redacted, defensively)        |
| parsed_json        | ``{response, validation, meta}`` or ``{error, meta}``              |
| validation_status  | valid / valid_with_warnings / partially_rejected / invalid / provider_error |
| approved_by/at     | set when every shown recommendation has been reviewed              |

``ai_citation`` stores each cited fact per item (``finding_index`` is the item's position in
the validation report: findings first, then recommendations). ``ai_review`` keeps the full
history of human decisions. API keys are never passed to this module; ``redact`` removes any
secret value that a provider might echo back.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from adm.ai.grounding import Status, ValidatedAnalysis, Verdict
from adm.ai.models import FactPack
from adm.db import repositories as repo

REDACTED = "[REDACTED]"
DECISIONS = ("approved", "rejected")


class ReviewError(ValueError):
    """A review could not be recorded (unknown output or item, rejected item, no reviewer)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def redact(text: str | None, secrets: Iterable[str]) -> str | None:
    if text is None:
        return None
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, REDACTED)
    return text


def _source_table(conn: sqlite3.Connection, fact_id: str, kind: str | None) -> str | None:
    if kind == "record":
        hit = repo.find_record(conn, fact_id)
        return hit[0] if hit else None
    return kind


# --- writing --------------------------------------------------------------------------------

@dataclass(frozen=True)
class AuditEntry:
    use_case: str
    provider: str
    model: str
    prompt_ref: str
    prompt_sha256: str
    pack: FactPack
    raw_text: str | None
    validation: ValidatedAnalysis | None
    latency_ms: int | None = None
    usage: dict[str, int] | None = None
    error: str | None = None
    status: str | None = None             # used when there is no validation (e.g. agent stopped)
    extra: dict[str, Any] | None = None   # stored under "agent" in parsed_json (agent traces)


def log_analysis(conn: sqlite3.Connection, entry: AuditEntry,
                 secrets: Iterable[str] = ()) -> int:
    secrets = [s for s in secrets if s]
    meta = {"prompt_sha256": entry.prompt_sha256, "latency_ms": entry.latency_ms,
            "usage": entry.usage or {}, "source_ids": entry.pack.source_ids,
            "fact_count": len(entry.pack.facts), "omitted_count": entry.pack.omitted_count}

    if entry.validation is None:
        status = entry.status or Status.PROVIDER_ERROR.value
        parsed: dict[str, Any] = {"error": entry.error or "Unknown provider error", "meta": meta}
    else:
        status = entry.validation.status.value
        response = (entry.validation.response.model_dump(mode="json")
                    if entry.validation.response else None)
        parsed = {"response": response, "validation": entry.validation.report(), "meta": meta}
    if entry.extra:
        parsed["agent"] = entry.extra

    with conn:
        cur = conn.execute(
            """INSERT INTO ai_output (use_case, provider, model, prompt_version, fact_pack_hash,
               fact_pack_json, raw_response, parsed_json, validation_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry.use_case, entry.provider, entry.model, entry.prompt_ref, entry.pack.hash,
             entry.pack.canonical_json(), redact(entry.raw_text, secrets),
             redact(json.dumps(parsed, ensure_ascii=False), secrets), status, _now()),
        )
        output_id = int(cur.lastrowid)
        if entry.validation is not None:
            rows = [(output_id, position, _source_table(conn, c.fact_id, c.kind), c.fact_id,
                     int(c.valid))
                    for position, item in enumerate(entry.validation.items)
                    for c in item.citations]
            conn.executemany(
                """INSERT INTO ai_citation (ai_output_id, finding_index, source_table, source_id,
                   valid) VALUES (?, ?, ?, ?, ?)""", rows)
    return output_id


# --- reading --------------------------------------------------------------------------------

def list_outputs(conn: sqlite3.Connection, limit: int = 200) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT ai_output_id, created_at, use_case, provider, model, prompt_version,
                  validation_status, fact_pack_hash, approved_by, approved_at,
                  (SELECT COUNT(*) FROM ai_review r WHERE r.ai_output_id = o.ai_output_id)
                      AS reviews
           FROM ai_output o ORDER BY ai_output_id DESC LIMIT ?""", conn, params=(limit,))


def get_output(conn: sqlite3.Connection, ai_output_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM ai_output WHERE ai_output_id = ?",
                       (ai_output_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["parsed"] = json.loads(out["parsed_json"]) if out["parsed_json"] else None
    out["fact_pack"] = json.loads(out["fact_pack_json"])
    return out


def citations_for(conn: sqlite3.Connection, ai_output_id: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM ai_citation WHERE ai_output_id = ? ORDER BY finding_index, source_id",
        conn, params=(ai_output_id,))


def review_history(conn: sqlite3.Connection, ai_output_id: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM ai_review WHERE ai_output_id = ? ORDER BY review_id", conn,
        params=(ai_output_id,))


def current_reviews(conn: sqlite3.Connection,
                    ai_output_id: int) -> dict[tuple[str, int], dict[str, Any]]:
    """Latest decision per item: {(item_kind, item_index): row}."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for r in conn.execute("SELECT * FROM ai_review WHERE ai_output_id = ? ORDER BY review_id",
                          (ai_output_id,)):
        out[(r["item_kind"], r["item_index"])] = dict(r)
    return out


# --- human review ---------------------------------------------------------------------------

def _item_status(parsed: dict[str, Any], kind: str, index: int) -> tuple[dict, str]:
    response = parsed.get("response") or {}
    items = response.get("findings" if kind == "finding" else "recommendations", [])
    if not 0 <= index < len(items):
        raise ReviewError(f"There is no {kind} {index} in this analysis.")
    verdicts = {(i["kind"], i["index"]): i["verdict"]
                for i in parsed.get("validation", {}).get("items", [])}
    return items[index], verdicts.get((kind, index), Verdict.REJECTED.value)


def record_review(conn: sqlite3.Connection, ai_output_id: int, item_kind: str, item_index: int,
                  decision: str, reviewer: str, comment: str = "") -> int:
    """Record a human decision on one AI item. Items the validator rejected cannot be
    approved: a human may only approve what passed (or was flagged by) the checks."""
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ReviewError("Enter the reviewer's name before approving or rejecting.")
    if decision not in DECISIONS:
        raise ReviewError(f"Decision must be one of {DECISIONS}.")
    output = get_output(conn, ai_output_id)
    if output is None:
        raise ReviewError(f"No AI analysis with ID {ai_output_id}.")
    if not output["parsed"] or "response" not in output["parsed"]:
        raise ReviewError("This analysis has no reviewable items.")
    item, verdict = _item_status(output["parsed"], item_kind, item_index)
    if decision == "approved" and verdict == Verdict.REJECTED.value:
        raise ReviewError("The validator rejected this item; it cannot be approved.")

    text = item.get("action") or item.get("statement") or ""
    now = _now()
    with conn:
        cur = conn.execute(
            """INSERT INTO ai_review (ai_output_id, item_kind, item_index, decision, reviewer,
               comment, item_text, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ai_output_id, item_kind, item_index, decision, reviewer, comment.strip() or None,
             text, now))
        if review_complete(conn, ai_output_id):
            conn.execute("UPDATE ai_output SET approved_by = ?, approved_at = ? "
                         "WHERE ai_output_id = ?", (reviewer, now, ai_output_id))
    return int(cur.lastrowid)


def reviewable_recommendations(conn: sqlite3.Connection, ai_output_id: int) -> list[int]:
    output = get_output(conn, ai_output_id)
    if not output or not output["parsed"] or "validation" not in output["parsed"]:
        return []
    return [i["index"] for i in output["parsed"]["validation"]["items"]
            if i["kind"] == "recommendation" and i["verdict"] != Verdict.REJECTED.value]


def review_complete(conn: sqlite3.Connection, ai_output_id: int) -> bool:
    """True when every shown (non-rejected) recommendation has a human decision."""
    needed = reviewable_recommendations(conn, ai_output_id)
    done = current_reviews(conn, ai_output_id)
    return bool(needed) and all(("recommendation", i) in done for i in needed)
