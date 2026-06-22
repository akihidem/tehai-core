# Multi-Team AgentOps Platform — verification_report.md

## 1. 検証概要

仮説: Meta Orchestrator がタスクをチーム単位で分配し、各チームが内部で Task Contract に
分解し、Verification/Security/Integration/Judge が検証と差し戻しを行うことで、単一エージェント
より高品質・低人間介入で開発タスクを完了できる。

実行は決定的オフライン（実モデル呼び出しなし）。意図的失敗は `injected_failures` で注入し、
Failure Router / Autonomous Loop Guard の挙動を再現可能に検証している。

**チェック合計: 43/43 合格**

## 2. 各シナリオの結果

### S-001 低リスクREADME更新

- goal: `READMEの誤字を修正する`
- 狙い: 低リスク文書変更が自律完了し、不要に人間承認を求めない
- selected teams: ['documentation_team']
- final_status: **completed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": true, "autonomous_completion": true, "human_intervention_required": false, "loop_count": 0, "team_count": 1, "task_count": 3, "failed_task_count": 0, "resolved_failure_count": 0, "test_pass_rate_before": 1.0, "test_pass_rate_after": 1.0, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": []}`

  Team Contracts / decomposition:
  - [TT-000] documentation_team → completed (3 task contracts, tiers={'small': 3}, loop=0)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, doc_only_low_risk=✓, no_unneeded_human=✓

### S-002 Todoアプリ機能追加

- goal: `Todoアプリにタスク完了フラグ機能を実装する`
- 狙い: Planning→Architecture→Implementation→Verification→Integration→Documentation の基本流れ
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'integration_team', 'documentation_team']
- final_status: **completed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": true, "autonomous_completion": true, "human_intervention_required": false, "loop_count": 0, "team_count": 6, "task_count": 19, "failed_task_count": 0, "resolved_failure_count": 0, "test_pass_rate_before": 1.0, "test_pass_rate_after": 1.0, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": []}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'small': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 1, 'medium': 1}, loop=0)
  - [TT-002] implementation_team → completed (7 task contracts, tiers={'small': 7}, loop=0)
  - [TT-003] verification_team → completed (3 task contracts, tiers={'small': 3}, loop=0)
  - [TT-004] integration_team → completed (2 task contracts, tiers={'medium': 1, 'small': 1}, loop=0)
  - [TT-005] documentation_team → completed (3 task contracts, tiers={'small': 3}, loop=0)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓

### S-003 認証機能追加

- goal: `ユーザー認証機能を実装する。トークンを安全に生成する`
- 狙い: Security Team が適切なタイミングで関与する
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'security_team', 'integration_team', 'documentation_team']
- final_status: **completed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": true, "autonomous_completion": true, "human_intervention_required": false, "loop_count": 0, "team_count": 7, "task_count": 24, "failed_task_count": 0, "resolved_failure_count": 0, "test_pass_rate_before": 1.0, "test_pass_rate_after": 1.0, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": []}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'large': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 2}, loop=0)
  - [TT-002] implementation_team → completed (7 task contracts, tiers={'large': 7}, loop=0)
  - [TT-003] verification_team → completed (3 task contracts, tiers={'large': 3}, loop=0)
  - [TT-004] security_team → completed (5 task contracts, tiers={'large': 5}, loop=0)
  - [TT-005] integration_team → completed (2 task contracts, tiers={'large': 2}, loop=0)
  - [TT-006] documentation_team → completed (3 task contracts, tiers={'large': 3}, loop=0)

  Cross-Team Competition (architecture): winner=**security** 案 — goal の risk プロファイルにより ['security', 'risk'] を重視 → security 案 (approach_C) を採用 (weighted=63.5)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, security_team_involved=✓, competition_run=✓, competition_security_wins=✓

### S-004 曖昧要件(意図的失敗)

- goal: `機能Xを実装する`
- 狙い: requirement_ambiguity を Product Planning Team に差し戻し、Clarification Report を作る
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'integration_team', 'documentation_team']
- final_status: **completed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": true, "autonomous_completion": true, "human_intervention_required": false, "loop_count": 1, "team_count": 6, "task_count": 19, "failed_task_count": 1, "resolved_failure_count": 2, "test_pass_rate_before": 0.667, "test_pass_rate_after": 1.0, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": ["requirement_ambiguity"]}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'small': 2}, loop=1)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 1, 'medium': 1}, loop=0)
  - [TT-002] implementation_team → completed (7 task contracts, tiers={'small': 7}, loop=1)
  - [TT-003] verification_team → completed (3 task contracts, tiers={'small': 3}, loop=0)
  - [TT-004] integration_team → completed (2 task contracts, tiers={'medium': 1, 'small': 1}, loop=0)
  - [TT-005] documentation_team → completed (3 task contracts, tiers={'small': 3}, loop=0)

  Failure routing / loop history:
  - #3 implementation_team: **requirement_ambiguity** → route `product_planning_team` | guard: auto reroute -> product_planning_team (allow=True, stop=None)

  Clarification Report: unclear=['implementation_team の成果物と受入条件が対応していない'], recommendation=「Product Planning Team で受入条件とAPI仕様を再定義する」, human_needed=False

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, routed_to_product_planning=✓, clarification_report_made=✓, recovered=✓

### S-005 セキュリティリスク(意図的失敗)

- goal: `認証トークンを外部に送信する機能を実装する`
- 狙い: security_risk を検出し Approval Gate で人間承認を要求して停止する
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'security_team', 'integration_team', 'documentation_team']
- final_status: **request_human_approval**  / human_intervention: True
- metrics: `{"system_type": "multi_team_agentops", "task_success": false, "autonomous_completion": false, "human_intervention_required": true, "loop_count": 1, "team_count": 7, "task_count": 19, "failed_task_count": 1, "resolved_failure_count": 0, "test_pass_rate_before": 0.571, "test_pass_rate_after": 0.571, "security_findings_count": 1, "unresolved_security_findings": 1, "human_intervention_rate": 1.0, "failure_types": ["security_risk"]}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'large': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 2}, loop=0)
  - [TT-002] implementation_team → completed (7 task contracts, tiers={'large': 7}, loop=0)
  - [TT-003] verification_team → completed (3 task contracts, tiers={'large': 3}, loop=0)
  - [TT-004] security_team → failed (5 task contracts, tiers={'large': 5}, loop=0)
  - [TT-005] integration_team → not_run (0 task contracts, tiers={}, loop=0)
  - [TT-006] documentation_team → not_run (0 task contracts, tiers={}, loop=0)

  Failure routing / loop history:
  - #5 security_team: **security_risk** → route `security_team` | guard: security_risk requires human approval (allow=False, stop=request_human_approval)

  Cross-Team Competition (architecture): winner=**security** 案 — goal の risk プロファイルにより ['security', 'risk'] を重視 → security 案 (approach_C) を採用 (weighted=63.5)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, human_approval_required=✓, stopped_at_gate=✓

### S-006 統合コンフリクト(意図的失敗)

- goal: `複数モジュールを統合する機能を実装する`
- 狙い: integration_conflict を Integration Team に戻す（Implementation に誤って戻さない）
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'integration_team', 'documentation_team']
- final_status: **completed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": true, "autonomous_completion": true, "human_intervention_required": false, "loop_count": 1, "team_count": 6, "task_count": 19, "failed_task_count": 1, "resolved_failure_count": 1, "test_pass_rate_before": 0.833, "test_pass_rate_after": 1.0, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": ["integration_conflict"]}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'small': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 1, 'medium': 1}, loop=0)
  - [TT-002] implementation_team → completed (7 task contracts, tiers={'small': 7}, loop=0)
  - [TT-003] verification_team → completed (3 task contracts, tiers={'small': 3}, loop=0)
  - [TT-004] integration_team → completed (2 task contracts, tiers={'medium': 1, 'small': 1}, loop=2)
  - [TT-005] documentation_team → completed (3 task contracts, tiers={'small': 3}, loop=0)

  Failure routing / loop history:
  - #5 integration_team: **integration_conflict** → route `integration_team` | guard: auto reroute -> integration_team (allow=True, stop=None)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, routed_to_integration=✓, not_routed_to_implementation=✓

### S-007 設計矛盾(意図的失敗)

- goal: `新しいデータ同期機能を実装する`
- 狙い: architecture_conflict を Architecture Team に戻して回復する
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'integration_team', 'documentation_team']
- final_status: **completed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": true, "autonomous_completion": true, "human_intervention_required": false, "loop_count": 1, "team_count": 6, "task_count": 19, "failed_task_count": 1, "resolved_failure_count": 1, "test_pass_rate_before": 0.833, "test_pass_rate_after": 1.0, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": ["architecture_conflict"]}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'small': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 1, 'medium': 1}, loop=2)
  - [TT-002] implementation_team → completed (7 task contracts, tiers={'small': 7}, loop=0)
  - [TT-003] verification_team → completed (3 task contracts, tiers={'small': 3}, loop=0)
  - [TT-004] integration_team → completed (2 task contracts, tiers={'medium': 1, 'small': 1}, loop=0)
  - [TT-005] documentation_team → completed (3 task contracts, tiers={'small': 3}, loop=0)

  Failure routing / loop history:
  - #2 architecture_team: **architecture_conflict** → route `architecture_team` | guard: auto reroute -> architecture_team (allow=True, stop=None)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, routed_to_architecture=✓

### S-008 同じ失敗の反復(意図的失敗)

- goal: `機能Yを実装する`
- 狙い: 同一 failure_type が3回続いたら自律ループを停止する
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'integration_team', 'documentation_team']
- final_status: **stop_as_failed**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": false, "autonomous_completion": false, "human_intervention_required": false, "loop_count": 3, "team_count": 6, "task_count": 11, "failed_task_count": 3, "resolved_failure_count": 0, "test_pass_rate_before": 0.333, "test_pass_rate_after": 0.333, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": ["implementation_error", "implementation_error", "implementation_error"]}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'small': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 1, 'medium': 1}, loop=0)
  - [TT-002] implementation_team → failed (7 task contracts, tiers={'small': 7}, loop=4)
  - [TT-003] verification_team → not_run (0 task contracts, tiers={}, loop=0)
  - [TT-004] integration_team → not_run (0 task contracts, tiers={}, loop=0)
  - [TT-005] documentation_team → not_run (0 task contracts, tiers={}, loop=0)

  Failure routing / loop history:
  - #3 implementation_team: **implementation_error** → route `implementation_team` | guard: auto reroute -> implementation_team (allow=True, stop=None)
  - #4 implementation_team: **implementation_error** → route `implementation_team` | guard: auto reroute -> implementation_team (allow=True, stop=None)
  - #5 implementation_team: **implementation_error** → route `implementation_team` | guard: same failure_type x3 (allow=False, stop=stop_as_failed)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, stopped_on_repeat=✓, bounded_loops=✓

### S-009 コスト超過(意図的失敗)

- goal: `大規模な一括移行を実装する`
- 狙い: cost_overrun は Meta へ送り scope 縮小を提案して停止する
- selected teams: ['product_planning_team', 'architecture_team', 'implementation_team', 'verification_team', 'integration_team', 'documentation_team']
- final_status: **shrink_scope**  / human_intervention: False
- metrics: `{"system_type": "multi_team_agentops", "task_success": false, "autonomous_completion": false, "human_intervention_required": false, "loop_count": 1, "team_count": 6, "task_count": 11, "failed_task_count": 1, "resolved_failure_count": 0, "test_pass_rate_before": 0.333, "test_pass_rate_after": 0.333, "security_findings_count": 0, "unresolved_security_findings": 0, "human_intervention_rate": 0.0, "failure_types": ["cost_overrun"]}`

  Team Contracts / decomposition:
  - [TT-000] product_planning_team → completed (2 task contracts, tiers={'small': 2}, loop=0)
  - [TT-001] architecture_team → completed (2 task contracts, tiers={'large': 1, 'medium': 1}, loop=0)
  - [TT-002] implementation_team → failed (7 task contracts, tiers={'small': 7}, loop=0)
  - [TT-003] verification_team → not_run (0 task contracts, tiers={}, loop=0)
  - [TT-004] integration_team → not_run (0 task contracts, tiers={}, loop=0)
  - [TT-005] documentation_team → not_run (0 task contracts, tiers={}, loop=0)

  Failure routing / loop history:
  - #3 implementation_team: **cost_overrun** → route `meta` | guard: cost overrun (allow=False, stop=shrink_scope)

  Checks: produced_team_contracts=✓, every_team_contract_valid=✓, task_contracts_generated=✓, cost_overrun_to_meta_shrink=✓

## 3. 成功した点

- 低リスク(README)は単一 Documentation Team で自律完了し、不要な承認を求めない (S-001)
- 基本フロー（6チーム）が依存DAGで実行される (S-002)
- 認証要求で Security Team が後付けでなく統合前に組み込まれる (S-003)
- requirement_ambiguity は Product Planning へ差し戻し、Clarification Report を生成 (S-004)
- security_risk は Approval Gate で人間承認を要求して停止 (S-005)
- integration_conflict は Integration Team に戻り、Implementation に誤送しない (S-006)
- architecture_conflict は Architecture Team へ (S-007)
- 同一 failure_type 反復は3回で自律ループ停止 (S-008)、cost_overrun は scope 縮小へ (S-009)

## 4. 失敗した点 / 既知の限界

- 実行は決定的スタブで、生成物の中身の品質は評価していない（tehai backend を ollama にすると実生成・実行に切替可）。
- 差し戻し時の下流チーム再実行はカスケードせず、原因チーム＋失敗チームのみ再実行する簡略化。
- A/B 比較は構造比較（後述）であり、実LLM品質のベンチマークではない。

## 5–7. Single Large Agent / Single Multi-Agent Team との比較

| 観点 | A: Single Large Agent | B: Single Multi-Agent Team | C: Multi-Team (本実装) |
|---|---|---|---|
| タスク分解 | なし（一括生成） | あり（1組織テンプレ内） | あり（チーム×内部DAG） |
| 観点分離レビュー | なし | あり | あり |
| **チーム横断の failure routing** | なし | なし | **あり**（原因チームへ差し戻し） |
| 承認ゲート | なし | 限定的 | あり（security/本番/外部送信） |
| 曖昧要件での挙動 | 未検証のまま出力 | 検出しても戻す先がない→人間 | Product Planning へ自律差し戻し |
| セキュリティ問題 | 見逃しやすい | 検出可 | 検出→Approval Gate→人間 |

B の実測（tehai 単一パイプライン, 代表ゴール「Todoアプリにタスク完了フラグ機能を実装する」）: 7 task contracts を生成するが、チーム横断の差し戻し先を持たない（失敗時は内部リトライ/エスカレーションのみ）。

**C の優位点**: 失敗を failure_type で分類し、原因チームへ差し戻すことで、単一チーム/単一エージェントが
「戻す先を持たない」状況を構造的に解決し、低リスク帯では人間介入なしで回復する。
**C の弱点**: チーム多重化によりタスク数・潜在コストが増える（高リスク・反復失敗時に限定すべき）。

## 8. 人間介入率

- 全 9 シナリオ中、人間承認を要したのは 1 件（= 意図的セキュリティリスク S-005 等）。
- 低リスク・自律回復可能な失敗（S-004/S-006/S-007）は人間なしで解決。

## 9. コストと品質のトレードオフ

- 多チーム化はタスク数を増やす（決定的実行ではコスト ~0 だが、実モデルでは線形以上に増える）。
- ゆえに cross-team competition（§15）や全チーム投入は高リスク/反復失敗に限定する設計。

## 10–13. ゲート/分類の妥当性

- 自律ループ停止条件: 同一failure×3 (S-008)・cost_overrun (S-009)・security (S-005) で正しく停止。
- セキュリティゲート: security_risk で require_human_approval が発火 (S-005)。
- failure_reason 分類: 注入した type が分類器を通り、ルーティング表どおりのチームへ到達（各シナリオ Checks 参照）。
- 差し戻しルート妥当性: integration_conflict→Integration（Implementationに誤送なし, S-006）, architecture_conflict→Architecture (S-007), requirement_ambiguity→Product Planning (S-004)。

## 14. 次に改善すべき点

- 下流チームのカスケード再実行、cross-team competition の実装、実 backend(ollama) での品質検証、
Global Evaluation Store の提案を config 採用ループ（tehai calibrate）に接続。

## Appendix: Global Evaluation Store（全シナリオ横断）

```json
{
  "n_runs": 9,
  "by_team": {
    "architecture_team": {
      "runs": 8,
      "success_rate": 1.0,
      "reroute_rate": 0.125
    },
    "documentation_team": {
      "runs": 6,
      "success_rate": 1.0,
      "reroute_rate": 0.0
    },
    "implementation_team": {
      "runs": 8,
      "success_rate": 0.75,
      "reroute_rate": 0.25
    },
    "integration_team": {
      "runs": 5,
      "success_rate": 1.0,
      "reroute_rate": 0.2
    },
    "product_planning_team": {
      "runs": 8,
      "success_rate": 1.0,
      "reroute_rate": 0.125
    },
    "security_team": {
      "runs": 2,
      "success_rate": 0.5,
      "reroute_rate": 0.0
    },
    "verification_team": {
      "runs": 6,
      "success_rate": 1.0,
      "reroute_rate": 0.0
    }
  },
  "failure_type_resolution": {
    "architecture_conflict": {
      "seen": 1,
      "resolved": 1,
      "resolution_rate": 1.0
    },
    "cost_overrun": {
      "seen": 1,
      "resolved": 0,
      "resolution_rate": 0.0
    },
    "implementation_error": {
      "seen": 3,
      "resolved": 0,
      "resolution_rate": 0.0
    },
    "integration_conflict": {
      "seen": 1,
      "resolved": 1,
      "resolution_rate": 1.0
    },
    "requirement_ambiguity": {
      "seen": 1,
      "resolved": 1,
      "resolution_rate": 1.0
    },
    "security_risk": {
      "seen": 1,
      "resolved": 0,
      "resolution_rate": 0.0
    }
  },
  "human_intervention_rate": 0.111
}
```

改善提案（提案のみ・自動適用なし）:
- team_allocation: team 'security_team' success_rate<0.6 — revisit its Team Contract granularity
- failure_routing: failure_type 'implementation_error' resolution_rate<0.5 — revisit its route
