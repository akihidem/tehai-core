"""Evaluation Store.

Reads the execution ledger and produces aggregate metrics + *proposed* (never
auto-applied) improvement suggestions. This is Phase 1/2 of the self-improvement
ladder: surface and propose. Phase 3 (bounded auto-tuning) is intentionally out
of scope — see FUTURE.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Optional

from .model_router import MEDIUM_MAX, SMALL_MAX


def _safe_mean(xs: list[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(mean(xs), 4) if xs else None


@dataclass
class GroupStat:
    n: int = 0
    success: int = 0

    @property
    def success_rate(self) -> Optional[float]:
        return round(self.success / self.n, 4) if self.n else None


@dataclass
class Metrics:
    n_records: int
    overall_success_rate: Optional[float]
    by_task_type: dict[str, float]
    by_model: dict[str, float]
    by_agent_template: dict[str, float]
    by_decomposition_strategy: dict[str, float]
    avg_rework: Optional[float]
    avg_review_score: Optional[float]
    avg_test_pass_rate: Optional[float]
    cost_estimate_error: Optional[float]
    time_estimate_error: Optional[float]
    escalation_rate: Optional[float]
    human_override_rate: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


# A record counts as "successful" when the judge accepted it and no failure_reason.
def _is_success(rec: dict) -> bool:
    if rec.get("failure_reason"):
        return False
    decision = rec.get("judge_decision")
    if decision is not None:
        return decision == "accept"
    return True


class EvaluationStore:
    def compute(self, records: list[dict]) -> Metrics:
        n = len(records)
        groups: dict[str, dict[str, GroupStat]] = {
            "task_type": defaultdict(GroupStat),
            "selected_model": defaultdict(GroupStat),
            "agent_template": defaultdict(GroupStat),
            "decomposition_strategy": defaultdict(GroupStat),
        }
        rework, review, testpass = [], [], []
        cost_err, time_err = [], []
        escalations = overrides = success_total = 0

        for rec in records:
            ok = _is_success(rec)
            success_total += int(ok)
            for key in groups:
                val = rec.get(key) or "unknown"
                g = groups[key][val]
                g.n += 1
                g.success += int(ok)
            rework.append(rec.get("rework_count"))
            review.append(rec.get("review_score"))
            testpass.append(rec.get("test_pass_rate"))
            if rec.get("estimated_cost") is not None and rec.get("actual_cost") is not None:
                cost_err.append(abs(rec["actual_cost"] - rec["estimated_cost"]))
            if rec.get("elapsed_seconds") is not None and rec.get("estimated_seconds") is not None:
                time_err.append(abs(rec["elapsed_seconds"] - rec["estimated_seconds"]))
            escalations += int(bool(rec.get("escalated")))
            overrides += int(bool(rec.get("human_override")))

        def rates(key: str) -> dict[str, float]:
            return {k: v.success_rate for k, v in sorted(groups[key].items()) if v.success_rate is not None}

        return Metrics(
            n_records=n,
            overall_success_rate=round(success_total / n, 4) if n else None,
            by_task_type=rates("task_type"),
            by_model=rates("selected_model"),
            by_agent_template=rates("agent_template"),
            by_decomposition_strategy=rates("decomposition_strategy"),
            avg_rework=_safe_mean(rework),
            avg_review_score=_safe_mean(review),
            avg_test_pass_rate=_safe_mean(testpass),
            cost_estimate_error=_safe_mean(cost_err),
            time_estimate_error=_safe_mean(time_err),
            escalation_rate=round(escalations / n, 4) if n else None,
            human_override_rate=round(overrides / n, 4) if n else None,
        )

    def suggestions(self, metrics: Metrics) -> list[dict[str, str]]:
        """Rule-based, *proposed* improvements. Application is out of scope (gated)."""
        out: list[dict[str, str]] = []
        for model, rate in (metrics.by_model or {}).items():
            if rate is not None and rate < 0.6:
                out.append({
                    "type": "model_routing",
                    "proposal": f"model '{model}' success_rate={rate} < 0.6 — consider "
                                f"escalating its task types one tier",
                    "status": "proposed (requires human approval)",
                })
        for tt, rate in (metrics.by_task_type or {}).items():
            if rate is not None and rate < 0.5:
                out.append({
                    "type": "decomposition",
                    "proposal": f"task_type '{tt}' success_rate={rate} < 0.5 — revisit its "
                                f"decomposition strategy / acceptance criteria granularity",
                    "status": "proposed (requires human approval)",
                })
        if metrics.cost_estimate_error is not None and metrics.cost_estimate_error > 0.1:
            out.append({
                "type": "cost_calibration",
                "proposal": f"mean cost estimate error={metrics.cost_estimate_error} — recalibrate "
                            f"estimated_cost model",
                "status": "proposed (requires human approval)",
            })
        if metrics.avg_rework is not None and metrics.avg_rework > 1.5:
            out.append({
                "type": "review",
                "proposal": f"avg rework={metrics.avg_rework} > 1.5 — acceptance criteria may be "
                            f"underspecified; tighten contracts before execution",
                "status": "proposed (requires human approval)",
            })
        return out

    def calibrate(self, records: list[dict]) -> "Calibration":
        """Phase 2: propose concrete config values from the ledger. NEVER applies
        them — emits a reviewable diff a human can adopt by hand. Self-improvement
        starts from logs; it does not auto-rewrite routing logic."""
        m = self.compute(records)
        current = {"router_small_max": SMALL_MAX, "router_medium_max": MEDIUM_MAX}
        proposed = dict(current)
        rationale: list[str] = []

        by_model = m.by_model or {}
        small_sr = by_model.get("small")
        if small_sr is not None and small_sr < 0.7:
            proposed["router_small_max"] = max(10, SMALL_MAX - 5)
            rationale.append(
                f"small-tier success_rate={small_sr} < 0.7 → lower router small_max "
                f"{SMALL_MAX}→{proposed['router_small_max']} (route fewer tasks to small)")
        medium_sr = by_model.get("medium")
        if medium_sr is not None and medium_sr < 0.7:
            proposed["router_medium_max"] = max(proposed["router_small_max"] + 5, MEDIUM_MAX - 5)
            rationale.append(
                f"medium-tier success_rate={medium_sr} < 0.7 → lower router medium_max "
                f"{MEDIUM_MAX}→{proposed['router_medium_max']} (escalate borderline tasks)")

        # Observed mean cost/elapsed per tier, straight from the ledger.
        cost_by_tier: dict[str, list] = defaultdict(list)
        secs_by_tier: dict[str, list] = defaultdict(list)
        for r in records:
            tier = r.get("selected_model")
            if r.get("actual_cost") is not None:
                cost_by_tier[tier].append(r["actual_cost"])
            if r.get("elapsed_seconds") is not None:
                secs_by_tier[tier].append(r["elapsed_seconds"])
        observed_cost = {t: round(mean(v), 4) for t, v in cost_by_tier.items() if v}
        observed_secs = {t: round(mean(v), 3) for t, v in secs_by_tier.items() if v}
        if observed_cost:
            proposed["observed_tier_cost"] = observed_cost
            rationale.append(f"observed mean cost/tier from ledger: {observed_cost}")
        if observed_secs:
            proposed["observed_tier_seconds"] = observed_secs
            rationale.append(f"observed mean elapsed/tier from ledger: {observed_secs}")

        if not rationale:
            rationale.append("metrics within thresholds — no parameter changes proposed")
        return Calibration(current=current, proposed=proposed, rationale=rationale,
                           metrics=m.to_dict())


@dataclass
class Calibration:
    current: dict
    proposed: dict
    rationale: list[str]
    metrics: dict = field(default_factory=dict)
    status: str = "proposed (requires human approval; NOT auto-applied)"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "current": self.current, "proposed": self.proposed,
                "rationale": self.rationale, "metrics": self.metrics}
