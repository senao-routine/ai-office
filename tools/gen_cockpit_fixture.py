#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コックピット（R99）の固定ワールド 2 本を作り直す。**この 2 本の正本はこのスクリプト**（JSON を手で直さない）。

  tests/fixtures/world/cockpit.json       golden 用（18 セッション・部屋に全員が入る・語彙が全部そろう・💬 の 600 秒の境の外も 1 人）
  tests/fixtures/world/cockpit_peak.json  ピーク用（47 セッション・部屋の定員を超える＝あふれを証明する）

使い方:
  python3 tools/gen_cockpit_fixture.py           2 本を書き直す
  python3 tools/gen_cockpit_fixture.py --check   書かずに比べるだけ（違えば exit 1）＝tests/cockpit_fixture.test.mjs が呼ぶ

なぜ生成器か: 手で書いた fixture は「サーバーが出さない形」を黙って混ぜる（第 1 波で実測: 思考中の人の
feed に「考え中…」＝parse_session は thinking を feed に入れない）。ここでは
  - roster は server の group_by_project(mode="session")、tasks は task_totals、OpenClaw 社員は
    openclaw_source.parse_openclaw_status に**作らせる**（形を手で捏造しない）
  - Claude の state / 「指示待ち」「休憩中」への言い換え / approvalMin / question / stuckTool は、
    parse_session と同じ規則で age・kind・ask から**導く**（食い違った組を書けない）。質問まちの
    target・question・questionOptions は、合成した AskUserQuestion の入力を server に通して作らせる
  - Claude の feed は「TOOL_VERB の動詞 対象」か「💬 本文」だけ（違えば ValueError）
evidence は scan_office と同じく roster を作った**後**に employees にだけ付ける。
実在の人名・社名・パスは使わない（パスは /Users/test/work/ の下の架空のフォルダ）。
basic.json / xl22.json は iso・pixel の golden が依存するので、ここでは作らない。
"""
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORLD = ROOT / "tests" / "fixtures" / "world"
CMD = "python3 tools/gen_cockpit_fixture.py"
NOW = 1790200000.0
W = "/Users/test/work/"


def load_server(home):
    """server/office_server.py を一時 HOME で読み込む（本物の ~/.claude には触らない）。"""
    os.environ["OFFICE_HOME"] = home
    spec = importlib.util.spec_from_file_location("office_server_fx", ROOT / "server" / "office_server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    return srv


class Maker:
    """1 本の fixture の employees を、各ソースの形で組み立てる。"""

    def __init__(self, srv):
        self.srv = srv
        # parse_session の feed に出る動詞＝describe_tool の辞書と、辞書に無いツール・MCP ツールの動詞
        self.tool_verbs = (set(srv.TOOL_VERB.values())
                           | {srv.describe_tool("", {})[0], srv.describe_tool("mcp__fx__probe", {})[0]})
        self.codex_verbs = {ja for ja, _en in srv.source_codex.VERB.values()} | {"作業中"}
        self.claude_rows = []
        self.codex_rows = []
        self.other_rows = []
        self.openclaw = []

    # ── Claude（server の parse_session と同じ形・同じ規則） ─────────────────
    def claude(self, session, cwd, dept, role, age, kind, verb, target="", *, prompts=False, **over):
        """kind: tool / said / think / order。verb は**イベントの動詞**（said なら「報告中」）。
        prompts=True は「そのツールが人間に聞き得る」（can_prompt）＝age>75 で承認まちになる。"""
        for key in ("state", "approvalMin", "question", "stuckTool"):
            if key in over:
                raise ValueError(f"{session}: {key} は age・kind・ask から導く（手で書かない）")
        expect = {"tool": None, "said": "報告中", "think": "考え中…", "order": "指示を受領"}
        if kind not in expect:
            raise ValueError(f"{session}: kind={kind} は parse_session が出さない")
        if kind == "tool" and verb not in self.tool_verbs:
            raise ValueError(f"{session}: 動詞「{verb}」は describe_tool が出さない")
        if expect[kind] and verb != expect[kind]:
            raise ValueError(f"{session}: kind={kind} の動詞は「{expect[kind]}」")
        feed = over.pop("feed", [])
        ask_verb, asked, feed_target = self.srv.TOOL_VERB["AskUserQuestion"], None, target
        if kind == "tool" and verb == ask_verb:
            # parse_session: 状態の対象と question は質問文＋「（ラベル / ラベル）」（_question_text・120 字）、
            # feed は describe_tool の質問文だけ（42 字）＝合成した tool_use の input を server に通して作らせる
            asked = {"question": target, "options": [{"label": o["label"], "description": o["desc"]}
                                                     for o in over.get("questionOptions") or []]}
            feed_target = self.srv.describe_tool("AskUserQuestion", {"questions": [asked]})[1]
            target = self.srv._question_text({"input": {"questions": [asked]}}, target)
        self._check_feed(session, kind, verb, feed_target, feed)
        ask = over.get("ask")
        # parse_session: age<25 は稼働・tool/think/order は 240 秒まで稼働・30 分までは待機・それ以降は休憩
        if age < 25 or (age < 240 and kind in ("tool", "think", "order")):
            state = "working"
        elif age < 1800:
            state = "waiting"
        else:
            state = "resting"
        ev_verb, ev_target = verb, target
        if state == "waiting" and kind == "said":
            verb = "指示待ち"
        if state == "resting":
            verb, target = "休憩中", ""
        approval, question = 0, ""
        if kind == "tool" and age > 75:
            if asked is not None:
                question = ev_target
                opts = self.srv._question_options(asked)   # 選択肢も transcript から server の形で
                if opts:
                    over["questionOptions"] = opts
                else:
                    over.pop("questionOptions", None)
            elif age < 1800 and prompts:
                approval = max(1, int(age // 60))
        if ask:
            state = "waiting"   # R86-H: 掲示がある＝あなた待ち
            if ask["kind"] == "question":
                question = question or ask["title"]
            elif not approval:
                approval = max(1, int((NOW - ask["ts"]) // 60))
        if over.get("questionOptions") and not question:
            raise ValueError(f"{session}: 質問が無いのに選択肢がある")
        # R110: 最後の工具名（server の parse_session は最新の assistant に tool_use が在るとき＝kind tool のときだけ持つ）。
        # 動詞から逆引き（TOOL_VERB は {工具名: 動詞}・同じ動詞を出す工具は辞書の順で最初のもの）＝部屋の用事の根拠。
        tool = ""
        if kind == "tool":
            tool = next((name for name, v in self.srv.TOOL_VERB.items() if v == ev_verb), "")
        row = {
            "session": session, "vendor": "claude", "cwd": cwd, "branch": "main", "title": "",
            "age": age, "mtime": NOW - age, "state": state, "kind": kind,
            "verb": verb, "tool": tool, "target": target, "feed": feed, "skills": [], "minions": 0,
            "pending": False, "listening": False, "lastSaid": "", "lastOrder": "",
            "question": question, "approvalMin": approval,
            "stuckTool": f"{verb} {target}".strip() if approval else "",
            "dept": dept, "role": role,
        }
        row.update(over)
        self.claude_rows.append(row)
        return row

    def _check_feed(self, session, kind, verb, target, feed):
        """feed は tool_use の「動詞 対象」と text の「💬 本文」だけ（thinking は feed に入らない）。
        target は feed に出る形（質問なら選択肢の接尾辞なし）。"""
        for line in feed:
            if line.startswith("💬 "):
                continue
            if not any(line == v or line.startswith(v + " ") for v in self.tool_verbs):
                raise ValueError(f"{session}: feed「{line}」は parse_session が出さない形")
        if feed and kind == "tool" and feed[0] != f"{verb} {target}".strip():
            raise ValueError(f"{session}: 最新の feed は今のツール「{verb} {target}」のはず")
        if feed and kind == "said" and not feed[0].startswith("💬 "):
            raise ValueError(f"{session}: 報告（said）の最新の feed は 💬 のはず")

    # ── Codex（server/source_codex.py の形） ─────────────────────────────
    def codex(self, thread, cwd, name, source, age, state, verb, minions=0, tokens=0):
        if verb not in self.codex_verbs:
            raise ValueError(f"cx-{thread}: 動詞「{verb}」は source_codex が出さない")
        row = {
            "session": "cx-" + thread, "cwd": cwd, "branch": "main", "title": name,
            "age": age, "mtime": NOW - age, "state": state,
            "kind": {"working": "tool", "waiting": "said", "resting": "idle"}[state],
            "verb": verb, "target": "", "feed": [verb], "skills": [], "minions": minions,
            "pending": False, "listening": state != "resting", "lastSaid": "", "lastOrder": "",
            "question": "", "approvalMin": 0, "stuckTool": "",
            "dept": name or "Codex", "role": source, "vendor": "codex", "tokens": tokens,
        }
        self.codex_rows.append(row)
        return row

    # ── 未知のベンダー（今のサーバーには無い将来のソースの代役） ─────────────
    def other(self, session, cwd, dept, role, age, state, verb, vendor="futurecli"):
        """vendor が claude / codex / openclaw のどれでもない人。UI が「other」として運び、
        claude に潰さないこと（B3）を証明する材料。形は source_codex に倣う。"""
        row = {
            "session": session, "cwd": cwd, "branch": "main", "title": "",
            "age": age, "mtime": NOW - age, "state": state,
            "kind": {"working": "tool", "waiting": "said", "resting": "idle"}[state],
            "verb": verb, "target": "", "feed": [verb], "skills": [], "minions": 0,
            "pending": False, "listening": state != "resting", "lastSaid": "", "lastOrder": "",
            "question": "", "approvalMin": 0, "stuckTool": "",
            "dept": dept, "role": role, "vendor": vendor,
        }
        self.other_rows.append(row)
        return row

    # ── OpenClaw（別 Mac の外部社員）＝契約 JSON を server に変換させる ─────────
    def openclaw_agents(self, agents, site="mini"):
        raw = {"v": 1, "site": site, "generatedAt": NOW - 5, "agents": agents}
        rows, meta = self.srv.openclaw_source.parse_openclaw_status(raw, NOW, lang="ja")
        if not meta["connected"] or len(rows) != len(agents):
            raise ValueError(f"OpenClaw の契約 JSON が変換されない: {meta}")
        self.openclaw = rows

    # ── scan_office と同じ順・同じ採番で並べ、roster を server に作らせる ─────
    def office(self, note, evidence, history, today):
        srv = self.srv
        # scan_office: Claude は mtime の新しい順に並べて採番 → OpenClaw → Codex は source_codex が
        # threads を ORDER BY id で返した順のまま採番して並べる（session=cx-<id>＝同じ順）。
        # 未知のベンダーは source_codex に倣う。
        cl = sorted(self.claude_rows, key=lambda e: e["mtime"], reverse=True)
        cx = sorted(self.codex_rows, key=lambda e: e["session"])
        ot = sorted(self.other_rows, key=lambda e: e["session"])
        for group in (cl, cx, ot):
            seen = {}
            for e in group:   # dept ごと・ソースごとに独立に採番（scan_office）
                n = seen.get(e["dept"], 0) + 1
                seen[e["dept"]] = n
                e["disp"] = e["dept"] if n == 1 else f"{e['dept']} {n}号"
        employees = cl + self.openclaw + cx + ot
        sessions = [e["session"] for e in employees]
        if len(set(sessions)) != len(sessions):
            raise ValueError("session が重複している")
        roster = srv.group_by_project(employees, "ja", mode="session")
        for e in employees:
            if e["session"] in evidence:
                e["evidence"] = evidence[e["session"]]
        missing = set(evidence) - set(sessions)
        if missing:
            raise ValueError(f"evidence の宛先が居ない: {sorted(missing)}")
        disp = {e["session"]: e["disp"] for e in employees}
        return {
            "_note": note,
            "v": 2,
            "officeName": "テストオフィス",
            "lang": "ja",
            "generatedAt": NOW,
            "setup": {"hookInstalled": True, "eventsWired": True, "approvalWired": True},
            "employees": employees,
            "sources": {"claude": {"fg": len(cl), "bg": 0, "agentsCli": False},
                        "codex": {"connected": True, "n": len(self.codex_rows), "reason": ""},
                        "openclaw": {"connected": bool(self.openclaw)}},
            "roster": roster,
            "history": [{"session": s, "disp": d or disp[s], "text": text, "ts": NOW - ago, "pending": False}
                        for s, d, text, ago in history],
            "today": today,
            "launchable": [],
            "avatarMode": "session",
            "counts": {s: sum(1 for e in employees if e["state"] == s) for s in ("working", "waiting", "resting")},
            "tasks": srv.task_totals(roster),
        }


NOTE_TAIL = (f"正本は tools/gen_cockpit_fixture.py（手で直さない・作り直し= `{CMD}`・"
             "一致は tests/cockpit_fixture.test.mjs が --check で見る）。"
             "employees は server の parse_session / source_codex / openclaw_source の形、"
             "roster は server の group_by_project(mode=\"session\") の出力そのまま。"
             "evidence は scan_office と同じく employees にだけ付く。vendor=futurecli は今のサーバーに無い"
             "将来のソースの代役（UI が other として運ぶことの材料）。"
             "basic.json / xl22.json は iso・pixel の golden が依存するので触らない。")


def build_cockpit(srv):
    """golden 用: 語彙が全部そろい、部屋（定員 26）に全員が入る 18 セッション。"""
    m = Maker(srv)
    c = m.claude
    # ── atlas-api: 3 本並走（claude 2 ＋ codex 1）＝同じフォルダの別ベンダー ──
    c("sess-cp-01", W + "atlas-api", "atlas-api", "開発", 6, "tool", "編集中", "router.ts",
      feed=["編集中 router.ts", "実行中 npm test", "💬 ルーターの型を揃えました"],
      lastSaid="ルーターの型を揃えました", lastOrder="ルーターの型を揃えて",
      work={"now": ["ルーターの型"], "next": ["ミドルウェア"], "done": ["エンドポイント一覧"],
            "counts": {"pending": 2, "in_progress": 1, "completed": 4}})
    # order＝指示を受け取った直後（まだ手を付けていない）
    c("sess-cp-02", W + "atlas-api", "atlas-api", "開発", 40, "order", "指示を受領", "型を直してから push して",
      lastOrder="型を直してから push して", feed=["編集中 schema.ts"])
    m.codex("cp0000000000a3", W + "atlas-api", "型エラーの修正", "cli", 12, "working", "実行中", tokens=48000)
    # ── ブログ編集部: 読書（読込中）と「報告して待機」（kind=said・waiting） ──
    c("sess-cp-04", W + "blog-studio", "ブログ編集部", "記事制作", 14, "tool", "読込中", "outline.md",
      feed=["読込中 outline.md", "調査中 draft-*.md"])
    c("sess-cp-05", W + "blog-studio", "ブログ編集部", "記事制作", 520, "said", "報告中",
      "下書きを 3 本まとめました。どれから公開しますか", listening=True,
      feed=["💬 下書きを 3 本まとめました。どれから公開しますか", "執筆中 draft-03.md"],
      lastSaid="下書きを 3 本まとめました。どれから公開しますか", lastOrder="下書きを 3 本書いて")
    # ── 動画編集部: 思考中（kind=think・机に居たまま）。thinking は feed に入らない＝直前のツールだけ ──
    c("sess-cp-06", W + "video-cut", "動画編集部", "本編カット", 50, "think", "考え中…", "",
      feed=["実行中 ffprobe intro.mp4"], lastOrder="冒頭 30 秒を詰めて")
    # 報告して 20 分返事を待っている人（kind=said・waiting・age ≥ 600）＝💬 の上限（統括の既定 600 秒）の外。
    # 💬 は出ず、台帳だけが持つ（tests/cockpit_smoke.py の門 19・tests/cockpit_fixture.test.mjs が「💬 が出ない」を見る）。
    # 台帳の並びで思考中の人（sess-cp-06）より後ろ＝机 9 席は今の 9 人のまま（前に置くと思考中の人が机からあふれ、
    # golden から 💭 の机の絵が消える）。机からあふれて受付の standby に立つ（standby は 💬 を消さない＝cx-…b1 が証拠）
    c("sess-cp-13", W + "video-cut", "動画編集部", "書き出し", 1200, "said", "報告中",
      "書き出しが終わりました。確認をお願いします", listening=True,
      feed=["💬 書き出しが終わりました。確認をお願いします", "実行中 render.sh final"],
      lastSaid="書き出しが終わりました。確認をお願いします", lastOrder="本編を書き出して")
    # ── インフラ: 承認まち（stuckTool＝❗）と、❗ではないがツールが長く止まっている人 ──
    c("sess-cp-07", W + "infra-ops", "インフラ", "運用", 380, "tool", "実行中", "deploy.sh staging", prompts=True,
      feed=["実行中 deploy.sh staging", "編集中 stack.yml"],
      ask={"tool": "Bash", "kind": "permission", "title": "deploy.sh staging を実行しますか",
           "options": [], "ts": NOW - 360})
    c("sess-cp-08", W + "infra-ops", "インフラ", "運用", 960, "tool", "実行中", "npm run build:all",
      feed=["実行中 npm run build:all"])
    # ── 議事録アプリ: 質問まち（AskUserQuestion の掲示） ──
    c("sess-cp-09", W + "memo-app", "議事録アプリ", "開発", 200, "tool", "質問中(返答待ち)", "保存先はどちらにしますか？",
      questionOptions=[{"label": "ローカル", "desc": "推奨"}, {"label": "共有フォルダ", "desc": ""}],
      ask={"tool": "AskUserQuestion", "kind": "question", "title": "保存先はどちらにしますか？",
           "options": ["ローカル", "共有フォルダ"], "ts": NOW - 190},
      feed=["質問中(返答待ち) 保存先はどちらにしますか？"])
    # ── リサーチ室: サブエージェント稼働中（minions>0＝会議室）と、リサーチ中 ──
    c("sess-cp-10", W + "research-lab", "リサーチ室", "調査", 20, "tool", "サブエージェントに指示中", "3 方向の比較調査",
      minions=3, feed=["サブエージェントに指示中 3 方向の比較調査"],
      work={"now": ["比較表"], "next": ["まとめ"], "done": [],
            "counts": {"pending": 1, "in_progress": 1, "completed": 0}})
    c("sess-cp-11", W + "research-lab", "リサーチ室", "調査", 30, "tool", "リサーチ中", "dot art palette 16 colors",
      title="配色の下調べ", feed=["リサーチ中 dot art palette 16 colors"])
    # R110: 段取り（TodoWrite）＝部屋の用事「ボードの手前で書く」の根拠を固定ワールドにも 1 人
    c("sess-cp-14", W + "memo-app", "議事録アプリ", "開発", 22, "tool", "段取り整理中", "保存先の切替 3 件",
      feed=["段取り整理中 保存先の切替 3 件", "読込中 settings.ts"],
      work={"now": ["保存先の切替"], "next": ["共有フォルダの権限"], "done": ["ローカル保存"],
            "counts": {"pending": 1, "in_progress": 1, "completed": 1}})
    # 同じフォルダで未知のベンダーが休憩中（ソファの 2 席目＝ソファ満席・vendor=other の材料）
    m.other("fc-cp0000000000c1", W + "research-lab", "リサーチ室", "cli", 3200, "resting", "作業中")
    # ── ショップ: テストが落ちた直後（証拠 failed） ──
    c("sess-cp-12", W + "shop-front", "ショップ", "フロント", 9, "tool", "実行中", "pytest -q",
      feed=["実行中 pytest -q", "編集中 cart.py"])
    # ── データ整形（codex だけのプロジェクト）: 報告して待機 と 休憩 ──
    m.codex("cp0000000000b1", W + "data-pipe", "データ整形", "vscode", 300, "waiting", "報告中", tokens=12000)
    m.codex("cp0000000000b2", W + "data-pipe", "データ整形", "cli", 2400, "resting", "編集中", tokens=3000)
    m.openclaw_agents([{"id": "helper-1", "name": "OpenClaw", "state": "working", "verb": "応答中",
                        "channel": "chat", "age": 15}])
    return m.office(
        "R99 cockpit 用の固定ワールド（Phase 3 の印・動き・ポーズを golden とユニットで証明するため）。" + NOTE_TAIL,
        evidence={"sess-cp-01": {"kind": "tested", "ago": 180},
                  "sess-cp-05": {"kind": "committed", "ago": 900},
                  "sess-cp-12": {"kind": "failed", "ago": 60}},
        history=[("sess-cp-02", "atlas-api", "型を直してから push して", 45),
                 ("sess-cp-05", "ブログ編集部", "下書きを 3 本書いて", 3600)],
        today={"sent": 2, "answered": 1, "lastSentAgo": 45, "capped": False},
    )


def build_peak(srv):
    """ピーク用: 部屋の定員（机 9・受付 9・ソファ 2・会議 3・ラック前 3＝26）を超える 47 セッション。
    机・ソファ・会議室・ラック前がそれぞれあふれ、受付は確認待ちと standby で両端から埋まり、残りは部屋の外。

    台帳の並び（＝roomLayout が枠を配る順）は、❗の若い人が居るプロジェクトから。その先頭の 4 件
    （memo-app・atlas-api・docs-site・design-kit）に休憩 3 人と会議 4 人を寄せてある＝机が埋まる前に
    ソファと会議室があふれ、あふれた人が机で仮住まいする絵（want≠zone）が必ず描かれる。"""
    m = Maker(srv)
    c = m.claude

    def ask_perm(tool, title, ago):
        return {"tool": tool, "kind": "permission", "title": title, "options": [], "ts": NOW - ago}

    # 1. atlas-api（claude 3 ＋ codex 2: 編集・承認まち［掲示あり］・会議・待機・休憩）
    c("sess-pk-01", W + "atlas-api", "atlas-api", "開発", 5, "tool", "編集中", "handler.ts",
      feed=["編集中 handler.ts", "💬 ハンドラを 2 つに分けました"],
      work={"now": ["ハンドラ分割"], "next": ["型の整理"], "done": ["ルート一覧"],
            "counts": {"pending": 3, "in_progress": 1, "completed": 5}})
    c("sess-pk-02", W + "atlas-api", "atlas-api", "開発", 150, "tool", "実行中", "npm run migrate", prompts=True,
      feed=["実行中 npm run migrate", "編集中 schema.sql"],
      ask=ask_perm("Bash", "npm run migrate を実行しますか", 140))
    m.codex("pk0000000000a1", W + "atlas-api", "API の分割", "cli", 10, "working", "実行中", minions=2, tokens=52000)
    m.codex("pk0000000000a2", W + "atlas-api", "型の整理", "vscode", 260, "waiting", "報告中", tokens=8000)
    # ソファの 3 人目（先頭 2 件の休憩 2 人がソファを埋めた後）＝机で仮住まい
    c("sess-pk-33", W + "atlas-api", "atlas-api", "開発", 3300, "tool", "実行中", "npm run lint",
      feed=["実行中 npm run lint"])
    # 2. blog-studio（claude 3: 報告して待機・思考中・休憩）
    c("sess-pk-03", W + "blog-studio", "ブログ編集部", "記事制作", 700, "said", "報告中", "構成案を 2 つ出しました",
      listening=True, feed=["💬 構成案を 2 つ出しました", "執筆中 plan.md"],
      lastSaid="構成案を 2 つ出しました", lastOrder="構成案を考えて")
    c("sess-pk-04", W + "blog-studio", "ブログ編集部", "記事制作", 40, "think", "考え中…", "",
      feed=["読込中 draft-01.md"])
    c("sess-pk-05", W + "blog-studio", "ブログ編集部", "記事制作", 2600, "said", "報告中", "公開しました",
      listening=True, feed=["💬 公開しました"])
    # 3. video-cut（claude 3: 実行・会議・❗なしで長く止まっている）
    c("sess-pk-06", W + "video-cut", "動画編集部", "本編カット", 12, "tool", "実行中", "ffmpeg -i take2.mov",
      feed=["実行中 ffmpeg -i take2.mov"])
    c("sess-pk-07", W + "video-cut", "動画編集部", "字幕", 18, "tool", "サブエージェントに指示中", "字幕の下書き", minions=2,
      feed=["サブエージェントに指示中 字幕の下書き"])
    c("sess-pk-08", W + "video-cut", "動画編集部", "書き出し", 1100, "tool", "実行中", "render.sh final",
      feed=["実行中 render.sh final"])
    # 4. infra-ops（claude 3: 承認まち［掲示なし＝推測の❗］・編集・指示を受領）
    c("sess-pk-09", W + "infra-ops", "インフラ", "運用", 420, "tool", "実行中", "apply.sh prod", prompts=True,
      feed=["実行中 apply.sh prod", "編集中 main.tf"])
    c("sess-pk-10", W + "infra-ops", "インフラ", "運用", 30, "tool", "編集中", "stack.yml",
      feed=["編集中 stack.yml"])
    c("sess-pk-11", W + "infra-ops", "インフラ", "監視", 60, "order", "指示を受領", "監視の閾値を下げて",
      lastOrder="監視の閾値を下げて", feed=["読込中 alerts.yml"])
    # 5. memo-app（claude 3 ＋ codex 1: 質問まち［掲示あり］・会議・休憩 2）＝台帳の先頭
    c("sess-pk-12", W + "memo-app", "議事録アプリ", "開発", 130, "tool", "質問中(返答待ち)", "書式はどちらにしますか？",
      questionOptions=[{"label": "Markdown", "desc": "推奨"}, {"label": "テキスト", "desc": ""}],
      ask={"tool": "AskUserQuestion", "kind": "question", "title": "書式はどちらにしますか？",
           "options": ["Markdown", "テキスト"], "ts": NOW - 120},
      feed=["質問中(返答待ち) 書式はどちらにしますか？"])
    c("sess-pk-13", W + "memo-app", "議事録アプリ", "開発", 8, "tool", "サブエージェントに指示中", "議事録の要約を分担",
      minions=2, feed=["サブエージェントに指示中 議事録の要約を分担", "読込中 notes.md"])
    m.codex("pk0000000000b1", W + "memo-app", "保存形式", "cli", 3000, "resting", "編集中", tokens=4000)
    c("sess-pk-32", W + "memo-app", "議事録アプリ", "開発", 2800, "said", "報告中", "要約を保存しました",
      listening=True, feed=["💬 要約を保存しました"])
    # 6. research-lab（claude 3: 会議・リサーチ・思考中）
    c("sess-pk-14", W + "research-lab", "リサーチ室", "調査", 22, "tool", "サブエージェントに指示中", "4 方向の比較調査",
      minions=3, feed=["サブエージェントに指示中 4 方向の比較調査"],
      work={"now": ["比較表"], "next": ["まとめ"], "done": ["候補の洗い出し"],
            "counts": {"pending": 2, "in_progress": 1, "completed": 1}})
    c("sess-pk-15", W + "research-lab", "リサーチ室", "調査", 45, "tool", "リサーチ中", "palette contrast ratio",
      feed=["リサーチ中 palette contrast ratio"])
    c("sess-pk-16", W + "research-lab", "リサーチ室", "調査", 70, "think", "考え中…", "",
      feed=["リサーチ中 color names"])
    # 7. shop-front（claude 3: テスト失敗・報告して待機・編集）
    c("sess-pk-17", W + "shop-front", "ショップ", "フロント", 7, "tool", "実行中", "pytest -q",
      feed=["実行中 pytest -q", "編集中 cart.py"])
    c("sess-pk-18", W + "shop-front", "ショップ", "フロント", 400, "said", "報告中", "決済画面を直しました",
      listening=True, feed=["💬 決済画面を直しました"], lastSaid="決済画面を直しました")
    c("sess-pk-19", W + "shop-front", "ショップ", "在庫", 15, "tool", "編集中", "stock.csv",
      feed=["編集中 stock.csv"])
    # 8. data-pipe（codex だけ 3: 稼働・待機・休憩）。id の順（c1→c3）と新しい順（c3→c1）をわざと逆にする
    #＝採番が source_codex の ORDER BY id に従っていること（mtime 順ではない）を disp で区別できる
    m.codex("pk0000000000c3", W + "data-pipe", "データ整形", "cli", 9, "working", "実行中", tokens=20000)
    m.codex("pk0000000000c2", W + "data-pipe", "データ整形", "vscode", 250, "waiting", "報告中", tokens=6000)
    m.codex("pk0000000000c1", W + "data-pipe", "データ整形", "cli", 4000, "resting", "考え中…", tokens=1500)
    # 9. design-kit（claude 2: 会議［会議室の 4 人目＝机で仮住まい］・質問まち［掲示なし＝推測の質問］）
    c("sess-pk-20", W + "design-kit", "デザイン部", "部品", 11, "tool", "サブエージェントに指示中", "部品の棚卸し",
      minions=1, feed=["サブエージェントに指示中 部品の棚卸し", "執筆中 tokens.css"])
    c("sess-pk-21", W + "design-kit", "デザイン部", "部品", 380, "tool", "質問中(返答待ち)", "角丸は何 px にしますか？",
      questionOptions=[{"label": "4px", "desc": ""}, {"label": "8px", "desc": ""}],
      feed=["質問中(返答待ち) 角丸は何 px にしますか？"])
    # 10. mobile-app（claude 3 ＋ codex 1）
    c("sess-pk-22", W + "mobile-app", "アプリ開発", "画面", 4, "tool", "編集中", "App.tsx",
      feed=["編集中 App.tsx"],
      work={"now": ["設定画面"], "next": ["通知"], "done": [],
            "counts": {"pending": 4, "in_progress": 1, "completed": 0}})
    c("sess-pk-23", W + "mobile-app", "アプリ開発", "画面", 25, "tool", "サブエージェントに指示中", "画面遷移の洗い出し",
      minions=2, feed=["サブエージェントに指示中 画面遷移の洗い出し"])
    c("sess-pk-24", W + "mobile-app", "アプリ開発", "画面", 900, "said", "報告中", "設定画面を出しました",
      listening=True, feed=["💬 設定画面を出しました"])
    m.codex("pk0000000000d1", W + "mobile-app", "通知の下地", "cli", 16, "working", "編集中", tokens=15000)
    # 11. docs-site（claude 2: 承認まち［掲示あり・Write］・会議）
    c("sess-pk-25", W + "docs-site", "ドキュメント", "執筆", 200, "tool", "執筆中", "index.md", prompts=True,
      feed=["執筆中 index.md"], ask=ask_perm("Write", "index.md を書き換えますか", 190))
    c("sess-pk-26", W + "docs-site", "ドキュメント", "執筆", 35, "tool", "サブエージェントに指示中", "用語の洗い出し",
      minions=1, feed=["サブエージェントに指示中 用語の洗い出し", "調査中 TODO"])
    # 12. ml-bench（claude 2 ＋ codex 1 ＋ 未知のベンダー 1）
    c("sess-pk-27", W + "ml-bench", "評価基盤", "計測", 90, "think", "考え中…", "",
      feed=["実行中 bench.py --quick"])
    c("sess-pk-28", W + "ml-bench", "評価基盤", "計測", 5000, "tool", "実行中", "bench.py --full",
      feed=["実行中 bench.py --full"])
    m.codex("pk0000000000e1", W + "ml-bench", "計測の自動化", "cli", 20, "working", "実行中", tokens=9000)
    m.other("fc-pk0000000000f1", W + "ml-bench", "評価基盤", "cli", 10, "working", "作業中")
    # 13. sound-lab（claude 3: 実行・報告して待機・指示を受領）
    c("sess-pk-29", W + "sound-lab", "音響室", "整音", 3, "tool", "実行中", "sox mix.wav out.wav",
      feed=["実行中 sox mix.wav out.wav"])
    c("sess-pk-30", W + "sound-lab", "音響室", "整音", 330, "said", "報告中", "ノイズを落としました",
      listening=True, feed=["💬 ノイズを落としました"])
    c("sess-pk-31", W + "sound-lab", "音響室", "整音", 50, "order", "指示を受領", "音量をそろえて",
      lastOrder="音量をそろえて", feed=["実行中 sox --i mix.wav"])
    # 外部社員 5 体（ラック前は 3 枠＝2 体があふれる）。サーバーは OpenClaw に❗を出さない
    #（openclaw_source は approvalMin=0・question="" 固定）＝外部社員の❗あふれはこの fixture では起きない。
    m.openclaw_agents([
        {"id": "helper-1", "name": "OpenClaw", "state": "working", "verb": "応答中", "channel": "chat", "age": 15},
        {"id": "helper-2", "name": "OpenClaw", "state": "waiting", "verb": "返信待ち", "channel": "chat", "age": 300},
        {"id": "scribe", "name": "記録係", "state": "working", "verb": "要約中", "channel": "notes", "age": 40,
         "minions": 1},
        {"id": "courier", "name": "配達係", "state": "resting", "verb": "", "channel": "mail", "age": 2000},
        {"id": "tutor", "name": "練習相手", "state": "working", "verb": "応答中", "channel": "chat", "age": 60},
    ])
    return m.office(
        "R99 cockpit のピーク時の固定ワールド（部屋の定員 26 を超える 47 セッション＝机・ソファ・会議室・"
        "ラック前のあふれ・受付の standby・部屋の外を roomLayout で証明するため。golden には使わない）。" + NOTE_TAIL,
        evidence={"sess-pk-01": {"kind": "tested", "ago": 120},
                  "sess-pk-10": {"kind": "tested", "ago": 600},
                  "sess-pk-17": {"kind": "failed", "ago": 40},
                  "sess-pk-18": {"kind": "committed", "ago": 700},
                  "sess-pk-24": {"kind": "committed", "ago": 1500}},
        history=[("sess-pk-11", None, "監視の閾値を下げて", 70),
                 ("sess-pk-31", None, "音量をそろえて", 60),
                 ("sess-pk-03", None, "構成案を考えて", 1500)],
        today={"sent": 6, "answered": 3, "lastSentAgo": 60, "capped": False},
    )


BUILDERS = {"cockpit": build_cockpit, "cockpit_peak": build_peak}


def render(office):
    return json.dumps(office, ensure_ascii=False, indent=1) + "\n"


def main(argv):
    check = "--check" in argv
    home = tempfile.mkdtemp(prefix="cp_fx_")
    saved = os.environ.get("OFFICE_HOME")
    try:
        srv = load_server(home)
        texts = {name: render(build(srv)) for name, build in BUILDERS.items()}
    finally:
        shutil.rmtree(home, ignore_errors=True)
        if saved is None:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = saved
    drift = 0
    # tests/fixtures/world/cockpit*.json は全部この生成器の持ち物＝BUILDERS から 1 本抜けると
    # その fixture は比べられないまま「一致」になる。抜けた（手で足した）ものは名指しで赤にする
    for stray in sorted({p.stem for p in WORLD.glob("cockpit*.json")} - set(texts)):
        drift += 1
        print(f"✗ tests/fixtures/world/{stray}.json は生成器（BUILDERS）に無い → 生成器へ戻すか消す")
    for name, text in texts.items():
        path = WORLD / f"{name}.json"
        old = path.read_text(encoding="utf-8") if path.is_file() else None
        if check:
            if old != text:
                drift += 1
                why = "が無い" if old is None else "が生成器の出力と違う（手で直した／server の形が変わった）"
                print(f"✗ {path.relative_to(ROOT)} {why} → `{CMD}` で作り直す")
            continue
        if old != text:
            path.write_text(text, encoding="utf-8")
        n = len(json.loads(text)["employees"])
        print(f"{'更新' if old != text else '変化なし'}: {path.relative_to(ROOT)}（{n} セッション）")
    if check and not drift:
        names = "・".join(f"{n}.json" for n in texts)
        print(f"✓ cockpit の固定ワールド {len(texts)} 本（{names}）は生成器の出力と一致")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
