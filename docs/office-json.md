# office_json v2 と中継 allowlist（正本・R90）

`GET /api/office` が返す JSON の正本は `server/office_server.py::scan_office()`。
デスクトップは `ui/core/world.js::buildWorld`、PWA は `ui/pwa/app.js` と `ui/pwa/bootpx.js`（R99-P4-6 で部屋をドット絵へ）、MCP は `server/mcp_office.py` が消費します。
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
| `app` | `{version}`＝**動いているコードの版**（R97-D・正本= `server/office_version.py`）。`v` はデータ形式の版で別物。 | **×**（新フィールドは既定で載せない） |
| `officeName` `lang` `avatarMode` `generatedAt` | 表示名・言語・表示単位・生成時刻（epoch秒） | ○ |
| `employees[]` | 1セッション=1件。Claude / Codex / OpenClaw ＋出勤の口の Bot（R126・`vendor:"bot"`・下の「Bot の行」）を統合 | entry allowlist（**Bot の行は Bot だけの狭い allowlist**＝下の「Bot の行」・R127） |
| `roster[]` | 表示単位。`avatarMode=session` が既定、`project` ではプロジェクト集約（Bot はどちらでも 1 体 1 組） | entry allowlist（Bot の組は Bot だけの狭い allowlist・`sessions[]` は載らない） |
| `counts` | `{working, waiting, resting}`。`employees` の状態別件数（Bot を含む） | ○（Bot を含む＝Mac と同じ。R126 は落とした Bot の分を引いていた） |
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
| `macActive` | R102-5: Mac の前に人がいる（キーボード・マウスの無操作 120 秒未満＝1・以上＝0）。`ioreg` の HIDIdleTime を 5 秒ごと・読めなければキーごと無し | 0/1 の整数だけ（Worker はこれが 1 の間スマホへ通知しない） |
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
| `door.ok` `door.bots` `door.n` `door.asks` | R126 出勤の口: 台帳が読めたか（bool・台帳が無いのは true）・登録した Bot の数・いま載せた行の数・開いている相談の数（整数だけ）。口の例外は握って `ok:false` | 除去（中継の sources の表に無い） |

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

> R97: `growth.maxSeen` は「**同時に居たセッション数の最大値**」で、オフィスの広さ（机の数＝ティア
> S≦8 / M≦12 / L≦18 / XL）を決める。Mac が `~/.claude/office_peak.json` に覚える（**増える方向にしか動かない**）。
> 以前はブラウザの localStorage にしか無く、別のブラウザで開く・保存領域が消えるだけで机が消え、
> 壊れたように見えた。中継にも載せる（スマホも別ブラウザ＝同じ理由で縮むため・載るのは整数ひとつ）。


| フィールド | ローカル | 中継 |
|---|---|---|
| `byProject[projectId]` | `{xp, level, breakdown}`。**いま名簿に居る分だけ**（roster の projectId・session と employees の session に当たる鍵・R123-C） | `{xp, level}`（同じく居る分だけ） |
| `office` | `{xp, level, breakdown, nextAt}` | `{xp, level, nextAt}` |
| `breakdown[key]` | `{count, xp}`。以下の寄与をすべて返す | 除去 |
| `streak15` | 回答待ち中央値が15分未満の継続日数。最初の観測日以降、回答の無い日も継続扱い | ○ |

`breakdown` のキーと倍率は `office_timeline._RATES` が正本です。

> R123-C: `office_timeline.growth_json` は timeline に一度でも出た全プロジェクト（session 単位の既定では 1 セッション 1 件）を返し、
> そのまま載せると使うほど太る（2026-11-09 実測 4,682 件＝office_json 1.66 MB 中 1.59 MB・中継の荷物 231 KB 中 204 KB）。
> office_json は `office_server.present_growth` で居る分だけに絞る（`office` と `streak15` は全件から計算したまま・キャッシュの dict は書き換えない）。
> 全件の内訳が要る読み手は `/api/digest`・`growth_json` を直接使う。

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
| `approve` | `{on, until, fps, enr, rk}`。rk＝Mac で［延長］が押されて 24 時間の窓が開いている端末 ID（R116-S1b・秘密でない・上限 8・スマホは自分の ID があれば新しい秘密を封書で求める）。on＝スマホから許可が ON か・until＝ON の終了時刻（epoch 秒・整数・R103-1。R118 から ON は期限なし＝4102444800〔2100-01-01〕）・fps＝有効な許可鍵の照合コード（秘密でない・R118 から PWA は見ない）・enr＝Mac の受付が開いている端末 ID（R102-9。R103-1 からは受付が無くてもスマホだけで登録できる） | ○ |
| `caps` | `{actions: 1, ws: 1, dialog: 0/1}`。実行・WS経路・**会話ビューア（R87 封書）**の能力。`dialog` は「封じられる Mac（CommonCrypto の AES-256-GCM が使える）」かつ「本人が `dialogRelay` を ON」のときだけ 1 | ○ |

**スマホから許可（`apv-` の封筒）の方式（R114・本人裁定 2026-10-08「ON の間はタップだけ」→R118・本人裁定 2026-10-13「段を問わず 1 タップ・常に ON」）**: 本文 `{"aioffice":1,"kind":"approve","reqId","session","askId","tool","inputSha","iat","decision":"allow"}` の
`mode` で段が分かれる。省略＝`"sig"`（R100-1b からの挙動・WebAuthn の `credId`/`authData`/`clientDataJSON`/`signature` が必須）。`"tap"`＝WebAuthn 無し
（端末のペアリング鍵の HMAC 封筒だけ。R118 から PWA が送るのは tap だけ・ON も OFF も `rst-` の封筒だけ）。daemon（`office_approve.approve`）は tap でも
封筒の HMAC と nonce・形式・reqId の冪等・ON の窓・dialog_seal・ペアリングが有効な端末（許可鍵が登録済みならその範囲）・掲示・seen の 3 点一致と 180 秒・
ask/input の一致・used・rate・危険判定を 1 つも省かず、省くのはパスキーの検証（credId の一致・clientData・authData・clone・署名）だけ。

| 段（決定の瞬間の再判定・掲示の時点） | `sig`（Face ID） | `tap`（タップ） |
|---|---|---|
| remote | 許可 | 許可 |
| review | 許可（全文を読んで 2 段） | 許可（全文を読んでタップ） |
| mac（全文あり） | 許可（全文確認＋Face ID） | 許可（R118。R114〜R117 は `tap-mac` で拒否していた） |
| 判定できない形（全文なし） | Mac だけ | Mac だけ |

hook への回答（`<session>.<askId>.reply.json`）には tap のときだけ `"mode":"tap"` が付く。hook は R114〜R117 では自分の再計算か掲示の段が remote/review で
なければ `tap-mac` で deny していた（R118 で撤去・決定の瞬間に掲示より危険な段なら `dangerous`、判定できない形なら deny は今までどおり）。監査（daemon の `office_approvals_audit.jsonl`・hook の `office_approvals_hook.jsonl` の
remote の decide）と daemon の `results[reqId]`＝`{state, reason, ts, mode}` に方式が残る。`actions.results[]`（公開ビュー）の形は変えない。

`recipes_public` / `results_public` は Python 関数名であり、JSONキーではありません。
レシピが無ければ実行UIは非表示です。`POST /api/hire` の起動結果もこの結果台帳で扱います。
遠隔雇用は `remoteHire:true` のときだけ許可され、既定はOFFです。

## セッション entry（`employees[]` / `roster[]`）

| キー | 中継 | 備考 |
|---|---|---|
| `session` `projectId` `crew` `sessions` `external` `site` `vendor` | ○ | 識別。projectId はパスを含まないハッシュ等の識別子 |
| `sessions[]` | `_ALLOW_SESSION` のみ | `session, state, age, attention, minions, pending, vendor` |
| `state` `kind` `verb` `age` `mtime` `minions` `pending` `listening` `attention` `approvalMin` | ○ | 状態・活動の種別。`minions` は `<session>/subagents/**/*.jsonl` のうち 15 分以内に書いた数＝**終わった子も 15 分数える**（作業中の根拠には使わない） |
| `minionsLive` | 0 以上の整数だけ（ほかの形はキーごと除去） | 直近 60 秒（`MINIONS_LIVE`）に書いたサブエージェントの jsonl の数＝いま裏で走っている子。roster は members の合計。「作業中」の根拠はこちら（`minions>0` で作業中と出すと、終わって指示待ちのセッションが 15 分「作業中」に見えた） |
| `tool` | ×（allowlist に無い＝中継に載せない） | 最後に呼んだ工具の名前（`Bash`・`Edit`・`Read`・`Grep`・`WebFetch`・`TodoWrite` …・40 字まで・無ければ `""`）。本文ではなく分類。部屋の「用事」（Read/Grep→本棚・WebFetch/WebSearch→掲示板・TodoWrite→ボード・Bash/Edit→机で画面と打鍵）の根拠（R110）。`verb` は表示用の訳語で言語に依るので、判定は `tool` を先に見る |
| `stuckTool` | ○（縮約） | 承認まちのツール（`"{verb} {target}"`）。target と同じ本文を含むので feed と同じ縮約（パスは末尾名・URL はホスト・60 字）を掛けて載せる（R99-P4-0） |
| `question` `questionOptions` | ○ | 質問文と選択肢（回答のために通す） |
| `ask` | `{tool, kind, remote, min?, qn?, multi?, id8?, questions?}` のみ（`relay_agent._ALLOW_ASK` の表。remote＝hook の危険判定が「スマホから許可できる形」と言ったか・0/1。R102-2 から remote（1 段）と review（全文を読んで 2 段）の両方が 1・段と警告のタグは封書の中だけ。min/qn/multi は整数だけ・形が違えばキーごと除去。questions は `office_server.clean_ask_questions` と同じ検証を通ったものだけ・`kind:"question"` かつ `tool:"AskUserQuestion"` のときだけ） | `title`（コマンド全文など）は除去。全文は登録した端末へ封書 `kind:"ask"` で。`min`＝掲示されてからの分（最初の 1 分は 0・質問とプランは `approvalMin` が立たないのでこちらで待ち時間を出す）。`qn`＝AskUserQuestion の問いの数（1 問なら 1）・`multi`＝どれかの問いが `multiSelect:true` なら 1。qn/multi は承認フックが掲示に書く（R102-11・推測しない）。旧い hook の掲示だけ、1 問目の文で transcript の未回答の AskUserQuestion と照合する（NFC でそろえる・照合できなければキーごと無し）。`id8`＝掲示の askId の先頭 8 桁（hook の乱数・本文ではない）。スマホは要求に答えるとき、表示していた要求の id8 を添えた本文 `{"aioffice":1,"kind":"answer","ask":id8,"text":…}` を署名して送り、relay_agent は id8 が一致する掲示にだけ答える（無ければ言葉を指示として届ける）。印の無い言葉は、送った時刻＋ASK_GRACE より後に出た掲示へは答えない。「✓ 送信しました」の照合も id8。`questions`（R112-H）＝AskUserQuestion の**全部の問いと選択肢**: `[{q, options, multi}]`。`q`＝問いの文（`question`、無ければ `header`・空白を畳んで 200 字まで）・`options`＝選択肢の label だけ（40 字まで・4 個まで・`description` は載せない）・`multi`＝その問いが `multiSelect:true` なら 1・それ以外 0。最大 8 問（9 問目以降は切る・`qn` は実数のまま）・空の問いは落とす。承認フックが掲示に書き（推測しない）、daemon が形を検証して写す（1 件でも形が違えば questions ごと無し＝問いの番号がずれた一部を見せない）。許可（Bash 等）と計画の承認（ExitPlanMode）には付かない。旧い hook の掲示には無い（transcript からは作らない）＝スマホは `qn>1`・`multi` の従来の表示に戻る。`title`・`options`（1 問目）・`qn`・`multi` は古いスマホとの互換のため今のまま。中継に載る（entry の `question`・`questionOptions` と同じ「回答のために通す」）。答えは今までどおり id8 を添えた `kind:"answer"` の本文 1 通 |
| `feed` | scrub | 発言行を除去、パスは basename、URL はホスト、60字 |
| `work` | sanitize | now/next/done 計8件、パス縮約、60字、counts |
| `skills` `dept` `role` `disp` `name` | ○ | 表示用の名前・分類 |
| `arch` | ○ | アクセサリ上書き（9種の識別子または null） |
| `color` | ○ | 殻の色（`PROJECT_COLORS` の8種のいずれか・未設定ならキーごと無し） |
| `title` | 既定で通す | `/rename` の表示名。`OFFICE_RELAY_TITLES=0` で空文字 |
| `aiTitle` | 除去（allowlist 外・`_LOCAL_ONLY_ENTRY` に宣言） | R123-A: Claude Code の自動タイトル（transcript の `ai-title` 行・最後の 1 件・空白を畳んで 40 字まで・無ければ ""）。Mac の一覧が `title`（/rename）→ `aiTitle` → これまでの名前の順で使う（`ui/hud/board.js` の `departmentBoard`）。会話から作られた文＝本文に近いので、スマホへ出すなら本人の裁定の後に allowlist へ |
| `detail` | 既定で通す、200字 | Claude bg の一行要約。`OFFICE_RELAY_DETAIL=0` で空文字 |
| `lastSaid` `target` `lastOrder` `cwd` `branch` | キーだけ残し空文字 | 本文・パス等 |
| `err` | `{k, ago}` のみ（k＝`api`・`limit`・`ctx` の enum・ago＝経過秒の整数・それ以外の形はキーごと除去） | R102-4: 止まった理由。transcript の最後が合成のエラー（`isApiErrorMessage`）なら種類（利用上限・会話の長さ・それ以外の API エラー）、フックの `StopFailure` から 3 時間は `api`。エラーの本文は持たない |
| `permMode` | enum のみ（`default` `acceptEdits` `plan` `bypassPermissions` `dontAsk` `auto`・ほかはキーごと除去） | R102-4: 権限モード（transcript の permission-mode 行）。端末の見出しに出す |
| `interruptedAt` | 除去（allowlist 外） | R102-4: 中断（Esc）の時刻。ターンの追跡（`office_events.turn_live`）が「閉じたもの」と読むためのローカル専用 |
| `evidence` | 除去（allowlist 外） | R98: 直近 24h の hook 記録から `{kind: committed\|tested\|failed, ago}`（`office_timeline.evidence_for`）。記録が無ければキーごと無し。Codex/OpenClaw は hook が無いので常に無し。本文は持たない |
| `bg` `pid` `tokens` `homeCwd` `evSeq` その他 | 除去 | allowlist 外はローカル専用 |

### Bot の行（R126 出勤の口・`vendor:"bot"`）

常駐 Bot・スクリプトは `session:"bot-<id>"`・`vendor:"bot"` の 1 行（`external` なし）。形の正本は `docs/door.md` §2（`server/office_door.py::door_employees` が作る）。
要点: `dept`／`disp`＝登録した名前・`role`＝役の札・`verb`＝出勤中は Bot の `doing`（相談中は「相談の答え待ち」・休憩中は「休憩中」）・`listening` は常に true・
`pending`＝未読があるか・`muted`＝消音か（R128 #4・⚙ の Bot パネル・真偽。employees の Bot の行だけ・roster には無い＝Mac の知らせと画面の音・通知が
`session` で引く）・`cwd`／`branch`／`lastSaid`／`lastOrder`／`target` は常に `""`・`feed` は常に `[]`。相談があるときは `state:"waiting"`・`question`・
`questionOptions`・`ask:{tool:"office_ask", kind:"question", title, options, askId, ts, min, remote:0}`（承認の掲示ではない＝`office_approvals/` に置かない）。

**中継（R127〜・不変条件 8 の例外＝本人裁定 2026-11-10）**: ふつうの entry allowlist ではなく `relay_agent._redact_door_entry` が Bot だけの狭い表
（`_ALLOW_DOOR_ENTRY`）で新しい dict を組む。R126 は載せなかった（落としていた）。

| キー | 中継 | 備考 |
|---|---|---|
| `session` `vendor` | ○ | `session` が `bot-<id>` の形でない行は丸ごと載らない・`vendor` は `"bot"` に固定 |
| `name` `dept` `disp` `role` | ○（口と同じ上限で切る） | 登録した名前と役の札 |
| `projectId` `crew` | ○ | roster の識別（パスを含まないハッシュ・整数） |
| `state` `kind` | enum だけ | `working`／`waiting`／`resting`・`tool`／`said`／`idle` |
| `verb` | 縮約 | Bot の doing（相談中は「相談の答え待ち」）。feed と同じ `_scrub_text_for_relay(…, 60)`（パスは末尾名・URL はホスト・秘密の形は伏字・60 字） |
| `age` `mtime` `pending` `listening` `attention` | ○（整数・bool） | 送信の指紋では `age` を 10 分に丸める（`DOOR_FP_MINUTES`） |
| `approvalMin` | 0 固定 | Bot は許可を待たない |
| `question` `questionOptions` | ○（**縮めない**） | Bot が自分で書いた問いと選択肢（200 字・40 字×4）。選択肢の文は答えの「選択肢「X」…」と口が突き合わせる＝変えない |
| `ask` | `{tool:"office_ask", kind:"question", remote:0, min?, id8?}` だけ（`_ALLOW_DOOR_ASK`） | `kind` が `question` でない ask は載らない（スマホに許可のカードを出させない）。`id8`＝askId の先頭 8 桁。askId 全体・`title`・`options`・`ts` は載らない。指紋では `min` を 10 分に丸める |
| `muted` | **載らない**（Bot の表に無い） | R128 #4 の消音は Mac の音と通知だけ。スマホの Push は Worker の `computeAttnNow` が中継の `question`・`approvalMin` から決める＝印を足しても Worker を変えなければ止まらない（R130 で決める）。番人= `test_relay_agent` の表のピン |
| `cwd` `branch` `lastSaid` `lastOrder` `target` `feed` `sessions` `title` `stuckTool` `skills` `minions` その他 | **鍵ごと無い** | ふつうの行の「キーだけ残して空文字」とも違う（Bot の行は Mac のセッションの文を持たない・手違いで持っていても出さない） |

実例（`relay_agent` の伏字の後・employees の 1 件・相談中）:
```json
{"session": "bot-mailbox", "vendor": "bot", "dept": "郵便受け", "disp": "郵便受け", "role": "受信箱", "state": "waiting", "kind": "said",
 "verb": "相談の答え待ち", "age": 420, "mtime": 1794602847.2, "pending": false, "listening": true, "approvalMin": 0,
 "question": "請求書 3 件を送ってよい？", "questionOptions": [{"label": "送る", "desc": ""}, {"label": "明日にする", "desc": ""}],
 "ask": {"tool": "office_ask", "kind": "question", "remote": 0, "min": 7, "id8": "0013042d"}}
```
roster の 1 件は同じ形に `name`・`projectId`・`crew`・`attention` が加わり `mtime` が無い。相談が閉じると `ask` が無くなり `question` は `""`・
`verb` は doing の縮約（例: `Discord の依頼を見回り中 /Users/…/works/inbox` → `Discord の依頼を見回り中 inbox`）。
スマホの答えは R102-11 と同じ本文 `{"aioffice":1,"kind":"answer","ask":"<id8>","text":"…"}` を `session:"bot-<id>"` 宛に署名して送る
（選択肢のボタンは `text` を `選択肢「X」でお願いします。`／`Please go with the option: X`）。印の無い言葉は相談が閉じているときだけ指示になる（`docs/door.md` §5）。

## 変更手順

1. `scan_office()` と本表を更新する。
2. 中継に載せるなら `relay_agent._ALLOW_ENTRY` / `_ALLOW_TOP` / `_ALLOW_SESSION` / `_ALLOW_ASK`（entry.ask の中）と必要な刈り込みを更新する。トップレベルが許可済みでも、新しいネスト値が安全とは限らない。
   Bot の行（R127）は別の表 `_ALLOW_DOOR_ENTRY` / `_ALLOW_DOOR_ASK`（`_redact_door_entry`）＝ふつうの表に足しても Bot の行には載らない（Bot に載せるならこちらとピン `test_r127_door_allowlist_is_pinned` を同時に）。
3. `tests/test_relay_agent.py` に載る/載らないのピンを追加する。
4. `AllowlistRedactionTest.test_allowlist_covers_ui_and_pwa_field_reads` で消費側の参照集合を検査する。ただし現行の静的検査は `ui/core/world.js` と `relay/src/worker.js` を読み、移設済みのPWA本文（`ui/pwa/app.js` / `relay/src/app_html.js`）は直接読まない。green だけでPWA全フィールドを網羅したと判断せず、実際の消費側と照合する。

## `dlg`（R87 封書）は office_json ではない

スマホの会話ビューアは、会話を **office_json に載せない**。別のレーンで運ぶ:

- 上り: スマホが `session="dlg-<32hex>"` の署名封筒（`text` は `{"aioffice":1,"kind":"dialog","reqId","session","depth":0}`）を `/instruct` へ。
  Worker は 0 行の変更（`act-` と同じ流儀）。relay_agent は `verify_envelope` を通してから daemon の `POST /api/dialog/sealed` へ回す。
  **office_inbox には書かない**（孤児 inbox を作らない）。
- 封緘: daemon（transcript を読める唯一のプロセス）が `dialog_page` の depth0 を AES-256-GCM で**この端末専用の鍵**（既存のペアリング秘密から HKDF）で封じ、
  `~/.claude/office_dialog_bundles.json`（0600・単一書き手・TTL 90秒）へ置く。relay_agent は別プロセスなので**ファイル経由**（R92 と同じ掟）。
- 下り: relay_agent が `/sync` の body に `dlg`（`{"v":1,"items":[bundle…]}` の JSON 文字列）を**指紋が変わった周だけ**載せる。
  Worker は kv 1 行 `dlg` に置き、status フレーム・`{"t":"status"}` 応答・フル Bearer の `GET /status` に同乗する（限定トークンには載せない）。
  Worker は**行の ts で 120 秒**経てば空として扱う（Mac がスリープ・停止して「空で上書きする sync」が来なくても暗号文が残らない）。
  `dlg` の上限は 180,000 字（Mac 側 MAX_LIVE 4 通 × 4 フレーム）。relay_agent は超える分を**新しい順に残して古い封書から落とし**標準出力に残す（黙って全部捨てない）。
  端末（PWA）も `e` を今の時刻で検算してから開く（`openBlob` の第 5 引数・core は時計を持たないので呼び手が渡す）。
  期限切れは空バンドルを 1 回送って行を消す。
  中継の自動減速（R80・`usage.level>=1`）の間は非空の `dlg` を保留する。ただし**急ぎの封書**（許可の全文 `kind:"ask"` と
  その理由の封筒）を含む周は全体を送る（人が「スマホで確認して許可」を押して待っている・一部だけ送ると中継の行が置き換わり
  送り済みの封書を消す）。急ぎの印は封書の外＝ファイルの `"urgent": [reqId…]`（`dialog_seal.urgent_ids()`）で、封書の形は変えない。
- Codex（`session="cx-<thread>"`・R113-B-I）は **`kind:"term"` だけ**開ける: daemon が `office_term.session_page` →
  `source_codex_rollout`（SQLite を読み取り専用で引いた rollout_path を `~/.codex/sessions`／`archived_sessions` の配下へ閉じ込めて読む）で行にし、
  上限と縮め方は Claude の行と同じ（`office_term.cmd_text`／`out_text`・shrink の `CMD_TOOLS`）。引けなければ `notfound`。
  cx- の `kind:"dialog"`（会話ビュー）は開けない＝従来どおり `notfound`。
- 出勤の口の Bot（`session="bot-<id>"`・R128 #3）も **`kind:"term"` だけ**: daemon が `office_term.session_page` → `source_door_log`（`<id>` を確かめ・
  登録した Bot だけ・口のフォルダの中の記録 `<id>.log.jsonl` を読むだけ）で行にする。行の形と画面の約束の正本は `docs/door.md` §9「記録の画面」
  （Mac の `GET /api/session/activity` も同じ行）。Bot の記録の本文はこの封書の中だけ＝office_json にも中継の平文にも新しい鍵は無い。引けなければ `notfound`・
  Bot の `kind:"dialog"` も `notfound`。
- bundle の形（`_assert_opaque` が送信前に検算する allowlist）: `{v:1, id, s, i, e, n, c:"zl", b}` か `{v:1, id, e, err}`。`err` は
  `unavailable / denied / notfound / toolarge / expired` の固定 enum だけ。`b` は base64url・`n×8223` バイト・`AO1` 始まり。
  **平文・自由文字列・余分なキーは構造的に通らない**（`_ALLOW_TOP` は触っていない）。
- 守られること／守られないこと（README と同じ文言）: 中継（Cloudflare）と輸送 Bearer だけを持つ第三者は中身を読めない。
  ただしメタデータ（いつ・どの端末が・どのセッションを要求したか・8KB 単位の長さ）は見える。前方秘匿性は無い。
  暗号文は TTL まで中継に残る。Mac は当然読める。端末を失うと会話も読まれる（Mac から失効させた瞬間に封は作られなくなる）。

## `today`（R96-B・2026-09-15）

`today = {sent, answered, lastSentAgo, capped}`。`answered` は daemon が積む日次 stats（`~/.claude/office_daily/<day>.stats.json`）の当日解消❗数＝**「今日のまとめ」の正本**。Mac もスマホもこれを表示し、端末ローカルの数字を正本にしない。中継はトップレベル `today` を allowlist で通す。
`lastSentAgo`（最後の送信からの秒）は走査のたびに増える＝relay_agent の送信の指紋（`_status_fingerprint`）には入れない（R116-S0・読むのは Mac の `ui/iso` だけ。中継には他の変化か heartbeat の送信に相乗りで載る）。
