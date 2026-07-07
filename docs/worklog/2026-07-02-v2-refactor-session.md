# 作業ログ — 2026-07-02 V2 debt map リファクタリングセッション

- **ブランチ**: `claude/v2-debt-map-refactor`（bdb6737 = 前回セッションのマージ済み main から分岐）
- **PR**: (このコミットの後に作成 — URL は最終報告を参照)
- **実施**: Claude Code（実装・テスト）。人間の承認を要する分岐は今回発生せず（F-4 のみ
  「バイト同一を証明できなければスキップ」という事前の条件付き指示どおりスキップ）。
- **対象**: `refactor-instructions.md` §12「V2 Debt Map」の F-1〜F-6、G-1〜G-3
  （F-4 を除く8項目すべて実装、F-4 はスキップ）。

---

## 1. 実施順序（指示書 §12「V2 実施順序」どおり）

1. G-2 — `api.ts` の `unknown` 戻り型を具体型に
2. F-2 / F-3 / F-5 — 小さく安全なバックエンド整理（1コミットにまとめて実施）
3. F-1 — `index_document`/`index_web_content` の重複統合
4. F-6 — SSRF防御フェッチ/YouTube抽出のサービス層移動
5. G-1 / G-3 — フロントエンド分割
6. F-4 — バイト同一が証明できず**スキップ**

各項目ごとに: 実装 → 該当テスト作成/拡張 → 検証（バックエンド: 全テストランナー、
フロントエンド: tsc+lint+build）→ コミット、の順で進めた。

## 2. Baseline（作業開始時）

- `git status`: クリーン、`main` は `origin/main` と同期、`bdb6737` (V2 debt map 追加コミット)。
- `./venv/bin/python -m compileall -q . -x venv`: エラーなし。
- バックエンド全テスト: 16ファイル・**92/92 合格**（前回セッション終了時と同じ）。
- フロントエンド: `npx tsc --noEmit` / `npm run lint` / `npm run build` すべてクリーン
  （14ルート、ビルド成功）。

## 3. 各項目の対応

### G-2. api.ts の型付け
`request<T>` の `unknown`/`unknown[]` を `lib/types.ts` の既存型
（`ResearchSession`/`Document`/`Report`/`RiskAnalysis`）に置換。5ページの冗長な `as X` キャストを削除。
ランタイム挙動は不変（型のみ）。検証: tsc/lint/build クリーン。

### F-2 / F-3 / F-5（バックエンド小整理・1コミット）
- **F-2**: `list_documents` のチャンク数 N+1 を `func.count`+`group_by` の一括クエリに変更。
  新規テストでクエリ件数が文書数に関わらず常に2件であることを固定（回帰検知）。
- **F-3**: `sse_event` を `services/anthropic_client.py` から `utils/sse.py` へ移動
  （anthropic_client 側は re-export し既存 import を壊さない）。4箇所の手組みエラーイベント
  文字列を `sse_event(...)` 呼び出しに置換 — 置換前後で生成される文字列が
  byte-for-byte 一致することをスクリプトで確認してからコミット。
- **F-5**: `assign_folder` が既存 `metadata_json` を読まずに上書きしていたのを
  マージに変更（パース失敗時は従来どおり上書き）。

### F-1. インデックス処理の重複統合
`_embed_and_store(doc, chunks, db, *, page_count=None, word_count=None)` を新設し
`index_document`/`index_web_content` から共通利用。例外処理ポリシー（re-raise vs 握りつぶし）は
呼び出し側に残した。新規 `tests/test_index_document.py`（4テスト）で
index_web_content との挙動差（空チャンク時のエラー扱い、re-raiseの有無）を固定。

### F-6. 取込ヘルパーのサービス層移動
SSRF防御フェッチ・YouTube抽出・スクレイピングを `routers/documents.py` から
`services/ingestion.py` へ移動。関数本体が移動前後で byte-for-byte 一致することを
`diff` で確認（1回目の抽出スクリプトのオフセットミスで空行1行分の差分が出たが、
範囲を訂正して再確認しゼロ差分であることを確認）。新規 `tests/test_ingestion.py`
（18テスト、すべてオフライン）で `_extract_youtube_id`/`_ip_is_blocked`/`_resolve_public_ip`
をカバー。`_assert_public_url` はどこからも呼ばれていないことを発見したが、
削除は§5の停止条件（削除要否不明）に該当するため見送り、報告のみ。

### G-1. library/page.tsx の分割（902行 → 4コンポーネント）
`UploadPanel`/`FolderSection`/`ChatPanel`/`RemindersPanel` に分割。共有state
（`docs`/`selectedDocs`）と、ページマウント時に読み込むタイミングを保つ必要がある
`reminders` は親に残した。各コンポーネントのJSXとロジック関数を元ファイルと `diff` して
検証。**この過程で実装ミスを発見・修正**: 初稿で `handleDelete`/`handleDeleteCollection` を
`loadDocs()`（サーバー再取得）に書き換えてしまっていたが、元実装は `setDocs` による
楽観的ローカル更新だった。`FolderSection` に `setDocs` を props 追加し元のロジックに戻した。

手動ブラウザ確認: 本セッションの環境では Chrome 拡張が未接続のため、クリック操作による
E2E確認は未実行。代替として、バックエンド(uvicorn, port 8123)とフロントエンド
(next dev, port 3123) を一時起動し、既存の開発用DBに対して `/api/documents/`・
`/api/reminders/` が実データを返すこと（F-2/F-5の実データでの動作確認を兼ねる）、
`/library` が200を返しサーバーログ・レンダリングHTMLにエラーが無いことを確認した。

### G-3. debate/page.tsx のエクスポートヘルパー移動
`buildMarkdown`/`buildPlainText`/`downloadBlob`/`exportAsPdf` を `lib/exportDebate.ts` へ。
`Argument` 型も単一の情報源として同ファイルへ移動。`buildPlainText`/`exportAsPdf` は
元々ページの `PERSONA_MAP` を暗黙参照していたため、真に純粋な関数にするため
`personaMap` を明示引数に追加（呼び出し側2箇所を更新）。`buildMarkdown`/`downloadBlob`
（シグネチャ変更なし）は移動前後で byte-for-byte 一致を diff で確認。

**発見（未修正）**: `exportAsPdf` 内の `win.document.write(html)` は移動元から不変の
既存ロジック。自動セキュリティレビューフックが一般的な document.write の注意点を
表示したが、「ロジックは一切変えない」という本項目の制約と矛盾するため変更していない。
討論参加者名・本文（LLM生成）やトピック（自由入力）が未エスケープでHTML文字列に
埋め込まれる点は、人間の判断を仰ぐ別イシューとして報告のみに留める。

### F-4. system プロンプトの二重管理 — **スキップ**
`default_system`（custom_system なし）と custom_system 分岐の制約文を単語単位で比較した結果、
プレフィックスの違いだけでなく実質的な文言差異が複数箇所にあることを確認:
- "Before answering any substantive question, call search_documents with a relevant query."
  vs "Call the tool before answering substantive questions." （文構造ごと異なる）
- default 分岐にのみ存在する2文（3–5文推奨の指示、会話履歴の利用に関する指示）が
  custom 分岐には存在しない
- 日時表現の指示文言も "relative date or time **expression**" / "you **MUST** call" /
  "to compute the exact target datetime, and finally call set_reminder" vs
  "relative date or time" / "call" / "then add_duration_to_datetime, then set_reminder"
  と複数箇所で異なる

指示書 §12 F-4 は「結合後の文字列がバイト単位で不変になる場合のみ共有定数へ抽出。
1文字でも変わるなら実装せず提案のみとして報告」と明記しているため、上記の理由により
**実装せず、`rag_service.py` のプロンプト構築コードには一切触れていない**。

---

## 4. テスト・検証結果

- バックエンド: 16ファイル・92テスト → **20ファイル・118テスト**（新規4ファイル: 
  `test_index_document.py`(4)、`test_ingestion.py`(18)、既存2ファイル拡張:
  `test_folder_ops.py`(4→7)、`test_sse.py`(9→10)）。全118件、各コミット後に
  フルスイート実行しすべて合格。
- フロントエンド: 各コミット後に `npx tsc --noEmit && npm run lint && npm run build` を
  実行、すべてクリーン（14ルート、ビルド成功）。
- Eval（API課金あり・APIキーなしのため）: **未実行**。F-1/F-3/F-5 は
  `rag_service.py`/`anthropic_client.py`/`routers/documents.py` のプロンプト以外の
  箇所を編集したため、CLAUDE.md の「service ファイル編集時は関連 eval を実行」ルールが
  厳密には適用されるが、本セッションはAPIキーを持たないため実行できなかった。
  人間に以下の実行を依頼:
  ```
  cd backend && python -m evals.eval_research_queries
  python -m evals.eval_synthesis_quality
  ```
  （プロンプト文言・temperature・フォールバック挙動はすべて不変のため、回帰は
  想定していないが、実測での確認が望ましい）

## 5. 申し送り

- F-4 は上記の理由でスキップ。`refactor-instructions.md` の F-4 エントリは
  「対応済み」マークを付けず、指示書どおり本ワークログと最終報告に理由を記載。
- G-1 の手動ブラウザ確認（アップロード→チャット→リマインダー操作）は Chrome 拡張未接続のため
  未実行 — 人間による実機確認を推奨。
- `_assert_public_url`（`services/ingestion.py`）はどこからも呼ばれていない
  （F-6 で移動のみ、削除せず）。削除の要否は人間の判断を仰ぐ。
  →【2026-07-07 対応済み】削除（コミット de634e9）。バックエンド全テスト
  （18ファイル）と compileall で確認。
- `exportAsPdf`（`lib/exportDebate.ts`）の `win.document.write(html)` は
  LLM生成テキスト・自由入力トピックを未エスケープでHTML文字列に埋め込む既存の
  パターン。今回のリファクタでは変更していないが、別途セキュリティ観点での
  レビューを推奨。
  →【2026-07-07 対応済み】`escapeHtml()` ヘルパーを追加し、`exportAsPdf` 内の
  全動的値（topic/roundName/personaName/title/initials/content/synthesis）に
  適用（コミット aabe9d0）。tsc/lint/build 確認済み。
- `routers/documents.py` の `from datetime import datetime` は F-6 以前から未使用
  （本セッションが作った状態ではない）。
- PR はレビュー・マージ待ち。マージ後、次回作業はこのブランチを `main` から作り直すこと。
