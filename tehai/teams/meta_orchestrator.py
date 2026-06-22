"""Meta Orchestrator.

Turns a product goal into a DAG of Team Contracts, assigns each to a registered
team, runs them in dependency order, and on failure routes the contract to the
team that can fix the root cause (not a blind same-team retry) — all under the
Autonomous Loop Guard. Reuses the entire tehai single-team pipeline underneath.
"""

from __future__ import annotations

import hashlib
from collections import deque
from typing import Optional

from ..model_router import HAZARD_KEYWORDS
from ..orchestrator import Orchestrator
from ..org_templates import _looks_trivial
from .competition import CrossTeamCompetition
from .failure_router import FailureRouter
from .global_eval import GlobalEvaluationStore
from .loop_guard import AutonomousLoopGuard, LoopGuardConfig, LoopState
from .models import (
    AutonomyLevel, ClarificationReport, FailureType, MetaRunResult,
    StopClassification, TeamContract, TeamTaskStatus,
)
from .registry import TeamRegistry
from .team_orchestrator import TeamOrchestrator


# Canonical inter-team dependencies (filtered to the selected composition).
_TEAM_DEPS = {
    "product_planning_team": [],
    "architecture_team": ["product_planning_team"],
    "implementation_team": ["architecture_team"],
    "verification_team": ["implementation_team"],
    "security_team": ["implementation_team"],
    "integration_team": ["verification_team", "security_team"],
    "documentation_team": ["integration_team"],
}
_FULL_ORDER = ["product_planning_team", "architecture_team", "implementation_team",
               "verification_team", "security_team", "integration_team", "documentation_team"]

_DOC_KEYWORDS = ("readme", "ドキュメント", "document", "文書", "手順", "誤字", "typo", "整形")
_IMPL_KEYWORDS = ("実装", "code", "コード", "機能", "feature", "implement", "api", "修正", "追加")


def _hazardous(text: str) -> bool:
    low = text.lower()
    return any(k in low or k in text for k in HAZARD_KEYWORDS)


class MetaOrchestrator:
    def __init__(self, team_registry: TeamRegistry, orchestrator: Orchestrator,
                 team_orchestrator: Optional[TeamOrchestrator] = None,
                 failure_router: Optional[FailureRouter] = None,
                 loop_guard: Optional[AutonomousLoopGuard] = None,
                 global_eval: Optional[GlobalEvaluationStore] = None,
                 logger=None):
        self.teams = team_registry
        self.orchestrator = orchestrator
        self.router = failure_router or FailureRouter()
        self.loop_guard = loop_guard or AutonomousLoopGuard()
        self.team_orch = team_orchestrator or TeamOrchestrator(orchestrator, team_registry, self.router)
        self.global_eval = global_eval or GlobalEvaluationStore()
        self.logger = logger

    @classmethod
    def default(cls, backend: str = "null", sandbox: bool = False,
                max_loops: int = 6) -> "MetaOrchestrator":
        orch = Orchestrator.default(backend, sandbox=sandbox)
        return cls(
            team_registry=TeamRegistry.load(),
            orchestrator=orch,
            loop_guard=AutonomousLoopGuard(LoopGuardConfig(max_loops=max_loops)),
        )

    # ----- composition ----- #
    def select_team_composition(self, goal: str) -> list[str]:
        low = goal.lower()
        doc_only = _looks_trivial(goal) or (
            any(k in low or k in goal for k in _DOC_KEYWORDS)
            and not any(k in low or k in goal for k in _IMPL_KEYWORDS)
        )
        if doc_only:
            return ["documentation_team"]
        teams = [t for t in _FULL_ORDER if t != "security_team"]
        if _hazardous(goal):
            # Security Team joins (before integration) — never bolted on at the end.
            teams.insert(teams.index("integration_team"), "security_team")
        return [t for t in teams if t in self.teams]

    def build_team_contracts(self, goal: str, composition: list[str],
                             autonomy: AutonomyLevel) -> list[TeamContract]:
        idx = {tid: f"TT-{i:03d}" for i, tid in enumerate(composition)}
        contracts: list[TeamContract] = []
        present = set(composition)
        for tid in composition:
            team = self.teams.get(tid)
            deps = [idx[d] for d in _TEAM_DEPS.get(tid, []) if d in present]
            downstream = [idx[o] for o in composition
                          if tid in _TEAM_DEPS.get(o, []) and o in present]
            contracts.append(TeamContract(
                team_task_id=idx[tid],
                assigned_team=tid,
                objective=f"{team.mission}（目標: {goal.strip()[:90]}）",
                input_artifacts=list(team.required_inputs),
                expected_outputs=list(team.expected_outputs),
                acceptance_criteria=[f"{team.team_name} の責務を満たす成果物がある", "受入条件がレビュー可能"],
                constraints=["既存APIを破壊しない", "秘密情報をログに出さない", "本番環境に接続しない"],
                dependencies=deps,
                downstream_teams=downstream,
                escalation_conditions=list(team.escalation_rules),
                autonomy_level=autonomy,
            ))
        return contracts

    @staticmethod
    def _topo(contracts: list[TeamContract]) -> list[str]:
        ids = {c.team_task_id for c in contracts}
        indeg = {c.team_task_id: 0 for c in contracts}
        adj: dict[str, list[str]] = {c.team_task_id: [] for c in contracts}
        for c in contracts:
            for d in c.dependencies:
                if d in ids:
                    adj[d].append(c.team_task_id)
                    indeg[c.team_task_id] += 1
        q = deque(sorted(t for t, d in indeg.items() if d == 0))
        order = []
        while q:
            n = q.popleft()
            order.append(n)
            for m in sorted(adj[n]):
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)
        return order

    @staticmethod
    def _derive_run_id(goal: str) -> str:
        return "MR-" + hashlib.sha1(goal.strip().encode("utf-8")).hexdigest()[:8]

    # ----- the multi-team loop ----- #
    def _downstream(self, ttid: str, by_id: dict) -> list[str]:
        """Transitive dependents of a team contract, in topological order."""
        order = self._topo(list(by_id.values()))
        pos = {t: i for i, t in enumerate(order)}
        dependents: set = set()
        frontier = [ttid]
        while frontier:
            cur = frontier.pop()
            for c in by_id.values():
                if cur in c.dependencies and c.team_task_id not in dependents:
                    dependents.add(c.team_task_id)
                    frontier.append(c.team_task_id)
        return sorted(dependents, key=lambda t: pos.get(t, 0))

    def run(self, goal: str, injected_failures: Optional[dict] = None,
            autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED,
            compete: bool = False, run_id: Optional[str] = None) -> MetaRunResult:
        if not goal or not goal.strip():
            raise ValueError("empty goal")
        run_id = run_id or self._derive_run_id(goal)
        # injected_failures: {team_id: (FailureType, resolve_after_n_failures)}
        injected = {k: (v if isinstance(v, tuple) else (v, 1)) for k, v in (injected_failures or {}).items()}

        composition = self.select_team_composition(goal)
        contracts = self.build_team_contracts(goal, composition, autonomy)
        by_id = {c.team_task_id: c for c in contracts}
        team_to_ttid = {c.assigned_team: c.team_task_id for c in contracts}
        order = self._topo(contracts)

        res = MetaRunResult(run_id=run_id, goal=goal, team_composition=composition,
                            team_contracts=contracts, execution_order=order)
        backend_name = getattr(self.orchestrator.backend, "name", "null")
        exec_note = (
            "決定的オフライン実行（実モデル呼び出しなし）。LLM/sandbox は tehai backend 経由で後付け可。"
            if backend_name == "null" else
            f"実行は tehai backend '{backend_name}' 経由（実モデル呼び出しあり）。"
        )
        res.assumptions = [
            exec_note,
            f"チーム構成はキーワード分類で {composition} を選択。",
            "失敗は failure_type で分類し、原因チームへ差し戻す（盲目的な同一チーム再実行はしない）。",
        ]

        # §15 Cross-Team Competition — high-stakes design solved by competing
        # approaches (maintainability/speed/security), Judge picks the winner.
        if (compete or _hazardous(goal)) and "architecture_team" in composition:
            comp = CrossTeamCompetition().run("architecture", goal)
            res.competitions.append(comp.to_dict())
            res.assumptions.append(
                f"高リスク設計のため cross-team competition を実行（採用案: {comp.winner_priority}）。")

        state = LoopState()
        team_results: dict = {}
        completed: set = set()
        fail_counts: dict = {}      # team_id -> times failed
        queue = deque(order)
        iterations = 0

        while queue and iterations < 60:
            iterations += 1
            ttid = queue.popleft()
            c = by_id[ttid]
            if ttid in completed:
                continue

            forced = None
            spec = injected.get(c.assigned_team)
            if spec and fail_counts.get(c.assigned_team, 0) < spec[1]:
                forced = spec[0]

            tr = self.team_orch.run(c, forced_failure=forced, counter=fail_counts.get(c.assigned_team, 0) + 1)
            team_results[ttid] = tr

            if tr.status in (TeamTaskStatus.COMPLETED, TeamTaskStatus.INTEGRATED):
                completed.add(ttid)
                continue

            # ---- failure path ----
            f = tr.failures[0]
            fail_counts[c.assigned_team] = fail_counts.get(c.assigned_team, 0) + 1
            same_count = state.record(f)
            risk_high = _hazardous(goal) or f.failure_type == FailureType.SECURITY_RISK
            gd = self.loop_guard.evaluate(
                f, state, risk_high=risk_high,
                prod_or_external=_hazardous(goal) and f.failure_type == FailureType.SECURITY_RISK,
                autonomy=autonomy, same_failure_count=same_count)

            res.loop_history.append({
                "iteration": iterations,
                "team": c.assigned_team,
                "failure": f.to_dict(),
                "guard": {"allow": gd.allow_autorun,
                          "stop": gd.stop.value if gd.stop else None, "reason": gd.reason},
            })

            # Assemble a Clarification Report before any human is involved (§14.1).
            if f.failure_type == FailureType.REQUIREMENT_AMBIGUITY:
                res.clarification_reports.append(ClarificationReport(
                    task_id=ttid,
                    what_is_unclear=[f"{c.assigned_team} の成果物と受入条件が対応していない", *f.evidence],
                    options=["要件を再定義して進める", "スコープを縮小する", "人間に確認する"],
                    recommendation="Product Planning Team で受入条件とAPI仕様を再定義する",
                    recommendation_risks=["再定義により下流チームの再実行コストが発生する"],
                    human_judgment_needed=not f.retry_allowed,
                ).to_dict())

            if gd.allow_autorun and f.recommended_route in team_to_ttid:
                # Re-route: fix the root-cause team, then re-run it AND its downstream
                # subtree (cascading reroute), so dependents see the corrected output.
                route_ttid = team_to_ttid[f.recommended_route]
                by_id[route_ttid].loop_count += 1
                c.loop_count += 1
                c.status = TeamTaskStatus.RETURNED_FOR_REVISION
                if route_ttid != ttid:
                    cascade = [route_ttid] + self._downstream(route_ttid, by_id)
                    if ttid not in cascade:
                        cascade.append(ttid)
                    for t in cascade:
                        completed.discard(t)
                        if t in queue:
                            queue.remove(t)
                    queue.extendleft(reversed(cascade))   # re-run in topological order
                else:
                    queue.appendleft(ttid)
            else:
                # Stop: classify and (if needed) require a human.
                res.final_status = (gd.stop or StopClassification.STOP_AS_FAILED).value
                if gd.stop == StopClassification.REQUEST_HUMAN_APPROVAL:
                    res.human_intervention_required = True
                break
        else:
            pass

        if res.final_status == "pending":
            res.final_status = "completed" if len(completed) == len(contracts) else "partial"

        res.team_results = team_results
        res.metrics = self._metrics(res, team_results, state, completed)
        self.global_eval.record_run(res, team_results)
        return res

    # ----- metrics ----- #
    def _metrics(self, res: MetaRunResult, team_results: dict, state: LoopState,
                 completed: set) -> dict:
        n_teams = len(res.team_contracts)
        task_count = sum(len(tr.plan.contracts) for tr in team_results.values() if tr.plan)
        first_pass = sum(1 for tr in team_results.values()
                         if tr.ok and tr.loop_count == 0)
        n_completed = len(completed)
        failures = res.loop_history
        resolved = sum(1 for ttid in completed if by_loops(team_results, ttid) > 0)
        security_findings = sum(1 for h in failures if h["failure"]["failure_type"] == "security_risk")
        return {
            "system_type": "multi_team_agentops",
            "task_success": res.final_status == "completed",
            "autonomous_completion": res.final_status == "completed" and not res.human_intervention_required,
            "human_intervention_required": res.human_intervention_required,
            "loop_count": state.loop_count,
            "team_count": n_teams,
            "task_count": task_count,
            "failed_task_count": len(failures),
            "resolved_failure_count": resolved,
            "test_pass_rate_before": round(first_pass / n_teams, 3) if n_teams else None,
            "test_pass_rate_after": round(n_completed / n_teams, 3) if n_teams else None,
            "security_findings_count": security_findings,
            "unresolved_security_findings": security_findings if res.human_intervention_required else 0,
            "human_intervention_rate": 1.0 if res.human_intervention_required else 0.0,
            "failure_types": [h["failure"]["failure_type"] for h in failures],
        }


def by_loops(team_results: dict, ttid: str) -> int:
    tr = team_results.get(ttid)
    return tr.loop_count if tr else 0
