# 🏢 AI Office

<!--version-->v2.1.0<!--/version--> · macOS 専用 · MIT · アカウント不要・テレメトリ無し・作者のサーバーは存在しません

**Claude Code・Codex CLI・OpenClaw のセッションに答え、留守中の仕事を振り返り、作業中も配信中も置いておける Mac の3Dオフィス。**

**1セッション＝1体のロボット**。Claude Code の `/rename` が表示名になるので、同じフォルダで並走するセッションも区別できます（プロジェクト単位の集約も設定可能）。Claude は丸頭とアンテナ1本、Codex は角丸四角の頭と2本、OpenClaw はロブスターのシルエットです。

見た目は、オーク床・白壁・大窓・植物・リネン・陶器のロボを組み合わせた、昼光の「写実寄り北欧オフィス」。正本は [アートディレクション](docs/art-direction.md) です。

![AI Office デモ](docs/demo.gif)

> **インストールなしでブラウザから試せます: <https://ai-office-demo.routinelabo-senao.workers.dev>**
> （[台帳の画面](https://ai-office-demo.routinelabo-senao.workers.dev/?ui=pixel)・静的ファイルだけ・こちらのサーバーは無し・アカウント不要）
>
> 手元で動かしていてセッションが無いとき: <http://localhost:4780/?demo=1>

## 何のための道具か

セッションを**動かす**道具は本家が出しています（**Agent View** が並列セッションを 1 か所に並べ、
**Remote Control** がスマホから答える）。どちらも良い道具で、AI Office はその置き換えではありません。
こちらが受け持つのは残り半分 — **1 日の仕事を、複数のベンダーをまたいで、一目で見る**ことです。

- **全部のベンダーが同じ床に居る。** Claude Code（対話・バックグラウンド）、Codex CLI、つないだ
  OpenClaw が 1 つの部屋に社員として出ます。1 セッション＝1 体、`/rename` がそのまま机の名札に。
- **枠とお金が 1 枚で見える。** サブスクの枠と従量の残高を並べて出します（5 プロバイダ・上限が
  無いものは「上限が設定されていません」と書く＝嘘の % を出さない）。
- **この Mac から出ない。** アカウントもテレメトリも、こちらのサーバーもありません。外へ出るのは
  **自分で置いた**中継と、**自分の鍵を入れた**プロバイダの残高だけです（「この Mac から何が出るか」）。
- **置いておく価値のある部屋。** 白×ラベンダーのガラスのロフト、生成した什器、歩く・座る・打鍵する・
  呼ぶときに手を挙げるロボット。離れた席からでも分かり、配信に載せても見られます。

## 主な機能

- **回答キューと状況シート** — 質問の実際の選択肢、数字キーでの回答、いま/次/完了タスク、最近の活動、返信チップ、自由入力。
- **Codex CLI 対応** — `~/.codex` の SQLite を読み取り専用で参照し、`codex queue` で指示を配達。セッション用アダプタは会話本文の列を読みません。配達には `queue` 対応の Codex CLI が必要で、失敗時は失敗として表示します。
- **雇う** — 登録済みプロジェクトと仕事を選び、「別ブランチで隔離」を切り替えて起動。`POST /api/hire` から `claude --bg` を実行し、隔離時は worktree を使います。Codex の選択肢は `open -a Terminal` をコピーし、手元で Terminal を開いて Codex を起動する導線です。
- **留守中ダイジェストと20秒リプレイ** — 返答待ち・完了・進行中をまとめ、記録した時間軸を20秒に圧縮して再現。端末の画面や会話本文の録画ではありません。
- **XPとレベル** — タスク完了・コミット・回答・早い回答のボーナス・稼働分・雇用・Codex のターン完了を集計。トークン量は対象外です。Mac では各寄与を開示し、中継では合計とレベルだけを送ります。
- **スマホPWAとWeb Push** — 同じ3Dレンダラを `quality:"mobile"` で使用（ブルームのパスは無し＝画面は発光の明るさだけで光る）。QRペアリング・端末別署名の返答・質問のプッシュ通知。
- **コストゲージ・通知・日報** — 利用枠と支出の確認、デスクトップ通知、日次のまとめ。
- **MCP** — `office_status` / `office_instruct` / `office_digest` で、エージェントからも状況確認・指示・ダイジェスト取得。

## 同じオフィスの 2 つの画面

既定は 3D のオフィスです。もう 1 つの画面が**台帳**（`?ui=pixel`・試験中）で、1 プロジェクト 1 行・行の高さ 30px
にして、20 プロジェクト・40 セッションを 1 画面に収めます。❗が出た行は配下のセッションを開いて最上段へ動き、
数字キーでそのまま答えられます。証拠列には「そのセッションが直近 24 時間で実際に commit を通したか／テストを
通したか」が出ます（自己申告ではなく hook が記録した事実だけ・分からないときは「—」）。切り替えは ⚙ 設定の
「画面」から。選択はブラウザごとに記憶されます。回答キュー・シート・定型文・雇う・ゲージは両方で同じコードです。

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

**まず見るだけ** — 何も入れず、`Ctrl-C` で跡形もなく消えます:

```bash
git clone https://github.com/senao-routine/ai-office.git
cd ai-office
bash setup.sh --demo
```


**自分のセッションを出勤させるときは:**

```bash
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

### この Mac から何が出るか

作者には何も送られません。アカウントもテレメトリも、こちらのサーバーもありません（リポジトリの
どこにも作者のエンドポイントは無く、起動時の版チェックの通信すらしません）。HTTP サーバーは
`127.0.0.1` だけで待ち受けるので、同じ LAN の他の機械からも触れません。

外へ出る通信は 2 種類だけで、どちらも自分で有効にしたときだけ動きます。

| 宛先 | いつ | 何を送るか |
|---|---|---|
| **自分で置いた Cloudflare Worker**（`relay/`） | スマホ中継を設定したときだけ | allowlist を通したオフィスの表示情報。本文もローカルパスも載りません（新しいフィールドは既定で載りません）。 |
| **api.x.com・api.openai.com・openrouter.ai・api.moonshot.ai・api.deepseek.com** | API キーを入れたプロバイダの分だけ | 自分の残高や枠を読むための `GET` 1 本（キー付き）。オフィスの情報は 1 つも載らず、キーが無ければ 1 バイトも出ません。結果は 15 分キャッシュします。 |

ブラウザ側はインターネットから何も読みません（CDN も Web フォントも解析タグも無し）。three.js は
このリポジトリに同梱しています。

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

**スマホで会話を読む（任意・既定 OFF）。** スマホ連携のシートで「スマホに会話を見せる」を ON にすると、シートの **💬 会話を見る** で、その端末だけが開ける形に **AES-256-GCM で封じた**直近の会話が届きます（鍵は端末ごとのペアリング秘密から導出）。守られること: 中継（Cloudflare）や輸送トークンだけを持つ第三者は中身を読めません。守られないこと: 「いつ・どの端末が・どのセッションを要求したか」とおおよその長さは中継に見えます。前方秘匿性はありません（秘密は30日固定）。暗号文は期限（90秒）まで中継に残ります。Mac 自身は当然読めます。**スマホを失うと、指示を送れることに加えて会話も読まれます**——Mac から端末を失効させれば、その瞬間から封は作られません。「ゼロ知識」ではありません。OS の暗号ライブラリが使えない Mac ではボタン自体が出ません。

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

| | |
|---|---|
| **Mac** | Apple Silicon / Intel。macOS 13 Ventura 以降（動作確認は 15 Sequoia）。 |
| **Python** | 素の `/usr/bin/python3`（3.9.6）で足ります（サーバーは標準ライブラリのみ）。無ければ `xcode-select --install`。 |
| **エージェント** | Claude Code（対話・バックグラウンド）／Codex CLI／設定済みの OpenClaw 連携のいずれか。雇用には Claude Code のバックグラウンド起動、Codex への配達には `codex queue`、**オフィスから許可プロンプトに答えるには `PermissionRequest` フックに対応した Claude Code** が要ります。 |
| **ブラウザ** | 3D は WebGL 対応ブラウザ。使えない環境では一覧表示に切り替わります。 |
| **Node.js** | 開発時の検査と、任意のスマホ中継だけに必要（wrangler が Node 22 以上）。ローカルで動かすだけなら不要です。 |
| **言語** | 日本語／英語（自動）。`office_config.json` の `"lang"` で固定できます。 |

**対応していないもの:** Windows と Linux。常駐・ターミナル連携・封書の暗号は macOS 固有の仕組みを使っています。

## 開発者向け

`bash dev.sh --check` が高速レーン、`bash verify.sh` が全ゲートです。[アートディレクション](docs/art-direction.md)・[Office JSON](docs/office-json.md)・[変更履歴](CHANGELOG.md) を正本として参照してください。

## 困ったときは

まず `bash setup.sh --check` を実行してください。詰まりやすい所（Python・フック・常駐・ポート）を診断します。
その出力を添えて [Issue](https://github.com/senao-routine/ai-office/issues) を立ててください（日本語で構いません）。
セキュリティに関わるものは公開 Issue ではなく、GitHub の非公開の脆弱性報告からお願いします（[SECURITY.md](.github/SECURITY.md)）。

個人で作っているので返信の速さは約束できませんが、全部読みます。

Built by [senao](https://github.com/senao-routine) / Routine Labo.
