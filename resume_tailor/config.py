"""Global configuration — task->tier mapping, runtime flags, .env loading.

Tiers are neutral labels ("fast" = simple/structured/short tasks, "strong" =
reasoning/writing tasks). The concrete model per tier is provider-specific and
resolved by the LLM client — this project uses Groq (see resume_tailor/llm).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ModelTier = Literal["fast", "strong"]

# task -> tier: simple/structured/short tasks -> fast; reasoning/writing -> strong
TIER_MAP: dict[str, ModelTier] = {
    "jd_parser": "fast",
    "chat_intent": "fast",
    "verifier": "fast",
    "ocr_cleanup": "fast",
    "matcher_reformulation": "strong",
    "bullet_generation": "strong",
    "chat_rewrites": "strong",
}

# max output tokens per task: the builder emits a full resume, the verifier a
# verdict list, the parser a short list. Overridable per task via
# GROQ_MAX_TOKENS_<TASK> (e.g. GROQ_MAX_TOKENS_BULLET_GENERATION=8000).
MAX_TOKENS_MAP: dict[str, int] = {
    "jd_parser": 1024,
    "verifier": 2048,
    "chat_intent": 512,
    "ocr_cleanup": 1024,
    "matcher_reformulation": 512,
    "bullet_generation": 4096,
    "chat_rewrites": 2048,
}
DEFAULT_MAX_TOKENS = 2048


def max_tokens_for(task: str) -> int:
    """Max output tokens for a task, env-overridable per task."""
    env = os.environ.get(f"GROQ_MAX_TOKENS_{task.upper()}")
    if env is not None and env.isdigit():
        return int(env)
    return MAX_TOKENS_MAP.get(task, DEFAULT_MAX_TOKENS)


def _load_env_file() -> None:
    """Minimal .env loader (no python-dotenv dependency).

    Looks for .env in the CWD and the project root; never overrides variables
    already set in the real environment.
    """
    seen: set[Path] = set()
    for path in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
        if path in seen or not path.exists():
            seen.add(path)
            continue
        seen.add(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def model_for(task: str) -> ModelTier:
    """Tier for a named pipeline task. Unknown tasks default to strong (safer)."""
    return TIER_MAP.get(task, "strong")


def is_dry_run() -> bool:
    """True when RESUME_TAILOR_DRY_RUN=1 — run against mocks, no live APIs.

    This is the switch for the offline/fixture mode (brainstorm rec #1) that
    makes the pipeline and the demo moments reproducible in CI.
    """
    return os.environ.get("RESUME_TAILOR_DRY_RUN", "") == "1"
