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
  `questions[]（AskUserQuestion: {question, header, options[{label, description}], multiSelect}・1〜4 問）`
  （掲示は 1 問目しか持たない＝問いの数と複数選択は transcript の未回答の tool_use から数える＝`ask.qn`/`ask.multi`）
- スキル実行の観測形式:
  - assistant の `message.content[]` に `{type:"tool_use", name:"Skill", input:{skill:"x-post"}}`
  - user の `message.content` に `<command-name>/x-post</command-name>`（スラッシュ付きのコマンド名）
  - AI Office はイベントの `timestamp` を基準に直近30分だけを採用し、表示用に英数字・`_`・`:`・`-` 以外を除去する
- サブエージェント: `projects/<dir>/<sessionId>/subagents/**/*.jsonl`（Workflowは `subagents/workflows/wf_*/`）
  （mtime 15 分以内＝`minions`・60 秒以内＝`minionsLive`。終わった子の jsonl は書かれなくなるだけで残る）
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
- `{"type":"ai-title","aiTitle":"…"}` — Claude Code の自動生成タイトル（Claude アプリの会話名と同じ）。ほぼ全セッションに存在し
  **リネーム後も更新され続ける**ため `title` には混ぜない（混ぜると全アバターが自動生成文になる）。
  R123-A（2026-11-09）から別の欄 `aiTitle` として運び、一覧の名前は `title`（/rename）→ `aiTitle` → プロジェクト名の順
  ＝名前の無いセッションだけが自動タイトルで見分けられる（組の見出しと行の名前が同じ字になっていた）。部屋の名札は今のまま。
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

## ターミナル画面の行（R113-B・2026-10-08観測・`server/office_term.py`）

- `cwd` は各行にある（無い行は直前の行の `cwd` を引き継ぐ）＝Read/Edit/Write/NotebookEdit/Grep/Glob のパスは `os.path.relpath(path, cwd)` で表示し、
  `cwd` が分からない・`..` 始まり（プロジェクトの外）・`~` 始まりは basename へ落とす（組み立てた文字列も伏字に通す）。
- 考え（assistant の `{type:"thinking"}`／`{type:"redacted_thinking"}`）は本文を持たず、次の見える行の直前（とページの終わり）まで続いた分を
  1 行 `{k:"th", n:ブロックの数, chars:thinking の字数の合計}` に数える（redacted は 0 字・時刻は考え始め・遡りや差分の切れ目では 2 行に分かれる）。
- Task/Agent の結果の `toolUseResult.agentId`（英数 17 字）→ 子 transcript `<project>/<session>/subagents/**/agent-<agentId>.jsonl`
  （子の行は全部 `isSidechain: true`・横に `agent-<id>.meta.json`）。発見規則は `office_term.subagent_files`（minions と共有）。

## 止まった理由の行（R102-4・2026-09-30観測）

- **合成のエラー行**: API エラー・利用上限・会話の長さの上限でターンが落ちると、assistant の行が `"isApiErrorMessage": true`・
  `message.model: "<synthetic>"`・本文 1 つの text（例「You've reached your … limit. Switch to another model…」「API Error: 529 … overloaded」
  「Prompt is too long」）で書かれる。このときフックは **StopFailure** を出し、**Stop は出さない**（実測: StopFailure 58 件の後に Stop は 0 件）
  ＝受信待機（Stop フック）が走らない＝スマホの言葉は届かない。`parse_session` は最後の行がこれなら `err={k: limit|ctx|api, ago}`
  （本文は持たない）・状態は waiting。フィクスチャ= `tests/fixtures/api_error_{limit,overloaded,context}.jsonl`。
- **中断（Esc）**: user の行の text が `[Request interrupted by user]`／`[Request interrupted by user for tool use]`（道具の途中なら直前に
  is_error の tool_result「The user doesn't want to proceed…」）。Stop フックは走らない。`parse_session` は最後の行がこれなら
  `interruptedAt`（ローカル専用）＝フックのイベントで追うターン（`office_events.turn_live`）を閉じたものとして読む。
  フィクスチャ= `tests/fixtures/interrupted_tool.jsonl`。

## 推定ロジックが依存している暗黙の前提

1. 最後のイベントが assistant の tool_use のまま時間が経つ ＝ ツール実行中 or **権限ダイアログ待ち**（75秒閾値）
2. 最後が assistant の text ＝ ターン終了（人間待ち）
3. 最後が user の tool_result ＝ モデルが思考中
4. 末尾 80KB だけ読めば直近状態の判定には足りる
5. （R102-4）transcript は道具の実行中・長い考えの間は止まって見える＝フックのイベントが配線されていれば、ターンが開いている間
   （UserPromptSubmit／本体の道具〜Stop・StopFailure）は作業中と読む（本体の道具は 30 分・道具の外は最後のイベントから 10 分まで）

## 変更履歴

- 2026-07-08: 初版（v1.4.2 時点の観測を記録）
- 2026-08-26: 状態ブロック行（custom-title / ai-title / agent-name 等）を追記（R85-1 リネーム表示）
- 2026-11-09: ai-title を別の欄 aiTitle として採用（R123-A・一覧の名前の 2 番手・中継には載せない）
- 2026-09-30: 合成のエラー行（isApiErrorMessage）と中断の行・ターンの追跡（R102-4）
- 2026-10-08: ターミナル画面の行の cwd・考えの数え方・子 transcript の agentId（R113-B）
