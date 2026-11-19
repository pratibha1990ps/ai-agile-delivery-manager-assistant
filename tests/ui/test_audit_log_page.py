"""T11: the AI Audit Log page."""

import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

from adm.ai.audit import record_review
from adm.ai.models import AnalysisRequest, Focus
from adm.ai.providers import MockProvider
from adm.config import load_config
from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from adm.services.raid_analyst import RaidAnalyst
from tests.conftest import read_files

PAGE = Path(__file__).resolve().parents[2] / "app" / "pages" / "7_AI_Audit_Log.py"


def page_text(at) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption]
                    + [i.value for i in at.info] + [e.value for e in at.error])


def test_empty_log_points_to_the_analyst():
    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception and "AI RAID Analyst" in page_text(at)


def test_log_lists_runs_and_shows_details(demo_dir):
    conn = connect(os.environ["ADM_DB_PATH"])
    init_db(conn)
    import_files(conn, read_files(demo_dir.glob("*.csv")))
    cfg = load_config()
    ok = RaidAnalyst(conn, MockProvider(), cfg).analyse(AnalysisRequest(focus=Focus.ESCALATION))
    RaidAnalyst(conn, MockProvider(fault="rag_override"), cfg).analyse(
        AnalysisRequest(scope="DAT", focus=Focus.RTE_ACTIONS))
    record_review(conn, ok.ai_output_id, "recommendation", 0, "approved", "Priya Nair")
    conn.close()

    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    assert at.metric[0].value == "2"                       # two runs
    at.selectbox(key="run").set_value(ok.ai_output_id)
    at = at.run()
    text = page_text(at)
    assert "raid_analyst@v1" in text and "✅ matches the stored fact pack" in text
    assert [t.label for t in at.tabs] == ["Human decisions", "Citations",
                                          "Fact pack (what the AI saw)", "Raw answer",
                                          "Source IDs"]
    assert any("Priya Nair" in df.value.to_csv() for df in at.dataframe)
