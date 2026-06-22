# FUTURE — 拡張計画

[English](FUTURE.md) | **日本語**

**ステータス:** 当初のロードマップ A–E は構築済み（分解 → スコア → レビュー →
生成 → 実行 → 成果物レビュー → judge → FSM → ログ → 評価 → 提案 → config 採用）、
opt-in の OS 隔離サンドボックスで接地済み。以下に残るものは、**この環境に無い
ツールにブロックされている**（完全な FS jail）、**安全のため意図的にスコープ外**
（フェーズ 3 自動チューニング）、**人間の判断**（公開）、あるいは **新規能力の無い
polish**（sqlite/dashboard、姉妹プロジェクト連携）のいずれか。各項目に印を付けた。

## A. モデルバックエンドの配線 ✅ 完了（分解＋スコアリング＋レビュー）
- **分解**（`architect._llm_decompose`）: モデルがサブタスクを提案し、決定的ガード
  （契約検証・委譲上限・重複/循環）が境界づける。いずれかが失敗すればヒューリスティック
  なテンプレート経路にフォールバックする。
- **スコアリング**（`scoring.Scorer`）: LLM 経路のスコアは分解呼び出しから来る。
  テンプレート経路は 1 回のバッチ・バックエンド呼び出しで再スコアされる
  （`Scorer.rescore`）。ヒューリスティックなフォールバック付き。戦略ラベルに
  `+llmscore` が付く。
- **レビュー**（`reviewer.Reviewer`）: 計画された各観点が単一視点の LLM レビューとして
  実行される → `ReviewResult` → 既存の `Judge`。決定的な観点別レビュアーが
  `NullBackend` 下／失敗時に走るので、ループはオフラインでも閉じる。`tehai review` /
  `Orchestrator.review_and_judge` として公開。
- すべて `--backend {null,echo,claude-cli,ollama}` 経由で公開。ローカル Ollama
  （gemma4）で実機検証済み: LLM レビュアーが観点別の fail/concerns を生成し、Judge が
  REVISE に集約した。
- **A 内での次**: 契約/計画ではなく、Section B の実行済み実成果物をレビュアーに
  与える。スコアリングを学習型ルーターに反映させる。

## B. 実行＋ステートマシン ✅ 完了（成果物生成）
- `executor.Executor` は割り当てられたエージェントを契約に対して走らせ
  *expected_output の成果物を生成* し（バックエンド、または NullBackend 下の決定的
  スタブ）、実成果物を Reviewer ＋ Judge に渡し、ライフサイクルを駆動する:
  accept → completed、revise/rerun → ガード上限までティアをエスカレートしてリトライ、
  discard → failed、human-gated な accept → escalated。依存関係は上流成果物を下流
  タスクへ引き継ぐ。`tehai run` / `Orchestrator.execute`。
- 権限ゲートを強制: `required_tools` に危険な権限を含む契約は Approval Gate で止まる
  （escalated）。自動実行は決してしない。
- レビューに基づく実ログ行を出力（review_score, rework_count, test_pass_rate,
  judge_decision, escalated, human_override）。
- **Sandbox**（`sandbox.Sandbox`、opt-in `--sandbox`）: 生成された成果物を、最小 env・
  POSIX リソース制限・プロセスグループのタイムアウト kill のもと、一時ディレクトリで
  実際にコンパイル/実行する（python py_compile ＋ unittest/pytest、node --check、
  tsc --noEmit）。実際の pass/fail が **AUTO_CHECK 観点を上書き** するので、本物の
  コンパイル/テスト失敗が Judge を REVISE に駆動する ── ループを LLM のテキスト読みでは
  なく実行で接地する。
- **OS 隔離**（`Sandbox(isolation="auto")`、既定）: `unshare` が非特権で使えるとき、
  ランナーは user ＋ **network** ＋ pid ＋ ipc ＋ uts namespace 内で実行される ──
  生成コードはネットワークに到達できない（exfiltration なし）。1 回プローブし、非対応
  なら best-effort にフォールバック。実機検証済み（ネットワーク遮断、gemma4 のコードが
  その下で緑で走った）。
- **強制可能な隔離**: `Sandbox(isolation="strict")` / `--sandbox-strict` は OS 隔離が
  使えない場合にコード実行を拒否する（黙って非隔離で走らせない）。
- **FS 読取ハードニング（deny-list）**: mount namespace 内で `/home` と `/root` に空の
  tmpfs を被せるので、生成コードはユーザーの秘密（SSH 鍵・dotfiles・トークン）を
  読めない。検証済み ── `$HOME` の秘密がサンドボックス内で読めない。python のパス
  （/usr,/lib）は無傷。
- タスクごとに実際の **`elapsed_seconds`** と **`actual_cost`** をログ ── Ollama
  バックエンドがトークン使用量（`eval_count`）を報告するので、コスト見積もり誤差と
  `calibrate` の観測ティア別コストが実データから計算されるようになった。
- **依然 TODO**: **完全な allow-list rootfs jail**（deny-list は /home,/root の外の
  秘密、例えば一部の /etc を取りこぼす）。このホストでは読み取り専用の `/` remount が
  書き込み可能な workdir を切り出せない（`/tmp` が `/` の superblock を共有）ので、
  完全な jail には bwrap/nsjail/コンテナ（無し）か docker daemon（ここでは停止中）が
  必要 ── 存在する環境では `isolation="docker"` を追加する。ほかに: 言語ランナーの
  追加、サンドボックス有効化をエージェントの `run_test` 権限に紐付け、claude-cli からの
  トークンコスト（現状 Ollama のみが使用量を報告する）。

## C. 要求感応の分解 ✅ 完了
- 些末／アトミックな要求は、要求から導いた task_type を持つ動的な
  `single_deliverable` 組織（1 タスク）に縮退する ──「typo を直す」がフルパイプライン
  を生まなくなった。LLM 経路は構成上、要求の形に沿う。
- **残る小さな点**: リスクを要求ごとではなくサブタスクごとに局所化する
  （ASSUMPTIONS #6） ── 現状は保守的、価値は低い。

## D. AgentOps / CI ゲート ✅ 着手済み
`Makefile`（test/demo/plan/run/clean）と `.github/workflows/ci.yml` が、すべての
push/PR を標準ライブラリのスイート＋ CLI スモーク（plan/run/evaluate/calibrate）で
Python 3.10–3.12 にわたってゲートするようになった。`tehai run` は failed タスクで、
`review` は revise-discard-rerun で非ゼロ終了するので、パイプラインは Judge で
ゲートされる。
CI ステージとして追加すべき残りのフック点（いずれも自然な居場所が既にある）:
- 自動テスト / 静的解析 / 型チェック / lint / カバレッジ → `AutoChecker` ＋
  `run_test` / `run_static_analysis` 権限。
- セキュリティスキャン / 依存監査 → `SecurityReviewer` / `DependencyAuditor`。
- prompt-eval / モデル比較 / エージェント別性能 → Evaluation Store。
- 最終ゲート: 外向きの変更に対する **Judge 判定 → 人間承認**。
- `tehai plan` は既に blocked タスクで非ゼロ終了するので、パイプラインをゲートできる。

## E. 段階的な自己改善ラダー ✅ 完了（フェーズ 1〜2）
- **フェーズ 1 ✅**: 忠実に記録し、メトリクスを可視化し、変更を *提案* する
  （`EvaluationStore.suggestions`）。自動適用は決してしない。
- **フェーズ 2 ✅**: `tehai calibrate <ledger>` が具体的な config diff を提案する
  （ルーター閾値、観測ティア別コスト/秒）。`--apply cfg.json` が *採用可能な* config を
  書き出し、人間がそれをレビューし、`plan/run --config cfg.json` が採用する
  （`config.load_config` は狭いルーターのつまみのみを尊重する ── コードでも、
  セキュリティ/権限ロジックでもない）。
- **フェーズ 3（設計上スコープ外）**: confirm-budget / holdout split の背後での有界な
  自動チューニング（cf. `self-improve-arch`）。意図的に作っていない ── 人間を介さずに
  ルーティングを自動変異させることは、まさにこのプロジェクトが防ぐよう設計された
  failure mode そのもの。セキュリティ/権限/エスカレーションのロジックを **決して**
  自動編集しない。

## F. 永続化と可観測性
- JSONL ledger を、追記専用 JSONL を真実の源として保ちつつ、クエリ可能なストア
  （sqlite、`self-improve-arch` が行うように）に差し替える。
- メトリクス上の小さな読み取り専用ダッシュボード（タスクタイプ/モデル/エージェント別の
  成功率、エスカレーション率・human-override 率、コスト/時間見積もり誤差）。

## G. 姉妹プロジェクトとの相互運用
- `tehai` が確定した契約を `rinne` の generate→L0→consensus→floor→gate エンジンに
  渡して実際のコード生成を行い、結果を Evaluation Store に戻せるオプションの
  アダプタ。

## H. マルチチーム AgentOps 層（`tehai/teams/`）✅ 完了（§15 ＋カスケード含む）
- **Cross-team competition（§15）✅**: `competition.CrossTeamCompetition` が高リスクな
  設計を競合アプローチ（maintainability/speed/security）で解き、goal のリスク
  プロファイルで重み付けした 8 基準で採点する。セキュリティ感応な goal → security-first
  アプローチが勝つ。hazardous な goal（または `meta.run(compete=True)`）で自動発火し、
  `MetaRunResult.competitions` に記録される。
- **カスケード再ルーティング ✅**: 再ルーティング時、根本原因チーム **とその下流
  サブツリー** が再実行される（トポロジ順）ので、依存先は修正済みの出力を見る。
- **実バックエンド ✅（実機検証済み）**: `MetaOrchestrator.default(backend="ollama")` が
  ライブモデルをチーム→tehai パイプライン全体に通す（doc のみの goal で実証）。大規模な
  マルチチームのライブ実行は遅い（チームあたり多数のモデル呼び出し） ── スポット
  チェック用に留める。
- **改善ループ**: Global Evaluation Store は提案のみを可視化する（§20 はチーム構成/
  ルーティングの自動変更を禁じる）。ルーター閾値のつまみは既存の `tehai calibrate
  --apply` / `--config` 経由で採用する。チームレベルの採用は意図的に人間のステップの
  ままにしている。
- **依然オープン**: Verification Team による真の（注入ではない）曖昧さ検出には
  ライブ judge が要る。決定的な実行は再現性のために失敗を注入する。
