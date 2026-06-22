# A (人間組織体系) vs B (検証中心の平らなデータフロー) — 実測レポート

- backend: `null`（決定的・オフライン） / シナリオ数: 5
- コストモデル: tier-weighted model-call proxy (tehai.costmodel): 1 generation + 1 call per review lens, by tier

## 集計

| 指標 | A 人間組織 | B 平ら検証 | 含意 |
|---|---|---|---|
| ① 信頼性 true_success_rate | 0.6 | 0.8 | 完了かつ欠陥流出ゼロの割合 |
| ① 欠陥の流出 escaped_defects | 1 | 0 | 成功偽装のまま出荷した盲点欠陥の数 |
| ② コスト total_cost_usd | 29.56 | 7.66 | tier重みコール代理（A/B ≈ 3.86×） |
| ② コスト total_model_calls | 611 | 202 | 生成+レビュー呼び出し総数 |
| ③ 人間介入率 | 0.0 | 0.2 | 自律で解けず人間に上げた run の割合 |

## シナリオ別

```
scenario               arch  true_succ escaped  calls   cost$  human
--------------------------------------------------------------------
baseline_clean         A           yes       0     63    1.62      -
                       B           yes       0     27    0.55      -

overt_impl             A           yes       0    171    3.82      -
                       B           yes       0     53    1.05      -

blindspot_security     A           yes       0    222   20.70      -
                       B           yes       0     52    4.72      -

blindspot_doc_escape   A            NO       1      8    0.16      -
                       B           yes       0      4    0.04      -

exhausted_retry        A            NO       0    147    3.26      -
                       B            NO       0     66    1.30    YES

AGGREGATE                         true_succ  escaped  calls   cost$  human
  A (human-org hierarchy)               0.6       1    611   29.56    0.0
  B (flat verification dataflow)        0.8       0    202    7.66    0.2
```

### baseline_clean
- 要求: 二分探索のユーティリティ関数とその単体テストを実装する
- 狙い: 欠陥なし — 階層の純粋なオーバーヘッドを測る基準線
- A: true_success=True, escaped=0, calls=63, cost=$1.62, human=False, teams=6, reroutes=0, late_caught=[]
- B: true_success=True, escaped=0, calls=27, cost=$0.55, human=False, nodes=7, local_retries=0

### overt_impl
- 要求: ユーザー登録のメール形式バリデーションを実装する
- 狙い: 明示的な実装バグ（どのレビューでも検出）。A=2回カスケード再ルート / B=ノード再試行
- A: true_success=True, escaped=0, calls=171, cost=$3.82, human=False, teams=6, reroutes=2, late_caught=[]
- B: true_success=True, escaped=0, calls=53, cost=$1.05, human=False, nodes=7, local_retries=6

### blindspot_security
- 要求: ログインAPIのパスワード照合処理を実装する
- 狙い: 実装に潜む認証の盲点。A=生成チームは見逃し独立な下流で遅れて検出 / B=ノードの外部検証で即捕捉
- A: true_success=True, escaped=0, calls=222, cost=$20.7, human=False, teams=7, reroutes=0, late_caught=['implementation']
- B: true_success=True, escaped=0, calls=52, cost=$4.72, human=False, nodes=7, local_retries=3

### blindspot_doc_escape
- 要求: READMEのインストール手順の誤字を修正し整形する
- 狙い: doc-only 構成で独立な下流検証が無い盲点。A=流出 / B=ノードで捕捉
- A: true_success=False, escaped=1, calls=8, cost=$0.16, human=False, teams=1, reroutes=0, late_caught=[]
- B: true_success=True, escaped=0, calls=4, cost=$0.04, human=False, nodes=1, local_retries=1

### exhausted_retry
- 要求: ファイルアップロードのサイズ制限チェックを実装する
- 狙い: 上限を超える厄介な欠陥。A=同一失敗3回で停止 / B=ローカル上限超過で人間ゲート（Bも万能ではない）
- A: true_success=False, escaped=0, calls=147, cost=$3.26, human=False, teams=6, reroutes=3, late_caught=[]
- B: true_success=False, escaped=0, calls=66, cost=$1.3, human=True, nodes=7, local_retries=6

## 読み方・妥当性の限界
- これは**決定的な構造シミュレーション**であり、実LLMのベンチではない。コストは「tier重み付きの呼び出し回数の代理」。
- A の blind-spot の遅延検出/流出は、A の**実構成**に基づきハーネスが規則でモデル化している（生成チームの self-review は盲点を共有 → 独立な下流チームだけが捕捉）。B の捕捉は実コード。
- 結論は「どちらが常に優れるか」ではなく、**階層が生むコストの所在**と**検証中心が拾う盲点**を再現可能な数字で示すこと。
