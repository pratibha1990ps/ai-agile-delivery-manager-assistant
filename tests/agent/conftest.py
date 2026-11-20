"""Shared fixtures for the agent tests: a toolbox over the full Project Meridian dataset."""

import pytest

from adm.agent.tools import ToolBox
from adm.config import load_config
from adm.db.connection import connect, init_db
from adm.facts.fact_pack import compute_rule_bundle
from adm.ingestion.importer import import_files
from adm.services.data import load_programme_data
from adm.synthetic.scenarios import AS_OF
from tests.conftest import read_files


@pytest.fixture(scope="module")
def toolbox(demo_dir):
    conn = connect(":memory:")
    init_db(conn)
    assert import_files(conn, read_files(demo_dir.glob("*.csv"))).status == "imported"
    data = load_programme_data(conn)
    yield ToolBox(data, compute_rule_bundle(data, AS_OF, load_config().thresholds))
    conn.close()
