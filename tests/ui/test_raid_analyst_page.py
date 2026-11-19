"""T10: the AI RAID Analyst page, driven like a user would (no browser, no paid API)."""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adm.ai.models import Focus
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from tests.conftest import read_files

PAGE = Path(__file__).resolve().parents[2] / "app" / "pages" / "2_RAID_Analyst.py"


@pytest.fixture
def loaded_db(demo_dir):
    conn = connect(os.environ["ADM_DB_PATH"])
    init_db(conn)
    assert import_files(conn, read_files(demo_dir.glob("*.csv"))).status == "imported"
    yield conn
    conn.close()


def run_analysis(scope="programme", focus=Focus.ESCALATION, fault=None):
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    at.selectbox(key="scope").set_value(scope)
    at.selectbox(key="focus").set_value(focus)
    at.selectbox(key="fault").set_value(fault)
    next(b for b in at.button if b.label == "Analyse").click()
    return at.run()


def text_of(at) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption]
                    + [s.value for s in at.success] + [w.value for w in at.warning]
                    + [e.value for e in at.error] + [i.value for i in at.info])


def test_page_without_data_asks_for_import():
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception
    assert "Data Import" in text_of(at)


def test_escalation_analysis_shows_four_layers(loaded_db):
    at = run_analysis()
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    assert labels == ["1 · Verified facts (rules)", "2 · AI interpretation",
                      "3 · Suggested actions", "4 · Human decisions", "Validation details"]
    body = text_of(at)
    assert "Every AI statement passed validation" in body
    assert "mock" in body and "raid_analyst@v1" in body
    assert "`ESC:RAID-R-031`" in body                    # citation chips
    assert "Suggestions only" in body
    assert loaded_db.execute("SELECT COUNT(*) FROM ai_output").fetchone()[0] == 1


def test_faulty_ai_answer_is_shown_as_rejected(loaded_db):
    at = run_analysis(fault="invented_id")
    assert not at.exception
    body = text_of(at)
    assert "Some AI statements failed validation" in body
    assert any("Rejected by validator" in e.label for e in at.expander)


def test_malformed_answer_still_shows_verified_facts(loaded_db):
    at = run_analysis(fault="malformed_json")
    body = text_of(at)
    assert "could not be used" in body and "not valid JSON" in body
    assert at.dataframe, "verified facts table should still render"


def test_approving_a_suggested_action_is_recorded(loaded_db):
    at = run_analysis(scope="IDS", focus=Focus.RTE_ACTIONS)
    at.text_input(key="reviewer").input("Priya Nair")
    approve = next(b for b in at.button if b.label == "Approve")
    approve.click()
    at = at.run()
    assert not at.exception, at.exception
    row = loaded_db.execute("SELECT decision, reviewer FROM ai_review").fetchone()
    assert tuple(row) == ("approved", "Priya Nair")
    assert "Approved by Priya Nair" in text_of(at)


def test_review_without_a_name_is_refused(loaded_db):
    at = run_analysis(scope="IDS", focus=Focus.RTE_ACTIONS)
    next(b for b in at.button if b.label == "Reject").click()
    at = at.run()
    assert "reviewer's name" in text_of(at)
    assert loaded_db.execute("SELECT COUNT(*) FROM ai_review").fetchone()[0] == 0
