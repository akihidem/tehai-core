"""Runtime config — the human-approved adoption path for calibration proposals.

`tehai calibrate <ledger> --apply cfg.json` writes an adoptable config; a human
reviews it; `tehai plan/run --config cfg.json` then loads it. The tool never
self-modifies code or auto-adopts — adoption is an explicit human act (Phase 2).
Only known, safe knobs are honoured (router thresholds); anything else is ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .model_router import MEDIUM_MAX, SMALL_MAX, ModelRouter

# Knobs adoptable via config (deliberately narrow — no security/permission or
# escalation logic is ever config-driven). Router thresholds are the original
# numeric knobs; the vendor-routing knobs below let a *human-ratified* benchmark
# proposal pick which backend serves each task class (see tehai/benchmark.py).
DEFAULTS: dict[str, Any] = {
    "router_small_max": SMALL_MAX,
    "router_medium_max": MEDIUM_MAX,
    "default_backend": "ollama",   # fallback lane for unmapped/absent task types
    "routing_table": {},           # task_type value -> backend name (deterministic)
    "backends": {},                # backend name -> constructor kwargs (host/model_by_tier/...)
    "unit_cost": {},               # backend name -> USD per 1k tokens (for bench cost; default free)
    "ensemble": {},                # EnsembleBackend spec (members / member+n, strategy, aggregator)
}


def load_config(source: Optional[Any]) -> dict[str, Any]:
    """Return a config dict (defaults overlaid with known keys from source).

    source may be None, a dict, or a path to a JSON file."""
    cfg: dict[str, Any] = dict(DEFAULTS)
    cfg["routing_table"] = dict(DEFAULTS["routing_table"])  # never alias the defaults
    cfg["backends"] = dict(DEFAULTS["backends"])
    cfg["unit_cost"] = dict(DEFAULTS["unit_cost"])
    cfg["ensemble"] = dict(DEFAULTS["ensemble"])
    if source is None:
        return cfg
    raw = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    # numeric router thresholds (the original narrow knobs)
    for k in ("router_small_max", "router_medium_max"):
        if k in raw and isinstance(raw[k], (int, float)):
            cfg[k] = raw[k]
    # Keep thresholds sane and ordered.
    cfg["router_small_max"] = max(0, min(100, cfg["router_small_max"]))
    cfg["router_medium_max"] = max(cfg["router_small_max"] + 1, min(100, cfg["router_medium_max"]))
    # vendor-routing knobs (deterministic table + per-backend constructor kwargs)
    if isinstance(raw.get("default_backend"), str):
        cfg["default_backend"] = raw["default_backend"]
    if isinstance(raw.get("routing_table"), dict):
        cfg["routing_table"] = {str(k): str(v) for k, v in raw["routing_table"].items()
                                if isinstance(v, str)}
    if isinstance(raw.get("backends"), dict):
        cfg["backends"] = {str(k): v for k, v in raw["backends"].items() if isinstance(v, dict)}
    if isinstance(raw.get("unit_cost"), dict):
        cfg["unit_cost"] = {str(k): float(v) for k, v in raw["unit_cost"].items()
                            if isinstance(v, (int, float))}
    if isinstance(raw.get("ensemble"), dict):
        cfg["ensemble"] = raw["ensemble"]
    return cfg


def router_from_config(source: Optional[Any]) -> ModelRouter:
    c = load_config(source)
    return ModelRouter(small_max=c["router_small_max"], medium_max=c["router_medium_max"])


def gama_from_config(source: Optional[Any], backend_names: Optional[list[str]] = None):
    """Build the deterministic gama vendor router (GamaBackend) from config.

    Instantiates the sub-backends named in the routing table (plus the default),
    forwarding each backend's constructor kwargs from cfg["backends"]. Returns a
    ready ``GamaBackend`` to hand to ``Orchestrator.default(<obj>)``.
    """
    from .backends import GamaBackend, get_backend  # lazy: avoid import cycle

    c = load_config(source)
    names = backend_names or sorted(set(c["routing_table"].values()) | {c["default_backend"]})
    backends = {n: get_backend(n, **c["backends"].get(n, {})) for n in names}
    return GamaBackend(backends, routing_table=c["routing_table"], default=c["default_backend"])


def ensemble_from_config(source: Optional[Any]):
    """Build an EnsembleBackend (the model-combination loop) from config.

    cfg["ensemble"] accepts either an explicit ``members`` list, or a single
    ``member`` spec repeated ``n`` times (homogeneous self-ensemble). Each spec is
    ``{"backend": <name>, "kwargs": {...}}``. Optional ``aggregator`` (same spec),
    ``strategy`` ("synthesize" | "majority" | "first"), and ``aggregator_prompt``.
    """
    from .backends import EnsembleBackend, get_backend  # lazy: avoid import cycle

    spec = load_config(source)["ensemble"] or {}

    def build(s):
        return get_backend(s["backend"], **(s.get("kwargs") or {}))

    if spec.get("members"):
        members = [build(m) for m in spec["members"]]
    elif spec.get("member"):
        members = [build(spec["member"]) for _ in range(int(spec.get("n", 3)))]
    else:
        raise ValueError("ensemble config needs 'members' or 'member'+'n'")
    aggregator = build(spec["aggregator"]) if spec.get("aggregator") else None
    return EnsembleBackend(members, strategy=spec.get("strategy", "synthesize"),
                           aggregator=aggregator,
                           aggregator_prompt=spec.get("aggregator_prompt"))


def build_backend(spec: Any):
    """Recursively build a backend from a spec ``{"backend": name, "kwargs": {...}}``.

    Composites compose: ``tool`` wraps ``kwargs['inner']`` (a spec); ``ensemble`` takes
    ``kwargs['members']`` (list of specs) or ``kwargs['member']`` + ``kwargs['n']`` plus
    optional ``aggregator``/``strategy``; ``gama`` takes ``kwargs['backends']`` (name ->
    spec) + ``routing_table`` + ``default``. Any other name goes to ``get_backend`` with
    the remaining kwargs. Lets a config declare a full sovereign stack (gama over tool /
    ensemble / coder lanes) as nested JSON.
    """
    from .backends import EnsembleBackend, GamaBackend, ToolBackend, get_backend

    name = spec["backend"]
    kw = dict(spec.get("kwargs") or {})
    if name == "tool":
        inner = build_backend(kw.pop("inner"))
        return ToolBackend(inner, **kw)
    if name == "ensemble":
        if kw.get("members"):
            members = [build_backend(m) for m in kw["members"]]
        else:
            members = [build_backend(kw["member"]) for _ in range(int(kw.get("n", 3)))]
        agg = build_backend(kw["aggregator"]) if kw.get("aggregator") else None
        return EnsembleBackend(members, strategy=kw.get("strategy", "synthesize"),
                               aggregator=agg, aggregator_prompt=kw.get("aggregator_prompt"))
    if name == "gama":
        backends = {n: build_backend(s) for n, s in (kw.get("backends") or {}).items()}
        return GamaBackend(backends, routing_table=kw.get("routing_table"),
                           default=kw.get("default"))
    return get_backend(name, **kw)
