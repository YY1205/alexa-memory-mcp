"""Public simulator protection: passcode gate, rate limits, daily model cap."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from simulator import guard as guard_mod  # noqa: E402
from simulator.app import build_app  # noqa: E402
from simulator.brains import ScriptedBrain  # noqa: E402
from simulator.guard import Guard, GuardSettings  # noqa: E402
from simulator.linking import AccountLink  # noqa: E402
from tests.conftest import CLIENT_ID, CLIENT_SECRET, SIM_REDIRECT  # noqa: E402
from tests.test_simulator import link  # noqa: E402

PASSCODE = "judge-passcode-for-tests"


def gated_app(server_url, brain=None, **limits):
    settings = GuardSettings(passcode=PASSCODE, session_secret="s" * 32, trust_proxy=True, **limits)
    guard = Guard(settings)
    account = AccountLink(f"{server_url}/mcp", CLIENT_ID, CLIENT_SECRET, SIM_REDIRECT)
    return build_app(f"{server_url}/mcp", brain or ScriptedBrain(), account=account, guard=guard), guard


def enter(client, passcode=PASSCODE):
    return client.post("/gate", data={"passcode": passcode}, follow_redirects=False)


def test_everything_but_health_is_behind_the_passcode(module_server):
    app, _ = gated_app(module_server)
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/", follow_redirects=False).headers["location"] == "/gate"
        for path in ("/api/status", "/api/memory"):
            r = c.get(path)
            assert r.status_code == 401 and r.json()["gate"] is True
        assert c.get("/oauth/start", follow_redirects=False).headers["location"] == "/gate"
        assert c.post("/api/reset", json={}).status_code == 401
        assert enter(c, "wrong").status_code == 401
        r = enter(c)
        assert r.status_code == 303
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert c.get("/").status_code == 200 and c.get("/api/status").json()["gated"] is True


def test_forged_or_expired_session_is_rejected(module_server, monkeypatch):
    app, guard = gated_app(module_server)
    with TestClient(app) as c:
        good = guard.make_cookie()
        payload, sig = good.rsplit(".", 1)
        c.cookies.set(guard_mod.COOKIE, payload + "." + "0" * len(sig))
        assert c.get("/api/status").status_code == 401
        c.cookies.set(guard_mod.COOKIE, good)
        assert c.get("/api/status").status_code == 200
        later = time.time() + guard_mod.SESSION_TTL + 1
        monkeypatch.setattr(guard_mod.time, "time", lambda: later)
        assert c.get("/api/status").status_code == 401


def test_passcode_guessing_is_rate_limited(module_server):
    app, _ = gated_app(module_server)
    with TestClient(app) as c:
        codes = [enter(c, f"guess-{i}").status_code for i in range(10)]
        assert codes == [401] * 10
        assert enter(c).status_code == 429  # even the right one, while locked


def test_utterances_are_rate_limited_per_client_and_globally(module_server):
    app, _ = gated_app(module_server, per_ip_per_minute=3, global_per_minute=5)
    with TestClient(app) as c:
        enter(c)

        def say(ip):
            # the client forges a left entry; the ALB-added rightmost one counts
            return c.post("/api/utterance", json={"session_id": ip, "text": "Hello?"},
                          headers={"X-Forwarded-For": f"6.6.6.6, {ip}"})

        assert [say("203.0.113.1").status_code for _ in range(3)] == [200, 200, 200]
        r = say("203.0.113.1")
        assert r.status_code == 429 and "a bit fast" in r.json()["error"]
        assert say("203.0.113.2").status_code == 200   # another judge still gets through
        assert say("203.0.113.3").status_code == 429   # global limit (5/min) reached
        assert "busy" in say("203.0.113.4").json()["error"]


class CountingBrain:
    """Stands in for Bedrock: counts calls, always answers."""
    name, model_id = "bedrock", "fake"

    def __init__(self):
        self.calls = 0

    def converse(self, system, messages, tool_config):
        self.calls += 1
        return {"output": {"message": {"role": "assistant", "content": [{"text": "Hi there."}]}},
                "stopReason": "end_turn"}


def test_daily_model_cap_falls_back_to_a_polite_message(module_server):
    brain = CountingBrain()
    app, guard = gated_app(module_server, brain=brain, daily_model_calls=2)
    with TestClient(app) as c:
        enter(c)
        link(c)
        replies = [c.post("/api/utterance", json={"session_id": "s", "text": "Hi"}).json() for _ in range(3)]
        assert [r["reply_text"] for r in replies[:2]] == ["Hi there.", "Hi there."]
        assert "today's limit" in replies[2]["reply_text"] and replies[2]["error"] == "brain"
        assert brain.calls == 2  # no Bedrock call once the cap is hit
        assert c.get("/api/status").json()["model_calls_today"] == 2


def test_reset_and_unlink_need_only_the_passcode_session(module_server):
    app, _ = gated_app(module_server)
    with TestClient(app) as c:
        assert c.post("/api/reset", json={}).status_code == 401
        enter(c)
        assert c.post("/api/reset", json={}).status_code == 200
        assert c.post("/api/unlink").status_code == 200


def test_public_simulator_refuses_to_start_without_the_gate():
    env = {**os.environ, "SIM_HOST": "0.0.0.0", "SIM_PASSCODE": "", "SIM_SESSION_SECRET": ""}
    r = subprocess.run([sys.executable, "-m", "simulator.app"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "SIM_PASSCODE" in r.stderr


def test_local_simulator_is_not_limited():
    g = Guard(GuardSettings(per_ip_per_minute=1))
    assert not g.gated and g.allow_utterance(None) is None  # open local demo
