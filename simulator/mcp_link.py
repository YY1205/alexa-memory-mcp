"""The simulator's connection to the memory server, using the official MCP client.

The server is stateless Streamable HTTP, so the simulator opens a short-lived
client per conversation turn (initialize + calls) instead of holding a socket
open across web requests.
"""
from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import Tool

MEMORY_URI = "memory://memories.md"
_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")  # Bedrock toolSpec name rule


@dataclass
class ServerProfile:
    """What the simulator learns from initialize + tools/list."""
    name: str
    instructions: str
    tools: list[Tool]


@dataclass
class ToolCall:
    result: Any            # parsed JSON result (dict) or plain text
    is_error: bool
    mcp_latency_ms: float


class NotLinked(Exception):
    """No access token: the user has to link their account first."""


class McpLink:
    """`token` is a fixed bearer token (scripts, localhost dev); `token_provider` is an
    async callable returning the current OAuth access token (the simulator)."""

    def __init__(self, url: str, token: str | None = None,
                 token_provider: Callable[[], Awaitable[str | None]] | None = None):
        self.url = url
        self.token = token
        self.token_provider = token_provider

    @property
    def base_url(self) -> str:
        return self.url.rsplit("/mcp", 1)[0]

    async def _headers(self, required: bool = True) -> dict[str, str] | None:
        token = await self.token_provider() if self.token_provider else self.token
        if not token:
            if required and self.token_provider:
                raise NotLinked()
            return None
        return {"Authorization": f"Bearer {token}"}

    @asynccontextmanager
    async def connect(self):
        async with create_mcp_http_client(headers=await self._headers()) as http:
            async with Client(streamable_http_client(self.url, http_client=http)) as client:
                yield client

    @staticmethod
    async def profile(client: Client) -> ServerProfile:
        tools = (await client.list_tools(cache_mode="bypass")).tools
        info = client.server_info
        return ServerProfile(name=info.name if info else "?", instructions=client.instructions or "",
                             tools=list(tools))

    @staticmethod
    async def call_tool(client: Client, name: str, arguments: dict) -> ToolCall:
        t0 = time.perf_counter()
        result = await client.call_tool(name, arguments)
        latency = (time.perf_counter() - t0) * 1000
        text = "".join(getattr(c, "text", "") for c in result.content)
        try:
            parsed: Any = json.loads(text)
        except ValueError:
            parsed = text
        if parsed == "" and result.structured_content is not None:
            parsed = result.structured_content
        is_error = bool(result.is_error) or (isinstance(parsed, dict) and "error" in parsed)
        return ToolCall(parsed, is_error, round(latency, 1))

    async def read_memory(self) -> tuple[str, float]:
        async with self.connect() as client:
            t0 = time.perf_counter()
            res = await client.read_resource(MEMORY_URI, cache_mode="bypass")
            latency = (time.perf_counter() - t0) * 1000
        return res.contents[0].text, round(latency, 1)

    async def reset_demo(self) -> dict:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(f"{self.base_url}/admin/reset", headers=await self._headers(required=False))
        if r.status_code == 404:
            raise RuntimeError("The memory server has no demo reset (start it with DEMO_ADMIN=1).")
        r.raise_for_status()
        return r.json()


# ---------- MCP tool schema -> Bedrock Converse toolSpec ----------

def _clean_schema(node: Any) -> Any:
    """Drop JSON-schema "title" annotations (noise for the model), keep everything else.

    Keys inside "properties" are argument names, so a property literally called
    "title" is kept.
    """
    if isinstance(node, list):
        return [_clean_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "title":
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {name: _clean_schema(sub) for name, sub in value.items()}
        else:
            out[key] = _clean_schema(value)
    return out


def to_bedrock_tool(tool: Tool) -> dict:
    if not _TOOL_NAME.match(tool.name):
        raise ValueError(f"Tool name {tool.name!r} is not a valid Bedrock tool name")
    schema = _clean_schema(dict(tool.input_schema or {}))
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    description = (tool.description or tool.title or tool.name).strip()
    return {"toolSpec": {"name": tool.name, "description": description,
                         "inputSchema": {"json": schema}}}


def to_bedrock_tool_config(tools: list[Tool]) -> dict:
    return {"tools": [to_bedrock_tool(t) for t in tools]}
