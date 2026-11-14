#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R90-D4: hooks/office-event.sh（hook イベントの記録専用 hook）の番人。

守るもの:
  1. 本文を1バイトも書かない（ファイルパス・コマンド本文・tool_response・prompt 本文が行に存在しない）
  2. どんな入力・環境でも無出力 exit 0（壊れJSON・sid 不正・書けない dir）
  3. 1行 ≤1500 バイト・dir 0700 / file 0600・追記（複数イベントが同じ日付ファイルに並ぶ）
  4. 軽い（1発火 <150ms が受入条件。フレーク回避のためテストは 1.0s で判定し実測を表示）
"""
import ast
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "office-event.sh"
_PY_HEREDOC_RE = re.compile(
    r"^read -r -d '' PY <<'PYEOF'\n(.*?)\nPYEOF$", re.S | re.M)


def embedded_python():
    """hook 内の PY ヒアドキュメント本体（office-event.sh が実際に実行する python）。
    R106: hook の python は `/usr/bin/python3`（3.9）が先に選ばれ得る（.claude/rules/hooks.md）。"""
    m = _PY_HEREDOC_RE.search(HOOK.read_text(encoding="utf-8"))
    assert m, "office-event.sh の PY ヒアドキュメントの形が変わった（抽出できない）"
    return m.group(1)


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
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

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
        """R106（意図して期待を変える・2026-10-03）: R98 の文字形だけの判定は、本人の実データで
        3 日 779 回の test に対し **0 件**しか拾えなかった（`| tail`・`$( )`・複数行・`; echo exit=$?`
        がほぼ全部をはじく保守的な規則だったため）。test の最終判定は「ランナー自身の要約行が実際の
        出力に在るか」だけに移った（hooks/office-event.sh の R106 節）。この回帰は tool_response を
        一切付けずに呼ぶ＝**どの文字形でも、出力が無ければ常に "" になる**ことを確かめる
        （old の文字形だけの kind="test" はもう信用しない＝`if kind == "test": kind = ""` で必ず捨てる）。
        下の TestRunnerOutputEvidence が「出力が在れば拾う」側（新方式が本当に直っていること）を確かめる。
        2 つの嘘を構造的に避けるのは変わらない（別モデルレビュー）:
          ① 名前に触れただけ（cat / echo / grep・引用の中・ヒアドキュメントの本文）は実行ではない
          ② 終了コードがテストのものでない形（`| tail`・`|| true`・`; echo`）は証拠にしない
        （commit/push は文字形のまま＝ここでは変えない。Pre が無いので R106 の HEAD 判定も作動しない）。"""
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
            # 同じ `&&` の鎖に成功した commit があれば commit を記録する（push は日次の件数表にしか使われない）
            ("git commit -am wip && git push", "git:commit"),
            ("bash verify.sh && git push", "git:push"),          # 鎖に commit が無ければ push のまま
            ("git commit -am wip; git push", "git:push"),        # `;` の手前は鎖の外（commit の成否は不明）
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
            # ★実機で踏んだ取りこぼし（2026-09-20・ドッグフーディング）:
            #   このプロジェクトで最も多い形（パスを変数に置いてから走らせる）が 1 件も拾えていなかった
            ('ROOT="/Users/x/develop/20260714 - ai-office"; cd "$ROOT"; bash dev.sh --check', "test"),
            ('ROOT="/no-space"; cd "$ROOT"; bash verify.sh', "test"),
            ('LOG="/tmp/a b.log"; bash verify.sh > "$LOG" 2>&1', "test"),
            ('ROOT="/w"; cd "$ROOT"; git commit -am wip', "git:commit"),
            ('ROOT="/w"; cat verify.sh', ""),
            ('TEST_CMD="npx vitest run"; echo x', ""),
        ]
        for i, (cmd, want) in enumerate(cases):
            run_hook(self.home, {"session_id": f"sess-evt-{i:04d}", "hook_event_name": "PostToolUse", "cwd": "/w",
                                 "tool_name": "Bash", "tool_input": {"command": cmd}})
        recs = [json.loads(ln) for ln in lines(self.home)]
        # R106: "test" を期待していた行は、出力（tool_response）を付けていないのでいまは "" になる。
        # commit/push（old の文字形判定）は不変＝そのまま比較する。
        expected = ["" if want == "test" else want for _, want in cases]
        self.assertEqual([r["kind"] for r in recs], expected)
        joined = "\n".join(lines(self.home))
        for secret in ("secret token", "--grep", "tests/hud_ids", "--release", "node_modules"):
            self.assertNotIn(secret, joined)

    def test_real_commit_and_test_shapes_are_classified(self):
        """R99: 本番で証拠の分類が 2026-09-19 の配備から 0 件だった（events 実測・4 日で commit 0 件）。
        実際に打たれている形を全部落としていた:
          ① 後ろに確認を続ける `git commit … && git log --oneline -1`（最後のセグメントだけ見ていた）
          ② Claude Code 標準のコミット形 `git commit -m "$(cat <<'EOF' … EOF\n)"`（置換＝一律 unsafe）
          ③ `git commit -F - <<'MSG'`（ヒアドキュメント＝一律 unsafe）
          ④ 行継続 `\\`＋改行（改行を文字として持ったトークンになり、次の行の先頭語が読めない）
        `&&` だけで繋がって終了 0 なら、パイプに流していない各段は**走って 0 を返した**＝証拠になる。
        引用した区切りの `cat <<'EOF'` の本文はリテラル＝値が読める。ヒアドキュメントの本文は標準入力で、
        終了コードの持ち主を変えない。どれも「分からないときは何も言わない」は崩さない（後半の否定側）。"""
        msg = "$(cat <<'EOF'\nR99: 秘密の件名 \"引用\" `tick` $(rm -rf /) (カッコ)\n\nCo-Authored-By: x\nEOF\n)"
        cases = [
            # ① 後ろに確認を続ける（終了 0 なら commit は走って成功した）
            ("cd /w && git add -A && git commit -q -m x && git log --oneline -1", "git:commit"),
            ('ROOT="/Users/x/20260714 - ai-office"; cd "$ROOT"; git add -A && git commit -q -m x '
             "&& git log --oneline -1", "git:commit"),
            # R106: test は出力の要約行が無いと言わない（この行は tool_response を付けていない）。
            # old の文字形判定が "test" と言えていたこと自体は後段の secret 漏れ検査で引き続き確認する。
            ("cd /w && bash verify.sh && echo OK", ""),
            ("cd /w && python3 -m unittest -q && git status --short", ""),
            ("bash verify.sh && git commit -am wip | tee log", ""),             # 流した commit は外し、手前の test
            # ② 標準のコミット形（本文は読めるリテラル・置換は cat だけ）
            ('git commit -m "' + msg + '"', "git:commit"),
            ('cd /w && git add -A && git commit -q -m "' + msg + '" && git log --oneline -1', "git:commit"),
            ('J="下準備/a b"; git add -- "$J" && git commit -q -m "' + msg + '" -- "$J" && git log --oneline -1',
             "git:commit"),
            # ③ ヒアドキュメントで渡す（本文は標準入力＝実行ではない）
            ("cd /w && git commit -q -F - -- a.md b.md <<'MSG'\n秘密の件名\n\nbody | tail; rm -rf /\nMSG",
             "git:commit"),
            ("git commit -F - <<-'MSG'\n\t件名\n\tMSG\n", "git:commit"),
            # ④ 行継続
            ("cd /w && git add -A && \\\n  git commit -q -- a.md \\\n  b.md", "git:commit"),
            # ⑤ 末尾の改行は区切りではない（ヒアドキュメントの行は必ず改行で終わる・使い捨てリポジトリの e2e で発見）
            ("python3 - <<PY && git add -A && git commit -q -m x\nprint(1)\nPY", "git:commit"),
            ("bash verify.sh && git commit -q -m x\n", "git:commit"),
            ('ROOT="/w"; cd "$ROOT"; bash verify.sh && git commit -q -m x', "git:commit"),  # 最後の段が勝つ
            # ★ここから下は変えない（分からないときは何も言わない）
            ('git commit -m "' + msg + '" | tail -3', ""),                        # tail の 0
            ('git commit -m "' + msg + '" 2>&1 | tail -3; git log -1', ""),
            ("git commit -F - <<'MSG' 2>&1 | tail -3\nm\nMSG", ""),
            ("pytest -q | tail -3 && echo ok", ""),                               # 流したテストは証拠にしない
            ("git commit -m x; git log -1", ""),                                   # 終了コードは git log のもの
            ("pytest -q && echo ok || echo ng", ""),
            ("exec true && git commit -m x && git log -1", ""),                    # 読めない先行
            ("false && git commit -m x && git log -1", ""),
            ('git commit -m "$(cat <<EOF\n$(pytest)\nEOF\n)"', ""),                # 展開される本文＝値が読めない
            ('git commit -m "$(printf %s x)"', ""),
            ('pytest "$(cat <<\'EOF\'\n--collect-only\nEOF\n)"', ""),              # 読めた値が非実行オプション
            ('git commit "$(cat <<\'EOF\'\n--dry-run\nEOF\n)"', ""),
            ("cat <<'EOF' > notes.md\npytest -q\ngit commit -m x\nEOF", ""),        # 本文は実行ではない
            ("git commit -F - <<'MSG'\nm\nMSG\necho done", ""),                    # 終端の後ろに続く
            ("git commit -F - <<'MSG'\nm", ""),                                    # 終端が無い
            ("pytest <<'IN' && echo ok\ndata\nIN", ""),                          # R106: 出力無し＝言わない
            ("echo a \\\n pytest -q", ""),                                        # 継続しても引数のまま
        ]
        for i, (cmd, want) in enumerate(cases):
            run_hook(self.home, {"session_id": f"sess-real-{i:04d}", "hook_event_name": "PostToolUse", "cwd": "/w",
                                 "tool_name": "Bash", "tool_input": {"command": cmd}})
        got = [json.loads(ln)["kind"] for ln in lines(self.home)]
        self.assertEqual(len(got), len(cases))
        for (cmd, want), kind in zip(cases, got):
            self.assertEqual(kind, want, cmd)
        joined = "\n".join(lines(self.home))
        for secret in ("秘密の件名", "tick", "rm -rf", "Co-Authored", "a.md", "log --oneline"):
            self.assertNotIn(secret, joined)
        # 失敗は `&&` が在ると誰の失敗か分からない（手前の段へ帰属させない）
        run_hook(self.home, {"session_id": "sess-real-fail", "hook_event_name": "PostToolUseFailure", "cwd": "/w",
                             "tool_name": "Bash",
                             "tool_input": {"command": "cd /w && git commit -q -m x && git log --oneline -1"}})
        self.assertEqual(json.loads(lines(self.home)[-1])["kind"], "")

    def test_chain_is_not_evidence_when_exit_code_is_not_zero(self):
        """R99 検収: 手前の段を見る処理は「PostToolUse＝鎖全体が終了 0」を前提にしている。前提が崩れる 2 形を落とす。
          ① サブシェルの括弧でまとめた鎖をパイプに流す形（`( … && … ) | tail`）は tail の 0。
             括弧は捨てて段を平らに並べるので `|` が括弧の外か読めない＝括弧を見たら手前の段を見ない
          ② Claude Code は最後のコマンドが grep/rg/egrep/fgrep/find/diff/test/[・git diff/git grep のとき
             終了 1 を成功扱いにする（PostToolUse・応答に returnCodeInterpretation）。commit が
             nothing to commit で 1 を返しても `… && git diff` なら成功として届く
        あわせて、ヒアドキュメントまわりの門 2 つ（読める置換の閉じ括弧・引用しない区切りの行継続）を押さえる。"""
        cases = [
            # ① サブシェル＋パイプ（検収役の 4 形）
            ("(git commit -q -m x && git log -1) 2>&1 | tail -3", ""),
            ("(bash verify.sh && echo PASS) 2>&1 | tail -5", ""),
            ("(cd /w && python3 -m unittest -q && echo ok) | tail -3", ""),
            ("(pytest -q && echo ok) | cat && echo done", ""),
            # 括弧が在っても最後の段そのものは従来どおり（終了コードは最後の段のもの）
            ("(cd /w && git commit -q -m x)", "git:commit"),
            # ② 最後が終了 1 を成功扱いされるコマンド（検収役の probe の形）
            ("cd /w && git add -A && git commit -q -m x && git log --oneline -1 && git diff", ""),
            ("git commit -q -m x && git log --oneline -1 | grep R99", ""),
            ("bash verify.sh && git status --short | grep -v '^??'", ""),
            ("pytest -q && [ -s report.xml ]", ""),
            ("pytest -q && test -f out.json", ""),
            ("git commit -q -m x && git log -1 && git -C /w diff --stat", ""),
            ("git commit -q -m x && git --no-pager grep -n TODO", ""),
            ("git commit -q -m x && find . -name '*.orig'", ""),
            ("git commit -q -m x && LC_ALL=C rg -q x log", ""),          # 前置きを外した名前でも見る
            ("git commit -q -m x && git log -1 | head -1", "git:commit"),  # head は 1 を成功扱いされない
            # ③ 読める置換は「終端の行の直後が `)` だけ」のときだけ（後ろにコマンドが続くと値が本文と違う）
            ('pytest "$(cat <<\'EOF\'\nEOF\nprintf -- --collect-%s only\n)" && echo ok', ""),
            # ④ 区切りを引用しないヒアドキュメントは本文の `\\`＋改行を繋げてから終端と比べる
            ("cat <<EOF\nx \\\nEOF\ngit commit -m x", ""),
            ("cat <<EOF\nEO\\\nF\nexit 0\nEOF\ngit commit -m x && git log -1", ""),
            ("cat <<'EOF' > a.txt\nx \\\nEOF\ngit commit -m x", "git:commit"),  # 引用した区切りは生の行で比べる
            ("cat <<\\EOF > a.txt\nx \\\nEOF\ngit commit -m x", "git:commit"),
            # ⑤ 別モデルレビュー: コマンド語の無いヒアドキュメントも 1 段（それ自体が終了 0 を返す）
            ("python3 -m unittest missing_module; <<'EOF'\nnote\nEOF", ""),
            ("pytest -q; <<EOF\nnote\nEOF", ""),
            # `&&` なら後ろの段まで来た＝commit は成功している（`;` のときだけが穴）
            ("git commit -m x && <<'EOF'\nnote\nEOF", "git:commit"),
            # ⑥ ANSI-C 引用の終端（`<<$'EOF'` の終端は EOF）は読まない
            ("cat <<$'EOF'\nx\nEOF\nexit 0\n$EOF\npytest", ""),
            ('cat <<$"EOF"\nx\nEOF\npytest', ""),
        ]
        for i, (cmd, want) in enumerate(cases):
            run_hook(self.home, {"session_id": f"sess-rc-{i:04d}", "hook_event_name": "PostToolUse", "cwd": "/w",
                                 "tool_name": "Bash", "tool_input": {"command": cmd}})
        got = [json.loads(ln)["kind"] for ln in lines(self.home)]
        self.assertEqual(len(got), len(cases))
        for (cmd, want), kind in zip(cases, got):
            self.assertEqual(kind, want, cmd)
        # 応答に returnCodeInterpretation が付いていたら終了 1＝どの段も成功の証拠にしない（値は読まない）
        for i, (response, want) in enumerate([
                ({"returnCodeInterpretation": "No matches found"}, ""),
                ({"returnCodeInterpretation": "Files differ", "stdout": ""}, ""),
                ({"stdout": "abc123 R99"}, "git:commit"), ({}, "git:commit"),
                # 値が null のキーは「印が無い」と同じ（将来キーが常に付く形になっても分類が全滅しない）
                ({"returnCodeInterpretation": None}, "git:commit")]):
            run_hook(self.home, {"session_id": f"sess-rci-{i:04d}", "hook_event_name": "PostToolUse", "cwd": "/w",
                                 "tool_name": "Bash", "tool_response": response,
                                 "tool_input": {"command": "git commit -q -m x && git log --oneline -1"}})
            self.assertEqual(json.loads(lines(self.home)[-1])["kind"], want, response)
        self.assertNotIn("No matches", "\n".join(lines(self.home)))

    def test_short_circuit_failure_is_not_attributed(self):
        """R106（意図して期待を変える）: R98 は「短絡（&&）が在る形は終了コードが誰のものか」という
        **帰属**の話だったが、test の最終判定はもう終了コードの帰属を見ない（R106 節）。だから
        `cd /w && pytest -q` のように old 判定なら帰属できた形も、要約行の無い実行は常に "" になる＝
        このテストは「帰属が読めてもなお、出力が無ければ言わない」ことを確かめる回帰に変わる。
        TestRunnerOutputEvidence.test_background_run_in_background_excludes_even_with_output が
        「出力が在っても run_in_background は除く」側（このテストが元々持っていた安全側の芯）を引き継ぐ。"""
        for i, (ev, cmd, want) in enumerate([
            ("PostToolUse", "false && pytest -q", ""),
            ("PostToolUse", "cd /w && pytest -q", ""),             # R106: 出力無し＝帰属に関わらず言わない
            ("PostToolUseFailure", "false && pytest -q", ""),
            ("PostToolUseFailure", "pytest -q", ""),
            ("PostToolUseFailure", "cd /w; pytest -q", ""),
            ("PostToolUseFailure", "cd /nonexistent && pytest", ""),
        ]):
            run_hook(self.home, {"session_id": f"sess-sc-{i:04d}", "hook_event_name": ev, "cwd": "/w",
                                 "tool_name": "Bash", "tool_input": {"command": cmd}})
        self.assertEqual([json.loads(ln)["kind"] for ln in lines(self.home)],
                         ["", "", "", "", "", ""])

    def test_background_by_response_is_not_evidence(self):
        """R98: Bash が途中でバックグラウンドへ移ると入力に印が無く、**応答に id** が返る。
        まだ終わっていない＝証拠にしない（別モデルレビュー）。R106（意図して期待を変える）: 後半 2 行は
        以前は文字形だけで "test" と言っていたが、`"ok"`／空は verify.sh 自身の要約行ではない＝新方式では
        言わない。実際に要約が読めると拾うことは test_background_bash_is_not_evidence で確かめる。"""
        for i, (response, want) in enumerate([
            ({"backgroundTaskId": "bg1"}, ""), ({"taskId": "t1"}, ""),
            ({"stdout": "ok"}, ""), ({}, ""),
        ]):
            run_hook(self.home, {"session_id": f"sess-bgr-{i:04d}", "hook_event_name": "PostToolUse",
                                 "cwd": "/w", "tool_name": "Bash",
                                 "tool_input": {"command": "bash verify.sh"}, "tool_response": response})
        self.assertEqual([json.loads(ln)["kind"] for ln in lines(self.home)], ["", "", "", ""])

    def test_background_bash_is_not_evidence(self):
        """R98/R106: `run_in_background` は**起動しただけ**で PostToolUse が返る＝まだ終わっていない
        （結果は後から別の形で届く・この PostToolUse には無い）。R106 でも明示的に除く対象（test の
        要約・commit の HEAD 判定どちらも）。**同じ、本物の verify.sh 成功要約を全行に付けて**、
        run_in_background が「要約がたまたま読めても」なお除かれることを確かめる（意図して期待を変える：
        非 background の 2 行は old は文字形だけで "test" と言えていたが、R106 は実際の要約が無ければ
        言わない＝ここでは本物の要約を付けて初めて拾えることも同時に示す）。"""
        real_pass = "…\n✅ verify: 全チェック合格\n"
        for i, (payload, want) in enumerate([
            ({"command": "bash verify.sh", "run_in_background": True}, ""),
            ({"command": "git commit -am wip", "run_in_background": True}, ""),
            ({"command": "bash verify.sh", "run_in_background": False}, "test"),
            ({"command": "bash verify.sh"}, "test"),
        ]):
            run_hook(self.home, {"session_id": f"sess-bg-{i:04d}", "hook_event_name": "PostToolUse",
                                 "cwd": "/w", "tool_name": "Bash", "tool_input": payload,
                                 "tool_response": {"stdout": real_pass}})
        recs = [json.loads(ln) for ln in lines(self.home)]
        self.assertEqual([r["kind"] for r in recs], ["", "", "test", "test"])
        # background の 2 行は要約が実際に読めていても ok 欄にすら現れない（kind="" のまま）
        self.assertTrue(recs[2]["ok"] and recs[3]["ok"])

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

    def test_subagent_tool_events_are_marked_without_the_id(self):
        """R102-4: サブエージェントの中の道具（入力に agent_id）は ag=1（ID そのものは載せない）。親のターンの判定
        （office_events.turn_live）に混ぜない＝裏のサブエージェントで閉じたターンを「作業中」へ開き直さない。"""
        run_hook(self.home, {"session_id": "sess-evt-0009", "hook_event_name": "PreToolUse", "cwd": "/w",
                             "tool_name": "Read", "tool_input": {"file_path": "/w/a.md"}, "agent_id": "agent-secret-id"})
        run_hook(self.home, {"session_id": "sess-evt-0009", "hook_event_name": "PreToolUse", "cwd": "/w",
                             "tool_name": "Read", "tool_input": {"file_path": "/w/a.md"}})
        run_hook(self.home, {"session_id": "sess-evt-0009", "hook_event_name": "SubagentStop", "cwd": "/w",
                             "agent_id": "a-2"})
        ls = [json.loads(l) for l in lines(self.home)]
        self.assertEqual([x["ag"] for x in ls], [1, 0, 0])
        self.assertNotIn("agent-secret-id", "".join(lines(self.home)))
        self.assertEqual(ls[2]["sub"], "a-2")

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

    def test_embedded_python_parses_as_python39(self):
        """R106 で足した commit の HEAD 判定・test の要約判定を含め、hook の python は
        `/usr/bin/python3`（3.9）でも import エラーにならない構文であること。"""
        ast.parse(embedded_python(), feature_version=(3, 9))


class RunnerOutputEvidenceTest(unittest.TestCase):
    """R106（2026-10-03・本人の実データ）: test は「ランナーが現れた」＋「ランナー自身の要約行が
    実際の出力に在る」の両方で決める（exit コードの帰属は見ない）。ここは `| tail`・`$( )`・複数行・
    `> log 2>&1; echo "exit=$?"` のような、本人が実際に打っていて old の判定が全滅していた形が、
    出力さえ本物なら拾えることを確かめる（直した側の回帰）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_event_out_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def _post(self, cmd, stdout="", stderr="", sid=None, ev="PostToolUse", extra=None):
        payload = {"session_id": sid or f"sess-out-{abs(hash(cmd)) % 100000:05d}",
                   "hook_event_name": ev, "cwd": "/w", "tool_name": "Bash",
                   "tool_input": {"command": cmd}, "tool_response": {"stdout": stdout, "stderr": stderr}}
        if extra:
            payload["tool_input"].update(extra)
        run_hook(self.home, payload)
        return json.loads(lines(self.home)[-1])

    def test_motivating_forms_now_count_with_real_output(self):
        """本人の実データの内訳（3 日分）そのもの: heredoc+pipe+redirect+複数行／pipe+redirect／
        bg+; echo／$()+heredoc／pipe。old はこれらを**文字形だけ**で全部はじいていた。R106 は
        文字形の緩い判定（ランナーがどこかに現れたか）＋本物の要約行で判定するので、同じ文字形でも
        本物の出力を付ければ拾える。"""
        pass_tail = "collecting...\n5 passed in 0.31s\n"
        cases = [
            ('pytest -q > /tmp/log 2>&1; echo "exit=$?"', pass_tail),   # bg+; echo（78 件）
            ("pytest -q | tail -3", pass_tail),                          # pipe（66 件）
            ("bash verify.sh 2>&1 | tail -20", "…\n✅ verify: 全チェック合格\n"),  # pipe+redirect（135件）
            ("ROOT=/w; cd \"$ROOT\"; python3 -m pytest tests -q\n"       # heredoc+pipe+redirect+複数行（153件）
             "echo done", pass_tail),
            ('ROOT="/w"; cd "$ROOT" && node --test tests/a.mjs 2>&1 | tee /tmp/log',
             "ℹ pass 3\nℹ fail 0\n"),                                    # 複数行＋代入＋pipe+redirect
        ]
        for cmd, out in cases:
            rec = self._post(cmd, stdout=out)
            self.assertEqual(rec["kind"], "test", cmd)
            self.assertTrue(rec["ok"], cmd)
            self.assertNotIn(out.strip().split("\n")[0], json.dumps(rec))  # 出力本文は書かない

    def test_no_summary_line_says_nothing(self):
        """`cat log` や `tail log` のようにランナーが**走っていない**コマンドの出力は数えない。
        ランナーの文字形はあっても要約行が無いとき（まだ出力が揃っていない等）も言わない。"""
        self.assertEqual(self._post("cat /tmp/pytest.log", stdout="5 passed in 0.1s\n")["kind"], "")
        self.assertEqual(self._post("tail -5 /tmp/pytest.log", stdout="5 passed in 0.1s\n")["kind"], "")
        self.assertEqual(self._post("pytest -q", stdout="collecting tests...\n")["kind"], "")

    def test_fail_marker_always_wins(self):
        rec = self._post("pytest -q", stdout="2 failed, 3 passed in 0.4s\n")
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))

    def test_fail_marker_is_not_hidden_by_a_later_pass_outside_an_8kb_window(self):
        """別モデルレビュー指摘（2026-10-04・CONFIRMED）: 1 回の Bash 呼び出しで同じランナーを 2 回
        打つ形（`pytest a; pytest b`。ユニット→統合の順で続けて打つのは普通にある）で、手前の fail
        要約を大きな出力で押し出すと、以前は末尾 8KB しか見ていなかったので後ろの pass だけが見えて
        ok=true になっていた（実機で再現）。全文を見るので手前の fail が勝つ。"""
        padding = "x" * 29000  # 旧実装の 8KB 窓を確実に超える
        stdout = "1 failed, 40 passed in 2.10s\n" + padding + "\n10 passed in 0.50s\n"
        rec = self._post('pytest tests/test_unit.py -v; pytest tests/test_integration.py -q', stdout=stdout)
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))
        self.assertNotIn("1 failed", json.dumps(rec))  # 出力本文は書かない

    def test_unittest_markers(self):
        self.assertEqual(self._post("python3 -m unittest", stdout="...\nOK\n")["ok"], True)
        rec = self._post("python3 -m unittest", stdout="...\nFAILED (failures=1)\n")
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))

    def test_node_test_markers(self):
        pass_out = "ℹ pass 4\nℹ fail 0\n"
        fail_out = "ℹ pass 2\nℹ fail 1\n"
        self.assertEqual(self._post("node --test tests/a.mjs", stdout=pass_out)["ok"], True)
        rec = self._post("node --test tests/a.mjs", stdout=fail_out)
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))

    def test_jest_vitest_markers(self):
        self.assertEqual(self._post("npx vitest run", stdout="Tests  3 passed, 3 total\n")["ok"], True)
        rec = self._post("npx jest", stdout="Tests:       1 failed, 2 passed, 3 total\n")
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))
        self.assertEqual(self._post("npx vitest run",
                         stdout="Test Files  2 passed (2)\n")["ok"], True)

    def test_verify_and_devcheck_markers(self):
        self.assertEqual(self._post("bash verify.sh", stdout="…\n✅ verify: 全チェック合格\n")["ok"], True)
        rec = self._post("bash verify.sh", stdout="…\n❌ verify: 3件失敗\n")
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))
        # worktree では UI 省略で ⚠（合格の一種）になる＝これも pass として読む
        self.assertEqual(self._post("bash verify.sh",
                         stdout="⚠ verify: 合格（ただし UI スモークは省略）\n")["ok"], True)
        self.assertEqual(self._post("bash dev.sh --check", stdout="✅ 高速レーン green\n")["ok"], True)
        rec = self._post("bash dev.sh --check", stdout="✗ x\n❌ 2 件\n")
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))

    def test_run_in_background_excludes_even_with_matching_output(self):
        """run_in_background は結果が**この** PostToolUse に無い（別経路で後から届く）＝
        要約がたまたま一致していても証拠にしない。"""
        rec = self._post("pytest -q", stdout="5 passed in 0.1s\n",
                         extra={"run_in_background": True})
        self.assertEqual(rec["kind"], "")

    def test_post_tool_use_failure_with_real_fail_output_is_still_test(self):
        """PostToolUseFailure（Claude Code 側は失敗と見ている）でも、要約行が fail なら同じ test/ok=False
        として記録する（台帳の「失敗」表示の元データ）。"""
        rec = self._post("bash verify.sh", stdout="…\n❌ verify: 1件失敗\n", ev="PostToolUseFailure")
        self.assertEqual((rec["kind"], rec["ok"]), ("test", False))


class ReviewFindingsEvidenceTest(unittest.TestCase):
    """2026-10-04 の別モデルレビュー（GPT-6 Astra・medium 8）の再現を固定する: 失敗を成功と記録しない・
    実際の書式を取りこぼさない・ランナー以外の出力を帰属させない・長いコマンドで遅くならない。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_event_review_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def _post(self, cmd, stdout="", stderr=""):
        run_hook(self.home, {"session_id": f"sess-rv-{abs(hash(cmd)) % 100000:05d}", "hook_event_name": "PostToolUse",
                             "cwd": "/w", "tool_name": "Bash", "tool_input": {"command": cmd},
                             "tool_response": {"stdout": stdout, "stderr": stderr}})
        rec = json.loads(lines(self.home)[-1])
        return rec["kind"], rec["ok"]

    def test_node_later_fail_is_not_hidden_by_the_first_zero(self):
        out = "ℹ tests 1\nℹ pass 1\nℹ fail 0\n" + "ℹ tests 1\nℹ pass 0\nℹ fail 1\n"
        self.assertEqual(self._post("node --test a.mjs; node --test b.mjs; true", out), ("test", False))

    def test_node_cancelled_is_not_success(self):
        out = "ℹ tests 2\nℹ pass 1\nℹ fail 0\nℹ cancelled 1\n"
        self.assertEqual(self._post("node --test slow.mjs", out), ("test", False))

    def test_node_tap_summary_is_read(self):
        self.assertEqual(self._post("node --test --test-reporter=tap a.mjs", "TAP version 13\n# tests 1\n# pass 1\n# fail 0\n# cancelled 0\n"),
                         ("test", True))
        self.assertEqual(self._post("node --test --test-reporter=tap b.mjs", "# tests 1\n# pass 0\n# fail 1\n"),
                         ("test", False))

    def test_jest_suite_failure_is_a_failure(self):
        out = "Test Suites: 1 failed, 1 passed, 2 total\nTests:       1 passed, 1 total\n"
        self.assertEqual(self._post("npx jest || true", out), ("test", False))

    def test_vitest_real_summary_format(self):
        ok = " Test Files  1 passed (1)\n      Tests  3 passed (3)\n   Start at  10:00:00\n"
        ng = " Test Files  1 failed | 1 passed (2)\n      Tests  1 failed | 3 passed (4)\n"
        self.assertEqual(self._post("npx vitest run", ok), ("test", True))
        self.assertEqual(self._post("npx vitest run --reporter=default", ng), ("test", False))

    def test_output_from_something_else_is_not_attributed_to_the_runner(self):
        self.assertEqual(self._post("printf '5 passed in 0.1s\\n'; false && pytest -q; true", "5 passed in 0.1s\n"), ("", True))
        self.assertEqual(self._post("cat old.log; pytest -q", "== 3 passed in 1s ==\n"), ("", True))
        self.assertEqual(self._post("tail -5 last_run.log && python3 -m unittest", "OK\n"), ("", True))
        # ランナーの出力をパイプで受ける段は出力源ではない（今まで通り数える）
        self.assertEqual(self._post("pytest -q 2>&1 | tail -3", "5 passed in 0.3s\n"), ("test", True))
        self.assertEqual(self._post("echo start; python3 -m unittest 2>&1 | tail -1", "start\nOK\n"), ("test", True))

    def test_independent_output_downstream_of_the_runner_is_not_attributed(self):
        """2 巡目のレビュー: パイプの下流でも、ファイルを読む段や要約を打つ printf はランナーの出力ではない。"""
        self.assertEqual(self._post("node --test missing.mjs | printf '# pass 1\\n# fail 0\\n'", "# pass 1\n# fail 0\n"), ("", True))
        self.assertEqual(self._post("pytest -q | cat old.log", "5 passed in 1s\n"), ("", True))
        self.assertEqual(self._post("pytest -q 2>&1 | grep -E 'passed|failed' last.log", "5 passed in 1s\n"), ("", True))
        # 標準入力を流すだけの段は今まで通り（tail -n 3・grep パターン）
        self.assertEqual(self._post("pytest -q 2>&1 | tail -n 3", "5 passed in 0.3s\n"), ("test", True))
        self.assertEqual(self._post("pytest -q 2>&1 | grep -E 'passed|failed'", "5 passed in 0.3s\n"), ("test", True))

    def test_colored_summaries_are_read(self):
        """2 巡目のレビュー: FORCE_COLOR の色の制御列が付いた失敗の要約を見落として、後ろの色なしの成功で合格になった。"""
        esc = "\x1b"
        out = f"{esc}[31mℹ fail 1{esc}[39m\n{esc}[32mℹ pass 0{esc}[39m\n" + "ℹ tests 1\nℹ pass 1\nℹ fail 0\n"
        self.assertEqual(self._post("FORCE_COLOR=1 node --test a.mjs; node --test b.mjs", out), ("test", False))

    def test_many_blank_lines_stay_fast(self):
        """2 巡目のレビュー: 改行をまたぐ ^\\s* は空行の多い出力で二乗（10 万行で 10 秒超＝記録ごと消える）。"""
        (kind, ok), sec = self._timed("node --test a.mjs", "\n" * 200_000 + "ℹ tests 1\nℹ pass 1\nℹ fail 0\n")
        self.assertEqual((kind, ok), ("test", True))
        self.assertLess(sec, 2.0)

    def _timed(self, cmd, stdout=""):
        t0 = time.time()
        res = self._post(cmd, stdout)
        return res, time.time() - t0

    def test_huge_or_many_segment_commands_are_not_classified_and_stay_fast(self):
        """赤チーム（high）: 割り方は 1MB で 2.7 秒・10MB で 9.7 秒（hook の timeout 10 秒を超えて記録ごと消える）、
        `&&` や `;` の段が数千あると二乗。大きすぎる・段が多すぎる形は分類しない（何も言わない）・速いまま。"""
        cases = [
            ("git commit -q -m " + "'" + "x" * 1_000_000 + "'", ""),               # 巨大な引用 1 個
            ("true && " * 5000 + "pytest -q", "1 passed in 0.1s\n"),             # && の鎖
            ("A=1; " * 5000 + "pytest -q", "1 passed in 0.1s\n"),                # 代入の段
        ]
        for cmd, out in cases:
            (kind, ok), sec = self._timed(cmd, out)
            self.assertEqual(kind, "", cmd[:40])
            self.assertLess(sec, 2.0, f"遅い: {cmd[:30]}… {sec:.2f}s")

    def test_huge_output_is_not_scanned(self):
        """赤チーム（medium）: 数十 MB の出力は走査しない（時間が読めない）＝何も言わない。"""
        (kind, ok), sec = self._timed("pytest -q", "x" * 9_000_000 + "\n1 passed in 1s\n")
        self.assertEqual(kind, "")
        self.assertLess(sec, 3.0)

    def test_long_unrelated_command_stays_fast(self):
        """先行の代入を毎回先頭から集め直す二乗の処理は、2,000 段で 2 秒かかった（別モデルレビューの実測）。
        段の上限（_R106_MAX_SEGS＝300）の内側の長いコマンドは今まで通り分類して、速いまま（上限の外は下の検査）"""
        cmd = "A=1; echo x; " * 140 + "pytest -q"
        t0 = time.time()
        kind, ok = self._post(cmd, "1 passed in 0.1s\n")
        self.assertEqual((kind, ok), ("test", True))
        self.assertLess(time.time() - t0, 1.5, "長いコマンドで hook が遅い（二乗の戻り）")


class ThirdReviewEvidenceTest(unittest.TestCase):
    """3 巡目の別モデルレビュー（649ab22・GPT-6 Astra・medium 2）の再現を固定する。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_event_review3_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def _post(self, cmd, stdout="", stderr=""):
        return ReviewFindingsEvidenceTest._post(self, cmd, stdout, stderr)

    def test_colored_or_composed_printed_summary_is_not_attributed(self):
        """printf の引数を書かれたままの文字で見ていた＝色の制御列の後ろの OK が素通りし、出力だけ色を落として
        unittest の要約と読んだ（テストは走っていないのに合格）。端末に出る形で判定する。"""
        esc = "\x1b"
        self.assertEqual(self._post("printf '\\033[32mOK\\033[0m\\n'; false && python3 -m unittest; true",
                                    f"{esc}[32mOK{esc}[0m\n"), ("", True))
        self.assertEqual(self._post("echo -e '\\e[32mOK\\e[0m'; false && python3 -m unittest; true",
                                    f"{esc}[32mOK{esc}[0m\n"), ("", True))
        self.assertEqual(self._post("printf 'x\\nOK\\n'; false && python3 -m unittest; true", "x\nOK\n"), ("", True))
        self.assertEqual(self._post("printf '%s\\n' OK; false && python3 -m unittest; true", "OK\n"), ("", True))
        # 要約に見えない echo は今まで通り（ランナーの要約を数える）
        self.assertEqual(self._post("echo '=== unit ==='; python3 -m unittest", "=== unit ===\nOK\n"), ("test", True))

    def test_filter_option_values_are_not_counted_as_files(self):
        """オプションの値（パターン・キー・変数）をファイルと数えて、標準入力だけを流す正しい要約を捨てていた。"""
        tap = "# pass 3\n# fail 0\n"
        self.assertEqual(self._post("node --test --test-reporter=tap a.mjs | grep -e '# pass' -e '# fail'", tap),
                         ("test", True))
        self.assertEqual(self._post("pytest -q 2>&1 | sed -e 's/x/y/' -e 's/a/b/'", "5 passed in 0.3s\n"), ("test", True))
        self.assertEqual(self._post("pytest -q 2>&1 | awk -v n=1 '{print}'", "5 passed in 0.3s\n"), ("test", True))
        self.assertEqual(self._post("pytest -q 2>&1 | sort -k 1,1 | cut -d ' ' -f 1-3", "5 passed in\n"), ("test", True))
        # -e でパターンを渡した後の位置引数はファイル＝ランナーの出力ではない（今まで通り帰属させない）
        self.assertEqual(self._post("node --test a.mjs | grep -e '# pass' old.log", tap), ("", True))
        self.assertEqual(self._post("pytest -q | awk -f prog.awk last.log", "5 passed in 1s\n"), ("", True))

    def test_fifth_review_files_are_not_hidden_behind_option_parsing(self):
        """5 巡目のレビュー（R106-4 への指摘）: プログラムを代入と取り違える・Mac の sed -l を値つきと読む・
        jq --rawfile の読むファイルを数えない＝古いログの OK を失敗したランナーの合格にした。"""
        bad = "python3 -m unittest missing 2>/dev/null | "
        self.assertEqual(self._post(bad + "awk 'NF=NF' old.log", "OK\n"), ("", True))
        self.assertEqual(self._post(bad + "sed -l 's/x/y/' old.log", "OK\n"), ("", True))
        self.assertEqual(self._post(bad + "jq -nr --rawfile log old.log '$log'", "OK\n"), ("", True))

    def test_fifth_review_summary_check_is_linear_and_word_aware(self):
        """長い数字の列で二乗（3 万桁で 11 秒＝hook の timeout）・`WEBHOOK` の OK を要約と見て正しい記録を捨てた。"""
        self.assertEqual(self._post("echo '=== WEBHOOK ==='; python3 -m unittest test_ok", "=== WEBHOOK ===\nOK\n"),
                         ("test", True))
        digits = "7" * 30000
        t0 = time.time()
        self.assertEqual(self._post(f"echo {digits}; python3 -m unittest", f"{digits}\nOK\n"), ("test", True))
        self.assertLess(time.time() - t0, 2.0, "長い数字の列で要約の判定が二乗に戻った")

    def test_sixth_review_printf_formats_are_rendered(self):
        """6 巡目のレビュー: 書式の指定（%s・%b）は出力に残らないのに、その英字の直後の OK を見落とした。"""
        esc = "\x1b"
        tail = "; false && python3 -m unittest; true"
        self.assertEqual(self._post("printf '%sOK\\n' ''" + tail, "OK\n"), ("", True))
        self.assertEqual(self._post("printf '%bOK%b\\n' '\\033[32m' '\\033[0m'" + tail, f"{esc}[32mOK{esc}[0m\n"), ("", True))
        self.assertEqual(self._post("printf '%s%s\\n' O K" + tail, "OK\n"), ("", True))
        self.assertEqual(self._post("printf '%d passed\\n' 5; false && pytest -q; true", "5 passed\n"), ("", True))
        # 要約に見えない printf は今まで通り
        self.assertEqual(self._post("printf '%s\\n' '=== WEBHOOK ==='; python3 -m unittest", "=== WEBHOOK ===\nOK\n"),
                         ("test", True))

    def test_seventh_review_printf_format_scan_is_linear(self):
        """7 巡目のレビュー: 書式の正規表現のフラグと幅がどちらも 0 を取れて二乗（`%` と 0 が 3 万個で 11 秒＝記録ごと消える）。"""
        fmt = "%" + "0" * 30000 + "!"
        t0 = time.time()
        self.assertEqual(self._post(f"printf '{fmt}\\n'; python3 -m unittest", fmt + "\nOK\n"), ("test", True))
        self.assertLess(time.time() - t0, 2.0, "printf の書式の判定が二乗に戻った")

    def test_eighth_review_printf_precision_is_honored(self):
        """8 巡目のレビュー: `%.s` は引数を出さないのに OK と組み立てて、正しいテストの記録を捨てた。精度で切った形が要約なら今まで通り外す。"""
        self.assertEqual(self._post("printf 'O%.s\\n' K; python3 -m unittest", "O\nOK\n"), ("test", True))
        self.assertEqual(self._post("printf '%.0s-' 1 2 3; python3 -m unittest", "---OK\nOK\n"), ("test", True))
        self.assertEqual(self._post("printf '%.2s\\n' OKAY; false && python3 -m unittest; true", "OK\n"), ("", True))

    def test_ninth_review_precision_edge_cases(self):
        """9 巡目のレビュー: 負の動的精度は「精度なし」・%b は逃がし文字を解いてから切る・桁が多すぎる精度で二乗（8.5 秒）。"""
        self.assertEqual(self._post("printf 'Test %.*sFiles\\n' -1 'Input '; python3 -m unittest", "Test Input Files\nOK\n"),
                         ("test", True))
        self.assertEqual(self._post("printf '%.4b\\n' '\\tOKAY'; python3 -m unittest", "\tOKA\nOK\n"), ("test", True))
        cmd = "printf '%." + "9" * 199000 + "s' " + "x " * 20 + "; python3 -m unittest"
        t0 = time.time()
        self.assertEqual(self._post(cmd, "xOK\nOK\n"), ("test", True))
        self.assertLess(time.time() - t0, 2.0, "桁の多い精度で int() が二乗に戻った")


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.test",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.test"})


def _make_repo(root):
    repo = root / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    _git(["add", "a.txt"], repo)
    # 準備の commit は 1 時間前に置く（reflog の時刻も GIT_COMMITTER_DATE に従う）。追い越しの救済（控えの直前 1.5 秒の
    # commit を拾う）が、テストの準備の commit を「この呼び出しの commit」と取り違えないように
    old_env = os.environ.get("GIT_COMMITTER_DATE")
    os.environ["GIT_COMMITTER_DATE"] = f"@{int(time.time()) - 3600} +0900"
    try:
        assert _git(["commit", "-q", "-m", "init"], repo).returncode == 0
    finally:
        if old_env is None:
            os.environ.pop("GIT_COMMITTER_DATE", None)
        else:
            os.environ["GIT_COMMITTER_DATE"] = old_env
    return repo


class CommitGroundTruthTest(unittest.TestCase):
    """R106: commit はコマンドの文字形を離れ、**このツール呼び出しの前後で HEAD が実際に動いたか**
    だけで決める（PreToolUse で控え、PostToolUse で比べる）。dir は payload の cwd と、先頭の
    `cd <dir>`・`git -C <dir>` だけを見る（.claude/rules/hooks.md・plan.md E）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_event_commit_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.root = Path(tempfile.mkdtemp(prefix="office_event_repo_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = _make_repo(self.root)

    def _pre(self, cmd, cwd, sid="sess-gt", tool_use_id="tu-1"):
        run_hook(self.home, {"session_id": sid, "hook_event_name": "PreToolUse", "cwd": cwd,
                             "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_use_id": tool_use_id})

    def _post(self, cmd, cwd, sid="sess-gt", tool_use_id="tu-1", ev="PostToolUse"):
        run_hook(self.home, {"session_id": sid, "hook_event_name": ev, "cwd": cwd,
                             "tool_name": "Bash", "tool_input": {"command": cmd}, "tool_use_id": tool_use_id,
                             "tool_response": {"stdout": ""}})
        return json.loads(lines(self.home)[-1])

    def test_head_change_confirms_commit_even_when_the_text_form_defeats_the_old_rule(self):
        """old は `git commit … > log 2>&1; echo "exit=$?"` のような形を一切拾えなかった
        （$? を見る echo が最後のセグメント＝終了コードの帰属が echo のものになる）。R106 は
        HEAD が本当に動いたかしか見ないので、この文字形でも拾える＝直した側の核心。"""
        cmd = f'cd {self.repo} && git commit --allow-empty -q -m x > /tmp/r106.log 2>&1; echo "exit=$?"'
        t0 = time.time()
        self._pre(cmd, str(self.repo))
        _git(["commit", "--allow-empty", "-q", "-m", "x"], self.repo)      # 実際にコミットする（本物の HEAD 移動）
        rec = self._post(cmd, str(self.repo))
        self.assertLess(time.time() - t0, 3.0, "R106 の git 往復が重い")
        self.assertEqual((rec["kind"], rec["ok"]), ("git:commit", True))

    def test_pre_absent_falls_back_to_old_rule(self):
        """PreToolUse を一度も呼ばなかった（＝控えが無い）ときは、旧来の文字形判定に任せる。"""
        rec = self._post("git commit -am wip", str(self.repo), sid="sess-gt-nopre", tool_use_id="tu-nopre")
        self.assertEqual(rec["kind"], "git:commit")      # old の文字形判定がそのまま効く

    def test_head_unchanged_does_not_confirm(self):
        """Pre は取れたが、実際には何もコミットしなかった（HEAD が同じ）→ override しない。
        このコマンド自体は old 判定でも commit と言えない形（--dry-run）なので、結果は "" のまま。"""
        cmd = f"cd {self.repo} && git commit --dry-run -q -m x"
        self._pre(cmd, str(self.repo), sid="sess-gt-dry", tool_use_id="tu-dry")
        rec = self._post(cmd, str(self.repo), sid="sess-gt-dry", tool_use_id="tu-dry")
        self.assertEqual(rec["kind"], "")

    def test_stale_pre_is_ignored(self):
        """控えが古すぎる（30 分超）ときは使わない＝旧来の判定に任せる。"""
        cmd = f"cd {self.repo} && git commit --allow-empty -q -m x"
        self._pre(cmd, str(self.repo), sid="sess-gt-stale", tool_use_id="tu-stale")
        pre_path = next((self.home / ".claude" / "office_events" / "commit_pre").glob("*.json"))
        stale = json.loads(pre_path.read_text(encoding="utf-8"))
        stale["t"] -= 1900
        pre_path.write_text(json.dumps(stale), encoding="utf-8")
        _git(["commit", "--allow-empty", "-q", "-m", "x"], self.repo)
        rec = self._post(cmd, str(self.repo), sid="sess-gt-stale", tool_use_id="tu-stale")
        self.assertEqual(rec["kind"], "git:commit")      # 控えは無視されたが、old 判定がこの形を拾う
        self.assertFalse(pre_path.exists())               # 古い控えも消費済み（使い捨て）

    def test_concurrent_tool_use_ids_do_not_cross_talk(self):
        """同じセッションでも tool_use_id が違えば別の控え＝並行しても取り違えない。"""
        cmd_a = f"cd {self.repo} && git commit --allow-empty -q -m a"
        self._pre(cmd_a, str(self.repo), sid="sess-gt-par", tool_use_id="tu-a")
        _git(["commit", "--allow-empty", "-q", "-m", "a"], self.repo)
        rec_a = self._post(cmd_a, str(self.repo), sid="sess-gt-par", tool_use_id="tu-a")
        self.assertEqual(rec_a["kind"], "git:commit")
        # 控えは既に消費済み＝同じ tool_use_id をもう一度 Post しても二重には効かない
        rec_a2 = self._post("echo noop", str(self.repo), sid="sess-gt-par", tool_use_id="tu-a")
        self.assertEqual(rec_a2["kind"], "")

    def test_dir_from_leading_cd_and_git_dash_c(self):
        """dir は payload の cwd だけでなく、先頭の `cd <dir>`／`git -C <dir>` も読む。"""
        outside = self.root / "elsewhere"
        outside.mkdir()
        cmd_cd = f"cd {self.repo} && git commit --allow-empty -q -m x"
        self._pre(cmd_cd, str(outside), sid="sess-gt-cd", tool_use_id="tu-cd")
        _git(["commit", "--allow-empty", "-q", "-m", "x"], self.repo)
        rec = self._post(cmd_cd, str(outside), sid="sess-gt-cd", tool_use_id="tu-cd")
        self.assertEqual((rec["kind"], rec["ok"]), ("git:commit", True))

        cmd_c = f"git -C {self.repo} commit --allow-empty -q -m y"
        self._pre(cmd_c, str(outside), sid="sess-gt-c", tool_use_id="tu-c")
        _git(["commit", "--allow-empty", "-q", "-m", "y"], self.repo)
        rec2 = self._post(cmd_c, str(outside), sid="sess-gt-c", tool_use_id="tu-c")
        self.assertEqual((rec2["kind"], rec2["ok"]), ("git:commit", True))

    def test_commit_that_finished_before_the_async_pre_is_not_claimed(self):
        """hook は非同期＝ごく短い commit では控えの前に HEAD が動き終わる。この追い越しは**数えない**（見落とすだけ）。
        reflog の時刻で救う版は、別の呼び出しの commit・--dry-run・作業木の違い・並行の二重計上で「成功」と嘘をついた
        （2026-10-04 の赤チームと 2 巡目の別モデルレビュー）＝分からないときは何も言わない。古い文字の規則でも拾えない形で見る。"""
        cmd = f'cd {self.repo} && git commit --allow-empty -q -m fast > /dev/null 2>&1; echo "exit=$?"'
        _git(["commit", "--allow-empty", "-q", "-m", "fast"], self.repo)     # 先に終わる
        self._pre(cmd, str(self.repo), sid="sess-gt-race", tool_use_id="tu-race")
        rec = self._post(cmd, str(self.repo), sid="sess-gt-race", tool_use_id="tu-race")
        self.assertEqual(rec["kind"], "")

    def test_dry_run_right_after_a_commit_is_not_claimed(self):
        """2 巡目のレビュー: 直前 1.5 秒の未記帳の commit があると `git commit --dry-run` まで成功になった（救済の撤去で解消）。"""
        _git(["commit", "--allow-empty", "-q", "-m", "just-before"], self.repo)
        cmd = f"cd {self.repo} && git commit --dry-run -q -m never; true"
        self._pre(cmd, str(self.repo), sid="sess-gt-dry2", tool_use_id="tu-dry2")
        rec = self._post(cmd, str(self.repo), sid="sess-gt-dry2", tool_use_id="tu-dry2")
        self.assertNotEqual((rec["kind"], rec["ok"]), ("git:commit", True))

    def test_an_older_commit_is_not_taken_for_this_call(self):
        """控えより 1.5 秒以上前の commit（＝前の呼び出し・準備の commit）は、HEAD が不変なら数えない。"""
        cmd = f"cd {self.repo} && git commit -q -m nothing-to-commit"
        self._pre(cmd, str(self.repo), sid="sess-gt-old", tool_use_id="tu-old")
        rec = self._post(cmd, str(self.repo), sid="sess-gt-old", tool_use_id="tu-old", ev="PostToolUseFailure")
        self.assertNotEqual(rec["kind"], "git:commit")

    def test_a_previous_calls_commit_is_not_credited_to_a_failing_call(self):
        """赤チーム（high）: 呼び出し A が本当に commit した 0.7 秒後に、呼び出し B が何も commit せずに失敗しても、
        B を「commit 成功」にしない（追い越しの救済は、まだ誰にも付けていない commit で、成功で終わった呼び出しだけ）。"""
        (self.repo / "a.txt").write_text("2\n", encoding="utf-8")
        cmd_a = f"cd {self.repo} && git commit -qam real"
        self._pre(cmd_a, str(self.repo), sid="sess-gt-ab", tool_use_id="tu-A")
        _git(["commit", "-qam", "real"], self.repo)
        rec_a = self._post(cmd_a, str(self.repo), sid="sess-gt-ab", tool_use_id="tu-A")
        self.assertEqual((rec_a["kind"], rec_a["ok"]), ("git:commit", True))
        for ev, cmd_b in (("PostToolUseFailure", f"cd {self.repo} && git commit -q -m nothing"),
                          ("PostToolUse", f'cd {self.repo} && git commit -q -m nothing > /dev/null 2>&1; echo "exit=$?"')):
            tu = "tu-B-" + ev
            self._pre(cmd_b, str(self.repo), sid="sess-gt-ab", tool_use_id=tu)
            rec_b = self._post(cmd_b, str(self.repo), sid="sess-gt-ab", tool_use_id=tu, ev=ev)
            self.assertFalse(rec_b["kind"] == "git:commit" and rec_b["ok"] is True, (ev, rec_b))

    def test_missing_tool_use_id_disables_the_ground_truth(self):
        """赤チーム（high）: tool_use_id が無いと控えの鍵がセッションになり、並行の別の呼び出しと取り違えた＝控えを作らない。"""
        cmd = f"cd {self.repo} && git commit --allow-empty -q -m x"
        run_hook(self.home, {"session_id": "sess-gt-noid", "hook_event_name": "PreToolUse", "cwd": str(self.repo),
                             "tool_name": "Bash", "tool_input": {"command": cmd}})
        self.assertFalse((self.home / ".claude" / "office_events" / "commit_pre").exists())

    def test_a_reused_tool_use_id_in_another_repo_is_not_trusted(self):
        """赤チーム（high）: 同じ tool_use_id で別のリポジトリの控えが上書きされても、コマンドと場所の照合で使わない。"""
        other = _make_repo(Path(tempfile.mkdtemp(prefix="office_event_repo2_")))
        self.addCleanup(shutil.rmtree, other.parent, ignore_errors=True)
        cmd_a = f"cd {self.repo} && git commit -q -m x"
        cmd_b = f"cd {other} && git commit --allow-empty -q -m y"
        # A の HEAD は直前の commit（コミッタ時刻が新しい＝時刻の条件では止まらない＝照合だけが止める形）
        _git(["commit", "--allow-empty", "-q", "-m", "recent"], self.repo)
        self._pre(cmd_a, str(self.repo), sid="sess-gt-dup", tool_use_id="tu-DUP")
        self._pre(cmd_b, str(other), sid="sess-gt-dup", tool_use_id="tu-DUP")      # 控えを上書き
        _git(["commit", "--allow-empty", "-q", "-m", "y"], other)
        rec = self._post(cmd_a, str(self.repo), sid="sess-gt-dup", tool_use_id="tu-DUP", ev="PostToolUseFailure")
        self.assertFalse(rec["kind"] == "git:commit" and rec["ok"] is True, rec)

    def test_non_commit_git_command_does_not_leave_a_memo(self):
        """「git」と「commit」の語があるだけ（commit を打つ段が無い）なら控えを作らない＝Post で待たない（赤チーム）。"""
        cmd = f"cd {self.repo} && git log --oneline | grep commit"
        self._pre(cmd, str(self.repo), sid="sess-gt-log", tool_use_id="tu-log")
        self.assertFalse((self.home / ".claude" / "office_events" / "commit_pre").exists())

    def test_existing_loose_memo_file_is_tightened_to_0600(self):
        """赤チーム（low）: 先に在った 0644 の控えも 0600 に直す（O_CREAT の mode は新規のときだけ効く）。"""
        cmd = f"cd {self.repo} && git commit --allow-empty -q -m x"
        self._pre(cmd, str(self.repo), sid="sess-gt-perm", tool_use_id="tu-perm")
        memo = next((self.home / ".claude" / "office_events" / "commit_pre").glob("*.json"))
        os.chmod(memo, 0o644)
        self._pre(cmd, str(self.repo), sid="sess-gt-perm", tool_use_id="tu-perm")
        self.assertEqual(stat.S_IMODE(memo.stat().st_mode), 0o600)

    def test_unrelated_repo_committing_does_not_create_a_false_positive(self):
        """`self.repo` 側では実際には何もコミットしない（ground truth は不確定＝HEAD 不変）のに、
        別の無関係なリポジトリでたまたまコミットが乗っても、**その事実では**証拠にならない
        （見るのは自分の dir の HEAD だけ）。ground truth は「分かったときだけ足す」側＝
        確定できないときは old の文字形判定の答え（当たっていようと外れていようと）をそのまま通す。
        この文字形は old 判定でも拾える（`cd … && git commit …` 単体）ので、最終結果は変わらず
        "git:commit" のまま＝ground truth が余計に黒く塗り替えてしまわないことを確かめる。"""
        other_root = self.root / "other_root"
        other_root.mkdir()
        other = _make_repo(other_root)
        cmd = f"cd {self.repo} && git commit -q -m x"       # self.repo では実際には打たない
        self._pre(cmd, str(self.repo), sid="sess-gt-wrong", tool_use_id="tu-wrong")
        _git(["commit", "--allow-empty", "-q", "-m", "z"], other)   # 別リポジトリの方だけ実際に動かす
        rec = self._post(cmd, str(self.repo), sid="sess-gt-wrong", tool_use_id="tu-wrong")
        # ground truth は self.repo の HEAD が不変なので確定しない（override しない）。
        # old の文字形判定はこの単体の形を拾えるので、答えはそこから来る（ground truth に消されない）。
        self.assertEqual(rec["kind"], "git:commit")


if __name__ == "__main__":
    unittest.main()
