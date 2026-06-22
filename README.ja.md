# tehai （手配）

[English](README.md) | **日本語**

> **統制された、監査可能な委譲（delegation）レイヤー**。要求を、小さく契約で
> 縛られたサブタスクの DAG に分解し、それぞれを *最も安く実行できるモデル* に
> 振り分け、リスクに応じてレビューし、ジャッジし、ログに残す ── 厳格な予算、
> 有界な再帰、最小権限のもとで。

`手配` とは *仕事に対して適切な人手を割り当て、差配する* こと。tehai がやるのは
まさにそれ。「エージェントを大量に湧かせる」のではなく、**誰が・何を・どのモデル
ティアで・どの権限で・どうレビューされて行うかを決め、そしてそれを後から証明する**。

> 📊 **設計思想ひと目で** ── 設計原則の 1 ページ・ビジュアル解説:
> [`docs/philosophy.html`](docs/philosophy.html)（レンダリング済み:
> [`docs/philosophy.png`](docs/philosophy.png)）。
> 📖 **使い方** ── コマンド・バックエンド・マルチチーム・安全性をまとめた
> 1 ページ HTML マニュアル: [`docs/manual.html`](docs/manual.html)。

これは **際限なく自己増殖するエージェントの群れではない**。委譲のあらゆる一手が、
ロールテンプレート・予算・深さ制限・権限境界・停止条件を必ず通過する。

ステータス: **MVP。** 既定は決定的で、パイプライン全体がオフラインで動く ──
**サードパーティ依存ゼロ**（Python ≥3.10 の標準ライブラリのみ）。**LLM バックエンドは
エンドツーエンドで配線済み**（`--backend ollama|claude-cli`）: モデルが分解・スコア
付け・成果物生成・観点別レビューを *提案* し、決定的なガード（契約検証、循環/重複/
委譲上限、権限ゲート、Judge の優先順位）が各ステップを *境界づける*。不正・無意味な
応答はすべてヒューリスティック／スタブ経路にフォールバックする。したがって
`tehai run` は実際の成果物を生成し、その成果物をレビューし、ジャッジする ── あるいは
スタブで完全オフラインのまま動く。ローカルの Ollama（gemma4）で実機検証済み。
オーケストレータのインターフェースは純粋な seam（差し替え点）。

---

## クイックスタート

```bash
cd tehai-core

# 要求を契約付きのタスク DAG にプランニングする。
python3 -m tehai plan "ログイン画面に入力バリデーションを実装する" --save-log

# JSON 出力（機械可読な RunPlan）。
python3 -m tehai plan "決済APIに認証を追加し本番にデプロイ" --json

# LLM による分解（モデルが提案し、ガードが境界づける。不正出力ならフォールバック）。
python3 -m tehai plan "ログイン画面の入力バリデーションを実装する" --backend ollama
python3 -m tehai plan "..." --backend ollama --ollama-host http://172.24.224.1:11434
# （決定的な既定は --backend null）

# 要求のタスクに対し、リスクベースのレビュー計画＋Judge を実行する。
python3 -m tehai review "決済APIに認証を追加する" --backend ollama --limit 1
python3 -m tehai review "add pagination" --artifact ./patch.txt   # ヒューリスティック、オフライン

# フルループ: 実際の成果物を生成 → 成果物をレビュー → Judge → FSM。
python3 -m tehai run "メール検証関数を実装する" --backend ollama --limit 1 --out ./out
python3 -m tehai run "add pagination" --out ./out   # オフライン: 決定的スタブ
# --sandbox: 生成コードを実際にコンパイル/実行する（opt-in。auto-check を実測で接地）。
python3 -m tehai run "addと単体テストをPythonで実装" --backend ollama --sandbox --out ./out

# レジストリ／組織テンプレートを確認する。
python3 -m tehai agents
python3 -m tehai orgs

# ledger からメトリクスと改善 *提案* を集計する。
python3 -m tehai evaluate runs/R-xxxxxxxx.jsonl

# 対照的な 3 つの要求でのエンドツーエンド・デモ。
python3 examples/sample_run.py

# テスト（依存ゼロ）。
python3 -m unittest discover -s tests -t .
```

---

## 1. アーキテクチャ概観

```
request
  │
  ▼
┌──────────────┐   classify        ┌───────────────────┐
│ Orchestrator │ ────────────────▶ │ Organization      │  6 templates
│ (conductor)  │                   │ Template selector │  (PM/Arch/…)
└──────┬───────┘                   └───────────────────┘
       │ decompose
       ▼
┌──────────────┐  per-type heuristics + guarded 1-level recursion
│ TaskArchitect│ ───────────────────────────────────────────────▶  Task DAG
└──────┬───────┘                                                   (TaskContracts)
       │ for each contract:
       ├─▶ Agent Designer   (registry.select_for_task — never fabricates)
       ├─▶ Model Router     (weighted score + hazard escalation → tier)
       ├─▶ Permission model (child capabilities ⊆ parent)
       └─▶ Review Planner   (risk-based lens composition + judge/human gate)
       │
       ▼
   topological order ──▶ RunPlan ──▶ Execution Logger (JSONL) ──▶ Evaluation Store
                                                                    (metrics + proposals)
```

各コンポーネントはモジュールに 1:1 で対応する。

| 仕様コンポーネント | モジュール | 中核の責務 |
|---|---|---|
| Orchestrator | `orchestrator.py` | classify → decompose → assign → route → review-plan → order |
| Task Architect | `architect.py` | 要求 → `TaskContract` の DAG。有界な再帰。LLM またはヒューリスティック |
| Model backend | `backends.py` | seam: Null（既定）/ Echo / Ollama / claude-cli アダプタ |
| Task scoring | `scoring.py` | バックエンド駆動の 6 軸（再）スコアリング、ヒューリスティックにフォールバック |
| Agent Designer | `registry.py` | レジストリから `select_for_task`。新規エージェントは提案を記録するのみ（生成しない） |
| Model Router | `model_router.py` | 加重多因子スコア＋hazard / context / failure エスカレーション |
| Reviewer planning | `review_planner.py` | リスクベース・観点分離のレビュー構成 |
| Reviewer execution | `reviewer.py` | 各観点をバックエンド経由で実行 → ReviewResult。決定的フォールバック |
| Executor | `executor.py` | 成果物生成 → 成果物レビュー → Judge → FSM。権限ゲート。リトライ |
| Sandbox | `sandbox.py` | opt-in: 成果物を実際にコンパイル/実行（隔離・制限付き）。auto-check を接地 |
| Judge | `judge.py` | レビューの根拠を比較 → accept / revise / discard / rerun |
| Recursive guard | `decompose_guard.py` | 深さ / 委譲 / 並行度 / 予算 / 循環 / 重複 / 進捗の停止条件 |
| Permissions | `permissions.py` | 最小権限、承認ゲート、child ⊆ parent |
| Logger | `logger.py` | 追記専用 JSONL ledger |
| Evaluation store | `evaluation.py` | メトリクス集計＋ *提案* としての改善 |
| Schemas | `schemas/*.json` + `schema.py` | Task Contract / Agent Template / Log Record ＋小さな validator |

```
tehai/
├── tehai/
│   ├── orchestrator.py     architect.py      registry.py
│   ├── model_router.py     decompose_guard.py permissions.py  scoring.py
│   ├── review_planner.py   reviewer.py       judge.py        evaluation.py
│   ├── executor.py         sandbox.py        logger.py       backends.py  models.py
│   ├── org_templates.py    schema.py         cli.py  __main__.py
│   ├── schemas/*.json                        (Task Contract / Agent / Log)
│   └── data/agents/registry.json             (seed Agent Registry)
├── examples/sample_run.py
├── tests/                  (216 tests, stdlib unittest)
├── README.md  ASSUMPTIONS.md  FUTURE.md  pyproject.toml
```

## 2. データモデル（`models.py`）

重心は **`TaskContract`**。無効な契約に対しては何も実行されない ──
`validate()` は曖昧な目的（「全部よしなに…」）、欠落した expected output /
受け入れ基準 / エスカレーション条件、自己依存、範囲外スコアを却下する。
検証に失敗した契約は修復のため親に差し戻される。

**タスクのライフサイクル**（`TaskStatus` + `TASK_TRANSITIONS`）:

```
pending → ready → running → reviewing → accepted → completed
              ↘ blocked        ↘ rejected → retrying → running
                               ↘ escalated ↗
              (any) → failed
```

**成果物のプロヴェナンス（来歴）** は第一級市民（`Provenance`）: 誰が・どの契約の
もとで・どの入力から作り・どのレビューを通過し・どの Judge が決定したか。

## 3. Agent Registry（エージェント・レジストリ）

選択は **レジストリ優先**。Agent Designer は登録済みテンプレートを選ぶか、せいぜい
そのパラメータを調整する。真に新規のニーズは **提案として記録される**
（`propose_new_template`） ── 決して自動生成しない。各テンプレートは
**responsibilities / allowed_actions / forbidden_actions / output_schema /
quality_bar / escalation_rules** で定義される ── ペルソナは副次的。

## 4. 組織テンプレート

6 つのテンプレート（Product Delivery, Research, Code Implementation, Incident
Response, Content Production, Security Review）。固定の階層ではない ──
オーケストレータが要求を分類し、合致するテンプレートのフェーズ・パイプラインを
具体的な契約へとインスタンス化する。**些末／アトミックな要求**（typo、リネーム、
整形…）は動的な `single_deliverable` 組織（1 タスク）に縮退するので、オフライン
経路でも常にフルパイプラインを吐かず、要求に感応する。

## 5. タスク分解

1 つのインターフェースの背後に、相互に差し替え可能な 2 経路:

- **決定的（既定）:** 各組織フェーズ → タイプ別の成果物テンプレート・
  ヒューリスティックな 6 軸スコア・DAG を成す依存関係を持つ 1 つの `TaskContract`。
  過大な実装タスクは **1 段だけ深く** 分割される ── ただし `DecompositionGuard`
  が許可した場合のみ。
- **LLM 駆動（`--backend ollama|claude-cli`）:** モデルに組織テンプレートのロールと
  有効なタスクタイプの enum を与え、最小のサブタスク集合を *提案* させる（よって
  些末な要求がフルパイプラインを生まなくなる）。提案された各契約は **同一の**
  `TaskContract.validate()`・委譲上限・目的の重複チェック・循環チェックを通される。
  **いずれかが失敗すれば** 決定的経路にフォールバックする。モデルが提案し ──
  ガードが決める。

*分解は進捗ではない*: 分割が許されるのは、子が親より小さく・契約可能で・非循環で・
重複せず・親の成果物に向かって前進する場合に限られる。

## 6. モデルルーティング

`model_selection_score = 0.25·complexity + 0.20·ambiguity + 0.20·risk +
0.15·context_size + 0.10·tool_risk + 0.10·domain_specialization` → 閾値でティアを
決定（≤35 small、≤65 medium、それ以外 large）。続いて **強制エスカレーション**:
目的中の hazard キーワード（auth / payment / production / secret / external-send /
delete）、本質的に高リスクなタイプ、`risk ≥ 70`、巨大コンテキスト、連続 2 回以上の
失敗。既定のバイアスは「最も安く実行できるティア」。

**effort はティアとは別の軸。** ティア = *どの* モデルか。**effort**（low / medium /
high / xhigh / max）= *どれだけ深く考えるか*。ルーターは推論難度の軸（complexity +
ambiguity + specialization）からタスクごとの `recommended_effort` を定め、hazard /
高リスク / 反復失敗で引き上げ、**割り当てエージェントのベースラインで下限を切る**
（各エージェントテンプレートは `recommended_effort` を持ち、ティア由来で既定値が
決まる: large→high、medium→medium、small→low）。よって SecurityReviewer は
≥high effort で、AutoChecker は low で推論する。CLI は `… / effort=high` と表示し、
バックエンドは seam 経由でこれを運ぶ（対応していれば API の thinking budget に
マップされる）。

## 7. 権限モデル

最小権限: 明示的に付与されていないアクションは **拒否**。危険／外向き／破壊的な
アクションは `NEEDS_APPROVAL` を返し、承認ゲート（Approval Gate、dry-run 可）を
通過しなければならない。子エージェントは親が持たない権限を決して保持できない
（`enforce_child_subset`）。

## 8. レビュー計画

リスクベースであり、決して一律ではない。文書整形 → 自動チェック 1 回。
実装 → テスト＋要件＋エッジケース。auth/secret/payment/external → ＋セキュリティ
レビュー＋ Judge ＋ **human gate**。リリース → 独立レビュー＋ Judge ＋ human gate。
**観点は分離される** ── 各レビュアーはちょうど 1 つの視点だけを見る（要件 *または*
エッジケース *または* セキュリティ *または* UX）。

プランナー（`review_planner.py`）が *どの* 観点かを決め、`reviewer.py` がそれらを
*実行* する ── 各観点はバックエンド経由の単一視点 LLM レビュー（または
`NullBackend` 下では決定的な構造レビュー）として走り、`ReviewResult` を生成し、
Judge がそれを集約する。まだ成果物が無い場合、観点は契約／計画そのものを
レビューする。`tehai review` / `Orchestrator.review_and_judge` で実行する。

## 9. Judge

レビューの *根拠* に対する決定的な優先順位:
`discard（critical/security）> rerun（一時的）> revise（その他の失敗）>
accept`。理由と根拠（観点別の判定／深刻度）が記録される。

## 9.5 実行と安全性（`executor.py`）

`Orchestrator.execute(plan)` は DAG をトポロジ順に辿る。各タスクで、割り当てられた
エージェントが **expected_output の成果物を生成** し（バックエンド経由の実コンテンツ、
または `NullBackend` 下の決定的スタブ）、その *成果物そのもの*（計画ではない）が
レビューされる。Judge の判定がステートマシンを駆動する:

```
generate → review(artifact) → judge ─ accept ───────────────→ completed
                                     ├ accept + human-gate ──→ escalated (awaiting approval)
                                     ├ revise / rerun ───────→ retry (tier escalates) up to guard cap → escalated
                                     └ discard ──────────────→ failed
```

上流の成果物は下流タスクへコンテキストとして引き継がれる。プロヴェナンスは成果物
ごとに記録される。

**接地（`--sandbox`、opt-in）:** サンドボックスを有効にすると、生成された成果物は
最小 env・POSIX リソース制限・プロセスグループのタイムアウト kill のもと、一時
ディレクトリで実際にコンパイル/実行される（`python -m py_compile` ＋
unittest/pytest、`node --check`、`tsc --noEmit`）。実際の pass/fail が
**auto-check 観点を上書き** するので、本物のコンパイル/テスト失敗が Judge を REVISE に
駆動する。実機検証済み: gemma4 が `add.py` ＋ unittest を生成し、サンドボックスが
それを緑で実行し、Judge が accept した。

**安全性:** Executor は成果物の *テキスト* のみを生成し、（`--sandbox` 時は）それを
隔離下で実行する ── 危険な／副作用のあるアクションは決して行わない。契約の
`required_tools` に危険な権限（deploy/push/delete/外部送信）が含まれる場合、実行は
Approval Gate で止まり、タスクは **escalated（自動実行は決してしない）**。
サンドボックスは **既定で OFF**（モデル出力の実行は危険）。有効時は OS の
**名前空間隔離** を `unshare` 経由で自動使用する（user ＋ **network** ＋ pid ＋ ipc ＋
uts、非特権）ので、生成コードは **ネットワークに到達できない** ── CLI は
`sandbox: on/unshare` と表示する。さらに **`/home` と `/root` に空の tmpfs を被せる**
ので、生成コードはユーザーの秘密（SSH 鍵／dotfiles／トークン）を読めない ──
テストで検証済み。**`--sandbox-strict`** で隔離を *必須* にできる（非隔離での実行を
拒否）。FS のハードニングは **deny-list であって完全な jail ではない**（/home,/root
の外の秘密は読める可能性がある ── bwrap/nsjail/コンテナが必要）。ASSUMPTIONS #16 /
FUTURE.md B を参照。

## 10. ロギングと評価

フェーズ 1: **忠実に記録し、何も変えない。** 追記専用 JSONL ledger
（`schemas/log_record.schema.json`）。`tehai run` は実際の `elapsed_seconds`・
レビュースコア・手戻り・judge 判定を記録する。Evaluation Store はタスクタイプ／
モデル／エージェント／戦略ごとの成功率、手戻り、レビュースコア、コスト・時間見積もり
誤差、エスカレーション率・human-override 率を集計し、**提案** としての改善を出す。
**フェーズ 2（完了）:** `tehai calibrate <ledger>` は ledger を具体的な *提案* config
diff に変える（ティア別成功率からのルーター閾値、観測されたティア別コスト/秒） ──
**決して自動適用しない**。`--apply cfg.json` は *採用可能な* config を書き出し、
人間がそれをレビューし、`tehai plan/run --config cfg.json` が採用する。採用は、
狭いつまみ（ルーター閾値のみ）を読み込む明示的な人間の行為 ── コードの自己改変では
ない。フェーズ 3（有界な自動チューニング）は設計上スコープ外のまま。CI: `Makefile`
＋ `.github/workflows/ci.yml` が、すべての push をスイート＋ CLI スモークでゲートする
（仕様 §12）。

## 11. CLI / API

`tehai plan|run|review|meta|teams|verify|agents|orgs|evaluate|calibrate`
（`meta`/`teams`/`verify` はマルチチーム層を駆動する ── 末尾近くのセクション参照）。
ライブラリ API: `from tehai import Orchestrator; o =
Orchestrator.default(backend="ollama")`。`o.plan("…")` → `RunPlan`、
`o.execute(plan)` → `{task_id: ExecutionResult}`、`o.review_and_judge(contract)` →
`(plan, results, decision)`。`tehai plan` は blocked なタスクで、`run` は failed な
タスクで、`review` は revise/discard/rerun で非ゼロ終了する ── よって CI はいずれでも
ゲートできる。

## 12. テスト

216 個の標準ライブラリ `unittest` テスト ── 契約検証、加重ルーティングと全
エスカレーション、ガードの全却下理由、権限のサブセット、リスクベースレビュー、
judge の優先順位、レジストリ／組織の整合性、ミニ schema validator、そしてエンド
ツーエンドのオーケストレーション検査（≥3 契約・すべて schema 妥当・トポロジ順・
agent + model + review が割り当て済み・サンプルログが schema 妥当）。実行:
`python3 -m unittest discover -s tests -t .`

## 13. 今後

[FUTURE.md](FUTURE.md) を参照: LLM バックエンドの配線（seam は既に存在）、要求感応の
分解、AgentOps/CI ゲート、段階的な自己改善ラダー（可視化 → 提案 → 有界な自動
チューニング）。

## マルチチーム AgentOps 層（`tehai/teams/`）

統制された **マルチチーム** 層が、上記のシングルチーム・プリミティブを再実装する
ことなく組み合わせ、AI 開発組織を構成する。

```
product goal
   ▼
Meta Orchestrator ── select team composition (Team Registry, 7 teams)
   ▼  Team Contracts (a DAG over teams)
Team Orchestrator (per team) ── Team Contract → tehai pipeline (architect/router/review/judge/execute)
   ▼  team result
Failure Router ── classify failure_type → route to the ROOT-CAUSE team (not a blind retry)
   ▼
Autonomous Loop Guard ── auto-reroute (low/med risk) OR stop (security/repeat/cost/prod → approval gate)
   ▼
Global Evaluation Store ── cross-team metrics + proposals (never auto-applied)
```

**チーム**（Team Registry、`data/teams/registry.json`）: Product Planning,
Architecture, Implementation, Verification, Security, Integration, Documentation ──
各チームは mission、内部エージェント（Agent Registry から再利用）、許可／禁止の
タスクタイプ、内部フェーズ・パイプライン（tehai の OrgTemplate に変換される）を持つ。

**Failure routing**（`failure_router.py`）: 11 種の failure type が根本原因で振り分け
られる ── `requirement_ambiguity`→Product Planning、`architecture_conflict`→
Architecture、`integration_conflict`→Integration（Implementation ではない）、
`security_risk`→Security（＋human gate）、`cost_overrun`→Meta/縮小、
`repeated_failure`/`permission_violation`→人間。失敗が同じチームを盲目的に再実行する
ことは決してない。

**Autonomous Loop Guard**（`loop_guard.py`）: 低／中リスクの失敗は自動で再ルーティング
するが、セキュリティリスク・同一 failure type の 3 回反復・コスト上限・本番/外部
アクション・低い judge 確信度・チーム衝突・スコープ変更では停止する（分類付き:
escalate / human-approval / shrink / clarify / fail / defer）。再ルーティング時は
根本原因チーム **とその下流サブツリー** が再実行される（カスケード）。

**Cross-team competition**（`competition.py`、仕様 §15）: 高リスクな設計（hazardous な
goal または `compete=True`）に対し、競合する複数アプローチ（maintainability / speed /
security）を goal のリスクプロファイルで重み付けした 8 基準で採点する ── セキュリティ
感応な goal は security-first アプローチを選ぶ。高コストなのでゲートされている。

```bash
python3 -m tehai meta "Todoアプリにタスク完了フラグ機能を実装する"   # 6 チームのフルフロー
python3 -m tehai meta "認証機能を実装する"                          # Security Team が加わる
python3 -m tehai teams                                             # チームテンプレート一覧
python3 -m tehai verify                                            # シナリオ実行 → verification_report.md
```

ライブラリ: `from tehai.teams import MetaOrchestrator;
MetaOrchestrator.default().run(goal)` は `MetaRunResult`（チーム契約、実行順、ループ
履歴、clarification レポート、メトリクス）を返す。`injected_failures={team:
(FailureType.X, n)}` は failure routing を決定的に実演する ── 検証ハーネスで使用。

**検証:** `verification/run_verification.py` は 9 シナリオ（意図的な曖昧さ／
セキュリティ／統合衝突／反復失敗／コスト超過の失敗を含む）を実行し、ルーティング＋
loop-guard の挙動を検査し（41 チェック）、A/B/C の構造比較（単一の大型エージェント vs
単一マルチエージェントチーム vs マルチチーム）付きで
[`verification_report.md`](verification_report.md) を書き出す。Schema:
`schemas/team_contract.schema.json`、`schemas/team_registry.schema.json`。

## ライセンス

MIT。
