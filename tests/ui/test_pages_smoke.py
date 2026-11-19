from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
PAGES = [APP_DIR / "Home.py", *sorted((APP_DIR / "pages").glob("*.py"))]


def test_home_module_status_reflects_built_pages():
    at = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=30).run()
    table = next(m.value for m in at.markdown if "| Module | Status |" in m.value)
    lines = {line.split("|")[1].strip(): line for line in table.splitlines()[2:]}
    assert "✅ Live" in lines["1. Delivery Control Tower"]
    assert "✅ Live" in lines["Data Import"]
    assert "⏳ Planned" in lines["2. AI RAID Analyst"]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_without_exception(page: Path):
    at = AppTest.from_file(str(page), default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.title, "page should render a title"
