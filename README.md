# AI Policy Research Assistant

AI 政策リサーチを支援する Web アプリケーションです。Claude（Anthropic）と Tavily を活用し、調査・分析・レポート生成をひとつの画面でこなせます。

## 主な機能

- **Research** — 質問を入力すると、クエリを自動分解して並列 Web 検索し、Claude が結果を統合・要約（ストリーミング出力）
- **Document Library** — PDF・Web ページ・YouTube 動画をアップロード／登録し、RAG（ChromaDB）で検索可能な知識ベースを構築
- **Analysis** — ドキュメントライブラリに基づいた深掘り分析をストリーミングで生成
- **AI Policy Debate** — 複数のペルソナ（規制推進派・技術楽観派など）が AI 政策テーマについて議論するシミュレーション
- **Reports** — Congressional Brief・Policy Memo・Risk Assessment の 3 テンプレートから PDF レポートを生成
- **Daily Digest** — 指定トピックの最新動向を毎日メールで配信（オプション）
- **Settings** — 使用モデル（claude-opus-4-6 / claude-haiku）や API キーをブラウザから変更可能

## 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | Next.js 14 · TypeScript · Tailwind CSS |
| バックエンド | FastAPI · Python 3.12 |
| AI | Anthropic Claude API（claude-opus-4-6 / claude-haiku-4-5） |
| Web 検索 | Tavily API |
| ベクター DB | ChromaDB + sentence-transformers |
| DB | SQLite（SQLAlchemy） |
| スケジューラ | APScheduler |

## セットアップ

### 必要なもの

- Python 3.11+
- Node.js 18+
- [Anthropic API キー](https://console.anthropic.com)
- [Tavily API キー](https://app.tavily.com)（無料枠: 1,000 リクエスト/月）

### 手順

```bash
# 1. リポジトリをクローン
git clone https://github.com/your-username/ai-research-policy-app.git
cd ai-research-policy-app

# 2. バックエンドの環境変数を設定
cp backend/.env.example backend/.env
# backend/.env を開いて API キーを入力

# 3. バックエンドの依存パッケージをインストール
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 4. フロントエンドの依存パッケージをインストール
cd frontend
npm install
cd ..
```

### 起動

```bash
./start.sh
```

- フロントエンド: http://localhost:3000
- バックエンド API ドキュメント: http://localhost:8000/docs

または個別に起動する場合:

```bash
# バックエンド
cd backend && source venv/bin/activate
uvicorn main:app --reload --port 8000

# フロントエンド（別ターミナル）
cd frontend && npm run dev
```

## 環境変数

`backend/.env.example` をコピーして `backend/.env` を作成し、以下の値を設定してください。

| 変数 | 説明 | 必須 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API キー | ✅ |
| `TAVILY_API_KEY` | Tavily 検索 API キー | ✅ |
| `CLAUDE_MODEL` | メインモデル（デフォルト: `claude-opus-4-6`） | |
| `CLAUDE_FAST_MODEL` | 高速モデル（デフォルト: `claude-haiku-4-5-20251001`） | |
| `CORS_ORIGINS` | フロントエンドの URL（デフォルト: `http://localhost:3000`） | |
| `DIGEST_EMAIL_TO` | ダイジェストメールの送信先 | |
| `DIGEST_EMAIL_FROM` | 送信元 Gmail アドレス | |
| `DIGEST_SMTP_PASSWORD` | Gmail アプリパスワード | |
| `DIGEST_TOPICS` | 監視するトピック（カンマ区切り） | |

> Gmail アプリパスワードは「Google アカウント → セキュリティ → 2 段階認証 → アプリパスワード」から発行できます。

## ライセンス

MIT
