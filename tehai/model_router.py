"""Model Router.

Tier is chosen from the *weighted* score profile — never complexity alone — and
then subjected to hard escalation overrides for hazardous work (auth/authz,
payment/billing, production, secrets, external send, destructive ops) and for
large context or repeated failure. Default bias: cheapest capable tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Effort, ModelTier, ScoreProfile, TaskContract, TaskType, max_effort


# Weighted-score thresholds (0-100 space). Tunable.
SMALL_MAX = 35.0
MEDIUM_MAX = 65.0

# Substrings that force escalation to LARGE regardless of score. These describe
# what the task is *about* (read from the objective), not safety prohibitions.
# Deliberately specific: bare "email"/"メール"/"送信" are excluded because
# validating an email field is benign; only outward *sending* is hazardous.
HAZARD_KEYWORDS = (
    "auth", "認証", "認可", "authz", "login", "ログイン", "permission", "権限",
    "payment", "課金", "billing", "決済", "secret", "秘密", "credential", "トークン",
    "token", "production", "本番", "deploy", "デプロイ", "delete", "削除", "drop",
    "external api", "外部api", "外部送信", "メール送信", "send_email", "slack通知",
    "webhook",
)

# Task types that are inherently high-stakes -> never route below LARGE.
HIGH_STAKES_TYPES = frozenset({
    TaskType.SECURITY_REVIEW,
    TaskType.ARCHITECTURE,
    TaskType.RELEASE,
})

# Context-size escalation (raw estimated tokens).
CONTEXT_ESCALATION_TOKENS = 48_000


@dataclass
class RoutingResult:
    tier: ModelTier
    base_tier: ModelTier
    score: float
    effort: Effort = Effort.MEDIUM
    reasons: list[str] = field(default_factory=list)


_EFFORT_BUMP = {Effort.LOW: Effort.MEDIUM, Effort.MEDIUM: Effort.HIGH,
                Effort.HIGH: Effort.XHIGH, Effort.XHIGH: Effort.MAX, Effort.MAX: Effort.MAX}


def _effort_for(scores: ScoreProfile, tier: ModelTier, hazard: bool,
                high_stakes: bool, consecutive_failures: int) -> Effort:
    # Effort tracks reasoning hardness (complexity/ambiguity/specialization), NOT
    # the full risk-weighted tier score.
    hardness = scores.complexity * 0.4 + scores.ambiguity * 0.4 + scores.domain_specialization * 0.2
    if hardness <= 25:
        eff = Effort.LOW
    elif hardness <= 45:
        eff = Effort.MEDIUM
    elif hardness <= 65:
        eff = Effort.HIGH
    elif hardness <= 82:
        eff = Effort.XHIGH
    else:
        eff = Effort.MAX
    if hazard or high_stakes or scores.risk >= 70:
        eff = max_effort(eff, Effort.HIGH)      # careful reasoning for risky work
    if tier is ModelTier.LARGE:
        eff = max_effort(eff, Effort.MEDIUM)
    if consecutive_failures >= 2:
        eff = _EFFORT_BUMP[eff]                  # think harder after repeated failure
    return eff


class ModelRouter:
    def __init__(self, small_max: float = SMALL_MAX, medium_max: float = MEDIUM_MAX):
        self.small_max = small_max
        self.medium_max = medium_max

    def _base_tier(self, score: float) -> ModelTier:
        if score <= self.small_max:
            return ModelTier.SMALL
        if score <= self.medium_max:
            return ModelTier.MEDIUM
        return ModelTier.LARGE

    @staticmethod
    def _hazard_hits(text: str) -> list[str]:
        low = text.lower()
        return [kw for kw in HAZARD_KEYWORDS if kw in low or kw in text]

    def route(
        self,
        contract: TaskContract,
        consecutive_failures: int = 0,
    ) -> RoutingResult:
        scores: ScoreProfile = contract.scores
        score = scores.weighted_score()
        base = self._base_tier(score)
        tier = base
        reasons = [f"weighted_score={score:.1f} -> base tier '{base.value}'"]

        def bump_to(target: ModelTier, why: str) -> None:
            nonlocal tier
            if target.rank > tier.rank:
                tier = target
                reasons.append(why)

        # 1) Hazard keywords in the OBJECTIVE -> LARGE. Constraints are safety
        #    prohibitions (e.g. "don't log secrets") and must not self-trigger.
        hits = self._hazard_hits(contract.objective)
        if hits:
            bump_to(ModelTier.LARGE, f"hazard keyword(s) {hits[:4]} -> escalate to large")

        # 2) Inherently high-stakes task type -> LARGE.
        if contract.task_type in HIGH_STAKES_TYPES:
            bump_to(ModelTier.LARGE, f"high-stakes task_type '{contract.task_type.value}' -> large")

        # 3) Very high single-axis risk -> at least LARGE.
        if scores.risk >= 70:
            bump_to(ModelTier.LARGE, f"risk_score={scores.risk} >= 70 -> large")

        # 4) Large context -> at least MEDIUM, bump one tier.
        if contract.estimated_context_tokens >= CONTEXT_ESCALATION_TOKENS:
            target = ModelTier.LARGE if base is ModelTier.MEDIUM else ModelTier.MEDIUM
            bump_to(target, f"estimated_context_tokens={contract.estimated_context_tokens} large")

        # 5) Repeated failure -> escalate one tier (capped at large).
        if consecutive_failures >= 2:
            nxt = {ModelTier.SMALL: ModelTier.MEDIUM,
                   ModelTier.MEDIUM: ModelTier.LARGE,
                   ModelTier.LARGE: ModelTier.LARGE}[tier]
            bump_to(nxt, f"{consecutive_failures} consecutive failures -> escalate")

        effort = _effort_for(scores, tier, bool(hits),
                             contract.task_type in HIGH_STAKES_TYPES, consecutive_failures)
        reasons.append(f"effort '{effort.value}' (reasoning hardness + escalation)")
        return RoutingResult(tier=tier, base_tier=base, score=score, effort=effort, reasons=reasons)
