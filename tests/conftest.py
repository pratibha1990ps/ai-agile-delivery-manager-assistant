import os
import shutil
import tempfile
from pathlib import Path

import pytest

from adm.db.connection import connect, init_db
from adm.synthetic.generate import generate_dataset, write_dataset, write_dirty_sample


def _hermetic_config_dir() -> str:
    """Copy the tracked config files only, so a developer's git-ignored config/llm.yaml (real
    provider, real key) can never change what the tests run against. Set before test modules
    are imported, because some call load_config() at import time."""
    src = Path(__file__).resolve().parents[1] / "config"
    dest = Path(tempfile.mkdtemp(prefix="adm_test_config_"))
    for name in ("settings.yaml", "thresholds.yaml", "llm.yaml.example"):
        shutil.copy(src / name, dest / name)
    return str(dest)


os.environ["ADM_CONFIG_DIR"] = _hermetic_config_dir()


@pytest.fixture(autouse=True)
def isolated_db_path(tmp_path, monkeypatch):
    """Never touch the real data/adm.db during tests."""
    monkeypatch.setenv("ADM_DB_PATH", str(tmp_path / "test.db"))


@pytest.fixture
def db():
    conn = connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def dataset():
    return generate_dataset()


@pytest.fixture(scope="session")
def demo_dir(tmp_path_factory, dataset) -> Path:
    out = tmp_path_factory.mktemp("synthetic")
    write_dataset(dataset, out)
    return out


@pytest.fixture(scope="session")
def dirty_file(tmp_path_factory) -> Path:
    return write_dirty_sample(tmp_path_factory.mktemp("samples") / "dirty_import.xlsx")


def read_files(paths) -> list[tuple[str, bytes]]:
    return [(p.name, p.read_bytes()) for p in sorted(paths)]
