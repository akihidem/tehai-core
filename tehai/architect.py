"""Task Architect.

Turns a request + chosen organization template into a DAG of contract-bound
subtasks. Deterministic by default (heuristic scoring + per-type artifact
templates). A ``ModelBackend`` may be supplied later to replace the heuristics
with LLM-authored decomposition — the orchestrator does not change.

It also demonstrates *guarded* recursive decomposition: an oversized
implementation task is split one level deeper, but only if the
``DecompositionGuard`` permits it.
"""

from __future__ import annotations

import json
import re

from .decompose_guard import DecompositionGuard, DecompositionState, _has_cycle
from .models import (
    ModelTier, OrgTemplate, Phase, ScoreProfile, TaskContract, TaskType,
)
from .model_router import HAZARD_KEYWORDS


class LLMDecompositionError(Exception):
    """Raised when an LLM-proposed decomposition is unusable; triggers fallback."""


_VALID_TASK_TYPES = ", ".join(t.value for t in TaskType)


def _extract_json(text: str):
    """Pull the first JSON value out of an LLM response (tolerates ``` fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Balanced-bracket scan for the first array/object.
    start = next((i for i, ch in enumerate(text) if ch in "[{"), None)
    if start is None:
        raise LLMDecompositionError("no JSON found in model output")
    opench = text[start]
    closech = "]" if opench == "[" else "}"
    depth = 0
    in_str = esc = False
    for j in range(start, len(text)):
        ch = text[j]
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == opench:
            depth += 1
        elif ch == closech:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:j + 1])
                except Exception as e:
                    raise LLMDecompositionError(f"malformed JSON: {e}")
    raise LLMDecompositionError("unbalanced JSON in model output")


# Per-task-type heuristic baselines (0-100). Tunable; documented in ASSUMPTIONS.md.
_COMPLEXITY = {
    TaskType.CLASSIFICATION: 10, TaskType.EXTRACTION: 15, TaskType.DOC_FORMATTING: 10,
    TaskType.SUMMARIZATION: 20, TaskType.RESEARCH: 55, TaskType.SPEC_DESIGN: 55,
    TaskType.ARCHITECTURE: 72, TaskType.CODE_IMPLEMENTATION: 48, TaskType.CODE_REVIEW: 35,
    TaskType.TEST_AUTHORING: 25, TaskType.QA: 30, TaskType.SECURITY_REVIEW: 75,
    TaskType.RELEASE: 40, TaskType.INCIDENT_RESPONSE: 62, TaskType.CONTENT: 35,
    TaskType.INTEGRATION: 65, TaskType.GENERIC: 40,
}
_DOMAIN_SPEC = {
    TaskType.SECURITY_REVIEW: 80, TaskType.ARCHITECTURE: 65, TaskType.INCIDENT_RESPONSE: 60,
    TaskType.RESEARCH: 55, TaskType.RELEASE: 45, TaskType.CODE_IMPLEMENTATION: 40,
    TaskType.DOC_FORMATTING: 10, TaskType.EXTRACTION: 15, TaskType.CLASSIFICATION: 10,
}
_TOOL_RISK = {
    TaskType.RELEASE: 70, TaskType.INCIDENT_RESPONSE: 60, TaskType.CODE_IMPLEMENTATION: 35,
    TaskType.SECURITY_REVIEW: 30, TaskType.CODE_REVIEW: 15, TaskType.DOC_FORMATTING: 5,
}
_STEPS = {
    TaskType.ARCHITECTURE: 7, TaskType.SECURITY_REVIEW: 7, TaskType.CODE_IMPLEMENTATION: 8,
    TaskType.INTEGRATION: 6, TaskType.RESEARCH: 6, TaskType.SPEC_DESIGN: 5,
    TaskType.QA: 4, TaskType.CODE_REVIEW: 3, TaskType.TEST_AUTHORING: 3,
    TaskType.CONTENT: 5, TaskType.DOC_FORMATTING: 2, TaskType.EXTRACTION: 2,
    TaskType.CLASSIFICATION: 1, TaskType.RELEASE: 3, TaskType.INCIDENT_RESPONSE: 5,
}
_CTX_TOKENS = {
    TaskType.ARCHITECTURE: 32000, TaskType.INTEGRATION: 40000, TaskType.SECURITY_REVIEW: 30000,
    TaskType.CODE_IMPLEMENTATION: 12000, TaskType.RESEARCH: 28000, TaskType.SPEC_DESIGN: 9000,
    TaskType.QA: 8000, TaskType.CODE_REVIEW: 9000, TaskType.TEST_AUTHORING: 6000,
    TaskType.CONTENT: 7000, TaskType.DOC_FORMATTING: 3000, TaskType.EXTRACTION: 3000,
}

_VAGUE = ("全部", "よしなに", "いい感じ", "なんとか", "全体", "as appropriate", "somehow")

_EXPECTED_OUTPUT = {
    TaskType.SPEC_DESIGN: ["requirements.md"],
    TaskType.ARCHITECTURE: ["architecture.md", "interfaces.md"],
    TaskType.CODE_IMPLEMENTATION: ["{slug}.py"],
    TaskType.TEST_AUTHORING: ["test_{slug}.py"],
    TaskType.CODE_REVIEW: ["code_review_report.md"],
    TaskType.QA: ["qa_report.md"],
    TaskType.RELEASE: ["release_notes.md"],
    TaskType.RESEARCH: ["findings.md"],
    TaskType.INTEGRATION: ["synthesis.md"],
    TaskType.SECURITY_REVIEW: ["security_review.md"],
    TaskType.CONTENT: ["draft.md"],
    TaskType.DOC_FORMATTING: ["polished.md"],
    TaskType.INCIDENT_RESPONSE: ["incident_log.md"],
    TaskType.SUMMARIZATION: ["summary.md"],
    TaskType.EXTRACTION: ["extracted.json"],
    TaskType.CLASSIFICATION: ["labels.json"],
    TaskType.GENERIC: ["artifact.md"],
}

_REQUIRED_TOOLS = {
    TaskType.CODE_IMPLEMENTATION: ["read_file", "write_file", "run_test"],
    TaskType.TEST_AUTHORING: ["read_file", "write_file", "run_test"],
    TaskType.CODE_REVIEW: ["read_repository", "run_static_analysis", "write_review_report"],
    TaskType.SECURITY_REVIEW: ["read_repository", "run_static_analysis", "write_review_report"],
    TaskType.QA: ["read_repository", "run_test", "write_review_report"],
    TaskType.RELEASE: ["read_repository", "run_test"],
    TaskType.RESEARCH: ["read_file", "search", "web_fetch"],
    TaskType.ARCHITECTURE: ["read_repository", "write_file"],
    TaskType.SPEC_DESIGN: ["read_file", "write_file"],
}

STD_ESCALATION = [
    "仕様矛盾を検出した場合",
    "必要コンテキストが上限を超える場合",
    "2回連続でテストまたはレビューに失敗した場合",
]
STD_DONE = [
    "expected_output がすべて生成されている",
    "acceptance_criteria をすべて満たしている",
    "必要なレビューを通過している",
]

# An implementation task above this complexity (and step) budget is a candidate
# for one level of guarded sub-decomposition.
SUBDIVIDE_COMPLEXITY = 45
SUBDIVIDE_STEPS = 6


def _slug(request: str) -> str:
    # Require tokens with alphabetic content (a bare "8" from "8文字" is not a slug).
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", request.lower()) if len(w) >= 2]
    if words:
        return "_".join(words[:3])[:24]
    return "task"


def _clamp(v: int) -> int:
    return max(0, min(100, v))


class TaskArchitect:
    def __init__(self, guard: DecompositionGuard | None = None, backend=None):
        self.guard = guard or DecompositionGuard()
        self.backend = backend  # optional ModelBackend; None/unavailable -> heuristics
        self.last_strategy = "template"   # "template" | "llm"
        self.last_error: str | None = None

    # ----- scoring ----- #
    def _score(self, request: str, task_type: TaskType) -> ScoreProfile:
        low = request.lower()
        n = len(request)

        complexity = _COMPLEXITY.get(task_type, 40)
        if any(k in low for k in ("multiple", "複数", "across", "全体", "整合")):
            complexity += 12
        if n > 160:
            complexity += 8

        ambiguity = 22
        if any(v in request or v in low for v in _VAGUE):
            ambiguity += 28
        if n < 48:
            ambiguity += 18
        if task_type in (TaskType.SPEC_DESIGN, TaskType.RESEARCH, TaskType.ARCHITECTURE):
            ambiguity += 12

        hazard_hits = [k for k in HAZARD_KEYWORDS if k in low or k in request]
        risk = 18
        if hazard_hits:
            risk += 45
        if task_type in (TaskType.SECURITY_REVIEW, TaskType.RELEASE, TaskType.ARCHITECTURE):
            risk += 25
        if task_type == TaskType.INCIDENT_RESPONSE:
            risk += 30

        ctx_tokens = _CTX_TOKENS.get(task_type, 8000)
        context_size = _clamp(int(ctx_tokens / 600))
        if any(k in low for k in ("既存", "リポジトリ", "repository", "codebase", "全体")):
            context_size = _clamp(context_size + 15)

        tool_risk = _TOOL_RISK.get(task_type, 12)
        if hazard_hits:
            tool_risk += 25

        domain = _DOMAIN_SPEC.get(task_type, 30)

        return ScoreProfile(
            complexity=_clamp(complexity),
            ambiguity=_clamp(ambiguity),
            risk=_clamp(risk),
            context_size=_clamp(context_size),
            tool_risk=_clamp(tool_risk),
            domain_specialization=_clamp(domain),
        )

    def _acceptance(self, task_type: TaskType, request: str) -> list[str]:
        base = {
            TaskType.CODE_IMPLEMENTATION: ["受入条件を満たす実装が存在する", "既存テストがすべて通る"],
            TaskType.TEST_AUTHORING: ["境界条件を含むテストが追加される", "テストが実行可能"],
            TaskType.CODE_REVIEW: ["各受入条件に pass/fail が付く", "指摘がコード位置付き"],
            TaskType.SECURITY_REVIEW: ["重大度付きで脆弱性が報告される", "再現条件または攻撃シナリオが示される"],
            TaskType.QA: ["失敗が再現条件付きで報告される"],
            TaskType.ARCHITECTURE: ["主要判断に理由がある", "境界条件が明示される"],
            TaskType.SPEC_DESIGN: ["成果物が1文で定義される", "受入条件がレビュー可能"],
            TaskType.RESEARCH: ["主張に出典がある", "問いに対する結論がある"],
            TaskType.INTEGRATION: ["各サブ成果物が統合される", "矛盾が解消される"],
            TaskType.CONTENT: ["構成に沿った本文がある"],
            TaskType.DOC_FORMATTING: ["表記が統一される"],
            TaskType.RELEASE: ["全ゲート通過が確認される"],
            TaskType.INCIDENT_RESPONSE: ["封じ込めと復旧が分離される"],
        }.get(task_type, ["成果物がレビュー可能な形で存在する"])
        return list(base)

    # ----- contract construction ----- #
    def _contract(
        self, task_id: str, phase: Phase, request: str, run_id: str,
        depth: int = 0, parent_task_id: str | None = None, dep_ids: list[str] | None = None,
    ) -> TaskContract:
        tt = phase.task_type
        slug = _slug(request)
        outputs = [o.replace("{slug}", slug) for o in _EXPECTED_OUTPUT.get(tt, ["artifact.md"])]
        scores = self._score(request, tt)
        objective = f"{phase.title}: {request.strip()[:120]}"
        constraints = ["既存API仕様を変更しない", "秘密情報をログに出力しない"]
        return TaskContract(
            task_id=task_id,
            objective=objective,
            task_type=tt,
            parent_task_id=parent_task_id,
            input_artifacts=[],
            expected_output=outputs,
            acceptance_criteria=self._acceptance(tt, request),
            constraints=constraints,
            required_tools=_REQUIRED_TOOLS.get(tt, ["read_file"]),
            estimated_context_tokens=_CTX_TOKENS.get(tt, 8000),
            estimated_steps=_STEPS.get(tt, 3),
            scores=scores,
            assigned_agent_template=phase.role,
            dependencies=list(dep_ids or []),
            escalation_conditions=list(STD_ESCALATION),
            done_definition=list(STD_DONE),
            depth=depth,
        )

    def decompose(
        self, request: str, org: OrgTemplate, run_id: str,
        state: DecompositionState | None = None,
    ) -> tuple[list[TaskContract], DecompositionState]:
        """Decompose into a contract DAG.

        If a usable ModelBackend is present, the LLM proposes the decomposition
        and the deterministic guards (contract validation, delegation/cycle/dup
        caps) bound it. Any failure falls back to the heuristic template path, so
        the pipeline never breaks because of a flaky/garbage model response.
        """
        state = state or DecompositionState()
        self.last_error = None

        if self.backend is not None and getattr(self.backend, "available", False):
            try:
                contracts = self._llm_decompose(request, org, run_id, state)
                self.last_strategy = "llm"
                return contracts, state
            except Exception as e:  # broad on purpose: never fail the pipeline
                self.last_error = f"{type(e).__name__}: {e}"

        self.last_strategy = "template"
        contracts = self._template_decompose(request, org, run_id)
        contracts = self._maybe_subdivide(contracts, request, run_id, state)
        return contracts, state

    def _template_decompose(self, request: str, org: OrgTemplate, run_id: str) -> list[TaskContract]:
        phase_to_id = {p.key: f"{run_id}-T{i:03d}" for i, p in enumerate(org.phases)}
        contracts: list[TaskContract] = []
        for phase in org.phases:
            tid = phase_to_id[phase.key]
            dep_ids = [phase_to_id[k] for k in phase.depends_on if k in phase_to_id]
            contracts.append(self._contract(tid, phase, request, run_id, dep_ids=dep_ids))
        return contracts

    # ----- LLM-backed decomposition (proposes; deterministic guards bound it) ----- #
    def _llm_prompt(self, request: str, org: OrgTemplate) -> str:
        roles = "\n".join(
            f"- {p.role} ({p.task_type.value})" for p in org.phases
        )
        cap = self.guard.config.max_delegations
        return (
            "You are a Task Architect. Decompose the REQUEST into the MINIMAL set of "
            "small, independently-executable subtasks — each small enough for a "
            "lightweight model, with a one-sentence concrete objective, explicit "
            "expected outputs, and testable/reviewable acceptance criteria. Do NOT pad "
            "with unnecessary phases; a trivial request may need only ONE subtask.\n\n"
            f"REQUEST:\n{request}\n\n"
            f"AVAILABLE ROLES (use only these names; pick the minimal sufficient subset):\n{roles}\n\n"
            f"VALID task_type values: {_VALID_TASK_TYPES}\n\n"
            "Return ONLY a JSON array (no prose, no markdown fences). Each element:\n"
            '{"id":"t1","objective":"<concrete, never vague like \'everything\'/\'as appropriate\'>",'
            '"task_type":"<one valid value>","role":"<one available role>",'
            '"expected_output":["<artifact>"],"acceptance_criteria":["<testable item>"],'
            '"dependencies":["<id in this array>"],"constraints":["<optional>"],'
            '"scores":{"complexity":0,"ambiguity":0,"risk":0,"context_size":0,'
            '"tool_risk":0,"domain_specialization":0}}\n'
            f"Rules: dependencies must reference ids in THIS array and form a DAG (no cycles). "
            f"At most {cap} subtasks. Scores are integers 0-100."
        )

    def _llm_decompose(
        self, request: str, org: OrgTemplate, run_id: str, state: DecompositionState,
    ) -> list[TaskContract]:
        raw = self.backend.complete(self._llm_prompt(request, org), ModelTier.LARGE)
        data = _extract_json(raw)
        if not isinstance(data, list) or not data:
            raise LLMDecompositionError("expected a non-empty JSON array of subtasks")
        if len(data) > self.guard.config.max_delegations:
            raise LLMDecompositionError(
                f"{len(data)} subtasks exceeds max_delegations {self.guard.config.max_delegations}"
            )

        # Map model-provided ids -> safe, run-scoped ids.
        id_map: dict[str, str] = {}
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise LLMDecompositionError("subtask is not an object")
            src = str(item.get("id", f"t{i}"))
            id_map[src] = f"{run_id}-L{i:03d}"

        contracts: list[TaskContract] = []
        seen_obj: set[str] = set()
        for i, item in enumerate(data):
            tid = id_map[str(item.get("id", f"t{i}"))]
            objective = str(item.get("objective", "")).strip()
            obj_key = re.sub(r"\s+", " ", objective.lower())
            if obj_key in seen_obj:
                raise LLMDecompositionError("duplicate objective among LLM subtasks")
            seen_obj.add(obj_key)

            try:
                tt = TaskType(str(item.get("task_type", "generic")))
            except ValueError:
                tt = TaskType.GENERIC

            sc = item.get("scores") or {}
            scores = ScoreProfile(
                complexity=_clamp(int(sc.get("complexity", self._score(request, tt).complexity))),
                ambiguity=_clamp(int(sc.get("ambiguity", self._score(request, tt).ambiguity))),
                risk=_clamp(int(sc.get("risk", self._score(request, tt).risk))),
                context_size=_clamp(int(sc.get("context_size", 20))),
                tool_risk=_clamp(int(sc.get("tool_risk", 15))),
                domain_specialization=_clamp(int(sc.get("domain_specialization", _DOMAIN_SPEC.get(tt, 30)))),
            )
            # Coerce null -> [] (a present key with a JSON null isn't the .get default).
            deps = [id_map[str(d)] for d in (item.get("dependencies") or []) if str(d) in id_map]
            outputs = [str(o) for o in (item.get("expected_output") or []) if str(o).strip()]
            criteria = [str(a) for a in (item.get("acceptance_criteria") or []) if str(a).strip()]
            constraints = [str(c) for c in (item.get("constraints") or [])] or \
                ["既存API仕様を変更しない", "秘密情報をログに出力しない"]

            contract = TaskContract(
                task_id=tid,
                objective=objective,
                task_type=tt,
                input_artifacts=[],
                expected_output=outputs,
                acceptance_criteria=criteria,
                constraints=constraints,
                required_tools=_REQUIRED_TOOLS.get(tt, ["read_file"]),
                estimated_context_tokens=_CTX_TOKENS.get(tt, 8000),
                estimated_steps=_STEPS.get(tt, 3),
                scores=scores,
                assigned_agent_template=item.get("role"),
                dependencies=deps,
                escalation_conditions=list(STD_ESCALATION),
                done_definition=list(STD_DONE),
                depth=0,
            )
            errs = contract.validate()
            if errs:
                raise LLMDecompositionError(f"invalid LLM contract {tid}: {errs[0]}")
            contracts.append(contract)

        if _has_cycle(contracts):
            raise LLMDecompositionError("LLM dependencies form a cycle")

        self.guard.register(contracts, state)
        return contracts

    def _maybe_subdivide(
        self, contracts: list[TaskContract], request: str, run_id: str,
        state: DecompositionState,
    ) -> list[TaskContract]:
        out: list[TaskContract] = []
        sub_idx = 0
        for parent in contracts:
            out.append(parent)
            big = (
                parent.task_type == TaskType.CODE_IMPLEMENTATION
                and parent.scores.complexity >= SUBDIVIDE_COMPLEXITY
                and parent.estimated_steps >= SUBDIVIDE_STEPS
            )
            if not big:
                continue

            slug = _slug(request)
            core_id = f"{parent.task_id}-S{sub_idx}a"
            test_id = f"{parent.task_id}-S{sub_idx}b"
            sub_idx += 1
            core = TaskContract(
                task_id=core_id, parent_task_id=parent.task_id,
                objective=f"コア実装: {request.strip()[:90]}",
                task_type=TaskType.CODE_IMPLEMENTATION,
                expected_output=[f"{slug}.py"],
                acceptance_criteria=["コアロジックが受入条件を満たす"],
                constraints=list(parent.constraints),
                required_tools=["read_file", "write_file", "run_test"],
                estimated_context_tokens=parent.estimated_context_tokens // 2,
                estimated_steps=max(2, parent.estimated_steps // 2),
                scores=ScoreProfile(
                    complexity=max(10, parent.scores.complexity - 15),
                    ambiguity=parent.scores.ambiguity, risk=parent.scores.risk,
                    context_size=max(5, parent.scores.context_size - 10),
                    tool_risk=parent.scores.tool_risk,
                    domain_specialization=parent.scores.domain_specialization,
                ),
                assigned_agent_template="BackendEngineer",
                escalation_conditions=list(STD_ESCALATION),
                done_definition=list(STD_DONE), depth=parent.depth + 1,
            )
            test = TaskContract(
                task_id=test_id, parent_task_id=parent.task_id,
                objective=f"テストと境界条件: {request.strip()[:80]}",
                task_type=TaskType.TEST_AUTHORING,
                expected_output=[f"test_{slug}.py"],
                acceptance_criteria=["境界条件を含むテストがある", "テストが実行可能"],
                constraints=list(parent.constraints),
                required_tools=["read_file", "write_file", "run_test"],
                estimated_context_tokens=parent.estimated_context_tokens // 3,
                estimated_steps=max(2, parent.estimated_steps // 3),
                scores=ScoreProfile(
                    complexity=25, ambiguity=parent.scores.ambiguity, risk=parent.scores.risk,
                    context_size=max(5, parent.scores.context_size - 15), tool_risk=10,
                    domain_specialization=parent.scores.domain_specialization,
                ),
                assigned_agent_template="TestEngineer",
                dependencies=[core_id],
                escalation_conditions=list(STD_ESCALATION),
                done_definition=list(STD_DONE), depth=parent.depth + 1,
            )
            children = [core, test]
            decision = self.guard.can_decompose(parent, children, state)
            if not decision.allowed:
                continue  # keep the parent monolithic; record nothing fabricated

            self.guard.register(children, state)
            # Parent now integrates its children: it depends on them.
            parent.dependencies = list(parent.dependencies) + [core_id, test_id]
            out.extend(children)
        return out
