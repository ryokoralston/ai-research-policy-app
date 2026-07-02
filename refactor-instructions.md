# refactor-instructions.md — AI Policy Research App リファクタリング指示書

この文書は実装担当モデル（Codex / Opus 等）向けの指示書である。
目的は「既存仕様を壊さず、技術的負債を減らし、今後変更しやすい状態にする」こと。
見た目の綺麗さや全面書き換えは目的ではない。**証拠なく大きな削除・書き換えをしてはならない。**

---

## 1. Objective

1. 重複コードの統合（バックエンドのエクスポート処理・SSE配管、フロントエンドのSSEパーサ等）
2. 明白に安全な整理（コメントのドリフト修正、冗長な例外処理、N+1クエリ、モデルの再ロード）
3. 小さな責務分離（ルーターに埋まったインデックス処理をサービス層へ移動）
4. 安全網の追加（外部APIを呼ばない・オフラインで動くユニットテスト）

**変更しないこと**: 機能追加、API仕様変更、DBスキーマ変更、プロンプト文言の変更、UI変更。

---

## 2. Project Understanding

### 何をするアプリか

AIポリシー調査アシスタント。Claude (Anthropic API) と Tavily (Web検索) を使い、調査・分析・レポート生成を行うWebアプリ。

| 機能 | 主要ファイル | 流れ |
|---|---|---|
| Research | `backend/routers/research.py` → `services/research_agent.py` | クエリ分解(Claude) → Tavily並列検索 → ソース毎要約(fast model) → 統合(main model, SSEストリーミング) |
| Document Library (RAG) | `routers/documents.py` → `services/rag_service.py`, `rag/*` | PDF/URL/YouTube取込 → チャンク化 → sentence-transformers埋め込み → ChromaDB。Q&Aは `search_documents` ツールを使う手動ツールループ + リマインダーツール |
| Risk Analysis | `routers/analysis.py` → `services/risk_analyzer.py` | セクション毎生成 + prefill/stop_sequences によるスコアJSON抽出 |
| Debate | `routers/debate.py` → `services/debate_service.py` + `templates/personas.py` | 10ペルソナ×4ラウンド + モデレーター統合、SSE |
| Reports | `routers/reports.py` → `services/report_generator.py` + `templates/*` | 3テンプレート、セクション毎 or 単一パス（語数制限検出時）、txt/PDFエクスポート |
| Daily Digest | `routers/digest.py` → `services/digest_service.py` | APScheduler cron → Tavily → Haikuで見出し生成 → Gmail SMTP送信 |
| Settings / Auth / Reminders | `routers/settings.py`, `routers/auth.py`, `services/auth.py`, `services/secret_crypto.py`, `services/reminder_tools.py` | モデル/APIキーのDB保存（Fernet暗号化）、共有パスワード + Fernetトークン認証 |

### エントリーポイント

- Backend: `backend/main.py`（FastAPI。lifespan で DB 初期化 + APScheduler 起動。`require_auth` を全ルーターに適用、`/api/auth/*` と `/health` のみ公開）
- Frontend: `frontend/src/app/*/page.tsx`（Next.js 14 App Router）。API クライアントは `frontend/src/lib/api.ts` に集約。

### データフロー

routers → services → `services/anthropic_client.py`（Anthropic/OpenAI ラッパー、DB設定60秒キャッシュ）/ `services/tavily_client.py` → SQLite (`database.py`, `models/*`) + ChromaDB (`rag/vector_store.py`)。
ストリーミングは SSE：長時間処理（research/debate）は `asyncio.Queue` + BackgroundTasks、それ以外（reports/analysis/documents-ask）は `StreamingResponse` で直接ジェネレーターを返す。

### 外部依存

Anthropic API、OpenAI API（モデルIDが `gpt-`/`o1`… の場合のみ）、Tavily、Gmail SMTP、YouTube oEmbed / youtube-transcript-api、ChromaDB（ローカル永続）、sentence-transformers（ローカル埋め込み・リランク）。

### 現在の検証コマンド

```bash
# セットアップ（venv/node_modules はリポジトリに含まれない）
cd backend && python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cd frontend && npm install

# オフラインテスト（API不要・必ず実行できる）
cd backend && ./venv/bin/python -m tests.test_reminder_tools

# 型/リント/ビルド（フロントエンド）
cd frontend && npx tsc --noEmit && npm run lint && npm run build

# Evals（ANTHROPIC_API_KEY 必須・API課金が発生する）
cd backend && python -m evals.eval_research_queries
python -m evals.eval_synthesis_quality
python -m evals.eval_prompt_versions
python -m evals.eval_being_specific
```

**CLAUDE.md のコミットルール**: eval ファイルや service ファイル（`research_agent.py`, `risk_analyzer.py` 等）を編集したら、コミット前に関連 eval を実行すること。本指示書は eval 実行を避けるため、**プロンプトビルダー関数と eval ファイルには触れない**方針にしている（§4 参照）。

---

## 3. Behaviors To Preserve（絶対に壊してはいけない挙動）

1. **SSE イベントのワイヤーフォーマット**。フロントエンドはイベント名とフィールド名に依存している：
   - research: `status`, `queries`, `sources_found`, `source_processed`, `synthesis_token`, `complete`, `error`, `heartbeat`
   - documents/ask: `start`, `tool`（`name`/`query`/`input` フィールド）, `token`, `complete`（`citations`）
   - reports/analysis: `section_start`, `token`, `section_end`, `scores`, `complete`, `error`
   - debate: `round_start`, `persona_start`, `token`, `persona_end`, `round_end`, `synthesis_start`, `complete`, `error`
2. **ストリーム終端判定は文字列マッチ**：`routers/research.py:59` と `routers/debate.py` は
   `'"event_type": "complete"' in event` で終了を判定する。`services/anthropic_client.py:sse_event` の
   `json.dumps` デフォルト区切り（`": "` のスペース）が**この判定の前提**。JSONシリアライズ方法を変えると
   ストリームが終了しなくなる。
3. **プロンプト文言はバイト単位で不変**。`build_decomposition_prompt` / `build_source_summary_prompt`
   （`services/research_agent.py`）は evals が本番プロンプトそのものをテストしている。
   `UNTRUSTED_CONTENT_GUARD` を **system 側に置く**設計も意図的（コメント参照）。文言・結合順を変えない。
4. **`cache_control={"type": "ephemeral"}` をトップレベルで渡すのは正しいAPI使用法**
   （`anthropic_client.py:245,309`）。「content block に付けるものだから」と“修正”しないこと。
5. **認証仕様**: `APP_PASSWORD` 未設定なら認証無効（ローカル開発用）。`/api/auth/*` と `/health` は常時公開。
6. **秘密情報の暗号化**: `EncryptedString` は `enc:v1:` プレフィックス付きで保存、レガシー平文はそのまま読めて
   起動時に `encrypt_legacy_secrets()` で暗号化される。この後方互換を壊さない。
7. **マスク仕様**: settings/digest の GET はキーを `***` で返す。digest の PUT は `***` を無視して既存値を保持。
   settings の PUT は「空文字なら既存維持」。フロントエンドは未変更時にキーを送らない。
8. **DBスキーマとカラム名**（`metadata`, `risk_scores`, `citations` 等のエイリアス含む）。マイグレーション機構は
   `Base.metadata.create_all` のみなので、カラム変更＝既存データ破壊になる。
9. **API パス**は `frontend/src/lib/api.ts` にある全エンドポイント。
10. **モデル設定の解決順**: DB (`ModelSettings`) → `.env` フォールバック、60秒TTLキャッシュ、
    `invalidate_ai_settings_cache()` による無効化。
11. **App UI は英語のみ**（CLAUDE.md）。フロントエンドに日本語を追加しない。

---

## 4. Non-Negotiables（作業上の制約）

- 最初に `git status` を確認し、既存の未コミット変更と自分の変更を混ぜない。
- 編集前に baseline（§6 のコマンド結果）を記録する。
- 変更は小さく、戻しやすい単位でコミットする（1コミット＝1論点）。
- 無関係な整形・ついでのリファクタリングをしない。既存挙動を勝手に変えない。
- 正しさが不明な場合は実装を止めて質問する（§5）。
- 各フェーズ終了ごとに §9 の検証を実行する。
- **プロンプト文言（system/user プロンプト、ツール description）と `backend/evals/` は変更禁止**。
  これにより CLAUDE.md の「eval 実行してからコミット」ルールに抵触せず、API課金なしで作業できる。
  ただし `research_agent.py` 等のサービスファイルの**プロンプト以外の箇所**を変更した場合、
  厳密には CLAUDE.md は関連 eval の実行を求めている — APIキーが使えない環境では、
  その旨を最終報告に明記し、人間に eval 実行を依頼すること。
- 新しい依存パッケージを追加しない（pytest 含む）。テストは既存の
  `tests/test_reminder_tools.py` と同じ「素の assert + 自前ランナー」形式で書く。
- コミットメッセージは変更内容を具体的に。CLAUDE.md のコミットルールに従う。

---

## 5. Stop And Ask Conditions（実装を止めて質問する条件）

以下に該当したら、勝手に決めずに人間へ質問すること：

1. DBスキーマ・保存データ・公開APIレスポンス形状に影響する変更全般。
2. テストと実装が矛盾していると気づいた場合。
3. 削除候補コードが本当に不要か確信できない場合。

---

## 6. Baseline Commands

作業開始時に以下を実行し、結果を記録すること（失敗したものも記録する）：

```bash
git status && git log --oneline -3
cd backend && python -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m tests.test_reminder_tools           # 期待: 16/16 passed
./venv/bin/python -m compileall -q . -x 'venv'           # 構文チェック
cd ../frontend && npm install && npx tsc --noEmit && npm run lint && npm run build
```

注意: `sentence-transformers`/`chromadb` のインストールは重い。ネットワーク制限でインストール不能な場合は
その旨を記録し、`compileall` と個別モジュールの import 可能性チェックに切り替えること。

---

## 7. Debt Map

各項目: **根拠 / なぜ負債か / 影響範囲 / リスク / 改善案 / 検証 / 実装可否**

### A. 重複（実装してよい）

**A-1. Markdown→プレーンテキスト変換と PDF エクスポートの重複 →【対応済み・2026-07-01】**
- 対応: `utils/export.py` に `markdown_to_plain` / `render_pdf` を集約（reports 版に統一。analysis の txt 出力で箇条書きが `- ` 正規化される微小変化あり）。テスト: `tests/test_export.py`。
- 根拠: `routers/reports.py:135-166` と `routers/analysis.py:52-104` に `_markdown_to_plain` と PDF生成コードがほぼ同一で存在。
- なぜ: 片方だけ直すと乖離する。既に乖離している（reports 版には箇条書き規則 `^[-*]\s+` があり、analysis 版にはない）。
- 影響: エクスポート機能のみ。リスク: 低。
- 改善案: `backend/utils/export.py` を新設し、`markdown_to_plain(text)` と `render_pdf(title, content) -> bytes` を移動。両ルーターから呼ぶ。**正規表現は現状のまま2種を統合せず**、reports 版（スーパーセット）に統一してよいが、analysis の txt 出力が箇条書き行で `- ` 正規化されるという小さな出力変化が起きることを報告に明記する。変化を避けたい場合は挙動フラグではなく reports 版に一本化で構わない（人間確認済み扱いにせず報告に書く）。
- 検証: 新設ユニットテスト（見出し・太字・リンク・箇条書きの変換）+ 手動でエクスポートAPIを叩ければ確認。
- 実装可: **可**。

**A-2. SSE キュー配管の重複（research / debate ルーター） →【対応済み・2026-07-01】**
- 対応: `utils/sse.py:queue_event_stream`（タイムアウト60/120秒維持）。B-2 と同時実施。テスト: `tests/test_sse.py`。
- 根拠: `routers/research.py:16-64` と `routers/debate.py:17-84` — `_sse_queues` dict、`event_generator`（タイムアウト60秒 vs 120秒、heartbeat、終端文字列判定）がコピー。
- なぜ: 終端判定という壊れやすい契約が2箇所に散在。
- 改善案: `backend/utils/sse.py`（または `services/sse.py`）に `queue_event_stream(queue, timeout_seconds)` ジェネレーターを抽出。タイムアウト値は引数で維持（60/120 を変えない）。終端判定文字列も現状のまま移動。dict 自体は各ルーターに残してよい（グローバル共有にしない）。
- 検証: sse_event 形式と終端判定のユニットテスト。研究/討論ストリームの手動確認ができない環境では、ジェネレーターに fake queue を渡すテストを書く。
- 実装可: **可**。

**A-3. Web ソースのインデックス処理がルーターに重複実装 →【両段階とも対応済み・2026-07-01・人間の承認あり】**
- 根拠: `routers/research.py` の `_index_web_source` が「チャンク→埋め込み→Chroma→DB」フローを
  独自の800文字段落チャンク（`rag/chunker.py` を使わない）で再実装していた。
- 決定: 第1段階＋第2段階（`chunk_plain_text` 統一 + 短文フォールバック）を一括採用・実装済み。
  - `services/rag_service.py:index_web_content(doc_id, content, db)` を新設。標準チャンカーを使用し、
    チャンカーが0件を返す短文（要約・スニペットのみのソース）は**全文1チャンク**として保存する
    フォールバック付き。空コンテンツは従来どおり「チャンク0件で indexed」（エラーにしない）。
  - ルーターの `_index_web_source` は DB セッション管理のみの薄いラッパーに（`documents.py` の
    `_index_document` と同型）。未使用になった import（`datetime`, `DocumentChunk`）も削除。
  - Web チャンクにも `page_number=1`・検出済み `section_header`・Chroma metadata の `chunk_index`
    が付くようになり、引用表示（`p.0, sec:` 空）の不自然さと読書順ソートの前提が改善。
- 既存データ: 移行なし（新規保存分のみ新方式）。粒度混在が気になるドキュメントは削除→再保存で解消可。
- テスト: `tests/test_index_web_content.py`（長文=複数チャンク＋metadata検証、短文フォールバック、
  空コンテンツ、埋め込み失敗→error。chromadb 等はスタブ）。

**A-4. `_mask` の重複とマスク・センチネル処理の非対称 →【対応済み・2026-07-01】**
- 対応: `utils/masking.py` に集約、settings PUT に `!= MASK` ガード追加（防御的・挙動互換）。テスト: `tests/test_masking_settings.py`。
- 根拠: `routers/settings.py:33` と `routers/digest.py:33-38`。digest は PUT で `body.smtp_password != MASK` をチェックするが、settings の PUT にはこのガードがない（現状はフロントエンドが `***` を送らないため実害なし — `frontend/src/app/settings/page.tsx:42-45` 参照）。
- 改善案: `utils/masking.py` 等に `mask_secret()` と `MASK = "***"` を集約。settings の PUT にも `body.anthropic_api_key != MASK` ガードを追加（防御的・挙動互換：現状のクライアントでは到達しない分岐）。
- 検証: ユニットテスト（`***` を送ってもキーが上書きされないこと）。
- 実装可: **可**。

**A-5. 「prefill + フェンス除去 + json.loads」パターンの重複 →【対応済み・2026-07-01】**
- 対応: `anthropic_client.generate_json` に集約。プロンプト・temperature・フォールバック不変。テスト: `tests/test_generate_json.py` / `tests/test_risk_analyzer.py`。
- eval 実行済み（2026-07-01・人間がローカルで実行）: `eval_research_queries` — code 10.0/10、combined 8.2/10、pass 8/8（≥8）。JSONパース失敗ゼロ＝構造化出力パイプラインの回帰なしを確認。
- `--compare-examples`（本番プロンプト v3 含む A/B）も実行済み: v2 8.2 / v3 8.2（delta +0.0）、
  v3 も code 10.0/10・パース失敗ゼロ。pass 7/8 の1件は品質採点（モデル採点 5/10）による
  もので構造要因ではない。例示の有無でスコア差なしという計測結果も記録しておく
  （プロンプト変更の判断材料。変更する場合は eval 駆動で別途承認を得ること）。
- 根拠: `services/research_agent.py:139-147`、`services/risk_analyzer.py:120-128`（evals にも同パターンがあるが evals は触らない）。
- 改善案: `services/anthropic_client.py` に `generate_json(prompt, *, system="", temperature=0.0, model=None)` を追加し、`prefill="```json"` / `stop_sequences=["```"]` / フェンス除去 / `json.loads` をまとめる。**呼び出し側のプロンプト文字列・temperature は一切変えない**。research_agent 側の `except Exception: sub_queries=[query]`、risk_analyzer 側の `except: pass` というフォールバック挙動もそのまま維持する（例外はヘルパーから素通しにする）。
- 検証: ヘルパーのユニットテスト（`generate_text` をモンキーパッチしてフェンス除去を検証）。
- 実装可: **可**（サービスファイル編集につき §4 の eval 注記を報告に含める）。

**A-6. フロントエンドの SSE パーサが3重実装 →【対応済み・2026-07-01】**
- 対応: `api.ts:consumeSseStream` に集約。パーサ状態をチャンク境界をまたいで保持する副次修正あり。検証: tsc / eslint / next build（ブラウザでの手動確認は本環境では未実施）。
- 根拠: `frontend/src/lib/api.ts:264-319` `postStream`、`frontend/src/app/research/page.tsx:60-120` のインライン実装、`frontend/src/app/debate/page.tsx:83-` のインライン実装。3つとも同じ行パースロジック。
- 改善案: `api.ts` にレスポンスボディを受け取る `consumeSseStream(body, onEvent)` を抽出し、`postStream` と research/debate ページの GET ストリームから共用。**イベントハンドリング（setState 等）はページ側に残す**。
- 検証: `npx tsc --noEmit` + `npm run build`。可能ならブラウザで research / debate の実行確認（`/verify` 相当の手動確認）。不可能なら報告に「未実行」と明記。
- 実装可: **可**。

**A-7. `_strip_metadata` の同名別処理 →【対応済み・2026-07-01】**
- 対応: `_strip_scores_json_lines` / `_strip_duplicate_heading` にリネーム（統合せず）。
- 根拠: `services/report_generator.py:234`（SCORES_JSON 行の除去）と `services/risk_analyzer.py:19`（重複セクション見出しの除去）。同名だが意味が違う。
- 改善案: 統合しない。それぞれ `_strip_scores_json_lines` / `_strip_duplicate_heading` 等に**リネームのみ**。
- 実装可: **可**（低優先）。

### B. 契約・正しさの曖昧さ（質問または慎重に）

**B-1. Retriever の並べ替えがコメントと矛盾 →【対応済み・2026-07-01・人間の承認あり】**
- 根拠: `rag/retriever.py` の最終ソートが `top.sort(key=lambda c: c.chunk_id)` だったが、
  `chunk_id` は `uuid4` 文字列（`rag_service.py:52`）のため実際は**ランダム順**で、
  クロスエンコーダのリランク順も破棄していた。
- 決定: 改善案 (b)（真の読書順）を採用・実装済み。
  - `rag/vector_store.py`: `RetrievedChunk` に `chunk_index` フィールドを追加
    （Chroma metadata に既存の値を `query()` で読むだけ。デフォルト 0）。
  - `rag/retriever.py`: `(doc_id, chunk_index)` でソート。選抜（top_k）はリランク順のまま、
    最終的な提示順のみ読書順に変更。コメントも実態に一致させた。
  - テスト: `tests/test_retriever_order.py`（リランク経路・フォールバック経路・空入力。
    chromadb / sentence-transformers はスタブ化しており重い依存なしで実行可能）。
- 関連メモ →【対応済み・2026-07-01】: `CrossEncoder` の毎クエリ再ロードを解消。
  `embedding_service.py` と同じ `@lru_cache(maxsize=1)` パターンの `_load_reranker()` に集約
  （ロード失敗は例外のためキャッシュされず、次クエリで再試行される）。
  テスト: `tests/test_retriever_order.py` にキャッシュ再利用・失敗非キャッシュの2テストを追加。

**B-2. SSE 終端判定が JSON 直列化の空白に依存 →【対応済み・2026-07-01】**
- 対応: イベント名（complete/error）ベースの判定に変更（ワイヤー不変）。research のエラーイベントに event_type が無くストリームが永久にハートビートし続けるバグも同時に解消。テスト: `tests/test_sse.py`。
- 根拠: §3-2 の通り。`sse_event` の `json.dumps(data)` が区切り `": "` を出す前提で `'"event_type": "complete"'` を substring 検索。
- 改善案: ワイヤーフォーマットは変えず、判定を頑健化する。例: A-2 のヘルパー内で `data:` 行を `json.loads` して `event_type` を見る、またはキューに `None` センチネルを積んで終端にする（プロデューサー側 `_run_research`/`run_debate` の `finally` で put）。**イベント名・ペイロードは変更しない**。
- 検証: ユニットテスト（complete/error/センチネルで終了、それ以外で継続）。
- 実装可: **可**（A-2 と同時に実施するのが安全）。

**B-3. Report status の語彙が不統一 →【対応済み・2026-07-01】**
- 根拠: `report_generator.py` が `"complete"` を保存する一方、PATCH 許可リストとフロントエンドは
  `draft|in_review|pre_approval|completed` を使用していた。
- 決定: 人間の承認により**案A（`completed` に正準化）**を採用・実装済み。
  - `services/report_generator.py` の2箇所（セクション方式・単一パス方式）を `"completed"` に変更。
  - `database.py:normalize_legacy_report_status()` を新設し `init_db()` から呼び出し
    （起動時冪等マイグレーション: `UPDATE reports SET status='completed' WHERE status='complete'`）。
  - `models/report.py` のコメントと `frontend/src/lib/types.ts` の型を実態に合わせて修正
    （幽霊値 `complete`/`archived` を型から削除）。
  - テスト: `tests/test_report_status.py`（生成2経路 + マイグレーションの書き換え・冪等性）。
- **PATCH 挙動も対応済み（2026-07-01・人間の承認あり）**: `routers/reports.py:update_report` は
  許可リスト外の status を 400 で拒否する（`ALLOWED_REPORT_STATUSES` 定数）。検証は他フィールド
  適用前に行うため、不正 status を含むリクエストは title/content も含め一切適用されない。
  テスト: `tests/test_report_status.py` の PATCH 3テスト。

**B-4. コメント/ドキュメントのドリフト →【対応済み・2026-07-01】**
- 対応: `models/document.py` の source_type コメントと `research_agent.py` docstring を実態に修正（CLAUDE.md は不変更）。
- 根拠: `models/document.py` の `source_type` コメント `'upload'|'scraped'`（実際は `upload|url|youtube|web`）。`services/research_agent.py:7` の「claude-haiku-3-5」（実際の fast model 既定は `claude-haiku-4-5-20251001` — `config.py:26`）。CLAUDE.md にも同じ記述があるが **CLAUDE.md はユーザー管理ファイルなので変更しない**（報告で指摘のみ）。
- 改善案: コード内コメント2箇所を実態に合わせて修正。
- 実装可: **可**（コメントのみ）。

### C. パフォーマンス・ブロッキング（一部実装可）

**C-1. CrossEncoder をクエリ毎にロード →【対応済み・2026-07-01】**
- 対応: `rag/retriever.py` に `@lru_cache(maxsize=1)` の `_load_reranker()` を新設
  （B-1 の関連メモと同一の対応。ロード失敗はキャッシュされず次クエリで再試行、
  フォールバック挙動は維持）。テスト: `tests/test_retriever_order.py`。

**C-2. ドキュメントタイトルの N+1 クエリ →【対応済み・2026-07-01】**
- 対応: `in_` による一括取得＋dict 引き。出力文字列は不変。テスト: `tests/test_rag_answer.py`。
- 根拠: `services/rag_service.py:157-159` — ツール実行のたびにチャンク毎 `db.query(Document)`。
- 改善案: chunk の `doc_id` を集めて `in_` で一括取得し dict 引き。出力文字列は不変に保つ。
- 実装可: **可**。

**C-3. async ハンドラ内の同期ブロッキング（DB / 埋め込み / リランク / SMTP以外）**
- 根拠: `run_research_agent` 等が同期 SQLAlchemy・sentence-transformers を async 関数内で直接呼ぶ。単一ワーカーのイベントループを長時間塞ぐ。
- 改善案: `asyncio.to_thread` への退避。ただし影響範囲が広く、SSE の順序性・DBセッションのスレッド境界に副作用があり得る。
- 実装可否: **提案のみ**（本リファクタでは着手しない）。

**C-4. ループ内の逐次 `db.commit()`**
- 根拠: `services/research_agent.py:217-232` — 検索結果1件ごとに commit。
- 改善案: ループ後に一括 commit。ただし途中失敗時に部分保存されなくなるという挙動変化があるため、**低優先・任意**。実施する場合は報告に挙動変化を明記。
- 実装可: 可（任意）。

### D. テスト不足（実装してよい — Phase 2 の中身）

**D-1. オフラインテストの空白地帯 →【対応済み・2026-07-01】**
- 対応: `tests/` は16ファイル・92テストに拡充（chunker / word limits / secret_crypto / auth / sse / export / masking / generate_json / risk_analyzer / rag_answer / folder_ops ほか）。全てオフライン実行可能。
- 根拠: `backend/tests/` には `test_reminder_tools.py` のみ。evals は API キー必須・課金あり。チャンク、語数制限抽出、暗号化、認証トークン、markdown変換、SSE 形式にテストがない。
- 改善案: 既存スタイル（素の assert + `__main__` ランナー、in-memory SQLite）で以下を追加：
  - `tests/test_chunker.py` — `chunk_text`（見出し検出、MIN/MAX トークン境界、オーバーラップ）、`chunk_plain_text`
  - `tests/test_export.py` — A-1 で抽出した `markdown_to_plain`
  - `tests/test_report_word_limits.py` — `_extract_word_limit`（英語・日本語パターン）、`_calculate_word_budgets`
  - `tests/test_secret_crypto.py` — encrypt/decrypt 往復、`enc:v1:` 冪等性、平文パススルー
  - `tests/test_auth.py` — `create_token`/`verify_token`（TTL失効は Fernet の `ttl` 引数を短くして検証）、`check_password`
  - `tests/test_sse.py` — `sse_event` の形式、A-2/B-2 のストリームヘルパー終端
- 注意: テストが import する経路で `config.get_settings()` が走るため、既存テスト同様 `DATABASE_URL` 等を先に環境変数で設定する。sentence-transformers を import しない構成にする（chunker は依存しない）。
- 実装可: **可**。リファクタ対象に触る**前**に書く。

### E. その他（提案のみ／質問）

**E-1. PDF フォントが macOS 専用**
- 根拠: `utils/pdf_renderer.py:10` — ヒラギノの絶対パス。Linux（Render 本番）では Helvetica にフォールバックし、日本語を含むレポートの PDF が文字化けする。
- 実装可否: **提案のみ**（フォント同梱はライセンス・サイズのプロダクト判断が必要）。

**E-2. Digest メールの見出しが日本語 →【対応済み・2026-07-01・人間の承認あり】**
- 根拠: `services/digest_service.py` — 見出し生成プロンプトが日本語で日本語出力を要求、
  メール HTML も `lang="ja"` だった。CLAUDE.md の「App UI は英語のみ」に対しメールが
  「UI」に含まれるか不明だったため質問 → **メールも英語とする**と人間が決定。
- 対応: 見出しプロンプトを英語（英語出力を要求）に変更、`lang="en"` に修正、
  モジュール docstring も更新。
- テスト: `tests/test_digest_service.py`（プロンプトが英語で日本語を含まない・
  エラー時のスニペットフォールバック・HTML が英語かつエスケープ済み）。

**E-3. マイグレーション機構がない**
- 根拠: `database.py:init_db` は `create_all` のみ + 手書きの `encrypt_legacy_secrets`。
- 実装可否: **提案のみ**（Alembic 導入は依存追加かつ本リファクタの範囲外）。

**E-4. 広すぎる例外の握りつぶし →【対応済み・2026-07-01】**
- 対応: 継続挙動は維持しつつ logger.warning を追加（documents 削除・rename_folder・digest 再スケジュール・risk_analyzer スコア抽出）。テスト: `tests/test_folder_ops.py`。
- 根拠: `routers/documents.py:340-345`（Chroma削除失敗を無視）、`rename_folder` の `except Exception: pass`、`routers/digest.py:reschedule_digest` の `except Exception: pass`、`services/risk_analyzer.py:130` の冗長な `except (json.JSONDecodeError, Exception)`。
- 改善案: **挙動（握りつぶして続行）は維持**しつつ、`logger.exception(...)` / `logger.warning(...)` を追加。risk_analyzer は `except Exception:` に簡約。
- 実装可: **可**。

**E-5. `datetime.utcnow()` の全面使用（Python 3.12 で非推奨）**
- 根拠: models のデフォルト値、各サービス。
- 実装可否: **提案のみ**。機械的置換でも naive/aware の混入リスクがあり、本リファクタの優先度は低い。

---

## 8. Implementation Phases

各フェーズの末尾で §9 を実行し、コミットしてから次へ進む。

1. **Phase 0 — Baseline**: `git status` 確認、§6 実行、結果を記録。
2. **Phase 1 — 安全網**: D-1 のテスト群のうち、以後のフェーズで触るモジュール分
   （export/word-limits/secret_crypto/auth/sse/chunker）を先に追加。全 green を確認。
3. **Phase 2 — 明白に安全な整理**:
   B-4（コメント修正）、E-4（ログ追加・冗長 except 簡約）、C-1（CrossEncoder キャッシュ）、
   C-2（N+1 解消）、A-7（リネーム）、A-4（`_mask` 集約 + settings ガード）。
4. **Phase 3 — 重複統合**: A-1（export 抽出）、A-5（`generate_json` ヘルパー）、
   A-6（フロント SSE パーサ共通化）。
5. **Phase 4 — 責務分離と境界**: A-2 + B-2（SSE ストリームヘルパー抽出と終端判定の頑健化、
   ワイヤー不変）。※ A-3 は両段階とも対応済み（§7 A-3 参照）。
6. **Phase 5 — 提案のみ（実装しない）**: C-3, E-1, E-3, E-5 を
   最終報告に「承認待ち提案」としてまとめる。承認なしに実装しない。

---

## 9. Verification Requirements

- 毎フェーズ: `./venv/bin/python -m compileall -q backend -x venv` 相当の構文チェック、
  `python -m tests.test_reminder_tools` を含む**全テストランナー**の実行（新設分含む）。
- フロントエンドを触ったフェーズ: `npx tsc --noEmit && npm run lint && npm run build`。
- 可能な環境なら（APIキーがあれば）: 起動確認 `uvicorn main:app --port 8000` + `/health`、
  research/debate/reports の SSE を1回ずつ手動実行。不可能なら未実行と報告。
- サービスファイル（`research_agent.py`, `rag_service.py`, `risk_analyzer.py`, `anthropic_client.py`）を
  編集した場合: CLAUDE.md により関連 eval の実行が求められる。APIキーが使えない場合は
  最終報告に「eval 未実行（要人間実行）: <コマンド>」と明記する。

---

## 10. Reporting Format

最終報告には以下を含めること：

1. **実行したコマンドと結果**（baseline と各フェーズの検証、pass/fail 件数）
2. フェーズ別の変更一覧（ファイル / 何を / なぜ / 挙動変化の有無）
3. 意図的に**変えなかった**もの（§3 の保護対象に触れそうで回避した箇所）
4. 未実行の検証（eval、手動SSE確認など）と、その理由・人間への依頼事項
5. 「承認待ち提案」一覧（§8 Phase 5）— 各提案に根拠ファイル:行番号を付ける
6. 作業中に新たに発見した問題（修正はせず報告のみ）

---

## 11. Out-of-scope Items（今回やらないこと）

- 機能追加・UI 変更・プロンプト改善（プロンプトの文言変更は全面禁止）
- DB スキーマ変更、Alembic 導入、保存データのマイグレーション
- async 化・並行処理の再設計（C-3）
- LangChain 等の導入（CLAUDE.md で禁止）、依存パッケージの追加・更新
- `_sse_queues` のマルチワーカー対応（現状は単一ワーカー前提。制約としてコメントに残すのは可）
- evals の変更・実行基盤の変更
- CLAUDE.md / README.md の書き換え（ドリフトは報告のみ）
- Fable 5 等への モデルID変更（現行 `claude-opus-4-6` / `claude-haiku-4-5-20251001` は有効なIDであり変更不要）

---

## 12. V2 Debt Map（2026-07-02 追加分析）

第1ラウンド（§7、全実装可項目対応済み）のマージ後に行った再分析で見つかった追加項目。
§3〜§5 の保護対象・制約・停止条件はすべて本セクションにも適用される。
特に **プロンプト文言のバイト単位不変**（§3-3）と **SSE ワイヤーフォーマット不変**（§3-1）。

### F. バックエンド（新規）

**F-1. `index_document` と `index_web_content` の残存重複 →【対応済み・2026-07-02】**
- 対応: `_embed_and_store(doc, chunks, db, *, page_count=None, word_count=None)` を新設し
  （word_count もキーワード引数として追加 — 両呼び出し元とも doc.word_count を設定するため必要。
  改善案の提案シグネチャに1引数追加した以外は提案どおり）、両関数から共通の「埋め込み→chunk_id
  採番→DocumentChunk構築→vs.add_chunks（同一metadata dict）→bulk_save_objects→doc.status更新」を
  そこに集約。例外処理は提案どおりヘルパーの外に残した: `index_document` は
  空チャンク/例外時に `status="error"` + re-raise、`index_web_content` は空チャンクなら
  `"indexed"`（0件, エラーにしない）・例外は握りつぶして `status="error"`。ヘルパー自身は
  例外を一切キャッチしない。
- テスト: 新設 `tests/test_index_document.py`（4テスト: txt アップロードの通常経路、空チャンクは
  index_web_content と異なりエラーになること、embedding失敗時に re-raise されること
  ＝index_web_content との挙動差の固定、doc不在/file_path無しの no-op）。既存
  `tests/test_index_web_content.py` は無変更のまま green。
- 根拠: `services/rag_service.py:16-92` と `:95-177`。「埋め込み生成 → chunk_id 採番 →
  `DocumentChunk` 構築 → `vs.add_chunks`（同一 metadata dict）→ `bulk_save_objects` →
  doc.status 更新」の約50行がほぼ同一（A-3 で `index_web_content` を新設した際に生まれた重複）。
- なぜ: metadata スキーマ（`doc_id`/`page_number`/`section_header`/`chunk_index`）という
  Chroma との契約が2箇所に散在。片方だけ直すと検索側（`vector_store.query`）と乖離する。
- 影響: インデックス処理のみ。リスク: 低〜中。
- 改善案: `_embed_and_store(doc, chunks, db, *, page_count=None)` を抽出して両者から呼ぶ。
  **例外時の挙動差は維持する**: `index_document` は `status="error"` にして re-raise、
  `index_web_content` は握りつぶして `status="error"`（バックグラウンドタスクのため）。
  この差はヘルパーの外側（呼び出し側の try/except）に残すこと。
- 検証: 既存 `tests/test_index_web_content.py` が green のまま + upload 経路の同型テストを追加。
- 実装可: **可**。

**F-2. `list_documents` のチャンク数 N+1 →【対応済み・2026-07-02】**
- 対応: `routers/documents.py:list_documents` を `func.count(DocumentChunk.id)` +
  `group_by(DocumentChunk.document_id)` の一括クエリ + dict 引きに変更（`sqlalchemy.func` を import）。
  レスポンス形状・キーは不変（`chunk_count` の既定値も従来どおり 0）。
- テスト: `tests/test_folder_ops.py::test_list_documents_chunk_counts_batched` — 0件/複数件の
  chunk_count が正しいこと、かつ `before_cursor_execute` フックでクエリ件数を数え、
  ドキュメント数に関わらず**常に2クエリ**（documents 1 + 集計1）であることを固定（N+1 の回帰検知）。
- 根拠: `routers/documents.py:276-288` — ドキュメント毎に `count()` クエリ（100文書=101クエリ）。
- 改善案: `func.count` + `group_by(DocumentChunk.document_id)` の一括クエリで dict を作り引く。
  レスポンス形状は不変。
- 検証: レスポンス JSON が変更前後で一致すること（既存テストスタイルで in-memory SQLite）。
- 実装可: **可**（C-2 と同型の修正）。

**F-3. `sse_event` の置き場所と生文字列エラーイベント →【対応済み・2026-07-02】**
- 対応: `sse_event` を `utils/sse.py` に移動。`services/anthropic_client.py` は
  `from utils.sse import sse_event`（re-export、既存の `from services.anthropic_client import
  sse_event` 呼び出し元 — `rag_service.py`/`report_generator.py`/`risk_analyzer.py`/
  `research_agent.py`/`debate_service.py`/`tests/test_sse.py` — は無変更で動作継続）。
  手組みの生文字列エラーイベント4箇所（`routers/research.py:53,167`、`routers/debate.py:71,117`、
  `services/debate_service.py:168`）を `sse_event("error", {...})` 呼び出しに置換。ペイロードの
  キー・順序は完全維持（`debate.py:117` の `{"message":…, "event_type":"error"}` の2キー順序も含む）
  — 置換前後で生成される文字列が byte-for-byte 一致することを手動 diff で確認済み。
- テスト: `tests/test_sse.py` に `test_anthropic_client_reexport_is_same_function_object`
  （`services.anthropic_client.sse_event is utils.sse.sse_event` を固定）を追加。既存の
  フォーマット・終端判定テストは無変更のまま green。
- 根拠: `sse_event` が `services/anthropic_client.py:379-381` にある（SSE 整形は Anthropic と無関係）。
  さらに `routers/research.py:53,167`・`routers/debate.py:71,117`・`services/debate_service.py:168` は
  `f"event: error\ndata: {json.dumps(...)}"` を手組みしており、ペイロードのばらつき
  （`event_type` 有無）もある。
- なぜ: B-2 でイベント名ベースの終端判定に統一済みなので、整形も1箇所に寄せるのが自然。
- 改善案: `sse_event` を `utils/sse.py` へ移動（`anthropic_client` からは re-export して
  既存 import を壊さない、または全 import を書き換え）。手組み箇所を `sse_event("error", {...})` に
  置換。**ペイロードのキーは現状のまま変えない**（`event_type` を勝手に足したり消したりしない）。
- 検証: `tests/test_sse.py` に整形の同値テスト追加。終端判定（イベント名ベース）が引き続き機能すること。
- 実装可: **可**。

**F-4. `answer_question` の system プロンプト制約文の二重管理**
- 根拠: `services/rag_service.py:258-286` — `default_system` と `custom_system` 分岐が
  ほぼ同文の制約（search_documents 必須・引用形式・リマインダー手順）を別々に持つ。
- なぜ: 片方だけ直すと挙動が分岐する（実際すでに微妙に文言が違う）。
- 改善案: **結合後の文字列がバイト単位で不変になる場合のみ**共有定数へ抽出。
  1文字でも変わるなら実装せず「提案のみ」として報告（§3-3 のプロンプト凍結が優先）。
- 実装可: **条件付き可**（バイト同一を diff で証明できた場合のみ）。

**F-5. `assign_folder` が `metadata_json` を全上書き →【対応済み・2026-07-02】**
- 対応: 既存 `metadata_json` を `json.loads` して dict であればマージ（`collection_id`/
  `collection_name` のみ上書き、他キーは保持）。パース失敗時・非dict時は空dictから開始し
  従来どおり2キーのみで上書き（挙動不変）＋ `logger.warning` を追加（E-4 と同じ「握りつぶして
  続行」パターン）。
- テスト: `tests/test_folder_ops.py::test_assign_folder_merges_existing_metadata`（他キー保持）、
  `::test_assign_folder_overwrites_malformed_metadata`（不正JSONは従来どおり2キーで上書き）。
- 根拠: `routers/documents.py:302-315` — 既存 metadata を読まずに collection キーのみで書き潰す。
  現状 metadata に他のキーは存在しないため実害なし（防御的修正）。
- 改善案: 既存 JSON を `json.loads` してマージ（パース失敗時は現行どおり上書き）。
- 検証: 既存 `tests/test_folder_ops.py` に「他キー保持」テストを追加。
- 実装可: **可**（低優先）。

**F-6. ルーターに埋まった取込ヘルパー群（責務分離） →【対応済み・2026-07-02】**
- 対応: `_ip_is_blocked`/`_resolve_public_ip`/`_assert_public_url`/`_safe_fetch_bytes`/
  `_extract_youtube_id`/`_get_youtube_transcript`/`_scrape_url`（+`MAX_SCRAPE_BYTES`/
  `MAX_REDIRECTS`）を `services/ingestion.py` へ移動。関数本体は移動前後で **byte-for-byte
  一致**することを `diff` コマンドで確認済み（末尾に余分な空行が入っただけの抽出ミスを検出して
  修正した上での確認 — 手順は最終報告に記載）。`routers/documents.py` は
  `_extract_youtube_id`/`_get_youtube_transcript`/`_scrape_url` を import するだけになり、
  移動に伴い使われなくなった import（`re`/`ipaddress`/`socket`/`urllib.parse.urlparse`/`asyncio`）
  を削除（`_assert_public_url`/`_ip_is_blocked`/`_resolve_public_ip`/`MAX_SCRAPE_BYTES`/
  `MAX_REDIRECTS` はルーター側では未使用になったため import せず、ingestion.py 内部でのみ使用）。
- 発見（未修正）: `_assert_public_url` はどこからも呼ばれていない（`_resolve_public_ip` を
  直接呼ぶ経路のみが使われている）。削除候補になり得るが§5の停止条件（削除の要不要が不明）に
  該当するため、移動のみ行い削除はしていない — 人間の判断待ち。同様に `routers/documents.py` の
  `from datetime import datetime` も本ファイルでは未使用（F-6以前からの既存の状態、今回の変更が
  作ったものではないため触れていない）。
- テスト: 新設 `tests/test_ingestion.py`（18テスト）— `_extract_youtube_id` の4URL形式+2否定ケース、
  `_ip_is_blocked` のloopback/private/link-local(クラウドmetadata含む)/reserved/multicast/
  unspecified/IPv6/公開IP、`_resolve_public_ip` のスキーム拒否・ホスト無し拒否・
  loopbackホスト名拒否・metadata IPリテラル拒否。すべてオフライン（DNS解決を伴う
  `localhost` テスト以外はネットワーク不要）。
- 根拠: `routers/documents.py:34-183` — SSRF 防御付きフェッチ（`_resolve_public_ip`/`_safe_fetch_bytes`）、
  YouTube 抽出、スクレイピングの約150行がルーターに同居。セキュリティ境界のコードが
  エンドポイント定義と混在し、単体テストもない。
- 改善案: `services/ingestion.py`（または `utils/safe_fetch.py`）へ移動。**ロジックは一切変えない**
  （SSRF 対策は監査済みコード — 挙動変更禁止）。移動後、純粋関数である
  `_extract_youtube_id` と `_ip_is_blocked` にオフラインテストを追加。
- 検証: 移動前後で関数本体の diff が空であること + 新規テスト green。
- 実装可: **可**（移動とテスト追加のみ。文言・ロジック変更は不可）。

### G. フロントエンド（新規）

**G-1. `library/page.tsx` の肥大化（902行・useState 約20個）**
- 根拠: `frontend/src/app/library/page.tsx` — アップロード / URL取込 / フォルダ管理 /
  Q&Aチャット / リマインダーの5責務が1コンポーネントに同居。
- 改善案: 見た目・挙動を一切変えずにコンポーネント分割:
  `components/library/UploadPanel.tsx`（upload+ingest+drag）、`FolderSection.tsx`
  （folders+rename modal）、`ChatPanel.tsx`（Q&A+system prompt+tool indicator）、
  `RemindersPanel.tsx`。state は各パネルへ移せるものだけ移し、共有が必要な
  `docs`/`selectedDocs` は親に残して props で渡す。
- 検証: `npx tsc --noEmit && npm run lint && npm run build` + ブラウザで
  アップロード→チャット→リマインダーの手動確認（不可能なら未実行と報告）。
- 実装可: **可**（機械的な抽出に徹する。JSX の構造・className を変えない）。

**G-2. `api.ts` の `unknown` 戻り型 →【対応済み・2026-07-02】**
- 対応: `api.ts` に `lib/types.ts` の既存型（`ResearchSession`/`Document`/`Report`/`RiskAnalysis`）を
  import し、`research`/`documents`/`reports`/`analysis` の `list`/`get`（+`reports.update`）の
  `request<unknown[]>`/`request<unknown>` を該当の具体型に置換。呼び出し側
  (`analysis/page.tsx`, `analysis/[analysisId]/page.tsx`, `library/page.tsx`, `reports/page.tsx`,
  `reports/[reportId]/page.tsx`) の冗長になった `as RiskAnalysis[]`/`as Document[]`/`as Report[]`/
  `as Report` キャストを削除（元々どれも `lib/types.ts` の正しい型にキャストしており、重複ローカル
  interface は見つからなかった — 削除の必要なし）。`reports.update` の3呼び出し箇所は戻り値を
  破棄しているため無変更。ランタイム挙動は不変（型のみ）。
- 検証: `npx tsc --noEmit` / `npm run lint` / `npm run build` すべてクリーン。
- 発見（未修正・対象外）: `reports/new/page.tsx` は `api.research.list()` を使わず生 `fetch` で
  ローカルの縮小版 `ResearchSession` interface（`topic`/`summary`/`completed_at`/`results` を持たない）
  を独自定義している。G-2 の対象は `api.ts` の戻り型のみのため今回は触れていない。
- 根拠: `frontend/src/lib/api.ts:87-88,115-116,143-144,157-158` — `list`/`get` が
  `unknown[]`/`unknown` を返し、各ページが独自インターフェースでキャストしている。
- 改善案: `lib/types.ts` の既存型（なければページ内定義をここへ昇格）を `request<T>` に指定。
  ページ側の重複ローカル型定義を削除して import に置換。**ランタイム挙動は不変**（型のみ）。
- 検証: `npx tsc --noEmit`（これが本項目の実質的なテスト）+ build。
- 実装可: **可**。G-1/G-3 より**先**に実施すると分割作業が型に守られる。

**G-3. `debate/page.tsx` のエクスポートヘルパー（約120行）**
- 根拠: `frontend/src/app/debate/page.tsx:87-205` — `buildMarkdown`/`buildPlainText`/
  `downloadBlob`/`exportAsPdf` がページ内定義。`components/ui/DownloadMenu.tsx` と役割が近い。
- 改善案: `lib/exportDebate.ts` へ移動（純関数なのでそのまま切り出せる）。
  DownloadMenu との統合は**しない**（挙動差の検証コストに見合わない — 移動のみ）。
- 実装可: **可**（低優先）。

### V2 実施順序

1. **G-2**（型付け — 以後の作業の安全網、変更はコンパイル時のみ）
2. **F-2, F-3, F-5**（小さく安全なバックエンド整理）
3. **F-1**（インデックス統合 — テスト既存）
4. **F-6**（取込ヘルパーの移動 + 新規テスト）
5. **G-1, G-3**（フロント分割 — 最後に。手動確認が必要なため）
6. **F-4** はバイト同一を証明できた場合のみ、任意のタイミングで。

各ステップ後に §9 の検証を実行してからコミットすること。
Phase 5（承認待ち提案 C-3/C-4/E-1/E-3/E-5）の扱いは第1ラウンドから変更なし。
