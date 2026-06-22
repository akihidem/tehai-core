"""Global Evaluation Store (spec §20).

Evaluates not just per-task logs but cross-team allocation, hand-offs, and
reroutes. Surfaces metrics + *proposed* improvements (never auto-applied).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _TeamStat:
    runs: int = 0
    completed: int = 0
    failed: int = 0
    reroutes: int = 0


class GlobalEvaluationStore:
    def __init__(self):
        self.runs: list[dict] = []
        self.team_stats: dict[str, _TeamStat] = defaultdict(_TeamStat)
        self.failure_type_seen: dict[str, int] = defaultdict(int)
        self.failure_type_resolved: dict[str, int] = defaultdict(int)
        self.human_interventions = 0

    def record_run(self, meta_result, team_results: dict) -> None:
        self.runs.append(meta_result.to_dict() if hasattr(meta_result, "to_dict") else dict(meta_result))
        if meta_result.human_intervention_required:
            self.human_interventions += 1
        for tr in team_results.values():
            st = self.team_stats[tr.team_id]
            st.runs += 1
            if tr.ok:
                st.completed += 1
            else:
                st.failed += 1
            if tr.loop_count > 0:
                st.reroutes += 1
        for h in meta_result.loop_history:
            ft = h["failure"]["failure_type"]
            self.failure_type_seen[ft] += 1
        # A failure_type counts resolved when the run ultimately completed.
        if meta_result.final_status == "completed":
            for h in meta_result.loop_history:
                self.failure_type_resolved[h["failure"]["failure_type"]] += 1

    def metrics(self) -> dict[str, Any]:
        by_team = {}
        for tid, st in sorted(self.team_stats.items()):
            by_team[tid] = {
                "runs": st.runs,
                "success_rate": round(st.completed / st.runs, 3) if st.runs else None,
                "reroute_rate": round(st.reroutes / st.runs, 3) if st.runs else None,
            }
        resolution = {}
        for ft, seen in sorted(self.failure_type_seen.items()):
            resolved = self.failure_type_resolved.get(ft, 0)
            resolution[ft] = {"seen": seen, "resolved": resolved,
                              "resolution_rate": round(resolved / seen, 3) if seen else None}
        return {
            "n_runs": len(self.runs),
            "by_team": by_team,
            "failure_type_resolution": resolution,
            "human_intervention_rate": round(self.human_interventions / len(self.runs), 3) if self.runs else None,
        }

    def suggestions(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for tid, st in self.team_stats.items():
            if st.runs and st.completed / st.runs < 0.6:
                out.append({"type": "team_allocation",
                            "proposal": f"team '{tid}' success_rate<0.6 — revisit its Team Contract granularity",
                            "status": "proposed (requires human approval)"})
        for ft, seen in self.failure_type_seen.items():
            resolved = self.failure_type_resolved.get(ft, 0)
            if seen >= 2 and resolved / seen < 0.5:
                out.append({"type": "failure_routing",
                            "proposal": f"failure_type '{ft}' resolution_rate<0.5 — revisit its route",
                            "status": "proposed (requires human approval)"})
        return out
