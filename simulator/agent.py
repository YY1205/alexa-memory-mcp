"""Plays Alexa+: a Converse tool-use loop between the brain and the MCP server."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import anyio

from .brains import Brain, BrainError
from .mcp_link import McpLink, ServerProfile, to_bedrock_tool_config

log = logging.getLogger(__name__)

SYSTEM_TEMPLATE = """\
You are Alexa+, a voice assistant, running in a simulator. Everything you write is
spoken aloud by a speaker.

How to talk:
- Reply in one or two short, natural spoken sentences. No markdown, lists, emoji or headings.
- Talk to the user as "you". Never read ids (like MEM-0004 or pending ids) aloud.
- When a tool result gives you a sentence to read back (confirmation_prompt), say it
  naturally in the second person and wait for the user's answer.
- Use the memory tools whenever the user asks about themselves or tells you something
  to remember, corrects something, or asks what needs updating. Don't invent facts
  that the tools did not return.

The memory server's own instructions. Follow them exactly:
<server_instructions>
{instructions}
</server_instructions>
"""


@dataclass
class Session:
    messages: list[dict] = field(default_factory=list)
    profile: ServerProfile | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _unwrap(exc: BaseException) -> BaseException:
    """The MCP client runs in a task group, which wraps errors in an ExceptionGroup."""
    while hasattr(exc, "exceptions") and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


class Agent:
    def __init__(self, link: McpLink, brain: Brain, max_steps: int = 8, max_turns: int = 20):
        self.link = link
        self.brain = brain
        self.max_steps = max_steps
        self.max_turns = max_turns  # user utterances kept as interaction context
        self.sessions: dict[str, Session] = {}

    def forget(self, session_id: str | None = None) -> None:
        if session_id is None:
            self.sessions.clear()
        else:
            self.sessions.pop(session_id, None)

    @staticmethod
    def system_prompt(profile: ServerProfile) -> str:
        return SYSTEM_TEMPLATE.format(instructions=profile.instructions.strip())

    def _trim(self, messages: list[dict]) -> list[dict]:
        starts = [i for i, m in enumerate(messages)
                  if m["role"] == "user" and any("text" in b for b in m["content"])]
        if len(starts) <= self.max_turns:
            return messages
        return messages[starts[-self.max_turns]:]  # cut at a turn boundary keeps tool pairs intact

    async def handle(self, session_id: str, text: str) -> dict:
        session = self.sessions.setdefault(session_id, Session())
        async with session.lock:
            return await self._turn(session, text.strip())

    async def _turn(self, session: Session, text: str) -> dict:
        trace: list[dict] = []
        model_ms = 0.0
        history_len = len(session.messages)
        session.messages.append({"role": "user", "content": [{"text": text}]})
        try:
            async with self.link.connect() as client:
                if session.profile is None:  # new session: discover tools + instructions
                    session.profile = await self.link.profile(client)
                profile = session.profile
                system = self.system_prompt(profile)
                tool_config = to_bedrock_tool_config(profile.tools)
                for _ in range(self.max_steps):
                    t0 = time.perf_counter()
                    response = await anyio.to_thread.run_sync(
                        self.brain.converse, system, session.messages, tool_config)
                    model_ms += (time.perf_counter() - t0) * 1000
                    message = response["output"]["message"]
                    session.messages.append(message)
                    uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
                    if response.get("stopReason") != "tool_use" or not uses:
                        reply = " ".join(b["text"] for b in message["content"] if "text" in b).strip()
                        break
                    results = []
                    for use in uses:
                        call = await self.link.call_tool(client, use["name"], use.get("input") or {})
                        trace.append({"tool": use["name"], "arguments": use.get("input") or {},
                                      "result": call.result, "mcp_latency_ms": call.mcp_latency_ms})
                        content = [{"json": call.result}] if isinstance(call.result, dict) \
                            else [{"text": str(call.result)}]
                        results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": content,
                                                       "status": "error" if call.is_error else "success"}})
                    session.messages.append({"role": "user", "content": results})
                else:
                    reply = "Sorry, I got stuck on that one."
        except Exception as exc:
            del session.messages[history_len:]  # keep the history a valid conversation
            err = _unwrap(exc)
            if isinstance(err, BrainError):
                return {"reply_text": str(err), "trace": trace, "model_latency_ms": round(model_ms, 1),
                        "error": "brain"}
            log.error("Turn failed", exc_info=err)
            return {"reply_text": f"I can't reach the memory server right now ({type(err).__name__}).",
                    "trace": trace, "model_latency_ms": round(model_ms, 1), "error": "mcp"}
        session.messages = self._trim(session.messages)
        return {"reply_text": reply or "Okay.", "trace": trace, "model_latency_ms": round(model_ms, 1)}
