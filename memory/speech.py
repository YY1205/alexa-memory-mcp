"""Turn stored facts into sentences a voice assistant can read out as is.

Facts are stored in the third person ("The user's sister ..."), which keeps the file
readable and portable. Anything the server asks the assistant to say aloud is
rephrased to the second person ("Your sister ..."), so any model can read it
verbatim.
"""
from __future__ import annotations

import re

IRREGULAR_PAST = {"got", "was", "had", "went", "made", "did", "bought", "moved", "left", "found",
                  "took", "began", "came", "saw", "met", "sold", "lost", "won", "paid"}
MODALS = {"will", "can", "should", "would", "could", "might", "must", "may", "now", "just",
          "also", "still", "never", "always", "really", "usually"}
# plural nouns that may follow "and" in a fact; leave them alone
NOT_VERBS = {"his", "hers", "its", "this", "has", "was", "is", "us", "plans", "kids", "friends",
             "parents", "cats", "dogs", "tools", "times", "days", "weeks", "months", "years"}
_SECOND = {"is": "are", "was": "were", "has": "have", "does": "do", "goes": "go"}


def second_person_verb(verb: str) -> str:
    v = verb.lower()
    if v in _SECOND:
        return _SECOND[v]
    if v in MODALS or v in IRREGULAR_PAST or v.endswith("ed") or not v.endswith("s") or v.endswith("ss"):
        return verb
    if v.endswith("ies"):
        return verb[:-3] + "y"
    if re.search(r"(sh|ch|x|z|o)es$", v):
        return verb[:-2]
    return verb[:-1]


def to_you(text: str) -> str:
    """'The user's sister ...' -> 'Your sister ...' (third to second person)."""
    s = re.sub(r"\bthe user's\b", "your", text, flags=re.I)
    s = re.sub(r"\bthe user (\w+)", lambda m: "you " + second_person_verb(m.group(1)), s, flags=re.I)
    s = re.sub(r"\bthe user\b", "you", s, flags=re.I)
    s = re.sub(r"\btheir\b", "your", s)
    s = re.sub(r"\bthey (\w+)", lambda m: "you " + second_person_verb(m.group(1)), s)
    # A second verb for the same subject: "you are learning Spanish and practices ..." ->
    # "... and practice ...". Only right after "and", and only when "you" is the subject.
    if re.match(r"^you\b", s, re.I):  # not "your sister ... and plays": other subject
        s = re.sub(r"\band ([a-z]+(?:es|s))\b(?= [a-z0-9])",
                   lambda m: "and " + second_person_verb(m.group(1))
                   if m.group(1) not in NOT_VERBS else m.group(0), s)
    return s[0].upper() + s[1:] if s else s


def lower_first(s: str) -> str:
    return s[0].lower() + s[1:] if s else s


def clause(fact: str) -> str:
    """A fact as a spoken clause: second person, no capital, no final punctuation."""
    return lower_first(to_you(fact.strip().rstrip(".!? ")))
