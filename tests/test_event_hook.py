#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R90-D4: hooks/office-event.sh（hook イベントの記録専用 hook）の番人。

守るもの:
  1. 本文を1バイトも書かない（ファイルパス・コマンド本文・tool_response・prompt 本文が行に存在しない）
  2. どんな入力・環境でも無出力 exit 0（壊れJSON・sid 不正・書けない dir）
  3. 1行 ≤1500 バイト・dir 0700 / file 0600・追記（複数イベントが同じ日付ファイルに並ぶ）
  4. 軽い（1発火 <150ms が受入条件。フレーク回避のためテストは 1.0s で判定し実測を表示）
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "office-event.sh"


def run_hook(home, payload):
    raw = payload if isinstance(payload, (str, bytes)) else json.dumps(payload, ensure_ascii=False)
    t0 = time.time()
    r = subprocess.run(["bash", str(HOOK)], input=raw, capture_output=True, text=True,
                       env={"OFFICE_HOME": str(home), "HOME": str(home),
                            "PATH": "/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin"})
    return r, time.time() - t0


def lines(home):
    d = Path(home) / ".claude" / "office_events"
    out = []
    for p in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        out.extend(p.read_text(encoding="utf-8").splitlines())
    return out


class EventHookTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_event_"))

    def test_edit_records_basename_only(self):
        r, sec = run_hook(self.home, {
            "session_id": "sess-evt-0001", "hook_event_name": "PostToolUse", "cwd": "/Users/x/secret-project",
            "tool_name": "Edit", "tool_input": {"file_path": "/Users/x/secret-project/docs/report.md",
                                                "old_string": "PASSWORD=hunter2", "new_string": "x"},
            "tool_response": {"content": "very secret content"},
            "transcript_path": "/Users/x/.claude/projects/p/s.jsonl"})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        ls = lines(self.home)
        self.assertEqual(len(ls), 1)
        rec = json.loads(ls[0])
        self.assertEqual((rec["v"], rec["ev"], rec["sid"], rec["tool"], rec["tgt"]),
                         (1, "PostToolUse", "sess-evt-0001", "Edit", "report.md"))
        self.assertEqual(len(rec["cwdh"]), 12)
        for secret in ("secret-project", "hunter2", "very secret", "transcript", "/Users/x"):
            self.assertNotIn(secret, ls[0])
        self.assertLess(sec, 1.0, f"hook が遅い: {sec:.3f}s")
        print(f"\n  event hook 1発火 {sec * 1000:.0f}ms")

    def test_bash_git_commit_is_classified_without_command_text(self):
        r, _ = run_hook(self.home, {
            "session_id": "sess-evt-0002", "hook_event_name": "PostToolUse", "cwd": "/w",
            "tool_name": "Bash", "tool_input": {"command": "cd /w && git commit -m 'add secret token abc123'"}})
        self.assertEqual(r.returncode, 0)
        rec = json.loads(lines(self.home)[0])
        self.assertEqual((rec["tool"], rec["kind"], rec["tgt"]), ("Bash", "git:commit", ""))
        self.assertNotIn("abc123", lines(self.home)[0])
        self.assertNotIn("commit -m", lines(self.home)[0])
        run_hook(self.home, {"session_id": "sess-evt-0002", "hook_event_name": "PostToolUse", "cwd": "/w",
                             "tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        self.assertEqual(json.loads(lines(self.home)[1])["kind"], "")

    def test_bash_test_runners_are_classified_without_command_text(self):
        """R98: 台帳の証拠列の元データ。テスト系は kind="test" の**分類だけ**（本文は書かない）。
        2 つの嘘を構造的に避ける（別モデルレビュー）:
          ① 名前に触れただけ（cat / echo / grep・引用の中・ヒアドキュメントの本文）は実行ではない
          ② 終了コードがテストのものでない形（`| tail`・`|| true`・`; echo`）は証拠にしない
        分からないときは何も言わない（台帳は「—」）＝失敗を成功で上書きしない。"""
        cases = [
            # 実際に走らせた＝終了コードがランナー自身のもの
            ("bash verify.sh", "test"),
            ("bash verify.sh > /tmp/log 2>&1", "test"),
            ("cd /w && python3 -m pytest tests -q", "test"),
            ("npm test -- --grep 'secret token'", "test"),
            ("node --test tests/hud_ids.test.mjs", "test"),
            ("cargo test --release", "test"),
            ("bash dev.sh --check", "test"),
            ("NO_COLOR=1 npx vitest run", "test"),
            ("uv run pytest -q", "test"),
            ("(cd w && pytest)", "test"),
            ("./verify.sh", "test"),
            # ★名前に触れただけ（実行していない）
            ("cat verify.sh", ""),
            ("echo pytest", ""),
            ("git diff -- verify.sh", ""),
            ("grep -n pytest tests/test_x.py", ""),
            ("bash dev.sh --shot", ""),
            ("rm -rf node_modules/jest", ""),
            ("echo test && ls testdata", ""),
            ("printf '%s' 'steps; pytest tests'", ""),           # 引用の中は実行ではない
            ("python3 - <<'PY'\nimport x  # pytest tests\nPY", ""),  # ヒアドキュメントの本文も
            ("echo 'bash verify.sh' >> notes.md", ""),
            # ★走らせたが**終了コードがテストのものでない**＝証拠にしない（失敗を成功で上書きしない）
            ("pytest -q | tail -3", ""),
            ("bash verify.sh 2>&1 | tail -20", ""),
            ("pytest || true", ""),
            ("pytest -q; echo done", ""),
            ("pytest &", ""),                                    # 待たない＝終了コードはテストのものでない
            ("pytest -q & wait", ""),
            ("pytest <<'IN'\ndata\nIN\necho done", ""),          # ヒアドキュメントの後ろに続く
            ("echo $(true; pytest)", ""),                        # 置換の中＝全体の終了コードは echo のもの
            ("echo `pytest -q`", ""),
            ("FOO=$(date +%s) bash verify.sh", ""),              # 置換が混じったら保守的に分類しない
            # git も同じ安全判定を通る（名前に触れただけ・終了コードが別物なら分類しない）
            ("git commit -m 'run pytest later'", "git:commit"),
            ("pytest -q && git commit -am wip", "git:commit"),
            ("git -C /w commit -m x", "git:commit"),
            ("git push origin master", "git:push"),
            ("echo 'git commit'", ""),
            ("git commit -am wip || true", ""),
            ("git commit -am wip | tee log", ""),
            ("cat .git/COMMIT_EDITMSG", ""),
            ("echo done # retry later; pytest -q", ""),         # コメントの中は実行ではない
            ("git commit -am wip && git push", "git:push"),      # 終了コードは最後のもの
            # 走らせていない形（版表示・ヘルプ・収集のみ）と、引用の中のオプション
            ("pytest --version", ""),
            ("pytest --collect-only tests", ""),
            ("npm test --help", ""),
            ("node -e 'console.log(1) // --test'", ""),
            ("node --test tests/a.mjs", "test"),
            ("cargo test --no-run", ""),                         # ビルドだけ
            ("make test -n", ""),                                # 何をするか表示するだけ
            ("TEST_CMD='npx vitest run'", ""),                    # 代入だけ＝実行していない
            ("CMD=\"bash verify.sh\"", ""),
            ("node app.js --test", ""),                          # スクリプトの引数であって node のオプションではない
            ("node --eval='console.log(42)' -- --test", ""),
            ("npx jest --showConfig", ""),
            ("node --test --watch tests/", "test"),
            ("git -C /repo commit --dry-run", ""),               # コミットしていない
            ("git --version", ""),
            ("if [ ! -d tests ]; then exit 0; fi; python3 -m unittest", ""),  # 到達したとは限らない
            ("for f in a b; do pytest $f; done", ""),
            # 10 巡目の対策が行き過ぎて普通の実行を落としていた（引数の "." ・値の引用）
            ("pytest .", "test"),
            ("python3 -m unittest discover -s .", "test"),
            ("git -C . commit -m wip", "git:commit"),
            ("PYTEST_ADDOPTS=\"-q\" pytest", "test"),
            ("cat <(printf x; pytest)", ""),                     # プロセス置換
            ("pytest --setup-plan", ""),
            ("go test -list Test", ""),
            ("make test -t", ""), ("make test -sn", ""),          # レシピを実行しない
            ("exec true; python3 -m unittest", ""),               # exec で置き換わる
            ("set -n; python3 -m unittest", ""),                  # シェルオプションで実行されない
            ("set -e; false; pytest", ""),
            ("source env.sh; pytest", ""),                        # 許可リストに無い先行コマンド
            ("ulimit -n 999; pytest", ""),
            ("trap 'x' EXIT; pytest", ""),
            ("echo start; pytest -q", "test"),                    # 支度だけの先行は許す
            ("cd /w; python3 -m unittest", "test"),
            ("exec true && pytest", ""),                         # 走らずに 0 で終わる
            ("set -n && pytest", ""),
            ("go test -list=Test", ""),                          # 値つきの非実行オプション
            ("npx jest --showConfig=true", ""),
            ("git add -A && git commit -m x", "git:commit"),
            ("command exec true && python3 -m unittest", ""),     # 前置きで実体を隠しても見抜く
            ("sudo exec true; pytest", ""),
            ("! pytest", ""),
            ("! (echo begin && python3 -m unittest x)", ""),      # 否定は終了コードを反転させる
            ("npx vitest list --filesOnly", ""),                  # 一覧を出すだけ
            ('PYTEST_ADDOPTS="--collect-only" pytest', ""),        # 環境変数に忍ばせた非実行
            ("MAKEFLAGS=-n make test", ""),
            ("make test -i", ""),                                 # 失敗しても 0 を返す
            ('PYTEST_ADDOPTS="-q" pytest', "test"),                # 普通の実行は拾う
            ("NO_COLOR=1 FORCE_COLOR=0 pytest -q", "test"),
            ("export MAKEFLAGS=-i; make test", ""),                # 先行 export の効果も見る
            ("export FOO=1; make test", "test"),
            ("git -C . commit --short", ""),                       # git の暗黙 dry-run
            ("git commit --porcelain", ""),
            ('export MAKEFLAGS="-i -s"; make test', ""),           # 値に空白があっても見る
            ("builtin exit 0 && python3 -m unittest", ""),        # 読めない先行は通さない
            ("go test -c ./...", ""),                             # ビルドだけ
            ("go test ./...", "test"),
            ("npm test --if-present", ""),                        # スクリプトが無ければ何もしない
            ("pytest --cache-show", ""), ("npx jest --clearCache", ""),
            ('python3 -m unittest "$(printf %s --help)"', ""),     # 二重引用符の中の置換
            ('bash verify.sh "a b"', "test"),                      # ただの引数は普通に拾う
            ("pytest() (false)", ""),                             # 関数定義は実行ではない
            ("go test -c=true ./...", ""),                        # 値つきのコンパイル専用
            ("pytest -o addopts=--collect-only", ""),             # 設定で収集だけにする
            ("pytest -o addopts=-q", "test"),
            ("pytest --collectonly", ""),                         # 綴り違いの収集専用
            ("git --html-path commit", ""),                       # 表示して終わる
            ("git --exec-path", ""),
        ]
        for i, (cmd, want) in enumerate(cases):
            run_hook(self.home, {"session_id": f"sess-evt-{i:04d}", "hook_event_name": "PostToolUse", "cwd": "/w",
                                 "tool_name": "Bash", "tool_input": {"command": cmd}})
        recs = [json.loads(ln) for ln in lines(self.home)]
        self.assertEqual([r["kind"] for r in recs], [want for _, want in cases])
        joined = "\n".join(lines(self.home))
        for secret in ("secret token", "--grep", "tests/hud_ids", "--release", "node_modules"):
            self.assertNotIn(secret, joined)

    def test_short_circuit_failure_is_not_attributed(self):
        """R98: `false && pytest` は pytest が走っていないのに全体は失敗する＝「テストの失敗」ではない。
        短絡（&&）が在る形は**終了 0 で、かつ先行が読める形のときだけ**最後のコマンドへ帰属できる
        （`builtin exit 0 && pytest` のように読めない先行は 0 でも走っていない・別モデルレビュー）。"""
        for i, (ev, cmd, want) in enumerate([
            ("PostToolUse", "false && pytest -q", ""),            # 先行 `false` は読めない＝通さない
            ("PostToolUse", "cd /w && pytest -q", "test"),        # 読める支度なら 0 で走って成功
            ("PostToolUseFailure", "false && pytest -q", ""),     # 失敗は誰の失敗か分からない
            ("PostToolUseFailure", "pytest -q", "test"),          # 単独なら失敗もテストのもの
            ("PostToolUseFailure", "cd /w; pytest -q", "test"),   # ; は短絡しない
            ("PostToolUseFailure", "cd /nonexistent && pytest", ""),  # cd が失敗しても同じ形
        ]):
            run_hook(self.home, {"session_id": f"sess-sc-{i:04d}", "hook_event_name": ev, "cwd": "/w",
                                 "tool_name": "Bash", "tool_input": {"command": cmd}})
        self.assertEqual([json.loads(ln)["kind"] for ln in lines(self.home)],
                         ["", "test", "", "test", "test", ""])

    def test_background_by_response_is_not_evidence(self):
        """R98: Bash が途中でバックグラウンドへ移ると入力に印が無く、**応答に id** が返る。
        まだ終わっていない＝証拠にしない（別モデルレビュー）。"""
        for i, (response, want) in enumerate([
            ({"backgroundTaskId": "bg1"}, ""), ({"taskId": "t1"}, ""),
            ({"stdout": "ok"}, "test"), ({}, "test"),
        ]):
            run_hook(self.home, {"session_id": f"sess-bgr-{i:04d}", "hook_event_name": "PostToolUse",
                                 "cwd": "/w", "tool_name": "Bash",
                                 "tool_input": {"command": "bash verify.sh"}, "tool_response": response})
        self.assertEqual([json.loads(ln)["kind"] for ln in lines(self.home)], ["", "", "test", "test"])

    def test_background_bash_is_not_evidence(self):
        """R98: `run_in_background` は**起動しただけ**で PostToolUse が返る＝終了コードを知らない。
        テストも git commit も分類しない（台帳は「—」＝失敗を成功で上書きしない・別モデルレビュー）。"""
        for i, (payload, want) in enumerate([
            ({"command": "bash verify.sh", "run_in_background": True}, ""),
            ({"command": "git commit -am wip", "run_in_background": True}, ""),
            ({"command": "bash verify.sh", "run_in_background": False}, "test"),
            ({"command": "bash verify.sh"}, "test"),
        ]):
            run_hook(self.home, {"session_id": f"sess-bg-{i:04d}", "hook_event_name": "PostToolUse",
                                 "cwd": "/w", "tool_name": "Bash", "tool_input": payload})
        self.assertEqual([json.loads(ln)["kind"] for ln in lines(self.home)],
                         ["", "", "test", "test"])

    def test_prompt_records_length_only(self):
        run_hook(self.home, {"session_id": "sess-evt-0003", "hook_event_name": "UserPromptSubmit",
                             "cwd": "/w", "prompt_text": "この本文は絶対に記録しない " * 3})
        rec = json.loads(lines(self.home)[0])
        self.assertGreater(rec["plen"], 0)
        self.assertNotIn("記録しない", lines(self.home)[0])

    def test_task_and_subagent_and_notification_fields(self):
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "TaskCreated", "cwd": "/w",
                             "task_id": "t1", "task_input": {"subject": "x" * 200, "description": "long"}})
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "SubagentStart", "cwd": "/w",
                             "agent_id": "a-1", "agent_type": "Explore"})
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "Notification", "cwd": "/w",
                             "notification_type": "permission_prompt", "message": "Claude needs your permission"})
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "PostToolUseFailure", "cwd": "/w",
                             "tool_name": "Bash", "tool_input": {"command": "false"}, "error": "boom"})
        ls = [json.loads(l) for l in lines(self.home)]
        self.assertEqual(ls[0]["task"], "t1")
        self.assertNotIn("tsub", ls[0])                      # 件名は自由記述＝記録しない（レビュー指摘）
        self.assertNotIn("xxxxx", lines(self.home)[0])
        self.assertEqual(ls[1]["sub"], "a-1")
        self.assertEqual(ls[2]["nt"], "permission_prompt")
        self.assertNotIn("needs your permission", lines(self.home)[2])
        self.assertFalse(ls[3]["ok"])
        for l in lines(self.home):
            self.assertLessEqual(len(l.encode("utf-8")), 1500)

    def test_bad_inputs_are_silent(self):
        for payload in ("", "{not json", json.dumps({"hook_event_name": "Stop"}),
                        json.dumps({"session_id": "../etc", "hook_event_name": "Stop"}),
                        json.dumps({"session_id": "ok-sid", "hook_event_name": "bad name"}),
                        json.dumps([1, 2, 3])):
            r, _ = run_hook(self.home, payload)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), payload)
        self.assertEqual(lines(self.home), [])
        self.assertFalse((self.home / ".claude" / "office_events").exists())

    def test_unwritable_dir_is_silent(self):
        base = self.home / ".claude"
        base.mkdir()
        os.chmod(base, 0o500)
        try:
            r, _ = run_hook(self.home, {"session_id": "sess-evt-0005", "hook_event_name": "Stop", "cwd": "/w"})
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        finally:
            os.chmod(base, 0o700)

    def test_permissions_and_append(self):
        for i in range(3):
            run_hook(self.home, {"session_id": "sess-evt-0006", "hook_event_name": "Stop", "cwd": "/w"})
        d = self.home / ".claude" / "office_events"
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        files = list(d.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(len(lines(self.home)), 3)

    def test_free_text_reason_is_not_recorded(self):
        run_hook(self.home, {"session_id": "sess-evt-0008", "hook_event_name": "SessionEnd", "cwd": "/w",
                             "reason": "/Users/example/private token=abc"})
        run_hook(self.home, {"session_id": "sess-evt-0008", "hook_event_name": "SessionStart", "cwd": "/w",
                             "source": "resume"})
        ls = lines(self.home)
        self.assertEqual(json.loads(ls[0])["src"], "")
        self.assertNotIn("private", ls[0])
        self.assertEqual(json.loads(ls[1])["src"], "resume")

    def test_huge_tool_response_still_records(self):
        payload = {"session_id": "sess-evt-0009", "hook_event_name": "PostToolUse", "cwd": "/w",
                   "tool_name": "Read", "tool_input": {"file_path": "/w/big.txt"},
                   "tool_response": {"content": "x" * 2_000_000}}
        r, sec = run_hook(self.home, payload)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        rec = json.loads(lines(self.home)[0])
        self.assertEqual((rec["tool"], rec["tgt"]), ("Read", "big.txt"))
        self.assertLess(sec, 2.0)

    def test_oversized_line_drops_optional_fields_before_giving_up(self):
        run_hook(self.home, {"session_id": "sess-evt-0007", "hook_event_name": "Skill", "cwd": "/w",
                             "tool_name": "Skill", "tool_input": {"skill": "s" * 5000}})
        ls = lines(self.home)
        self.assertEqual(len(ls), 1)
        self.assertLessEqual(len(ls[0].encode("utf-8")), 1500)


if __name__ == "__main__":
    unittest.main()
