import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

from adm.db.connection import connect, init_db
from adm.ingestion.importer import import_files
from tests.conftest import read_files

PAGE = Path(__file__).resolve().parents[2] / "app" / "pages" / "1_Control_Tower.py"


def test_control_tower_renders_with_demo_data(demo_dir):
    conn = connect(os.environ["ADM_DB_PATH"])
    init_db(conn)
    assert import_files(conn, read_files(demo_dir.glob("*.csv"))).status == "imported"
    conn.close()

    at = AppTest.from_file(str(PAGE), default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("Squad health" in s.value for s in at.subheader)
    assert len(at.tabs) == 6
    md = " ".join(m.value for m in at.markdown)
    assert "RED" in md     # programme badge
