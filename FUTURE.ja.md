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

## I. ベンダ・ルーティングとバックエンド・ベンチ ── gama（蝦蟇）✅ 完了（決定的 Conductor）
- **バックエンド ✅**: `claude-tui`（定額サブスクの Claude Code TUI レーン。従量 `--print`
  ではない）、`codex`（`codex exec`）、`gemini`（OpenAI互換・キーでゲート）、`ssh-openai`
  （OpenAI互換サーバを SSH 越しに）、リモート対応 `ollama`。すべて stdlib・使うまで不活性。
- **GamaBackend（Conductor）✅**: `task_type`（seam 越しに渡る）でテーブル引き、未マップは
  `default_backend` へフォールバック。`config.gama_from_config` で構築し、`--config` ＋
  `--backend gama` で採用。
- **外部錨としてのベンチ ✅**: `tehai bench` がタスククラス別に決定的チェッカで採点し、
  `routing_table` を提案する（提案のみ・人が承認 ── `calibrate` と同じ作法）。ローカル
  `ollama`（gemma4・5/5 クラス）で実証。
- **EnsembleBackend（モデル組み合わせループ）✅**: 同一タスクに N 個の sub-backend を
  走らせて統合（`synthesize` / `majority` / `first`）── gama の routing に対する
  mixture-of-agents 版。`--backend ensemble`・`ensemble_from_config`・
  `examples/ensemble.example.json`。homogeneous 自己アンサンブル（1モデル×温度）か
  heterogeneous mix。
  知見（敵対的検証・測定を公平化済み）: ①同一コピーは無意味 ── 7B×5 == 7B×1（easy 0.8 /
  hard 0.5）。②異種の軽量混合（7B+24B+32B）は単体7Bを有意に超える（hard 0.5→0.83）── 別々の
  blind spot を補い合う。③だが強モデル 122B（hard 1.0）には届かない ── 全メンバーが同じく外す
  クラス（大きい暗算）は統合では復旧不能で、規模/ツールが不可欠。注: 当初「異種が 122B に勝つ」
  と見えたのは**測定バグ**（推論モデルのコード未抽出・トークン切れ）── `_extract_code` 修正＋
  アーキ別 max_tokens で 122B の hard が 0.5→1.0 に回復。異種アーキ比較は公平な抽出＋トークン
  予算が要る。
- **ToolBackend（program-aided）＋構造化combination ✅（検証済み）**: 暗算できない小モデルも
  **Python を書かせて実行**すれば解ける（`ToolBackend`/PAL）── 7B+tool は数学 0/3→3/3＝122B 同等。
  **gama routing × ensemble × tool** を主権的軽量システムに合成（qa→7B+tool, code→Coder,
  研究→異種）したら hard6問で 122B と同点(1.0)、12問でも combined **0.92** vs 122B 0.83 で頑健
  （ただし 122B の code 2問落ちはトークン切れの疑いゆえ「互角」と読む・クリーンな勝ちとは言わない）。
  教訓: **"構造"（各クラスを正しい軽量機構へ振る）はコピー(無意味)も素朴な重ね(0.83)も超え、122B と
  互角・完全ローカル**。combined の唯一の取りこぼし（曜日=mod 演算を ensemble に送った）は
  **ルーティングの穴**（計算可能な推論は tool に振るべき）で能力差ではない。N 小・単発。
- **主権的リモート floor ✅（Mac Studio MLX で実証）**: `ssh-openai` が OpenAI互換サーバ
  （MLX `mlx_lm.server`・LM Studio・vLLM）を `ssh <host> curl localhost:<port>/v1/…` で呼ぶ
  （プロンプトは stdin・ポート非開放・0.5〜2 秒/コール）。`ollama` レーンにも ollama ホスト
  用の `transport:"ssh"`（`ollama run`）がある。`examples/gama_config.macstudio.example.json`。
- **見送り ── LLM Conductor**: 呼び出しごとの LLM ルータ（例: Claude が各サブタスクのベンダを
  毎回決める）は意図的に未実装 ── 透明性とコストを柔軟性と引き換えにし、自己申告依存を
  再び招く。追求するなら実験フラグの裏に置く。
- **次**: ベンダ別の実 `unit_cost` を入れて score-per-dollar を最適化（今は score→latency
  のみ）。per-instance（クラス単位でない）ルーティング。
