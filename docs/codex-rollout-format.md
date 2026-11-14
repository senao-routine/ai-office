# Codex rollout 形式の観測台帳

> この JSONL は Codex CLI の非公式な内部形式であり、公開 API ではない。フィールドや階層は予告なく変わりうる。
> リソースモニターで Codex カードだけが `unavailable` になった場合や、スマホのセッション画面で Codex の行が出なくなった場合は、
> まず rollout 形式の変更を疑う。

## 2026-07-14 に観測した形

ローカルの `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` 1本を読み、値・会話本文・識別子・実パスを
記録せず、次のフィールド名と入れ子だけを確認した。

- 1行は `timestamp`、`type`、`payload` を持つ JSON オブジェクト。
- セッション情報行はトップレベル `type: "session_meta"` で、`payload.cli_version` などを持つ。
- 使用量行はトップレベル `type: "event_msg"`、`payload.type: "token_count"` の形を取る。
- レート制限は `payload.rate_limits` にあり、観測キーは次のとおり。
  - `limit_id`、`limit_name`、`plan_type`
  - `primary`: `used_percent`、`window_minutes`、`resets_at`
  - `secondary`: `null` または primary と同じ3フィールドを持つオブジェクト。使用中でも null のことがあるため、表示時は走査した直近の非null値を採用する。
  - `credits`、`individual_limit`、`rate_limit_reached_type`
- セッション累積トークンは `payload.info.total_token_usage` にあり、
  `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`、`total_tokens` を持つ。
- 同じ token-count 行に `payload.rate_limits` と `payload.info.total_token_usage` が共存しうる。

## 合成 fixture

`tests/fixtures/codex_rollout*.jsonl` は 2026-07-14 に、Codex CLI `0.144.1` 相当の上記観測形を基に
手書きした完全な合成データである。実セッションの値・文字列・ID・パスは含めていない。
（ディレクトリ `tests/fixtures/codex_rollout/` は別物＝下の R113-B 節の匿名化フィクスチャ。）

## 2026-10-08（R113-B）: 会話の行（スマホのセッション画面）に使う形

本人裁定（2026-10-08）で、スマホの「セッション画面」（E2EE 封書 `kind:"term"`・dialogRelay が ON のときだけ）に Codex のやり取りも載せる。
読むのは `server/source_codex_rollout.py` だけ（一覧とゲージは従来どおり `source_codex` が SQLite のメタデータだけを読む）。
この節の数字は、この Mac の rollout 全部（`~/.codex/sessions` 5,601 本＋`archived_sessions` 6 本）を**型の名前・キーの名前・件数だけ**数えたもの
（本文・パス・id は記録していない。件数は正規表現で数えた概数）。

### rollout_path（SQLite）

- `~/.codex/state_5.sqlite` の `threads` 表: `id`（UUID・office の session は `cx-<id>`）・`rollout_path`（絶対パス）・`archived`・`cli_version`・
  `history_mode`（観測は全部 `paginated`）ほか。`title`・`first_user_message`・`preview` は本文の列＝読まない。
- `rollout_path` は 5,600 本が `~/.codex/sessions/YYYY/MM/DD/rollout-<時刻>-<id>.jsonl`（archived=0）、6 本が `~/.codex/archived_sessions/`（archived=1）。
- 読み手は `mode=ro`＋`PRAGMA query_only=1` で `SELECT rollout_path FROM threads WHERE id = ?` だけを投げ、`resolve()` した実パスが
  この 2 か所の配下の `rollout-*.jsonl`（通常ファイル）でなければ読まない（DB の書き換え・`..`・外へのリンクを拒否）。

### 1 行の形と型の一覧

1 行＝`{timestamp, type, payload}`（新しい版は `ordinal`・`metadata` も持つ）。上の階層の `type`:

| type | 件数 | 会話の行に使うか |
|---|---|---|
| `response_item` | 196,099 | 使う（下の変換表） |
| `event_msg` | 157,663 | 一部（下の変換表） |
| `token_usage_record` | 26,272 | 使わない（0.153 以降） |
| `turn_context` | 6,122 | 使わない |
| `session_meta` | 5,808 | 使わない（`cli_version`・`source` の記録） |
| `world_state` | 5,608 | 使わない（0.144 以降） |
| `inter_agent_communication_metadata` | 2,928 | 使わない（サブエージェント同士） |
| `compacted` | 27 | `sys`（会話を圧縮しました） |
| `realtime_item` | 2 | 使わない |

`response_item` の `payload.type`: reasoning 57,733 / message 37,886 / custom_tool_call 35,775 / custom_tool_call_output 35,766 /
function_call 12,902 / function_call_output 12,895 / agent_message 2,928 / web_search_call 108 / tool_search_call 47 / tool_search_output 47 /
image_generation_call 12（`result` に画像の base64＝使わない）。

`event_msg` の `payload.type`: item_completed 85,920 / token_count 58,531 / task_started 6,605 / task_complete 6,085 /
thread_settings_applied 504 / turn_aborted 17 / thread_goal_updated 1。
`item_completed` の `item.type`: Reasoning 29,746 / CommandExecution 22,377 / AgentMessage 13,839 / UserMessage 5,790 / Extension 3,840 /
FileChange 3,552 / SubAgentActivity 3,128 / McpToolCall 1,413 / ImageView 706 / EnteredReviewMode 574 / ExitedReviewMode 563 /
WebSearch 216 / CollabAgentToolCall 147 / ContextCompaction 28 / FunctionCallOutput 1。

### 版の違い（threads.cli_version ごと）

- **0.42〜0.46（旧版）**: `function_call` の `shell`（arguments＝JSON 文字列 `{command: [argv], workdir}`）・`update_plan`（`{plan: [{step, status}]}`）、
  `custom_tool_call` の `apply_patch`（input＝パッチの本文 `*** Begin Patch` …）。`*_output.output` は JSON 文字列 `{output, metadata: {exit_code, duration_seconds}}`。
  `task_complete` に `duration_ms` が無い。
- **0.72**: `function_call` の `shell_command`（`{command: 文字列, workdir}`）。
- **0.141〜0.144**: `function_call` の `exec_command`（`{cmd, …}`）・`write_stdin`。出力は文字列 `Chunk ID…／Process exited with code N／Output:…`。
  message に `phase`（0.142 から `id` も）。MCP の道具は `function_call`（`namespace` 付き）＋同じ id の `McpToolCall`。
- **0.144 以降（新版）**: `custom_tool_call` の `exec`（input＝**JS のコード文字列**）。出力は部品の並び `[{type: input_text|input_image, text}]`
  （1 行目が `Script completed` / `Script failed` / `Script error:`）。
- **0.153 以降**: `item_completed` の `CommandExecution`（`command: [zsh, -lc, 本文]`・`aggregated_output`・`exit_code`・`status`）・
  `FileChange`（`changes: {パス: {type: update|add|delete, unified_diff | content, move_path}}`）。マルチエージェント
  （`spawn_agent`・`send_message`・`followup_task`・`wait_agent`・`agent_message`・`SubAgentActivity`）。
- **item_completed の無い rollout は 24 本だけ**で、全部 `session_meta`＋1 行以下（道具を含むものは無い）。0.46 の rollout にも
  `item_completed` がある＝新しい CLI が書き直している。旧 CLI の形はフィクスチャ `no_item_completed.jsonl`（下記）で再現する。

### 同じ出来事の写し（片方だけを使う）

- `item_completed.AgentMessage` は `response_item` の assistant の message と**同じ id・同じ本文**（直近 400 本で 1,097/1,097・隣り合う行）。
- `item_completed.UserMessage` は `response_item` の user の message から、部品ごとに `<…` で始まる注入（`environment_context`・
  `recommended_plugins`・`image` の枠・`user_action`…）と `# AGENTS.md instructions` を落とした残りと一致（直近の対話 600 本で 175/175。
  UserMessage にだけあるものは全部 `<` で始まる）。
- `item_completed.Reasoning` は `response_item.reasoning` の写し（`response_item` 側の方が多い）。
- `McpToolCall` の id は 328 件が `function_call`（`js`・`namespace` mcp__…）の `call_id` と一致（写し）、113 件は一致なし（exec の中から呼んだもの）。
  見分けは id の形だけ（R113-B-S3・状態なし＝窓をまたいでも二重にならない）: exec の中から呼んだもの＝`exec-…`（手元の rollout 5,611 本で 742/742）、
  function_call の写し＝`call_…`（＝その call_id・660/660）。item_completed からは `exec-…` だけを出し、`call_…` と知らない形は出さない（2026-10-08 実測）。
- `CommandExecution`・`FileChange` の id は `call_id` と一致しない（exec の中の出来事＝response_item には無い）。

差分の取得は数秒おきに行の途中で区切られる＝窓をまたいだ写しは状態なしでは突き合わせられない。だから**どの版にもある response_item 側だけ**を
使い、item_completed からは response_item に無いもの（CommandExecution・FileChange・写しでない McpToolCall）だけを使う。

### 変換表（Codex → 端末の行・`office_term.KINDS`）

| Codex | 行 |
|---|---|
| `response_item.message` role=user（注入の部品を落とした残り） | `u` |
| `response_item.message` role=assistant | `a` |
| `response_item.message` role=developer・`response_item.agent_message` | 出さない |
| `response_item.reasoning`（続いた分をまとめる） | `th`（`n`＝件数・本文も encrypted_content も載せない） |
| `function_call` shell／shell_command／exec_command／local_shell | `tool` n=`Shell`（`[sh, -lc, 本文]` は本文・伏字・ARG_MAX） |
| `custom_tool_call` exec | `tool` n=`exec`（SHOW_EXEC_INPUT＝True: input をそのまま伏字＋上限・False: 名前だけ） |
| `apply_patch`（custom／function） | `tool` n=`apply_patch`（触ったファイルの basename だけ） |
| `update_plan` | `todo`（status: completed→c・in_progress→i・他→p）・その出力は出さない |
| `spawn_agent`／`js`／`view_image` | `tool`（task_name／title／path の basename） |
| その他の function_call | `tool`（名前だけ・引数は載せない）・名前の無いものは出さない |
| `web_search_call` | `tool` n=`WebSearch`（検索語は載せない） |
| `*_output` | `res`（`for`＝call_id の印・exit_code≠0／1 行目が `Script failed`・`Script error`／文字列の出力の `Process exited with code N≠0`／success=false は `err`・画像だけは `img`） |
| サブエージェントの道具（spawn_agent・wait_agent・send_message・followup_task・list_agents…）の `*_output` | `res` の要約だけ（spawn＝裏で動いています・timed_out＝待ち時間切れ・他＝完了）＝報告の本文は載せない（Claude 側の Task の結果と同じ）。呼び出しが窓の外でも結果の形（timed_out・task_name・agents・submission_id）で当てる |
| `item_completed.CommandExecution` | `tool` n=`Shell`＋`res`（aggregated_output・exit_code≠0 か status failed は `err`） |
| `item_completed.FileChange` | ファイルごとの `res`（update＝`office_term._diff_rows` の差分・add＝先頭 6 行・delete＝削除） |
| `item_completed.McpToolCall`（写しでないもの＝id が `exec-…`） | `tool` n=`サーバー.道具`（引数は載せない）＋`res`（isError／failed は `err`） |
| `item_completed` の AgentMessage／UserMessage／Reasoning | 出さない（写し） |
| `item_completed` の SubAgentActivity／CollabAgentToolCall／その他 | 出さない |
| `event_msg.task_complete`（`duration_ms` があるとき） | `turn` |
| `event_msg.turn_aborted` | `int` |
| 上の階層の `compacted` | `sys` |
| 知らない type／payload・壊れた行 | 黙って無視 |

上限は office_term の定数（人 800 字・AI 1600 字・道具の引数 200 字/4 行・結果 600 字/8 行・差分 16 行×120 字・Todo 12 件）。

### 匿名化フィクスチャ（tests/fixtures/codex_rollout/）

`python3 tools/capture_fixture.py --kind codex_rollout <実 rollout> <出力名> --start 0 --limit 200 [--drop-items]` で切り出した
（本文は「先頭 12 字＋長さ」・`<タグ` と `# AGENTS.md instructions` の印だけ頭に残す・id は窓の中の位置で決まる固定ダミー・パスは basename・
cwd は `/Users/test/demo/project`・実の日時は 2026-01-01 に置き換え・encrypted_content は `gAAAA-REDACTED`）。
切り出した後に grep（ホームのパス・アカウント名・UUID・長い 16 進・メール・実の日時）と目視と `ng_check.sh` を通してから保存した。

- `legacy_function_call.jsonl`（0.46・70 行）: function_call shell×3／update_plan×2・custom_tool_call apply_patch×2・item_completed の写しつき
- `no_item_completed.jsonl`（49 行）: 同じ rollout を `--drop-items` で切り出した＝item_completed の無い CLI の形（行は legacy と同じになる）
- `exec_custom_tool_call.jsonl`（0.159・67 行）: custom_tool_call exec×6・CommandExecution×3・McpToolCall×6（失敗 1）・FileChange×1・
  spawn_agent・agent_message・SubAgentActivity・developer／AGENTS.md の注入・task_complete（duration_ms）

門= `tests/test_source_codex_rollout.py`（3 系統の行の件数・種類・伏字・写しの二重なし・閉じ込め・SQLite の読み取り専用・SHOW_EXEC_INPUT の両分岐）。
