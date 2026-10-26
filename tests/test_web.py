"""The web page: it loads, and it never writes text as HTML."""

import re

from fastapi.testclient import TestClient

from api.main import WEB_PAGE, app


def test_home_page_loads():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Job Hunt Agent" in response.text


def test_page_never_writes_text_as_html():
    """AI output can contain text copied from job ads and web pages. Writing it with
    innerHTML would run any hidden <script> in it (cross-site scripting), so the page
    must only use textContent. This test fails if anyone adds an HTML-writing call."""
    page = WEB_PAGE.read_text()
    dangerous = re.findall(r"\.(?:inner|outer)HTML\s*=|insertAdjacentHTML|document\.write", page)
    assert dangerous == []
