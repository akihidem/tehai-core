"""Backend-driven task scoring.

The Model Router consumes a 6-axis ScoreProfile. Those axes can come from:
- the LLM decomposition call (already scored inline), or
- this Scorer, which (re)scores tasks via the backend in ONE batched call —
  used for the heuristic/template decomposition path so its scores are also
  model-derived when a backend is present.

Any failure falls back silently: scores are left as the heuristic values.
"""

from __future__ import annotations

from .architect import _extract_json  # one-way import; architect never imports scoring
from .models import ModelTier, ScoreProfile, TaskContract


_AXES = ("complexity", "ambiguity", "risk", "context_size", "tool_risk", "domain_specialization")


def _clamp(v) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return 0


class Scorer:
    def __init__(self, backend=None):
        self.backend = backend
        self.last_error: str | None = None

    def available(self) -> bool:
        return self.backend is not None and getattr(self.backend, "available", False)

    def rescore(self, contracts: list[TaskContract], request: str) -> bool:
        """Overwrite each contract.scores with LLM-derived scores (batched).

        Returns True if the backend produced usable scores, False on fallback
        (in which case the existing heuristic scores are left untouched).
        """
        self.last_error = None
        if not self.available() or not contracts:
            return False
        try:
            scores = self._llm_score(contracts, request)
        except Exception as e:  # never break the pipeline on a flaky model
            self.last_error = f"{type(e).__name__}: {e}"
            return False
        for c in contracts:
            if c.task_id in scores:
                c.scores = scores[c.task_id]
        return True

    def _prompt(self, contracts: list[TaskContract], request: str) -> str:
        lines = "\n".join(
            f'- "{c.task_id}": {c.objective}  [type={c.task_type.value}]' for c in contracts
        )
        return (
            "Score each TASK on six axes (integers 0-100) so a router can pick the "
            "cheapest capable model. Axes:\n"
            "- complexity: intrinsic difficulty of the work\n"
            "- ambiguity: how under-specified / open to interpretation it is\n"
            "- risk: blast radius — auth, payment, production, secrets, data loss\n"
            "- context_size: how much surrounding context is needed\n"
            "- tool_risk: danger of the tools it must use (deploy/delete/external)\n"
            "- domain_specialization: need for specialist knowledge\n\n"
            f"REQUEST CONTEXT:\n{request}\n\nTASKS:\n{lines}\n\n"
            'Return ONLY a JSON object keyed by the exact task id:\n'
            '{"<task_id>": {"complexity":0,"ambiguity":0,"risk":0,"context_size":0,'
            '"tool_risk":0,"domain_specialization":0}, ...}\n'
            "Every task id above must appear exactly once."
        )

    def _llm_score(self, contracts: list[TaskContract], request: str) -> dict[str, ScoreProfile]:
        raw = self.backend.complete(self._prompt(contracts, request), ModelTier.LARGE)
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object keyed by task id")
        out: dict[str, ScoreProfile] = {}
        for c in contracts:
            sc = data.get(c.task_id)
            if not isinstance(sc, dict):
                raise ValueError(f"missing scores for task {c.task_id}")
            out[c.task_id] = ScoreProfile(**{ax: _clamp(sc.get(ax, 0)) for ax in _AXES})
        return out
