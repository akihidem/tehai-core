"""Cross-Team Review and Competition (spec §15).

For a high-stakes design decision, the same problem is solved by competing teams
with different priorities (maintainability / speed / security); a Judge scores
each approach on 8 criteria and picks a winner. Expensive — gated to high-risk
designs, repeated failures, or an explicit request.

Deterministic model: each priority boosts the criteria it optimizes, and the
Judge weights the criteria by the goal's risk profile (a security-sensitive goal
weights security/risk highly → the security-first approach wins).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model_router import HAZARD_KEYWORDS

CRITERIA = ["requirement_fit", "impl_cost", "maintainability", "security",
            "extensibility", "testability", "risk", "future_change"]

# Higher score == better on that axis (impl_cost/risk already framed as "lower is
# better" → a positive boost means "cheaper / lower risk").
_PRIORITY_BOOST = {
    "maintainability": {"maintainability": 30, "testability": 20, "future_change": 25, "impl_cost": -15},
    "speed": {"impl_cost": 30, "requirement_fit": 10, "maintainability": -15, "security": -10, "testability": -10},
    "security": {"security": 35, "risk": 25, "requirement_fit": 10, "impl_cost": -20},
}


def _clamp(v: float) -> int:
    return max(0, min(100, int(round(v))))


@dataclass
class Approach:
    approach_id: str
    priority: str
    scores: dict
    weighted_total: float


@dataclass
class CompetitionResult:
    subject: str
    criteria_weights: dict
    approaches: list[Approach] = field(default_factory=list)
    winner: str = ""
    winner_priority: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "criteria_weights": self.criteria_weights,
            "approaches": [{"approach_id": a.approach_id, "priority": a.priority,
                            "scores": a.scores, "weighted_total": round(a.weighted_total, 2)}
                           for a in self.approaches],
            "winner": self.winner,
            "winner_priority": self.winner_priority,
            "rationale": self.rationale,
        }


class CrossTeamCompetition:
    def __init__(self, base: int = 50):
        self.base = base

    @staticmethod
    def _hazardous(goal: str) -> bool:
        low = goal.lower()
        return any(k in low or k in goal for k in HAZARD_KEYWORDS)

    def weights_for(self, goal: str) -> dict:
        w = {c: 1.0 for c in CRITERIA}
        if self._hazardous(goal):
            w["security"], w["risk"], w["requirement_fit"] = 3.0, 2.5, 1.5
        else:
            w["maintainability"], w["testability"], w["future_change"] = 2.0, 1.5, 1.5
            w["impl_cost"] = 1.5
        return w

    def run(self, subject: str, goal: str,
            priorities=("maintainability", "speed", "security")) -> CompetitionResult:
        weights = self.weights_for(goal)
        wsum = sum(weights.values())
        approaches: list[Approach] = []
        for i, pr in enumerate(priorities):
            boost = _PRIORITY_BOOST.get(pr, {})
            scores = {c: _clamp(self.base + boost.get(c, 0)) for c in CRITERIA}
            total = sum(scores[c] * weights[c] for c in CRITERIA) / wsum
            approaches.append(Approach(f"approach_{chr(65 + i)}", pr, scores, total))

        winner = max(approaches, key=lambda a: a.weighted_total)
        dominant = sorted(weights, key=weights.get, reverse=True)[:2]
        rationale = (f"goal の risk プロファイルにより {dominant} を重視 → "
                     f"{winner.priority} 案 ({winner.approach_id}) を採用 "
                     f"(weighted={winner.weighted_total:.1f})")
        return CompetitionResult(subject=subject, criteria_weights=weights,
                                 approaches=approaches, winner=winner.approach_id,
                                 winner_priority=winner.priority, rationale=rationale)
