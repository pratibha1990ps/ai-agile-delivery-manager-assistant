"""A6: the AI Leadership Reporting page, driven like a user would (no browser, no paid API)."""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adm.ai.audit import get_output
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from tests.conftest import read_files

PAGE = Path(__file__).resolve().parents[2] / "app" / "pages" / "4_Leadership_Report.py"
AUDIT_PAGE = Path(__file__).resolve().parents[2] / "app" / "pages" / "7_AI_Audit_Log.py"


@pytest.fixture
def loaded_db(demo_dir):
    conn = connect(os.environ["ADM_DB_PATH"])
    init_db(conn)
    assert import_files(conn, read_files(demo_dir.glob("*.csv"))).status == "imported"
    yield conn
    conn.close()


def prepare(fault=None):
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    at.selectbox(key="fault").set_value(fault)
    next(b for b in at.button if b.label == "Prepare weekly report").click()
    return at.run()


def text_of(at) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption]
                    + [s.value for s in at.success] + [w.value for w in at.warning]
                    + [e.value for e in at.error] + [i.value for i in at.info])


def test_page_without_data_asks_for_import():
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception and "Data Import" in text_of(at)


def test_a_normal_run_shows_the_report_and_its_tabs(loaded_db):
    at = prepare()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["1 · Report", "2 · Asks for leadership",
                                          "3 · Human decisions", "Agent steps",
                                          "Validation details"]
    body = text_of(at)
    assert "Every statement passed the checks" in body
    assert "mock demo agent" in body and "weekly_report@v1" in body
    assert "3 of 8 tool calls" in body
    assert "Computed by deterministic rules. The AI cannot change this table." in body
    assert "Data Platform is RED this week." in body
    assert "`DEPSTATE:DEP-012`" in body                     # citation chips
    assert loaded_db.execute("SELECT COUNT(*) FROM ai_output").fetchone()[0] == 1


def test_the_status_table_comes_from_rules(loaded_db):
    at = prepare()
    rows = {r["Squad"]: r["Status"] for r in at.dataframe[0].value.to_dict("records")}
    assert "RED" in rows["Data Platform (DAT)"] and "GREEN" in rows["Payments (PAY)"]


def test_a_faulty_agent_report_is_shown_with_rejections(loaded_db):
    at = prepare("invented_id")
    body = text_of(at)
    assert not at.exception
    assert "rejected by the checker" in body
    assert any("Rejected by the checker in this section" in e.label for e in at.expander)


def test_an_agent_that_never_stops_is_cut_off_and_still_shows_the_table(loaded_db):
    at = prepare("never_stops")
    body = text_of(at)
    assert not at.exception
    assert "The agent stopped before finishing" in body and "more than 8 tool calls" in body
    assert "8 of 8 tool calls" in body
    assert at.dataframe, "rule-computed table and step table should still render"


def test_approving_an_ask_is_recorded_and_visible_in_the_audit_log(loaded_db):
    at = prepare()
    at.text_input(key="reviewer").input("Priya Nair")
    next(b for b in at.button if b.label == "Approve").click()
    at = at.run()
    assert not at.exception, at.exception
    assert "Approved" in text_of(at) and "Priya Nair" in text_of(at)
    rows = loaded_db.execute("SELECT reviewer, decision FROM ai_review").fetchall()
    assert [tuple(r) for r in rows] == [("Priya Nair", "approved")]

    audit = AppTest.from_file(str(AUDIT_PAGE), default_timeout=60).run()
    assert not audit.exception
    audit.selectbox(key="run").set_value(1)
    audit = audit.run()
    assert any("weekly report agent" in s.value for s in audit.subheader)
    assert "Agent steps" in [t.label for t in audit.tabs]


def test_approving_without_a_name_is_refused(loaded_db):
    at = prepare()
    next(b for b in at.button if b.label == "Approve").click()
    at = at.run()
    assert any("name" in e.value for e in at.error)
    assert get_output(loaded_db, 1)["approved_by"] is None
