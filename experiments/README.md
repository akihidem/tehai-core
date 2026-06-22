# experiments/ — A vs B 実証実験

`docs/ai-native-org.html` で立てた問い ——「AI前提の時代、tehai の**人間組織体系 (A)** は最適か、それとも**検証中心の平らなデータフロー (B)** か」—— を、主張で終わらせず tehai 上で**実測**する。

## 走らせ方

```bash
# リポジトリルートで
python3 -m experiments.org_vs_dataflow            # 比較表を表示 + REPORT.md/results.json 生成
python3 -m experiments.org_vs_dataflow --json-only # 表示なし、ファイルだけ
python3 -m experiments.org_vs_dataflow --backend ollama  # 実モデルで spot-check（任意・既定は null）
```

出力:
- `experiments/REPORT.md` — 集計表 + シナリオ別 + 妥当性の限界（人間可読）
- `experiments/results.json` — 機械可読の全結果（決定的・2回走らせても byte 一致）

## 何を測るか（3指標）
1. **信頼性** `true_success_rate` … 完了 **かつ** 欠陥流出ゼロの割合 ＋ `escaped_defects`
2. **コスト** `total_model_calls` / `total_cost_usd` … tier 重み付き呼び出し代理（再試行・再ルート込み）
3. **人間介入率** … 自律で解けず人間に上げた run の割合

## A と B
- **A = 人間組織体系** = 実物の `tehai.teams.MetaOrchestrator`。overt 欠陥は `injected_failures` で実注入。失敗は原因チームへ差し戻し、**そのチーム＋下流サブツリーをカスケード再実行**。
- **B = 検証中心の平らなデータフロー** = 実物の `tehai.FlatDataflowOrchestrator`。単一の平らな task DAG を**全ノード外部検証**し、失敗は**そのノードだけローカル再試行**、上限超過で人間ゲート。

両者のコストは同一の `tehai.costmodel` で再構成 → 比較は**会計規則の差ではなく構造の差**の創発。

## Threats to Validity（妥当性の限界・隠さない）
- **決定的な構造シミュレーション**であって、実LLMの品質ベンチではない。`backend=null` 既定。コストは「tier 重み付きコール数の代理」。
- **A の blind-spot** の遅延検出/流出だけは、A の**実構成**に基づきハーネスが規則でモデル化している（生成チームの self-review は盲点を共有 → 独立な下流チームだけが捕捉。無ければ流出）。B 側の捕捉は実コード。
- 欠陥モデルは単純化（overt / blind_spot の2層）。`resolve_after` は固定。
- 他の代替構造（市場・ブラックボード・進化型）は対象外。B のみを A と比較する。

## 結論の立て方
「どちらが常に勝つか」を断定しない。**階層が生むコストの所在**（チーム多重 × カスケード再ルート）と、**検証中心が拾う盲点**（独立な下流が無いと A は流出）を、再現可能な数字で可視化する。数字が `docs/ai-native-org.html` の主張と食い違えば、**主張側を実測に合わせて直す**。
