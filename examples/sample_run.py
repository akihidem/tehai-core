"""End-to-end sample tour (offline, deterministic). Run:
    python examples/sample_run.py

Tours the whole pipeline on contrasting requests: plan -> (execute generate ->
review -> judge -> FSM) -> evaluate -> calibrate. Fully offline (NullBackend +
deterministic stubs), so no model or network is needed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tehai import EvaluationStore, ExecutionLogger, Orchestrator
from tehai.cli import _render_plan

REQUESTS = [
    "ログイン画面に入力バリデーションを実装する。メール形式とパスワード長を検証する。",
    "決済APIに認証トークン検証を追加し、本番にデプロイする",  # hazardous -> large + human gate
    "競合3社のオンボーディング体験を調査して比較レポートにまとめる",  # research org
    "READMEの誤字を修正する",  # trivial -> single_deliverable (one task)
]


def main() -> None:
    orch = Orchestrator.default()
    tmp = Path(tempfile.mkdtemp(prefix="tehai_demo_"))

    print("#" * 78 + "\n# 1) PLAN — request -> contract-bound task DAG\n" + "#" * 78)
    plans = []
    for req in REQUESTS:
        plan = orch.plan(req)
        plans.append(plan)
        print("=" * 78)
        print(_render_plan(plan, orch.router, orch))
        print()

    print("#" * 78 + "\n# 2) EXECUTE — generate -> review the artifact -> judge -> FSM\n" + "#" * 78)
    ledger = ExecutionLogger(tmp / "exec.jsonl")
    results = orch.execute(plans[0], limit=3, logger=ledger)
    for tid, r in results.items():
        judged = r.decision.decision.value if r.decision else "-"
        print(f"  {tid}: {r.status.value} (judge={judged}, {r.elapsed_seconds}s) "
              f"-> artifacts {list(r.artifacts)}")

    print("\n" + "#" * 78 + "\n# 3) EVALUATE + 4) CALIBRATE (proposal only)\n" + "#" * 78)
    store = EvaluationStore()
    records = ledger.read()
    metrics = store.compute(records)
    print(f"  records={metrics.n_records}  success={metrics.overall_success_rate}  "
          f"by_model={metrics.by_model}")
    cal = store.calibrate(records)
    print(f"  calibration: {cal.status}")
    print(f"  rationale: {cal.rationale[0]}")
    print(f"\nledger: {ledger.path}")


if __name__ == "__main__":
    main()
