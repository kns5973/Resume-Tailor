"""LLM client abstraction.

Every pipeline call goes through a thin client keyed by task name. The tier
("fast"/"strong") comes from config; the concrete model is provider-specific
and env-configurable. This project uses Groq as its provider (OpenAI-compatible
endpoint with JSON mode). MockLLMClient covers tests, CI, and dry-run mode
(RESUME_TAILOR_DRY_RUN=1, or no GROQ_API_KEY configured).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Protocol

import requests

from resume_tailor.config import is_dry_run, max_tokens_for, model_for


class LLMError(RuntimeError):
    """Raised when the LLM returns something unusable (bad JSON, API error)."""


class LLMClient(Protocol):
    """Anything that turns a prompt into a JSON dict, keyed by task name."""

    def complete_json(self, *, task: str, system: str, prompt: str) -> dict: ...


def model_name_for(task: str) -> str:
    """Concrete model for a task's tier, env-overridable (Groq model ids)."""
    tier = model_for(task)
    if tier == "fast":
        return os.environ.get("GROQ_MODEL_FAST", "llama-3.1-8b-instant")
    return os.environ.get("GROQ_MODEL_STRONG", "llama-3.3-70b-versatile")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json(raw: str) -> dict:
    """Parse a model's text response as JSON.

    Tolerates ```json fences AND prose around the JSON (models sometimes emit
    "Here is the JSON:\\n{...}" despite strict prompts). Falls back to extracting
    the first {...} block before giving up.
    """
    text = _FENCE.sub("", raw.strip()).strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        block = re.search(r"\{.*\}", text, re.DOTALL)
        if block:
            try:
                data = json.loads(block.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        raise LLMError("model returned invalid JSON (no object found)")
    return data


# --------------------------------------------------------------------------
# Mock client (tests / CI / dry-run)
# --------------------------------------------------------------------------

SAMPLE_PARSED_JD = {
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "requirements": [
        {"requirement": "Experience with Redis and message queues", "priority": "must_have", "keywords": ["redis", "queue"]},
        {"requirement": "Proficient in Python", "priority": "must_have", "keywords": ["python"]},
        {"requirement": "Knowledge of distributed systems", "priority": "nice_to_have", "keywords": ["distributed systems"]},
    ],
}

# Only jd_parser has a coherent default fixture. Reformulation has NO default:
# a generic query would silently steer every requirement to the same chunks in
# dry-run mode — better to fail loudly so callers register their own mock.
DEFAULT_MOCKS: dict[str, list[str]] = {
    "jd_parser": [json.dumps(SAMPLE_PARSED_JD)],
}

MockHandler = Callable[[str], str]  # prompt -> JSON text


class MockLLMClient:
    """Deterministic client for tests and dry-run mode.

    responses: {task: handler} where handler is either a list of JSON strings
    (each call pops the next; the last is reused once the queue empties) or a
    callable(prompt) -> JSON string (e.g. for prompt-aware reformulations in
    demos). Every call is recorded on `calls` for assertions.
    """

    def __init__(self, responses: dict[str, list[str] | MockHandler] | None = None) -> None:
        self._responses = {**DEFAULT_MOCKS, **(responses or {})}
        self.calls: list[tuple[str, str, str]] = []  # (task, system, prompt)

    def register(self, task: str, handler: list[str] | MockHandler) -> None:
        """Register or replace the handler for a task (used by demos)."""
        self._responses[task] = handler

    def complete_json(self, *, task: str, system: str, prompt: str) -> dict:
        self.calls.append((task, system, prompt))
        handler = self._responses.get(task)
        if handler is None:
            raise LLMError(f"no mock response registered for task '{task}'")
        if callable(handler):
            raw = handler(prompt)
        else:
            queue: list[str] = handler
            raw = queue[0] if len(queue) == 1 else queue.pop(0)
        return _parse_json(raw)


# --------------------------------------------------------------------------
# Real client (Groq)
# --------------------------------------------------------------------------

class GroqClient:
    """Groq via its OpenAI-compatible endpoint, plain HTTP (no openai SDK).

    JSON mode is requested explicitly (response_format) — Groq guarantees valid
    JSON output when the prompt also mentions JSON (our system prompts do).
    """

    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        *,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    def _post(self, payload: dict, headers: dict) -> requests.Response:
        """POST with bounded 429 (rate-limit) retries and backoff.

        The free tier hits tokens-per-minute limits easily (a full pipeline run
        fires several calls in quick succession), so a short, bounded retry
        honoring Retry-After absorbs transient 429s. Other HTTP errors surface
        immediately.
        """
        resp = None
        for attempt in range(self._max_retries + 1):
            resp = requests.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=120,
            )
            if resp.status_code != 429 or attempt >= self._max_retries:
                return resp
            retry_after = resp.headers.get("Retry-After", "")
            delay = float(retry_after) if retry_after.isdigit() else self._retry_backoff * (2**attempt)
            time.sleep(min(delay, 15.0))
        return resp  # pragma: no cover — the loop always returns

    def complete_json(self, *, task: str, system: str, prompt: str) -> dict:
        model = model_name_for(task)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": max_tokens_for(task),
        }
        resp = None
        try:
            resp = self._post(payload, {"Authorization": f"Bearer {self._api_key}"})
            resp.raise_for_status()
        except requests.RequestException as exc:
            if resp is not None:
                detail = f"HTTP {resp.status_code}: {resp.text[:300]}"
            else:
                detail = str(exc)
            raise LLMError(f"Groq API error ({model}): {detail}") from exc
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_json(content)


def get_client(api_key: str | None = None) -> LLMClient:
    """Mock client in dry-run mode or without a key; otherwise Groq."""
    key = api_key or os.environ.get("GROQ_API_KEY", "")
    if is_dry_run() or not key:
        return MockLLMClient()
    return GroqClient(api_key=key)
