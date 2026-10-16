# Alexa+ readiness

How this server meets each Alexa+ MCP requirement we know of, and where the proof is.
"Test" means a test in the default suite (`.venv/bin/python -m pytest -q`); "Proof" means
a captured run in `proof/`. Requirements come from the Alexa+ track materials as recorded
in `CLAUDE.md` ("Alexa+ constraints and plan").

Real Alexa+ (Alexa AI CLI, MCP Toolkit, devices) is partner-gated for hackathon
participants, so nothing here has been tried against Alexa itself. The web simulator
(`simulator/`) links and calls the server the same way: a pre-registered OAuth client,
auth code + PKCE + resource, then MCP over Streamable HTTP.

## Transport, protocol, latency

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| Streamable HTTP transport | Pass | `server.py`, `build_app` (`streamable_http_app`, `/mcp`) | Test `tests/test_memory.py::test_full_voice_flow_over_http`; Proof `proof/inspector-tools-list-oauth.txt` (MCP Inspector over Streamable HTTP) |
| MCP spec 2025-11-25 or later | Pass | `requirements.txt` (`mcp>=2.3,<3`) | Test `test_full_voice_flow_over_http` (negotiates 2026-07-28 ≥ 2025-11-25); Test `tests/test_oauth.py::test_client_asking_for_2025_11_25_gets_it` |
| Round-trip tool latency < 500 ms | Pass (local) | `memory/cache.py` (reads from memory, Drive write-behind); no LLM in the server | Test `tests/test_latency.py` (p95 < 500 ms per tool and the memory resource, with OAuth on and a 1 s storage save); Proof `proof/latency-local.txt` (p95 ≤ 1.7 ms) |
| Standard MCP results; Alexa+ does NLU and response generation | Pass | `server.py` tools return plain JSON; read-back sentences are written for speech (`memory/speech.py`) | Test `tests/test_latency.py::test_server_never_imports_boto3` (no model in the request path) |

## Authentication checklist

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| OAuth 2.1 authorization code grant | Pass | `auth/oauth.py`, `OAuthServer.authorize` / `.token` | Test `test_oauth.py::test_full_happy_path` |
| PKCE required, S256 only | Pass | `_validate_authorize`, `token` (`code_verifier` checked with S256) | Tests `test_bad_authorize_requests_are_redirected_with_error` (plain / missing challenge), `test_wrong_code_verifier_is_rejected`, `test_missing_code_verifier_is_rejected` |
| `resource` parameter (RFC 8707) on authorize and token | Pass | `_validate_authorize`, `token` | Tests `test_bad_authorize_requests_are_redirected_with_error` (missing / wrong resource), `test_token_requires_the_right_resource` |
| Unauthenticated MCP request → 401 **without** `WWW-Authenticate` | Pass | `OAuthServer.middleware`, `_json_error` | Test `test_unauthenticated_mcp_gets_401_without_www_authenticate`, `test_expired_access_token_is_rejected`; Proof `proof/inspector-tools-list-oauth.txt` (curl -i) |
| Protected Resource Metadata (RFC 9728) | Pass | `protected_resource_metadata` at `/.well-known/oauth-protected-resource` and `.../mcp` | Test `test_protected_resource_metadata_has_required_fields` (resource, authorization_servers, scopes_supported, bearer_methods_supported) |
| Authorization server metadata at `/.well-known/oauth-authorization-server` | Pass | `authorization_server_metadata` | Test `test_authorization_server_metadata_has_required_fields` (issuer, endpoints, response/grant types, scopes, S256, token endpoint auth methods) |
| No DCR, CIMD, OIDC or step-up; pre-registered client | Pass | One client from `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` / `OAUTH_REDIRECT_URIS` | Tests: metadata has no `registration_endpoint`; `test_unknown_client_is_not_redirected`; `test_redirect_uri_mismatch_is_not_redirected` |
| Client authentication at the token endpoint | Pass | `client_secret_basic` and `client_secret_post` | Tests `test_client_secret_post_also_works`, `test_wrong_client_secret_is_rejected` |
| Authorization codes single-use, short-lived | Pass | 60 s, deleted on first use | Tests `test_code_is_single_use`, `test_code_expires_after_60_seconds` |
| `state` round trip; redirect URI exact match | Pass | `state` required; `redirect_uri` must be registered and must match at `/token` | Tests `test_bad_authorize_requests_are_redirected_with_error` (missing state), `test_token_redirect_uri_must_match` |
| Short-lived access tokens bound to the resource and scopes | Pass | `auth/tokens.py` (opaque, 15 min, audience = resource URI) | Tests `test_expired_access_token_is_rejected`, `test_access_token_is_bound_to_the_resource` |
| Refresh tokens with rotation | Pass | `TokenStore.use_refresh`; reuse revokes the grant family | Test `test_refresh_rotates_and_old_refresh_token_is_rejected` |
| Scopes enforced per tool | Pass | `memory.read`: recall, list_due_reviews, memory resource; `memory.write`: propose, supersede, confirm, resolve_review | Tests `test_read_only_token_cannot_write`, `test_write_only_token_cannot_read` |
| Tokens and secrets never stored or logged in the clear | Pass | Only SHA-256 hashes on disk (`.oauth/tokens.json`, 0600, gitignored); owner passphrase only as scrypt hash | Test `test_tokens_survive_a_restart` (no token text on disk, mode 0600) |
| Owner sign-in protected | Pass | Rate limit (5 wrong / 15 min per client, 30 / hour total), CSRF nonce, no framing, no referrer | Tests `test_wrong_passphrase_is_refused_and_rate_limited`, `test_sign_in_page_cannot_be_framed`, `test_owner_can_deny` |
| HTTPS and real auth when public | Pass | `AuthSettings.from_env` refuses a public host without OAuth, with an `http://` resource URI, or with the dev token | Test `test_public_host_requires_oauth_and_https` |

## Known deviations and assumptions

* The token endpoint's `401 invalid_client` also has no `WWW-Authenticate: Basic` header,
  although RFC 6749 asks for one when the client used Basic auth. We omit it everywhere to
  stay on the safe side of the Alexa+ rule; confirm with the Alexa+ team.
* Alexa's account-linking redirect URIs must be added to `OAUTH_REDIRECT_URIS` when the
  skill/add-on is created (the exact URIs come from the Alexa developer console).
* Single owner per deployment; one registered client (Alexa). The simulator registers as
  that client in demos.
* `MCP_BEARER_TOKEN` exists only for localhost development and tests; the server refuses
  to start with it on a public host.

## What remains for a real Alexa+ listing

| Item | Status |
|---|---|
| Deployment with HTTPS (AWS App Runner), `MCP_RESOURCE_URI=https://…/mcp`, `ALLOWED_HOSTS` | Planned Oct 20 |
| Persist `.oauth/tokens.json` across deploys (volume or secret-backed store) | To do with deployment |
| `addon.json` (Alexa+ add-on manifest) and the Agent Skill description | Planned Oct 19 |
| Icons (small and large) for the listing | To do |
| Privacy policy URL and terms of use URL | To do |
| Register Alexa's redirect URIs and client credentials from the developer console | Needs partner access |
| End-to-end test with real Alexa+ (account linking and tool calls) | Blocked: Alexa AI CLI / MCP Toolkit / devices are partner-gated |
| Real Google Drive backend in the demo | Planned Oct 19 |
