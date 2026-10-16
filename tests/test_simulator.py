"""Simulated Alexa+: golden demo conversations end to end (ScriptedBrain + real server)."""
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from mcp.types import Tool
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory.schema import from_markdown  # noqa: E402
from simulator.app import build_app  # noqa: E402
from simulator.brains import BedrockBrain, BrainError, ScriptedBrain, UnavailableBrain  # noqa: E402
from simulator.mcp_link import McpLink, to_bedrock_tool  # noqa: E402
from demo_data.seed import spoken_date  # noqa: E402
from tests.conftest import TEST_TODAY, TOKEN  # noqa: E402

ADMIN = {"X-Admin-Token": "admin-test"}


@pytest.fixture(scope="module")
def sim(module_server):
    app = build_app(McpLink(f"{module_server}/mcp", TOKEN), ScriptedBrain(), admin_token="admin-test")
    with TestClient(app) as client:
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
        for step in out["trace"]:
            assert step["mcp_latency_ms"] < 500, step
        return out

    return say


def memory(sim) -> dict:
    r = sim.get("/api/memory")
    assert r.status_code == 200
    return {rec.id: rec for rec in from_markdown(r.json()["markdown"])}


def tools(out) -> list[str]:
    return [step["tool"] for step in out["trace"]]


def test_status_reports_server_tools_and_brain(sim):
    s = sim.get("/api/status").json()
    assert s["brain"] == "scripted"
    assert s["server"]["has_instructions"]
    assert set(s["server"]["tools"]) == {"recall", "propose_memory", "supersede_memory", "confirm_memory",
                                         "list_due_reviews", "resolve_review"}


def test_index_page_is_served(sim):
    r = sim.get("/")
    assert r.status_code == 200 and "Simulated Alexa+" in r.text
    assert sim.get("/static/app.js").status_code == 200


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


def test_reset_needs_admin_token(sim):
    assert sim.post("/api/reset", json={}).status_code == 403


def test_empty_utterance_is_rejected(sim):
    assert sim.post("/api/utterance", json={"session_id": "x", "text": "  "}).status_code == 400


def test_brain_error_is_reported_and_history_stays_valid(module_server):
    app = build_app(McpLink(f"{module_server}/mcp", TOKEN),
                    UnavailableBrain("bedrock", "No AWS credentials found."), admin_token="admin-test")
    with TestClient(app) as client:
        out = client.post("/api/utterance", json={"session_id": "s", "text": "Hi?"}).json()
        assert out["error"] == "brain" and out["reply_text"] == "No AWS credentials found."
        status = client.get("/api/status").json()
        assert status["brain_error"] == "No AWS credentials found."


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
