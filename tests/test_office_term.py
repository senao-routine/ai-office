# -*- coding: utf-8 -*-
"""R100-1a: ターミナル画面の行（server/office_term.py）の門。

- 行の種類と上限（上限の表は office_term の定数が正本）
- 道具の引数と結果は scrub を通す（秘密の伏字）・URL は scheme://host/path（query は載せない）
- 差分カーソル: off>size・行頭でない off・直前の行が違う（ファイルが差し替わった）→ reset
- 書きかけの行は読まない・差分が上限を超えたら gap
- R113-B（本人 2026-10-08「もっとそのまま」）: 考えの印 th・Bash の全文（失敗はサンドイッチ）・差分 100 行・相対パス・
  URL の path・検索語（定数で切替）・サブエージェントの末尾 kids・shrink の新しい段・ENVELOPE_BUDGET への収束
"""
import base64
import json
import os
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import office_term as ot  # noqa: E402
import dialog_seal  # noqa: E402
from office_actions import scrub_output  # noqa: E402

CMD_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")


def row(kind, **kw):
    return json.dumps({"type": kind, "uuid": kw.pop("uuid", "u"), "message": kw}, ensure_ascii=False) + "\n"


def asst(*blocks):
    return row("assistant", role="assistant", content=list(blocks))


def user(content):
    return row("user", role="user", content=content)


class RowsTest(unittest.TestCase):
    def rows(self, text):
        return ot.rows_from_lines(text.splitlines(), scrub_output, CMD_RE)

    def test_kinds(self):
        t = (user("npm test を回して") + asst({"type": "text", "text": "回します"},
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "npm test"}})
             + user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok 3 tests", "is_error": False}])
             + asst({"type": "tool_use", "id": "t2", "name": "Edit",
                     "input": {"file_path": "/Users/x/secret/app.js", "old_string": "a", "new_string": "b"}})
             + asst({"type": "tool_use", "id": "t3", "name": "Grep", "input": {"pattern": "password=hunter2"}})
             + asst({"type": "tool_use", "id": "t4", "name": "AskUserQuestion", "input": {"questions": [
                 {"question": "どちら?", "options": [{"label": "A"}, {"label": "B"}]}]}})
             + user("<command-name>/verify</command-name>")
             + user("<system-reminder>x</system-reminder>")
             + user([{"type": "tool_result", "tool_use_id": "t5", "content": [{"type": "text", "text": "boom"}],
                      "is_error": True}]))
        with mock.patch.object(ot, "SHOW_SEARCH_TERMS", False):          # 検索語を載せない分岐（R113-B の定数）
            got = self.rows(t)
        self.assertEqual([r["k"] for r in got], ["u", "a", "tool", "res", "tool", "tool", "q", "cmd", "res"])
        strip = lambda r: {k: v for k, v in r.items() if k not in ("id", "for", "nl")}   # R102-3: 道具と結果の印・総行数
        self.assertEqual(strip(got[2]), {"k": "tool", "n": "Bash", "t": "npm test"})
        self.assertEqual(got[3]["for"], got[2]["id"])
        self.assertEqual(strip(got[4]), {"k": "tool", "n": "Edit", "t": "app.js"})
        self.assertEqual(strip(got[5]), {"k": "tool", "n": "Grep", "t": ""}, "検索語を載せた")
        self.assertEqual((got[6]["t"], got[6]["o"]), ("どちら?", ["A", "B"]))   # R102-3: 選択肢は o に分ける
        self.assertEqual(got[7]["t"], "/verify")
        self.assertEqual(strip(got[8]), {"k": "res", "t": "boom", "err": 1})
        self.assertNotIn("secret", json.dumps(got, ensure_ascii=False))
        self.assertNotIn("hunter2", json.dumps(got, ensure_ascii=False))

    def test_secrets_in_commands_and_results_are_scrubbed(self):
        key = "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2
        t = (asst({"type": "tool_use", "id": "t1", "name": "Bash",
                   "input": {"command": f"curl -H 'Authorization: Bearer {key}' https://x"}})
             + user([{"type": "tool_result", "tool_use_id": "t1", "content": f"token={key}"}]))
        out = json.dumps(self.rows(t), ensure_ascii=False)
        self.assertNotIn(key, out, "秘密が端末の行に出た（伏字を通していない）")

    def test_limits(self):
        long = "\n".join(f"line {i} " + "x" * 50 for i in range(40))
        t = asst({"type": "text", "text": long}) + asst({"type": "tool_use", "id": "t", "name": "Bash",
                                                          "input": {"command": "echo " + "y" * 500}})
        got = self.rows(t)
        self.assertLessEqual(len(got[0]["t"]), ot.TEXT_MAX)
        self.assertLessEqual(got[0]["t"].count("\n"), ot.TEXT_LINES - 1)
        self.assertTrue(got[0]["t"].endswith("…"))
        self.assertEqual(got[1]["t"], "echo " + "y" * 500)                # R113-B: 500 字の command はそのまま（従来は 200 字）
        long_cmd = asst({"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "echo " + "y " * 5000}})
        self.assertLessEqual(len(self.rows(long_cmd)[0]["t"]), ot.BASH_CMD_MAX)

    def test_broken_and_foreign_lines_are_skipped(self):
        got = self.rows('not json\n{"type":"summary"}\n[]\n' + user("はい"))
        self.assertEqual(got, [{"k": "u", "t": "はい"}])

    def test_r121_description_and_background_marks(self):
        # R121: 作業ビューは説明 ds を見出しにする。伏字→切る（引数と同じ順）・引数と同じなら付けない・裏で実行は bg
        t = (asst({"type": "tool_use", "id": "d1", "name": "Bash",
                   "input": {"command": "ls", "description": "一覧を見る", "run_in_background": True}})
             + asst({"type": "tool_use", "id": "d2", "name": "Bash",
                     "input": {"command": "curl -H x", "description": "鍵 sk-ant-api03-" + "A" * 40 + " で叩く"}})
             + asst({"type": "tool_use", "id": "d3", "name": "Task",
                     "input": {"description": "PWA を調べる", "subagent_type": "Explore", "prompt": "P"}})
             + asst({"type": "tool_use", "id": "d4", "name": "Bash", "input": {"command": "pwd", "description": "  "}})
             + asst({"type": "tool_use", "id": "d5", "name": "Bash",
                     "input": {"command": "x", "description": "長" * 300, "run_in_background": "yes"}}))
        got = self.rows(t)
        self.assertEqual((got[0].get("ds"), got[0].get("bg")), ("一覧を見る", 1))
        self.assertNotIn("A" * 20, got[1]["ds"])                          # 秘密の型は伏字
        self.assertNotIn("ds", got[2])                                    # Task は t が説明＝重ねない
        self.assertNotIn("ds", got[3])                                    # 空白だけの説明は無い扱い
        self.assertLessEqual(len(got[4]["ds"]), ot.DESC_MAX)
        self.assertNotIn("bg", got[4])                                    # True だけ（文字列の "yes" は裏ではない）

    def test_r121_pasted_content_keeps_the_instruction(self):
        # 以前は `<` 始まり＝注入行として人の指示ごと消えていた（貼り付けの囲みだけ外す・他の注入行は今までどおり捨てる）
        got = self.rows(user('<pasted_content id="7">\n続きから\n</pasted_content id="7">')
                        + user([{"type": "text", "text": '<pasted_content id="8">UI を直して</pasted_content id="8">'}])
                        + user("<system-reminder>x</system-reminder>"))
        self.assertEqual(got, [{"k": "u", "t": "続きから"}, {"k": "u", "t": "UI を直して"}])


def at(line, ts="2026-09-30T08:12:34.567Z", **extra):
    """transcript の 1 行に時刻・付帯（toolUseResult 等）を足す。"""
    d = json.loads(line)
    d["timestamp"] = ts
    d.update(extra)
    return json.dumps(d, ensure_ascii=False) + "\n"


def tool_use(tid, name, inp, cwd=None):
    """assistant の道具の行（cwd つきも作れる）。"""
    d = {"type": "assistant", "uuid": "u", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}
    if cwd is not None:
        d["cwd"] = cwd
    return json.dumps(d, ensure_ascii=False) + "\n"


def result(tid, content, is_error=False, **extra):
    return at(user([{"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": is_error}]), **extra)


_ERR_LINES = 500


def _junk(n, width=70, seed=0):
    """伏字の型に当たらず zlib でも縮まない本文（乱数の base64）を n 字・width 字ごとに改行。"""
    s = base64.b64encode(random.Random(seed).randbytes(n)).decode()[:n]
    return "\n".join(s[i:i + width] for i in range(0, n, width))


def _worst_page():
    """上限いっぱいの最悪の 1 枚: ROWS_MAX 行＝Bash の道具（4000 字/40 行）と結果（8000 字/120 行・失敗はサンドイッチ）の対・
    差分 100 行×200 字・新しい KIDS_PAGE_MAX 件の Task に子 20 行。封じる側と同じ鍵（kind 等）も持つ。"""
    rows, half = [], ot.ROWS_MAX // 2
    for i in range(half):
        rows.append({"k": "tool", "n": "Bash", "id": "%08x" % i, "t": _junk(ot.BASH_CMD_MAX, 100, seed=i)})
        r = {"k": "res", "for": "%08x" % i, "nl": _ERR_LINES, "f": "a.py", "add": ot.DIFF_LINES, "rem": 0,
             "d": [["+", j, _junk(ot.DIFF_COL, 10 ** 6, seed=10 ** 5 + i * 1000 + j)] for j in range(ot.DIFF_LINES)]}
        if i % 2:
            r["err"] = 1
            r["t"] = ot.clip_mid(_junk(_ERR_LINES * 70, 70, seed=2 * 10 ** 6 + i), ot.BASH_OUT_MAX, ot.BASH_OUT_LINES)
        else:
            r["t"] = ot.clip(_junk(ot.BASH_OUT_MAX * 2, 70, seed=3 * 10 ** 6 + i), ot.BASH_OUT_MAX, ot.BASH_OUT_LINES)
        if i >= half - ot.KIDS_PAGE_MAX:
            r["kids"] = [{"k": "res", "nl": 30, "t": _junk(1500, 70, seed=4 * 10 ** 6 + i * 100 + j)}
                         for j in range(ot.TASK_KIDS_MAX)]
        rows.append(r)
    return {"rows": rows, "gap": False, "kind": "term", "v": 2, "session": "sess-x", "reqId": "a" * 32, "iat": 1}


def _server_loop(page):
    """office_server._term_sealed と同じ回し方（入るか見る→入らなければ shrink(step)）。入った時点の段の数（入らなければ None）。"""
    step = 0
    for _ in range(ot.SHRINK_STEPS + 1):
        if ot.fits(page):
            return step
        if not ot.shrink(page, step):
            return None
        step += 1
    return None


def _child_lines(n, secret=None):
    """子 transcript の行（全部 isSidechain・cwd つき）: 最初に指示、あとは Bash の道具と結果の対 n 組。"""
    out = [json.dumps({"type": "user", "isSidechain": True, "cwd": "/w/p",
                       "message": {"role": "user", "content": "CHILD-PROMPT"}}, ensure_ascii=False)]
    for i in range(n):
        cmd = "echo kid-%d" % i + (" " + secret if secret and i == n - 1 else "")
        out.append(json.dumps({"type": "assistant", "isSidechain": True, "cwd": "/w/p", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "k%d" % i, "name": "Bash", "input": {"command": cmd}}]}}, ensure_ascii=False))
        out.append(json.dumps({"type": "user", "isSidechain": True, "cwd": "/w/p", "toolUseResult": {"stdout": "out-%d" % i, "stderr": ""},
                               "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "k%d" % i,
                                                                         "content": "out-%d" % i}]}}, ensure_ascii=False))
    return "\n".join(out) + "\n"


def _task_pair(tid, agent_id, desc="PWA を調べる"):
    """親の Task の道具と結果（toolUseResult.agentId で子 transcript を指す）。"""
    return (at(asst({"type": "tool_use", "id": tid, "name": "Task",
                     "input": {"description": desc, "subagent_type": "Explore", "prompt": "PROMPT-BODY"}}))
            + result(tid, "done", toolUseResult={"status": "completed", "agentId": agent_id, "totalToolUseCount": 3,
                                                 "totalTokens": 1000, "totalDurationMs": 5000}))


def _kids_tree(root, agent_id, child, where=("subagents",)):
    """<root>/proj/sess.jsonl（親＝最後が Task の対）と <root>/proj/sess/<where…>/agent-<id>.jsonl（子）を作る。"""
    sub = root.joinpath("proj", "sess", *where)
    sub.mkdir(parents=True, exist_ok=True)
    (sub / ("agent-%s.jsonl" % agent_id)).write_text(child, encoding="utf-8")
    parent = root / "proj" / "sess.jsonl"
    parent.write_text(user("調べて") + _task_pair("ta", agent_id), encoding="utf-8")
    return parent


class V2Test(unittest.TestCase):
    """R102-3（本人「Claude Code のセッションみたいに中身が見えるように」）: 行の v2。"""

    def rows(self, text):
        return ot.rows_from_lines(text.splitlines(), scrub_output, CMD_RE)

    def test_tool_and_result_are_linked_and_timed(self):
        t = (at(asst({"type": "tool_use", "id": "toolu_A", "name": "Bash", "input": {"command": "npm test"}}))
             + at(user([{"type": "tool_result", "tool_use_id": "toolu_A", "content": "ok"}]),
                  toolUseResult={"stdout": "a\nb\nc\nd", "stderr": "", "interrupted": False}))
        tool, res = self.rows(t)
        self.assertEqual(len(tool["id"]), 8)
        self.assertEqual(res["for"], tool["id"])
        self.assertNotIn("toolu_A", json.dumps([tool, res]))              # ID そのものは載せない
        import datetime
        want = int(datetime.datetime(2026, 9, 30, 8, 12, 34, tzinfo=datetime.timezone.utc).timestamp())
        self.assertEqual((tool["ts"], res["ts"]), (want, want))
        self.assertEqual((res["nl"], res["t"]), (4, "a\nb\nc\nd"))

    def test_read_carries_no_file_body(self):
        body = "SECRET-BODY-MARK\n" * 30
        t = (asst({"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/Users/x/p/app.py"}})
             + at(user([{"type": "tool_result", "tool_use_id": "r1", "content": body}]),
                  toolUseResult={"type": "text", "file": {"filePath": "/Users/x/p/app.py", "content": body,
                                                          "numLines": 30, "startLine": 1, "totalLines": 30}}))
        got = self.rows(t)
        self.assertEqual(got[0]["t"], "app.py")
        self.assertEqual((got[1]["rl"], got[1]["t"]), (30, "30 行を読み込み"))
        self.assertNotIn("SECRET-BODY-MARK", json.dumps(got))

    def test_edit_diff_has_line_numbers_counts_and_is_scrubbed(self):
        key = "sk-" + "Z9y8X7w6V5u4T3s2R1q0" * 2
        patch = [{"oldStart": 21, "oldLines": 3, "newStart": 21, "newLines": 3,
                  "lines": [" KINDS = (1,)", "-ROWS_MAX = 80", "+ROWS_MAX = 150", f"+TOKEN = '{key}'", " x = 1"]}]
        t = (asst({"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "/Users/x/p/office_term.py",
                                                                                 "old_string": "a", "new_string": "b"}})
             + at(user([{"type": "tool_result", "tool_use_id": "e1", "content": "ok"}]),
                  toolUseResult={"filePath": "/Users/x/p/office_term.py", "oldString": "ROWS_MAX = 80",
                                 "newString": "ROWS_MAX = 150", "originalFile": "WHOLE-ORIGINAL", "structuredPatch": patch}))
        res = self.rows(t)[1]
        self.assertEqual((res["f"], res["add"], res["rem"]), ("office_term.py", 2, 1))
        self.assertEqual([x[:2] for x in res["d"]], [[" ", 21], ["-", 22], ["+", 22], ["+", 23], [" ", 24]])
        out = json.dumps(res, ensure_ascii=False)
        self.assertNotIn(key, out, "差分の本文に秘密が残った（伏字を通していない）")
        self.assertNotIn("WHOLE-ORIGINAL", out)
        self.assertIn("+2 −1", res["t"])

    def test_diff_is_capped(self):
        patch = [{"oldStart": 1, "newStart": 1, "lines": ["+line %d " % i + "x" * 500 for i in range(300)]}]
        t = (asst({"type": "tool_use", "id": "e2", "name": "Write", "input": {"file_path": "/p/a.txt", "content": "."}})
             + at(user([{"type": "tool_result", "tool_use_id": "e2", "content": "ok"}]),
                  toolUseResult={"type": "update", "filePath": "/p/a.txt", "structuredPatch": patch}))
        res = self.rows(t)[1]
        self.assertEqual((len(res["d"]), res["add"]), (ot.DIFF_LINES, 300))
        self.assertTrue(all(len(x[2]) <= ot.DIFF_COL for x in res["d"]))

    def test_many_hunks_stay_within_the_cap(self):
        # 別モデルレビュー（3cc5275）: hunk の区切り「…」だけが上限の後も積まれ、100 hunk で 110 行になった
        patch = [{"oldStart": i * 10 + 1, "newStart": i * 10 + 1, "lines": ["-a%d" % i, "+b%d" % i]} for i in range(100)]
        t = (asst({"type": "tool_use", "id": "e3", "name": "Edit", "input": {"file_path": "/p/a.py"}})
             + at(user([{"type": "tool_result", "tool_use_id": "e3", "content": "ok"}]),
                  toolUseResult={"filePath": "/p/a.py", "structuredPatch": patch}))
        res = self.rows(t)[1]
        self.assertLessEqual(len(res["d"]), ot.DIFF_LINES)
        self.assertEqual((res["add"], res["rem"]), (100, 100))            # 数は全部数える
        self.assertNotEqual(res["d"][-1][0], "…", "最後が区切りだけ")

    def test_failed_tool_keeps_the_reason(self):
        # 別モデルレビュー（3cc5275）: 失敗した Read・Grep・Task が「1 行を読み込み」「1 件」「完了」になり、理由が消えた
        for name, tur in (("Read", {"type": "text", "file": {"numLines": 1}}), ("Grep", {"numFiles": 1}),
                          ("Task", {"totalToolUseCount": 1}), ("Bash", {"stdout": "", "stderr": "boom"})):
            t = (asst({"type": "tool_use", "id": "f1", "name": name, "input": {}})
                 + at(user([{"type": "tool_result", "tool_use_id": "f1", "is_error": True,
                             "content": "Permission denied: the user rejected this"}]), toolUseResult=tur))
            res = self.rows(t)[1]
            self.assertEqual(res.get("err"), 1, name)
            self.assertIn("Permission denied", res["t"], name)
            self.assertNotIn("読み込み", res["t"], name)

    def test_result_without_its_tool_row_is_read_by_shape(self):
        # 別モデルレビュー（3cc5275）: 道具の行が前のページ（差分の取得をまたいだ）だと、Edit の結果が汎用の本文へ落ちて
        # 差分が消えた → 結果の形から道具を当てる
        patch = [{"oldStart": 5, "newStart": 5, "lines": ["-x = 1", "+x = 2"]}]
        t = at(user([{"type": "tool_result", "tool_use_id": "e9", "content": "The file has been updated."}]),
               toolUseResult={"filePath": "/p/a.py", "structuredPatch": patch})
        (res,) = self.rows(t)
        self.assertEqual((res["f"], res["add"], res["rem"]), ("a.py", 1, 1))
        self.assertEqual([x[:2] for x in res["d"]], [["-", 5], ["+", 5]])
        for tur, key, want in (({"stdout": "a\nb", "stderr": ""}, "nl", 2), ({"numFiles": 3}, "nf", 3),
                               ({"type": "text", "file": {"numLines": 7}}, "rl", 7)):
            t = at(user([{"type": "tool_result", "tool_use_id": "zz", "content": "x"}]), toolUseResult=tur)
            self.assertEqual(self.rows(t)[0].get(key), want, tur)

    def test_write_create_shows_count_and_head(self):
        body = "\n".join("l%d" % i for i in range(42))
        t = (asst({"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "/p/new.md", "content": body}})
             + at(user([{"type": "tool_result", "tool_use_id": "w1", "content": "ok"}]),
                  toolUseResult={"type": "create", "filePath": "/p/new.md", "content": body, "structuredPatch": []}))
        res = self.rows(t)[1]
        self.assertEqual((res["wl"], len(res["d"]), res["d"][0]), (42, ot.WRITE_HEAD, ["+", 1, "l0"]))
        self.assertEqual(ot.WRITE_HEAD, ot.DIFF_LINES * 6 // 16)            # 従来の 16 行に対する 6 行と同じ比率

    def test_other_tools(self):
        t = (asst({"type": "tool_use", "id": "g1", "name": "Grep", "input": {"pattern": "password=hunter2", "path": "/Users/x/p/src"}},
                  {"type": "tool_use", "id": "f1", "name": "WebFetch", "input": {"url": "https://docs.example.com/a?token=zz"}},
                  {"type": "tool_use", "id": "a1", "name": "Task", "input": {"description": "PWA を調べる", "subagent_type": "Explore",
                                                                              "prompt": "PROMPT-BODY"}},
                  {"type": "tool_use", "id": "d1", "name": "TodoWrite", "input": {"todos": [
                      {"content": "直す", "status": "completed"}, {"content": "試す", "status": "in_progress"},
                      {"content": "報告", "status": "pending"}]}})
             + at(user([{"type": "tool_result", "tool_use_id": "g1", "content": "a\nb\nc"}]), toolUseResult={"numFiles": 3})
             + at(user([{"type": "tool_result", "tool_use_id": "f1", "content": "x"}]), toolUseResult={"code": 200, "bytes": 4096})
             + at(user([{"type": "tool_result", "tool_use_id": "a1", "content": "done"}]),
                  toolUseResult={"totalToolUseCount": 12, "totalTokens": 34500, "totalDurationMs": 62000})
             + user([{"type": "tool_result", "tool_use_id": "d1", "content": "ok"}]))
        got = self.rows(t)
        k = [r["k"] for r in got]
        self.assertEqual(k, ["tool", "tool", "tool", "todo", "res", "res", "res"])
        self.assertEqual(got[0]["t"], 'pattern: "password=hunter2", path: "src"')   # R113-B（本人裁定）: 検索語は伏字を通して載せる
        self.assertEqual(got[1]["t"], "https://docs.example.com/a")       # R113-B: URL は scheme://host/path（query は落とす）
        self.assertEqual((got[2]["t"], got[2]["sub"]), ("PWA を調べる", "Explore"))
        self.assertEqual(got[3]["i"], [["c", "直す"], ["i", "試す"], ["p", "報告"]])
        self.assertEqual(got[4]["nf"], 3)
        self.assertEqual(got[5]["code"], 200)
        self.assertEqual((got[6]["tu"], got[6]["tk"], got[6]["ms"]), (12, 34500, 62000))
        out = json.dumps(got, ensure_ascii=False)
        for leak in ("token=zz", "PROMPT-BODY"):
            self.assertNotIn(leak, out)

    def test_interrupt_compact_turn_and_thinking(self):
        t = (user("[Request interrupted by user]")
             + json.dumps({"type": "system", "subtype": "turn_duration", "durationMs": 192000}) + "\n"
             + json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n"
             + json.dumps({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "SUMMARY-BODY"}}) + "\n"
             + asst({"type": "thinking", "thinking": "THINK-BODY"}, {"type": "text", "text": "はい"})
             + json.dumps({"type": "user", "isSidechain": True, "message": {"role": "user", "content": "SIDE"}}) + "\n")
        got = self.rows(t)
        self.assertEqual([r["k"] for r in got], ["int", "turn", "sys", "sys", "th", "a"])   # R113-B: 考えは印だけ
        self.assertEqual(got[1]["ms"], 192000)
        self.assertEqual({k: got[4][k] for k in ("n", "chars")}, {"n": 1, "chars": len("THINK-BODY")})
        out = json.dumps(got, ensure_ascii=False)
        for leak in ("SUMMARY-BODY", "THINK-BODY", "SIDE"):
            self.assertNotIn(leak, out)

    def test_plan_text_is_a_scrubbed_capped_row(self):
        """R104-2: ExitPlanMode の計画の本文を plan 行に（伏字・3000 字/60 行まで）。道具の行にはしない。"""
        body = "## 計画\n" + "\n".join("%d. 手順 %d" % (i, i) for i in range(1, 90)) + "\nexport API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz0123"
        got = self.rows(asst({"type": "tool_use", "id": "p1", "name": "ExitPlanMode", "input": {"plan": body}}))
        self.assertEqual([r["k"] for r in got], ["plan"])
        self.assertTrue(got[0]["t"].startswith("## 計画\n1. 手順 1"))
        self.assertLessEqual(got[0]["t"].count("\n"), ot.PLAN_LINES)
        self.assertLessEqual(len(got[0]["t"]), ot.PLAN_MAX + 2)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", got[0]["t"])
        self.assertEqual(self.rows(asst({"type": "tool_use", "id": "p2", "name": "ExitPlanMode", "input": {"plan": "  "}})), [])
        secret = asst({"type": "tool_use", "id": "p3", "name": "ExitPlanMode",
                       "input": {"plan": "鍵は sk-ant-" + "q" * 40 + " を使う"}})
        self.assertNotIn("q" * 40, json.dumps(self.rows(secret), ensure_ascii=False))
        page = {"rows": [{"k": "plan", "t": "\n".join("行 %d" % i for i in range(60))}], "gap": False}
        self.assertTrue(ot.shrink(page, ot.SHRINK_TEXT))
        self.assertLessEqual(page["rows"][0]["t"].count("\n"), 12)                 # 封書に入らないときは先頭だけ

    def test_question_options_and_keys_are_closed(self):
        t = asst({"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {"questions": [
            {"question": "どちら?", "options": [{"label": "A案"}, {"label": "B案"}]}]}})
        got = self.rows(t)
        self.assertEqual((got[0]["t"], got[0]["o"]), ("どちら?", ["A案", "B案"]))
        allowed = {"k", "t", "ts", "n", "id", "sub", "for", "err", "nl", "int", "bg", "rl", "img", "f", "add", "rem", "d",
                   "wl", "nf", "code", "st", "tu", "tk", "ms", "i", "o",
                   "chars", "kids", "kx"}                                       # R113-B: 考えの字数・子の行・省いた子の行の数
        for r in got:
            self.assertTrue(set(r) <= allowed, r)
            self.assertIn(r["k"], ot.KINDS)
        # 種類を全部含む会話（子の行つき）でも鍵は閉じている（内部の _l・_aid が漏れない）
        with tempfile.TemporaryDirectory() as d:
            parent = _kids_tree(Path(d), "abc123def4567890a", _child_lines(25))
            with parent.open("a", encoding="utf-8") as f:
                f.write(at(asst({"type": "thinking", "thinking": "考え"}, {"type": "text", "text": "答え"})))
            page = ot.public(ot.read_page(parent, None, scrub_output, CMD_RE))
        rows = page["rows"] + [k for r in page["rows"] for k in r.get("kids") or []]
        self.assertTrue(any(r.get("kids") for r in page["rows"]))
        self.assertTrue(any(r["k"] == "th" for r in rows))
        for r in rows:
            self.assertTrue(set(r) <= allowed, r)
            self.assertIn(r["k"], ot.KINDS)

    def test_shrink_order_and_worst_page_fits(self):
        """R113-B（B9）: 段の順（kids → 差分を半分 → 従来相当 → 古い差分の本文 → 結果を 2 行 → 半分ずつ）と、
        最悪の 1 枚（伏字の型に当たらない乱数の本文・上限いっぱい）が ENVELOPE_BUDGET に収まること。"""
        page = _worst_page()
        self.assertFalse(ot.fits(page))
        self.assertTrue(ot.shrink(page, ot.SHRINK_KIDS))                       # 0': 入らないので子の行は全部外す（印 kx）
        rows = page["rows"]
        self.assertFalse(any("kids" in r for r in rows))
        self.assertEqual(sum(r.get("kx", 0) for r in rows), ot.KIDS_PAGE_MAX * ot.TASK_KIDS_MAX)
        self.assertTrue(ot.shrink(page, ot.SHRINK_DIFF_HALF))                  # 差分は新しい 4 件も半分
        ds = [r["d"] for r in rows if "d" in r]
        self.assertEqual(len(ds), ot.ROWS_MAX // 2)
        self.assertTrue(all(len(d) <= ot.DIFF_LINES // 2 and all(len(x[2]) <= ot.DIFF_COL // 2 for x in d) for d in ds))
        self.assertTrue(ot.shrink(page, ot.SHRINK_OLD))                        # 0'': Bash の入出力・差分を従来相当へ
        for r in rows:
            if r["k"] == "tool":
                self.assertLessEqual(len(r["t"]), ot.ARG_MAX)
                self.assertLess(r["t"].count("\n"), ot.ARG_LINES)
            if r["k"] == "res":
                self.assertLessEqual(len(r["t"]), ot.RESULT_MAX)
                self.assertLess(r["t"].count("\n"), ot.RESULT_LINES)
                self.assertLessEqual(len(r["d"]), ot.DIFF_LINES_OLD)
                self.assertTrue(all(len(x[2]) <= ot.DIFF_COL_OLD for x in r["d"]))
                if r.get("err"):                                               # 失敗は組み直しても中略の数は元の総行数から
                    marks = [m for m in (ot._MARK_RE.match(x) for x in r["t"].split("\n")) if m]
                    self.assertEqual([int(m.group(1)) for m in marks], [_ERR_LINES - r["t"].count("\n")])
        self.assertTrue(ot.shrink(page, ot.SHRINK_DIFF_DROP))                  # 新しい 4 件以外の差分の本文を外す
        self.assertEqual(sum(1 for r in rows if "d" in r), 4)
        self.assertTrue(all("d" in r for r in [r for r in rows if r["k"] == "res"][-4:]))
        self.assertTrue(ot.shrink(page, ot.SHRINK_TEXT))
        self.assertTrue(all(r["t"].count("\n") <= 1 for r in rows if r["k"] == "res"))
        n = len(page["rows"])
        self.assertTrue(ot.shrink(page, ot.SHRINK_HALVE))
        self.assertEqual((len(page["rows"]), page["gap"]), (n - n // 2, True))
        step = ot.SHRINK_HALVE + 1
        while not ot.fits(page):
            self.assertLess(step, ot.SHRINK_STEPS, "封じる側のループの回数の中で収まらない")
            self.assertTrue(ot.shrink(page, step))
            step += 1
        self.assertLessEqual(ot.sealed_size(page), ot.ENVELOPE_BUDGET)

    def test_worst_page_converges_within_the_server_loop(self):
        """封じる側（office_server._term_sealed）と同じ回し方で、最悪の 1 枚も縮み切る（段を足しても回数が追従）。"""
        self.assertIsNotNone(_server_loop(_worst_page()))
        # 1 行しか入らない封書でも縮み切る（150 行→1 行＝半分ずつ 8 回。直書きの 10 回のままだと届かなかった）
        page = {"rows": [{"k": "a", "t": _junk(ot.TEXT_MAX, 80, seed=i)} for i in range(ot.ROWS_MAX)], "gap": False}
        one = ot.sealed_size({"rows": page["rows"][-1:], "gap": True})
        with mock.patch.object(ot, "ENVELOPE_BUDGET", one + 8):
            steps = _server_loop(page)
        self.assertIsNotNone(steps)
        self.assertEqual(len(page["rows"]), 1)
        self.assertGreater(steps, 10)

    def test_envelope_budget_is_four_frames_and_matches_the_seal(self):
        self.assertEqual(ot.ENVELOPE_BUDGET, dialog_seal.MAX_FRAMES * dialog_seal.BLOCK - 4)
        self.assertEqual(ot.ENVELOPE_BUDGET, 32764)
        if not dialog_seal.AVAILABLE:
            self.skipTest("封緘バックエンドが無い")
        seal = lambda pg: dialog_seal.seal("ab" * 32, "dev1", "c" * 32, "sess-x", ot.public(pg), iat=1)   # noqa: E731
        page = _worst_page()
        self.assertFalse(ot.fits(page))
        with self.assertRaises(ValueError):                                   # 大きさの見積もりは封緘と同じ（入らない＝toolarge）
            seal(page)
        self.assertIsNotNone(_server_loop(page))
        self.assertLessEqual(seal(page)["n"], dialog_seal.MAX_FRAMES)
        # 境目: 入らない最後の 1 枚と入る最初の 1 枚で、見積もりと封緘の判定が一致する
        rows = [{"k": "a", "t": _junk(ot.TEXT_MAX, 80, seed=7000 + i)} for i in range(40)]
        k = next(n for n in range(1, 41) if not ot.fits({"rows": rows[:n], "gap": False}))
        with self.assertRaises(ValueError):
            seal({"rows": rows[:k], "gap": False})
        self.assertLessEqual(seal({"rows": rows[:k - 1], "gap": False})["n"], dialog_seal.MAX_FRAMES)

    def test_kids_are_dropped_oldest_first_and_stop_when_it_fits(self):
        """0': 子の行は古い Task から外し、入った時点で止める（新しい Task の子は残る）。印 kx＝省いた行の数。"""
        rows = []
        for i in range(3):
            rows.append({"k": "res", "for": "%08x" % i, "t": "完了",
                         "kids": [{"k": "a", "t": _junk(1500, 10 ** 6, seed=i * 100 + j)} for j in range(ot.TASK_KIDS_MAX)]})
        page = {"rows": rows, "gap": False}
        self.assertFalse(ot.fits(page))
        self.assertTrue(ot.shrink(page, ot.SHRINK_KIDS))
        self.assertTrue(ot.fits(page))
        self.assertEqual([("kids" in r, r.get("kx")) for r in rows], [(False, 20), (False, 20), (True, None)])


class CursorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "s.jsonl"
        self.p.write_text(user("一つ目") + asst({"type": "text", "text": "二つ目"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def page(self, after=None):
        return ot.read_page(self.p, after, scrub_output, CMD_RE)

    def test_reset_then_delta_then_nothing(self):
        a = self.page()
        self.assertTrue(a["reset"])
        self.assertEqual([r["t"] for r in a["rows"]], ["一つ目", "二つ目"])
        self.assertEqual(a["cur"]["off"], self.p.stat().st_size)
        with self.p.open("a", encoding="utf-8") as f:
            f.write(user("三つ目"))
        b = self.page(a["cur"])
        self.assertFalse(b["reset"])
        self.assertEqual([r["t"] for r in b["rows"]], ["三つ目"])
        c = self.page(b["cur"])
        self.assertEqual((c["rows"], c["reset"], c["cur"]), ([], False, b["cur"]))

    def test_half_written_line_waits(self):
        a = self.page()
        with self.p.open("a", encoding="utf-8") as f:
            f.write(user("書きかけ")[:-8])
        b = self.page(a["cur"])
        self.assertEqual((b["rows"], b["reset"], b["cur"]), ([], False, a["cur"]))

    def test_bad_cursor_resets(self):
        a = self.page()
        size = self.p.stat().st_size
        for bad, why in (({"off": size + 10, "h": a["cur"]["h"]}, "off がサイズより先"),
                         ({"off": size - 3, "h": a["cur"]["h"]}, "行頭でない off"),
                         ({"off": size, "h": "0" * 16}, "直前の行が違う"),
                         ({"off": "1", "h": a["cur"]["h"]}, "型違い"),
                         ({"off": True, "h": a["cur"]["h"]}, "bool"),
                         ({"off": size, "h": "ZZ" * 8}, "16 進でない"),
                         (None, "無し")):
            with self.subTest(why):
                r = self.page(bad)
                self.assertTrue(r["reset"], why)
                self.assertEqual(len(r["rows"]), 2)

    def test_rewritten_file_resets(self):
        """同じ長さで中身が違うファイルに化けても、直前の行の hash で見抜く（巻き戻し・取り違えにならない）。"""
        a = self.page()
        self.p.write_text(user("一つ目") + asst({"type": "text", "text": "別物だ"}), encoding="utf-8")
        self.assertEqual(self.p.stat().st_size, a["cur"]["off"])
        r = self.page(a["cur"])
        self.assertTrue(r["reset"])
        self.assertEqual(r["rows"][-1]["t"], "別物だ")

    def test_big_delta_is_capped_with_gap(self):
        a = self.page()
        with self.p.open("a", encoding="utf-8") as f:
            for i in range(ot.ROWS_MAX + 20):
                f.write(user(f"m{i}"))
        b = self.page(a["cur"])
        self.assertFalse(b["reset"])
        self.assertTrue(b["gap"])
        self.assertEqual(len(b["rows"]), ot.ROWS_MAX)
        self.assertEqual(b["rows"][-1]["t"], f"m{ot.ROWS_MAX + 19}")
        self.assertEqual(b["cur"]["off"], self.p.stat().st_size)


class BackTest(unittest.TestCase):
    """R104-2（洗い出し backlog-term-depth）: 端末を開く前の行を遡る。reset の最初の行の位置（beg）から BACK_ROWS 行ずつ前へ。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "s.jsonl"
        lines = []
        for i in range(400):
            lines.append(user("指示 %d" % i) if i % 2 == 0 else asst({"type": "text", "text": "返事 %d" % i}))
        self.p.write_text("".join(lines), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_walk_back_to_the_top_reproduces_every_row(self):
        whole = ot.rows_from_lines(self.p.read_text(encoding="utf-8").splitlines(), scrub_output, CMD_RE)
        page = ot.public(ot.read_page(self.p, None, scrub_output, CMD_RE))
        self.assertTrue(page["reset"])
        self.assertEqual(len(page["rows"]), ot.ROWS_MAX)
        self.assertIsNotNone(page["beg"])
        got = list(page["rows"])
        beg, rounds = page["beg"], 0
        while beg is not None:
            older = ot.read_before(self.p, beg, scrub_output, CMD_RE)
            self.assertIsNotNone(older)
            older = ot.public(older)
            self.assertTrue(older["older"])
            self.assertLessEqual(len(older["rows"]), ot.BACK_ROWS)
            got = older["rows"] + got
            beg = older["beg"]
            rounds += 1
            self.assertLess(rounds, 20)
        self.assertEqual([r["t"] for r in got], [r["t"] for r in whole])            # 抜けも重なりも無い
        for r in got:
            self.assertFalse({"_l", "_offs"} & set(r))

    def test_small_file_has_no_beg_and_bad_cursor_stops(self):
        small = Path(self.tmp.name) / "small.jsonl"
        small.write_text(user("一つ目") + asst({"type": "text", "text": "二つ目"}), encoding="utf-8")
        self.assertIsNone(ot.public(ot.read_page(small, None, scrub_output, CMD_RE))["beg"])  # もう前が無い
        page = ot.public(ot.read_page(self.p, None, scrub_output, CMD_RE))
        self.assertIsNone(ot.read_before(self.p, dict(page["beg"], h="0" * 16), scrub_output, CMD_RE))
        self.assertIsNone(ot.read_before(self.p, {"off": "x"}, scrub_output, CMD_RE))
        self.assertIsNone(ot.read_before(self.p, dict(page["beg"], off=10 ** 9), scrub_output, CMD_RE))

    def test_shrink_moves_beg_forward(self):
        page = ot.read_page(self.p, None, scrub_output, CMD_RE)
        first_before = ot.public(page)["beg"]
        self.assertTrue(ot.shrink(page, ot.SHRINK_HALVE))                              # 古い方の半分を落とす
        after = ot.public(page)
        self.assertGreater(after["beg"]["off"], first_before["off"])                   # 落とした行も遡りで取り直せる
        self.assertTrue(after["gap"])
        older = ot.public(ot.read_before(self.p, after["beg"], scrub_output, CMD_RE))
        whole = [r["t"] for r in ot.rows_from_lines(self.p.read_text(encoding="utf-8").splitlines(), scrub_output, CMD_RE)]
        i = whole.index(page["rows"][0]["t"])
        self.assertEqual([r["t"] for r in older["rows"]], whole[max(0, i - ot.BACK_ROWS):i])   # 落とした行の直前まで


class BackEdgeTest(unittest.TestCase):
    """R104-3（別モデルレビュー medium 4 件）: 行の途中で切れる・長い行・大きな行の手前・計画の冒頭。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def walk(self, p):
        page = ot.public(ot.read_page(p, None, scrub_output, CMD_RE))
        got, beg, n = list(page["rows"]), page["beg"], 0
        while beg is not None:
            older = ot.read_before(p, beg, scrub_output, CMD_RE)
            self.assertIsNotNone(older, "カーソルが確かめられない")
            older = ot.public(older)
            got = older["rows"] + got
            beg, n = older["beg"], n + 1
            self.assertLess(n, 200)
        return got, older if n else page

    def test_a_line_with_many_rows_is_split_and_nothing_is_lost(self):
        p = self.d / "multi.jsonl"
        p.write_text("".join(asst(*[{"type": "text", "text": "返事 %d-%d" % (i, j)} for j in range(7)]) for i in range(120)),
                     encoding="utf-8")
        whole = [r["t"] for r in ot.rows_from_lines(p.read_text(encoding="utf-8").splitlines(), scrub_output, CMD_RE)]
        got, last = self.walk(p)
        self.assertEqual([r["t"] for r in got], whole)                            # 行の途中で切れても抜けない
        self.assertTrue(last["top"])

    def test_long_lines_still_verify(self):
        p = self.d / "long.jsonl"
        big = "x" * 300_000
        p.write_text(user("最初") + asst({"type": "text", "text": big}) + "".join(user("指示 %d" % i) for i in range(160)),
                     encoding="utf-8")
        got, last = self.walk(p)
        self.assertEqual(got[0]["t"], "最初")                                       # 256KiB を超える行のカーソルも確かめられる
        self.assertTrue(last["top"])

    def test_a_huge_record_before_the_cursor_widens_the_window(self):
        p = self.d / "huge.jsonl"
        huge = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "image", "source": {"data": "A" * 900_000}}]}})
        p.write_text(user("ずっと前の指示") + huge + "\n" + "".join(user("指示 %d" % i) for i in range(150)), encoding="utf-8")
        got, last = self.walk(p)
        self.assertEqual(got[0]["t"], "ずっと前の指示")                               # 大きな行の手前でも「はじまり」と言わずに遡る
        self.assertTrue(last["top"])

    def test_plan_keeps_its_head(self):
        plan = "IMPORTANT-BEGIN\n" + "\n".join("計画の手順" * 30 for _ in range(40))
        got = ot.rows_from_lines(asst({"type": "tool_use", "id": "p", "name": "ExitPlanMode", "input": {"plan": plan}}).splitlines(),
                                 scrub_output, CMD_RE)
        self.assertTrue(got[0]["t"].startswith("IMPORTANT-BEGIN"), got[0]["t"][:40])  # 伏字は末尾を残す＝先に先頭から切る


def _epoch(iso):
    import datetime
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class R113Test(unittest.TestCase):
    """R113-B（本人 2026-10-08「Claude Code のやり取りをもっとそのまま」）の門＝新しい振る舞い 1 件につき回帰 1 件。"""

    def rows(self, text):
        return ot.rows_from_lines(text.splitlines(), scrub_output, CMD_RE)

    # ── B1 素材の先切りをしない（R113-B-S2: 伏字は素材全体へ掛けてから切る） ────────────────
    def test_scrub_runs_on_the_whole_material_before_any_cut(self):
        src = (ROOT / "server" / "office_term.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\w)\]]\[:(2000|400|600|4000|12000)\]", src), "素材の先切りが直書きに戻った")
        self.assertIsNone(re.search(r"_scrub\(scrub,\s*_(head|tail)\(", src), "切ってから伏字を掛ける形に戻った")
        self.assertFalse(hasattr(ot, "_raw"), "素材の先切り（_raw）が戻った")
        for n in (80, ot.RESULT_MAX, ot.DIFF_COL, ot.BASH_CMD_MAX, ot.BASH_OUT_MAX, ot.PLAN_MAX):   # 上限いっぱいまで見える
            body = " ".join("w%05d" % i for i in range(n))
            got = ot._fit(scrub_output, body, n, 10 ** 6)
            self.assertTrue(n - 8 <= len(got) <= n, (n, len(got)))                         # 語の途中で切らない分だけ短い

    def test_scrub_keeps_the_head_of_multibyte_output(self):
        # scrub_output の既定の上限（8000 バイト）は末尾を残す＝日本語 3000 字（9000 バイト）の出力の先頭が消えていた
        out = "先頭の行\n" + "あ" * 3000
        res = self.rows(tool_use("b", "Bash", {"command": "cat a"}) + result("b", out, toolUseResult={"stdout": out, "stderr": ""}))[1]
        self.assertTrue(res["t"].startswith("先頭の行\nあ"), res["t"][:20])

    # ── B2 考えの印 ───────────────────────────────────────────────────────────
    def test_thinking_is_a_mark_with_count_and_chars_only(self):
        t = (at(asst({"type": "thinking", "thinking": "THINK-ONE"}), ts="2026-10-08T01:00:00Z")
             + at(asst({"type": "thinking", "thinking": "THINK-TWO-LONGER"}), ts="2026-10-08T01:00:05Z")
             + json.dumps({"type": "file-history-snapshot"}) + "\n"
             + asst({"type": "redacted_thinking", "data": "ENCRYPTED-BLOB"}, {"type": "text", "text": "結論"})
             + asst({"type": "thinking", "thinking": "TRAILING"}))
        got = self.rows(t)
        self.assertEqual([r["k"] for r in got], ["th", "a", "th"])
        self.assertEqual(got[0], {"k": "th", "n": 3, "chars": len("THINK-ONE") + len("THINK-TWO-LONGER"),
                                  "ts": _epoch("2026-10-08T01:00:00Z")})    # 考え始めの時刻・本文の鍵は無い
        self.assertEqual(got[2], {"k": "th", "n": 1, "chars": len("TRAILING")})   # ループの終わりで出す
        out = json.dumps(got, ensure_ascii=False)
        for leak in ("THINK-ONE", "THINK-TWO", "ENCRYPTED-BLOB", "TRAILING"):
            self.assertNotIn(leak, out)

    def test_thinking_marks_survive_walking_back(self):
        """遡りの頁の切れ目で考えの印が消えたり二重になったりしない（見える行は全部・考えの数の合計も一致）。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            p.write_text("".join(user("指示 %d" % i) + asst({"type": "thinking", "thinking": "a" * 7})
                                 + asst({"type": "thinking", "thinking": "b" * 5}) + asst({"type": "text", "text": "返事 %d" % i})
                                 for i in range(200)), encoding="utf-8")
            whole = ot.rows_from_lines(p.read_text(encoding="utf-8").splitlines(), scrub_output, CMD_RE)
            page = ot.public(ot.read_page(p, None, scrub_output, CMD_RE))
            got, beg, n = list(page["rows"]), page["beg"], 0
            while beg is not None:
                older = ot.public(ot.read_before(p, beg, scrub_output, CMD_RE))
                got, beg, n = older["rows"] + got, older["beg"], n + 1
                self.assertLess(n, 50)
        vis = lambda rs: [r["t"] for r in rs if r["k"] != "th"]   # noqa: E731
        ths = lambda rs: (sum(r["n"] for r in rs if r["k"] == "th"), sum(r["chars"] for r in rs if r["k"] == "th"))  # noqa: E731
        self.assertEqual(vis(got), vis(whole))
        self.assertEqual(ths(got), ths(whole))
        self.assertEqual(ths(whole), (400, 200 * 12))

    # ── B3 Bash の入出力 ───────────────────────────────────────────────────────
    def test_bash_command_and_output_are_shown_in_full_up_to_the_new_caps(self):
        cmd = "\n".join("step %d && make target-%d" % (i, i) for i in range(30))          # 30 行（従来は 200 字/4 行で切れた）
        out = "\n".join("line %03d " % i + "x" * 60 for i in range(100))                 # 6900 字・100 行（従来の先切り 4000 字を超える）
        tool, res = self.rows(tool_use("b", "Bash", {"command": cmd}) + result("b", out, toolUseResult={"stdout": out, "stderr": ""}))
        self.assertEqual(tool["t"], cmd)
        self.assertEqual((res["t"], res["nl"]), (out, 100))
        big = "\n".join("row %d " % i + "z" * 40 for i in range(400))
        res = self.rows(tool_use("c", "Bash", {"command": "x"}) + result("c", big, toolUseResult={"stdout": big, "stderr": ""}))[1]
        self.assertTrue(res["t"].startswith("row 0 "))                                    # 成功は先頭優先のまま
        self.assertEqual(res["t"].count("\n"), ot.BASH_OUT_LINES - 1)
        self.assertLessEqual(len(res["t"]), ot.BASH_OUT_MAX + 1)
        self.assertNotIn("中略", res["t"])

    def test_failed_bash_output_is_a_sandwich_with_the_reason_at_the_end(self):
        text = "Exit code 2\n" + "\n".join("build %03d ok" % i for i in range(300)) + "\nError: TARGET-FAILED at the end"
        res = self.rows(tool_use("f", "Bash", {"command": "make"}) + result("f", text, is_error=True))[1]
        self.assertEqual((res["err"], res["nl"]), (1, 302))
        tl = res["t"].split("\n")
        self.assertEqual((tl[0], tl[-1]), ("Exit code 2", "Error: TARGET-FAILED at the end"))   # 先頭も末尾（理由）も見える
        marks = [(i, m) for i, m in enumerate(map(ot._MARK_RE.match, tl)) if m]
        self.assertEqual(len(marks), 1)
        k, m = marks[0]
        self.assertEqual(int(m.group(1)), 302 - (len(tl) - 1))                             # 中略の数＝総行数−見えている行
        self.assertLess(k, len(tl) - 1 - k)                                                # 先頭 40%・末尾 60%
        self.assertLessEqual(len(tl), ot.BASH_OUT_LINES)
        self.assertLessEqual(len(res["t"]), ot.BASH_OUT_MAX)
        short = self.rows(tool_use("g", "Bash", {"command": "make"}) + result("g", "Exit code 1\nboom", is_error=True))[1]
        self.assertEqual(short["t"], "Exit code 1\nboom")                                  # 収まるならそのまま

    def test_huge_failed_output_takes_both_ends_and_counts_the_middle(self):
        body = "\n".join("L%06d " % i + "q" * 50 for i in range(20000))                    # 約 1.2MB＝素材の両端だけ切り出す
        res = self.rows(tool_use("h", "Bash", {"command": "x"}) + result("h", body, is_error=True))[1]
        tl = res["t"].split("\n")
        self.assertTrue(tl[0].startswith("L000000") and tl[-1].startswith("L019999"), (tl[0], tl[-1]))
        m = [x for x in map(ot._MARK_RE.match, tl) if x]
        self.assertEqual(int(m[0].group(1)), 20000 - (len(tl) - 1))
        long_line = "E" * 30000 + " REASON-AT-END"                                          # 1 行だけの長い失敗も末尾が見える
        res = self.rows(tool_use("i", "Bash", {"command": "x"}) + result("i", long_line, is_error=True))[1]
        self.assertTrue(res["t"].endswith("REASON-AT-END"))
        self.assertLessEqual(len(res["t"]), ot.BASH_OUT_MAX)

    # ── B10 秘密は末尾にあっても伏字 ───────────────────────────────────────────
    def test_secrets_at_the_end_are_scrubbed(self):
        secrets = {"bearer": ("Bearer " + "Tk9xYz" * 6, "Tk9xYz" * 6), "sk": ("sk-" + "a1B2c3D4e5" * 3, "a1B2c3D4e5" * 3),
                   "akia": ("AKIA" + "ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"), "hex": ("0123456789abcdef" * 4, "0123456789abcdef" * 4)}
        for name, (sec, core) in secrets.items():
            with self.subTest(name):
                cmd = "echo " + "w " * 1500 + "&& curl -H 'Authorization: " + sec + "'"         # command の末尾
                fail = "\n".join("log %d" % i for i in range(400)) + "\nfailed with " + sec       # 失敗の出力の末尾（中略の後）
                ok = "\n".join("out %d" % i for i in range(60)) + "\ntoken " + sec               # 成功の出力の末尾（上限の中）
                diff = [{"oldStart": 1, "newStart": 1, "lines": ["+" + "v" * 150 + " " + sec]}]   # 差分の行の末尾
                t = (tool_use("c1", "Bash", {"command": cmd}) + result("c1", fail, is_error=True)
                     + tool_use("c2", "Bash", {"command": "x"}) + result("c2", ok, toolUseResult={"stdout": ok, "stderr": ""})
                     + tool_use("c3", "Edit", {"file_path": "/p/a.py"})
                     + result("c3", "ok", toolUseResult={"filePath": "/p/a.py", "structuredPatch": diff}))
                got = self.rows(t)
                out = json.dumps(got, ensure_ascii=False)
                self.assertNotIn(core, out, "末尾の秘密が伏字にならなかった")
                self.assertEqual(out.count("[secret]"), 4, out[-400:])
                self.assertIn("中略", got[1]["t"])                                                 # 失敗はサンドイッチの末尾で見えている

    def test_a_secret_cut_by_the_raw_window_leaves_no_fragment(self):
        """旧来の素材の先切り（最終上限の 2 倍の窓）の切れ目に鍵がかかっても断片が残らない（伏字でパスが縮むと切れ目が窓に入る）。
        R113-B-S2 で先切り自体をやめた＝窓の大きさは旧来の値を直に書いて同じ形を作る。"""
        hexsec = "fedcba9876543210" * 4
        r = 2 * ot.BASH_CMD_MAX                                                             # 旧 _raw(BASH_CMD_MAX)
        filler = "".join("/very/long/dir/%s/f%d " % ("d" * 150, i) for i in range(45))     # 伏字で basename に縮む
        cmd = filler + "y" * (r - len(filler) - 30) + " " + hexsec + " tail"
        pos = cmd.index(hexsec)
        self.assertTrue(pos < r < pos + 64)                                                # 切れ目が鍵の途中
        got = self.rows(tool_use("b", "Bash", {"command": cmd}))[0]["t"]
        self.assertIn("y" * 50, got)                                                       # 切れ目の手前まで窓に入っている
        self.assertNotIn(hexsec[:12], got)
        # 失敗の出力の末尾側（両端だけ切り出す側）の切れ目でも同じ
        r = 2 * ot.BASH_OUT_MAX                                                             # 旧 _raw(BASH_OUT_MAX)
        paths = " " + " ".join("/very/long/dir/%s/g%d" % ("d" * 150, i) for i in range(90))
        paths += " " + "k" * (r - 30 - len(paths) - 1)
        text = "Exit code 1\n" + "m\n" * 20000 + "zz " + hexsec + paths                   # 鍵は独立した語（伏字の型）
        start = len(text) - r
        self.assertTrue(text.index(hexsec) < start < text.index(hexsec) + 64)
        res = self.rows(tool_use("f", "Bash", {"command": "x"}) + result("f", text, is_error=True))[1]
        self.assertIn("g89", res["t"])                                                     # 末尾側の素材が窓に入っている
        self.assertNotIn(hexsec[-24:], res["t"])

    # ── B4 差分 ───────────────────────────────────────────────────────────────
    def test_diff_shows_up_to_100_lines_of_200_columns(self):
        patch = [{"oldStart": 1, "newStart": 1, "lines": ["+L%02d " % i + "c" * 180 for i in range(90)]}]
        res = self.rows(tool_use("e", "Edit", {"file_path": "/p/a.py"})
                        + result("e", "ok", toolUseResult={"filePath": "/p/a.py", "structuredPatch": patch}))[1]
        self.assertEqual(len(res["d"]), 90)                                                # 従来は 16 行で切れた
        self.assertEqual(res["d"][-1][2], "L89 " + "c" * 180)                               # 従来は 120 字で切れた

    # ── B5 相対パス ───────────────────────────────────────────────────────────
    def test_paths_are_relative_to_the_project_and_outside_falls_back_to_basename(self):
        cwd = "/Users/someone/p"                                                           # 架空のホーム（実名をテストに書かない）
        t = (tool_use("r1", "Read", {"file_path": "/Users/someone/p/server/app.py"}, cwd=cwd)
             + tool_use("r2", "Edit", {"file_path": "/Users/someone/p/ui/pwa/app.js"})             # cwd 無し＝前の行の cwd
             + tool_use("r3", "Read", {"file_path": "/Users/someone/other/secret-dir/keys.txt"}, cwd=cwd)  # プロジェクトの外
             + tool_use("r4", "Write", {"file_path": "/Users/someone/p/docs/a.md"}, cwd=cwd)
             + result("r4", "ok", toolUseResult={"type": "create", "filePath": "/Users/someone/p/docs/a.md", "content": "x"})
             + tool_use("r5", "NotebookEdit", {"notebook_path": "/Users/someone/p/nb/a.ipynb"}, cwd=cwd))
        got = self.rows(t)
        self.assertEqual([r["t"] for r in got if r["k"] == "tool"],
                         ["server/app.py", "ui/pwa/app.js", "keys.txt", "docs/a.md", "nb/a.ipynb"])
        self.assertEqual(got[4]["f"], "docs/a.md")                                         # 結果の f も相対
        out = json.dumps(got, ensure_ascii=False)
        for leak in ("secret-dir", "other", "/Users", "someone"):
            self.assertNotIn(leak, out)
        nocwd = self.rows(tool_use("r6", "Read", {"file_path": "/Users/someone/p/server/app.py"}))
        self.assertEqual(nocwd[0]["t"], "app.py")                                          # cwd が分からない＝basename
        self.assertEqual(ot._rel("/Users/someone/p/../q/a.py", cwd), "a.py")               # .. で外へ出る＝basename
        self.assertEqual(ot._rel("../q/a.py", cwd), "a.py")
        self.assertEqual(ot._rel("~/x/a.py", cwd), "a.py")

    def test_home_paths_are_collapsed_after_any_character(self):
        """実データ下見: scrub_output は `…`・>・|・（・： の直後のパスを畳まない＝Bash の全文でユーザー名が出た。直前の字を問わず畳む。"""
        cmd = ("echo x >/Users/someone/p/log.txt | tee`/Users/someone/p/a.sh` （/Users/someone/p/日本語/b.md） "
               "場所：/home/someone/c.py cd /Users/someone && ls")
        out = "\n".join(["パスは`/Users/someone/p/server/x.py`です", "-/Users/someone/q", "/Users/someone/",
                         "dir=${REPO:-/Users/someone/repo}", "旧パス/Users/someone/old.txt"])
        got = self.rows(tool_use("b", "Bash", {"command": cmd}) + result("b", out, toolUseResult={"stdout": out, "stderr": ""}))
        text = json.dumps(got, ensure_ascii=False)
        self.assertNotIn("someone", text)
        for want in ("log.txt", "`a.sh`", "（b.md）", "：c.py", "cd ~", "x.py", "-q", ":-repo}", "旧パスold.txt"):
            self.assertIn(want, got[0]["t"] + "\n" + got[1]["t"], want)
        self.assertIsNone(ot._HOME_RE.search("https://example.com/Users/keep/this"))      # URL の途中は畳まない（直前が英数）

    # ── B6 URL ────────────────────────────────────────────────────────────────
    def test_webfetch_shows_scheme_host_and_path_but_not_query(self):
        url = "https://user:pw@docs.example.com:8443/guide/intro;v=1?token=zz&q=1#frag"
        got = self.rows(tool_use("w", "WebFetch", {"url": url})
                        + tool_use("w2", "WebFetch", {"url": "https://api.example.com/v1/keys/sk-" + "Q" * 30 + "/x"})
                        + tool_use("w3", "WebFetch", {"url": "not a url"}))
        self.assertEqual(got[0]["t"], "https://docs.example.com:8443/guide/intro;v=1")
        self.assertEqual(got[1]["t"], "https://api.example.com/v1/keys/[secret]/x")         # path の区切りごとに伏字
        self.assertEqual(got[2]["t"], "")
        out = json.dumps(got, ensure_ascii=False)
        for leak in ("token", "zz", "frag", "pw@", "user", "Q" * 30):
            self.assertNotIn(leak, out)

    # ── B7 検索語（定数で切替） ─────────────────────────────────────────────────
    def test_search_terms_follow_the_switch(self):
        t = (tool_use("g", "Grep", {"pattern": "TODO sk-" + "R" * 30, "path": "/Users/x/p/server", "glob": "*.py"}, cwd="/Users/x/p")
             + result("g", "a\nb", toolUseResult={"numFiles": 2})
             + tool_use("h", "Glob", {"pattern": "**/*.md"}))
        self.assertTrue(ot.SHOW_SEARCH_TERMS, "本人裁定（2026-10-08）: 検索語は載せる")
        on = self.rows(t)
        self.assertEqual(on[0]["t"], 'pattern: "TODO [secret]", path: "server", glob: "*.py"')
        self.assertEqual(on[2]["t"], 'pattern: "**/*.md"')
        with mock.patch.object(ot, "SHOW_SEARCH_TERMS", False):
            off = self.rows(t)
        self.assertEqual((off[0]["t"], off[2]["t"]), ("server", ""))
        self.assertEqual(on[1]["nf"], off[1]["nf"])                                        # 結果の件数は従来どおり
        self.assertNotIn("R" * 30, json.dumps(on + off))

    # ── B8 サブエージェントの末尾 ───────────────────────────────────────────────
    def test_task_result_carries_the_tail_of_the_subagent(self):
        sec = "sk-" + "K" * 30
        with tempfile.TemporaryDirectory() as d:
            parent = _kids_tree(Path(d), "a1b2c3d4e5f60718", _child_lines(25, secret=sec))
            page = ot.public(ot.read_page(parent, None, scrub_output, CMD_RE))
            res = [r for r in page["rows"] if r["k"] == "res"][-1]
            kids = res["kids"]
            self.assertEqual(len(kids), ot.TASK_KIDS_MAX)
            self.assertEqual((kids[-1]["k"], kids[-1]["t"]), ("res", "out-24"))           # 末尾から
            self.assertEqual((kids[-2]["k"], kids[-2]["t"]), ("tool", "echo kid-24 [secret]"))   # 子の行も同じ伏字
            out = json.dumps(page, ensure_ascii=False)
            for leak in ("K" * 30, "CHILD-PROMPT", "PROMPT-BODY", "_aid", "_l\""):
                self.assertNotIn(leak, out)
            # 差分（カーソルの後）に来た Task の結果にも添える＝子は都度読み直すスナップショット
            sub = Path(d) / "proj" / "sess" / "subagents"
            (sub / "agent-b2b2b2b2.jsonl").write_text(_child_lines(3), encoding="utf-8")
            with parent.open("a", encoding="utf-8") as f:
                f.write(_task_pair("tb", "b2b2b2b2"))
            delta = ot.read_page(parent, page["cur"], scrub_output, CMD_RE)
            self.assertFalse(delta["reset"])
            self.assertEqual([r["t"] for r in delta["rows"][-1]["kids"]][-1], "out-2")
            self.assertNotIn("kids", ot.read_page(parent, None, scrub_output, CMD_RE, subagents=False)["rows"][-1])

    def test_workflow_children_are_found_and_paths_are_closed_in(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            parent = _kids_tree(root, "wf0000001", _child_lines(2), where=("subagents", "workflows", "wf_1"))
            self.assertEqual(ot.read_page(parent, None, scrub_output, CMD_RE)["rows"][-1]["kids"][-1]["t"], "out-1")
            outside = root / "outside.jsonl"
            outside.write_text(_child_lines(2, secret="sk-" + "Z" * 30), encoding="utf-8")
            sub = root / "proj" / "sess" / "subagents"
            os.symlink(outside, sub / "agent-evil0001.jsonl")                              # 子の置き場の外を指すリンク
            for aid in ("evil0001", "../../outside", "missing01"):
                with parent.open("a", encoding="utf-8") as f:
                    f.write(_task_pair("t-" + aid, aid))
                last = ot.read_page(parent, None, scrub_output, CMD_RE)["rows"][-1]
                self.assertEqual(last["k"], "res")
                self.assertFalse({"kids", "_aid"} & set(last), aid)
            names = sorted(os.path.basename(p) for p in ot.subagent_files(parent))
            self.assertIn("agent-wf0000001.jsonl", names)
            (sub / "agent-wf0000001.meta.json").write_text("{}", encoding="utf-8")
            self.assertFalse(any(n.endswith(".json") for n in (os.path.basename(p) for p in ot.subagent_files(parent))))

    def test_kids_are_attached_to_the_newest_tasks_only(self):
        with tempfile.TemporaryDirectory() as d:
            parent = _kids_tree(Path(d), "k000", _child_lines(1))
            sub = Path(d) / "proj" / "sess" / "subagents"
            with parent.open("a", encoding="utf-8") as f:
                for i in range(1, ot.KIDS_PAGE_MAX + 3):
                    (sub / ("agent-k%03d.jsonl" % i)).write_text(_child_lines(1), encoding="utf-8")
                    f.write(_task_pair("t%d" % i, "k%03d" % i))
            res = [r for r in ot.read_page(parent, None, scrub_output, CMD_RE)["rows"] if r["k"] == "res"]
        self.assertEqual([("kids" in r) for r in res], [False] * 3 + [True] * ot.KIDS_PAGE_MAX)

    def test_subagent_discovery_and_shrink_loop_are_shared_with_the_server(self):
        src = (ROOT / "server" / "office_server.py").read_text(encoding="utf-8")
        self.assertIn("office_term.subagent_files(path)", src)                             # minions と kids は同じ発見規則
        self.assertNotIn('path.stem / "subagents"', src)
        self.assertIn("range(office_term.SHRINK_STEPS + 1)", src)                          # 縮める回数は段の数に追従
        self.assertEqual(ot.SHRINK_STEPS, ot.SHRINK_HALVE + ot.ROWS_MAX.bit_length())

class R113S2Test(unittest.TestCase):
    """R113-B-S2（別モデルレビュー high 4・medium 2）の門＝1 件につき「壊すと赤」の回帰 1 件。"""

    def rows(self, text):
        return ot.rows_from_lines(text.splitlines(), scrub_output, CMD_RE)

    # ── 1 子の置き場は親のプロジェクトの配下だけ（リンクで外へ出ない） ──────────────────────
    def test_subagent_dir_linked_outside_the_project_is_not_read(self):
        child = _child_lines(2).replace("echo kid-", "echo OUTSIDE-")
        for how in ("subagents", "session"):
            with self.subTest(how), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                elsewhere = root / "elsewhere"
                (elsewhere / "subagents").mkdir(parents=True)
                (elsewhere / "subagents" / "agent-out00001.jsonl").write_text(child, encoding="utf-8")
                (root / "proj").mkdir()
                parent = root / "proj" / "sess.jsonl"
                parent.write_text(user("調べて") + _task_pair("ta", "out00001"), encoding="utf-8")
                if how == "subagents":                        # <session>/subagents がリンクで外を指す
                    (root / "proj" / "sess").mkdir()
                    os.symlink(elsewhere / "subagents", root / "proj" / "sess" / "subagents")
                else:                                         # <session> そのものがリンクで外を指す
                    os.symlink(elsewhere, root / "proj" / "sess")
                self.assertIsNone(ot.subagent_root(parent))
                self.assertEqual(list(ot.subagent_files(parent)), [])     # office_server の minions も同じ規則
                page = ot.read_page(parent, None, scrub_output, CMD_RE)
                self.assertNotIn("kids", page["rows"][-1])
                self.assertNotIn("OUTSIDE-", json.dumps(page, ensure_ascii=False))

    # ── 2 伏字は切り詰めの前に素材全体へ（末尾優先の切り出しで Bearer が落ちない） ─────────────
    def test_bearer_prefix_just_before_the_tail_window_does_not_leave_the_token(self):
        tok = "Zq7Wv3Kp9" * 4
        r = 2 * ot.BASH_OUT_MAX                                                             # 旧来の末尾の素材の窓
        paths = " " + " ".join("/very/long/dir/%s/g%d" % ("d" * 150, i) for i in range(90))   # 伏字で縮む＝切れ目が表示に入る
        tail = tok + paths
        tail += " " + "k" * (r - len(tail) - 1)
        text = "Exit code 1\n" + "m\n" * 20000 + "Authorization: Bearer " + tail
        self.assertTrue(text[len(text) - r:].startswith(tok))                              # 旧来の切れ目＝トークンの直前
        res = self.rows(tool_use("f", "Bash", {"command": "x"}) + result("f", text, is_error=True))[1]
        self.assertIn("g89", res["t"])                                                     # 末尾は見えている
        self.assertIn("[secret]", res["t"])
        self.assertNotIn(tok[:12], res["t"], "Bearer の文脈が切れてトークンが残った")
        # 先頭側（成功の出力）も同じ: 切れ目の直前の Bearer と直後のトークン
        head = "Authorization: Bearer " + tok + " " + " ".join("w%05d" % i for i in range(3000))
        ok = self.rows(tool_use("o", "Bash", {"command": "x"}) + result("o", head, toolUseResult={"stdout": head, "stderr": ""}))[1]
        self.assertNotIn(tok[:12], ok["t"])
        self.assertTrue(ok["t"].startswith("Authorization: [secret] w00000"), ok["t"][:60])

    # ── 3 区切りが見つからない長い語は断片を返さず固定の印 ───────────────────────────────
    def test_a_long_word_at_the_cut_becomes_the_fixed_mark(self):
        self.assertEqual(ot._head("ok " + "A" * 3000, 1500), "ok " + ot._OMIT)
        self.assertEqual(ot._tail("B" * 3000 + " end", 1500), ot._OMIT + " end")
        self.assertEqual(ot._head("C" * 3000, 1500), ot._OMIT)
        self.assertEqual(ot._tail("D" * 3000, 1500), ot._OMIT)
        self.assertEqual(ot._head("ok abc" + "E" * 10, 8), "ok ")                          # 短い語も断片は捨てる
        self.assertEqual(ot._tail("F" * 10 + "xyz end", 6), " end")
        jp = "日本語の長い行" * 400
        self.assertEqual(ot._head(jp, 100), jp[:100])                                     # 日本語は語の途中でも切ってよい
        for n in (5, 100, 1500):
            self.assertLessEqual(len(ot._head("ok " + "A" * 3000, n)), n)
            self.assertLessEqual(len(ot._tail("A" * 3000 + " ok", n)), n)
        cmd = "echo start " + "eyJ" + "x" * (ot.BASH_CMD_MAX * 2)                         # 上限をまたぐ長い語（区切り無し）
        got = self.rows(tool_use("b", "Bash", {"command": cmd}))[0]["t"]
        self.assertTrue(got.startswith("echo start "), got[:40])
        self.assertIn(ot._OMIT, got)
        self.assertNotIn("x" * 10, got, "長い語の断片が残った")
        self.assertLessEqual(len(got), ot.BASH_CMD_MAX)

    # ── 4 サブエージェント（kids）の発話も伏字→切る ─────────────────────────────────────
    def test_subagent_utterances_are_scrubbed_before_the_cut(self):
        tok, sk = "Tk9xYz" * 6, "sk-" + "a1B2c3D4e5" * 3
        child = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "指示 Authorization: Bearer " + tok}},
            {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [
                {"type": "text", "text": "返事 " + sk}]}},
            {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [
                {"type": "text", "text": "w " * (ot.TEXT_MAX // 2 - 4) + "Bearer " + tok}]}}))   # 上限の切れ目に Bearer
        with tempfile.TemporaryDirectory() as d:
            parent = _kids_tree(Path(d), "s2kid0001", child)
            kids = ot.read_page(parent, None, scrub_output, CMD_RE)["rows"][-1]["kids"]
        self.assertEqual([k["k"] for k in kids], ["u", "a", "a"])
        out = json.dumps(kids, ensure_ascii=False)
        for leak in (tok[:8], sk[3:15]):
            self.assertNotIn(leak, out, "子の発話の秘密が残った")
        self.assertEqual(out.count("[secret]"), 3)
        self.assertEqual(self.rows(user("親の指示 " + sk))[0]["t"], "親の指示 " + sk)      # 親の発話は従来どおり（上限だけ）

    # ── 5 cwd が分からなければ相対の入力パスも basename ───────────────────────────────────
    def test_relative_paths_without_a_cwd_fall_back_to_basename(self):
        for cwd in ("", None, "relative/cwd"):
            self.assertEqual(ot._rel("src/secret-dir/app.py", cwd), "app.py", cwd)
        self.assertEqual(ot._rel("src/app.py", "/w/p"), "src/app.py")                      # cwd があれば相対のまま
        got = self.rows(tool_use("r", "Read", {"file_path": "src/secret-dir/app.py"}))
        self.assertEqual(got[0]["t"], "app.py")
        self.assertNotIn("secret-dir", json.dumps(got))

    # ── 6 R113-B-S4: パスも伏字→relpath／basename→切る（Codex の _name と同じ型） ─────────────────
    def test_paths_are_scrubbed_before_relpath_or_basename(self):
        """`/` を含む秘密（Bearer の値）を relpath／basename で区切ってから伏字にすると、後ろ半分（ijklmnop…）が型に当たらない断片で残った。
        プロジェクトの外（basename）・cwd 無し（basename）・Grep の場所・Write の結果の f のどれでも残らない。プロジェクトの中の相対は従来どおり。"""
        cwd = "/w/p"
        slash = "Bearer abcdefgh/ijklmnopqrst"
        t = (tool_use("r1", "Read", {"file_path": "/w/other/%s.py" % slash}, cwd=cwd)              # プロジェクトの外＝basename
             + tool_use("r2", "Read", {"file_path": "src/%s.py" % slash})                         # cwd 無し＝前の行の cwd＝相対
             + tool_use("g1", "Grep", {"pattern": "x", "path": "/w/other/%s" % slash}, cwd=cwd)
             + tool_use("w1", "Write", {"file_path": "/w/other/%s.md" % slash}, cwd=cwd)
             + result("w1", "ok", toolUseResult={"type": "create", "filePath": "/w/other/%s.md" % slash, "content": "x"})
             + tool_use("r3", "Read", {"file_path": "/w/p/server/app.py"}, cwd=cwd))
        got = self.rows(t)
        out = json.dumps(got, ensure_ascii=False)
        for frag in ("ijklmnop", "abcdefgh"):
            self.assertNotIn(frag, out, "パスの秘密が断片で残った（区切ってから伏字にした）")
        self.assertEqual([r["t"] for r in got if r["k"] == "tool"],
                         ["[secret]", "src/[secret]", 'pattern: "x", path: "[secret]"', "[secret]", "server/app.py"])
        self.assertEqual([r["f"] for r in got if r.get("f")], ["[secret]"])
        for c in ("", None, "relative/cwd"):                                               # cwd が分からない＝basename
            self.assertEqual(ot._path_arg("src/%s.py" % slash, c, scrub_output), "[secret]", c)
        self.assertEqual(ot._path_arg("/w/p/cfg/%s.py" % slash, cwd, scrub_output), "cfg/[secret]")   # 中の相対は残す


class SessionPageTest(unittest.TestCase):
    """R113-B: session_page は cx- で Codex の rollout（source_codex_rollout）を、それ以外は従来の transcript を選ぶ（分岐は 1 か所）。"""

    def test_cx_prefix_selects_the_codex_rollout(self):
        import os
        import sqlite3
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            tid = "019a0000-0000-7000-8000-0000000000aa"
            roll = home / ".codex" / "sessions" / "2026" / "10" / "08" / ("rollout-x-%s.jsonl" % tid)
            roll.parent.mkdir(parents=True)
            roll.write_text(json.dumps({"timestamp": "2026-10-08T01:00:00Z", "type": "response_item", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Codex の返事"}]}},
                ensure_ascii=False) + "\n", encoding="utf-8")
            db = sqlite3.connect(str(home / ".codex" / "state_5.sqlite"))
            try:
                with db:
                    db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
                    db.execute("INSERT INTO threads VALUES (?, ?)", (tid, str(roll)))
            finally:
                db.close()
            claude = home / "s.jsonl"
            claude.write_text(user("Claude への指示"), encoding="utf-8")
            with mock.patch.dict(os.environ, {"OFFICE_HOME": str(home)}):
                page = ot.session_page("cx-" + tid, None, None, scrub_output, CMD_RE)
                self.assertEqual([r["t"] for r in page["rows"]], ["Codex の返事"])          # path（None）は使わない
                self.assertTrue(page["reset"])
                same = ot.session_page("cx-" + tid, None, page["cur"], scrub_output, CMD_RE)
                self.assertEqual((same["rows"], same["reset"]), ([], False))
                with self.assertRaises(FileNotFoundError):                                 # 引けない＝OSError（notfound）
                    ot.session_page("cx-019a0000-0000-7000-8000-0000000000bb", claude, None, scrub_output, CMD_RE)
                old = ot.session_page("sess-claude-1", claude, None, scrub_output, CMD_RE)  # 従来の経路はそのまま
                self.assertEqual(old, ot.read_page(claude, None, scrub_output, CMD_RE))
                self.assertEqual([r["t"] for r in old["rows"]], ["Claude への指示"])


if __name__ == "__main__":
    unittest.main()
