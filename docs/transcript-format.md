# Claude Code トランスクリプト形式の観測台帳

> **なぜこのファイルがあるか**: AI Office は `~/.claude/projects/<プロジェクト>/<セッションID>.jsonl` を
> 解析して社員の状態を推定するが、この形式は**非公開仕様**でバージョンアップで変わりうる（最大リスク）。
> 観測した事実をここに記録し、形式が変わったら差分を追記 → `tools/capture_fixture.py` でフィクスチャ更新
> → verify.sh を green に戻す、が復旧手順。一次検知は verify.sh の「実データカナリア」。

## 観測済みの構造（2026-07-08・Claude Code / Fable 5 世代）

- 1行=1イベントのJSONL。パースに使うキー:
  - `type`: `"user"` / `"assistant"` を状態推定に使用。ほかに `"file-history-snapshot"` 等が混在（無視）
  - `cwd`: 作業ディレクトリ（社員の部署判定に使用）／`gitBranch`: ブランチ表示
  - `message.role` / `message.content`
- `message.content` の形:
  - assistant → ブロック配列: `{type:"text",text}` / `{type:"thinking"}` / `{type:"tool_use", name, input}`
  - user → **文字列**（人間の指示）または ブロック配列（`{type:"tool_result"}` を含む=ツール結果返却）
- `tool_use.input` のよく使うキー: `file_path` / `command`+`description` / `pattern` / `skill` / `prompt` / `url` /
  `questions[]（AskUserQuestion: {question, options[{label}]}）`
- スキル実行の観測形式:
  - assistant の `message.content[]` に `{type:"tool_use", name:"Skill", input:{skill:"x-post"}}`
  - user の `message.content` に `<command-name>/x-post</command-name>`（スラッシュ付きのコマンド名）
  - AI Office はイベントの `timestamp` を基準に直近30分だけを採用し、表示用に英数字・`_`・`:`・`-` 以外を除去する
- サブエージェント: `projects/<dir>/<sessionId>/subagents/**/*.jsonl`（Workflowは `subagents/workflows/wf_*/`）
- ファイル名 = セッションID（UUID）。mtime = 最終イベント時刻として利用可
- macOS の日本語パスは **NFD 正規化**で保存されることがある → 比較は必ず NFC 経由（server の `nfc()`）

## タスク系tool_use（非公開仕様・2026-07-24観測）

- タスク登録は assistant の `message.content[]` に次の形で現れる。
  `{"type":"tool_use","name":"TaskCreate","input":{"subject":"概要欄(YouTube構成案・概要欄.md)を生成","description":"...","activeForm":"概要欄を生成中"}}`
- 登録直後の user 行にある `tool_result` の `tool_use_id` を TaskCreate と対応付ける。
  結果本文は `Task #2 created successfully: <subject>` の形で、ここからタスクIDを取得する。
- 状態変更は `TaskUpdate` の `input.taskId` と `input.status` で表現される。
  `status` は `pending` / `in_progress` / `completed` / `deleted`。`addBlockedBy` など status のない更新は無視する。
- 旧形式として `TodoWrite` も存在し、`input.todos[]` の各要素は `content` / `status` / `activeForm` を持つ。
  AI Office ではこの配列を現在のタスク表として全置換し、IDは配列インデックスを使う。
- タスクの初期値は pending。TaskCreate の ID対応に失敗した場合は TaskCreate の出現順による連番へフォールバックする。
- 現在の社員カード解析では、通常状態用の末尾80KB窓とは別に、`TaskCreate` / `TaskUpdate` / `todos` を含む行だけを末尾2MBから拾う。
  タスク表はセッション別のメモリに60分保持し、新しい観測で上書き合流する（daemon再起動でリセット可）。

## 状態ブロック行（非公開仕様・2026-08-26観測）

ターン境界ごとに `type` が `user`/`assistant` 以外の「状態ブロック行」が繰り返し追記される。
このMacの実測での出現量（多い順）: `last-prompt` / `mode` / `permission-mode` / `ai-title` /
`agent-name` / `bridge-session` / `custom-title`。**最後の1件が現在値**。

- **`{"type":"custom-title","customTitle":"決済チーム","sessionId":"<UUID>"}`**
  — `/rename` で人間が付けたセッション名。AI Office はこれを `title` として表示名に採用する（R85-1）。
  実測で最後の1件は全サンプルEOFから20KB以内＝末尾80KB窓で拾える。空文字はリネーム解除。
  `sessionId` はファイル名と一致するが、resume/fork 引き継ぎがあるため不問で採用する。
- `{"type":"ai-title","aiTitle":"…"}` — Claude Code の自動生成タイトル。ほぼ全セッションに存在し
  **リネーム後も更新され続ける**ため表示名には採用しない（採用すると全アバターが自動生成文になる）。
- `{"type":"agent-name","agentName":"…"}` — 自動命名と `/rename` 由来が混在し「リネームされたか」の
  判定に使えない。不採用。
- `{"type":"summary"}` 行は旧世代の遺物（2026-08-26時点の全走査で0件）。

## 会話ビューアの抽出（R86-B・2026-08-27観測）

`dialog_from_lines`（GET /api/session/dialog）が依存する観測事実:
- user の `content` が**文字列**のとき、人間の生指示のほかに **`<`始まりの注入行**が混在する:
  `<command-name>…</command-name>`（スラッシュコマンド実行）・`<local-command-stdout>`（その出力）・
  `<system-reminder>`（ハーネス注入）等。会話としては `<command-name>` のみ「/x-post」の形で残し、他は捨てる
- user の `content` が**配列**のとき `{type:"tool_result"}` が主（ツール結果返却＝会話でない）。
  `{type:"text"}` ブロックが人間の発話
- assistant の thinking / tool_use（AskUserQuestion 以外）は会話に出さない（本文・コマンドの露出面を増やさない）

## 推定ロジックが依存している暗黙の前提

1. 最後のイベントが assistant の tool_use のまま時間が経つ ＝ ツール実行中 or **権限ダイアログ待ち**（75秒閾値）
2. 最後が assistant の text ＝ ターン終了（人間待ち）
3. 最後が user の tool_result ＝ モデルが思考中
4. 末尾 80KB だけ読めば直近状態の判定には足りる

## 変更履歴

- 2026-07-08: 初版（v1.4.2 時点の観測を記録）
- 2026-08-26: 状態ブロック行（custom-title / ai-title / agent-name 等）を追記（R85-1 リネーム表示）
