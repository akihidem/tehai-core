"""Judge.

Compares the *grounds* of separated reviews and classifies the artifact into one
of: accept / revise / discard / rerun. Deterministic precedence so the same
evidence always yields the same verdict, with the reason recorded.
"""

from __future__ import annotations

from .models import Decision, JudgeDecision, ReviewResult, TaskContract


_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_TRANSIENT = ("flaky", "transient", "timeout", "ネットワーク", "一時的", "再試行")


class Judge:
    def decide(self, contract: TaskContract, reviews: list[ReviewResult]) -> Decision:
        basis = [f"{r.lens.value}:{r.verdict}:{r.severity}" for r in reviews]

        if not reviews:
            return Decision(
                task_id=contract.task_id, decision=JudgeDecision.ACCEPT,
                reason="no review required for this task", basis=basis,
            )

        fails = [r for r in reviews if r.verdict == "fail"]
        concerns = [r for r in reviews if r.verdict == "concerns"]

        # Transient/flaky failure -> rerun before condemning the artifact.
        if fails and any(
            any(t in (r.rationale + " " + " ".join(r.findings)).lower() for t in _TRANSIENT)
            for r in fails
        ):
            return Decision(
                contract.task_id, JudgeDecision.RERUN,
                "failure looks transient/flaky -> rerun the task", basis,
            )

        if fails:
            worst = max(_SEVERITY_RANK.get(r.severity, 0) for r in fails)
            sec_fail = any(r.lens.value == "security" for r in fails)
            if worst >= _SEVERITY_RANK["critical"] or (sec_fail and worst >= _SEVERITY_RANK["high"]):
                return Decision(
                    contract.task_id, JudgeDecision.DISCARD,
                    "critical/security failure -> discard artifact and restart from contract",
                    basis,
                )
            return Decision(
                contract.task_id, JudgeDecision.REVISE,
                f"{len(fails)} failing review(s), worst severity rank {worst} -> revise",
                basis,
            )

        if concerns:
            return Decision(
                contract.task_id, JudgeDecision.ACCEPT,
                f"all reviews pass with {len(concerns)} non-blocking concern(s) noted",
                basis,
            )

        return Decision(
            contract.task_id, JudgeDecision.ACCEPT, "all reviews pass", basis,
        )
