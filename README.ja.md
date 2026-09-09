# 🏢 AI Office 2.0

**Claude Code・Codex CLI・OpenClaw のセッションに答え、留守中の仕事を振り返り、作業中も配信中も置いておける Mac の3Dオフィス。**

**1セッション＝1体のロボット**。Claude Code の `/rename` が表示名になるので、同じフォルダで並走するセッションも区別できます（プロジェクト単位の集約も設定可能）。Claude は丸頭とアンテナ1本、Codex は角丸四角の頭と2本、OpenClaw はロブスターのシルエットです。

見た目は、オーク床・白壁・大窓・植物・リネン・陶器のロボを組み合わせた、昼光の「写実寄り北欧オフィス」。正本は [アートディレクション](docs/art-direction.md) です。

![AI Office デモ](docs/demo.gif)

> セッションが無くても見られます: <http://localhost:4780/?demo=1>

## 3本柱

- **ベンダー横断で答えられる。** Claude Code（対話＋バックグラウンド）・Codex CLI・接続済みの OpenClaw を一緒に表示。❗トレイの数字キー、3Dのロボットをクリックした状況シート、スマホから返答できます。配達経路はベンダーごとに異なり、Claude の権限承認はローカルの PermissionRequest フックが担います。
- **人間側の計測と時間軸。** 返答待ちの時間や、留守中に終わった仕事を記録。留守中ダイジェスト・20秒リプレイ・XP/レベルで経過を振り返れます。トークン量は XP に入れず、Mac の画面で内訳を開示します。
- **上質な3Dの、置いておける画面。** 明るい室内に弱いブルーム・ビネット・静止グレイン、自前のポスト処理、PMREM の小部屋環境マップと昼光ライティング。同時稼働の最大値で S/M/L/XL（24席）に広がり、レベルで装飾が解錠されます。配信にもセカンドモニタにも使えます。

## 主な機能

- **回答キューと状況シート** — 質問の実際の選択肢、数字キーでの回答、いま/次/完了タスク、最近の活動、返信チップ、自由入力。
- **Codex CLI 対応** — `~/.codex` の SQLite を読み取り専用で参照し、`codex queue` で指示を配達。セッション用アダプタは会話本文の列を読みません。配達には `queue` 対応の Codex CLI が必要で、失敗時は失敗として表示します。
- **雇う** — 登録済みプロジェクトと仕事を選び、「別ブランチで隔離」を切り替えて起動。`POST /api/hire` から `claude --bg` を実行し、隔離時は worktree を使います。Codex の選択肢は `open -a Terminal` をコピーし、手元で Terminal を開いて Codex を起動する導線です。
- **留守中ダイジェストと20秒リプレイ** — 返答待ち・完了・進行中をまとめ、記録した時間軸を20秒に圧縮して再現。端末の画面や会話本文の録画ではありません。
- **XPとレベル** — タスク完了・コミット・回答・早い回答のボーナス・稼働分・雇用・Codex のターン完了を集計。トークン量は対象外です。Mac では各寄与を開示し、中継では合計とレベルだけを送ります。
- **スマホPWAとWeb Push** — 同じ3Dレンダラを `quality:"mobile"` で使用。QRペアリング・端末別署名の返答・質問のプッシュ通知。
- **コストゲージ・通知・日報** — 利用枠と支出の確認、デスクトップ通知、日次のまとめ。
- **MCP** — `office_status` / `office_instruct` / `office_digest` で、エージェントからも状況確認・指示・ダイジェスト取得。

## 配達経路

Claude Code への指示は `~/.claude/office_inbox/<セッションID>.json` に投函し、Stop フックがターン終了時に届けます。待機中ならフックが受け取り、作業中ならターン終了を待ちます。

**権限プロンプトで止まった端末にも答えられます。** ターン終了を待てない許可要求には、`PermissionRequest` フックが質問を掲示し、回答を決定として返します。ターミナルで直接答えることもでき、フック待機中も人の回答が優先されます。

- **Mac のローカル画面（127.0.0.1）では実行を許可できます。**
- **スマホからは言葉を届けます。** 保留中の権限要求に対する実行許可は渡しません。
- Codex への指示は `codex queue`、OpenClaw は設定済みの inbox/relay 経路を使います。

## 配信モード

<http://localhost:4780/?stream=1> で通常のHUDを隠し、3Dオフィス・活動の字幕帯・❗件数を表示します。自動カメラが活動を追い、手動操作すると停止します。

- OBSの横画面: <http://localhost:4780/?stream=1&aspect=16:9>
- 縦動画: <http://localhost:4780/?stream=1&aspect=9:16>
- プロジェクト名を隠し、1文字のバッジと状態だけにする: `&privacy=1` を追加。
- 名札を大きくする: `&plates=large` を追加。

設定から OBS・縦動画・セカンドモニタ用URLをコピーできます。canvas のキャプチャと MediaRecorder に対応したブラウザでは、**20秒リプレイを WebM で保存**できます。`privacy=1` は表示上の匿名化で、中継データの契約を変更する設定ではありません。

## クイックスタート

```bash
git clone https://github.com/senao-routine/ai-office.git && cd ai-office
bash setup.sh
```

フックの配線、常駐登録と `launchctl` による起動、疎通確認、画面を開くところまで進めます。再実行も可能です。末尾の初回セッション体験は任意で、既定は No です。

```bash
bash setup.sh --check       # 診断のみ
bash setup.sh --no-daemon   # 常駐登録せず、その場で起動
```

<http://localhost:4780> を開くとセッションが表示されます。セッションが無ければ `/?demo=1` で確認できます。

### 何に触るか（What it touches）

- `~/.claude/settings.json` — バックアップを残し、指示配達の **Stop**、許可・質問に答える **PermissionRequest**、**17イベント**の非同期記録を配線します。無関係な既存フックは維持します。
- `hooks/office-event.sh` — 記録フックのリポジトリ正本。`~/.claude/hooks/office-event.sh` に配布します。種別・識別子・件数・ファイルの basename など限られたメタデータだけを `~/.claude/office_events/<YYYY-MM-DD>.jsonl` に記録し、**プロンプト・コマンド・返答の本文は書きません**。
- `~/.claude/office_timeline.sqlite` — ダイジェスト・リプレイ・XPのためのローカルDB。イベントや状態・返答待ちの区間を持ち、会話本文の列はありません。`~/.claude/office_seen.json` にダイジェストの確認時刻を保存します。
- `~/.claude/office_inbox/` と `~/.claude/office_approvals/` — 指示や保留中の許可要求・回答の受け渡し。回答に必要なテキストを含む場合があり、本文を持たない記録ログとは別です。
- `~/.claude/office_recipes.json`・`~/.claude/office_action_results.json`・`~/.claude/office_actions_audit.jsonl`（いずれも 0600）— スマホから実行できる操作の許可リスト、直近の実行結果、監査記録。結果をファイルで共有するのは、オフィス本体とスマホ中継が**別プロセス**で動くためです。保存するのは表示名・状態・伏字済みの出力だけで、コマンド・作業ディレクトリ・環境変数は保存しません。
- `~/.claude/projects/` — Claude のトランスクリプトは読み取り専用。Codex のセッション用アダプタも `~/.codex/state_5.sqlite` と `~/.codex/thread_history_1.sqlite` を読み取り専用で参照し、DBを変更しません。
- `~/Library/Application Support/AIOffice/` — 常駐アプリ本体とデータ。
- `~/Library/LaunchAgents/com.senao.aioffice.plist` — ログイン時の自動起動。
- Terminal を開く際の「自動化」と通知について、macOS が許可を求める場合があります。

### MCP登録（任意）

```bash
claude mcp add --scope user aioffice -- "$(command -v python3)" "$PWD/server/mcp_office.py"
claude mcp list
```

## スマホ連携（任意）

中継は**自分の Cloudflare アカウント**で動き、Mac から外向きに接続します。ポート開放は不要です。

```bash
bash relay/setup.sh
```

Cloudflare のログイン、トークンと通知鍵の生成、デプロイ、ローカル設定、疎通確認まで行います。そのあとオフィスの「スマホ連携」からデバイスを発行してペアリング。iPhone ではホーム画面に追加してプッシュ通知を有効にします。セットアップには Node.js が必要です。詳細は [中継のガイド](relay/README.md) を参照してください。

中継は allowlist 方式です。状態・質問と選択肢・設定したセッション名・バックグラウンドの一行要約・定型文・処理済みの実行結果を送り、会話本文とローカルパスのフィールドは Mac 側で除去します。セッション名と一行要約は個別に送信を止められます。[正確なデータ契約](docs/office-json.md) を参照してください。指示は端末別 HMAC 署名を Mac で検証し、中継の輸送トークンだけでは偽造できません。

## 料金

**全機能無料。** ローカルオフィス・スマホPWA・通知・設定済みの遠隔実行・コスト面・配信モードを含みます。版分け、ライセンス、AI Office のサブスクはありません。AIプロバイダと Cloudflare の利用は各自のアカウントの契約に従います。

更新情報と Routine Labo のコミュニティは [特設ページ](https://routinelabo-lp.routinelabo-senao.workers.dev) にあります。

## アンインストール

先に常駐を止めてください。`uninstall.sh` は **意図的に `launchctl` を実行しません**
（ログイン項目の解除はあなたの操作にしています）。登録が残っていると、何を実行すればよいかを
表示して止まります。

```bash
launchctl bootout gui/$(id -u)/com.senao.aioffice
launchctl bootout gui/$(id -u)/com.senao.aioffice.relay   # スマホ配達を有効にしていた場合

bash macapp/uninstall.sh              # plist とコードを削除（設定・データは残す）
bash macapp/uninstall.sh --purge-data # アプリの設定・データも削除
```

`~/.claude/settings.json` のフック配線は自動では消えません。`office-inbox-wait` / `office-approval-wait` / `office-event` を参照する AI Office の項目を削除するか、設定のバックアップへ戻してください。記録フックは複数のイベントに登録されています。`~/.claude` のイベント・タイムラインはアプリのデータディレクトリとは別に保存されています。

## 動作環境

- macOS・Python 3.9+。サーバーは Python 標準ライブラリのみ。
- Claude Code、Codex CLI、または設定済みの OpenClaw 連携。雇用には Claude Code のバックグラウンド起動、Codex への配達には `codex queue` 対応が必要です。
- 3Dは WebGL 対応ブラウザ。利用できない場合は一覧表示へ切り替えられます。
- 開発時の検査と任意のスマホ中継セットアップには Node.js が必要です。ローカルオフィスの起動に UI のパッケージインストールは不要です。
- UI言語は日本語/英語。`office_config.json` の `"lang"` で固定でき、常駐版の設定は `~/Library/Application Support/AIOffice/data/office_config.json` にあります。

## 開発者向け

`bash dev.sh --check` が高速レーン、`bash verify.sh` が全ゲートです。[アートディレクション](docs/art-direction.md)・[Office JSON](docs/office-json.md)・[ロードマップ](docs/ROADMAP.md) を正本として参照してください。

Built by [senao](https://github.com/senao-routine) / Routine Labo.
