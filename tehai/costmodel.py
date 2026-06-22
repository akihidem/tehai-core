"""Shared, tier-weighted cost model.

A "cost" here is a deterministic proxy: every model call is charged by the tier
it runs on (a strong-tier review costs more than a cheap generation). One
generate→review pass = 1 generation at the task's tier + one review call per
planned lens at that lens's tier.

This single module is used by BOTH the flat dataflow orchestrator (B) and the
A/B experiment's reconstruction of the multi-team path (A), so the two are
charged with the *identical* formula — the cost comparison is then an emergent
property of structure, not of two different accounting rules.

It is NOT a USD oracle. It measures *relative* call volume × tier, which is what
distinguishes a flat pipeline from a 7-team hierarchy with cascading reroutes.
"""

from __future__ import annotations

from .models import ModelTier, ReviewPlan, TaskContract


# Same per-tier table the rest of tehai uses (orchestrator/executor `_TIER_COST`).
TIER_COST: dict[ModelTier, float] = {
    ModelTier.SMALL: 0.01,
    ModelTier.MEDIUM: 0.05,
    ModelTier.LARGE: 0.20,
}


def attempt_cost(contract: TaskContract, review_plan: ReviewPlan) -> tuple[int, float]:
    """Cost of ONE generate→review pass over a contract: a generation at the
    task's tier plus one review call per planned lens at its tier. Returns
    (model_calls, usd)."""
    calls = 1 + len(review_plan.steps)
    usd = TIER_COST[contract.recommended_model] + sum(
        TIER_COST[s.model_tier] for s in review_plan.steps
    )
    return calls, round(usd, 6)


def exec_cost(contract: TaskContract, review_plan: ReviewPlan,
              attempts: int) -> tuple[int, float]:
    """Cost of ``attempts`` generate→review passes over a contract."""
    c, u = attempt_cost(contract, review_plan)
    n = max(1, attempts)
    return c * n, round(u * n, 6)
