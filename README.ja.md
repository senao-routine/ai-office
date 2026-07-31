# 🏢 AI Office — AIエージェント艦隊の管制室

**Macで動く全 Claude Code セッションを、3Dアイソメのロボットオフィスとしてライブ表示し、そのまま操作する。**

同じプロジェクトのセッションは**1体のアバターに自動集約**（`works ×9`）。誰が作業中で、誰がサブエージェントと会議中で、誰があなたの承認待ちで止まっているかがフロア1枚で分かる。見るだけでなく**動かせる**のが核: ❗トレイの数字キーで即回答・ロボットをタップして状況シートから指示・外出先はスマホPWAから承認。

![AI Office demo](docs/demo.gif)

> セッションが無くても <http://localhost:4780/?demo=1> ですぐ見られます。

## なぜ作ったか

エージェントを並列で走らせると、ボトルネックはAIではなく**人間側**になる。席を離れた瞬間に、承認プロンプトと質問でセッションが止まる。

AI Office はそのループを閉じる:

- ❗ 承認・質問が発生するとロボットの頭上にビックリマーク＋（Pro）iPhoneへ**プッシュ通知**
- 質問の**実際の選択肢がそのままボタン**になる（`AskUserQuestion` をミラー）— 1キー/1タップで回答
- 返答は Stop-hook 経由で**実セッションの中に届き、エージェントは走り続ける**

## 主な機能

- **🤖 ライブ3Dオフィス** — 1アバター=1プロジェクト・タイピング/伸び/コーヒー/会議のロボット挙動
- **❗ 回答キュー** — 最優先の質問がトレイに常駐、数字キーで即答
- **📇 状況シート** — 生ログでなく「人間が読む1文」＋いま/次/完了タスク＋クイック返信チップ＋自由入力
- **👑 ボスデスク** — 金の王冠ロボをクリック→任意のプロジェクトへ指令
- **🔔 デスクトップ通知＋18時の日報** / **🎬 デモモード**（`/?demo=1`）
- **📱 スマホPWA＋Web Push**（Pro） / **💸 コストゲージ**（Pro）
- **🔌 MCPツール** — `office_status` / `office_instruct`（エージェント自身がオフィスを読み書きできる）
- **🔒 プライバシー設計** — 127.0.0.1のみ・transcripts読み取り専用・**本文はMac側で除去してから中継**
- **依存ゼロ** — サーバーはPython標準ライブラリのみ。クローンして即動く

## クイックスタート

```bash
git clone https://github.com/senao-routine/ai-office.git && cd ai-office
python3 server/office_server.py
```

<http://localhost:4780> を開く。**指示配達の有効化**（これが本体・1分）:

```bash
bash hooks/install.sh --wire   # ~/.claude/settings.json へ Stop hook を自動配線（バックアップ作成・冪等）
```

常駐化（ログイン時自動起動・再起動後も生存）:

```bash
bash macapp/install.sh
```

MCP登録（任意）:

```bash
claude mcp add --scope user aioffice -- "$(command -v python3)" "$PWD/server/mcp_office.py"
claude mcp list   # → aioffice: connected
```

## 配達経路（仕組み）

1. UI/PWA/MCP から投函 → `~/.claude/office_inbox/<セッションID>.json`
2. グローバル Stop hook（`office-inbox-wait.sh`・asyncRewake）がポストを監視し、**そのセッションを起こして指示を渡す**
3. 届くタイミング: 待機中=数秒／作業中=ターン終了直後／閉じたセッション=次に開いた後。📨=配達待ち

権限ダイアログの代行は不可（仕様）。「今やっていること」はトランスクリプト末尾（読み取り専用・直近のみ）からの推定。

## スマホ連携（Pro）

中継は**自分の Cloudflare アカウント**で動く（無料枠で十分・ポート開放なし・Macは完全アウトバウンド）。

1. `bash relay/deploy.sh`（Worker + Durable Object をデプロイ）
2. オフィスUI → 📱 → デバイス発行 → QRペアリング
3. ホーム画面に追加（iOS 16.4+）→ 🔔 でPush有効化

中継は輸送トークンしか持たず**指示を偽造できない**（真正性は per-device HMAC 署名を Mac 側で検証する二層認証）。

## エディション

| | 価格 | 内容 |
|---|---|---|
| **Free** | $0 | ローカルオフィス全機能（3D表示・デスクトップからの指示・デモ・MCP） |
| **Pro** | $12.99 買い切り | スマホPWA配達・プッシュ通知・コストダッシュボード |
| **Hybrid** | $29 買い切り | Pro＋外部エージェント（OpenClawノード）を同じオフィスに表示 |

オフラインライセンスファイル。サブスク無し・アカウント無し・テレメトリ無し。販売リンクは公開時に掲載（このリポジトリを Watch）。

## 動作環境

- macOS（Apple Silicon / Intel）・Python 3.9+（システムの `python3` でOK）
- [Claude Code](https://claude.com/claude-code)
- UI言語: 日本語/英語（ロケール自動判定・`office_config.json` の `"lang"` で固定可）

## 開発者向け

検証ループは `bash verify.sh`（全ゲート）と `bash dev.sh`（fixture高速レーン）。中継の設計は `relay/README.md`。

---

Built by [senao](https://github.com/senao-routine) / Routine Labo.
