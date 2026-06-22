"""org_vs_dataflow — measure A (human-org hierarchy) vs B (flat verification dataflow).

Runs the SAME request suite through both architectures under the SAME injected
defects and the SAME tier-weighted cost model, then reports three metric families:

    ① reliability  — true_success (completed AND no escaped defect), escaped_defects
    ② cost         — model_calls and tier-weighted usd (reruns/reroutes included)
    ③ human rate   — fraction of runs that needed a human (unresolved escalation)

A = `tehai.teams.MetaOrchestrator` (real), driven with `injected_failures` for
overt defects; its blind-spot late-catch / escape is modeled from A's *real*
composition (the producer's own review shares the blind spot — only an
independent downstream team catches it; if none exists, the defect escapes).

B = `tehai.FlatDataflowOrchestrator` (real); it verifies every node externally,
so it catches overt AND blind-spot defects at the node (node-local retry).

Both costs are reconstructed with `tehai.costmodel` so the comparison is an
emergent property of structure, not of two accounting rules.

Run:  python3 -m experiments.org_vs_dataflow [--backend null] [--out-dir experiments]
Deterministic: the same suite + backend yields byte-identical results.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tehai import Defect, FlatDataflowOrchestrator       # noqa: E402
from tehai.costmodel import exec_cost                    # noqa: E402
from tehai.review_planner import ReviewPlanner           # noqa: E402
from tehai.teams import AutonomyLevel, FailureType, MetaOrchestrator  # noqa: E402


# stage -> the team that owns it (A) and its default failure_type
TEAM_OF_STAGE = {
    "requirements": "product_planning_team",
    "architecture": "architecture_team",
    "implementation": "implementation_team",
    "verification": "verification_team",
    "security": "security_team",
    "integration": "integration_team",
    "documentation": "documentation_team",
}
FAILURE_OF_STAGE = {
    "requirements": "requirement_ambiguity",
    "architecture": "architecture_conflict",
    "implementation": "implementation_error",
    "verification": "test_failure",
    "security": "security_risk",
    "integration": "integration_conflict",
    "documentation": "implementation_error",
}
# Teams that act as an *independent* downstream verifier — the only place A can
# catch a blind-spot defect the producing team's own review missed.
INDEPENDENT_VERIFIERS = {"verification_team", "security_team", "integration_team"}


# --------------------------------------------------------------------------- #
# Scenario definition
# --------------------------------------------------------------------------- #
@dataclass
class DefectSpec:
    failure_type: str | None = None   # None -> stage default
    resolve_after: int = 1            # node-local re-runs / reroutes to fix
    blind_spot: bool = False          # producer's own review misses it


@dataclass
class Scenario:
    key: str
    request: str
    defects: dict[str, DefectSpec] = field(default_factory=dict)
    note: str = ""


SUITE = [
    Scenario("baseline_clean",
             "二分探索のユーティリティ関数とその単体テストを実装する",
             {}, "欠陥なし — 階層の純粋なオーバーヘッドを測る基準線"),
    Scenario("overt_impl",
             "ユーザー登録のメール形式バリデーションを実装する",
             {"implementation": DefectSpec(resolve_after=2)},
             "明示的な実装バグ（どのレビューでも検出）。A=2回カスケード再ルート / B=ノード再試行"),
    Scenario("blindspot_security",
             "ログインAPIのパスワード照合処理を実装する",
             {"implementation": DefectSpec(failure_type="security_risk",
                                           resolve_after=1, blind_spot=True)},
             "実装に潜む認証の盲点。A=生成チームは見逃し独立な下流で遅れて検出 / B=ノードの外部検証で即捕捉"),
    Scenario("blindspot_doc_escape",
             "READMEのインストール手順の誤字を修正し整形する",
             {"documentation": DefectSpec(blind_spot=True)},
             "doc-only 構成で独立な下流検証が無い盲点。A=流出 / B=ノードで捕捉"),
    Scenario("exhausted_retry",
             "ファイルアップロードのサイズ制限チェックを実装する",
             {"implementation": DefectSpec(resolve_after=4)},
             "上限を超える厄介な欠陥。A=同一失敗3回で停止 / B=ローカル上限超過で人間ゲート（Bも万能ではない）"),
]


# --------------------------------------------------------------------------- #
# A (human-org) — real run + cost reconstruction + blind-spot modeling
# --------------------------------------------------------------------------- #
def _team_base_costs(meta_result, rp: ReviewPlanner) -> dict[str, tuple[int, float]]:
    """Per-team cost of ONE run of that team (its planned tasks, real attempts)."""
    base: dict[str, tuple[int, float]] = {}
    for tr in meta_result.team_results.values():
        calls, usd = 0, 0.0
        if tr.plan:
            for c in tr.plan.contracts:
                rplan = rp.plan(c)
                er = tr.task_results.get(c.task_id) if tr.task_results else None
                attempts = er.attempts if (er and er.attempts) else 1
                cc, uu = exec_cost(c, rplan, attempts)
                calls += cc
                usd += uu
        base[tr.team_task_id] = (calls, round(usd, 6))
    return base


def _meta_cost(meta_result, base: dict[str, tuple[int, float]]) -> tuple[int, float]:
    """Total A cost = each team's base cost × (1 + its reroutes)."""
    calls, usd = 0, 0.0
    for ttid, (c, u) in base.items():
        tr = meta_result.team_results.get(ttid)
        mult = 1 + (tr.loop_count if tr else 0)
        calls += c * mult
        usd += u * mult
    return calls, round(usd, 4)


def _a_blindspot(meta_result, stage: str,
                 base: dict[str, tuple[int, float]]) -> tuple[int, int, float, bool]:
    """Model a blind-spot defect for A from its REAL composition.

    Returns (escaped_delta, extra_calls, extra_usd, late_caught).
    Rule: the producing team's own review misses it. If an independent verifier
    team runs downstream, it is caught late — modeled as re-running the producing
    stage and everything after it once. Otherwise it escapes (ships defective).
    """
    team_of = {c.assigned_team: c.team_task_id for c in meta_result.team_contracts}
    team_s = TEAM_OF_STAGE.get(stage)
    if team_s not in team_of:
        return 0, 0, 0.0, False  # this stage isn't even in A's composition
    ttid_s = team_of[team_s]
    order = meta_result.execution_order
    pos = {t: i for i, t in enumerate(order)}
    p_s = pos.get(ttid_s, 10 ** 9)

    has_downstream_verifier = any(
        c.assigned_team in INDEPENDENT_VERIFIERS and pos.get(c.team_task_id, -1) > p_s
        for c in meta_result.team_contracts
    )
    if not has_downstream_verifier:
        return 1, 0, 0.0, False  # escapes

    extra_calls, extra_usd = 0, 0.0
    for ttid, (c, u) in base.items():
        if pos.get(ttid, -1) >= p_s:            # re-run producing stage + downstream
            extra_calls += c
            extra_usd += u
    return 0, extra_calls, round(extra_usd, 4), True


def run_A(scenario: Scenario, backend: str, rp: ReviewPlanner) -> dict:
    inj = {
        TEAM_OF_STAGE[stage]: (FailureType(spec.failure_type or FAILURE_OF_STAGE[stage]),
                               spec.resolve_after)
        for stage, spec in scenario.defects.items() if not spec.blind_spot
    }
    meta = MetaOrchestrator.default(backend).run(
        scenario.request, injected_failures=inj, autonomy=AutonomyLevel.SUPERVISED)
    base = _team_base_costs(meta, rp)
    calls, usd = _meta_cost(meta, base)

    escaped, late = 0, []
    for stage, spec in scenario.defects.items():
        if spec.blind_spot:
            e, ec, eu, lc = _a_blindspot(meta, stage, base)
            escaped += e
            calls += ec
            usd = round(usd + eu, 4)
            if lc:
                late.append(stage)
    m = meta.metrics
    return {
        "task_success": bool(m["task_success"]),
        "true_success": bool(m["task_success"]) and escaped == 0,
        "escaped_defects": escaped,
        "late_caught_blindspots": late,
        "human_intervention_required": bool(m["human_intervention_required"]),
        "model_calls": calls,
        "cost_usd": usd,
        "team_count": m["team_count"],
        "task_count": m["task_count"],
        "reroutes": m["loop_count"],
        "final_status": meta.final_status,
        "composition": list(meta.team_composition),
    }


# --------------------------------------------------------------------------- #
# B (flat verification dataflow) — real run
# --------------------------------------------------------------------------- #
def run_B(scenario: Scenario, backend: str) -> dict:
    inj = {
        stage: Defect(failure_type=(spec.failure_type or FAILURE_OF_STAGE[stage]),
                      resolve_after=spec.resolve_after, blind_spot=spec.blind_spot)
        for stage, spec in scenario.defects.items()
    }
    res = FlatDataflowOrchestrator.default(backend).run(scenario.request, injected_defects=inj)
    # Did every injected defect find a matching node? (fairness self-check)
    node_stages = {n["stage"] for n in res.node_records}
    unmatched = [s for s in scenario.defects if s not in node_stages]
    m = res.metrics
    return {
        "task_success": bool(m["task_success"]),
        "true_success": bool(m["true_success"]),
        "escaped_defects": res.escaped_defects,
        "human_intervention_required": res.human_intervention_required,
        "model_calls": res.model_calls,
        "cost_usd": res.cost_usd,
        "node_count": m["node_count"],
        "local_retries": res.local_retries,
        "final_status": res.final_status,
        "unmatched_defect_stages": unmatched,
    }


# --------------------------------------------------------------------------- #
# Drive the suite + report
# --------------------------------------------------------------------------- #
def run_suite(backend: str = "null") -> dict:
    rp = ReviewPlanner()
    rows = []
    for sc in SUITE:
        a = run_A(sc, backend, rp)
        b = run_B(sc, backend)
        rows.append({"key": sc.key, "request": sc.request, "note": sc.note,
                     "defects": {s: vars(d) for s, d in sc.defects.items()},
                     "A": a, "B": b})

    def agg(side: str) -> dict:
        n = len(rows)
        return {
            "true_success_rate": round(sum(r[side]["true_success"] for r in rows) / n, 3),
            "escaped_defects": sum(r[side]["escaped_defects"] for r in rows),
            "total_model_calls": sum(r[side]["model_calls"] for r in rows),
            "total_cost_usd": round(sum(r[side]["cost_usd"] for r in rows), 4),
            "human_intervention_rate": round(
                sum(r[side]["human_intervention_required"] for r in rows) / n, 3),
        }

    return {
        "backend": backend,
        "n_scenarios": len(rows),
        "cost_model": "tier-weighted model-call proxy (tehai.costmodel): "
                      "1 generation + 1 call per review lens, by tier",
        "scenarios": rows,
        "aggregate": {"A_human_org": agg("A"), "B_flat_dataflow": agg("B")},
    }


def _fmt_table(report: dict) -> str:
    lines = []
    h = f"{'scenario':<22} {'arch':<5} {'true_succ':>9} {'escaped':>7} {'calls':>6} {'cost$':>7} {'human':>6}"
    lines.append(h)
    lines.append("-" * len(h))
    for r in report["scenarios"]:
        for side, label in (("A", "A"), ("B", "B")):
            d = r[side]
            lines.append(
                f"{(r['key'] if side=='A' else ''):<22} {label:<5} "
                f"{('yes' if d['true_success'] else 'NO'):>9} {d['escaped_defects']:>7} "
                f"{d['model_calls']:>6} {d['cost_usd']:>7.2f} "
                f"{('YES' if d['human_intervention_required'] else '-'):>6}")
        lines.append("")
    a, b = report["aggregate"]["A_human_org"], report["aggregate"]["B_flat_dataflow"]
    lines.append("AGGREGATE                         true_succ  escaped  calls   cost$  human")
    lines.append(f"  A (human-org hierarchy)           {a['true_success_rate']:>7}  {a['escaped_defects']:>6}  "
                 f"{a['total_model_calls']:>5}  {a['total_cost_usd']:>6.2f}  {a['human_intervention_rate']:>5}")
    lines.append(f"  B (flat verification dataflow)    {b['true_success_rate']:>7}  {b['escaped_defects']:>6}  "
                 f"{b['total_model_calls']:>5}  {b['total_cost_usd']:>6.2f}  {b['human_intervention_rate']:>5}")
    return "\n".join(lines)


def _report_md(report: dict) -> str:
    a = report["aggregate"]["A_human_org"]
    b = report["aggregate"]["B_flat_dataflow"]
    cost_ratio = round(a["total_cost_usd"] / b["total_cost_usd"], 2) if b["total_cost_usd"] else None
    out = []
    out.append("# A (人間組織体系) vs B (検証中心の平らなデータフロー) — 実測レポート")
    out.append("")
    out.append(f"- backend: `{report['backend']}`（決定的・オフライン） / シナリオ数: {report['n_scenarios']}")
    out.append(f"- コストモデル: {report['cost_model']}")
    out.append("")
    out.append("## 集計")
    out.append("")
    out.append("| 指標 | A 人間組織 | B 平ら検証 | 含意 |")
    out.append("|---|---|---|---|")
    out.append(f"| ① 信頼性 true_success_rate | {a['true_success_rate']} | {b['true_success_rate']} | "
               "完了かつ欠陥流出ゼロの割合 |")
    out.append(f"| ① 欠陥の流出 escaped_defects | {a['escaped_defects']} | {b['escaped_defects']} | "
               "成功偽装のまま出荷した盲点欠陥の数 |")
    out.append(f"| ② コスト total_cost_usd | {a['total_cost_usd']} | {b['total_cost_usd']} | "
               f"tier重みコール代理（A/B ≈ {cost_ratio}×） |")
    out.append(f"| ② コスト total_model_calls | {a['total_model_calls']} | {b['total_model_calls']} | "
               "生成+レビュー呼び出し総数 |")
    out.append(f"| ③ 人間介入率 | {a['human_intervention_rate']} | {b['human_intervention_rate']} | "
               "自律で解けず人間に上げた run の割合 |")
    out.append("")
    out.append("## シナリオ別")
    out.append("")
    out.append("```")
    out.append(_fmt_table(report))
    out.append("```")
    out.append("")
    for r in report["scenarios"]:
        out.append(f"### {r['key']}")
        out.append(f"- 要求: {r['request']}")
        out.append(f"- 狙い: {r['note']}")
        out.append(f"- A: true_success={r['A']['true_success']}, escaped={r['A']['escaped_defects']}, "
                   f"calls={r['A']['model_calls']}, cost=${r['A']['cost_usd']}, "
                   f"human={r['A']['human_intervention_required']}, "
                   f"teams={r['A']['team_count']}, reroutes={r['A']['reroutes']}, "
                   f"late_caught={r['A']['late_caught_blindspots']}")
        out.append(f"- B: true_success={r['B']['true_success']}, escaped={r['B']['escaped_defects']}, "
                   f"calls={r['B']['model_calls']}, cost=${r['B']['cost_usd']}, "
                   f"human={r['B']['human_intervention_required']}, nodes={r['B']['node_count']}, "
                   f"local_retries={r['B']['local_retries']}")
        if r["B"]["unmatched_defect_stages"]:
            out.append(f"  - ⚠ B に対応ノードが無い注入段: {r['B']['unmatched_defect_stages']}（無効注入）")
        out.append("")
    out.append("## 読み方・妥当性の限界")
    out.append("- これは**決定的な構造シミュレーション**であり、実LLMのベンチではない。"
               "コストは「tier重み付きの呼び出し回数の代理」。")
    out.append("- A の blind-spot の遅延検出/流出は、A の**実構成**に基づきハーネスが規則でモデル化している"
               "（生成チームの self-review は盲点を共有 → 独立な下流チームだけが捕捉）。B の捕捉は実コード。")
    out.append("- 結論は「どちらが常に優れるか」ではなく、**階層が生むコストの所在**と"
               "**検証中心が拾う盲点**を再現可能な数字で示すこと。")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A (human-org) vs B (flat verification dataflow)")
    ap.add_argument("--backend", default="null", help="tehai backend (null/echo/claude-cli/ollama)")
    ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args(argv)

    report = run_suite(args.backend)

    results_path = os.path.join(args.out_dir, "results.json")
    report_path = os.path.join(args.out_dir, "REPORT.md")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(_report_md(report) + "\n")

    if not args.json_only:
        print(_fmt_table(report))
        print(f"\nwrote {results_path}\nwrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
