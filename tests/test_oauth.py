"""OAuth 2.1 account linking against the Alexa+ MCP authentication checklist."""
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from auth import client as oc  # noqa: E402
from auth import oauth as oauth_mod  # noqa: E402
from auth import tokens as tokens_mod  # noqa: E402
from memory.schema import to_markdown  # noqa: E402
from memory.service import MemoryService  # noqa: E402
from memory.store import LocalFileStore  # noqa: E402
from tests.conftest import (CLIENT_ID, CLIENT_SECRET, OTHER_REDIRECT, PASSPHRASE, SIM_REDIRECT,  # noqa: E402
                            TEST_RECORDS, oauth_env)

BASE = "http://127.0.0.1:8000"
RESOURCE = f"{BASE}/mcp"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-11-25"}


def make_app(tmp_path, monkeypatch, **env):
    for key, value in {**oauth_env(tmp_path, 8000), **env}.items():
        monkeypatch.setenv(key, value)
    for key in ("MCP_BEARER_TOKEN", "ALLOWED_HOSTS", "HOST", "PORT"):
        if key not in env:
            monkeypatch.delenv(key, raising=False)
    path = tmp_path / "memories.md"
    path.write_text(to_markdown(TEST_RECORDS))
    return server.build_app(MemoryService(LocalFileStore(path)))


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(make_app(tmp_path, monkeypatch), base_url=BASE, follow_redirects=False) as c:
        yield c


def start_link(c, scopes=None, redirect=SIM_REDIRECT, **overrides):
    """Return (endpoints, verifier, redirect params) after the owner approves."""
    ep = oc.discover(RESOURCE, c)
    verifier, challenge = oc.pkce_pair()
    url = oc.authorize_url(ep, CLIENT_ID, redirect, "state-123", challenge, scopes)
    for key, value in overrides.items():  # tamper with the authorize request
        url = url.replace(f"{key}=", f"x_{key}=") if value is None else url + f"&{key}={value}"
    return ep, verifier, oc.owner_approves(c, url, PASSPHRASE)


def token_form(ep, code, verifier, redirect=SIM_REDIRECT, **extra):
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
            "code_verifier": verifier, "resource": ep.resource}
    form.update(extra)
    return {k: v for k, v in form.items() if v is not None}


def call_tool(c, token, name, args):
    r = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r


# ---------------------------------------------------------------- metadata + 401

def test_unauthenticated_mcp_gets_401_without_www_authenticate(client):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic Zm9vOmJhcg=="}):
        r = client.post("/mcp", headers={**MCP_HEADERS, **headers},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401
        assert "www-authenticate" not in {k.lower() for k in r.headers}
        assert r.json()["error"] == "invalid_token" and r.json()["message"]  # Alexa docs' 401 body


def test_protected_resource_metadata_has_required_fields(client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        body = client.get(path).json()
        assert body["resource"] == RESOURCE
        assert body["authorization_servers"] == [BASE]
        assert body["scopes_supported"] == ["memory.read", "memory.write"]
        assert body["bearer_methods_supported"] == ["header"]


def test_authorization_server_metadata_has_required_fields(client):
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == BASE
    assert body["authorization_endpoint"] == f"{BASE}/authorize"
    assert body["token_endpoint"] == f"{BASE}/token"
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert body["scopes_supported"] == ["memory.read", "memory.write"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert set(body["token_endpoint_auth_methods_supported"]) == {"client_secret_basic", "client_secret_post"}
    assert "registration_endpoint" not in body  # no dynamic client registration


def test_client_asking_for_2025_11_25_gets_it(client):
    """Alexa+ speaks MCP 2025-11-25; the server must agree to exactly that version."""
    ep, verifier, params = start_link(client)
    tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens.access_token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "alexa-like-client", "version": "1"}}})
    assert r.json()["result"]["protocolVersion"] == "2025-11-25"


# ---------------------------------------------------------------- happy path

def test_full_happy_path(client):
    ep, verifier, params = start_link(client)
    assert params["state"] == "state-123" and params["iss"] == BASE
    tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    assert tokens.scope == "memory.read memory.write"
    r = call_tool(client, tokens.access_token, "propose_memory", {"fact": "The user hates jazz."})
    assert r.status_code == 200 and "pending_id" in r.text


def test_resource_with_trailing_slash_and_refresh_without_resource(client):
    """As documented for Alexa: resource may carry a trailing slash; refresh omits it."""
    ep = oc.discover(RESOURCE, client)
    ep.resource = RESOURCE + "/"
    verifier, challenge = oc.pkce_pair()
    params = oc.owner_approves(client, oc.authorize_url(ep, CLIENT_ID, SIM_REDIRECT, "s", challenge), PASSPHRASE)
    tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    assert call_tool(client, tokens.access_token, "list_due_reviews", {}).status_code == 200
    r = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
                    auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 200 and r.json()["refresh_token"]  # a refresh token every time


def test_client_secret_post_also_works(client):
    ep, verifier, params = start_link(client)
    r = client.post("/token", data=token_form(ep, params["code"], verifier,
                                              client_id=CLIENT_ID, client_secret=CLIENT_SECRET))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"


def test_wrong_client_secret_is_rejected(client):
    ep, verifier, params = start_link(client)
    r = client.post("/token", data=token_form(ep, params["code"], verifier), auth=(CLIENT_ID, "wrong"))
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"
    assert "www-authenticate" not in {k.lower() for k in r.headers}


# ---------------------------------------------------------------- authorize errors

def test_redirect_uri_mismatch_is_not_redirected(client):
    ep = oc.discover(RESOURCE, client)
    _, challenge = oc.pkce_pair()
    r = client.get(oc.authorize_url(ep, CLIENT_ID, "https://evil.example/cb", "s", challenge))
    assert r.status_code == 400 and "location" not in r.headers


def test_unknown_client_is_not_redirected(client):
    ep = oc.discover(RESOURCE, client)
    _, challenge = oc.pkce_pair()
    r = client.get(oc.authorize_url(ep, "someone-else", SIM_REDIRECT, "s", challenge))
    assert r.status_code == 400 and "location" not in r.headers


@pytest.mark.parametrize("tamper, error", [
    ({"resource": None}, "invalid_request"),                        # missing resource
    ({"resource": "https%3A%2F%2Fother.example%2Fmcp"}, "invalid_target"),  # wrong resource
    ({"code_challenge_method": "plain"}, "invalid_request"),        # PKCE must be S256
    ({"code_challenge": None}, "invalid_request"),                  # PKCE required
    ({"state": None}, "invalid_request"),                           # state required
    ({"scope": "admin"}, "invalid_scope"),
])
def test_bad_authorize_requests_are_redirected_with_error(client, tamper, error):
    ep = oc.discover(RESOURCE, client)
    _, challenge = oc.pkce_pair()
    url = oc.authorize_url(ep, CLIENT_ID, SIM_REDIRECT, "state-123", challenge)
    for key, value in tamper.items():
        url = url.replace(f"&{key}=", f"&x_{key}=")
        if value is not None:
            url += f"&{key}={value}"
    r = client.get(url)
    assert r.status_code == 302
    assert r.headers["location"].startswith(SIM_REDIRECT) and f"error={error}" in r.headers["location"]


def test_wrong_passphrase_is_refused_and_rate_limited(client):
    ep = oc.discover(RESOURCE, client)
    _, challenge = oc.pkce_pair()
    url = oc.authorize_url(ep, CLIENT_ID, SIM_REDIRECT, "s", challenge)
    statuses = []
    for _ in range(7):
        page = client.get(url)
        if page.status_code != 200:
            statuses.append(page.status_code)
            continue
        nonce = page.text.split('name="nonce" value="')[1].split('"')[0]
        r = client.post("/authorize", data={"nonce": nonce, "passphrase": "guess", "action": "approve",
                                            "scope_memory.read": "on"})
        statuses.append(r.status_code)
    assert statuses[:5] == [401] * 5 and statuses[5:] == [429, 429]
    # even the right passphrase is refused while locked
    with pytest.raises(oc.OAuthError):
        oc.owner_approves(client, url, PASSPHRASE)


def test_rate_limit_uses_the_address_the_proxy_added():
    from starlette.requests import Request

    def request(xff):
        return Request({"type": "http", "headers": [(b"x-forwarded-for", xff.encode())],
                        "client": ("10.0.0.5", 1234)})

    # a client can put anything on the left; the ALB appends the real address on the right
    assert oauth_mod.client_address(request("6.6.6.6, 203.0.113.9"), True) == "203.0.113.9"
    assert oauth_mod.client_address(request("203.0.113.9"), True) == "203.0.113.9"
    assert oauth_mod.client_address(request("6.6.6.6, 203.0.113.9"), False) == "10.0.0.5"


def test_owner_can_deny(client):
    ep = oc.discover(RESOURCE, client)
    _, challenge = oc.pkce_pair()
    page = client.get(oc.authorize_url(ep, CLIENT_ID, SIM_REDIRECT, "s", challenge))
    nonce = page.text.split('name="nonce" value="')[1].split('"')[0]
    r = client.post("/authorize", data={"nonce": nonce, "action": "deny"})
    assert "error=access_denied" in r.headers["location"]


def test_sign_in_page_cannot_be_framed(client):
    ep = oc.discover(RESOURCE, client)
    _, challenge = oc.pkce_pair()
    page = client.get(oc.authorize_url(ep, CLIENT_ID, SIM_REDIRECT, "s", challenge))
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


# ---------------------------------------------------------------- token errors

def test_wrong_code_verifier_is_rejected(client):
    ep, _, params = start_link(client)
    other_verifier, _ = oc.pkce_pair()
    r = client.post("/token", data=token_form(ep, params["code"], other_verifier), auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_missing_code_verifier_is_rejected(client):
    ep, _, params = start_link(client)
    r = client.post("/token", data=token_form(ep, params["code"], None), auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 400


@pytest.mark.parametrize("resource, error", [(None, "invalid_request"), ("https://other.example/mcp", "invalid_target")])
def test_token_requires_the_right_resource(client, resource, error):
    ep, verifier, params = start_link(client)
    form = token_form(ep, params["code"], verifier)
    form.pop("resource")
    if resource:
        form["resource"] = resource
    r = client.post("/token", data=form, auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 400 and r.json()["error"] == error


def test_token_redirect_uri_must_match(client):
    ep, verifier, params = start_link(client)
    r = client.post("/token", data=token_form(ep, params["code"], verifier, redirect=OTHER_REDIRECT),
                    auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_code_is_single_use(client):
    ep, verifier, params = start_link(client)
    oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    with pytest.raises(oc.OAuthError) as e:
        oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    assert e.value.error == "invalid_grant"


def test_code_expires_after_60_seconds(client, monkeypatch):
    ep, verifier, params = start_link(client)
    later = time.time() + 61
    monkeypatch.setattr(oauth_mod.time, "time", lambda: later)
    with pytest.raises(oc.OAuthError) as e:
        oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    assert e.value.error == "invalid_grant"


# ---------------------------------------------------------------- access + refresh tokens

def test_expired_access_token_is_rejected(client, monkeypatch):
    ep, verifier, params = start_link(client)
    tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    assert call_tool(client, tokens.access_token, "list_due_reviews", {}).status_code == 200
    later = time.time() + 901
    monkeypatch.setattr(tokens_mod.time, "time", lambda: later)
    r = call_tool(client, tokens.access_token, "list_due_reviews", {})
    assert r.status_code == 401 and "www-authenticate" not in {k.lower() for k in r.headers}


def test_access_token_is_bound_to_the_resource(tmp_path, monkeypatch):
    """A token issued for one resource URI is useless at another (audience check)."""
    ep_tokens = None
    with TestClient(make_app(tmp_path, monkeypatch), base_url=BASE, follow_redirects=False) as c:
        ep, verifier, params = start_link(c)
        ep_tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, c)
    other = make_app(tmp_path, monkeypatch, MCP_RESOURCE_URI="http://127.0.0.1:8000/other-mcp")
    with TestClient(other, base_url=BASE) as c:  # same token store, different resource
        assert call_tool(c, ep_tokens.access_token, "list_due_reviews", {}).status_code == 401


def test_refresh_rotates_and_old_refresh_token_is_rejected(client):
    ep, verifier, params = start_link(client)
    first = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    second = oc.refresh(ep, CLIENT_ID, CLIENT_SECRET, first.refresh_token, client)
    assert second.refresh_token != first.refresh_token and second.access_token != first.access_token
    assert call_tool(client, second.access_token, "list_due_reviews", {}).status_code == 200
    with pytest.raises(oc.OAuthError) as e:
        oc.refresh(ep, CLIENT_ID, CLIENT_SECRET, first.refresh_token, client)  # reuse of a rotated token
    assert e.value.error == "invalid_grant"
    # reuse revokes the whole family, including the newest tokens
    with pytest.raises(oc.OAuthError):
        oc.refresh(ep, CLIENT_ID, CLIENT_SECRET, second.refresh_token, client)
    assert call_tool(client, second.access_token, "list_due_reviews", {}).status_code == 401


def test_tokens_survive_a_restart(tmp_path, monkeypatch):
    with TestClient(make_app(tmp_path, monkeypatch), base_url=BASE, follow_redirects=False) as c:
        ep, verifier, params = start_link(c)
        tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, c)
    store_file = tmp_path / "tokens.json"
    assert tokens.access_token not in store_file.read_text()  # only hashes on disk
    assert oct(store_file.stat().st_mode & 0o777) == "0o600"
    with TestClient(make_app(tmp_path, monkeypatch), base_url=BASE) as c:  # "restarted" server
        assert call_tool(c, tokens.access_token, "list_due_reviews", {}).status_code == 200
        assert oc.refresh(ep, CLIENT_ID, CLIENT_SECRET, tokens.refresh_token, c).access_token


# ---------------------------------------------------------------- scopes per tool

def test_read_only_token_cannot_write(client):
    ep, verifier, params = start_link(client, scopes=["memory.read"])
    tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    assert tokens.scope == "memory.read"
    for tool, args in [("recall", {"query": "sister"}), ("list_due_reviews", {}), ("memory_overview", {})]:
        assert "insufficient_scope" not in call_tool(client, tokens.access_token, tool, args).text
    for tool, args in [("propose_memory", {"fact": "The user hates jazz."}),
                       ("supersede_memory", {"old_memory_id": "MEM-0001", "new_fact": "x"}),
                       ("confirm_memory", {"pending_id": "MEM-0001", "confirmed": True}),
                       ("resolve_review", {"memory_id": "MEM-0007", "still_true": False})]:
        assert "insufficient_scope: memory.write required" in call_tool(client, tokens.access_token, tool, args).text
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens.access_token}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                          "params": {"uri": "memory://memories.md"}})
    assert "MEM-0004" in r.text


def test_write_only_token_cannot_read(client):
    ep, verifier, params = start_link(client, scopes=["memory.write"])
    tokens = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, client)
    for tool, args in [("recall", {"query": "sister"}), ("memory_overview", {})]:
        assert "insufficient_scope: memory.read required" in call_tool(client, tokens.access_token, tool, args).text
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens.access_token}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                          "params": {"uri": "memory://memories.md"}})
    assert "MEM-0004" not in r.text


# ---------------------------------------------------------------- deployment guards

def test_public_host_requires_oauth_and_https(tmp_path, monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    for key in ("OAUTH_CLIENT_ID", "MCP_BEARER_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit, match="without OAuth"):
        oauth_mod.AuthSettings.from_env("0.0.0.0", 8000, tmp_path)
    for key, value in oauth_env(tmp_path, 8000).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("MCP_RESOURCE_URI", "http://memory.example.com/mcp")
    with pytest.raises(SystemExit, match="https"):
        oauth_mod.AuthSettings.from_env("0.0.0.0", 8000, tmp_path)
    monkeypatch.setenv("MCP_RESOURCE_URI", "http://127.0.0.1:8000/mcp")  # local container test
    assert oauth_mod.AuthSettings.from_env("0.0.0.0", 8000, tmp_path).oauth_enabled
    monkeypatch.setenv("MCP_RESOURCE_URI", "https://memory.example.com/mcp")
    monkeypatch.setenv("MCP_BEARER_TOKEN", "dev")
    with pytest.raises(SystemExit, match="localhost development only"):
        oauth_mod.AuthSettings.from_env("0.0.0.0", 8000, tmp_path)


def test_admin_reset_needs_write_token_from_other_hosts(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, DEMO_ADMIN="1")
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:  # client host "testclient": not local
        assert c.post("/admin/reset").status_code == 401
        ep, verifier, params = start_link(c, scopes=["memory.read"])
        read_only = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, c)
        assert c.post("/admin/reset", headers={"Authorization": f"Bearer {read_only.access_token}"}).status_code == 401
        ep, verifier, params = start_link(c)
        full = oc.exchange_code(ep, CLIENT_ID, CLIENT_SECRET, params["code"], SIM_REDIRECT, verifier, c)
        assert c.post("/admin/reset", headers={"Authorization": f"Bearer {full.access_token}"}).status_code == 200
