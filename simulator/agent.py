"""Plays Alexa+: a Converse tool-use loop between the brain and the MCP server."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import anyio

from .brains import Brain, BrainError
from .mcp_link import McpLink, NotLinked, ServerProfile, to_bedrock_tool_config
from .skills import READ_REFERENCE_TOOL, Skill, load_skills, reference_tool_spec

log = logging.getLogger(__name__)
# Some models (e.g. Amazon Nova Pro) put their reasoning in <thinking> tags; never speak it.
_THINKING = re.compile(r"<thinking>.*?(</thinking>|$)", re.S | re.I)
# ...and sometimes wraps the spoken part in <speech> tags: keep the words, drop the tags.
_SPEECH_TAGS = re.compile(r"</?speech>", re.I)

# Only the persona lives here. How to use the memory server comes from its own
# `instructions` (the minimal contract) and from the Agent Skill (skills/), loaded
# progressively; don't duplicate those rules in this prompt.
SYSTEM_TEMPLATE = """\
You are Alexa+, a voice assistant, running in a simulator. Everything you write is
spoken aloud by a speaker. Use the tools of connected services when a request needs
them, and don't invent facts the tools did not return. This device has a screen: a memory
export appears in its "Your memory file" panel, where the user can download or copy it.

The memory server's own instructions. Follow them exactly:
<server_instructions>
{instructions}
</server_instructions>
{skills}"""


@dataclass
class Session:
    messages: list[dict] = field(default_factory=list)
    profile: ServerProfile | None = None
    loaded_skills: list[str] = field(default_factory=list)  # names whose SKILL.md body is in context
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def spoken_text(message: dict) -> str:
    """The part of a model message that is meant to be said aloud."""
    text = " ".join(b["text"] for b in message["content"] if "text" in b)
    return re.sub(r"\s+", " ", _SPEECH_TAGS.sub("", _THINKING.sub("", text))).strip()


def _unwrap(exc: BaseException) -> BaseException:
    """The MCP client runs in a task group, which wraps errors in an ExceptionGroup."""
    while hasattr(exc, "exceptions") and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


class Agent:
    def __init__(self, link: McpLink, brain: Brain, max_steps: int = 8, max_turns: int = 20,
                 skills: list[Skill] | None = None, model_cap=None):
        self.link = link
        self.brain = brain
        self.model_cap = model_cap  # DailyCap: model calls allowed per UTC day (public demo)
        self.skills = load_skills() if skills is None else skills
        self.max_steps = max_steps
        self.max_turns = max_turns  # user utterances kept as interaction context
        self.sessions: dict[str, Session] = {}

    def forget(self, session_id: str | None = None) -> None:
        if session_id is None:
            self.sessions.clear()
        else:
            self.sessions.pop(session_id, None)

    def system_prompt(self, profile: ServerProfile, loaded: list[str] = ()) -> str:
        blocks = []
        if self.skills:  # level 1: name + description of every skill
            listing = "\n".join(f"- {s.name}: {s.description}" for s in self.skills)
            blocks.append("\nSkills (their instructions are added here automatically the first time "
                          f"you use the matching service's tools):\n{listing}\n")
        for skill in self.skills:  # level 2: the SKILL.md body, once the session uses its tools
            if skill.name in loaded:
                refs = ", ".join(skill.references())
                blocks.append(f'\n<skill name="{skill.name}">\n{skill.body()}\n</skill>\n'
                              f"Reference files ({READ_REFERENCE_TOOL}): {refs}\n")
        return SYSTEM_TEMPLATE.format(instructions=profile.instructions.strip(), skills="".join(blocks))

    def _skills_for(self, profile: ServerProfile) -> list[Skill]:
        return [s for s in self.skills if s.mcp_server in (None, profile.name)]

    def _read_reference(self, args: dict) -> dict:
        skill = next((s for s in self.skills if s.name == args.get("skill")), None)
        try:
            if not skill:
                raise FileNotFoundError(f"unknown skill {args.get('skill')!r}")
            return {"path": args.get("path"), "content": skill.read_reference(str(args.get("path")))}
        except FileNotFoundError as e:
            return {"error": str(e)}

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
                server_tools = {t.name for t in profile.tools}
                tool_config = to_bedrock_tool_config(profile.tools)
                if self.skills:
                    tool_config["tools"].append(reference_tool_spec(self.skills))
                for _ in range(self.max_steps):
                    system = self.system_prompt(profile, session.loaded_skills)
                    if self.model_cap and self.brain.name != "scripted" and not self.model_cap.take():
                        raise BrainError("The demo has reached today's limit for answers. "
                                         "Please try again tomorrow.")
                    t0 = time.perf_counter()
                    response = await anyio.to_thread.run_sync(
                        self.brain.converse, system, session.messages, tool_config)
                    model_ms += (time.perf_counter() - t0) * 1000
                    message = response["output"]["message"]
                    session.messages.append(message)
                    uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
                    if response.get("stopReason") != "tool_use" or not uses:
                        reply = spoken_text(message)
                        break
                    results = []
                    if server_tools & {u["name"] for u in uses}:  # first use of the memory tools
                        for skill in self._skills_for(profile):
                            if skill.name not in session.loaded_skills:
                                session.loaded_skills.append(skill.name)
                                trace.append({"tool": f"skill loaded: {skill.name}", "kind": "skill",
                                              "arguments": {}, "result": f"{skill.name}/SKILL.md added to "
                                              "the instructions", "mcp_latency_ms": None})
                    for use in uses:
                        if use["name"] == READ_REFERENCE_TOOL:  # level 3, local file, no MCP
                            result = self._read_reference(use.get("input") or {})
                            trace.append({"tool": READ_REFERENCE_TOOL, "kind": "skill",
                                          "arguments": use.get("input") or {}, "result": result,
                                          "mcp_latency_ms": None})
                            results.append({"toolResult": {"toolUseId": use["toolUseId"],
                                                           "content": [{"json": result}],
                                                           "status": "error" if "error" in result else "success"}})
                            continue
                        call = await self.link.call_tool(client, use["name"], use.get("input") or {})
                        trace.append({"tool": use["name"], "kind": "mcp", "arguments": use.get("input") or {},
                                      "result": call.result, "mcp_latency_ms": call.mcp_latency_ms,
                                      **({"file": call.file} if call.file else {})})  # file: screen only
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
            if isinstance(err, NotLinked):
                return {"reply_text": "Your memory isn't linked yet. Use Link account to connect it.",
                        "trace": trace, "model_latency_ms": round(model_ms, 1), "error": "unlinked"}
            if isinstance(err, BrainError):
                return {"reply_text": str(err), "trace": trace, "model_latency_ms": round(model_ms, 1),
                        "error": "brain"}
            log.error("Turn failed", exc_info=err)
            return {"reply_text": f"I can't reach the memory server right now ({type(err).__name__}).",
                    "trace": trace, "model_latency_ms": round(model_ms, 1), "error": "mcp"}
        session.messages = self._trim(session.messages)
        return {"reply_text": reply or "Okay.", "trace": trace, "model_latency_ms": round(model_ms, 1)}
