import json

from flask import Flask

from app.middleware import rate_limiter


# ---------------------------------------------------------------------------
# Client identifier tests
# ---------------------------------------------------------------------------


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


def test_no_proxy_headers_returns_remote_addr(monkeypatch):
    """Falls back to REMOTE_ADDR when no proxy headers are present."""
    app = Flask(__name__)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.0.0.5"}):
        assert rate_limiter._get_client_identifier() == "10.0.0.5"


def test_invalid_forwarded_ip_falls_through(monkeypatch):
    """Invalid IPs in X-Forwarded-For are ignored."""
    app = Flask(__name__)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": "not-an-ip"},
    ):
        # Should fall through to X-Real-IP or REMOTE_ADDR
        result = rate_limiter._get_client_identifier()
        assert result == "127.0.0.1"


def test_x_real_ip_preferred_over_remote_when_trusted(monkeypatch):
    """X-Real-IP is used when X-Forwarded-For is absent but trust is enabled."""
    app = Flask(__name__)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "10.0.0.1"},
        headers={"X-Real-IP": "198.51.100.50"},
    ):
        assert rate_limiter._get_client_identifier() == "198.51.100.50"


# ---------------------------------------------------------------------------
# Rate limiter initialisation and 429 handler tests
# ---------------------------------------------------------------------------


def test_init_limiter_registers_429_handler():
    """init_limiter returns a Limiter and registers a 429 error handler."""
    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = False  # don't enforce in test
    limiter = rate_limiter.init_limiter(app)
    assert limiter is not None

    # The 429 handler should be registered on the app
    assert 429 in app.error_handler_spec[None]


def test_429_handler_returns_correct_response():
    """The 429 handler returns JSON with the expected structure."""
    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = False

    rate_limiter.init_limiter(app)

    # Simulate a 429 by calling the registered handler directly
    with app.test_request_context("/"):
        # Flask may register under None or the Exception class
        handlers_429 = app.error_handler_spec.get(None, {}).get(429, {})
        handler = None
        for handler_fn in handlers_429.values():
            handler = handler_fn
            break
        assert handler is not None, f"No 429 handler found. Spec: {app.error_handler_spec}"

        class FakeRateLimitExceeded(Exception):
            retry_after = 120

        response, status_code = handler(FakeRateLimitExceeded())
        body = json.loads(response.get_data(as_text=True))

        assert status_code == 429
        assert body["error"] == "rate_limit_exceeded"
        assert body["retry_after"] == 120
        assert "message" in body


# ---------------------------------------------------------------------------
# should_trust_proxy_headers edge cases
# ---------------------------------------------------------------------------


def test_trust_proxy_invalid_remote_addr(monkeypatch):
    """Returns False for an invalid (non-IP) REMOTE_ADDR without env override."""
    app = Flask(__name__)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "not-an-ip"}):
        assert rate_limiter._should_trust_proxy_headers() is False


def test_trust_proxy_ipv6_loopback(monkeypatch):
    """IPv6 loopback (::1) is treated as trusted."""
    app = Flask(__name__)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    with app.test_request_context(
        "/",
        environ_base={"REMOTE_ADDR": "::1"},
        headers={"X-Forwarded-For": "203.0.113.55"},
    ):
        assert rate_limiter._should_trust_proxy_headers() is True
        assert rate_limiter._get_client_identifier() == "203.0.113.55"


def test_trust_proxy_env_values(monkeypatch):
    """Various truthy env values enable proxy header trust."""
    app = Flask(__name__)
    for val in ("1", "true", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("TRUST_PROXY_HEADERS", val)
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "8.8.8.8"}):
            assert rate_limiter._should_trust_proxy_headers() is True

    for val in ("0", "false", "no", ""):
        monkeypatch.setenv("TRUST_PROXY_HEADERS", val)
        with app.test_request_context("/", environ_base={"REMOTE_ADDR": "8.8.8.8"}):
            assert rate_limiter._should_trust_proxy_headers() is False
