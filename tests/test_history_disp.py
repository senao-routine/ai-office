#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan.md #13: 「最近の指示」に古い別形式の行が混じる実バグ（real-data-readonly-02）。

事実: `server/office_server.py` の履歴保存は `disp` を送信時点のスキャンから引けないと空文字を
保存するだけだが、**表示を組む `scan_office()` 側**が毎回 `h.get("disp") or h.get("session","")[:8]`
で session の生の断片を名前のように見せていた。「プロジェクト名(works)」形式の他の行と体裁が違う
＝「古い別形式」に見える。本当は古い別形式のデータが混じっているのではなく、表示のたびに毎回
同じ理由で化けていた。

直し方（plan.md #13）: disp を今のスキャンから引き直す（セッションがまだ在籍していれば直る）か、
それも無ければ生のID断片を出さずに行を「最近の指示」から外す。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 4811


def _get(path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"X-Office-Local": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class HistoryDispTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(subprocess.run([sys.executable, str(ROOT / "tests" / "make_home.py")],
                                       check=True, capture_output=True, text=True).stdout.strip())
        now = time.time()
        history = [
            # ① 正規（この回で送って disp が引けた）
            {"session": "sess-verify0001", "disp": "demo-project", "text": "ok", "ts": now - 10},
            # ② 送信時に disp を引けなかった古い行だが、セッションはまだ在籍している＝今のスキャンから引き直せる
            {"session": "sess-verify0002", "disp": "", "text": "resolve me", "ts": now - 20},
            # ③ disp も引けず、セッション自体もとうに居ない（実データの『36日前』相当）
            {"session": "sess-long-gone-0000", "disp": "", "text": "please refactor the billing module",
             "ts": now - 30},
        ]
        inbox = cls.home / ".claude" / "office_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "_history.json").write_text(json.dumps(history), encoding="utf-8")
        env = dict(os.environ, OFFICE_HOME=str(cls.home),
                   OFFICE_CONFIG=str(cls.home / "office_config.json"))
        cls.log = open(cls.home / "daemon.log", "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server/office_server.py"),
                                     "--port", str(PORT)], env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                if _get("/api/office")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("test server did not come up")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.log.close()
        import shutil
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_resolved_and_unresolvable_rows(self):
        status, body = _get("/api/office")
        self.assertEqual(status, 200)
        hist = {h["session"]: h["disp"] for h in body["history"]}
        self.assertEqual(hist.get("sess-verify0001"), "demo-project")
        # ②: 送信時に空だった disp が、今のスキャンから正しく引き直されている
        self.assertEqual(hist.get("sess-verify0002"), "demo-project 2号")
        # ③: 引けない行は session の生の断片（"sess-long-"）のような文字列を見せず、丸ごと落ちる
        self.assertNotIn("sess-long-gone-0000", hist)
        for h in body["history"]:
            self.assertNotEqual(h["disp"], h["session"][:8], h)
            self.assertTrue(h["disp"], "空の表示名を残したまま出している行がある")


if __name__ == "__main__":
    unittest.main()
