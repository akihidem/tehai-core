"""Review Planner.

Reviews are composed by risk, not applied uniformly. A doc-formatting task gets a
single automated check; an auth/secret/payment change gets requirements +
security + edge-case review, a Judge, and a human gate. Lenses are always
separated — each ReviewStep sees exactly one viewpoint.
"""

from __future__ import annotations

from .models import (
    ModelTier, ReviewLens, ReviewPlan, ReviewStep, TaskContract, TaskType,
)
from .model_router import HAZARD_KEYWORDS


# lens -> (reviewer agent template, model tier)
_LENS_AGENT = {
    ReviewLens.AUTO_CHECK: ("AutoChecker", ModelTier.SMALL),
    ReviewLens.REQUIREMENTS: ("RequirementsReviewer", ModelTier.SMALL),
    ReviewLens.EDGE_CASES: ("EdgeCaseReviewer", ModelTier.MEDIUM),
    ReviewLens.SECURITY: ("SecurityReviewer", ModelTier.LARGE),
    ReviewLens.UX: ("UXReviewer", ModelTier.SMALL),
    ReviewLens.TESTS: ("AutoChecker", ModelTier.SMALL),
}

_LOW_RISK_TYPES = frozenset({
    TaskType.DOC_FORMATTING, TaskType.EXTRACTION, TaskType.CLASSIFICATION,
    TaskType.SUMMARIZATION,
})
_UI_SIGNALS = ("ui", "画面", "frontend", "フロント", "button", "ボタン", "form", "フォーム", "画面遷移")
# Keywords that demand a human gate (outward-facing / irreversible). Read from
# the objective only — boilerplate safety constraints must not self-trigger.
_HUMAN_GATE_SIGNALS = (
    "production", "本番", "deploy", "デプロイ", "secret", "秘密", "credential",
    "payment", "課金", "決済", "billing", "delete", "削除", "外部送信",
    "external api", "外部api", "メール送信", "slack通知", "webhook",
)


def _step(lens: ReviewLens) -> ReviewStep:
    agent, tier = _LENS_AGENT[lens]
    return ReviewStep(lens=lens, agent_template_id=agent, model_tier=tier)


class ReviewPlanner:
    def plan(self, contract: TaskContract) -> ReviewPlan:
        tt = contract.task_type
        # Read intent from the objective only; constraints are safety prohibitions.
        text = contract.objective.lower()
        hazard = any(k in text or k in contract.objective for k in HAZARD_KEYWORDS)
        human_gate = any(k in text for k in _HUMAN_GATE_SIGNALS)
        is_ui = any(k in text for k in _UI_SIGNALS) or contract.assigned_agent_template == "FrontendEngineer"
        high_risk = contract.scores.risk >= 60

        lenses: list[ReviewLens] = []
        require_judge = False
        reasons: list[str] = []

        if tt in _LOW_RISK_TYPES and not hazard:
            lenses = [ReviewLens.AUTO_CHECK]
            reasons.append("low-risk transform -> automated check only")

        elif tt == TaskType.SECURITY_REVIEW or hazard:
            lenses = [ReviewLens.AUTO_CHECK, ReviewLens.REQUIREMENTS,
                      ReviewLens.SECURITY, ReviewLens.EDGE_CASES]
            require_judge = True
            reasons.append("security-sensitive (auth/secret/payment/external) -> security + judge")

        elif tt in (TaskType.CODE_IMPLEMENTATION, TaskType.TEST_AUTHORING, TaskType.INTEGRATION):
            lenses = [ReviewLens.AUTO_CHECK, ReviewLens.REQUIREMENTS, ReviewLens.EDGE_CASES]
            reasons.append("normal implementation -> tests + requirements + edge-cases")

        elif tt in (TaskType.CODE_REVIEW, TaskType.QA):
            lenses = [ReviewLens.REQUIREMENTS, ReviewLens.EDGE_CASES]
            reasons.append("review/qa -> requirements + edge-cases")

        elif tt in (TaskType.ARCHITECTURE, TaskType.SPEC_DESIGN, TaskType.RESEARCH):
            lenses = [ReviewLens.REQUIREMENTS, ReviewLens.EDGE_CASES]
            require_judge = high_risk
            reasons.append("design/research -> requirements + edge-cases" + (" + judge" if high_risk else ""))

        elif tt == TaskType.RELEASE:
            lenses = [ReviewLens.AUTO_CHECK, ReviewLens.REQUIREMENTS]
            require_judge = True
            human_gate = True
            reasons.append("release -> independent review + judge + human gate")

        else:  # content, incident, generic
            lenses = [ReviewLens.REQUIREMENTS, ReviewLens.EDGE_CASES]
            reasons.append(f"{tt.value} -> requirements + edge-cases")

        if is_ui and ReviewLens.UX not in lenses:
            lenses.append(ReviewLens.UX)
            reasons.append("UI change -> add UX review")

        # A Judge is warranted whenever ≥3 separated lenses disagree-prone, or risk high.
        if len(lenses) >= 3 or high_risk:
            require_judge = True

        if human_gate:
            require_judge = True
            reasons.append("outward-facing/irreversible -> human approval gate")

        steps = [_step(l) for l in lenses]
        return ReviewPlan(
            task_id=contract.task_id,
            steps=steps,
            require_judge=require_judge,
            require_human_gate=human_gate,
            rationale="; ".join(reasons),
        )
