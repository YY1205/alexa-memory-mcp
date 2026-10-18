# Alexa+ readiness

How this server meets each Alexa+ MCP requirement we know of, and where the proof is.
"Test" means a test in the default suite (`.venv/bin/python -m pytest -q`); "Proof" means
a captured run in `proof/`. Requirements come from the Alexa+ track materials as recorded
in `CLAUDE.md` ("Alexa+ constraints and plan").

Sources (public Alexa+ developer docs, read Oct 16, 2026):
[MCP quickstart](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-quickstart.html),
[account linking](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-account-linking),
[client lifecycle](https://developer.amazon.com/docs/alexaplus/add-ons/mcp-toolkit-client-lifecycle.html),
[Alexa AI CLI reference](https://developer.amazon.com/docs/alexaplus/add-ons/alexa-ai-cli-reference).

Real Alexa+ (Alexa AI CLI, MCP Toolkit, devices) is partner-gated for hackathon
participants, so nothing here has been tried against Alexa itself. The web simulator
(`simulator/`) links and calls the server the same way: a pre-registered OAuth client,
auth code + PKCE + resource, then MCP over Streamable HTTP.

## Transport, protocol, latency

| Requirement | Status | Where | Evidence |
|---|---|---|---|
| Streamable HTTP transport | Pass | `server.py`, `build_app` (`streamable_http_app`, `/mcp`) | Test `tests/test_memory.py::test_full_voice_flow_over_http`; Proof `proof/inspector-tools-list-oauth.txt`, and against the public HTTPS deployment `proof/inspector-tools-list-public.txt` |
| MCP spec 2025-11-25 or later | Pass | `requirements.txt` (`mcp>=2.3,<3`) | Test `test_full_voice_flow_over_http` (negotiates 2026-07-28 ≥ 2025-11-25); Test `tests/test_oauth.py::test_client_asking_for_2025_11_25_gets_it` |
| Round-trip tool latency < 500 ms | Pass (local) | `memory/cache.py` (reads from memory, Drive write-behind); no LLM in the server | Test `tests/test_latency.py` (p95 < 500 ms per tool and the memory resource, with OAuth on and a 1 s storage save); Proof `proof/latency-local.txt` (p95 ≤ 1.7 ms); with real Google Drive `proof/latency-drive.txt` (p95 ≤ 2.2 ms while 14 uploads ran); public HTTPS deployment from a home connection `proof/latency-public.txt` (p95 74-105 ms incl. ~70 ms internet round trip) |
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
| 401 body shape | Pass | `_json_error`: `error`, `message` (as in the docs' example) and `error_description` | Test `test_unauthenticated_mcp_gets_401_without_www_authenticate` |
| `resource` matches the manifest URL with or without a trailing slash | Pass | `_same_resource` | Test `test_resource_with_trailing_slash_and_refresh_without_resource` |
| Refresh requests without `resource`; a refresh token with every token response | Pass | `token` (refresh grant) | Same test; `test_refresh_rotates_and_old_refresh_token_is_rejected` |
| HTTPS and real auth when public | Pass | `AuthSettings.from_env` refuses a public host without OAuth, with an `http://` resource URI, or with the dev token | Test `test_public_host_requires_oauth_and_https` |

## Known deviations and assumptions

* The token endpoint's `401 invalid_client` also has no `WWW-Authenticate: Basic` header,
  although RFC 6749 asks for one when the client used Basic auth. We omit it everywhere to
  stay on the safe side of the Alexa+ rule; confirm with the Alexa+ team.
* Alexa's account-linking redirect URIs must be added to `OAUTH_REDIRECT_URIS`; the docs say
  the Developer Hub and `alexa-ai configure-account-linking` show the full list (not public).
* The docs don't say whether Alexa authenticates at the token endpoint with HTTP Basic or
  in the body (their sample shows only `client_id`); we accept both.
* Alexa caches the auth metadata at deploy time: re-deploy (`alexa-ai deploy`) after changing
  OAuth settings or tools.
* Single owner per deployment; one registered client (Alexa). The simulator registers as
  that client in demos.
* `MCP_BEARER_TOKEN` exists only for localhost development and tests; the server refuses
  to start with it on a public host.

## Listing artifacts

| Item | Status | Where |
|---|---|---|
| `addon.json` | Draft (documented fields only; placeholder host) | `addon-package/addon.json`, generated and checked by `scripts/make_addon.py`; Tests `tests/test_addon.py` |
| Icons: light set 72, 64, 88, 126, 180, 241 px (required) | Done (original artwork) | `addon-package/assets/icon-*.png` from `icon.svg` (`scripts/render_assets.sh`), served at `/addon-assets/` |
| Carousel image 600x900 with alt text (required) | Done | `addon-package/assets/carousel-600x900.png` |
| Dark icons, banner 1200x600 (optional) | Not provided | |
| Privacy policy URL | Done | `/privacy` from `docs/privacy.md`; Test `tests/test_public_pages.py` |
| Terms of use URL | Done | `/terms` from `docs/terms.md` |
| Agent Skill for assistants using the server | Done | `skills/portable-memory/` (agentskills.io format); loaded by the simulator |

### addon.json: draft

Every key path in `addon.json` appears in the quickstart's example
(`manifestVersion`, `storeListing.distributionCountries`, `storeListing.locales.en-US.{default,
name.value, name.spokenForm, shortDescription, fullDescription, examplePhrases,
privacyAndCompliance.privacyPolicyUrl/termsOfUseUrl, mediaAssets.icons.light/dark,
mediaAssets.carouselImages, mediaAssets.bannerImages}`, `integrations[].type = "MCP"`,
`integrations[].config.endpoints.default.{type = "HTTPS", uri}`), and the limits we check
come from the same page. Open questions, to settle with `alexa-ai new mcp` once we have
partner access:

1. **Host**: now the public demo (`https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws`); it changes if the service is re-created.
2. **`accountLinking.enabled`**: the account-linking doc says `alexa-ai new` writes it into
   the manifest, but no example shows where. We left it out rather than guess.
3. **`size` format**: the example uses strings; we assume `"72x72"`-style values.
4. **`storeListing.locales.en-US.default`**: copied as `"DEFAULT"` from the example; its
   meaning isn't explained.
5. **`name.spokenForm`**: present in the example (IPA); whether it's required, and whether
   our IPA is accepted, is not documented.
6. **Media hosting**: the docs require HTTPS URIs for media; we serve them from our own
   server (`/addon-assets/`). Whether Amazon copies them at deploy or hot-links is not said.
7. **Other generated files**: the CLI also creates `package.json` and `.gitignore` in the
   project root; their contents are not documented.

## What remains for a real Alexa+ listing

| Item | Status |
|---|---|
| Deployment with HTTPS | **Done Oct 18**: ECS Express Mode, `https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws/mcp` (until Nov 21) |
| Persist `.oauth/tokens.json` across deploys | Not done by design for the demo: a restart resets memory and links (docs/DEPLOY-PLAN.md) |
| `addon.json` with the deployed host | Done (make_addon.py); `alexa-ai deploy` / `submit` need partner access |
| `PRIVACY_CONTACT` on /privacy and /terms | Done |
| Register Alexa's redirect URIs and client credentials (`alexa-ai configure-account-linking`) | Needs partner access |
| End-to-end test with real Alexa+ (account linking and tool calls) | Blocked: Alexa AI CLI / MCP Toolkit / devices are partner-gated |
