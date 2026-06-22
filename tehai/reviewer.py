"""Reviewer — actually runs the planned review lenses.

The ReviewPlanner decides *which* separated lenses apply; this executes them. With
a backend, each lens is a single-viewpoint LLM review; without one (or on failure)
a deterministic structural reviewer runs so the loop closes offline. Output feeds
the existing Judge unchanged.

When no artifact is supplied (planning time), the lens reviews the CONTRACT/plan
itself — e.g. "are the acceptance criteria testable/sufficient for this lens?".
"""

from __future__ import annotations

from .architect import _extract_json
from .models import (
    ModelTier, ReviewLens, ReviewPlan, ReviewResult, TaskContract,
)
from .model_router import HAZARD_KEYWORDS


_VERDICTS = {"pass", "fail", "concerns"}
_SEVERITIES = {"none", "low", "medium", "high", "critical"}

_LENS_VIEW = {
    ReviewLens.REQUIREMENTS: "whether it satisfies the acceptance_criteria — nothing else",
    ReviewLens.EDGE_CASES: "failure modes and boundary conditions ONLY",
    ReviewLens.SECURITY: "auth, authorization, secrets, injection, privilege escalation ONLY",
    ReviewLens.UX: "user-facing behaviour and reachability ONLY",
    ReviewLens.AUTO_CHECK: "deterministic checks (tests run, lint, schema) ONLY",
    ReviewLens.TESTS: "whether the test evidence is sufficient and honest ONLY",
}

# Words that signal a contract considered boundary/error behaviour.
_EDGE_WORDS = ("境界", "boundary", "error", "失敗", "invalid", "null", "empty", "空",
               "0", "limit", "上限", "下限", "超", "未満", "以上", "overflow", "edge")
_UX_WORDS = ("feedback", "フィードバック", "message", "メッセージ", "error", "エラー", "表示")


class Reviewer:
    def __init__(self, backend=None):
        self.backend = backend
        self.last_error: str | None = None

    def available(self) -> bool:
        return self.backend is not None and getattr(self.backend, "available", False)

    def run(self, contract: TaskContract, plan: ReviewPlan,
            artifact: str | None = None) -> list[ReviewResult]:
        results: list[ReviewResult] = []
        for step in plan.steps:
            r = None
            if self.available():
                try:
                    r = self._llm_review(contract, step.lens, artifact)
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {e}"
            if r is None:
                r = self._heuristic_review(contract, step.lens, artifact)
            results.append(r)
        return results

    # ----- LLM review (one separated viewpoint per call) ----- #
    def _llm_review(self, contract: TaskContract, lens: ReviewLens,
                    artifact: str | None) -> ReviewResult:
        view = _LENS_VIEW.get(lens, lens.value)
        under_review = (
            f"ARTIFACT UNDER REVIEW:\n{artifact}"
            if artifact else
            "NO ARTIFACT YET — review the CONTRACT/PLAN itself: are the acceptance "
            "criteria testable and sufficient for this viewpoint?"
        )
        prompt = (
            f"You are a {lens.value} reviewer. Judge ONLY {view}. Be strict and concrete.\n\n"
            f"TASK CONTRACT:\n"
            f"  objective: {contract.objective}\n"
            f"  acceptance_criteria: {contract.acceptance_criteria}\n"
            f"  constraints: {contract.constraints}\n"
            f"  expected_output: {contract.expected_output}\n\n"
            f"{under_review}\n\n"
            'Return ONLY JSON: {"verdict":"pass|fail|concerns",'
            '"severity":"none|low|medium|high|critical","rationale":"<one sentence>",'
            '"findings":["<specific issue>"]}'
        )
        data = _extract_json(self.backend.complete(prompt, lens_tier(lens)))
        if not isinstance(data, dict):
            raise ValueError("review is not a JSON object")
        verdict = str(data.get("verdict", "")).lower()
        severity = str(data.get("severity", "none")).lower()
        if verdict not in _VERDICTS:
            raise ValueError(f"bad verdict {verdict!r}")
        if severity not in _SEVERITIES:
            severity = "none" if verdict == "pass" else "medium"
        findings = [str(f) for f in (data.get("findings") or [])]
        return ReviewResult(
            task_id=contract.task_id, lens=lens, verdict=verdict, severity=severity,
            rationale=str(data.get("rationale", "")), findings=findings,
        )

    # ----- deterministic fallback (structural) ----- #
    def _heuristic_review(self, contract: TaskContract, lens: ReviewLens,
                          artifact: str | None) -> ReviewResult:
        def res(verdict, severity="none", rationale="", findings=None):
            return ReviewResult(contract.task_id, lens, verdict, severity, rationale, findings or [])

        text = " ".join(contract.acceptance_criteria).lower()

        if lens in (ReviewLens.AUTO_CHECK, ReviewLens.TESTS):
            errs = contract.validate()
            if errs or not contract.expected_output or not contract.acceptance_criteria:
                return res("fail", "medium", "structural check failed",
                           errs[:1] or ["missing expected_output/acceptance_criteria"])
            return res("pass", "none", "structural checks pass (contract valid, outputs+criteria present)")

        if lens == ReviewLens.REQUIREMENTS:
            if not contract.acceptance_criteria:
                return res("fail", "medium", "no acceptance criteria to satisfy")
            if len(contract.acceptance_criteria) == 1:
                return res("concerns", "low", "only one acceptance criterion — likely under-specified")
            return res("pass", "none", "acceptance criteria present and plural")

        if lens == ReviewLens.EDGE_CASES:
            if not any(w in text for w in _EDGE_WORDS):
                return res("concerns", "low", "no acceptance criterion mentions a boundary/error case",
                           ["add explicit boundary/error acceptance criteria"])
            return res("pass", "none", "boundary/error conditions referenced in criteria")

        if lens == ReviewLens.SECURITY:
            hazardous = any(k in contract.objective.lower() or k in contract.objective
                            for k in HAZARD_KEYWORDS)
            guards_secrets = any("秘密" in c or "secret" in c.lower() or "認証" in c
                                 for c in contract.constraints)
            if hazardous and not guards_secrets:
                return res("concerns", "medium",
                           "security-sensitive objective without an explicit secret/auth constraint",
                           ["add a constraint covering secret handling / authz"])
            return res("pass", "none", "no obvious security gap at plan level")

        if lens == ReviewLens.UX:
            if contract.assigned_agent_template == "FrontendEngineer" and not any(w in text for w in _UX_WORDS):
                return res("concerns", "low", "UI task without a user-feedback/error acceptance criterion")
            return res("pass", "none", "no UX gap at plan level")

        return res("pass", "none", f"{lens.value}: no deterministic check")


def lens_tier(lens: ReviewLens) -> ModelTier:
    """Security reviews warrant the strong tier; the rest are cheap."""
    return ModelTier.LARGE if lens == ReviewLens.SECURITY else ModelTier.SMALL
