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

# The only knobs adoptable via config (deliberately narrow — no security/permission
# or escalation logic is ever config-driven).
DEFAULTS: dict[str, Any] = {
    "router_small_max": SMALL_MAX,
    "router_medium_max": MEDIUM_MAX,
}


def load_config(source: Optional[Any]) -> dict[str, Any]:
    """Return a config dict (defaults overlaid with known keys from source).

    source may be None, a dict, or a path to a JSON file."""
    cfg: dict[str, Any] = dict(DEFAULTS)
    if source is None:
        return cfg
    raw = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    for k in DEFAULTS:
        if k in raw and isinstance(raw[k], (int, float)):
            cfg[k] = raw[k]
    # Keep thresholds sane and ordered.
    cfg["router_small_max"] = max(0, min(100, cfg["router_small_max"]))
    cfg["router_medium_max"] = max(cfg["router_small_max"] + 1, min(100, cfg["router_medium_max"]))
    return cfg


def router_from_config(source: Optional[Any]) -> ModelRouter:
    c = load_config(source)
    return ModelRouter(small_max=c["router_small_max"], medium_max=c["router_medium_max"])
