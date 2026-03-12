from flask import Flask

from app.middleware import rate_limiter


def test_forwarded_header_used_for_private_proxy(monkeypatch):
    app = Flask(__name__)

    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": "203.0.113.10, 127.0.0.1"},
    ):
        assert rate_limiter._get_client_identifier() == "203.0.113.10"


def test_forwarded_header_ignored_for_public_client(monkeypatch):
    app = Flask(__name__)

    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
        headers={"X-Forwarded-For": "203.0.113.10"},
    ):
        assert rate_limiter._get_client_identifier() == "8.8.8.8"


def test_env_override_allows_proxy_headers(monkeypatch):
    app = Flask(__name__)

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "198.51.100.8"},
        headers={"X-Real-IP": "203.0.113.22"},
    ):
        assert rate_limiter._get_client_identifier() == "203.0.113.22"
