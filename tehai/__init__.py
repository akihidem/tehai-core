"""tehai — 手配: a controlled, auditable delegation layer.

Turn a request into a DAG of small, contract-bound subtasks; route each to the
cheapest capable model; review by risk; judge; log. Registry-bound agents,
bounded recursion, least-privilege permissions.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .architect import TaskArchitect
from .backends import (
    ClaudeCliBackend, EchoBackend, ModelBackend, NullBackend, OllamaBackend, get_backend,
)
from .decompose_guard import DecompositionGuard, DecompositionState, GuardConfig
from .evaluation import Calibration, EvaluationStore
from .executor import ExecutionResult, Executor
from .flat_dataflow import Defect, FlatDataflowOrchestrator, FlatRunResult
from .judge import Judge
from .logger import ExecutionLogger, LogRecord
from .model_router import ModelRouter
from .models import (
    Action,
    AgentTemplate,
    Decision,
    Effort,
    JudgeDecision,
    ModelTier,
    OrgTemplate,
    ReviewLens,
    ReviewPlan,
    RunPlan,
    ScoreProfile,
    TaskContract,
    TaskStatus,
    TaskType,
)
from .orchestrator import Orchestrator
from .permissions import ActionDecision, PermissionModel
from .registry import AgentRegistry
from .review_planner import ReviewPlanner
from .reviewer import Reviewer
from .sandbox import Sandbox, SandboxResult
from .scoring import Scorer

__all__ = [
    "__version__",
    "Action",
    "ActionDecision",
    "AgentRegistry",
    "AgentTemplate",
    "Calibration",
    "ClaudeCliBackend",
    "Decision",
    "DecompositionGuard",
    "DecompositionState",
    "EchoBackend",
    "Effort",
    "EvaluationStore",
    "ExecutionResult",
    "Executor",
    "Defect",
    "FlatDataflowOrchestrator",
    "FlatRunResult",
    "GuardConfig",
    "ModelBackend",
    "NullBackend",
    "OllamaBackend",
    "get_backend",
    "Judge",
    "JudgeDecision",
    "ExecutionLogger",
    "LogRecord",
    "ModelRouter",
    "ModelTier",
    "Orchestrator",
    "OrgTemplate",
    "PermissionModel",
    "ReviewLens",
    "ReviewPlan",
    "ReviewPlanner",
    "Reviewer",
    "RunPlan",
    "Sandbox",
    "SandboxResult",
    "ScoreProfile",
    "Scorer",
    "TaskArchitect",
    "TaskContract",
    "TaskStatus",
    "TaskType",
]
