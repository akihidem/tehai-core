"""Multi-Team AgentOps layer on top of the tehai single-team primitives.

Meta Orchestrator → Team Contracts → Team Orchestrators → (tehai pipeline) →
Failure Router → Autonomous Loop Guard → Global Evaluation Store.
"""

from __future__ import annotations

from .competition import CompetitionResult, CrossTeamCompetition
from .failure_router import FailureRouter, FailureSignal
from .global_eval import GlobalEvaluationStore
from .loop_guard import AutonomousLoopGuard, LoopGuardConfig, LoopState
from .meta_orchestrator import MetaOrchestrator
from .models import (
    AutonomyLevel,
    ClarificationReport,
    Failure,
    FailureType,
    MetaRunResult,
    StopClassification,
    TeamContract,
    TeamResult,
    TeamTaskStatus,
    TeamTemplate,
)
from .registry import TeamRegistry
from .team_orchestrator import TeamOrchestrator

__all__ = [
    "AutonomousLoopGuard",
    "AutonomyLevel",
    "ClarificationReport",
    "CompetitionResult",
    "CrossTeamCompetition",
    "Failure",
    "FailureRouter",
    "FailureSignal",
    "FailureType",
    "GlobalEvaluationStore",
    "LoopGuardConfig",
    "LoopState",
    "MetaOrchestrator",
    "MetaRunResult",
    "StopClassification",
    "TeamContract",
    "TeamOrchestrator",
    "TeamRegistry",
    "TeamResult",
    "TeamTaskStatus",
    "TeamTemplate",
]
