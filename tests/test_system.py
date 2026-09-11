"""System endpoints: public health, auth probe for the UI login, safe config."""

from __future__ import annotations


def test_health_is_public(make_client):
    client = make_client(auth_key="secret-1")
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["version"]


def test_auth_check_requires_the_configured_key(make_client):
    client = make_client(auth_key="secret-1")
    assert client.get("/auth/check").status_code == 401
    assert client.get("/auth/check", headers={"X-API-Key": "wrong"}).status_code == 401
    r = client.get("/auth/check", headers={"X-API-Key": "secret-1"})
    assert r.status_code == 200
    assert r.json()["auth_enabled"] is True


def test_keyless_mode_when_no_key_configured(make_client):
    client = make_client(auth_key=None)
    r = client.get("/auth/check")
    assert r.status_code == 200
    assert r.json()["auth_enabled"] is False


def test_keyless_mode_refuses_non_loopback_clients(make_client):
    """Keyless auth is a LOCAL convenience. A keyless instance reached from a
    non-loopback address is network-exposed with no auth, so protected routes
    must fail closed (403) instead of serving wide open. /health stays public."""
    client = make_client(auth_key=None, client_addr=("203.0.113.7", 5000))
    assert client.get("/auth/check").status_code == 403
    assert client.get("/health").status_code == 200


def test_config_requires_auth_and_never_leaks_the_key(make_client):
    client = make_client(auth_key="secret-2")
    assert client.get("/config").status_code == 401

    r = client.get("/config", headers={"X-API-Key": "secret-2"})
    assert r.status_code == 200
    assert "secret-2" not in r.text
    body = r.json()
    # LLM defaults surface model names and the env var NAME, never a key value.
    assert body["llm"]["seeds"]["model"] == "gpt-5.6-luna"
    assert body["llm"]["bulk"]["model"] == "gpt-5.6-luna"
    assert body["llm"]["bulk"]["api_key_env"] == "OPENAI_API_KEY"
    assert body["budgets"]["max_llm_calls"] > 0
    # key_configured tells the UI whether a server-wide fallback key exists, so
    # it can prompt for a per-request key on an open instance — a BOOL, not the key.
    assert body["llm"]["bulk"]["key_configured"] in (True, False)
    assert body["llm_key_from_request_supported"] is True


def test_config_reports_key_configured_from_env(make_client, monkeypatch):
    """key_configured reflects whether the purpose's env var is actually set."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = make_client(auth_key="secret-2")
    body = client.get("/config", headers={"X-API-Key": "secret-2"}).json()
    assert body["llm"]["bulk"]["key_configured"] is False

    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    body = client.get("/config", headers={"X-API-Key": "secret-2"}).json()
    assert body["llm"]["bulk"]["key_configured"] is True


def test_security_headers_on_every_response(make_client):
    client = make_client(auth_key="secret-1")
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_content_security_policy_protects_app_but_not_docs(make_client):
    client = make_client(auth_key="secret-1")
    csp = client.get("/health").headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # Swagger/ReDoc load their bundle from a CDN + an inline init script, so the
    # strict same-origin CSP must NOT apply to the doc pages — otherwise they
    # silently fail to render.
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Content-Security-Policy" not in docs.headers


def test_unhandled_errors_return_sanitized_500(make_client):
    client = make_client(auth_key="secret-1")
    client.app.router.add_api_route("/boom", _boom, methods=["GET"])
    client_no_raise = client.__class__(client.app, raise_server_exceptions=False)
    r = client_no_raise.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error."}
    assert "RuntimeError" not in r.text


async def _boom() -> dict:
    raise RuntimeError("internal detail that must never leak")


def test_non_ascii_key_header_is_a_401_not_a_500(make_client):
    """Header bytes reach the app decoded as latin-1, and secrets.compare_digest
    refuses non-ASCII str -- so a key containing an umlaut used to raise inside
    the auth dependency: a 500 with a full traceback, triggerable with no
    credentials. Wrong key, wrong answer: it must be a plain 401."""
    client = make_client(auth_key="test-key")
    res = client.get("/auth/check", headers={"X-API-Key": "schüssel".encode("latin-1")})
    assert res.status_code == 401


def test_over_long_name_in_a_path_is_a_400(make_client):
    client = make_client(auth_key="test-key")
    res = client.get(f"/refine/{'a' * 300}/ops", headers={"X-API-Key": "test-key"})
    assert res.status_code == 400


def test_every_route_except_health_requires_the_key(make_client):
    """Auth is enforced by a router-level dependency, which is the right
    structure -- and exactly why one router added without it would pass
    every per-route test. This walks the live route table instead."""
    client = make_client(auth_key="test-key")
    checked = []
    # The OpenAPI path table is the published contract (and what the CI smoke
    # check reads); app.routes keeps included routers nested in this FastAPI.
    for path, methods in client.app.openapi()["paths"].items():
        if path == "/health":
            continue
        concrete = path
        for param in ("name", "run_id", "sample_id"):
            concrete = concrete.replace("{" + param + "}", "x")
        for method in methods:
            res = client.request(method.upper(), concrete)
            assert res.status_code == 401, (method, path, res.status_code)
            checked.append((method, path))
    assert len(checked) >= 40, checked
