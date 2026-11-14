# -*- coding: utf-8 -*-
"""R113-B-C: Codex の rollout をスマホのセッション画面（端末の行）へ（server/source_codex_rollout.py）の門。

- B11 安全な読み取り: cx-<thread_id> の検算・SQLite は読み取り専用（mode=ro＋query_only・SELECT だけ）・
  rollout_path は ~/.codex/sessions か archived_sessions の配下の rollout-*.jsonl だけ（外・../・リンクで外へ・名前違いは読まない）
- B12 行への変換: 実機から切り出した匿名化フィクスチャ 3 系統（旧版 function_call／新版 custom_tool_call exec＋item_completed／
  item_completed の無い形）で行の件数・種類・伏字・写しの二重なし・知らない型の無視
- B13 exec の input: SHOW_EXEC_INPUT の両分岐（本人裁定 2026-10-08＝True）
- カーソル（差分・遡り）は office_term と同じ形
- R113-B-I: 上限と縮め方は office_term と同じ 1 本（命令の入力 cmd_text・出力 out_text・差分・shrink の CMD_TOOLS）
"""
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import office_term as ot  # noqa: E402
import source_codex_rollout as scr  # noqa: E402
from office_actions import scrub_output  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "codex_rollout"
TID = "019a0000-0000-7000-8000-000000000001"
KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2


def fixture(name, real_ids=True):
    """real_ids: 匿名化（capture_fixture）が消した McpToolCall の id の形を実機の形へ戻す。実機では exec の中から呼んだもの（写しでない）の
    id は全部 `exec-…`（手元の rollout 5,611 本で 742/742・写しは call_… で 660/660）。exec_custom_tool_call の 6 件は全部 codex_app の
    道具を exec の中から呼んだもの（どの call_id とも一致しない＝写しでない）なので、`"id": "id-…"` を `"id": "exec-id-…"` へ戻す。"""
    lines = (FIX / (name + ".jsonl")).read_bytes().splitlines()
    if not real_ids:
        return lines
    return [x.replace(b'"type": "McpToolCall", "id": "id-', b'"type": "McpToolCall", "id": "exec-id-') for x in lines]


def rows_of(lines, **kw):
    return scr.rows_from_codex_lines(lines, scrub_output, **kw)


def ln(typ, payload, sec=0):
    return json.dumps({"timestamp": "2026-10-08T01:00:%02d.000Z" % sec, "type": typ, "payload": payload},
                      ensure_ascii=False)


def msg(role, *texts, **kw):
    parts = [{"type": "output_text" if role == "assistant" else "input_text", "text": t} for t in texts]
    return ln("response_item", dict({"type": "message", "role": role, "content": parts}, **kw))


def item(it):
    return ln("event_msg", {"type": "item_completed", "item": it})


def fcall(name, args, call_id):
    return ln("response_item", {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id})


def ccall(name, inp, call_id):
    return ln("response_item", {"type": "custom_tool_call", "name": name, "input": inp, "call_id": call_id,
                                "status": "completed"})


def out(call_id, output, custom=False):
    return ln("response_item", {"type": "custom_tool_call_output" if custom else "function_call_output",
                                "call_id": call_id, "output": output})


def claude_bash(cmd, out, err=False):
    """同じ命令と出力を Claude の transcript の形で（Bash の tool_use と tool_result）＝上限が同じかを比べる相手。"""
    use = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": cmd}}]}}, ensure_ascii=False)
    b = {"type": "tool_result", "tool_use_id": "b1", "content": out}
    extra = {}
    if err:
        b["is_error"] = True
    else:
        extra["toolUseResult"] = {"stdout": out, "stderr": ""}
    res = json.dumps(dict({"type": "user", "message": {"role": "user", "content": [b]}}, **extra), ensure_ascii=False)
    return ot.rows_from_lines([use, res], scrub_output, re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>"))


def words(n, seed, width=60):
    """伏字の型に当たらない本文（小文字の短い語）を n 字・width 字ごとに改行（/ を含まない＝パスに畳まれない）。"""
    rnd, out, line = random.Random(seed), [], 0
    while sum(len(x) for x in out) < n:
        w = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(3, 9)))
        line += len(w) + 1
        out.append(w + ("\n" if line >= width else " "))
        line = 0 if line >= width else line
    return "".join(out)[:n]


def make_home(tmp, threads):
    """一時 HOME に Codex の SQLite（threads: id, rollout_path）を作る。threads＝{id: rollout_path の文字列}。"""
    home = Path(tmp)
    (home / ".codex" / "sessions").mkdir(parents=True, exist_ok=True)
    (home / ".codex" / "archived_sessions").mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(home / ".codex" / "state_5.sqlite"))
    try:
        with db:
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, cwd TEXT NOT NULL DEFAULT '')")
            db.executemany("INSERT INTO threads (id, rollout_path) VALUES (?, ?)", list(threads.items()))
    finally:
        db.close()
    return home


class FixtureRowsTest(unittest.TestCase):
    """実機（2026-10-08）から tools/capture_fixture.py --kind codex_rollout で切り出した 3 系統。"""

    def kinds(self, rows):
        out = {}
        for r in rows:
            out[r["k"]] = out.get(r["k"], 0) + 1
        return out

    def assert_linked(self, rows):
        ids = set()
        for r in rows:
            if r["k"] == "tool" and r.get("id"):
                ids.add(r["id"])
            if r["k"] == "res" and "for" in r:
                self.assertIn(r["for"], ids, "結果の印が先に出た道具の行と結ばれていない")

    def test_legacy_function_call(self):
        """旧版（0.46: function_call shell／update_plan・custom_tool_call apply_patch）。item_completed の写しは二重にしない。"""
        lines = fixture("legacy_function_call")
        rows = rows_of(lines)
        self.assertEqual(self.kinds(rows), {"u": 5, "th": 11, "tool": 5, "res": 5, "a": 5, "todo": 2})
        self.assertEqual([r["n"] for r in rows if r["k"] == "tool"], ["Shell", "Shell", "apply_patch", "Shell", "apply_patch"])
        patch = [r for r in rows if r.get("n") == "apply_patch"]
        self.assertTrue(all(r["t"].endswith(".md") and "/" not in r["t"] for r in patch), "apply_patch は basename だけ")
        self.assertEqual([[x[0] for x in r["i"]] for r in rows if r["k"] == "todo"], [["i", "p"], ["c", "c"]])
        self.assertFalse(any(r["k"] == "res" and "Plan updated" in r["t"] for r in rows), "update_plan の結果は todo の行で足りる")
        n_ri_asst = sum(1 for x in lines if b'"role": "assistant"' in x)
        n_agent = sum(1 for x in lines if b'"AgentMessage"' in x)
        self.assertEqual((n_ri_asst, n_agent), (5, 5))
        self.assertEqual(self.kinds(rows)["a"], n_ri_asst, "AgentMessage（写し）で AI の行が二重になった")
        self.assertFalse(any(r["t"].startswith(("<", "# AGENTS.md")) for r in rows if r["k"] == "u"), "注入の本文を人の指示として出した")
        self.assert_linked(rows)

    def test_no_item_completed_makes_the_same_rows(self):
        """item_completed の無い形（旧版の実 rollout から item_completed 行を落として再現）＝response_item だけで同じ行。"""
        lines = fixture("no_item_completed")
        self.assertFalse(any(b"item_completed" in x for x in lines))
        self.assertEqual(rows_of(lines), rows_of(fixture("legacy_function_call")))

    def test_exec_custom_tool_call(self):
        """新版（0.159: custom_tool_call exec＋item_completed の CommandExecution／McpToolCall／FileChange）。"""
        lines = fixture("exec_custom_tool_call")
        rows = rows_of(lines)
        self.assertEqual(self.kinds(rows), {"u": 1, "a": 3, "tool": 16, "res": 17, "th": 7, "turn": 1})
        names = [r["n"] for r in rows if r["k"] == "tool"]
        self.assertEqual(names.count("exec"), 6)
        self.assertEqual(names.count("Shell"), 3)                       # CommandExecution
        self.assertEqual(sum(1 for n in names if n.startswith("codex_app.")), 6)   # McpToolCall（サーバー.道具）
        raw = [r["n"] for r in rows_of(fixture("exec_custom_tool_call", real_ids=False)) if r["k"] == "tool"]
        self.assertEqual(sum(1 for n in raw if n.startswith("codex_app.")), 0,
                         "id が exec-… でない McpToolCall（写し・知らない形）を item_completed から出した")
        self.assertEqual(names.count("spawn_agent"), 1)
        spawn = next(r for r in rows if r.get("n") == "spawn_agent")
        self.assertEqual([(r.get("st"), r["t"]) for r in rows if r.get("for") == spawn["id"]],
                         [("bg", "裏で動いています（サブエージェント）")])         # 結果の本文（task_name 等）は載せない
        self.assertTrue(all(r["t"].startswith("REDACTED(") for r in rows if r.get("n") == "exec"), "exec の input（伏字・上限）")
        self.assertEqual(sum(1 for r in rows if r["k"] == "res" and r.get("err")), 1)   # 失敗した McpToolCall
        fc = [r for r in rows if r["k"] == "res" and "f" in r]
        self.assertEqual(len(fc), 1)
        self.assertEqual((fc[0]["f"], fc[0]["add"], fc[0]["rem"]), ("memory.md", 12, 0))
        self.assertLessEqual(len(fc[0]["d"]), ot.DIFF_LINES)
        self.assertEqual([r["ms"] for r in rows if r["k"] == "turn"], [288388])
        # サブエージェント同士のやり取り（agent_message・SubAgentActivity）と developer・AGENTS.md の注入は出さない
        self.assertEqual(len([r for r in rows if r["k"] == "u"]), 1)
        self.assertFalse(any("Message Type" in r.get("t", "") for r in rows))
        n_ri_asst = sum(1 for x in lines if b'"role": "assistant"' in x)
        self.assertEqual(self.kinds(rows)["a"], n_ri_asst, "AgentMessage（写し）で AI の行が二重になった")
        self.assert_linked(rows)

    def test_every_fixture_row_is_closed_and_scrubbed(self):
        for name in ("legacy_function_call", "no_item_completed", "exec_custom_tool_call"):
            with self.subTest(name):
                rows = rows_of(fixture(name))
                text = json.dumps(rows, ensure_ascii=False)
                for r in rows:
                    self.assertIn(r["k"], ot.KINDS)
                    self.assertFalse({"_l", "_offs"} & set(r))
                    if r["k"] in ("tool", "res"):
                        self.assertEqual(scrub_output(r["t"]), r["t"], "伏字を通していない（scrub は冪等）")
                        self.assertLessEqual(len(r["t"]), ot.BASH_CMD_MAX if r["k"] == "tool" else ot.BASH_OUT_MAX)
                self.assertNotIn("gAAAA", text, "暗号化された考えを載せた")
                self.assertNotIn("encrypted_content", text)

    def test_show_exec_input_both_branches(self):
        """B13（本人裁定 2026-10-08）: 既定は True＝input をそのまま伏字＋上限。False＝exec の名前だけ。"""
        self.assertIs(scr.SHOW_EXEC_INPUT, True)
        lines = fixture("exec_custom_tool_call")
        on = [r for r in rows_of(lines, show_exec_input=True) if r.get("n") == "exec"]
        off = [r for r in rows_of(lines, show_exec_input=False) if r.get("n") == "exec"]
        self.assertEqual(len(on), 6)
        self.assertTrue(all(r["t"] for r in on))
        self.assertEqual([r["t"] for r in off], [""] * 6)
        with mock.patch.object(scr, "SHOW_EXEC_INPUT", False):        # 定数を変えると既定の呼び方も名前だけ
            self.assertEqual([r["t"] for r in rows_of(lines) if r.get("n") == "exec"], [""] * 6)
        secret = ccall("exec", "await tools.exec_command({cmd: 'curl -H \"Authorization: Bearer %s\" x'})" % KEY, "c1")
        got = rows_of([secret], show_exec_input=True)
        self.assertNotIn(KEY, json.dumps(got), "exec の input の秘密を伏字にしていない")
        self.assertLessEqual(len(got[0]["t"]), ot.BASH_CMD_MAX)


class SyntheticRowsTest(unittest.TestCase):
    def test_injected_user_parts_are_dropped(self):
        rows = rows_of([
            msg("user", "# AGENTS.md instructions for /x\n\n<INSTRUCTIONS>…"),
            msg("user", "<environment_context>\n  <cwd>/x</cwd>\n</environment_context>"),
            msg("user", "<image name=[Image #1]>", "</image>", "この画像を直して"),
            msg("developer", "<permissions>…"),
            item({"type": "UserMessage", "id": "u1", "content": [{"type": "text", "text": "この画像を直して"}]}),
        ])
        self.assertEqual([(r["k"], r["t"]) for r in rows], [("u", "この画像を直して")])

    def test_mirrors_are_not_doubled(self):
        rows = rows_of([
            item({"type": "AgentMessage", "id": "m1", "content": [{"type": "Text", "text": "返事"}]}),
            msg("assistant", "返事", id="m1"),
            item({"type": "Reasoning", "id": "r1", "summary_text": ["秘密の考え"], "raw_content": []}),
            ln("response_item", {"type": "reasoning", "id": "r1", "summary": [{"type": "summary_text", "text": "秘密の考え"}],
                                 "encrypted_content": "gAAAAABsecret"}),
            fcall("js", {"code": "await x()", "title": "ページを開く"}, "c9"),
            item({"type": "McpToolCall", "id": "c9", "server": "node_repl", "tool": "js", "status": "completed",
                  "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}}),
            out("c9", "ok"),
            item({"type": "McpToolCall", "id": "exec-m7", "server": "codex_app", "tool": "read_thread", "status": "completed",
                  "arguments": {"threadId": "t"}, "result": {"content": [{"type": "text", "text": "本文"}]}}),
        ])
        self.assertEqual([(r["k"], r.get("n")) for r in rows],
                         [("a", None), ("th", 1), ("tool", "js"), ("res", None), ("tool", "codex_app.read_thread"), ("res", None)])
        self.assertEqual(rows[2]["t"], "ページを開く")
        text = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("秘密の考え", text, "考えの本文を載せた")
        self.assertNotIn("gAAAA", text)

    def test_mcp_is_not_doubled_across_windows(self):
        """R113-B-S3（別モデルレビュー medium）: MCP の道具の呼び出しの写し（function_call と item_completed の McpToolCall）は、
        差分の取得の窓の境目がどこに来ても 1 回だけ（状態を持たない＝response_item 側だけ・exec の中から呼んだもの＝id が exec-… は
        response_item に無いので item_completed から 1 回）。旧来は「同じ窓に呼び出しが見えたか」で決めていて、窓をまたぐと二重に出た。"""
        lines = [
            msg("user", "スレッドを見て"),
            ln("response_item", {"type": "function_call", "name": "js", "namespace": "mcp__node_repl__",
                                 "arguments": json.dumps({"code": "x", "title": "ページを開く"}), "call_id": "call_m1"}),
            item({"type": "McpToolCall", "id": "call_m1", "server": "node_repl", "tool": "js", "status": "completed",
                  "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}}),
            out("call_m1", "ok"),
            ccall("exec", "await tools.codex_app.read_thread({})", "call_x1"),
            item({"type": "McpToolCall", "id": "exec-k1", "server": "codex_app", "tool": "read_thread", "status": "completed",
                  "result": {"content": [{"type": "text", "text": "本文"}]}}),
            out("call_x1", [{"type": "input_text", "text": "Script completed"}], custom=True),
            msg("assistant", "見ました"),
        ]

        def tools(rows):
            return [r["n"] for r in rows if r["k"] == "tool"]

        whole = tools(rows_of(lines))
        self.assertEqual(whole, ["js", "exec", "codex_app.read_thread"])
        for k in range(len(lines) + 1):                         # 窓の境目をどこに置いても同じ（状態を持たない）
            with self.subTest(split=k):
                self.assertEqual(tools(rows_of(lines[:k]) + rows_of(lines[k:])), whole)
        with tempfile.TemporaryDirectory() as d:                 # 本物の差分の読み方（read_page の cur）で 2 回に分けて読む
            p = Path(d) / "rollout-mcp.jsonl"
            p.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
            first = scr.read_page(p, None, scrub_output)
            with p.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines[2:]) + "\n")
            second = scr.read_page(p, first["cur"], scrub_output)
        self.assertFalse(second["reset"])
        self.assertEqual(tools(first["rows"]) + tools(second["rows"]), whole, "窓をまたいだ MCP の写しを二重に出した")

    def test_consecutive_reasoning_counts(self):
        r = {"type": "reasoning", "summary": [], "encrypted_content": "gAAAA"}
        rows = rows_of([ln("response_item", r), ln("event_msg", {"type": "token_count"}), ln("response_item", r),
                        ln("response_item", r), msg("assistant", "a"), ln("response_item", r)])
        self.assertEqual([(x["k"], x.get("n")) for x in rows], [("th", 3), ("a", None), ("th", 1)])

    def test_secrets_are_scrubbed_everywhere(self):
        rows = rows_of([
            fcall("shell", {"command": ["bash", "-lc", "curl -H 'Authorization: Bearer %s' https://x" % KEY]}, "c1"),
            out("c1", json.dumps({"output": "token=%s" % KEY, "metadata": {"exit_code": 0}})),
            item({"type": "CommandExecution", "id": "e1", "command": ["/bin/zsh", "-lc", "export OPENAI_API_KEY=%s" % KEY],
                  "aggregated_output": "key: %s" % KEY, "exit_code": 0, "status": "completed"}),
            item({"type": "FileChange", "id": "f1", "status": "completed", "changes": {
                "/Users/someone/secret/app.env": {"type": "update", "unified_diff": "@@ -1 +1 @@\n-A=1\n+API_KEY=%s\n" % KEY}}}),
            item({"type": "McpToolCall", "id": "exec-m1", "server": "s", "tool": "t", "status": "completed",
                  "arguments": {"token": KEY}, "result": {"content": [{"type": "text", "text": "Bearer %s" % KEY}]}}),
        ])
        text = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn(KEY, text, "秘密が端末の行に出た")
        self.assertNotIn("/Users/someone", text, "パスを basename にしていない")
        self.assertEqual(rows[0]["n"], "Shell")
        self.assertTrue(rows[0]["t"].startswith("curl "), "[bash, -lc, 本文] は本文を出す")
        fc = [r for r in rows if r.get("f")]
        self.assertEqual(fc[0]["f"], "app.env")

    def test_limits_are_office_terms(self):
        """R113-B-I: 上限は office_term と同じ 1 本（命令の入力 BASH_CMD_MAX・出力 BASH_OUT_MAX・失敗は先頭 40%＋中略＋末尾 60%・
        差分 DIFF_LINES×DIFF_COL・新規作成は WRITE_HEAD 行）。同じ命令と出力なら Claude の Bash の行と字面まで一致する。"""
        long_cmd = "echo " + words(ot.BASH_CMD_MAX * 3, 1, width=90)
        long_out = "\n".join("line %d %s" % (i, "x" * 80) for i in range(300))
        fail_out = "\n".join("step %d ok" % i for i in range(400)) + "\nerror: the real reason is at the end"
        diff = "@@ -1,150 +1,150 @@\n" + "\n".join("+" + "z" * 400 for _ in range(150))
        rows = rows_of([
            item({"type": "CommandExecution", "id": "e1", "command": ["zsh", "-lc", long_cmd],
                  "aggregated_output": long_out, "exit_code": 0, "status": "completed"}),
            ccall("exec", "x " * 5000, "c1"),
            item({"type": "FileChange", "id": "f1", "status": "completed",
                  "changes": {"a.py": {"type": "update", "unified_diff": diff},
                              "b.py": {"type": "add", "content": "\n".join("new %d" % i for i in range(200))}}}),
            msg("assistant", "\n".join("段落 %d" % i for i in range(200))),
            msg("user", "u" * 5000),
            item({"type": "CommandExecution", "id": "e2", "command": ["zsh", "-lc", "make test"],
                  "aggregated_output": fail_out, "exit_code": 2, "status": "failed"}),
            item({"type": "McpToolCall", "id": "exec-m1", "server": "s", "tool": "t", "status": "completed",
                  "result": {"content": [{"type": "text", "text": long_out}]}}),
        ])
        shell, res, exe, fc, add, a, u, _ftool, fres, _mtool, mres = rows
        self.assertLessEqual(len(shell["t"]), ot.BASH_CMD_MAX)
        self.assertLess(shell["t"].count("\n"), ot.BASH_CMD_LINES)
        self.assertGreater(len(shell["t"]), ot.ARG_MAX * 10, "命令の入力が旧上限（ARG_MAX）のまま")
        self.assertLessEqual(len(exe["t"]), ot.BASH_CMD_MAX)
        self.assertGreater(len(exe["t"]), ot.ARG_MAX * 10)
        self.assertLessEqual(len(res["t"]), ot.BASH_OUT_MAX)
        self.assertLess(res["t"].count("\n"), ot.BASH_OUT_LINES)
        self.assertGreater(res["t"].count("\n"), ot.RESULT_LINES * 10, "命令の出力が旧上限（RESULT_LINES）のまま")
        self.assertEqual(res["nl"], 300)
        # Claude の Bash と同じ（入力・成功の出力・失敗のサンドイッチ）
        c_tool, c_res = claude_bash(long_cmd, long_out)
        self.assertEqual((shell["t"], res["t"]), (c_tool["t"], c_res["t"]))
        self.assertEqual(fres["t"], claude_bash("make test", fail_out, err=True)[1]["t"])
        self.assertEqual(fres["err"], 1)
        self.assertRegex(fres["t"], r"…（中略 \d+ 行）…")
        self.assertTrue(fres["t"].endswith("error: the real reason is at the end"), "失敗の理由（末尾）が落ちた")
        # 命令でない結果（MCP）は RESULT_MAX
        self.assertLessEqual(len(mres["t"]), ot.RESULT_MAX)
        self.assertLess(mres["t"].count("\n"), ot.RESULT_LINES)
        self.assertEqual(len(fc["d"]), ot.DIFF_LINES)
        self.assertTrue(all(len(x[2]) <= ot.DIFF_COL for x in fc["d"]))
        self.assertEqual(fc["add"], 150)
        self.assertEqual((add["wl"], len(add["d"])), (200, ot.WRITE_HEAD))
        self.assertLessEqual(a["t"].count("\n"), ot.TEXT_LINES - 1)
        self.assertLessEqual(len(a["t"]), ot.TEXT_MAX)
        self.assertLessEqual(len(u["t"]), ot.USER_MAX)

    def test_errors_images_and_turn_marks(self):
        rows = rows_of([
            fcall("shell", {"command": ["bash", "-lc", "false"]}, "c1"),
            out("c1", json.dumps({"output": "", "metadata": {"exit_code": 1}})),
            ccall("exec", "x", "c2"),
            out("c2", [{"type": "input_text", "text": "Script failed\nWall time 0.1s"}], custom=True),
            fcall("exec_command", {"cmd": "ls nope"}, "c3"),
            out("c3", "Chunk ID: 1\nWall time: 0.0 seconds\nProcess exited with code 2\nOutput:\nls: nope"),
            item({"type": "CommandExecution", "id": "e1", "command": ["zsh", "-lc", "exit 3"], "aggregated_output": "",
                  "exit_code": 3, "status": "failed"}),
            fcall("view_image", {"path": "/Users/x/shot.png"}, "c4"),
            out("c4", [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]),
            item({"type": "FileChange", "id": "f1", "status": "failed",
                  "changes": {"/x/new.txt": {"type": "add", "content": "a\nb\n"}, "/x/old.txt": {"type": "delete"}}}),
            ln("event_msg", {"type": "turn_aborted", "reason": "interrupted"}),
            ln("compacted", {"message": "要約"}),
            ln("event_msg", {"type": "task_complete", "duration_ms": 61000}),
        ])
        res = [r for r in rows if r["k"] == "res"]
        self.assertEqual([bool(r.get("err")) for r in res], [True, True, True, True, False, True, True])
        self.assertEqual(res[1]["t"].split("\n")[0], "Script failed")
        img = res[4]
        self.assertEqual((img.get("img"), img["t"]), (1, "画像を読み込み"))
        self.assertNotIn("base64", json.dumps(rows))
        self.assertEqual([r["t"] for r in rows if r.get("n") == "view_image"], ["shot.png"])
        self.assertEqual((res[5]["f"], res[5]["wl"], res[5]["d"][0]), ("new.txt", 2, ["+", 1, "a"]))
        self.assertEqual(res[6]["t"], "old.txt を削除")
        self.assertEqual([r["k"] for r in rows[-3:]], ["int", "sys", "turn"])
        self.assertEqual(rows[-1]["ms"], 61000)

    def test_subagent_reports_are_summarized(self):
        """サブエージェントの道具（collaboration）の結果は要約だけ（Claude 側の Task の結果と同じ・報告の本文は載せない）。
        呼び出しが前の窓にあって名前が分からなくても、結果の形（timed_out・task_name…）で当てる。"""
        rows = rows_of([
            fcall("wait_agent", {"timeout_ms": 1000}, "w1"),
            out("w1", json.dumps({"message": "サブの最終報告（長い本文）", "timed_out": False}, ensure_ascii=False)),
            out("w2", json.dumps({"message": "窓の外の呼び出しの報告", "timed_out": True}, ensure_ascii=False)),
            out("w3", json.dumps({"task_name": "/root/review"})),
            fcall("send_message", {"target": "x", "message": "サブへの指示"}, "w4"),
            out("w4", json.dumps({"submission_id": "s1"})),
        ])
        self.assertEqual([(r["k"], r.get("st"), r["t"]) for r in rows],
                         [("tool", None, ""), ("res", None, "完了"), ("res", None, "待ち時間切れ"),
                          ("res", "bg", "裏で動いています（サブエージェント）"), ("tool", None, ""), ("res", None, "完了")])
        text = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("報告", text)
        self.assertNotIn("サブへの指示", text)

    def test_exec_success_is_not_turned_into_an_error_by_inner_exit_codes(self):
        rows = rows_of([ccall("exec", "x", "c1"), out("c1", [
            {"type": "input_text", "text": "Script completed\nWall time 0.2s"},
            {"type": "input_text", "text": "Process exited with code 1\nOutput:\nls: nope"}], custom=True)])
        self.assertNotIn("err", rows[1], "exec が成功したのに中の命令の終了コードで失敗にした")

    def test_file_names_are_scrubbed_before_they_are_cut(self):
        """R113-B-S3（別モデルレビュー high）: ファイル名（apply_patch の見出し・FileChange・view_image の path）は伏字→切るの順
        （office_term._fit と同じ型）。①長いパッチの拾う範囲（_PATCH_SCAN）の境目で長いファイル名の末尾の sk-… が切れても断片が残らない
        ②basename で `/` の所を切る前に伏字（Bearer の値は `/` を含めるので、先に basename にすると後ろ半分が型に当たらない断片で残る）。"""
        name = "n" * 150 + "-" + KEY
        head = "*** Add File: "
        body = "*** Begin Patch\n*** Update File: a.py\n"
        at = scr._PATCH_SCAN - len(head) - len(name) + len(KEY) - 8      # 旧来の先切りなら KEY は「sk-a1B2c」＝型に当たらない 5 字
        pad = at - len(body) - 1
        patch = body + "+" + "y" * (pad - 1) + "\n" + head + name + "\n+z\n*** End Patch"
        self.assertEqual(patch.index(head + name), at)
        rows = rows_of([ccall("apply_patch", patch, "p1"), fcall("apply_patch", {"input": patch}, "p2")])
        self.assertEqual([r["t"] for r in rows], ["a.py, " + "n" * 150 + "-[secret]"] * 2)
        slash = "Bearer abcdefgh/ijklmnopqrstuv.py"                  # 区切りの後ろ（ijklmnop…）も秘密の一部
        rows += rows_of([
            item({"type": "FileChange", "id": "f1", "status": "completed", "changes": {
                "/Users/x/proj/" + name: {"type": "add", "content": "a\n"},
                "cfg/" + slash: {"type": "update", "unified_diff": "@@ -1 +1 @@\n-a\n+b\n"}}}),
            fcall("view_image", {"path": "shots/" + slash}, "v1"),
            ccall("apply_patch", "*** Begin Patch\n*** Delete File: cfg/%s\n*** End Patch" % slash, "p3"),
        ])
        text = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("sk-", text)
        for frag in (KEY[:8], KEY[3:8], KEY[-8:], "ijklmnop", "abcdefgh"):
            self.assertNotIn(frag, text, "ファイル名の秘密が断片で残った（切ってから伏字にした）")
        self.assertEqual([r.get("f") for r in rows if r.get("f")], ["n" * 150 + "-[secret]", "[secret]"])
        self.assertEqual([r["t"] for r in rows if r.get("n") in ("view_image", "apply_patch")][-2:], ["[secret]", "[secret]"])

    def test_tool_and_server_names_are_scrubbed_before_they_are_cut(self):
        """R113-B-S4: 道具の名前（function_call の name）と McpToolCall のサーバー名・道具名も伏字→切る。旧来は [:48]／[:40] で先に切り、
        伏字も通していなかった（名前の末尾の sk-… が断片のまま載った）。長い名前は伏字の後で 48 字（部品は 40 字）。"""
        name = "lookup_" + "x" * 30 + "-" + KEY
        rows = rows_of([
            fcall(name, {}, "n1"),
            out("n1", "ok"),
            item({"type": "McpToolCall", "id": "exec-n2", "server": "srv-" + KEY, "tool": "read", "status": "completed",
                  "result": {"content": [{"type": "text", "text": "ok"}]}}),
            item({"type": "McpToolCall", "id": "exec-n3", "server": "s" * 60, "tool": "t" * 60, "status": "completed",
                  "result": {"content": [{"type": "text", "text": "ok"}]}}),
        ])
        text = json.dumps(rows, ensure_ascii=False)
        for frag in (KEY[:8], KEY[3:8], KEY[-8:]):
            self.assertNotIn(frag, text, "名前の秘密が断片で残った（切ってから／伏字なしで載せた）")
        names = [r["n"] for r in rows if r["k"] == "tool"]
        self.assertEqual(names[:2], ["lookup_" + "x" * 30 + "-[secret]", "srv-[secret].read"])
        self.assertTrue(names[2].startswith("s" * (scr.MCP_PART_MAX - 1) + "….t"), names[2])   # 長いサーバー名でも道具名が見える
        self.assertTrue(all(len(n) <= scr.NAME_MAX for n in names))
        self.assertEqual([r["k"] for r in rows], ["tool", "res", "tool", "res", "tool", "res"])
        self.assertEqual(rows[1]["for"], rows[0]["id"])                                 # 素の名前で結果を結ぶ（伏字の名前で振り分けない）

    def test_update_plan_is_a_todo_and_its_output_is_skipped(self):
        rows = rows_of([
            fcall("update_plan", {"plan": [{"step": "調べる", "status": "completed"}, {"step": "直す", "status": "in_progress"},
                                           {"step": "確かめる", "status": "pending"}]}, "p1"),
            out("p1", "Plan updated"),
            ccall("apply_patch", "*** Begin Patch\n*** Update File: /Users/x/a/b.py\n@@\n-x\n+y\n*** Add File: c.md\n+z\n*** End Patch", "p2"),
        ])
        self.assertEqual(rows[0], {"k": "todo", "i": [["c", "調べる"], ["i", "直す"], ["p", "確かめる"]], "t": "Todo を更新", "ts": rows[0]["ts"]})
        self.assertEqual((rows[1]["n"], rows[1]["t"]), ("apply_patch", "b.py, c.md"))
        self.assertEqual(len(rows), 2)

    def test_unknown_and_broken_lines_are_ignored(self):
        junk = [
            b"not json", b"[]", b"null", b"\xff\xfe", "{" * 5000,
            ln("response_item", "x"),
            ln("response_item", {"type": "brand_new_kind", "text": "x"}),
            ln("response_item", {"type": "message", "role": "assistant", "content": "文字列"}),
            ln("response_item", {"type": "message", "role": "assistant", "content": [None, 3, {"type": "x"}]}),
            ln("response_item", {"type": "function_call", "name": "shell", "arguments": "{not json", "call_id": "c"}),
            ln("response_item", {"type": "function_call", "name": None, "arguments": 3}),
            ln("response_item", {"type": "image_generation_call", "result": "iVBORw0KGgo" * 100}),
            ln("response_item", {"type": "agent_message", "content": [{"type": "input_text", "text": "サブの報告"}]}),
            ln("event_msg", {"type": "item_completed", "item": "x"}),
            ln("event_msg", {"type": "item_completed", "item": {"type": "Hologram", "text": "x"}}),
            ln("event_msg", {"type": "item_completed", "item": {"type": "SubAgentActivity", "agent_path": "/root/x"}}),
            ln("event_msg", {"type": "item_completed", "item": {"type": "FileChange", "changes": "x"}}),
            ln("event_msg", {"type": "item_completed", "item": {"type": "CommandExecution", "command": None}}),
            ln("event_msg", {"type": "task_complete", "duration_ms": True}),
            ln("event_msg", {"type": "token_count", "rate_limits": {}}),
            ln("world_state", {"full": True}), ln("mystery", {}), json.dumps({"type": "session_meta"}),
            msg("assistant", "最後の行"),
        ]
        rows = rows_of(junk)
        # 残るのは: 引数が壊れた shell（名前だけ・引数を推測しない）／command の無い CommandExecution（名前だけ＋結果）／最後の行
        self.assertEqual([(r["k"], r.get("n"), r["t"]) for r in rows],
                         [("tool", "Shell", ""), ("tool", "Shell", ""), ("res", None, "（出力なし）"), ("a", None, "最後の行")])
        self.assertNotIn("iVBORw0KGgo", json.dumps(rows))
        self.assertNotIn("サブの報告", json.dumps(rows, ensure_ascii=False))


class ReaderTest(unittest.TestCase):
    """B11: SQLite は読み取り専用・rollout_path は ~/.codex/sessions か archived_sessions の配下だけ。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.homedir = base / "home"
        sessions = self.homedir / ".codex" / "sessions" / "2026" / "10" / "08"
        archived = self.homedir / ".codex" / "archived_sessions"
        sessions.mkdir(parents=True)
        archived.mkdir(parents=True)
        self.good = sessions / ("rollout-2026-10-08T01-00-00-%s.jsonl" % TID)
        self.good.write_text(msg("user", "一つ目") + "\n" + msg("assistant", "二つ目") + "\n", encoding="utf-8")
        self.arch = archived / "rollout-old.jsonl"
        self.arch.write_text(msg("assistant", "しまったもの") + "\n", encoding="utf-8")
        outside = base / "secret"
        outside.mkdir()
        (outside / "rollout-x.jsonl").write_text(msg("assistant", "外の秘密") + "\n", encoding="utf-8")
        (sessions / "rollout-link.jsonl").symlink_to(outside / "rollout-x.jsonl")
        (sessions / "notes.jsonl").write_text(msg("assistant", "名前違い") + "\n", encoding="utf-8")
        (self.homedir / ".codex" / "sessions" / "rollout-dir.jsonl").mkdir()
        self.ids = {
            "good": TID, "arch": "019a0000-0000-7000-8000-000000000002", "outside": "019a0000-0000-7000-8000-000000000003",
            "dots": "019a0000-0000-7000-8000-000000000004", "link": "019a0000-0000-7000-8000-000000000005",
            "name": "019a0000-0000-7000-8000-000000000006", "rel": "019a0000-0000-7000-8000-000000000007",
            "gone": "019a0000-0000-7000-8000-000000000008", "dir": "019a0000-0000-7000-8000-000000000009",
            "root": "019a0000-0000-7000-8000-00000000000a",
        }
        make_home(self.homedir, {
            self.ids["good"]: str(self.good),
            self.ids["arch"]: str(self.arch),
            self.ids["outside"]: str(outside / "rollout-x.jsonl"),
            self.ids["dots"]: str(self.homedir / ".codex" / "sessions" / ".." / ".." / ".." / "secret" / "rollout-x.jsonl"),
            self.ids["link"]: str(sessions / "rollout-link.jsonl"),
            self.ids["name"]: str(sessions / "notes.jsonl"),
            self.ids["rel"]: "sessions/rollout-2026-10-08T01-00-00-%s.jsonl" % TID,
            self.ids["gone"]: str(sessions / "rollout-gone.jsonl"),
            self.ids["dir"]: str(self.homedir / ".codex" / "sessions" / "rollout-dir.jsonl"),
            self.ids["root"]: str(self.homedir / ".codex" / "sessions"),
        })

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, key):
        return scr.rollout_path("cx-" + self.ids[key], self.homedir)

    def test_confinement(self):
        self.assertEqual(self.path("good"), self.good.resolve())
        self.assertEqual(self.path("arch"), self.arch.resolve())
        for key, why in (("outside", "閉じ込めの外"), ("dots", "../ で外へ"), ("link", "リンクで外へ"),
                         ("name", "rollout-*.jsonl でない"), ("rel", "相対パス"), ("gone", "無いファイル"),
                         ("dir", "ディレクトリ"), ("root", "sessions そのもの")):
            with self.subTest(why):
                self.assertIsNone(self.path(key), why)
                with self.assertRaises(FileNotFoundError):
                    scr.term_page("cx-" + self.ids[key], None, scrub_output, home=self.homedir)

    def test_root_symlinks_are_not_followed(self):
        """R113-B-S3（別モデルレビュー high）: 許可の根（~/.codex/sessions・archived_sessions）や ~/.codex そのものが外を指すリンクなら、
        その根は使わない（根を resolve した実体で比べるだけだと、リンクの先の rollout-*.jsonl を「配下」として読んだ）。ホームそのもののリンクは許す。"""
        base = Path(self.tmp.name)
        outside = base / "elsewhere"
        (outside / "codex" / "sessions").mkdir(parents=True)
        leak = outside / "rollout-leak.jsonl"
        leak.write_text(msg("assistant", "リンクの先の秘密") + "\n", encoding="utf-8")
        deep = outside / "codex" / "sessions" / "rollout-deep.jsonl"
        deep.write_text(msg("assistant", "リンクの先の秘密") + "\n", encoding="utf-8")
        ids = ["019a0000-0000-7000-8000-0000000000%02x" % i for i in range(0x10, 0x16)]

        # ① sessions そのものが外へのリンク（archived_sessions は本物）
        h1 = base / "h1"
        (h1 / ".codex").mkdir(parents=True)
        (h1 / ".codex" / "sessions").symlink_to(outside, target_is_directory=True)
        (h1 / ".codex" / "archived_sessions").mkdir()
        ok1 = h1 / ".codex" / "archived_sessions" / "rollout-ok.jsonl"
        ok1.write_text(msg("assistant", "本物") + "\n", encoding="utf-8")
        make_home(h1, {ids[0]: str(h1 / ".codex" / "sessions" / "rollout-leak.jsonl"), ids[1]: str(leak), ids[2]: str(ok1)})
        # ② ~/.codex そのものが外へのリンク（sessions はリンクの先の本物のディレクトリ）
        h2 = base / "h2"
        h2.mkdir()
        (h2 / ".codex").symlink_to(outside / "codex", target_is_directory=True)
        make_home(h2, {ids[3]: str(h2 / ".codex" / "sessions" / "rollout-deep.jsonl"), ids[4]: str(deep)})
        # ③ ホームそのもののリンク（/var → /private/var のような）は許す
        hl = base / "home-link"
        hl.symlink_to(self.homedir, target_is_directory=True)

        for home, tid, why in ((h1, ids[0], "sessions がリンク（リンク越しのパス）"), (h1, ids[1], "sessions がリンク（実体のパス）"),
                               (h2, ids[3], "~/.codex がリンク（リンク越しのパス）"), (h2, ids[4], "~/.codex がリンク（実体のパス）")):
            with self.subTest(why):
                self.assertIsNone(scr.rollout_path("cx-" + tid, home), why)
                with self.assertRaises(FileNotFoundError):
                    scr.term_page("cx-" + tid, None, scrub_output, home=home)
        self.assertEqual(scr.rollout_path("cx-" + ids[2], h1), ok1.resolve(), "リンクでない方の根まで使えなくした")
        self.assertEqual(scr.rollout_path("cx-" + TID, hl), self.good.resolve(), "ホームそのもののリンクまで拒否した")

    def test_bad_session_ids_never_reach_sqlite(self):
        seen = []
        for bad in (None, 123, "", "cx-", "abc", "cx-ab", "cx-" + "a" * 70, "cx-../../etc", "cx-a/b-c-d-e-f",
                    "cx-" + TID + "\n", "CX-" + TID, "cx-" + TID + "' OR '1'='1"):
            with self.subTest(repr(bad)):
                self.assertIsNone(scr.rollout_path(bad, self.homedir, trace=seen.append))
        self.assertEqual(seen, [], "形の違う session で SQLite を開いた")

    def test_sqlite_is_read_only(self):
        db = self.homedir / ".codex" / "state_5.sqlite"
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        listing = sorted(p.name for p in db.parent.iterdir())
        statements = []
        self.assertIsNotNone(scr.rollout_path("cx-" + TID, self.homedir, trace=statements.append))
        self.assertEqual(statements, ["PRAGMA query_only=1", "SELECT rollout_path FROM threads WHERE id = '%s'" % TID])
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in db.parent.iterdir()), listing, "-wal/-shm などを作った")

    def test_missing_or_broken_db(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(scr.rollout_path("cx-" + TID, empty))
        broken = Path(self.tmp.name) / "broken"
        (broken / ".codex").mkdir(parents=True)
        (broken / ".codex" / "state_5.sqlite").write_bytes(b"not a database" * 100)
        self.assertIsNone(scr.rollout_path("cx-" + TID, broken))

    def test_home_comes_from_office_home(self):
        with mock.patch.dict(os.environ, {"OFFICE_HOME": str(self.homedir)}):
            self.assertEqual(scr.rollout_path("cx-" + TID), self.good.resolve())

    def test_term_page_reset_delta_and_back(self):
        page = scr.term_page("cx-" + TID, None, scrub_output, home=self.homedir)
        self.assertTrue(page["reset"])
        self.assertEqual([r["t"] for r in page["rows"]], ["一つ目", "二つ目"])
        self.assertEqual(page["cur"]["off"], self.good.stat().st_size)
        with self.good.open("a", encoding="utf-8") as f:
            f.write(msg("user", "三つ目") + "\n" + msg("assistant", "書きかけ")[:-5])
        delta = scr.term_page("cx-" + TID, page["cur"], scrub_output, home=self.homedir)
        self.assertFalse(delta["reset"])
        self.assertEqual([r["t"] for r in delta["rows"]], ["三つ目"])         # 書きかけの行は待つ
        pub = ot.public(page)
        self.assertIsNone(pub["beg"])                                         # 小さいファイル＝もう前が無い
        self.assertTrue(pub["top"])
        self.assertIsNone(scr.term_page("cx-" + TID, None, scrub_output, before={"off": 1, "h": "0" * 16}, home=self.homedir))


class CursorTest(unittest.TestCase):
    """カーソルは office_term と同じ形・同じ検算（差分・取り違え・遡りで全部の行が戻る）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name) / "rollout-x.jsonl"
        lines = [msg("user", "指示 %d" % i) if i % 2 == 0 else msg("assistant", "返事 %d" % i) for i in range(400)]
        self.p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_walk_back_to_the_top_reproduces_every_row(self):
        whole = rows_of(self.p.read_bytes().splitlines())
        page = ot.public(scr.read_page(self.p, None, scrub_output))
        self.assertEqual(len(page["rows"]), ot.ROWS_MAX)
        got, beg, n = list(page["rows"]), page["beg"], 0
        while beg is not None:
            older = scr.read_before(self.p, beg, scrub_output)
            self.assertIsNotNone(older)
            older = ot.public(older)
            self.assertTrue(older["older"])
            self.assertLessEqual(len(older["rows"]), ot.BACK_ROWS)
            got, beg, n = older["rows"] + got, older["beg"], n + 1
            self.assertLess(n, 20)
        self.assertEqual([r["t"] for r in got], [r["t"] for r in whole])
        self.assertTrue(older["top"])

    def test_rewritten_file_and_bad_cursor_reset(self):
        a = scr.read_page(self.p, None, scrub_output)
        for bad in ({"off": a["cur"]["off"] + 10, "h": a["cur"]["h"]}, {"off": a["cur"]["off"], "h": "0" * 16},
                    {"off": "1", "h": a["cur"]["h"]}, None):
            with self.subTest(bad):
                self.assertTrue(scr.read_page(self.p, bad, scrub_output)["reset"])
        same = scr.read_page(self.p, a["cur"], scrub_output)
        self.assertEqual((same["rows"], same["reset"]), ([], False))

    def test_shrink_and_public_work_on_codex_pages(self):
        # 段は番号の直書きでなく名前で（R113-B-S が前に 3 段を足した＝旧来の「2＝半分」は SHRINK_OLD になった）
        page = scr.read_page(self.p, None, scrub_output)
        first = ot.public(page)["beg"]
        self.assertTrue(ot.shrink(page, ot.SHRINK_HALVE))
        after = ot.public(page)
        self.assertGreater(after["beg"]["off"], first["off"])
        self.assertTrue(after["gap"])
        older = ot.public(scr.read_before(self.p, after["beg"], scrub_output))    # 落とした行も遡りで取り直せる
        whole = [r["t"] for r in rows_of(self.p.read_bytes().splitlines())]
        i = whole.index(page["rows"][0]["t"])
        self.assertEqual([r["t"] for r in older["rows"]], whole[max(0, i - ot.BACK_ROWS):i])   # 落とした行の直前まで


class ShrinkTest(unittest.TestCase):
    """R113-B-I: Codex の 1 枚も office_term.shrink の段の順で縮み、封じる側のループの回数の中で ENVELOPE_BUDGET に収まる。"""

    def test_worst_codex_page_shrinks_like_claude_and_fits(self):
        lines = []
        for i in range(60):                                   # 命令の対（入力・出力とも上限を超える・半分は失敗）
            lines.append(item({"type": "CommandExecution", "id": "e%d" % i,
                               "command": ["zsh", "-lc", words(ot.BASH_CMD_MAX + 500, i, width=100)],
                               "aggregated_output": words(ot.BASH_OUT_MAX + 1000, 1000 + i),
                               "exit_code": i % 2, "status": "failed" if i % 2 else "completed"}))
        for i in range(15):
            lines.append(ccall("exec", words(ot.BASH_CMD_MAX + 500, 2000 + i, width=100), "x%d" % i))
            hunk = "@@ -1,%d +1,%d @@\n" % (ot.DIFF_LINES, ot.DIFF_LINES) + "\n".join(
                "+" + words(ot.DIFF_COL + 50, 3000 + i * 1000 + j, width=10 ** 6) for j in range(ot.DIFF_LINES))
            lines.append(item({"type": "FileChange", "id": "f%d" % i, "status": "completed",
                               "changes": {"m%d.py" % i: {"type": "update", "unified_diff": hunk}}}))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rollout-worst.jsonl"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            page = scr.read_page(p, None, scrub_output)
        self.assertEqual(len(page["rows"]), ot.ROWS_MAX)
        self.assertFalse(ot.fits(page))
        names = {r.get("n") for r in page["rows"] if r["k"] == "tool"}
        self.assertEqual(names, {"Shell", "exec"})
        self.assertTrue(names <= set(ot.CMD_TOOLS), "Codex の命令の道具が縮める段（CMD_TOOLS）から漏れた")
        for step in range(ot.SHRINK_OLD + 1):                 # kids（無い）→ 差分を半分 → 従来相当
            self.assertTrue(ot.shrink(page, step))
        for r in page["rows"]:
            if r["k"] == "tool":
                self.assertLessEqual(len(r["t"]), ot.ARG_MAX, r["n"])
                self.assertLess(r["t"].count("\n"), ot.ARG_LINES)
            elif "nl" in r:
                self.assertLessEqual(len(r["t"]), ot.RESULT_MAX)
                self.assertLess(r["t"].count("\n"), ot.RESULT_LINES)
            if "d" in r:
                self.assertLessEqual(len(r["d"]), ot.DIFF_LINES_OLD)
                self.assertTrue(all(len(x[2]) <= ot.DIFF_COL_OLD for x in r["d"]))
        step = ot.SHRINK_OLD + 1                              # 封じる側（office_server._term_sealed）と同じ回し方で収まる
        while not ot.fits(page):
            self.assertLess(step, ot.SHRINK_STEPS + 1, "封じる側のループの回数の中で収まらない")
            self.assertTrue(ot.shrink(page, step))
            step += 1
        self.assertLessEqual(ot.sealed_size(page), ot.ENVELOPE_BUDGET)
        self.assertTrue(page["rows"])

    def test_no_hardcoded_raw_cuts_or_bare_scrub(self):
        """素材の先切りの直書き（[:2000] 等）と、office_term._scrub を通さない伏字（先頭が消える・ホームのパスが残る）を戻さない。"""
        src = (ROOT / "server" / "source_codex_rollout.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"[\w)\]]\[:(2000|400|600|4000|12000)\]", src), "素材の先切りが直書きに戻った")
        self.assertIsNone(re.search(r"(?<![\w.])scrub\(", src), "office_term._scrub を通さずに伏字を掛けた")
        for old in ("ARG_MAX", "RESULT_MAX", "RESULT_LINES", "DIFF_COL_OLD", "DIFF_LINES_OLD"):
            self.assertNotIn("office_term." + old, src, "Codex の行が旧上限（縮めた後の値）を直に使っている")


if __name__ == "__main__":
    unittest.main()
