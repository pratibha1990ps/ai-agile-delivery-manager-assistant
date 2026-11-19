from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
PAGES = [APP_DIR / "Home.py", *sorted((APP_DIR / "pages").glob("*.py"))]


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_page_renders_without_exception(page: Path):
    at = AppTest.from_file(str(page), default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.title, "page should render a title"
