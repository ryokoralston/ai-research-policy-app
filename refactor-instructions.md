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

1. `rag/retriever.py` の並べ替え（§7 B-1）を「修正」する場合。検索回答の内容が変わる。
2. save-to-library のチャンク方式統一（§7 A-3 の第2段階）。インデックス済みデータとの一貫性に影響。
3. Digest メールの日本語見出し（§7 E-2）を変更しようとする場合 — 仕様かバグか判断できない。
4. DBスキーマ・保存データ・公開APIレスポンス形状に影響する変更全般。
5. テストと実装が矛盾していると気づいた場合。
6. 削除候補コードが本当に不要か確信できない場合。

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

**A-1. Markdown→プレーンテキスト変換と PDF エクスポートの重複**
- 根拠: `routers/reports.py:135-166` と `routers/analysis.py:52-104` に `_markdown_to_plain` と PDF生成コードがほぼ同一で存在。
- なぜ: 片方だけ直すと乖離する。既に乖離している（reports 版には箇条書き規則 `^[-*]\s+` があり、analysis 版にはない）。
- 影響: エクスポート機能のみ。リスク: 低。
- 改善案: `backend/utils/export.py` を新設し、`markdown_to_plain(text)` と `render_pdf(title, content) -> bytes` を移動。両ルーターから呼ぶ。**正規表現は現状のまま2種を統合せず**、reports 版（スーパーセット）に統一してよいが、analysis の txt 出力が箇条書き行で `- ` 正規化されるという小さな出力変化が起きることを報告に明記する。変化を避けたい場合は挙動フラグではなく reports 版に一本化で構わない（人間確認済み扱いにせず報告に書く）。
- 検証: 新設ユニットテスト（見出し・太字・リンク・箇条書きの変換）+ 手動でエクスポートAPIを叩ければ確認。
- 実装可: **可**。

**A-2. SSE キュー配管の重複（research / debate ルーター）**
- 根拠: `routers/research.py:16-64` と `routers/debate.py:17-84` — `_sse_queues` dict、`event_generator`（タイムアウト60秒 vs 120秒、heartbeat、終端文字列判定）がコピー。
- なぜ: 終端判定という壊れやすい契約が2箇所に散在。
- 改善案: `backend/utils/sse.py`（または `services/sse.py`）に `queue_event_stream(queue, timeout_seconds)` ジェネレーターを抽出。タイムアウト値は引数で維持（60/120 を変えない）。終端判定文字列も現状のまま移動。dict 自体は各ルーターに残してよい（グローバル共有にしない）。
- 検証: sse_event 形式と終端判定のユニットテスト。研究/討論ストリームの手動確認ができない環境では、ジェネレーターに fake queue を渡すテストを書く。
- 実装可: **可**。

**A-3. Web ソースのインデックス処理がルーターに重複実装**
- 根拠: `routers/research.py:138-208` `_index_web_source` が、`services/rag_service.py:index_document` と同等の「チャンク→埋め込み→Chroma→DB」フローを、独自の800文字段落チャンク（`rag/chunker.py` を使わない）で再実装。
- なぜ: RAG インデックスの所有者が曖昧。チャンクポリシーが2系統ある。
- 影響: save-to-library 機能、ライブラリ検索品質。
- 改善案（2段階）:
  - 第1段階（**実装可**）: 関数を**挙動そのまま** `services/rag_service.py` へ移動（`index_web_content(doc_id, content, db)`）。ルーターは呼ぶだけにする。チャンクロジックは1行も変えない。
  - 第2段階（**提案のみ・§5-3**）: チャンク化を `chunk_plain_text` に統一する。チャンクサイズ・境界が変わり、以後の検索結果に影響するため人間の承認が必要。
- 検証: 第1段階は移動のみ＝import と compileall、可能なら save-to-library の手動実行。
- 実装可: 第1段階のみ**可**。

**A-4. `_mask` の重複とマスク・センチネル処理の非対称**
- 根拠: `routers/settings.py:33` と `routers/digest.py:33-38`。digest は PUT で `body.smtp_password != MASK` をチェックするが、settings の PUT にはこのガードがない（現状はフロントエンドが `***` を送らないため実害なし — `frontend/src/app/settings/page.tsx:42-45` 参照）。
- 改善案: `utils/masking.py` 等に `mask_secret()` と `MASK = "***"` を集約。settings の PUT にも `body.anthropic_api_key != MASK` ガードを追加（防御的・挙動互換：現状のクライアントでは到達しない分岐）。
- 検証: ユニットテスト（`***` を送ってもキーが上書きされないこと）。
- 実装可: **可**。

**A-5. 「prefill + フェンス除去 + json.loads」パターンの重複**
- 根拠: `services/research_agent.py:139-147`、`services/risk_analyzer.py:120-128`（evals にも同パターンがあるが evals は触らない）。
- 改善案: `services/anthropic_client.py` に `generate_json(prompt, *, system="", temperature=0.0, model=None)` を追加し、`prefill="```json"` / `stop_sequences=["```"]` / フェンス除去 / `json.loads` をまとめる。**呼び出し側のプロンプト文字列・temperature は一切変えない**。research_agent 側の `except Exception: sub_queries=[query]`、risk_analyzer 側の `except: pass` というフォールバック挙動もそのまま維持する（例外はヘルパーから素通しにする）。
- 検証: ヘルパーのユニットテスト（`generate_text` をモンキーパッチしてフェンス除去を検証）。
- 実装可: **可**（サービスファイル編集につき §4 の eval 注記を報告に含める）。

**A-6. フロントエンドの SSE パーサが3重実装**
- 根拠: `frontend/src/lib/api.ts:264-319` `postStream`、`frontend/src/app/research/page.tsx:60-120` のインライン実装、`frontend/src/app/debate/page.tsx:83-` のインライン実装。3つとも同じ行パースロジック。
- 改善案: `api.ts` にレスポンスボディを受け取る `consumeSseStream(body, onEvent)` を抽出し、`postStream` と research/debate ページの GET ストリームから共用。**イベントハンドリング（setState 等）はページ側に残す**。
- 検証: `npx tsc --noEmit` + `npm run build`。可能ならブラウザで research / debate の実行確認（`/verify` 相当の手動確認）。不可能なら報告に「未実行」と明記。
- 実装可: **可**。

**A-7. `_strip_metadata` の同名別処理**
- 根拠: `services/report_generator.py:234`（SCORES_JSON 行の除去）と `services/risk_analyzer.py:19`（重複セクション見出しの除去）。同名だが意味が違う。
- 改善案: 統合しない。それぞれ `_strip_scores_json_lines` / `_strip_duplicate_heading` 等に**リネームのみ**。
- 実装可: **可**（低優先）。

### B. 契約・正しさの曖昧さ（質問または慎重に）

**B-1. Retriever の並べ替えがコメントと矛盾（ランダム順になっている）**
- 根拠: `rag/retriever.py:49-51` — `top.sort(key=lambda c: c.chunk_id)`。コメントは「(doc_id, chroma_id サフィックスの chunk_index) で読書順に戻す」と言うが、`chunk_id` は `uuid4` 文字列（`rag_service.py:52`）なので実際は**ランダム順ソート**であり、クロスエンコーダのランク順も破棄される。
- なぜ: コメント（意図）とコード（実態）が矛盾。回答生成に渡すコンテキストの順序が毎回無意味に決まる。
- 影響: ライブラリ Q&A の回答品質。リスク: 「修正」すると回答内容が変わる＝仕様変更。
- 改善案: 2案 — (a) リランク順を保持（sort 行を削除）、(b) 真の読書順（metadata の `chunk_index` を `RetrievedChunk` に追加し `(doc_id, chunk_index)` でソート）。
- 実装可否: **提案のみ。§5-2 に従い実装前に質問**。
- 検証（承認後）: fake VectorStore を使ったユニットテストで順序を検証。

**B-2. SSE 終端判定が JSON 直列化の空白に依存**
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

**B-4. コメント/ドキュメントのドリフト**
- 根拠: `models/document.py` の `source_type` コメント `'upload'|'scraped'`（実際は `upload|url|youtube|web`）。`services/research_agent.py:7` の「claude-haiku-3-5」（実際の fast model 既定は `claude-haiku-4-5-20251001` — `config.py:26`）。CLAUDE.md にも同じ記述があるが **CLAUDE.md はユーザー管理ファイルなので変更しない**（報告で指摘のみ）。
- 改善案: コード内コメント2箇所を実態に合わせて修正。
- 実装可: **可**（コメントのみ）。

### C. パフォーマンス・ブロッキング（一部実装可）

**C-1. CrossEncoder をクエリ毎にロード**
- 根拠: `rag/retriever.py:38-40` — `CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")` を `retrieve()` の呼び出しごとに生成。モデルロードは重い（初回は数百MBのダウンロード、以後も毎回のデシリアライズ）。
- 改善案: `services/embedding_service.py` の `_load_model` と同じく `functools.lru_cache` でモジュールレベルにキャッシュ。`except Exception` フォールバック（ベクトル順）は維持。
- 検証: 既存の fallback 挙動を含むユニットテスト or import 確認。挙動は同一（同じモデル・同じ入力）。
- 実装可: **可**。

**C-2. ドキュメントタイトルの N+1 クエリ**
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

**D-1. オフラインテストの空白地帯**
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

**E-2. Digest メールの見出しが日本語**
- 根拠: `services/digest_service.py:52-64` — プロンプトも出力も日本語。CLAUDE.md は「App UI は英語のみ」。メールが「UI」に含まれるかは仕様判断。
- 実装可否: **変更しない。質問のみ**（§5-4）。

**E-3. マイグレーション機構がない**
- 根拠: `database.py:init_db` は `create_all` のみ + 手書きの `encrypt_legacy_secrets`。
- 実装可否: **提案のみ**（Alembic 導入は依存追加かつ本リファクタの範囲外）。

**E-4. 広すぎる例外の握りつぶし**
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
5. **Phase 4 — 責務分離と境界**: A-3 第1段階（`_index_web_source` をサービス層へ移動、挙動不変）、
   A-2 + B-2（SSE ストリームヘルパー抽出と終端判定の頑健化、ワイヤー不変）。
6. **Phase 5 — 提案のみ（実装しない）**: B-1, A-3 第2段階, C-3, E-1, E-2, E-3, E-5 を
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
