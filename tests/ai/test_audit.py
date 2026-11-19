"""T8: AI audit log, citations, redaction and the human review workflow."""

import hashlib
import json

import pytest

from adm.ai.audit import (
    REDACTED,
    AuditEntry,
    ReviewError,
    citations_for,
    current_reviews,
    get_output,
    list_outputs,
    log_analysis,
    record_review,
    review_complete,
    review_history,
)
from adm.ai.grounding import validate
from adm.db.connection import connect, init_db
from tests.ai.test_grounding import PACK, answer, finding, rec

SECRET = "sk-live-TOPSECRET-987654"

GOOD = answer(
    [finding("RAID-A-018 is 17 days overdue.", ["RULE:overdue_action:RAID-A-018"]),
     finding("Uncited claim about the programme.", [])],                       # rejected
    [rec("Agree a new due date for RAID-A-018", ["RULE:overdue_action:RAID-A-018"]),
     rec("Escalate RAID-R-031 at the ART sync", ["ESC:RAID-R-031"], refs=(0,)),
     rec("Do something unsupported", [])],                                       # rejected
)


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()


def entry(raw=GOOD, validation="auto", error=None):
    v = validate(raw, PACK) if validation == "auto" else validation
    return AuditEntry(use_case="raid_analyst", provider="mock", model="mock-grounded-v1",
                      prompt_ref="raid_analyst@v1", prompt_sha256="abc123", pack=PACK,
                      raw_text=raw, validation=v, latency_ms=12,
                      usage={"input_tokens": 10, "output_tokens": 5}, error=error)


# --- logging -------------------------------------------------------------------------------

def test_successful_call_is_fully_logged(conn):
    oid = log_analysis(conn, entry())
    row = get_output(conn, oid)
    assert row["provider"] == "mock" and row["model"] == "mock-grounded-v1"
    assert row["prompt_version"] == "raid_analyst@v1"
    assert row["validation_status"] == "partially_rejected"
    assert row["fact_pack_hash"] == PACK.hash and row["created_at"]
    meta = row["parsed"]["meta"]
    assert meta["prompt_sha256"] == "abc123" and meta["source_ids"] == PACK.source_ids
    # finding 0 + recommendations 0 and 1 pass; finding 1 and recommendation 2 are uncited
    assert row["parsed"]["validation"]["counts"] == {"verified": 3, "flagged": 0,
                                                     "rejected": 2}
    assert row["approved_by"] is None


def test_stored_fact_pack_reproduces_its_hash(conn):
    row = get_output(conn, log_analysis(conn, entry()))
    assert hashlib.sha256(row["fact_pack_json"].encode()).hexdigest() == row["fact_pack_hash"]
    assert row["fact_pack"]["request"]["focus"] == "immediate_attention"


def test_citations_are_stored_per_item_with_validity(conn):
    oid = log_analysis(conn, entry(answer([finding("RAID-R-031 scores 20.",
                                                   ["ESC:RAID-R-031", "ESC:FAKE"])])))
    cites = citations_for(conn, oid)
    assert list(cites["source_id"]) == ["ESC:FAKE", "ESC:RAID-R-031"]
    assert dict(zip(cites["source_id"], cites["valid"], strict=True)) == {
        "ESC:FAKE": 0, "ESC:RAID-R-031": 1}
    assert set(cites["source_table"].dropna()) == {"rule_result"}


def test_provider_error_is_logged_without_a_response(conn):
    oid = log_analysis(conn, entry(raw=None, validation=None, error="Timed out after 30s."))
    row = get_output(conn, oid)
    assert row["validation_status"] == "provider_error" and row["raw_response"] is None
    assert row["parsed"]["error"] == "Timed out after 30s."
    assert citations_for(conn, oid).empty


def test_invalid_answer_is_logged_with_its_raw_text(conn):
    oid = log_analysis(conn, entry(raw='{"summary": broken'))
    row = get_output(conn, oid)
    assert row["validation_status"] == "invalid" and row["raw_response"] == '{"summary": broken'
    assert row["parsed"]["response"] is None


def test_secrets_are_redacted_and_never_stored(conn):
    leaky = GOOD.replace("Identity & Security needs attention.",
                         f"Identity & Security needs attention. key={SECRET}")
    log_analysis(conn, entry(raw=leaky), secrets=[SECRET])
    log_analysis(conn, entry(raw=None, validation=None, error=f"auth failed for {SECRET}"),
                 secrets=[SECRET])
    for table in ("ai_output", "ai_citation"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            assert all(SECRET not in str(v) for v in tuple(row))
    raw = conn.execute("SELECT raw_response FROM ai_output WHERE raw_response IS NOT NULL")
    assert REDACTED in raw.fetchone()[0]


def test_list_outputs_newest_first(conn):
    first = log_analysis(conn, entry())
    second = log_analysis(conn, entry(raw=None, validation=None, error="x"))
    df = list_outputs(conn)
    assert list(df["ai_output_id"]) == [second, first]
    assert set(df.columns) >= {"provider", "model", "prompt_version", "validation_status",
                               "fact_pack_hash", "reviews"}


# --- human review ---------------------------------------------------------------------------

def test_approving_and_rejecting_recommendations(conn):
    oid = log_analysis(conn, entry())
    record_review(conn, oid, "recommendation", 0, "approved", "Priya Nair", "Agreed at sync")
    assert not review_complete(conn, oid)                  # recommendation 1 still pending
    record_review(conn, oid, "recommendation", 1, "rejected", "Priya Nair", "Not yet")
    assert review_complete(conn, oid)                      # rec 2 was rejected by validator
    row = get_output(conn, oid)
    assert row["approved_by"] == "Priya Nair" and row["approved_at"]
    reviews = current_reviews(conn, oid)
    assert reviews[("recommendation", 0)]["decision"] == "approved"
    assert reviews[("recommendation", 0)]["item_text"] == "Agree a new due date for RAID-A-018"


def test_validator_rejected_items_cannot_be_approved(conn):
    oid = log_analysis(conn, entry())
    with pytest.raises(ReviewError, match="rejected this item"):
        record_review(conn, oid, "recommendation", 2, "approved", "Priya Nair")
    record_review(conn, oid, "recommendation", 2, "rejected", "Priya Nair")   # allowed


def test_review_needs_a_reviewer_valid_decision_and_existing_item(conn):
    oid = log_analysis(conn, entry())
    with pytest.raises(ReviewError, match="reviewer"):
        record_review(conn, oid, "recommendation", 0, "approved", "  ")
    with pytest.raises(ReviewError, match="Decision"):
        record_review(conn, oid, "recommendation", 0, "maybe", "Priya")
    with pytest.raises(ReviewError, match="no recommendation 9"):
        record_review(conn, oid, "recommendation", 9, "approved", "Priya")
    with pytest.raises(ReviewError, match="No AI analysis"):
        record_review(conn, 999, "recommendation", 0, "approved", "Priya")


def test_provider_error_has_nothing_to_review(conn):
    oid = log_analysis(conn, entry(raw=None, validation=None, error="x"))
    with pytest.raises(ReviewError, match="no reviewable"):
        record_review(conn, oid, "recommendation", 0, "approved", "Priya")


def test_review_history_is_kept_and_latest_decision_wins(conn):
    oid = log_analysis(conn, entry())
    record_review(conn, oid, "recommendation", 0, "rejected", "Ben Hartley")
    record_review(conn, oid, "recommendation", 0, "approved", "Priya Nair", "Changed my mind")
    assert len(review_history(conn, oid)) == 2
    latest = current_reviews(conn, oid)[("recommendation", 0)]
    assert latest["decision"] == "approved" and latest["reviewer"] == "Priya Nair"


def test_findings_can_be_reviewed_too(conn):
    oid = log_analysis(conn, entry())
    record_review(conn, oid, "finding", 0, "approved", "Priya Nair")
    assert ("finding", 0) in current_reviews(conn, oid)


# --- schema compatibility -------------------------------------------------------------------

def test_existing_databases_gain_the_review_table(conn):
    conn.execute("DROP TABLE ai_review")
    init_db(conn)                                    # what the app does on every start
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='ai_review'").fetchone()


def test_parsed_json_is_valid_json(conn):
    oid = log_analysis(conn, entry())
    raw = conn.execute("SELECT parsed_json FROM ai_output WHERE ai_output_id=?",
                       (oid,)).fetchone()[0]
    assert json.loads(raw)["response"]["summary"]
