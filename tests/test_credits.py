# -*- coding: utf-8 -*-
"""R105: スペシャルサンクスの名簿（ui/credits.json）の形の門。

配布物に入る＝公開面。載せてよいのはニックネームと段（A/B/C）だけ。メール・URL・@ハンドル・改行・制御文字・長すぎる名前は落とす。
名簿を更新したら ng_check も通す（本人の名前・所属が推測できる語を載せない＝公開ポリシー）。
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CREDITS = ROOT / "ui" / "credits.json"
BETA = ROOT / "ui" / "beta.json"
BAD = re.compile(r"[@/\\<>\x00-\x1f\x7f]|https?:|www\.|\.(com|net|jp|io|dev)\b", re.I)


class CreditsTest(unittest.TestCase):
    def test_shape(self):
        data = json.loads(CREDITS.read_text(encoding="utf-8"))
        self.assertEqual(data.get("v"), 1)
        self.assertIsInstance(data.get("people"), list)
        seen = set()
        for p in data["people"]:
            self.assertEqual(set(p), {"name", "tier"}, f"載せてよいのは name と tier だけ: {p}")
            name = p["name"]
            self.assertIsInstance(name, str)
            self.assertTrue(1 <= len(name.strip()) <= 24, f"名前の長さ: {name!r}")
            self.assertEqual(name, name.strip())
            self.assertIsNone(BAD.search(name), f"連絡先や URL に見える名前は載せない: {name!r}")
            self.assertIn(p["tier"], ("A", "B", "C"))
            self.assertNotIn(name.casefold(), seen, f"重複: {name!r}")
            seen.add(name.casefold())

    def test_bad_pattern_catches_contacts(self):
        for bad in ("a@b", "https://x", "x.com", "line\nbreak", "@handle", "www.example"):
            self.assertIsNotNone(BAD.search(bad), bad)
        for good in ("たろう", "Sato", "ねこ好き", "R2-D2", "ほし☆"):
            self.assertIsNone(BAD.search(good), good)


class BetaConfigTest(unittest.TestCase):
    def test_feedback_url_is_https_or_empty(self):
        data = json.loads(BETA.read_text(encoding="utf-8"))
        self.assertEqual(data.get("v"), 1)
        url = data.get("feedbackUrl")
        self.assertIsInstance(url, str)
        # 画面（ui/hud/beta.js の FORM_RE）と同じ条件: 空か https の URL だけ（javascript: 等を開かせない）
        self.assertTrue(url == "" or re.fullmatch(r"https://[^\s\"'<>]+", url), url)


if __name__ == "__main__":
    unittest.main()
