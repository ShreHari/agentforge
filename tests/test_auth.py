"""Sign-in: the api lets in only requests with a valid Google ID token.

Google is replaced by a pretend token checker, so no network and no real accounts.
"""

import time
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import auth
from api.main import app, get_current_user
from core.config import get_settings

CLIENT_ID = "test-client.apps.googleusercontent.com"


def pretend_google(token, request, audience):
    """Stands in for google.oauth2.id_token.verify_oauth2_token."""
    assert audience == CLIENT_ID  # we always check the token was issued for US
    tokens = {
        "good": {"sub": "google-123", "iss": "https://accounts.google.com",
                 "exp": time.time() + 3600},
        "fake-issuer": {"sub": "x", "iss": "https://evil.example", "exp": time.time() + 3600},
    }
    if token not in tokens:
        raise ValueError("bad signature, wrong audience, or expired")
    pretend_google.calls += 1
    return tokens[token]


@pytest.fixture(autouse=True)
def real_sign_in(monkeypatch):
    """Switch off the conftest "test-user" shortcut and use the real check."""
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    get_settings.cache_clear()
    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", pretend_google)
    pretend_google.calls = 0
    auth._verified.clear()
    yield
    auth._verified.clear()


def status_of(call) -> int:
    try:
        call()
    except HTTPException as error:
        return error.status_code
    return 200


def test_a_valid_token_gives_the_google_account_id():
    assert get_current_user("Bearer good") == "google-123"


@pytest.mark.parametrize("header", [None, "", "good", "Basic good", "Bearer nonsense"])
def test_no_token_or_a_bad_one_is_refused(header):
    assert status_of(lambda: get_current_user(header)) == 401


def test_a_token_from_someone_other_than_google_is_refused():
    assert status_of(lambda: get_current_user("Bearer fake-issuer")) == 401


def test_without_a_client_id_nobody_gets_in(monkeypatch):
    """Fail closed: a server with sign-in switched off must not let everyone in."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    get_settings.cache_clear()
    assert status_of(lambda: get_current_user("Bearer good")) == 503


def test_a_checked_token_is_remembered_until_it_expires():
    get_current_user("Bearer good")
    get_current_user("Bearer good")
    assert pretend_google.calls == 1  # the page polls every 2 s: don't ask Google each time

    auth._verified["good"] = auth.VerifiedToken("google-123", expires_at=time.time() - 1)
    get_current_user("Bearer good")
    assert pretend_google.calls == 2  # expired in the cache: checked again


def test_the_api_refuses_requests_without_sign_in():
    with TestClient(app) as client:
        assert client.get(f"/runs/{uuid.uuid4()}").status_code == 401
        assert client.post("/parse", files={"file": ("cv.pdf", b"%PDF-", "application/pdf")}
                           ).status_code == 401


def test_public_pages_need_no_sign_in():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/config").json() == {"google_client_id": CLIENT_ID}
