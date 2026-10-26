"""
FakeLLM — an offline stand-in for a hosted chat model.

No network, no API key, no account, no cost. Pure Python standard library.

It behaves the way real LLM APIs behave on a bad day:
  * transient errors (rate limits, timeouts)
  * output that is *almost* JSON (code fences, leading prose, truncation)
  * output that is valid JSON but wrong (dropped items, invented items,
    bad enum casing, numbers as strings)
  * an unpinned "default" model alias that silently moves to a newer model
  * token usage and latency on every response
  * tool calling (the model may ask you to run a tool before it answers)

It is NOT a model. Its classifications come from simple heuristics and are
deliberately mediocre. Do not tune prompts against it. You are evaluated on how
your system behaves around it, not on how accurate it is.

Usage
-----
    from starter.fake_llm import FakeLLM, RateLimitError, LLMTimeoutError

    llm = FakeLLM(seed=42)
    resp = llm.complete(
        system="...your prompt...",
        messages=[{"role": "user", "content": json.dumps(raw_failures)}],
        model="drvn-sim-1.0",          # omit to use the unpinned default alias
        tools=[GET_TEST_HISTORY_SPEC],  # optional, see history_tool.py
    )
    resp.content       # str | None   (None when the model asks for tools)
    resp.tool_calls    # list[dict]   [{"id", "name", "arguments": {...}}]
    resp.usage         # {"input_tokens": int, "output_tokens": int}
    resp.latency_ms    # simulated latency (the fake only sleeps a fraction)
    resp.model         # the model that actually served the request
    resp.finish_reason # "stop" | "length" | "tool_use"
    resp.request_id    # unique per call

Message format
--------------
The user message must contain the run's failures as a JSON array of objects
with at least `test_file`, `test_title`, `error_message`, `retry_count`
(the `raw_failures` list from a run file works as-is). It may also be wrapped
as {"failures": [...]} or embedded in surrounding text.

To answer a tool call, append the assistant turn and one tool turn per call:
    {"role": "assistant", "tool_calls": resp.tool_calls}
    {"role": "tool", "tool_call_id": "<id>", "content": json.dumps(result)}
then call complete() again with the extended message list.

Pricing (fictional, for cost tracking) — USD per 1M tokens:
    drvn-sim-1.0   input 0.80   output 4.00
    drvn-sim-1.1   input 1.00   output 5.00
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field

KNOWN_MODELS = ("drvn-sim-1.0", "drvn-sim-1.1")
PRICING_PER_MTOK = {
    "drvn-sim-1.0": {"input": 0.80, "output": 4.00},
    "drvn-sim-1.1": {"input": 1.00, "output": 5.00},
}


class LLMError(Exception):
    """Non-retryable error (bad request, unknown model, etc.)."""


class RateLimitError(Exception):
    """Retryable. `retry_after_s` says how long the provider wants you to wait."""

    def __init__(self, retry_after_s: float):
        super().__init__(f"429 rate limited, retry after {retry_after_s:.1f}s")
        self.retry_after_s = retry_after_s


class LLMTimeoutError(Exception):
    """Retryable. The request took too long and was abandoned."""


@dataclass
class LLMResponse:
    model: str
    content: str | None
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str = "stop"
    request_id: str = ""


def _signature(error_message: str) -> str:
    first = (error_message or "").split("\n")[0]
    if "ERR_CONNECTION_REFUSED" in first:
        return "Staging service unreachable"
    if "response.status()" in first:
        return "Server errors on write endpoints"
    if "expect(received)" in first:
        return "Assertion mismatch on business values"
    if "waitForURL" in first:
        return "Navigation did not complete"
    if "not attached to the DOM" in first:
        return "Detached element during form fill"
    if "Timeout" in first:
        return "UI element not clickable in time"
    return "Other failures"


_BASE_CLASS = {
    "Staging service unreachable": "environment",
    "Server errors on write endpoints": "environment",
    "Assertion mismatch on business values": "real_bug",
    "Navigation did not complete": "environment",
    "Detached element during form fill": "flaky",
    "UI element not clickable in time": "flaky",
    "Other failures": "flaky",
}


def _extract_failures(text: str) -> list[dict]:
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "[{":
            continue
        try:
            obj, _ = dec.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("failures"), list):
            obj = obj["failures"]
        if isinstance(obj, list) and obj and all(
            isinstance(x, dict) and "test_file" in x for x in obj
        ):
            return obj
    return []


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeLLM:
    def __init__(self, seed: int | None = None, time_scale: float = 0.05,
                 drift_after_calls: int = 25):
        """
        seed:               make faults reproducible (use a fixed seed in CI)
        time_scale:         fraction of the simulated latency actually slept
        drift_after_calls:  after this many calls, the unpinned default alias
                            silently starts routing to the newer model
        """
        self._rng = random.Random(seed)
        self._time_scale = time_scale
        self._drift_after = drift_after_calls
        self._calls = 0

    # ------------------------------------------------------------------ API
    def complete(self, system: str, messages: list[dict], model: str | None = None,
                 tools: list[dict] | None = None, max_output_tokens: int = 4096,
                 temperature: float = 0.0) -> LLMResponse:
        self._calls += 1
        rng = self._rng

        if model is None:
            served = "drvn-sim-1.1" if self._calls > self._drift_after else "drvn-sim-1.0"
        elif model in KNOWN_MODELS:
            served = model
        else:
            raise LLMError(f"400 unknown model '{model}'. Known: {', '.join(KNOWN_MODELS)}")

        if not messages or not any(m.get("role") == "user" for m in messages):
            raise LLMError("400 at least one user message is required")

        user_text = next(m["content"] for m in messages if m.get("role") == "user")
        failures = _extract_failures(user_text if isinstance(user_text, str) else json.dumps(user_text))
        if not failures:
            raise LLMError("400 no failures found in the user message")

        prompt_text = (system or "") + json.dumps(messages, default=str)
        in_tok = _tokens(prompt_text)
        latency = int(350 + 22 * len(failures) + rng.gauss(0, 120) + (180 if served == "drvn-sim-1.1" else 0))
        latency = max(120, latency)

        # transient faults
        roll = rng.random()
        if roll < 0.04:
            self._sleep(150)
            raise RateLimitError(retry_after_s=round(rng.uniform(0.5, 3.0), 1))
        if roll < 0.07:
            self._sleep(latency * 3)
            raise LLMTimeoutError("request exceeded 30s and was abandoned")

        history = self._tool_results(messages)
        tool_names = {t.get("name") for t in (tools or [])}

        # tool use: ask for history on retried tests, once
        if "get_test_history" in tool_names and not history:
            retried = [f for f in failures if int(f.get("retry_count", 0) or 0) > 0]
            if retried and rng.random() < 0.9:
                calls = []
                for f in retried[:12]:
                    calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "name": "get_test_history",
                        "arguments": {"test_id": f"{f['test_file']}::{f['test_title']}"},
                    })
                self._sleep(latency // 2)
                return LLMResponse(model=served, content=None, tool_calls=calls,
                                   usage={"input_tokens": in_tok, "output_tokens": 30 * len(calls)},
                                   latency_ms=latency // 2, finish_reason="tool_use",
                                   request_id=f"req_{uuid.uuid4().hex[:12]}")

        body = self._classify(failures, served, history)
        content, finish = self._serialize(body, served, max_output_tokens)
        self._sleep(latency)
        return LLMResponse(model=served, content=content,
                           usage={"input_tokens": in_tok, "output_tokens": _tokens(content)},
                           latency_ms=latency, finish_reason=finish,
                           request_id=f"req_{uuid.uuid4().hex[:12]}")

    # -------------------------------------------------------------- internals
    def _sleep(self, ms: float) -> None:
        if self._time_scale > 0:
            time.sleep(ms * self._time_scale / 1000)

    @staticmethod
    def _tool_results(messages: list[dict]) -> dict[str, object]:
        id_to_test = {}
        for m in messages:
            if m.get("role") == "assistant":
                for c in m.get("tool_calls") or []:
                    id_to_test[c.get("id")] = (c.get("arguments") or {}).get("test_id")
        out = {}
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id") in id_to_test:
                try:
                    out[id_to_test[m["tool_call_id"]]] = json.loads(m.get("content") or "null")
                except json.JSONDecodeError:
                    out[id_to_test[m["tool_call_id"]]] = None
        return out

    def _classify(self, failures: list[dict], served: str, history: dict) -> dict:
        rng = self._rng
        items, clusters = [], {}
        for f in failures:
            sig = _signature(f.get("error_message", ""))
            cls = _BASE_CLASS[sig]
            retries = int(f.get("retry_count", 0) or 0)
            rationale = f"Matches '{sig}' pattern."
            test_id = f"{f['test_file']}::{f['test_title']}"
            hist = history.get(test_id)
            if retries > 0:
                cls, rationale = "flaky", "Passed on a retry, treated as intermittent."
            if isinstance(hist, list) and hist:
                failed = sum(1 for n in hist if isinstance(n, dict) and n.get("failed"))
                if failed >= 3:
                    cls = "real_bug"
                    rationale = f"Recurring: failed {failed} of the last {len(hist)} nights despite passing on retry."
            if served == "drvn-sim-1.1" and cls == "flaky" and rng.random() < 0.35:
                cls, rationale = "environment", "Likely staging instability."
            conf = round(rng.uniform(0.86, 0.95) if served == "drvn-sim-1.1" else rng.uniform(0.83, 0.92), 2)
            clusters[sig] = clusters.get(sig, 0) + 1
            items.append({"test_file": f["test_file"], "test_title": f["test_title"],
                          "cluster": sig, "classification": cls,
                          "confidence": conf, "rationale": rationale})

        # silent semantic faults (valid JSON, wrong content)
        if len(items) > 6 and rng.random() < 0.10:
            for _ in range(rng.randint(2, 4)):
                items.pop(rng.randrange(len(items)))
        if items and rng.random() < 0.04:
            rng.choice(items)["classification"] = rng.choice(["Real Bug", "FLAKY", "Environment"])
        if items and rng.random() < 0.03:
            rng.choice(items)["confidence"] = str(rng.choice(items)["confidence"])
        if items and rng.random() < 0.03:
            fake = dict(rng.choice(items))
            fake["test_title"] = fake["test_title"] + " (retry)"
            items.append(fake)
        if items and rng.random() < 0.02:
            rng.choice(items).pop("cluster", None)

        return {"clusters": [{"name": k, "size": v} for k, v in clusters.items()],
                "classifications": items}

    def _serialize(self, body: dict, served: str, max_output_tokens: int) -> tuple[str, str]:
        rng = self._rng
        text = json.dumps(body, indent=2)
        fence_p = 0.15 if served == "drvn-sim-1.1" else 0.08
        r = rng.random()
        if r < fence_p:
            text = "```json\n" + text + "\n```"
        elif r < fence_p + 0.05:
            text = "Here is the triage for tonight's run:\n\n" + text
        if rng.random() < 0.04:
            text = text[: int(len(text) * rng.uniform(0.4, 0.9))]
            return text, "length"
        if _tokens(text) > max_output_tokens:
            return text[: max_output_tokens * 4], "length"
        return text, "stop"
