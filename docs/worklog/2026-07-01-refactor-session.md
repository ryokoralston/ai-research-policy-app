# 作業ログ — 2026-07-01 リファクタリングセッション

- **ブランチ**: `claude/fable-5-ai-policy-prompts-kqtqac`（全21コミット）
- **PR**: [#1 Debt-map refactor](https://github.com/ryokoralston/ai-research-policy-app/pull/1)
- **実施**: Claude Code（実装・テスト） + 人間（方針決定・eval 実行）
- **成果物**: バグ修正5件、リファクタ11項目、テスト 1ファイル16件 → 16ファイル92件、
  `refactor-instructions.md`（負債マップ兼決定記録）

---

## 1. 分析フェーズ

コードベース全体を読み、実装担当モデル向けの指示書 `refactor-instructions.md` を作成。
技術的負債を A（重複）/ B（契約の曖昧さ）/ C（性能）/ D（テスト不足）/ E（その他）に分類し、
「実装可」「提案のみ」「質問対象」に仕分けた。

## 2. 人間の判断を仰いだ項目と決定内容

| 項目 | 決定 |
|---|---|
| B-3: Report status の語彙不統一（`complete` vs `completed`） | **案A: `completed` に正準化** + 起動時冪等マイグレーション |
| B-3残: PATCH が不正 status を黙って無視 | **400 で拒否**（他フィールド適用前に検証） |
| B-1: RAG 検索結果の並び順（UUIDソート＝実質ランダム） | **案A: 真の読書順**（`doc_id, chunk_index`） |
| E-2: Digest メールの日本語見出し | **英語に統一**（メールも「UI は英語」ルールの対象と決定） |
| A-3: save-to-library のチャンク方式 | **案A: `chunk_plain_text` に統一 + 短文フォールバック** |
| 残りの実装可11項目（D-1, A-1, A-4, A-5, A-7, B-4, C-2, E-4, A-2, B-2, A-6） | **全部実施** |

## 3. 修正したバグ

1. **Report status 不整合** — 生成完了時に UI が知らない `complete` を保存していた。
   `completed` に正準化し、既存行は `normalize_legacy_report_status()`（起動時・冪等）で移行。
2. **RAG 並び順** — リランク後のソートキーがランダム UUID で、コメントの意図（読書順）にも
   リランク順にもなっていなかった。`(doc_id, chunk_index)` ソートに修正。
3. **SSE エラーストリームのハング** — research のエラーイベントは `event_type` を持たず、
   旧終端判定が永久にマッチしなかった。イベント名ベースの判定に変更（A-2/B-2 実施中に発見）。
4. **フロント SSE パーサのチャンク境界バグ** — `event:` 行と `data:` 行が別チャンクに
   分かれるとイベント名を失う。共通化（`consumeSseStream`）時に修正（A-6 実施中に発見）。
5. **Digest メールが日本語** — プロンプトと `lang` 属性を英語化。

## 4. リファクタ（挙動維持）

- `utils/export.py` — Markdown→txt / PDF 生成の重複統合（analysis の txt で箇条書きが
  `- ` 正規化される微小変化のみ・報告済み）
- `utils/masking.py` — `***` センチネル処理の集約 + settings PUT にガード追加
- `anthropic_client.generate_json` — prefill+フェンス除去+parse の重複統合
- `utils/sse.py` — research/debate の SSE キュー配管統合（タイムアウト 60/120 秒維持）
- `rag_service.index_web_content` — save-to-library インデックスをサービス層へ移動 + 標準チャンカー化
- `rag/retriever.py` — CrossEncoder を `lru_cache` でキャッシュ（毎クエリ再ロード解消）
- `rag_service` — ドキュメントタイトルの N+1 クエリを IN 一括取得に
- 例外握りつぶし4箇所に `logger.warning` 追加（継続挙動は維持）
- `_strip_metadata` 同名2関数のリネーム、コメントドリフト修正

## 5. テスト・検証

- **バックエンド**: `backend/tests/` 16ファイル・92テスト、全て合格。
  すべてオフライン実行可（APIキー・ネットワーク・重量依存不要。chromadb 等はスタブ）。
- **フロントエンド**: `tsc --noEmit` / `eslint` / `next build` すべてクリーン。
- **Evals**（人間がローカルで実行・2026-07-01）:
  - `eval_research_queries`: code 10.0/10、combined 8.2/10、pass 8/8、JSONパース失敗ゼロ
  - `--compare-examples`: v2（例示なし）8.2 / v3（本番・例示あり）8.2、delta +0.0。
    v3 も code 10.0/10。※例示の効果ゼロという計測結果は将来のプロンプト簡素化の判断材料

## 6. 未実施（提案のみ・要承認）

- C-3: async ハンドラ内の同期ブロッキングの `to_thread` 退避
- C-4: ループ内逐次 `db.commit()` の一括化（部分保存の挙動変化あり）
- E-1: PDF フォントの Linux 対応（ヒラギノ絶対パス → 同梱はライセンス判断要）
- E-3: Alembic 導入
- E-5: `datetime.utcnow()` の置換（Python 3.12 非推奨）

## 7. 申し送り

- PR #1 はレビュー・マージ待ち。マージ後、次回作業はこのブランチを `main` から作り直すこと。
- save-to-library の既存ドキュメントは旧チャンク（800文字）のまま。気になるものは削除→再保存で新方式に揃う。
- Analysis の txt エクスポートは箇条書きが `- ` に正規化される（唯一の意図的な出力変化）。
- 詳細な根拠・検証方法は `refactor-instructions.md` の各項目（【対応済み】注記）を参照。
