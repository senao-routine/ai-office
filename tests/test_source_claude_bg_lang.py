#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan.md #23: `server/source_claude_bg.py` の `detail`（外部の bg 管理プロセスが自己申告する自由文）が
AI Office の言語設定を無視していた。実測: 設定を English に切り替えても台帳の『いま』列だけ日本語が残る
（他の全 UI は英語化済みという対比で初めて気づける静かな嘘）。

自由文は翻訳できない＝**言語を合わせられないなら出さない**（嘘より無言）。lang="ja" のときだけ通し、
"en"（やその他）では detail を空にする。既定は後方互換のため lang="ja"（呼び忘れても今までどおり）。
"""
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import source_claude_bg as bg  # noqa: E402

RAW = {"daemonShort": "d1", "sessionId": "sess-abc", "state": "working",
       "detail": "verify を実行中", "updatedAt": time.time()}


class DetailLanguageGateTest(unittest.TestCase):
    def test_ja_passes_detail_through(self):
        rec = bg.parse_job_state(RAW, time.time(), "ja")
        self.assertEqual(rec["detail"], "verify を実行中")

    def test_en_blanks_the_free_text_instead_of_lying(self):
        rec = bg.parse_job_state(RAW, time.time(), "en")
        self.assertEqual(rec["detail"], "")

    def test_unknown_lang_also_blanks(self):
        rec = bg.parse_job_state(RAW, time.time(), "fr")
        self.assertEqual(rec["detail"], "")

    def test_default_lang_keeps_old_behavior(self):
        """既定値（呼び忘れても壊れない）は ja のまま＝既存の呼び出し元を壊さない。"""
        rec = bg.parse_job_state(RAW, time.time())
        self.assertEqual(rec["detail"], "verify を実行中")

    def test_bg_index_propagates_lang(self):
        import json
        import tempfile
        home = Path(tempfile.mkdtemp(prefix="office_bg_lang_"))
        self.addCleanup(__import__("shutil").rmtree, home, ignore_errors=True)
        jobs = home / ".claude" / "jobs" / "d1"
        jobs.mkdir(parents=True)
        (jobs / "state.json").write_text(json.dumps(RAW), encoding="utf-8")
        now = time.time()
        idx_ja = bg.bg_index(home, now, lang="ja")
        idx_en = bg.bg_index(home, now, lang="en")
        self.assertEqual(idx_ja.lookup("sess-abc")["detail"], "verify を実行中")
        self.assertEqual(idx_en.lookup("sess-abc")["detail"], "")


if __name__ == "__main__":
    unittest.main()
