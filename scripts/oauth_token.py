"""Get an access token from the memory server through the real OAuth flow (dev use).

Runs auth code + PKCE + resource exactly like a client, plays the owner on the
sign-in page with your passphrase, and prints only the access token, for tools that
take a bearer token (scripts/bench.py, MCP Inspector, the Bedrock scripts):

    TOKEN=$(OAUTH_CLIENT_ID=... OAUTH_CLIENT_SECRET=... python scripts/oauth_token.py \\
            http://127.0.0.1:8000/mcp --redirect-uri http://127.0.0.1:8001/oauth/callback)

The passphrase comes from OWNER_PASSPHRASE or is asked for without echo. The
redirect URI must be registered on the server; nothing listens on it, the code is
read from the redirect itself.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth import client as oc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mcp_url")
    ap.add_argument("--redirect-uri", default=(os.getenv("OAUTH_REDIRECT_URIS") or "").split(",")[0])
    ap.add_argument("--scope", default="memory.read memory.write")
    args = ap.parse_args()
    client_id, client_secret = os.getenv("OAUTH_CLIENT_ID"), os.getenv("OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret and args.redirect_uri):
        ap.error("set OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET and --redirect-uri (or OAUTH_REDIRECT_URIS)")
    passphrase = os.getenv("OWNER_PASSPHRASE") or getpass.getpass("Owner passphrase: ")
    with httpx.Client(timeout=10, follow_redirects=False) as http:
        ep = oc.discover(args.mcp_url, http)
        verifier, challenge = oc.pkce_pair()
        state = secrets.token_urlsafe(16)
        url = oc.authorize_url(ep, client_id, args.redirect_uri, state, challenge, args.scope.split())
        params = oc.owner_approves(http, url, passphrase)
        if params.get("state") != state or "code" not in params:
            print(f"Linking failed: {params.get('error', 'no code')}", file=sys.stderr)
            return 1
        tokens = oc.exchange_code(ep, client_id, client_secret, params["code"], args.redirect_uri, verifier, http)
    print(tokens.access_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
