"""The simulator's "Alexa brain": decides which MCP tools to call and what to say.

Every brain speaks the Amazon Bedrock Converse format (messages with text,
toolUse and toolResult blocks), so the agent loop is identical for all of them.

* BedrockBrain  - Amazon Bedrock Runtime Converse API with tool use (boto3).
* ScriptedBrain - deterministic rules for the golden demo conversations
                  (docs/demo-script.md); runs offline and in tests.
"""
from __future__ import annotations

import json
import re
from itertools import count
from typing import Any, Protocol

from memory.speech import IRREGULAR_PAST, MODALS, lower_first, to_you


class BrainError(Exception):
    """A problem the UI should show as is (credentials, model access, ...)."""


class Brain(Protocol):
    name: str
    model_id: str | None

    def converse(self, system: str, messages: list[dict], tool_config: dict) -> dict:
        """Return a Converse-shaped response: {"output": {"message": ...}, "stopReason": ...}."""
        ...


class UnavailableBrain:
    """Stands in when the configured brain can't be built; every turn reports why."""

    def __init__(self, name: str, reason: str, model_id: str | None = None):
        self.name, self.reason, self.model_id = name, reason, model_id

    def converse(self, system, messages, tool_config):
        raise BrainError(self.reason)


# ---------------------------------------------------------------- Bedrock

class BedrockBrain:
    """Amazon Bedrock Runtime, Converse API with tool use.

    Credentials come from the default AWS credential chain (environment, profile,
    SSO, instance role). This class never reads, logs or stores them.
    """

    name = "bedrock"

    def __init__(self, model_id: str, region: str | None = None, max_tokens: int = 512, client=None):
        if not model_id:
            raise BrainError("BEDROCK_MODEL_ID is not set. Pick a Bedrock model (or inference "
                             "profile) that supports tool use, or run with SIM_BRAIN=scripted.")
        self.model_id = model_id
        self.max_tokens = max_tokens
        if client is None:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import BotoCoreError, MissingDependencyException, NoRegionError

            try:
                # Bedrock calls occasionally stall; a voice turn that waits a minute is
                # useless, so fail fast (15 s read timeout, one retry) and say so.
                client = boto3.client("bedrock-runtime", region_name=region, config=Config(
                    connect_timeout=5, read_timeout=15, retries={"max_attempts": 2, "mode": "standard"}))
            except NoRegionError as e:
                raise BrainError("No AWS region configured. Set AWS_REGION (e.g. us-east-1).") from e
            except MissingDependencyException as e:
                raise BrainError("Your AWS credentials need an extra SDK package (for example `aws login` "
                                 "credentials need botocore[crt]). Run: pip install -r requirements.txt") from e
            except BotoCoreError as e:
                raise BrainError(f"AWS SDK setup failed: {type(e).__name__}") from e
        self._client = client

    def converse(self, system: str, messages: list[dict], tool_config: dict) -> dict:
        from botocore.exceptions import (BotoCoreError, ClientError, EndpointConnectionError,
                                         NoCredentialsError, PartialCredentialsError)

        region = self._client.meta.region_name
        try:
            return self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=messages,
                toolConfig=tool_config,
                inferenceConfig={"maxTokens": self.max_tokens},
            )
        except (NoCredentialsError, PartialCredentialsError) as e:
            raise BrainError("No AWS credentials found. Configure the default AWS credential chain "
                             "(for example `aws configure` or AWS_PROFILE) and restart the simulator, "
                             "or run it with SIM_BRAIN=scripted.") from e
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            message = e.response.get("Error", {}).get("Message", "")
            if code == "AccessDeniedException":
                raise BrainError(f"Bedrock denied access to {self.model_id} in {region}. Enable model "
                                 f"access in the Bedrock console and check IAM permissions "
                                 f"(bedrock:InvokeModel). AWS said: {message}") from e
            if code in ("ResourceNotFoundException", "ValidationException"):
                raise BrainError(f"Bedrock rejected the request for {self.model_id} in {region} "
                                 f"({code}): {message}") from e
            if code in ("ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException"):
                raise BrainError(f"Bedrock is busy ({code}). Try again in a moment.") from e
            if code in ("UnrecognizedClientException", "ExpiredTokenException", "InvalidSignatureException"):
                raise BrainError(f"AWS rejected the credentials ({code}). Refresh them and restart.") from e
            raise BrainError(f"Bedrock error {code}: {message}") from e
        except EndpointConnectionError as e:
            raise BrainError(f"Can't reach Bedrock in {region}. Check the network and AWS_REGION.") from e
        except BotoCoreError as e:
            raise BrainError(f"AWS SDK error: {type(e).__name__}") from e


# ---------------------------------------------------------------- Scripted

YES = {"yes", "yeah", "yep", "sure", "correct", "right", "please do", "do it", "ok", "okay",
       "yes please", "it is", "still true", "that's right"}
NO = {"no", "nope", "no thanks", "don't", "do not", "never mind", "not really", "cancel"}
REVIEW_ASK = re.compile(r"\b(anything|something)\b.*\b(update|out of date|check|review)\b|\bout of date\b")
_THIRD = {"am": "is", "have": "has", "do": "does", "go": "goes", "are": "is", "was": "was"}


def _normal(text: str) -> str:
    return re.sub(r"[^a-z' ]", "", text.lower()).strip()


def _third_person_verb(verb: str) -> str:
    v = verb.lower()
    if v in _THIRD:
        return _THIRD[v]
    if v in IRREGULAR_PAST or v in MODALS or v.endswith("ed") or v.endswith("s"):
        return verb
    if re.search(r"(sh|ch|x|z|o)$", v):
        return verb + "es"
    if re.search(r"[^aeiou]y$", v):
        return verb[:-1] + "ies"
    return verb + "s"


def to_fact(utterance: str) -> str:
    """'Remember that I hate jazz.' -> 'The user hates jazz.' (first to third person)."""
    text = utterance.strip().rstrip(".!?")
    text = re.sub(r"^(please\s+)?(can you\s+)?remember(\s+that)?\s+", "", text, flags=re.I)
    words = text.split()
    out: list[str] = []
    seen_subject = False
    i = 0
    while i < len(words):
        w = words[i]
        bare = w.rstrip(",;")
        tail = w[len(bare):]
        lower = bare.lower()
        if lower in ("i", "i'm", "i've", "i'll", "i'd"):
            subject = "they" if seen_subject else "the user"
            seen_subject = True
            if lower == "i'm":
                out.append(f"{subject} {'are' if subject == 'they' else 'is'}{tail}")
            elif lower == "i've":
                out.append(f"{subject} {'have' if subject == 'they' else 'has'}{tail}")
            elif lower == "i'll":
                out.append(f"{subject} will{tail}")
            elif lower == "i'd":
                out.append(f"{subject} would{tail}")
            else:
                out.append(subject + tail)
                if subject == "the user" and i + 1 < len(words) and not tail:
                    nxt = words[i + 1].rstrip(",;")
                    out.append(_third_person_verb(nxt) + words[i + 1][len(nxt):])
                    i += 1
        elif lower == "my":
            out.append(("their" if seen_subject else "the user's") + tail)
            seen_subject = True
        elif lower == "me":
            out.append("them" + tail)
        else:
            out.append(w)
        i += 1
    fact = " ".join(out)
    if not seen_subject and re.match(r"^the\s+(?!user\b)", fact, re.I):
        fact = "the user's " + fact[4:]  # "The kitchen contractor ..." -> "The user's kitchen contractor ..."
    return fact[0].upper() + fact[1:] + "."


def _category(fact: str) -> tuple[str, int | None]:
    t = fact.lower()
    if re.search(r"\b(hates?|loves?|likes?|prefers?|favou?rite|enjoys?)\b", t):
        return "preference", None
    if re.search(r"\b(appointment|meeting|tomorrow|today|monday|tuesday|wednesday|thursday|friday|"
                 r"saturday|sunday|next week|starts?|deadline|due)\b", t):
        return "active_context", 7
    if re.search(r"\b(job|work|lives?|name|birthday|allergic)\b", t):
        return "profile", None
    return "other", None


class ScriptedBrain:
    """Rule-based stand-in for the model, covering the golden demo conversations.

    It reads the same Converse history a model would: the current utterance, the
    tool results so far this turn, and the tool calls of the previous turn (to
    know which question it asked last).
    """

    name = "scripted"
    model_id = None

    def __init__(self):
        self._ids = count(1)

    # ----- reading the history -----
    @staticmethod
    def _is_utterance(msg: dict) -> bool:
        return msg["role"] == "user" and any("text" in b for b in msg["content"])

    @staticmethod
    def _calls(messages: list[dict]) -> list[dict]:
        """Tool calls (name, input, result) in order, from toolUse/toolResult pairs."""
        uses, calls = {}, []
        for msg in messages:
            for block in msg["content"]:
                if "toolUse" in block:
                    uses[block["toolUse"]["toolUseId"]] = block["toolUse"]
                elif "toolResult" in block:
                    tr = block["toolResult"]
                    use = uses.get(tr["toolUseId"], {})
                    content = tr.get("content") or [{}]
                    result: Any = content[0].get("json", content[0].get("text"))
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except ValueError:
                            pass
                    calls.append({"name": use.get("name"), "input": use.get("input", {}), "result": result})
        return calls

    def _open_question(self, prior: list[dict]) -> tuple | None:
        """What did we ask at the end of the previous turn?"""
        if not prior:
            return None
        last = prior[-1]
        r = last["result"] if isinstance(last["result"], dict) else {}
        if last["name"] == "propose_memory" and "pending_id" in r:
            if r.get("replaces"):
                return ("update", r["pending_id"], r["replaces"])
            return ("confirm", r["pending_id"])
        if last["name"] == "supersede_memory" and "pending_id" in r:
            return ("confirm", r["pending_id"])
        if last["name"] == "list_due_reviews" and r.get("count"):
            return ("review", r["memories"][0])
        return None

    # ----- deciding -----
    def _plan(self, utterance: str, question: tuple | None, done: list[dict]):
        """Return ("tool", name, input) or ("say", text)."""
        u = _normal(utterance)
        yes = u in YES or u.startswith("yes")
        no = u in NO or u.startswith("no ") or u == "no"
        n = len(done)
        last = done[-1]["result"] if done and isinstance(done[-1]["result"], dict) else {}
        if done and "error" in last:
            return ("say", last.get("spoken_answer") or "Sorry, something went wrong.")

        kind = question[0] if question else None
        if kind == "confirm" and (yes or no):
            if n == 0:
                return ("tool", "confirm_memory", {"pending_id": question[1], "confirmed": yes})
            return ("say", last["spoken_answer"])

        if kind == "update" and (yes or no):
            # One answer settles it: yes saves the new fact and retires the old one.
            _, pending, old_id = question
            if n == 0:
                args = {"pending_id": pending, "confirmed": yes}
                if yes:
                    args["replaces"] = old_id
                return ("tool", "confirm_memory", args)
            return ("say", last["spoken_answer"])

        if kind == "review" and utterance.strip():
            memory = question[1]
            if yes:
                if n == 0:
                    return ("tool", "resolve_review", {"memory_id": memory["id"], "still_true": True,
                                                       "review_in_days": 30})
                return ("say", last["spoken_answer"])
            if no:
                if n == 0:
                    return ("tool", "resolve_review", {"memory_id": memory["id"], "still_true": False})
                return ("say", last["spoken_answer"])
            # New information instead of yes/no: retire the old fact, propose the new one.
            fact = to_fact(utterance)
            category, review = _category(fact)
            if n == 0:
                return ("tool", "resolve_review", {"memory_id": memory["id"], "still_true": False})
            if n == 1:
                args = {"fact": fact, "category": category}
                if review:
                    args["review_in_days"] = review
                return ("tool", "propose_memory", args)
            return ("say", f"Thanks for the update. {last['confirmation_prompt']}")

        if yes or no:
            return ("say", "Sorry, I'm not sure what you're answering. What would you like to do?")

        if utterance.strip().endswith("?"):
            if REVIEW_ASK.search(u):
                if n == 0:
                    return ("tool", "list_due_reviews", {})
                if not last.get("count"):
                    return ("say", "Everything I remember looks up to date.")
                first = last["memories"][0]
                more = f" That's one of {last['count']} things to check." if last["count"] > 1 else ""
                return ("say", f"I have a note that {lower_first(to_you(first['fact'].rstrip('.')))}. "
                               f"Is that still true?{more}")
            if n == 0:
                return ("tool", "recall", {"query": utterance.strip()})
            if not last.get("memories"):
                return ("say", "I don't have anything about that yet.")
            return ("say", to_you(last["memories"][0]["fact"]))

        # A statement: something to remember.
        fact = to_fact(utterance)
        category, review = _category(fact)
        if n == 0:
            args = {"fact": fact, "category": category}
            if review:
                args["review_in_days"] = review
            return ("tool", "propose_memory", args)
        return ("say", last["confirmation_prompt"])  # already phrased for speech by the server

    def converse(self, system: str, messages: list[dict], tool_config: dict) -> dict:
        starts = [i for i, m in enumerate(messages) if self._is_utterance(m)]
        if not starts:
            raise BrainError("No user utterance in the conversation.")
        current = starts[-1]
        previous = starts[-2] if len(starts) > 1 else current
        utterance = next(b["text"] for b in messages[current]["content"] if "text" in b)
        question = self._open_question(self._calls(messages[previous:current]))
        action = self._plan(utterance, question, self._calls(messages[current + 1:]))
        if action[0] == "tool":
            block = {"toolUse": {"toolUseId": f"scripted-{next(self._ids)}", "name": action[1],
                                 "input": action[2]}}
            return {"output": {"message": {"role": "assistant", "content": [block]}},
                    "stopReason": "tool_use"}
        return {"output": {"message": {"role": "assistant", "content": [{"text": action[1]}]}},
                "stopReason": "end_turn"}
