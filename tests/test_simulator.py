"""Simulated Alexa+: golden demo conversations end to end (ScriptedBrain + real server)."""
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from mcp.types import Tool
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory.schema import from_markdown  # noqa: E402
from simulator.app import build_app  # noqa: E402
from simulator.brains import BedrockBrain, BrainError, ScriptedBrain, UnavailableBrain  # noqa: E402
from simulator.linking import AccountLink  # noqa: E402
from simulator.mcp_link import to_bedrock_tool  # noqa: E402
from auth import client as oc  # noqa: E402
from demo_data.seed import spoken_date  # noqa: E402
from tests.conftest import CLIENT_ID, CLIENT_SECRET, PASSPHRASE, SIM_REDIRECT, TEST_TODAY  # noqa: E402

ADMIN = {"X-Admin-Token": "admin-test"}


def make_sim(server_url, brain=None):
    account = AccountLink(f"{server_url}/mcp", CLIENT_ID, CLIENT_SECRET, SIM_REDIRECT)
    app = build_app(f"{server_url}/mcp", brain or ScriptedBrain(), admin_token="admin-test", account=account)
    return app, account


def link(sim, scopes=None, approve=True):
    """Click "Link account", sign in as the owner on the server's page, follow the callback."""
    start = sim.get("/oauth/start", follow_redirects=False)
    assert start.status_code == 303
    authorize_url = start.headers["location"]
    with httpx.Client(follow_redirects=False) as browser:
        if approve:
            params = oc.owner_approves(browser, authorize_url, PASSPHRASE, scopes)
        else:
            page = browser.get(authorize_url)
            nonce = page.text.split('name="nonce" value="')[1].split('"')[0]
            r = browser.post(authorize_url.split("?")[0], data={"nonce": nonce, "action": "deny"})
            params = dict(httpx.URL(r.headers["location"]).params)
    return sim.get("/oauth/callback", params=params, follow_redirects=False)


@pytest.fixture(scope="module")
def sim(module_server):
    app, _ = make_sim(module_server)
    with TestClient(app) as client:
        assert link(client).headers["location"] == "/?linked=1"
        yield client


@pytest.fixture
def chat(sim, request):
    """Fresh demo data and a fresh session per test; returns say(text) -> response."""
    session = request.node.name
    assert sim.post("/api/reset", json={"session_id": session}, headers=ADMIN).status_code == 200

    def say(text):
        r = sim.post("/api/utterance", json={"session_id": session, "text": text})
        assert r.status_code == 200, r.text
        out = r.json()
        assert "error" not in out, out
        out["all_trace"], out["trace"] = out["trace"], mcp_only(out)  # tests look at MCP calls
        for step in out["trace"]:
            assert step["mcp_latency_ms"] < 500, step
        return out

    return say


def memory(sim) -> dict:
    r = sim.get("/api/memory")
    assert r.status_code == 200
    assert r.json()["backend"] == "local-file" and r.json()["web_view_link"] is None
    return {rec.id: rec for rec in from_markdown(r.json()["markdown"])}


def mcp_only(out) -> list[dict]:
    return [step for step in out["trace"] if step.get("kind") == "mcp"]


def tools(out) -> list[str]:
    return [step["tool"] for step in out["trace"]]


def test_status_reports_server_tools_and_brain(sim):
    s = sim.get("/api/status").json()
    assert s["brain"] == "scripted"
    assert s["server"]["has_instructions"]
    assert set(s["server"]["tools"]) == {"recall", "memory_overview", "propose_memory", "supersede_memory",
                                         "confirm_memory", "list_due_reviews", "resolve_review"}


def test_index_page_is_served(sim):
    r = sim.get("/")
    assert r.status_code == 200 and "Simulated Alexa+" in r.text
    assert sim.get("/static/app.js").status_code == 200


def test_conversation_0_what_do_you_know_about_me(chat):
    out = chat("What do you know about me?")
    assert tools(out) == ["memory_overview"]
    assert out["reply_text"].startswith("I remember 10 things about you, including that ")
    assert "may need updating" in out["reply_text"] and "MEM-" not in out["reply_text"]


def test_conversation_1_recall(chat):
    out = chat("When is my sister's birthday?")
    assert tools(out) == ["recall"]
    assert out["reply_text"] == "Your sister Maya's birthday is on November 2."
    assert "MEM-" not in out["reply_text"]


def test_conversation_2_save_with_confirmation(chat, sim):
    out = chat("Remember that my dentist appointment is Friday at 3.")
    assert tools(out) == ["propose_memory"]
    assert out["reply_text"] == "Should I remember that your dentist appointment is Friday at 3?"
    pending = out["trace"][0]["result"]["pending_id"]
    assert memory(sim)[pending].status == "pending"

    out = chat("Yes.")
    assert tools(out) == ["confirm_memory"]
    assert out["reply_text"] == "Got it, I'll remember that."
    rec = memory(sim)[pending]
    assert rec.status == "active" and rec.source_type == "user_explicit"
    assert rec.fact == "The user's dentist appointment is Friday at 3."


def test_conversation_3_correction_one_yes(chat, sim):
    out = chat("The kitchen contractor now starts on the 27th.")
    assert tools(out) == ["propose_memory"]
    result = out["trace"][0]["result"]
    assert result["replaces"] == "MEM-0006"
    start = spoken_date(TEST_TODAY + timedelta(days=6))
    assert out["reply_text"] == ("This looks like an update to your note that you are renovating the kitchen "
                                 f"and the contractor starts on {start}. Should I change it to: your kitchen "
                                 "contractor now starts on the 27th?")
    pending = result["pending_id"]
    assert memory(sim)[pending].status == "pending"

    out = chat("Yes.")  # one answer: save the new fact and retire the old one
    assert tools(out) == ["confirm_memory"]
    assert out["trace"][0]["arguments"] == {"pending_id": pending, "confirmed": True, "replaces": "MEM-0006"}
    assert out["reply_text"] == "Got it, I've updated that."
    mem = memory(sim)
    assert mem["MEM-0006"].status == "historical" and mem["MEM-0006"].superseded_by == pending
    assert mem[pending].status == "active" and mem[pending].supersedes == "MEM-0006"
    assert mem[pending].fact == "The user's kitchen contractor now starts on the 27th."
    assert not [r for r in mem.values() if r.status == "pending"]


def test_conversation_4_review(chat, sim):
    out = chat("Is there anything I should update?")
    assert tools(out) == ["list_due_reviews"]
    assert out["reply_text"] == ("I have a note that you are waiting to hear back about a job "
                                 "interview at a design studio. Is that still true?")  # exactly one overdue

    out = chat("I got the job, I start in November.")
    assert tools(out) == ["resolve_review", "propose_memory"]
    assert out["trace"][0]["arguments"] == {"memory_id": "MEM-0007", "still_true": False}
    assert out["reply_text"] == ("Thanks for the update. Should I remember that you got the job, "
                                 "you start in November?")
    assert memory(sim)["MEM-0007"].status == "historical"
    pending = out["trace"][1]["result"]["pending_id"]

    out = chat("Yes.")
    assert tools(out) == ["confirm_memory"]
    rec = memory(sim)[pending]
    assert rec.status == "active" and rec.fact == "The user got the job, they start in November."


def test_conversation_5_decline(chat, sim):
    before = memory(sim)
    out = chat("Remember that I hate jazz.")
    assert out["reply_text"] == "Should I remember that you hate jazz?"
    out = chat("No.")
    assert tools(out) == ["confirm_memory"]
    assert out["trace"][0]["arguments"]["confirmed"] is False
    assert out["reply_text"] == "Okay, I won't remember that."
    after = memory(sim)
    assert after == before
    assert not any("jazz" in r.fact for r in after.values())


def test_conversation_6_hand_edit_is_used(tmp_path):
    """The user edits memories.md by hand (here: the local file; in the demo: in Google
    Drive); after the refresh interval Alexa answers with the edit."""
    from tests.conftest import running_server

    with running_server(tmp_path, {"MEMORY_REFRESH_SECONDS": "0.3"}) as url:
        app, _ = make_sim(url)
        with TestClient(app) as client:
            link(client)
            path = tmp_path / "memories.md"
            path.write_text(path.read_text().replace("Biscuit", "Pepper"))  # the hand edit
            import time
            time.sleep(1.0)
            out = client.post("/api/utterance", json={"session_id": "s6",
                                                      "text": "When does my dog need his heartworm pill?"}).json()
            assert [s["tool"] for s in mcp_only(out)] == ["recall"]
            assert out["reply_text"] == "Your dog Pepper needs his heartworm pill on the first day of each month."
            assert "Pepper" in client.get("/api/memory").json()["markdown"]


def test_reset_needs_admin_token(sim):
    assert sim.post("/api/reset", json={}).status_code == 403


def test_empty_utterance_is_rejected(sim):
    assert sim.post("/api/utterance", json={"session_id": "x", "text": "  "}).status_code == 400


def test_brain_error_is_reported_and_history_stays_valid(module_server):
    app, _ = make_sim(module_server, UnavailableBrain("bedrock", "No AWS credentials found."))
    with TestClient(app) as client:
        link(client)
        out = client.post("/api/utterance", json={"session_id": "s", "text": "Hi?"}).json()
        assert out["error"] == "brain" and out["reply_text"] == "No AWS credentials found."
        status = client.get("/api/status").json()
        assert status["brain_error"] == "No AWS credentials found."


# ---------- account linking (like Alexa) ----------

def test_unlinked_simulator_asks_to_link_and_tokens_never_reach_the_browser(module_server):
    app, account = make_sim(module_server)
    with TestClient(app) as client:
        status = client.get("/api/status").json()
        assert status["account"] == {"linked": False, "configured": True}
        assert status["server"] == {"reachable": True}
        out = client.post("/api/utterance", json={"session_id": "s", "text": "When is my sister's birthday?"}).json()
        assert out["error"] == "unlinked" and "Link account" in out["reply_text"]
        assert client.get("/api/memory").status_code == 401

        r = link(client)
        assert r.headers["location"] == "/?linked=1"
        status = client.get("/api/status").json()
        assert status["account"]["linked"] and status["account"]["scopes"] == ["memory.read", "memory.write"]
        assert len(status["server"]["tools"]) == 7
        out = client.post("/api/utterance", json={"session_id": "s", "text": "When is my sister's birthday?"})
        memory_page = client.get("/api/memory")
        for response in (client.get("/api/status"), out, memory_page, r):
            assert account._tokens.access_token not in response.text
            assert account._tokens.refresh_token not in response.text
            assert "pm_at_" not in response.text and "pm_rt_" not in response.text
        assert "November 2" in out.json()["reply_text"]

        assert client.post("/api/unlink", headers=ADMIN).json() == {"linked": False}
        assert client.get("/api/memory").status_code == 401


def test_denied_link_stays_unlinked(module_server):
    app, account = make_sim(module_server)
    with TestClient(app) as client:
        r = link(client, approve=False)
        assert "link_error=Linking+was+cancelled" in r.headers["location"]
        assert not account.linked


def test_forged_callback_is_rejected(module_server):
    app, account = make_sim(module_server)
    with TestClient(app) as client:
        r = client.get("/oauth/callback", params={"code": "x", "state": "never-issued"}, follow_redirects=False)
        assert "link_error=" in r.headers["location"] and not account.linked


def test_access_token_refreshes_automatically(module_server):
    app, account = make_sim(module_server)
    with TestClient(app) as client:
        link(client)
        old = account._tokens
        account._tokens = oc.Tokens(old.access_token, old.refresh_token, 0, old.scope)  # pretend it expired
        out = client.post("/api/utterance", json={"session_id": "s", "text": "When is my sister's birthday?"}).json()
        assert "November 2" in out["reply_text"]
        assert account._tokens.refresh_token != old.refresh_token  # rotated
        assert account._tokens.access_token != old.access_token


def test_read_only_link_cannot_save(module_server):
    app, _ = make_sim(module_server)
    with TestClient(app) as client:
        link(client, scopes=["memory.read"])
        out = client.post("/api/utterance", json={"session_id": "s",
                                                  "text": "Remember that I hate jazz."}).json()
        assert "insufficient_scope" in str(mcp_only(out)[0]["result"])


# ---------- Agent Skill: progressive disclosure ----------

def test_skill_body_loads_when_memory_tools_are_first_used(chat):
    out = chat("When is my sister's birthday?")
    assert [s["tool"] for s in out["all_trace"]] == ["skill loaded: portable-memory", "recall"]
    out = chat("Remember that I hate jazz.")
    assert [s["tool"] for s in out["all_trace"]] == ["propose_memory"]  # loaded once per session


def test_system_prompt_has_skill_metadata_then_body():
    from simulator.agent import Agent
    from simulator.mcp_link import ServerProfile

    agent = Agent(link=None, brain=ScriptedBrain())
    profile = ServerProfile("portable-memory", "SERVER CONTRACT", [])
    before = agent.system_prompt(profile)
    assert "- portable-memory: Use the Portable Memory MCP server" in before
    assert "## Updates: one yes" not in before and "SERVER CONTRACT" in before
    after = agent.system_prompt(profile, ["portable-memory"])
    assert "## Updates: one yes" in after and "references/TOOLS.md" in after
    # the hardcoded prompt no longer duplicates the skill's rules
    assert "confirmation_prompt" not in before.replace("SERVER CONTRACT", "")


def test_skill_references_are_read_on_demand_and_confined():
    from simulator.agent import Agent

    agent = Agent(link=None, brain=ScriptedBrain())
    ok = agent._read_reference({"skill": "portable-memory", "path": "references/TOOLS.md"})
    assert "## confirm_memory(pending_id, confirmed, replaces?)" in ok["content"]
    for bad in ("../SKILL.md", "/etc/passwd", "references/../../server.py", "SKILL.md"):
        assert "error" in agent._read_reference({"skill": "portable-memory", "path": bad})
    assert "error" in agent._read_reference({"skill": "nope", "path": "references/TOOLS.md"})


def test_skill_follows_the_agentskills_spec():
    from simulator.skills import SKILLS_DIR, load_skills, parse_frontmatter

    skills = load_skills()
    assert [s.name for s in skills] == ["portable-memory"]
    skill = skills[0]
    fields, body = parse_frontmatter((skill.root / "SKILL.md").read_text())
    assert set(fields) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    assert len(fields["description"]) <= 1024 and len(fields["compatibility"]) <= 500
    assert len(body.splitlines()) < 500 and len(body) / 4 < 5000  # ~tokens
    assert skill.mcp_server == "portable-memory" and skill.root.parent == SKILLS_DIR
    for ref in ("references/TOOLS.md", "references/DIALOGUES.md"):
        assert f"]({ref})" in body and (skill.root / ref).is_file()


def test_model_reasoning_is_never_spoken():
    from simulator.agent import spoken_text

    msg = {"role": "assistant", "content": [
        {"text": "<thinking> I will ask the user if it is still true. </thinking> Is that still true?"}]}
    assert spoken_text(msg) == "Is that still true?"
    assert spoken_text({"role": "assistant", "content": [{"text": "<thinking>cut off"}]}) == ""


# ---------- MCP tool schema -> Bedrock toolSpec ----------

def test_tool_schema_converts_to_bedrock_tool_spec():
    tool = Tool(name="recall", description="Look up memories.", input_schema={
        "type": "object", "title": "recallArguments",
        "properties": {
            "query": {"title": "Query", "type": "string"},
            "category": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Category"},
            "title": {"type": "string", "title": "Title"},  # an argument literally named "title"
        },
        "required": ["query"],
    })
    assert to_bedrock_tool(tool) == {"toolSpec": {
        "name": "recall", "description": "Look up memories.",
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "title": {"type": "string"},
            },
            "required": ["query"],
        }},
    }}


def test_tool_without_arguments_or_description_still_converts():
    spec = to_bedrock_tool(Tool(name="list_due_reviews", input_schema={"type": "object"}))["toolSpec"]
    assert spec["description"] == "list_due_reviews"  # Bedrock requires a non-empty description
    assert spec["inputSchema"]["json"] == {"type": "object", "properties": {}}


def test_invalid_tool_name_is_rejected():
    with pytest.raises(ValueError):
        to_bedrock_tool(Tool(name="bad name!", input_schema={"type": "object"}))


# ---------- BedrockBrain error handling (no network) ----------

class _FakeBedrock:
    class meta:
        region_name = "us-east-1"

    def __init__(self, exc):
        self.exc = exc

    def converse(self, **kwargs):
        raise self.exc


def test_bedrock_sdk_setup_errors_are_clear(monkeypatch):
    import boto3
    from botocore.exceptions import MissingDependencyException

    def fail(*args, **kwargs):
        raise MissingDependencyException(msg="needs botocore[crt]")

    monkeypatch.setattr(boto3, "client", fail)
    with pytest.raises(BrainError, match="botocore\\[crt\\]"):
        BedrockBrain("some-model", "us-east-1")


def test_bedrock_missing_model_id_is_a_clear_error():
    with pytest.raises(BrainError, match="BEDROCK_MODEL_ID"):
        BedrockBrain("", client=_FakeBedrock(None))


@pytest.mark.parametrize("exc, expected", [
    (NoCredentialsError(), "No AWS credentials"),
    (ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no access"}}, "Converse"),
     "Enable model access"),
    (ClientError({"Error": {"Code": "ValidationException", "Message": "bad model"}}, "Converse"),
     "bad model"),
])
def test_bedrock_errors_become_readable_messages(exc, expected):
    brain = BedrockBrain("some-model", client=_FakeBedrock(exc))
    with pytest.raises(BrainError, match=expected):
        brain.converse("system", [{"role": "user", "content": [{"text": "hi"}]}], {"tools": []})
