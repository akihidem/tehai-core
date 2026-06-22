"""Failure Router.

A failure never blindly re-runs the same team. It is classified into a
failure_type and routed to the team that can actually fix the root cause
(spec §11/§12). Routing is a deterministic table; classification maps observed
signals to a failure_type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Failure, FailureType


# failure_type -> (recommended_route, recommended_action, retry_allowed, requires_human_approval)
_ROUTING = {
    FailureType.REQUIREMENT_AMBIGUITY: ("product_planning_team", "受入条件とAPI仕様を再定義する", True, False),
    FailureType.ARCHITECTURE_CONFLICT: ("architecture_team", "設計を見直しモジュール境界を再定義する", True, False),
    FailureType.IMPLEMENTATION_ERROR: ("implementation_team", "実装を修正し再テストする", True, False),
    FailureType.TEST_FAILURE: ("implementation_team", "失敗したテストの原因を修正する", True, False),
    FailureType.SECURITY_RISK: ("security_team", "脅威を分析しリスク採否を判断する", True, True),
    FailureType.INTEGRATION_CONFLICT: ("integration_team", "コンフリクトを解消し再統合する", True, False),
    FailureType.COST_OVERRUN: ("meta", "スコープを縮小するか上位に判断を仰ぐ", False, False),
    FailureType.CONTEXT_OVERFLOW: ("architecture_team", "タスクをさらに小さく再分解する", True, False),
    FailureType.PERMISSION_VIOLATION: ("human", "権限境界違反のため停止し人間承認を求める", False, True),
    FailureType.REPEATED_FAILURE: ("human", "同一失敗が反復したため停止し人間判断を求める", False, True),
    FailureType.UNKNOWN_FAILURE: ("meta", "上位モデルへエスカレーションするか人間承認を求める", False, True),
}


@dataclass
class FailureSignal:
    """What the Meta/Team layer observed when something went wrong."""

    task_id: str
    detected_by: str
    failure_type: Optional[FailureType] = None   # explicit, if known
    contract_invalid: bool = False
    security_finding: bool = False
    judge_discard: bool = False
    test_failed: bool = False
    integration_conflict: bool = False
    over_budget: bool = False
    context_over_limit: bool = False
    permission_denied: bool = False
    repeated: bool = False
    evidence: tuple = ()


class FailureRouter:
    def route(self, failure_type: FailureType) -> tuple[str, str, bool, bool]:
        return _ROUTING[failure_type]

    def classify(self, signal: FailureSignal, counter: int = 1) -> Failure:
        ft = signal.failure_type or self._infer(signal, counter)
        route, action, retry, human = self.route(ft)
        return Failure(
            failure_id=f"F-{signal.task_id}-{ft.value}",
            task_id=signal.task_id,
            failure_type=ft,
            detected_by=signal.detected_by,
            evidence=list(signal.evidence),
            recommended_route=route,
            recommended_action=action,
            retry_allowed=retry,
            requires_human_approval=human,
        )

    @staticmethod
    def _infer(s: FailureSignal, counter: int) -> FailureType:
        # Order matters: most specific / highest-stakes first.
        if s.permission_denied:
            return FailureType.PERMISSION_VIOLATION
        if s.security_finding:
            return FailureType.SECURITY_RISK
        if counter >= 3 or s.repeated:
            return FailureType.REPEATED_FAILURE
        if s.over_budget:
            return FailureType.COST_OVERRUN
        if s.context_over_limit:
            return FailureType.CONTEXT_OVERFLOW
        if s.contract_invalid:
            return FailureType.REQUIREMENT_AMBIGUITY
        if s.integration_conflict:
            return FailureType.INTEGRATION_CONFLICT
        if s.test_failed:
            return FailureType.TEST_FAILURE
        if s.judge_discard:
            return FailureType.IMPLEMENTATION_ERROR
        return FailureType.UNKNOWN_FAILURE
