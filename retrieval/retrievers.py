"""Pick the memories relevant to a question.

The server only does fast, offline keyword retrieval so every tool call stays
well under the Alexa+ latency budget. Alexa+ (or the web simulator's Bedrock
"brain") does the language understanding and phrases the spoken answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from memory.schema import MemoryRecord

_STOP = set(
    "a an the is are was were be to of in on at for and or my me i you your do does did "
    "what whats when where who how about have has had it this that with any".split()
    # every stored fact is phrased "The user ...", so "user" says nothing about relevance
    + ["user", "users", "they", "their", "them"]
)
# Dates are what usually *changes* in an update, so they don't make two facts the same topic.
_DATEISH = set(
    "january february march april may june july august september october november december "
    "monday tuesday wednesday thursday friday saturday sunday today tomorrow yesterday".split()
)
_NUMBER = re.compile(r"\d+(st|nd|rd|th)?")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    # crude stemming so "meetings" matches "meeting"
    return {w.rstrip("s") for w in words if w not in _STOP and len(w) > 1}


def _topic(text: str) -> set[str]:
    return {t for t in _tokens(text) if t.rstrip("s") not in _DATEISH and t not in _DATEISH
            and not _NUMBER.fullmatch(t)}


def strong_match(fact: str, candidates: list[MemoryRecord]) -> MemoryRecord | None:
    """The candidate that `fact` most likely updates, if any.

    Strong = at least two shared topic words (dates and numbers ignored) covering
    at least half of the smaller fact.
    """
    mine = _topic(fact)
    best, best_score = None, 0.0
    for r in candidates:
        theirs = _topic(r.fact)
        shared = len(mine & theirs)
        if shared < 2:
            continue
        score = shared / min(len(mine), len(theirs))
        if score >= 0.5 and score > best_score:
            best, best_score = r, score
    return best


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

