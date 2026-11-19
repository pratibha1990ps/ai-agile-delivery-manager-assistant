from pathlib import Path

import pytest

from adm.db.connection import connect, init_db
from adm.synthetic.generate import generate_dataset, write_dataset, write_dirty_sample


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
