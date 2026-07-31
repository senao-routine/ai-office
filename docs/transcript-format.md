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

## 推定ロジックが依存している暗黙の前提

1. 最後のイベントが assistant の tool_use のまま時間が経つ ＝ ツール実行中 or **権限ダイアログ待ち**（75秒閾値）
2. 最後が assistant の text ＝ ターン終了（人間待ち）
3. 最後が user の tool_result ＝ モデルが思考中
4. 末尾 80KB だけ読めば直近状態の判定には足りる

## 変更履歴

- 2026-07-08: 初版（v1.4.2 時点の観測を記録）
