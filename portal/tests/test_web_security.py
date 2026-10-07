"""Cross-site posts, response headers, cookies and configuration."""

import pytest

from app.settings import ConfigurationError, Settings

from conftest import make_settings


def test_refuses_a_form_posted_from_another_site(client):
    response = client.post("/login", data={"username": "admin", "password": "admin12345"},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_refuses_a_form_post_that_hides_where_it_came_from(client):
    assert client.post("/login", data={"username": "admin", "password": "admin12345"}).status_code == 403
    assert client.post("/login", data={"username": "admin", "password": "admin12345"},
                       headers={"Origin": "null"}).status_code == 403


def test_accepts_a_same_site_form_that_sends_only_a_referer(client):
    response = client.post("/login", data={"username": "admin", "password": "salah"},
                           headers={"Referer": "http://testserver/login"})
    assert response.status_code == 401


def test_every_page_says_no_script_runs_here(client):
    headers = client.get("/login").headers
    assert "script-src 'none'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    # Not no-referrer: browsers then submit forms with `Origin: null`.
    assert headers["referrer-policy"] == "same-origin"
    assert "strict-transport-security" not in headers


def test_production_adds_hsts(make_client):
    client = make_client(APP_ENV="production", OUTBOX_ENCRYPTION_KEY="A" * 43 + "=")
    assert "strict-transport-security" in client.get("/login").headers


def test_cookies_are_secure_in_production_unless_published_at_http(tmp_path):
    key = "A" * 43 + "="
    assert make_settings(tmp_path, APP_ENV="production", OUTBOX_ENCRYPTION_KEY=key,
                         APP_BASE_URL="https://hc.corp.example").cookies_secure
    assert not make_settings(tmp_path, APP_ENV="production", OUTBOX_ENCRYPTION_KEY=key,
                             APP_BASE_URL="http://localhost:3000").cookies_secure
    assert not make_settings(tmp_path).cookies_secure


def test_production_refuses_to_start_without_an_encryption_key(tmp_path):
    with pytest.raises(ConfigurationError, match="OUTBOX_ENCRYPTION_KEY"):
        make_settings(tmp_path, APP_ENV="production")


def test_refuses_to_start_without_the_service_token(tmp_path):
    with pytest.raises(ConfigurationError, match="AD_SERVICE_TOKEN"):
        Settings.from_env({"HC_DATA_DIR": str(tmp_path)})
