"""Team Registry: load + select team templates; convert a team to a tehai
OrgTemplate so the Team Orchestrator can reuse the existing Task Architect.

Registry-first: the Meta Orchestrator never fabricates a team; a genuinely novel
need is recorded as a proposal (propose_new_team), never auto-created.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import OrgTemplate, Phase, TaskType
from .models import TeamTemplate


DATA_DIR = Path(__file__).parent.parent / "data" / "teams"


class TeamRegistryError(Exception):
    pass


class TeamRegistry:
    def __init__(self, teams: dict[str, TeamTemplate]):
        self._teams = teams
        self.proposals: list[dict] = []

    @classmethod
    def load(cls, data_dir: Path | str = DATA_DIR) -> "TeamRegistry":
        data_dir = Path(data_dir)
        teams: dict[str, TeamTemplate] = {}
        files = sorted(data_dir.glob("*.json"))
        if not files:
            raise TeamRegistryError(f"no team templates under {data_dir}")
        for fp in files:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            for entry in (raw if isinstance(raw, list) else [raw]):
                t = TeamTemplate.from_dict(entry)
                if t.team_id in teams:
                    raise TeamRegistryError(f"duplicate team id: {t.team_id}")
                teams[t.team_id] = t
        return cls(teams)

    def get(self, team_id: str) -> TeamTemplate:
        if team_id not in self._teams:
            raise TeamRegistryError(f"unknown team: {team_id}")
        return self._teams[team_id]

    def __contains__(self, team_id: str) -> bool:
        return team_id in self._teams

    def all(self) -> list[TeamTemplate]:
        return list(self._teams.values())

    def ids(self) -> list[str]:
        return list(self._teams.keys())

    def to_org_template(self, team: TeamTemplate) -> OrgTemplate:
        """Wrap a team's internal phase pipeline as a tehai OrgTemplate."""
        phases = [
            Phase(
                key=p["key"], title=p.get("title", p["key"]), role=p["role"],
                task_type=TaskType(p["task_type"]), depends_on=list(p.get("depends_on", [])),
            )
            for p in team.phases
        ]
        return OrgTemplate(
            org_template_id=f"team:{team.team_id}",
            name=team.team_name,
            description=team.mission,
            roles=list(team.internal_agents),
            phases=phases,
            matches_keywords=list(team.matches_keywords),
        )

    def forbids(self, team: TeamTemplate, task_type: str) -> bool:
        return task_type in team.forbidden_task_types

    def propose_new_team(self, name: str, reason: str) -> dict:
        proposal = {"team": name, "reason": reason}
        self.proposals.append(proposal)
        return proposal
