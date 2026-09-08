# office_json v2 と中継 allowlist（正本・R90）

`GET /api/office` が返す JSON の正本は `server/office_server.py::scan_office()`。
デスクトップは `ui/core/world.js::buildWorld`、PWA は `ui/pwa/app.js` と `ui/pwa/boot3d.js`、MCP は `server/mcp_office.py` が消費します。
PWA の配布用 HTML は `relay/src/app_html.js` に生成し、Worker から配信します。

**`v: 2` は `sources` / `events` / `growth` を持つ現在の office スキーマです。**
`employees` / `roster` など従来のキー名は互換のため維持しています。無料/Pro などの版分けフィールドはありません。
履歴上の `edition` は R84 の全機能無料化後、R90-U7 でコード・設定参照・中継から撤去しました。
hook 記録行や署名封筒にも `v` がありますが、office_json とは別の版番号です。

中継前に `server/relay_agent.py::_redact_office_for_relay()` が allowlist を適用します。
追加しただけのフィールドは中継に載りません。会話本文・ローカルパスのフィールドは除去しますが、
質問・選択肢・表示名・一行要約・定型文・処理済み実行結果は用途に応じて通します。「文字列を一切送らない」契約ではありません。

## トップレベル

| キー | ローカルで返す形・意味 | 中継 |
|---|---|---|
| `v` | 整数 `2` | ○ |
| `officeName` `lang` `avatarMode` `generatedAt` | 表示名・言語・表示単位・生成時刻（epoch秒） | ○ |
| `employees[]` | 1セッション=1件。Claude / Codex / OpenClaw を統合 | entry allowlist |
| `roster[]` | 表示単位。`avatarMode=session` が既定、`project` ではプロジェクト集約 | entry allowlist |
| `counts` | `{working, waiting, resting}`。`employees` の状態別件数 | ○ |
| `tasks` | `roster` のタスク集計（`pending, inProgress, completed`） | ○ |
| `today` | `{sent, lastSentAgo, capped}`。保持する送信履歴のうち今日の件数・最終送信からの秒数（未送信は null）・台帳上限到達 | ○ |
| `setup` | `{hookInstalled, eventsWired}`（bool） | ○ |
| `sources` | ソース別の接続・件数・診断。下表参照 | `codex.reason` を除去 |
| `events` | `{seq, wired, lastTs}`。イベント収集のメタデータ | `{seq, wired}` のみ |
| `growth` | `{byProject, office, streak15}`。XP・レベル・内訳 | 内訳を除去。下表参照 |
| `actions` | `{recipes, results, caps}`。許可レシピと実行結果 | 生産者側で公開用に整形した値を通す |
| `relay` `res` | 中継使用率・リソース枠の要約（未設定・取得不可時などは null）。USD・email・account を含まない | ○ |
| `templates` | 保存した定型文（label/text）。最大8件・各120字 | 意図的に通す |
| `launchable[]` | `{projectId, name, ageSec}`。起動先候補、パス無し | ○ |
| `history[]` | 直近の指示と配達状況。指示全文を含む | 空配列に置換 |
| `timeline` `digest` その他 | `scan_office()` のトップレベルには無い。時間軸・ダイジェストは別API | allowlist 外は除去 |

## `sources`

| フィールド | 意味 | 中継 |
|---|---|---|
| `claude.fg` `claude.bg` | 表示対象の対話 / バックグラウンドセッション件数 | ○ |
| `claude.agentsCli` | `claude agents --json` 由来の索引が空でないか（bool） | ○ |
| `codex.connected` `codex.n` | SQLite ソースの接続状態と表示件数 | ○ |
| `codex.reason` | 空文字、`disabled`、`missing`、`locked-or-schema` などのローカル診断 | 除去 |
| `openclaw.connected` | OpenClaw ソースの接続状態（bool） | ○ |

Codex は `~/.codex/state_5.sqlite` と `~/.codex/thread_history_1.sqlite` の非本文列を
`mode=ro` / `query_only` で参照します。親スレッドを表示し、子は `minions` に集計します。
`source == "exec"` の表示窓は30分（`EXEC_WINDOW`）、対話（`cli` / `vscode` 等）は3時間（`SHOW_WINDOW`）。
回帰は `test_sources_codex.test_exec_threads_use_the_short_window`。

履歴（2026-09-07 実測）: 直近3時間の thread 91件中77件が exec、最終更新の中央値86分でした。
対話と同じ窓で終了済みの仕事が残る問題を受け、表示窓を分離しました。この数値は当時の観測で、現在の件数ではありません。

## `events`

| フィールド | 意味 | 中継 |
|---|---|---|
| `seq` | `office_events` の最新シーケンス。未収集時は0 | ○ |
| `wired` | 記録フックの配線確認。`setup.eventsWired` と同値 | ○ |
| `lastTs` | 直近イベントの epoch秒。無ければ0 | 除去 |

ここにはイベント本文や配列を入れません。更新通知は `GET /api/events`（SSE）、保存した時間軸は
`GET /api/timeline`、留守中の集計は `GET /api/digest`、確認済み時刻の更新は `POST /api/seen` です。
記録元は `hooks/office-event.sh`、保存先は `~/.claude/office_timeline.sqlite`。記録フックは本文を書きません。

## `growth`

| フィールド | ローカル | 中継 |
|---|---|---|
| `byProject[projectId]` | `{xp, level, breakdown}` | `{xp, level}` |
| `office` | `{xp, level, breakdown, nextAt}` | `{xp, level, nextAt}` |
| `breakdown[key]` | `{count, xp}`。以下の寄与をすべて返す | 除去 |
| `streak15` | 回答待ち中央値が15分未満の継続日数。最初の観測日以降、回答の無い日も継続扱い | ○ |

`breakdown` のキーと倍率は `office_timeline._RATES` が正本です。

| キー | 数えるもの | XP |
|---|---|---|
| `tasksDone` | 完了タスク | 件数 × 10 |
| `commits` | 記録されたコミット | 件数 × 5 |
| `asksAnswered` | 解消した質問・承認待ち | 件数 × 2 |
| `fastAnswers` | 5分以内に解消した待ち | 件数 × 1（通常回答と合計3） |
| `activeMin` | 稼働分 | 分 × 0.2 |
| `hires` | 雇用 | 件数 × 5 |
| `turnsCompleted` | Codex の完了ターン | 件数 × 3 |

`xp` は内訳の和、`level = floor(sqrt(floor(xp / 100)))`、`office.nextAt = 100 × (level + 1)²`。
**トークン量は XP に含めません。** Mac の表示は内訳を開示し、中継で省かれた内訳をPWAで捏造しません。
間取りを決める同時稼働の最大値は `ui/platform/growth.js` が保持する別の状態で、`growth` のフィールドではありません。

## `actions`

| フィールド | 実際のキー・意味 | 中継 |
|---|---|---|
| `recipes[]` | `{id, label, dangerous, returnOutput}`。`office_actions.recipes_public()` で生成 | ○。argv/cwd/env は含めない |
| `results[]` | `{reqId, kind, recipe, label, state, startedAt, durationMs, exitCode, bytes, output}`。あれば `bgId` も含む | ○。output は生産時と `results_public()` の両方で scrub |
| `caps` | `{actions: 1, ws: 1}`。実行・WS経路の能力を示す | ○ |

`recipes_public` / `results_public` は Python 関数名であり、JSONキーではありません。
レシピが無ければ実行UIは非表示です。`POST /api/hire` の起動結果もこの結果台帳で扱います。
遠隔雇用は `remoteHire:true` のときだけ許可され、既定はOFFです。

## セッション entry（`employees[]` / `roster[]`）

| キー | 中継 | 備考 |
|---|---|---|
| `session` `projectId` `crew` `sessions` `external` `site` `vendor` | ○ | 識別。projectId はパスを含まないハッシュ等の識別子 |
| `sessions[]` | `_ALLOW_SESSION` のみ | `session, state, age, attention, minions, pending, vendor` |
| `state` `kind` `verb` `age` `mtime` `minions` `pending` `listening` `attention` `approvalMin` `stuckTool` | ○ | 状態・活動の種別 |
| `question` `questionOptions` | ○ | 質問文と選択肢（回答のために通す） |
| `ask` | `{tool, kind}` のみ | `title`（コマンド全文など）は除去 |
| `feed` | scrub | 発言行を除去、パスは basename、URL はホスト、60字 |
| `work` | sanitize | now/next/done 計8件、パス縮約、60字、counts |
| `skills` `dept` `role` `disp` `name` | ○ | 表示用の名前・分類 |
| `arch` | ○ | アクセサリ上書き（9種の識別子または null） |
| `title` | 既定で通す | `/rename` の表示名。`OFFICE_RELAY_TITLES=0` で空文字 |
| `detail` | 既定で通す、200字 | Claude bg の一行要約。`OFFICE_RELAY_DETAIL=0` で空文字 |
| `lastSaid` `target` `lastOrder` `cwd` `branch` | キーだけ残し空文字 | 本文・パス等 |
| `bg` `pid` `tokens` `homeCwd` `evSeq` その他 | 除去 | allowlist 外はローカル専用 |

## 変更手順

1. `scan_office()` と本表を更新する。
2. 中継に載せるなら `relay_agent._ALLOW_ENTRY` / `_ALLOW_TOP` / `_ALLOW_SESSION` と必要な刈り込みを更新する。トップレベルが許可済みでも、新しいネスト値が安全とは限らない。
3. `tests/test_relay_agent.py` に載る/載らないのピンを追加する。
4. `AllowlistRedactionTest.test_allowlist_covers_ui_and_pwa_field_reads` で消費側の参照集合を検査する。ただし現行の静的検査は `ui/core/world.js` と `relay/src/worker.js` を読み、移設済みのPWA本文（`ui/pwa/app.js` / `relay/src/app_html.js`）は直接読まない。green だけでPWA全フィールドを網羅したと判断せず、実際の消費側と照合する。
