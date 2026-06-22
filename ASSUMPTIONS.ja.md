# ASSUMPTIONS（前提）

[English](ASSUMPTIONS.md) | **日本語**

MVP 構築中に置いた妥当な前提。仕様の「ブロックせず進め、ここに記録せよ」という
指示に従ったもの。いずれも後から見直すコストは小さい。

## スコープと環境
1. **配置**: 新規の独立リポ `~/Projects/tehai`（ユーザー確認済み）。これは独立した
   *委譲 / AI 組織プランニング* 層であり、姉妹プロジェクトである `recurse`（仮説
   ループ）・`rinne`（再帰コードエンジン）・`self-improve-arch`（サービス RSI）を
   補完する。これらのいずれも、汎用の Task DAG ＋リッチな Task Contract ＋ Agent
   Registry ＋多因子 Model Router ＋組織テンプレートを実装していない ── それが
   tehai の提供するもの。
2. **バックエンドの実在性（Q2 は未回答のまま）**: 推奨された **「決定的＋ LLM seam」**
   から始めた。LLM seam は現在 **分解について配線済み**（`--backend ollama|claude-cli`）。
   既定は `NullBackend` のまま（決定的・オフライン・単体テスト可能）。LLM が *提案* し、
   決定的ガードが *境界づけ*、不正出力は *フォールバック* する。`EchoBackend` はテスト
   ダブル。
   - **実際の Ollama 環境**（この機材、2026-06-19）: 接続先は
     **`http://localhost:11434`**（`172.24.224.1` ではない ── `~/Projects/context.md`
     は古い）、モデルは **gemma4** 系（`gemma4:latest`, `gemma4:e2b`）。旧来の
     `gemma2`/`codellama`/`llama3` は無い。`OllamaBackend` の既定をそれに合わせて
     更新済み。`--ollama-host` で上書き可。
   - `claude-cli` / `ollama` アダプタは `recurse/recurse/llm.py` を踏襲。ティア→モデルの
     マップは編集可能。
3. **言語 / 依存**: Python ≥3.10、**標準ライブラリのみ**（`recurse` /
   `self-improve-arch` およびハウススタイルに合致）。テストは標準ライブラリの
   `unittest` を使うので、インストール無しで `python3 -m unittest discover` が動く。
   この機材のインタプリタは `python3`（bare な `python` は無い）。

## 分解とスコアリング
4. **決定的な分解はヒューリスティック。** Task Architect は選ばれた組織テンプレートの
   フェーズ・パイプラインをインスタンス化する。LLM のように要求を「理解」する
   **わけではない** ── フェーズの *タイプ* はテンプレート由来。それでも 2 つの仕組みで
   要求感応になる: (a) **LLM バックエンド** が要求の形に沿ったタスク集合を提案する
   （`--backend ollama` はテンプレートの 7 に対し 4 タスクを生成した）、(b)
   **些末／アトミックな要求は `single_deliverable` 組織（1 タスク）に縮退する** ので
   「typo を直す」がフルパイプラインを生まなくなった。非自明なオフライン要求は依然
   固定の組織パイプラインを使う（許容範囲 ── MVP の眼目はフェーズの最小性ではなく
   統制構造）。
5. **スコアのベースライン**（`architect.py` の `_COMPLEXITY/_DOMAIN_SPEC/_TOOL_RISK/
   _STEPS/_CTX_TOKENS`）は手調整の定数で、学習されたものではない。意図的に保守的
   （リスク時はエスカレーション寄りにバイアス）。Evaluation Store がこれらを時間を
   かけて再較正する想定の機構（ゲート付き）。
6. **リスクは要求の粒度で計算される**: hazardous な要求の全サブタスクが高めの
   リスクを継承する（保守的）。安全側として許容。将来版ではサブタスクごとに
   リスクを局所化できる。
7. **`context_size_score`** は `estimated_context_tokens`（≈ tokens/600、クランプ）
   から導出される ── 実測のコンテキストウィンドウではなくプロキシ。

## ルーティングとレビュー
8. **ルーター閾値**: 加重スコア ≤35 → small、≤65 → medium、それ以外 → large。
   `ModelRouter(small_max, medium_max)` で調整可能。
9. **hazard 検出は OBJECTIVE のみを読む**。`constraints` は読まない。
   「秘密情報をログに出力しない」のような標準的な安全禁止事項は hazard トークン
   （秘密）を含むため、さもなくば全タスクを LARGE ＋ human gate に強制してしまう。
   裸の `email`/`メール`/`送信` は除外される（メールフィールドの検証は無害）。
   外向きの *送信*（`メール送信`, `外部送信`, `webhook`, `slack通知`）のみが
   hazardous。
10. ライブアダプタにおける **ティア→モデルのマッピング**（haiku/sonnet/opus、
    gemma2/llama3.1）はプレースホルダ。バックエンドを配線する際に実際のモデル ID を
    設定すること。

## 組織テンプレートとエージェント
11. **6 つの組織テンプレート** とそのフェーズ・パイプラインは固定のシード。組織選択は
    キーワードベース（部分文字列マッチ、優先順位付き、`product_delivery` をフォール
    バック）。曖昧な要求はより広いテンプレートを選びうる（例: 「auth」機能は
    `security_review` に分類される）。これは設計上、よりレビューを多くする方向に
    倒している。
12. **Agent Registry は同梱の `data/agents/registry.json`** 配列 1 つ（ローダは
    `*.json` を glob するので、テンプレート別ファイルも機能する）。`allowed`/`forbidden`
    のアクション集合は内部整合的かつ最小権限になるよう記述。`git_push`/
    `production_deploy` を持てるのは `ReleaseManager` のみ（いずれもゲート付き）。

## 状態とロギング
13. **`run_id` は要求の SHA-1 から導出される**（内容アドレス指定・実時計に非依存）
    ので計画は再現可能。`--run-id` で上書き可。
14. **サンプルログは計画時の *見積もり*** であり、実際の実行結果ではない（MVP に
    実行は無い）。`estimated_cost` は粗いティア別定数 × ステップ数を使う。これらは
    Evaluation Store を初期投入し動かすために存在する。
15. **自己改善はフェーズ 1 のみ**: 記録＋可視化＋ *提案*。ルーティング/分解ロジックの
    自動書き換えは無い。フェーズ 2〜3 は設計済みだが未実装（FUTURE.md 参照）。
16. **Executor ＋ sandbox。** 既定では `tehai run` は成果物の *テキスト* を生成し、その
    テキストをレビューする。危険な／副作用のあるアクションは決して行わない（それらは
    Approval Gate で止まり → escalated）。**opt-in の `--sandbox`** を付けると、生成
    コードを実際にコンパイル/実行し（py_compile ＋ unittest/pytest、node --check、
    tsc --noEmit）、実際の pass/fail が auto-check 観点を上書きする。
    - **OS 隔離（既定 `isolation="auto"`）**: `unshare` が非特権で動くとき（user
      namespace 有効 ── この WSL2 ホストでは真）、ランナーは user ＋ **network** ＋
      pid ＋ ipc ＋ uts namespace で実行されるので、生成コードは **ネットワークに
      到達できない**（主な exfiltration リスク）。1 回プローブし、非対応なら
      best-effort にフォールバック。CLI は `sandbox: on/unshare` と表示する。
    - **best-effort 層（常時）**: 一時ディレクトリ、最小 env（秘密を継承しない・
      PYTHONPATH 無し）、`shell=False`、POSIX rlimit（CPU/AS/FSIZE）、新セッション＋
      タイムアウト時の **SIGKILL** プロセスグループ刈り取り（namespace 内で PID 1 と
      なるランナーは SIGTERM を無視するため）、ファイル名サニタイズ（`..`/`/` 不可）。
    - **FS 読取ハードニング（deny-list）**: mount namespace 内で `/home` と `/root` に
      空の tmpfs を被せるので、生成コードはユーザーの秘密（SSH 鍵・dotfiles・
      トークン）を読めない ── テストで検証済み。python のパス（/usr,/lib）は無傷。
      best-effort（mount 失敗時はスキップ、ネットワーク隔離は依然有効）。
    - **依然として部分的**: これは deny-list であって rootfs jail ではない ──
      /home,/root の外の秘密（例: 一部の /etc ファイル）は読める。完全な allow-list
      jail には bwrap/nsjail/コンテナ（無し）か docker daemon（ここでは停止中）が
      必要で、読み取り専用の `/` remount はこのホストでは書き込み可能な workdir を
      切り出せない（`/tmp` が `/` の superblock 上にある）。サンドボックスは既定で
      OFF のままで、明示的に有効化する必要がある。
    - pytest は実行と *同じ最小化 env* で検出される（venv の `PYTHONPATH` 上にしか
      存在しなかったため）。無い場合、テストファイルはインタプリタで直接実行され、
      `unittest.main()` 形式のテストのみ走る（main ガードの無い pytest 専用ファイルは
      false pass になる ── 既知の限界）。

## プロセス
18. ユーザーは初期のターンで「design-docs-first」を選び、その後「新規リポ
    `~/Projects/tehai`」に切り替えた ── これをコード MVP を作る承認（ドキュメントのみ
    ではない）と解釈した。受け入れ基準は構築前に凍結し、構築後に検証した。

## マルチチーム AgentOps 層（`tehai/teams/`）
19. **シングルチームのプリミティブの *上に* 構築し、横に並べてはいない。** Meta/Team
    層は既存の Task Architect・Agent Registry・Model Router・Reviewer・Judge・
    Executor・Evaluation Store と 6 つの組織テンプレートをそのまま再利用する（仕様は
    それらを同一に再規定している）。チームの分解は、その内部フェーズ・パイプラインを
    tehai の `OrgTemplate` として包み、既存の `Orchestrator` を呼ぶことで行う
    （`Orchestrator.plan` に新規 optional の `org=` フックを 1 つ追加しただけ）。
20. **決定的・再現可能な失敗注入。** 決定的エンジンは通常の goal から曖昧な要件を
    「検出」できないので、検証ハーネスは `injected_failures={team: (FailureType,
    resolve_after)}` で失敗を注入し、Failure Router ＋ Loop Guard を再現可能に実演する。
    `resolve_after=1` は「再ルーティングで根本原因が直る → 回復」をモデル化し、大きな値は
    「持続する → ガードが停止」をモデル化する。真の契約レベルの曖昧さ（漠然とした
    目的）も `TeamContract.validate` で検出される。
21. **A/B のベースラインは *構造的* であり経験的ではない。** verification_report.md は
    Single Large Agent (A) / Single Multi-Agent Team (B) / Multi-Team (C) を能力
    （分解・リスクベースレビュー・チーム横断の失敗ルーティング・承認ゲート）で比較
    する。C の数値は実際の実行、B のタスク数は実際の tehai 実行、A はモデル化。これは
    LLM 品質ベンチマークではない（それには配線済みバックエンドが要る）。
22. **再ルーティングはカスケードする。** 失敗時、エンジンは根本原因チーム *と* その
    推移的な下流サブツリー（トポロジ順）を再実行するので、依存先は修正済みの出力を
    見る。反復上限＋ loop guard で有界（既に通過しているチームの再実行は失敗を増やさ
    ない）。チームレベルの能力境界は `forbidden_task_types` ＋下層 tehai のエージェント
    権限モデル（child ⊆ parent）で強制される。
23. **Cross-team competition（§15）** は決定的でゲート付き: 各 priority が最適化する
    基準を押し上げ、Judge は goal のリスクプロファイルで基準を重み付けするので、
    セキュリティ感応な goal は security-first アプローチを選ぶ。これは LLM の設計
    バトル（それにはバックエンドが要る）ではない ── 決定構造をモデル化したもので、
    hazardous な goal / `compete=True` のときのみ自動発火する。
