# 出勤の口（door）— 契約の正本（R126〜）

常駐 Bot・スクリプト（郵便受けの見回り・定期の自動処理など）を、AI Office の部屋に「質問を持てる 1 人」として座らせる口。
設計= `~/.claude/plans/deep-rolling-book.md` §1（2026-11-10・本人の裁定 4 つ）。実装= `server/office_door.py`（台帳・状態・CLI）＋
`server/mcp_office.py --bot <id>`（MCP の道具）＋ `server/office_server.py`（`scan_office()` の合流・守る線）。
登録= CLAUDE.md のルーティング・`.claude/rules/door.md`・`docs/office-json.md`（Bot の行と `sources.door`）。

**R127（いまの段）＝相談の往復**: 口のファイルはこの Mac のローカル。人は Mac の質問カード（`/api/approval/reply` の bot- の枝）とスマホ
（id8 つきの答え＝`relay_agent` の bot- の枝）から Bot の相談に答えられ、Bot の行は中継に **Bot だけの狭い allowlist** で載る（不変条件 8 の改定・
本人裁定 2026-11-10・§6）。R126 は Mac だけ（中継に何も載せない）だった。クラウドの Bot の入口（`POST /door/…`）は R130。

**R128 #3＝記録の画面**: Bot を選ぶと、その Bot の記録（`<id>.log.jsonl`）が Mac の中央（作業ビュー）とスマホの端末画面に、セッションの会話と同じ
画面の作法で出る（§9）。本文は封書（`kind:"term"`）の中だけを通る＝中継の平文に鍵を足さない。

**R128 #4＝Bot パネル**: Mac の ⚙ 設定の「Bot」の節で、一覧・追加（直し）・削除・消音・登録コマンドのコピーができる（§10）。登録・削除・消音は
**Mac の前の人だけ**（Origin 必須の POST・loopback）＝中継・MCP・Bot の道具には道が無い。消音は**鳴らさないだけ**で、Mac の音と通知だけに効く
（スマホの Push は Worker が決める＝変えない・R130 で決める）。

## 1. 道具（ローカルも中継も同じ名前・5 つ）

| 道具 | 引数（上限） | 返すもの・働き |
|---|---|---|
| `office_checkin` | `doing`（1〜60 字・必須）・`state`（`working`／`waiting`／`resting`・既定 `working`）・`ttl_min`（1〜240 の整数・既定 15） | 出勤を記録。「届いているもの: 答え N・指示 M」と数（`answers`・`instructs`・`others`）。**前と同じ内容で、期限の半分より前なら書かない**（`same: true`・期限の半分を過ぎたら延ばす＝毎分呼んでも書き込みは期限の半分に 1 回） |
| `office_report` | `title`（1〜80 字・必須）・`body`（600 字まで・省略可）・`kind`（`done`／`info`／`warn`・既定 `done`） | 終わったことの記録（Bot の記録 `<id>.log.jsonl` に 1 行）。**鳴らさない**（気づいてほしいことは ask） |
| `office_ask` | `question`（1〜200 字・必須）・`options`（1〜40 字の文字列 × 4 個まで・重複なし・省略可）・`ttl_hours`（1〜168 の整数・既定 24） | あなたの番に並ぶ。`askId`（`secrets.token_hex(16)`＝32 桁の 16 進）と `deadline` を返す。**開いている相談は Bot ごとに 1 件**（2 件目は `ask-open`＝「先の答えを待っています」と先の `askId`）・**1 時間 6 件／1 日 20 件まで**（取り下げ・期限切れも数える） |
| `office_inbox` | `ack`（読んだ id の配列・省略可） | 未読を覗く（**消さない**・覗くだけでは書かない＝状態を書くのは期限切れの相談を閉じる掃除が要るときだけ）。`ack` に渡した id だけ消す＝at-least-once。`items[]` の種類は下の表 |
| `office_withdraw` | `askId`（32 桁・必須） | 相談を取り下げる（ほかの窓口で先に答えが出たとき）。もう閉じた相談には `already`（`answered`／`withdrawn`／`expired`）で答える（冪等） |

- 上限の定数は掲示の質問と同じ値（`ASK_Q_LEN` 200・`ASK_OPT_MAX` 4・`ASK_OPT_LEN` 40）で、正本は `server/office_common.py`。
- 上限を超えた・型が違う引数は**切り詰めずに断る**（`error: "bad-args"`・何も書かない）。`ttl_min`／`ttl_hours` は整数（`15.0` は受ける・`"15"`・`true` は断る）。
  MCP では知らない引数も断る（`additionalProperties: false`）。
- 自由文は信じない: NFC にそろえ、制御文字と書式文字（双方向の上書き・ゼロ幅）を落とす（絵文字の結合 ZWJ は残す）。1 行の欄（名前・役・`doing`・題・問い・選択肢）は
  改行とタブを空白にして畳む。`body`・指示・答えは改行を残す。**文字はそのまま文字**（HTML として解釈しない＝描く側が必ず textContent）。
- 受け付けは 1 件ごとの id で冪等（`req_id`・CLI は `--req-id`・`[A-Za-z0-9_.:-]{1,64}`）。Bot ごとに適用済みの id を状態に持ち（200 件・14 日）、
  同じ id の再配達には前の結果を `dup: true` つきで返す（二重に入れない）。MCP の道具には出さない（stdio は再配達しない・中継の口＝R130 が使う）。
  R127: 人の側の `answer`・`instruct` も `req_id` を受ける＝スマホの封筒は `relay_agent` が端末 id と nonce のハッシュ（`relay:` ＋ 40 桁）を渡す
  （nonce の控えを失った再配達でも二重に入れない・`dup` の指示は送信履歴にも二度書かない）。

### 結果の形（道具・CLI 共通）
`{"ok": true|false, "text": "人が読む 1 文", ...}`。断ったときは `error`:
`bad-args`（形・上限）・`bad-id`・`unregistered`（登録なし）・`ask-open`（開いている相談がある）・`rate-hour`／`rate-day`・`no-ask`（その相談は無い・閉じている）・
`src`（呼び手が人でない）・`ledger-broken`（台帳が読めない＝上書きしない）・`too-many`（登録は 64 体まで）・`internal`（MCP の中の例外）。
文の言語は `OFFICE_LANG`（MCP はオフィスの言語設定）。

### 未読（`office_inbox` の `items[]`）
| `kind` | キー | いつ届く |
|---|---|---|
| `answer` | `id`・`ts`・`askId`・`text`・`choice{index,label}`（選択肢に当たったときだけ） | 人が相談に答えた（Mac の質問カード＝`/api/approval/reply`・スマホの id8 つきの答え＝`relay_agent`・§5） |
| `instruct` | `id`・`ts`・`text` | 人が Bot に指示した（Mac の画面＝`/api/instruct`・スマホの印の無い言葉＝`relay_agent` → `post_instruction("bot-<id>", …, src=…)`・相談が閉じているときだけ） |
| `expired` | `id`・`ts`・`askId` | 相談の期限が切れた（掃除のときに 1 件） |
| `rejected` | `id`・`ts`・`askId`・`why` | 受け付けた後で Mac が断った相談（中継の口＝R130 から） |

未読は Bot ごとに 100 件まで（超えたら古い順に捨てて監査）・**14 日で消す**。
状態ファイルの読み取り上限（`STATE_READ_MAX`）は、上限いっぱいの未読（100 件 × 4000 字 × UTF-8 の 4 バイト）が収まる大きさにする（小さいと、正しい状態を「壊れている」と読んで未読と出勤を空で上書きする）。書く側はそれより小さく収める（`_fit_state`＝超えるなら未読を古い順に捨てて監査 `drop`／`bytes`）。

### 選択肢の解決（Bot が文字列の一致に頼らなくて済む）
答えの文が次のどれかで、`X` が選択肢のどれかと同じなら `choice{index,label}` を付ける（`office_door.resolve_choice`）。
文の型は Mac とスマホの実物（`ui/hud/strings.js` の `opt_text`・`ui/pwa/app.js` の `T(…)`）で、`tests/test_office_door.py` が実物から拾ってピンしている。

| 送り手 | 文の型 |
|---|---|
| Mac（ja）・スマホ（ja） | `選択肢「X」でお願いします。` |
| Mac（en） | `Please go with option "X".` |
| スマホ（en） | `Please go with the option: X` |
| 手で打った | 選択肢と同じ文（`はい` など）だけ |

## 2. 行の必須キー（`door_employees()` が作る・`tests/test_office_door.py` の RowTest がピン）

`employees[]` に 1 体 1 行（`external` は付けない＝Claude のセッションと同じ道＝席・あなたの番・知らせを通す）:

| キー | 値 |
|---|---|
| `session` | `bot-<id>` |
| `vendor` | `"bot"`（UI は R128 まで「その他」の形で描く） |
| `dept`・`disp` | 登録した名前（24 字まで）。同じ名前は id 順に採番（`見回り 2号`・en は `#2`） |
| `role` | 役の札（16 字まで・既定 `Bot`） |
| `state`・`kind` | 下の「見え方」。`kind` は `working→tool`・`waiting→said`・`resting→idle` |
| `verb` | 出勤中は `doing`／相談中は「相談の答え待ち」／休憩中は「休憩中」 |
| `age`・`mtime` | 最後に動いた時刻（出勤・報告・相談・取り下げ）とそこからの秒 |
| `listening` | 常に `true`（Bot はいつでも受け取れる＝「次に操作すると届く」と誤案内しない） |
| `pending` | 未読があるか |
| `muted` | 消音か（R128 #4・台帳の `muted`＝真偽の `true` だけ）。Mac の知らせと画面の音・通知だけが読む＝**鳴らさないだけ**（行・相談・あなたの番はこのまま）。中継には載らない（§10） |
| `cwd`・`branch`・`lastSaid`・`lastOrder`・`target` | 常に `""`（本文とパスの鍵は空） |
| `feed` | 常に `[]`（報告の題を入れない＝feed は中継に載る鍵） |
| `question`・`approvalMin`・`stuckTool`・`skills`・`minions`・`avatar` | `""`・`0`・`""`・`[]`・`0`・`0`（相談中は `question`＝問い） |

相談があるとき: `state:"waiting"`・`question`・`questionOptions:[{label, desc:""}]`（選択肢があるとき）・
`ask:{tool:"office_ask", kind:"question", title, options, askId（32 桁）, ts, min, remote:0}`。

- `avatarMode=project` でも Bot は常に 1 体 1 組（`group_by_project` が `ses:<session>` で単独に置く＝同じ名前・空の cwd で畳まない）。
- Bot の id= `^[a-z0-9][a-z0-9-]{3,31}$`（session は 8〜36 字＝`post_instruction` の宛先の形に合う）。
- `sources.door`= `{ok, bots, n, asks}`（台帳が読めたか・登録数・載せた行・開いている相談の数＝数だけ）。中継には載らない。
- **中継に載る形（R127〜）**: 上の行をそのまま載せない。`relay_agent._redact_door_entry` が Bot だけの狭い allowlist（`_ALLOW_DOOR_ENTRY`）で作り直す＝
  `session`・`vendor`・`projectId`・`crew`・`name`・`dept`・`disp`・`role`・`state`・`kind`・`verb`（縮約）・`age`・`mtime`・`pending`・`listening`・`attention`・
  `approvalMin`（0 固定）・`question`・`questionOptions`・`ask{tool:"office_ask", kind:"question", remote:0, min, id8}`。本文とパスの鍵・`feed`・`sessions`・`title` は
  鍵ごと無い。表と実例は `docs/office-json.md` の「Bot の行」。

### 見え方（出勤・休憩・退勤）
`ttl_min` の間は出勤の `state`（既定 作業中）→ その後 3 時間（`SHOW_WINDOW`・`OFFICE_SHOW_WINDOW`）は休憩 → 退勤（行が消える）。
**開いている相談がある間は居続ける**（期限まで・受付に並ぶ＝既存の `zoneOf`）。一度も動いていない Bot は載らない。
未来の時刻（5 分を超える）・期限が 168 時間より先の相談は毒として読まない（居座らせない）。

## 3. 身元
- **登録した Bot だけ**が出勤できる（`unregistered` は監査に 1 行・状態は作らない・口のフォルダが無い HOME には何も書かない）。
- どの Bot かは**起動の設定**で決まる: MCP は `mcp_office.py --bot <id>`（そのときは上の 5 つだけを出し、いつもの `office_status`・`office_instruct`・`office_digest` は出さない・呼べない）。
  シェルからは `python3 server/office_door.py <checkin|report|ask|inbox|withdraw> --bot <id>`（server/ と一緒に常駐先へ配られる）。
- 同じユーザーのプロセスは信頼の内側（今の stdio MCP と同じ線＝Bash を持つセッションは CLI で Bot を名乗れる）。守るのは外からの線。
- **登録・直し・削除・消音は人の操作**: Mac の ⚙ の Bot パネル（§10）か、ターミナルの CLI（`add`・`remove`・`list`）。Bot の道具・中継・MCP には道が無い
  （台帳を書くのは `add_bot`／`remove_bot`／`set_muted` の 3 つだけ＝`tests/test_door_panel.py` が AST で見る）。
- **Bot の MCP はその仕事の設定にだけ足す**（`claude mcp add --scope user` で全セッションに配らない）:
  ```bash
  python3 server/office_door.py add mailbox --name 郵便受け --role 受信箱     # 登録（人の操作）＝結果の mcp に下の 1 行
  claude mcp add aioffice-door-mailbox -- <python3 実体> "<…>/server/mcp_office.py" --bot mailbox   # その仕事のフォルダで（--scope user にしない）
  ```
  この 1 行の正本は `office_door.mcp_command(id)`（パネルの［登録コマンドをコピー］と CLI の `add` の `mcp` が同じ物）。id は `valid_id` を通ったものだけ
  ＝英小文字・数字・- だけでシェルの特殊文字が入らない・python3 の実体と `mcp_office.py` のパスは `shlex.quote`（スペースのあるフォルダでも 1 語）。
  daemon が組むときの python3 はその daemon の実体（常駐なら `macapp/install.sh` が焼いた実体・パスは `app/server/mcp_office.py`）。秘密は含まない。

## 4. 置き場と流れ（状態は全部ファイル＝不変条件 9）

| ファイル | 中身 | 権限・上限 |
|---|---|---|
| `~/.claude/office_door/bots.json` | 台帳 `{v:1, bots:{<id>:{name, role, added, muted}}}`（R128 #4: `muted`＝消音・真偽の `true` だけを消音と読む・登録の直しでも保つ） | 0600・64 体まで・読めなければ `ok:false`（上書きしない） |
| `~/.claude/office_door/bot/<id>.json` | 状態（出勤・開いている相談 1 件・未読・相談の時刻・閉じた相談・適用済みの id） | 0600・1 MiB を超えたら壊れたものとして空から |
| `~/.claude/office_door/bot/<id>.log.jsonl` | 記録（出勤の内容の変化・報告・相談・答え・指示・取り下げ・期限切れ）。R128: 記録の画面（§9）が読むだけ | 0600・256 KiB を超えたら新しい半分だけ残す（別の inode に作り直す） |
| `~/.claude/office_door/audit.jsonl` | 監査（本文なし＝`ev`・`bot`・数・種類・askId だけ） | 0600・1 MiB で同じく回す |

フォルダは 0700。書き込みは Bot ごとの `office_common.file_flock`＋`atomic_write_json`（読み→掃除→変更→原子的に置き換え）。
`door_employees()` は**読むだけ**（ロックも書き込みもしない＝走査を止めない・本物の HOME を走査する verify ▶8 でも書かない）。
`office_door.py` は `office_server` を import しない（循環と CLI の重さ）。

```
[ローカルの Bot・スクリプト] ─ stdio MCP（--bot）／CLI ─▶ office_door.py ─▶ ~/.claude/office_door/bot/<id>.json
daemon: scan_office() が door_employees() を OpenClaw の隣に合流（例外は握る＝sources.door.ok=false）─▶ office_json ─▶ Mac
                                                     └▶ relay_agent の伏字（Bot だけの狭い allowlist・R127）─▶ 中継 ─▶ スマホ
人 → Bot（R127）:
  Mac の質問カード /api/approval/reply（bot- の枝・askId 32 桁・言葉だけ）─▶ office_door.answer(src="local")  ─┐
  Mac の画面 /api/instruct ─▶ post_instruction("bot-<id>", …, src="local")＝相談が閉じているときだけ ──────────┤
  スマホ id8 つきの答え ─▶ relay_agent（bot- の枝）─▶ office_door.answer(src="relay")                         ─┼▶ 状態の inbox ─▶ Bot が office_inbox で読む
  スマホ 印の無い言葉 ─▶ relay_agent（bot- の枝）─▶ post_instruction(src="relay")＝相談が閉じているときだけ ──┘
```

- 掃除: 期限の切れた相談は閉じて未読に `expired` を 1 件・14 日を過ぎた未読と古い控えを捨てる。Bot が道具を呼ぶたびに（その Bot の分）・daemon の
  `prune_inbox_litter`（1 時間に 1 回・全 Bot）で走る。走査（`door_employees`）は期限を時刻で見るだけ＝切れた相談は掃除を待たずに画面から消える。
- 相談の生死は**期限**と取り下げ・回答で決める（Bot に心拍は無い）。

## 5. 人から Bot へ（指示・答え）
- Bot へ言葉を渡せる呼び手は `office_door.HUMAN_SRCS` だけ＝`local`（Mac の画面＝loopback＋CSRF）と `relay`（スマホ＝ペアリングした端末の署名を
  `relay_agent` が確かめた封筒・R127 から）。`mcp`（セッション）は入れない。
  `post_instruction(session, text, *, src, req_id=None)` の `src` は**キーワード専用・既定値なし**（`local`・`relay`・`mcp`）＝呼び忘れた経路が黙って「人」にならない。
  `bot-` 宛は `src` が人でなければ断る（監査に `refuse`・`why:"src"`）。
- **相談が開いている間、その Bot への言葉は答えになる・指示は受けない**（`ask-open`＝指示は相談が閉じてから）。答えは開いている相談の `askId`（32 桁）か
  先頭 8 桁（id8）に縛る＝**画面に出していた相談への答えだけが届く**。**合わない答えは捨てて監査に残す**（`discard`・`nomatch`／`closed`）。
  セッションと違い「指示として届ける」に落とさない＝別の依頼への「やる」に化けない。
- **Mac の質問カード（R127）**: 今のカードのまま（`ui/hud/send.js` は相手に `ask` があれば `approvalReply(session, "deny", 文, ask.askId)`）。
  `/api/approval/reply` の bot- の枝＝`office_server.answer_bot`: `behavior` が `"deny"`（文章の答え）以外は断る＝**`allow` は 400**（監査 `refuse`・`op:"answer"`・
  `why:"allow"`）・`askId` は 32 桁で必須（無い・形が違えば「画面を読み込み直してください」）・言葉は口の `answer`（`src:"local"`）へ・掲示板は読まない書かない・
  答えた直後の `/api/office` は閉じた相談を出さない（`_mark_written`）。選択肢のボタンは `選択肢「X」でお願いします。` を送る＝口が `choice` に解決する。
- **スマホ（R127・`relay_agent._deliver_door`）**: `bot-` 宛の封筒を 1 通ずつ順に口へ（口の受信箱は何件でも持てる＝連結しない）。
  - id8 つきの答え（`{"aioffice":1,"kind":"answer","ask":id8,"text":…}`・R102-11 と同じ本文）→ `office_door.answer(src="relay")`。id8 が開いている相談に
    合うときだけ届く。合わない・閉じた相談への答えは捨てて監査（`discard`・`src:"relay"`）。
  - 印の無い言葉 → `post_instruction(src="relay")`＝相談が閉じていれば指示。**開いていれば口が断る**（`refuse`・`ask-open`）＝見ていない相談の答えにはしない
    （いまのスマホは相談を出している相手へは必ず id8 を添える＝印の無い言葉は、送った時点で相談を見ていない）。
  - 無署名（`RELAY_ALLOW_UNSIGNED` の移行用でも）・宛先の形が違う封筒は人の言葉と言えない＝捨てて監査（`unsigned`／`bad-id`）。
  - ack と nonce: 口が受けた・断った（恒久）＝ack＋nonce をコミット（ack を失った再取得はリプレイで落ちる）・`req_id`（端末と nonce のハッシュ）で
    口の中も冪等。`OSError`／`ValueError`（一時障害）＝ack せず残置し、その Bot の後ろの封筒も次周へ（順番を守る）。それ以外の例外は外へ出さず
    その 1 通を捨てて監査（`discard`・`why:"error"`＝毒の 1 通で後ろを止めない・他のセッションの配達を巻き込まない）。

## 6. 守る線（構造で）
- **不変条件 7（許可）**: Bot の相談は `~/.claude/office_approvals/` に置かない。`write_approval_reply("bot-…")` は `ValueError`（allow も deny も）。
  `pending_approvals("bot-…")`・`_ask_raw("bot-…")` は掲示を読まない（同じユーザーの別プロセスが置いた偽の掲示も ❗ と許可の材料にならない）。Bot への答えは言葉だけ。
- **セッションとの壁（両向き）**:
  - Bot → セッション: `--bot` の道具は 5 つだけ（`office_instruct`・`office_status` が無い・呼べば `-32602`）。
  - Bot の文 → セッションのプロンプト: 普通の `office_status` は Bot の行を「名前（Bot）と状態」だけにする（`doing`・相談の文・session を出さない）。
  - セッション → Bot: MCP の `office_instruct` は `bot-` 宛・Bot の名前を断る（Bot は宛先の候補にも入れない）＋ `post_instruction` が `src="mcp"` を断る。
- **不変条件 8（中継・R127 で改定＝本人裁定 2026-11-10）**: 「本文は中継に流さない。例外は出勤の口の文だけ: Bot が自分で書いた報告・質問と、人がその Bot に
  宛てた答え・指示。Mac のセッションの本文・パスは口にも載せない」。Bot の行は Bot だけの狭い allowlist（§2・`_ALLOW_DOOR_ENTRY`）で載る＝`verb` は
  `_scrub_text_for_relay(…, 60)` で縮める・問いと選択肢は縮めない（答えの照合に使う）・`ask` は印（id8）だけ・許可の形の `ask` は載らない・本文とパスの鍵は
  鍵ごと無い。`counts` は Mac と同じ（Bot を引かない）。`sources.door` は中継の sources の表に無い。送信の指紋では Bot の行の `age`・`ask.min` を 10 分に
  丸める（`DOOR_FP_MINUTES`＝長く居る Bot・長く待つ相談が毎分の sync を起こさない）。残るリスク= `docs/security-notes.md` R127。
- **数字を守る**: Bot の稼働は自己申告＝`office_timeline.record_states` に入れない（稼働分・XP に混ぜない・数え方は R132）。知らせの鍵（`attention_diff`・
  `_attn_track`）は Bot の相談だけ askId を足す（`_attn_key`＝1 体 1 件でも、答えた直後の次の相談で鳴る・数える）。消えた相談は口が `answered` と言う分だけ
  「答えた ❗」（日報の stats）に数える（`office_door.closed_how`＝期限切れ・取り下げ・登録の取り消しは数えない）。Bot の分は時間軸（`office_timeline`）へは
  送らない（時間軸は Bot の行を持たない）。セッションの鍵は変えない（続けて出る許可の掲示のたびに鳴らさない・日報の数え方を変えない）。
- 不変条件 1・2・10: server/ は stdlib・flat（`office_door.py` 1 本）・127.0.0.1 だけ・署名 canonical と KAT は触らない。

## 7. 監査（`audit.jsonl` の `ev`）
`add`・`update`・`remove`・`checkin`（内容が変わったときだけ）・`report`（`kind`）・`ask`（`askId`・`n`・`ttlH`）・`withdraw`・`expire`・`answer`（`src`・`choice`）・
`instruct`（`src`・`n`＝字数）・`ack`（`n`）・`discard`（`why`＝`nomatch`／`closed`／`unsigned`／`bad-id`／`error`・`src`）・`drop`（`why`＝`cap`／`old`・`n`）・
`reset`（壊れた状態から作り直した）・`refuse`（`op`・`why`＝上の `error`。R127: Mac から Bot へ allow＝`op:"answer"`・`why:"allow"`・`src:"local"`）・
`mute`（R128 #4・`on`＝true／false・値が替わったときだけ。断りは `refuse`・`op:"mute"`）。
本文・問い・選択肢・名前は書かない。

## 8. 検証
- `python3 -m unittest tests.test_office_door tests.test_mcp_office tests.test_relay_agent tests.test_office_data tests.test_door_e2e`（一時 HOME だけ）。
- 端から端まで（R127）= `tests/test_door_e2e.py`: 本物の daemon（空きのテスト用ポート）・本物の `mcp_office.py --bot`・別プロセスの `relay_agent` で
  相談 → Mac の `/api/approval/reply`（allow は 400・askId 違いは捨てて監査・Origin 無しは 403）／スマホの id8 つきの封筒 → `office_inbox` → ack。
- verify ▶11: `--bot` の道具が 5 つ・出勤できる・`office_status` を呼べない／テスト用ポートの `/api/office` に Bot が 1 人・中継へ送る形は Bot の表の鍵だけ
  （本文・パスの鍵なし・`sources.door` なし）。
- verify ▶8 のカナリアは `vendor=="claude"` だけを数える（Bot が居ても transcript の形の変化を見落とさない）。
- 手で: `python3 server/office_door.py add demo --name 見回り` → `checkin`／`report` → Mac の画面（またはテスト用ポート 4797 の `/api/office`）に `bot-demo`。
- 記録の画面（R128 #3・§9）: `tests/test_door_log.py`（7 種類の ev → 行・伏字と毒の文・壊れた行・大きすぎる行・封書に収まる縮め方・差分と遡り・
  作り直しの後の reset と遡りの停止・形の違う id／登録なし／リンク・読むだけ・呼び手の bot- の枝）・`tests/test_door_e2e.py` の記録の節（本物の daemon の
  `/api/session/activity` と、別プロセスの relay_agent → `/api/dialog/sealed` → 端末の秘密で開いた封書が同じ行）・`tests/activity.test.mjs`／
  `activity_view.test.mjs`（描き手）・`hud_send_ask.test.mjs`（3 本を出さない）・`app_smoke.py`（Mac の中央）・`pwa3d_smoke.py` の `_r128_pins`（スマホ）。
- Bot パネルと消音（R128 #4・§10）: `tests/test_door_panel.py`（口の消音と台帳・登録コマンドの形・API の往復と断りの code・Origin／CSRF／loopback の門・
  中継と MCP の道が無い・台帳を書く関数は 3 つ・中継の平文に muted が無い・Mac の知らせ＝新しい ❗／待たせすぎ／朝のまとめ・`_watch_once` の 1 周を本物の
  口のファイルで・daemon の作り直しでも効く・4 様式の配線とスマホの束が muted を読まない）・`test_office_door` の RowTest（`muted`）・`test_relay_agent` の
  表のピン（`muted` は載らない）・`tests/sound.test.mjs`（音）・`hud_board_replay.test.mjs`（`mutedSessions`）・`app_smoke.py` の R128 #4（断り → 追加 → 名前の毒 →
  コピー → 消音で画面の通知を出さない → 2 回押しの削除）・`cockpit_smoke.py`（┤ Bot ├ の群と角・文字の段）。守る線は 33 通りの変異で赤（§10）。

## 9. 記録の画面（R128 #3・Mac の中央とスマホの端末画面が同じ行を見る）
- **読み手**= `server/source_door_log.py`（`office_term.session_page` の `bot-` の分岐）。呼び手は Codex（`cx-`）の先例と同じ 2 か所:
  Mac の `GET /api/session/activity`（`office_server.activity_page`）とスマホの封書 `kind:"term"`（`_dialog_sealed` → `_term_sealed`）。どちらも `bot-` では
  transcript を探さない（`_session_transcript` を呼ばない）。会話ビュー（`kind:"dialog"`）は Bot には開けない＝`notfound`。スマホで記録が見えるのは
  「スマホに会話を見せる」（dialogRelay）が ON の間だけ（セッションと同じ門・OFF は `denied`）。
- **読む場所の閉じ込め**: `bot-<id>` の `<id>` を `valid_id` で確かめる（`bot_of_session`）・**登録した Bot だけ**（`is_registered`・台帳が読めなければ誰も）・
  口のフォルダ（`office_door/` と `bot/`）がリンクなら読まない・記録はリンクを辿らず、待たずに開く（`O_NOFOLLOW`＋`O_NONBLOCK`＝FIFO に替わっていても
  書き手を待って止まらない・開いてから種類を見て断る）・通常のファイルだけ。外れたら `FileNotFoundError`
  （Mac は空の頁 200・スマホは `notfound`）。**読むだけ**（ロックも書き込みも掃除もしない＝期限の切れた相談も閉じない）。
- **行の形**（office_term の行の種類に載せる・新しい種類は `ev` だけ＝古い描き手は知らない種類として黙って飛ばす）:

  | 記録の `ev` | 行 | 画面（`ui/core/activity.js` → `ui/hud/activity.js`） |
  |---|---|---|
  | `report` | `{k:"a", t:題＋空行＋本文, rk:"done"／"info"／"warn"}` | Bot の本文。種類の札（報告／お知らせ／注意＝注意だけ attn の字）と題（1 行目）・本文は Markdown を要素で |
  | `ask` | `{k:"q", t:問い, o:[選択肢]}` | 質問のカード（開いている相談なら答えの操作がこのカードに付く） |
  | `answer` | `{k:"u", t:答え, as:"answer", ch:選んだ番号?}` | 人の吹き出し＋札「相談への答え（選択肢 N）」／「相談への答え」 |
  | `instruct` | `{k:"u", t:指示, as:"instruct"}` | 人の吹き出し＋札「指示」 |
  | `checkin` | `{k:"ev", e:"working"／"waiting"／"resting", t:doing}` | 1 行「出勤／出勤（指示待ち）／休憩 doing」 |
  | `expired` | `{k:"ev", e:"expired"}` | 1 行「相談の期限が切れました（答えはありません）」 |
  | `withdraw` | `{k:"ev", e:"withdrawn"}` | 1 行「Bot が相談を取り下げました」 |

  各行に `ts`（整数の秒・読めなければ付けない＝数でない・範囲の外・`10**400` のような巨大な整数でも行は残して頁を落とさない）。**相談の id（askId・id8）は行に載せない**。知らない `ev`・形の違う値・壊れた行（JSON でない・深い入れ子・
  壊れた UTF-8）は黙って飛ばす。退勤は記録に書かれない（出勤の期限と表示の窓＝時刻で決まる）＝いまの状態は見出しの札が言う。
- **誰が・いつ・何を**: Bot＝本文と 1 行・人＝吹き出し（答えか指示かは札）。`record` の描き方（Mac の app・cockpit とスマホが Bot のときに渡す）は
  考え中を出さず、最初の行と 2 分以上の間に時刻・日が替わったら（2 分たっていなくても区切って）日付（M/D）を添える。答えの吹き出しの札で選んだ選択肢と自由な答えが分かれ、期限切れ・
  取り下げは吹き出しではない 1 行＝答えと混ざらない。
- **文**: Bot が書いた文も人の文も、行にするときに `office_term._fit`（ホームの下のパスを畳む → `office_actions.scrub_output` の秘密の伏字・パス→basename
  → 上限で切る）を通す。HTML として無害化はしない（描く側は要素と textContent だけ）。上限は office_term と同じ（報告 1600 字/40 行・問い 1600 字/6 行・
  選択肢 60 字×4・人の言葉 800 字/20 行・doing 200 字）・1 頁 `ROWS_MAX`・封書は `fits`／`shrink`（古い方から落として `gap`・`beg` も進む）。
- **カーソル**: 差分 `{off, h}`・遡り `{off, h, i}` は `office_term.read_page`／`read_before` と同じ約束。記録は `LOG_MAX` を超えると新しい半分で
  作り直される（別の inode）＝合わない差分は reset（末尾から）・合わない遡りは None（遡りを止める）。
- **Mac**: Bot を選ぶと中央にその記録（app・cockpit）。選択肢の無い Bot の相談にセッション向けの 3 本（承認して進める・いったん停止・進捗を報告）を出さない
  （`ui/hud/tray.js` の `answerActs`＝自由な文だけ・スマホの R127 と同じ作法）。「ターミナルを前へ」（`#app-jump`・`#cp-jump`・シートの `#sheetterm`）は
  Bot には出さない（ロボのダブルクリックも何もしない）・シートの会話の節も出さない。届き方の案内は「Bot が次に出勤したときに読みます」。Bot は人の言葉で
  記録が増えても行が変わらないことがある（未読が立ったままの 2 通目・別の画面からの答え）＝app は送れたらすぐ取り直し、記録を取り直す印に未読（`pending`）の
  替わり目と Bot が最後に動いた時刻（`mtime`）を足し、**Bot を選んでいる間は 5 秒ごとに差分を取りに行く**（カーソルつき＝増えていなければ 0 行・
  `ui/hud/activity_feed.js` の `botRecordDue`＝app と cockpit が同じ関数）。セッションは今までどおり変化があったときだけ。
- **スマホ**: Bot の入口は「記録を見る」（シート）・一覧の行と端末の切替も記録の画面を開く（`canTermFor` が Bot を通す）。「このプロジェクトを起動」・
  セッション向けの定型（3 本）・会話を見る（dialog）は出さないまま。Bot を「セッション」と呼ばない（宛先の行に「このセッションだけ」を付けない）・送るボタンは
  相談があれば「この答えを送る」・無ければ「この言葉を送る」。入力欄と届き方の案内・見つからない（古い Mac は `bot-` に `notfound`）・
  見えない理由（会話を見せるが OFF）は Bot の言葉。作業ビューが読めない旧い描き方にも記録の 1 行。

## 10. Bot パネル（R128 #4・⚙ の一覧・追加・削除・消音・登録コマンド＝Mac の前の人だけ）

### API（`server/office_server.py`・office_json には載せない）
| 道 | 門 | 中身 |
|---|---|---|
| `GET /api/door/bots` | loopback＋CSRF（`X-Office-Local`・Origin があれば loopback） | `{ok, ledger（台帳が読めたか）, max（64）, bots:[{bot, session, name, role, added, lastSeen, age, ask, unread, muted, mcp}]}`。`lastSeen`＝最後に動いた時刻（0＝一度も）・`age`＝そこからの秒（一度も動いていなければ `null`）・`ask`＝開いている相談があるか・`unread`＝未読の数・`mcp`＝登録コマンド（§3）。相談の文・未読の中身・doing は載せない（数と有無だけ） |
| `POST /api/door/bot/add` | `_STRICT_POST`（loopback＋`X-Office-Local`＋**Origin 必須**・`Sec-Fetch-Site` があれば same-origin） | `{bot, name?, role?}`。同じ id は名前と役の直し（登録時刻と消音は保つ）。名前・役を省く（`null`）と既定（名前＝id・役＝Bot）。成功は口の結果＋`mcp` |
| `POST /api/door/bot/remove` | 同上 | `{bot}`。台帳から外し、状態と記録（`<id>.json`・`<id>.log.jsonl`）も消す＝取り消せない |
| `POST /api/door/bot/mute` | 同上 | `{bot, on: true／false}`（真偽だけ・`"true"`・1 は断る）。同じ値なら書かない（`changed:false`） |

- 断りは **400 と `{ok:false, code, error}`**＝`code` は口の `error`（`bad-args`・`unregistered`・`ledger-broken`・`too-many`）・`error` は口の文
  （`OFFICE_LANG`／設定の言語）。入力の検査は全部 `office_door`（`valid_id`・名前 24 字・役 16 字・見えない文字だけの名前は空＝断る・64 体・壊れた台帳は上書きしない）。
- 書けたら `_mark_written()`＝直した直後の `/api/office` に今の登録と消音が出る（read-your-writes）。振り分けは `DOOR_BOT_ROUTES`（3 本）＝`_STRICT_POST` に同じ
  3 本（`test_door_panel` がピン）・前方一致（`/api/door/bot/addX` も Origin が要る）・枝の中でも loopback を見る。
- **道が無い所**: 中継（`relay_agent` は `/api/door/` も `add_bot`／`remove_bot`／`set_muted` も呼ばない＝封筒にも枝にも無い）・MCP（Bot の道具は 5 つのまま・
  いつもの 3 つにも無い）・Bot の道具（台帳を書く関数は `add_bot`／`remove_bot`／`set_muted` だけ＝AST で見る）。同じユーザーのプロセスは CLI と偽の Origin で
  登録・削除・消音できる（§3 の信頼の内側・残るリスク= `docs/security-notes.md` R128 #4）。

### 消音（鳴らさないだけ）
- 状態は台帳の `muted`（不変条件 9＝ファイル・daemon を作り直しても効く）。行に `muted`（§2）。
- **鳴らさないもの（Mac）**: daemon の知らせ（`_watch_once`＝`attention_diff` の新しい ❗・`secretary_nudges` の待たせすぎ・`build_morning_report` の朝のまとめ）と、
  画面の音（`ui/platform/sound.js` の chime と door）・画面の通知（`ui/hud/tray.js` の `Notification`）。消音の集合は daemon が `_quiet_sessions`（employees の
  bot- の行で `muted` が true・id の形も確かめる）、画面が `ui/hud/board.js` の `mutedSessions` で作り、様式の apply が `built.muted` に添える
  （`buildWorld` はスマホの束と共有＝そこには足さない）。
- **出し続けるもの**: Bot の行・状態・相談（`question`・`ask`）・あなたの番・受付・記録・タブの数（`(N❗)`）・日報（18 時）の数・「答えた ❗」の数。
- **解いた瞬間に前からの相談では鳴らさない**: 消音の間も鍵（`attention_diff` の集合・画面の `previous`／`queueIds`）と待たせすぎの段は進める＝解いた後は
  次の新しい相談・次の段から鳴る。
- **スマホは鳴る（今回の範囲の外）**: スマホの Push は Worker の `computeAttnNow`（中継の平文の `question`・`approvalMin` から決める）。消音の印を中継の平文に
  1 つ足しても、Worker（`relay/src/worker.js`・Durable Object）を変えなければ Push は止まらない＝`muted` は中継に載せない（`_ALLOW_DOOR_ENTRY` に無い・
  `test_relay_agent` の表のピン）。スマホの音も同じ。止めるなら R130（中継の口）で Worker と一緒に決める。

### 画面（`ui/hud/bots.js`・⚙ の設定＝`ui/hud/admin.js` の `renderSettings`）
- 置き場: app・iso・pixel は節の見出し「Bot」と上の罫、cockpit は `┤ Bot ├` の群（表示・通知・スマホ・**Bot**・診断・このアプリの 6 群）。デモは
  `demo_server_only` の 1 行だけ（サーバーが無い）。
- 一覧（1 体 1 行・端末の一覧 `.mdev` と同じ段）: 名前・役・消音中／id・最後に動いた時刻（`agoStr`）か「まだ出勤していません」・相談あり・未読 N／
  登録コマンド（等幅・折り返し・選びやすく）／［消音にする｜消音を解く］［登録コマンドをコピー］［削除］（取り消せない操作を最後に）。
- 0 体: 「Bot はまだいません。常駐のスクリプトや Bot を登録すると、出勤・報告・相談がこのオフィスに並び、あなたの番から答えられます。」
- 追加: id（例: mailbox）・名前・役（省くと Bot）＋［登録］。id の決まりは欄の下の注記。断りはサーバー（口）の文を欄の下に残す（トーストは消える）。
- 削除は **2 回押し**（1 回目で 3 秒だけ「もう一度押すと削除（状態と記録も消えます）」＝admin の「外す」「まとめて失効」と同じ作法）。
- コピーは `navigator.clipboard`（行に見えているのと同じ 1 行）・押すと「コピーしました」・使えなければ行のコマンドを選んでおいて ⌘C を案内。
- Bot の名前・役・id は**必ず文字**（`mEl` の textContent・innerHTML を使わない）。要素は参照で持つ（`querySelector` の文字で探さない＝hud の DOM の契約に数えない）。
- 色・形は既存の部品だけ（`.mdev`・`.mbtn`・`.mrevoke`・`.minput`・`.mgo`・`.mnote`）＋`.mbotcmd`（罫と地の明度差だけ）。左の帯・影・グラデーション・絵文字は無い。
  cockpit では角 0・文字の段（12/14）の門（`cockpit_smoke` の R105-A）を通る。

### 変異で赤を確かめた線（2026-11-14・`tests/test_door_panel.py` ほか）
書き換えの POST を `_STRICT_POST` から外す・POST の loopback・GET の loopback と CSRF・`_mark_written`・断りの code・`muted` を真偽の true 以外でも読む・
直しで消音を落とす・`on` の型・壊れた台帳への書き込み・登録コマンドの `valid_id` と `shlex.quote`・行の `muted`（RowTest）・Bot の道具が台帳を書く・
`_quiet_sessions` が空／Bot 以外も効く・`attention_diff` の quiet と鍵の控え・待たせすぎの quiet と段の前進・朝のまとめ・`_watch_once` の 2 か所の受け渡し・
中継の表に `muted`・`relay_agent` からの消音・`sound.js` の chime と door・`mutedSessions` の形・cockpit の apply の配線（以上 29 は unittest／node）と、
実ブラウザの `app_smoke`（tray の消音・名前の innerHTML・削除の 1 回押し・app の apply の配線）。
