"""Pick the memories relevant to a question.

KeywordRetriever works offline and is the fallback. BedrockRetriever asks a
model on Amazon Bedrock (Converse API) to choose the relevant records and to
phrase a short answer that sounds natural when spoken aloud.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from memory.schema import MemoryRecord

log = logging.getLogger(__name__)

_STOP = set(
    "a an the is are was were be to of in on at for and or my me i you your do does did "
    "what whats when where who how about have has had it this that with any".split()
)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    # crude stemming so "meetings" matches "meeting"
    return {w.rstrip("s") for w in words if w not in _STOP and len(w) > 1}


@dataclass
class RetrievalResult:
    records: list[MemoryRecord]
    spoken_answer: str | None  # None when the retriever does not write answers
    engine: str


class Retriever(Protocol):
    def retrieve(self, query: str, candidates: list[MemoryRecord], limit: int) -> RetrievalResult: ...


class KeywordRetriever:
    def retrieve(self, query: str, candidates: list[MemoryRecord], limit: int) -> RetrievalResult:
        q = _tokens(query)
        scored = []
        for r in candidates:
            overlap = len(q & _tokens(f"{r.fact} {r.category} {' '.join(r.tags)}"))
            if overlap:
                scored.append((overlap, r.updated_at, r))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return RetrievalResult([r for _, _, r in scored[:limit]], None, "keyword")


_SYSTEM = """You select facts from a user's personal memory for a voice assistant.
You receive a question and a list of memory records (id + fact).
Return ONLY a JSON object, no prose and no code fences:
{"ids": [ids of the records that help answer, most relevant first],
 "spoken_answer": "one or two short sentences answering the question from those records only"}
Rules: never invent facts that are not in the records. If nothing is relevant,
return {"ids": [], "spoken_answer": null}."""


class BedrockRetriever:
    def __init__(self, model_id: str, region: str | None = None, max_candidates: int = 200):
        import boto3

        self._client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.max_candidates = max_candidates
        self._fallback = KeywordRetriever()

    def retrieve(self, query: str, candidates: list[MemoryRecord], limit: int) -> RetrievalResult:
        pool = sorted(candidates, key=lambda r: r.updated_at, reverse=True)[: self.max_candidates]
        listing = "\n".join(f"{r.id}: {r.fact}" for r in pool)
        try:
            resp = self._client.converse(
                modelId=self.model_id,
                system=[{"text": _SYSTEM}],
                messages=[{"role": "user", "content": [{"text": f"Question: {query}\n\nRecords:\n{listing}"}]}],
                inferenceConfig={"maxTokens": 400, "temperature": 0},
            )
            text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
            text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
            data = json.loads(text)
            by_id = {r.id: r for r in pool}
            chosen = [by_id[i] for i in data.get("ids", []) if i in by_id][:limit]
            return RetrievalResult(chosen, data.get("spoken_answer") if chosen else None, "bedrock")
        except Exception:  # network, permissions, malformed JSON...
            log.exception("Bedrock retrieval failed; falling back to keyword search")
            result = self._fallback.retrieve(query, candidates, limit)
            result.engine = "keyword (bedrock fallback)"
            return result
