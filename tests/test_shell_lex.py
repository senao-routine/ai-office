# -*- coding: utf-8 -*-
"""R100-0: シェルの割り方の正本 hooks/office_shell_lex.py の門。

- 固定コーパス（tests/fixtures/shell_lex_corpus.json・61 本）の割り方が変わらない
  （切り出す前の office-event.sh の `_split` で作った値＝切り出しで意味が変わっていない証拠）
- office-event.sh に割り方の写しが残っていない（2 か所にあると片方だけ直ってずれる）
- Python 3.9 の構文（hook は /usr/bin/python3＝3.9 が先に選ばれる）
- hook は cwd（ユーザーのプロジェクト）にある同名ファイルを拾わない
- hooks/install.sh が部品も配る（配らないと配備先で証拠が全部「—」になる）
"""
import ast
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEX = ROOT / "hooks" / "office_shell_lex.py"
EVENT = ROOT / "hooks" / "office-event.sh"
CORPUS = ROOT / "tests" / "fixtures" / "shell_lex_corpus.json"


def load():
    spec = importlib.util.spec_from_file_location("office_shell_lex_t", LEX)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ShellLexTest(unittest.TestCase):
    def test_corpus_is_split_exactly_as_before(self):
        split = load().split
        rows = json.loads(CORPUS.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(rows), 60)
        for row in rows:
            segs, seps, unsafe, had_and, had_semi, had_group = split(row["cmd"])
            got = {"segs": [[[t, q] for t, q in s] for s in segs], "seps": seps, "unsafe": unsafe,
                   "and": had_and, "semi": had_semi, "group": had_group}
            want = {k: row[k] for k in got}
            self.assertEqual(got, want, row["cmd"])

    def test_python39_syntax(self):
        ast.parse(LEX.read_text(encoding="utf-8"), feature_version=(3, 9))

    def test_event_hook_has_no_copy_of_the_splitter(self):
        src = EVENT.read_text(encoding="utf-8")
        self.assertNotIn("segs, seps, seg, tok, quote, esc, unsafe", src, "office-event.sh に割り方の写しが残っている")
        self.assertIn("office_shell_lex.py", src)
        self.assertNotIn("sys.path.insert", src, "sys.path で読むと cwd の同名ファイルを拾い得る")

    def test_install_ships_the_lexer(self):
        src = (ROOT / "hooks" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("office_shell_lex.py", src)

    def test_hook_never_imports_the_lexer_from_cwd(self):
        """cwd（ユーザーのプロジェクト）に同名のファイルがあっても実行しない。hook の隣に無ければ証拠にしないだけ（exit 0）。"""
        # R106: test の最終判定はランナー自身の要約行も要る（割り方の部品が無いと要約探し自体が
        # 空振りする＝ここでは本物の要約を付けて「部品が実際に使われたか」を変わらず見分ける）。
        payload = {"hook_event_name": "PostToolUse", "session_id": "sess-lex00001", "tool_name": "Bash",
                   "tool_input": {"command": "npm test"},
                   "tool_response": {"exit_code": 0, "stdout": "Tests:       3 passed, 3 total\n"}, "cwd": "/tmp"}
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as hooks, \
                tempfile.TemporaryDirectory() as home:
            marker = Path(proj) / "PWNED"
            (Path(proj) / "office_shell_lex.py").write_text(
                f"open({str(marker)!r}, 'w').write('x')\ndef split(t):\n    return [], [], True, False, False, False\n",
                encoding="utf-8")
            lone = Path(hooks) / "office-event.sh"          # 部品を配っていない配備先を模す
            lone.write_text(EVENT.read_text(encoding="utf-8"), encoding="utf-8")
            kinds = []
            for hook in (EVENT, lone):
                r = subprocess.run(["bash", str(hook)], input=json.dumps(payload), capture_output=True,
                                   text=True, cwd=proj, env=dict(os.environ, OFFICE_HOME=home), timeout=30)
                self.assertEqual(r.returncode, 0, hook)
                self.assertFalse(marker.exists(), f"{hook}: cwd の office_shell_lex.py を実行した")
                d = Path(home) / ".claude" / "office_events"
                rows = [json.loads(x) for f in sorted(d.glob("*.jsonl")) for x in f.read_text().splitlines()]
                kinds.append(rows[-1]["kind"] if rows else None)
            # 対照: 部品が隣にある本物は npm test を「テストを走らせた」と読む／隣に無ければ証拠にしない
            self.assertEqual(kinds, ["test", ""], "hook が割り方の部品を使っていない（検査が空振り）")


class HookIsolationTest(unittest.TestCase):
    """R100-0: hook の python は隔離モード（-I）で起動する。`python -c`／`python -` の sys.path[0] は cwd＝
    Claude Code が hook を走らせる**ユーザーのプロジェクト**で、そこに json.py・secrets.py があると
    標準ライブラリより先に読まれて実行される（承認の hook がそれを踏むと、許可の判定そのものを乗っ取られる）。"""

    HOOKS = {
        "office-approval-wait.sh": ({"session_id": "sess-iso00001", "tool_name": "Bash",
                                     "tool_input": {"command": "ls"}, "transcript_path": "/nonexistent"},
                                    {"OFFICE_APPROVAL_WAIT": "0.5", "OFFICE_APPROVAL_POLL": "0.1"}),
        "office-event.sh": ({"session_id": "sess-iso00001", "hook_event_name": "PostToolUse",
                             "tool_name": "Bash", "tool_input": {"command": "npm test"}}, {}),
        "office-inbox-wait.sh": ({"session_id": "sess-iso00001", "hook_event_name": "Stop"},
                                 {"OFFICE_WAIT_LOOPS": "1", "OFFICE_WAIT_INTERVAL": "0"}),
        "office-statusline-capture.sh": ({"session_id": "sess-iso00001", "model": {"display_name": "x"}}, {}),
    }

    def test_hooks_do_not_import_from_the_project_dir(self):
        for name, (payload, extra) in self.HOOKS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as home:
                marker = Path(proj) / "PWNED"
                for mod in ("json", "secrets", "importlib", "time"):
                    (Path(proj) / f"{mod}.py").write_text(
                        f"open({str(marker)!r}, 'a').write({mod!r})\n", encoding="utf-8")
                r = subprocess.run(["bash", str(ROOT / "hooks" / name)], input=json.dumps(payload),
                                   capture_output=True, text=True, cwd=proj, timeout=60,
                                   env=dict(os.environ, OFFICE_HOME=home, **extra))
                self.assertIn(r.returncode, (0, 2), name)
                self.assertFalse(marker.exists(),
                                 f"{name}: cwd の {marker.read_text() if marker.exists() else ''}.py を実行した")

    def test_every_hook_python_call_is_isolated(self):
        import re
        for f in sorted((ROOT / "hooks").glob("*.sh")):
            if f.name == "install.sh":
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'"\$OFFICE_PY(_CAND)?" -(?!I )', line):
                    self.fail(f"{f.name}:{n}: python を -I 無しで起動している: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
