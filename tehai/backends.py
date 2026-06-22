"""Model backend seam.

The MVP planning pipeline is fully deterministic and needs **no** backend. This
module exists so that a live model can later be dropped in *without touching the
orchestrator* — the architect/reviewers accept an optional ``ModelBackend`` and
fall back to heuristics when it is ``None``.

The two live adapters mirror the pattern already proven in
``~/Projects/recurse/recurse/llm.py`` (ClaudeCliBackend / OllamaBackend). They
shell out lazily and are never invoked by the test suite.
"""

from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod

from .models import ModelTier


class ModelBackend(ABC):
    """Minimal completion interface. Tier lets an adapter pick a concrete model."""

    name: str = "abstract"
    available: bool = False
    # Token usage of the most recent complete() call, if the provider reports it:
    # {"prompt_tokens", "completion_tokens", "total_tokens"} or None.
    last_usage: dict | None = None

    @abstractmethod
    def complete(self, prompt: str, tier: ModelTier, **kwargs) -> str:  # pragma: no cover
        ...


class NullBackend(ModelBackend):
    """Default. Signals 'deterministic mode' — calling it is a programming error."""

    name = "null"
    available = False

    def complete(self, prompt: str, tier: ModelTier, **kwargs) -> str:
        raise RuntimeError(
            "NullBackend: no model wired. The MVP runs deterministically; pass a "
            "real ModelBackend (claude-cli / ollama) only when you want LLM-backed "
            "decomposition or review."
        )


class EchoBackend(ModelBackend):
    """Deterministic test double: returns a stable, inspectable JSON envelope."""

    name = "echo"
    available = True

    def complete(self, prompt: str, tier: ModelTier, **kwargs) -> str:
        return json.dumps({"tier": tier.value, "echo": prompt[:200]}, ensure_ascii=False)


class ClaudeCliBackend(ModelBackend):
    """Live seam — shells out to `claude --print`. Inert unless explicitly used.

    Mirrors recurse's ClaudeCliBackend. Tiers map to model flags; here we only
    pass the prompt and let the CLI default apply, to keep the seam dependency-free.
    """

    name = "claude-cli"
    available = True

    def __init__(self, model_by_tier: dict | None = None, timeout: int = 600):
        self.model_by_tier = model_by_tier or {
            ModelTier.SMALL: "haiku",
            ModelTier.MEDIUM: "sonnet",
            ModelTier.LARGE: "opus",
        }
        self.timeout = timeout

    def complete(self, prompt: str, tier: ModelTier, **kwargs) -> str:  # pragma: no cover
        model = self.model_by_tier.get(tier, "sonnet")
        # `effort` (kwargs) is the router's reasoning-effort decision; the print CLI
        # has no flag for it today, so it's a recorded seam — map it to the API
        # thinking budget when using an SDK/API backend instead.
        self.last_effort = kwargs.get("effort")
        proc = subprocess.run(
            ["claude", "--print", "--model", model],
            input=prompt, capture_output=True, text=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude cli failed: {proc.stderr.strip()}")
        return proc.stdout


class OllamaBackend(ModelBackend):
    """Live seam — local ollama HTTP. Inert unless explicitly used."""

    name = "ollama"
    available = True

    # On this machine Ollama answers on localhost:11434. Under some WSL2 setups it
    # is only reachable via the Windows host route (e.g. http://172.24.224.1:11434);
    # override `host` if localhost fails.
    def __init__(self, host: str = "http://localhost:11434",
                 model_by_tier: dict | None = None):
        self.host = host.rstrip("/")
        self.last_usage = None
        self.model_by_tier = model_by_tier or {
            ModelTier.SMALL: "gemma4:e2b",
            ModelTier.MEDIUM: "gemma4:latest",
            ModelTier.LARGE: "gemma4:latest",
        }

    def complete(self, prompt: str, tier: ModelTier, **kwargs) -> str:  # pragma: no cover
        import urllib.request

        model = self.model_by_tier.get(tier, "llama3.1:8b")
        body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read())
        pt, ct = data.get("prompt_eval_count"), data.get("eval_count")
        self.last_usage = (
            {"prompt_tokens": pt or 0, "completion_tokens": ct or 0,
             "total_tokens": (pt or 0) + (ct or 0)}
            if (pt is not None or ct is not None) else None
        )
        return data.get("response", "")


_BACKENDS = {
    "null": NullBackend,
    "echo": EchoBackend,
    "claude-cli": ClaudeCliBackend,
    "ollama": OllamaBackend,
}


def get_backend(name: str = "null", **kwargs) -> ModelBackend:
    """Factory. Defaults to the deterministic NullBackend.

    Extra kwargs are forwarded to the adapter constructor (e.g.
    get_backend("ollama", host="http://172.24.224.1:11434")).
    """
    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown backend {name!r}; choose from {sorted(_BACKENDS)}")
    return cls(**kwargs) if kwargs else cls()
