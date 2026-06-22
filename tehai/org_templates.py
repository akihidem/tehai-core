"""Organization templates + selection.

These are *not* a fixed hierarchy. The orchestrator classifies the request and
picks the org template whose shape fits the work. Each template carries a default
phase pipeline (a DAG over phase keys) that the Task Architect instantiates into
concrete Task Contracts.
"""

from __future__ import annotations

from .models import OrgTemplate, Phase, TaskType


def _t(v: str) -> TaskType:
    return TaskType(v)


CATALOG: dict[str, OrgTemplate] = {
    "product_delivery": OrgTemplate(
        org_template_id="product_delivery",
        name="Product Delivery",
        description="要求→設計→実装→QA→リリースの一般的なプロダクト開発",
        roles=["ProductManager", "SystemArchitect", "Implementer", "QAEngineer", "ReleaseManager"],
        matches_keywords=["feature", "機能", "プロダクト", "product", "mvp", "ship", "リリース", "deliver", "build"],
        phases=[
            Phase("analyze", "要求分析", "ProductManager", _t("spec_design"), []),
            Phase("architect", "設計", "SystemArchitect", _t("architecture"), ["analyze"]),
            Phase("implement", "実装", "Implementer", _t("code_implementation"), ["architect"]),
            Phase("qa", "検証", "QAEngineer", _t("qa"), ["implement"]),
            Phase("release", "リリース", "ReleaseManager", _t("release"), ["qa"]),
        ],
    ),
    "research": OrgTemplate(
        org_template_id="research",
        name="Research",
        description="問いの定義→文献/手法→批判→統合",
        roles=["ResearchLead", "LiteratureAnalyst", "MethodDesigner", "Critic", "Synthesizer"],
        matches_keywords=["research", "調査", "研究", "survey", "文献", "literature", "比較検討", "analyze", "分析"],
        phases=[
            Phase("frame", "問いの定義", "ResearchLead", _t("research"), []),
            Phase("literature", "文献調査", "LiteratureAnalyst", _t("research"), ["frame"]),
            Phase("method", "手法設計", "MethodDesigner", _t("spec_design"), ["frame"]),
            Phase("critique", "批判的検証", "Critic", _t("research"), ["literature", "method"]),
            Phase("synthesize", "統合", "Synthesizer", _t("integration"), ["critique"]),
        ],
    ),
    "code_implementation": OrgTemplate(
        org_template_id="code_implementation",
        name="Code Implementation",
        description="技術リード→実装(BE/FE)→テスト→コードレビュー",
        roles=["TechnicalLead", "BackendEngineer", "FrontendEngineer", "TestEngineer", "CodeReviewer"],
        matches_keywords=["implement", "実装", "code", "コード", "refactor", "bug", "バグ", "function", "endpoint", "api"],
        phases=[
            Phase("plan", "実装計画", "TechnicalLead", _t("spec_design"), []),
            Phase("backend", "バックエンド実装", "BackendEngineer", _t("code_implementation"), ["plan"]),
            Phase("frontend", "フロントエンド実装", "FrontendEngineer", _t("code_implementation"), ["plan"]),
            Phase("tests", "テスト作成", "TestEngineer", _t("test_authoring"), ["backend", "frontend"]),
            Phase("review", "コードレビュー", "CodeReviewer", _t("code_review"), ["tests"]),
        ],
    ),
    "incident_response": OrgTemplate(
        org_template_id="incident_response",
        name="Incident Response",
        description="指揮→調査→封じ込め→復旧→ポストモーテム",
        roles=["IncidentCommander", "Investigator", "ContainmentAgent", "RecoveryAgent", "PostmortemReviewer"],
        matches_keywords=["incident", "障害", "インシデント", "outage", "down", "復旧", "postmortem", "regression"],
        phases=[
            Phase("command", "指揮", "IncidentCommander", _t("incident_response"), []),
            Phase("investigate", "原因調査", "Investigator", _t("incident_response"), ["command"]),
            Phase("contain", "封じ込め", "ContainmentAgent", _t("incident_response"), ["investigate"]),
            Phase("recover", "復旧", "RecoveryAgent", _t("incident_response"), ["contain"]),
            Phase("postmortem", "総括", "PostmortemReviewer", _t("incident_response"), ["recover"]),
        ],
    ),
    "content_production": OrgTemplate(
        org_template_id="content_production",
        name="Content Production",
        description="編集→取材→執筆→ファクトチェック→校正",
        roles=["Editor", "Researcher", "Drafter", "FactChecker", "CopyEditor"],
        matches_keywords=["article", "記事", "blog", "content", "コンテンツ", "エッセイ", "原稿", "draft", "write", "執筆"],
        phases=[
            Phase("editorial", "編集方針", "Editor", _t("content"), []),
            Phase("research", "取材", "Researcher", _t("research"), ["editorial"]),
            Phase("draft", "執筆", "Drafter", _t("content"), ["research"]),
            Phase("factcheck", "ファクトチェック", "FactChecker", _t("content"), ["draft"]),
            Phase("copyedit", "校正", "CopyEditor", _t("doc_formatting"), ["factcheck"]),
        ],
    ),
    "security_review": OrgTemplate(
        org_template_id="security_review",
        name="Security Review",
        description="観点設計→脅威モデル/静的解析/依存監査→リスク裁定",
        roles=["SecurityLead", "ThreatModeler", "StaticAnalysisAgent", "DependencyAuditor", "RiskJudge"],
        matches_keywords=["security", "セキュリティ", "vuln", "脆弱", "auth", "認証", "認可", "secret", "秘密", "exploit", "audit", "監査"],
        phases=[
            Phase("lead", "観点設計", "SecurityLead", _t("security_review"), []),
            Phase("threat", "脅威モデリング", "ThreatModeler", _t("security_review"), ["lead"]),
            Phase("static", "静的解析", "StaticAnalysisAgent", _t("security_review"), ["lead"]),
            Phase("deps", "依存監査", "DependencyAuditor", _t("security_review"), ["lead"]),
            Phase("risk", "リスク裁定", "RiskJudge", _t("security_review"), ["threat", "static", "deps"]),
        ],
    ),
}


# Priority order matters: more specific intents are tested before the generic
# product_delivery fallback.
_SELECTION_ORDER = [
    "security_review",
    "incident_response",
    "research",
    "content_production",
    "code_implementation",
    "product_delivery",
]


# Signals that a request is atomic enough for a single deliverable (so the
# deterministic path doesn't instantiate a full multi-phase pipeline for a typo).
_TRIVIAL_SIGNALS = (
    "typo", "誤字", "脱字", "rename", "リネーム", "format", "整形", "表記",
    "コメント", "comment", "one-liner", "一行", "軽微", "小さな", "微修正", "wording",
)


def _trivial_task_type(request: str) -> TaskType:
    low = request.lower()
    if any(k in low or k in request for k in
           ("typo", "誤字", "脱字", "format", "整形", "表記", "コメント", "comment", "wording")):
        return TaskType.DOC_FORMATTING
    if any(k in low or k in request for k in
           ("rename", "リネーム", "関数", "function", "変数", "code", "コード")):
        return TaskType.CODE_IMPLEMENTATION
    return TaskType.GENERIC


def _single_deliverable(request: str) -> OrgTemplate:
    tt = _trivial_task_type(request)
    role = {"doc_formatting": "CopyEditor", "code_implementation": "Implementer"}.get(
        tt.value, "Implementer")
    return OrgTemplate(
        org_template_id="single_deliverable",
        name="Single Deliverable",
        description="軽微・単一成果物のための1タスク分解（フルパイプラインを避ける）",
        roles=[role],
        phases=[Phase("deliver", "成果物作成", role, tt, [])],
    )


def _looks_trivial(request: str) -> bool:
    low = request.lower()
    return len(request) <= 64 and any(s in low or s in request for s in _TRIVIAL_SIGNALS)


def select_org_template(request: str) -> OrgTemplate:
    """Classify a free-text request into the best-fitting organization template.

    Trivial/atomic requests collapse to a single-deliverable org so the
    deterministic path stays request-sensitive (closes ASSUMPTIONS #4 for the
    offline path; the LLM path is already request-sensitive)."""
    if _looks_trivial(request):
        return _single_deliverable(request)
    low = request.lower()
    best_id, best_hits = "product_delivery", 0
    for tid in _SELECTION_ORDER:
        tmpl = CATALOG[tid]
        hits = sum(1 for kw in tmpl.matches_keywords if kw.lower() in low)
        if hits > best_hits:
            best_id, best_hits = tid, hits
    return CATALOG[best_id]


def get_org_template(org_template_id: str) -> OrgTemplate:
    if org_template_id not in CATALOG:
        raise KeyError(f"unknown org template: {org_template_id}")
    return CATALOG[org_template_id]
