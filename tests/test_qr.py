# -*- coding: utf-8 -*-
"""P6 QR生成のゴールデン（verify ▶4 の unittest で常時）。vendored segno（信頼ライブラリ）で
matrix生成＝手書きエンコーダの壊れQRリスク無し。
このKATは「vendored segno の差替/破損・qr_gen の退行」を検知する回帰ロックであり、ライブデコード
による独立オラクルではない（segno自体を信頼＝vendoringの目的）。KAT_MATRIX_SHA は vendoring時に
pip版 segno 1.6.6 から一度生成した固定値。再導出手順は tools/vendor/README.md に記載。"""
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 固定payload（app の pair_url と同形式・194B → 版10 ECC-M）。s/tは固定hexで決定論的。
KAT_PAYLOAD = ("https://ex.workers.dev/app#v=1&d=d_0123456789ab&s=" + "ab" * 32
               + "&t=" + "cd" * 32 + "&e=1900000000")
KAT_VERSION = 10
KAT_MATRIX_SHA = "73d60acaa1dbbc5e4c6a72d94e2cf1807d4fea982679385811a05d3333f7b859"


class QrTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools" / "vendor"))
        from segno import encoder
        self.encoder = encoder
        spec = importlib.util.spec_from_file_location("qr_gen_t", ROOT / "tools" / "qr_gen.py")
        self.qr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.qr)

    def test_matrix_kat(self):
        code = self.encoder.encode(KAT_PAYLOAD, error="m")
        self.assertEqual(code.version, KAT_VERSION)
        flat = "".join(str(c) for row in code.matrix for c in row)
        self.assertEqual(hashlib.sha256(flat.encode()).hexdigest(), KAT_MATRIX_SHA,
                         "vendored segno のmatrixが黄金値と不一致＝vendor破損/バージョン差の疑い")

    def test_svg_output(self):
        out = self.qr.svg(KAT_PAYLOAD)
        self.assertTrue(out.startswith("<svg"))
        self.assertIn("</svg>", out)
        self.assertIn("<path", out)
        self.assertIn("viewBox", out)
        # 版10=57モジュール＋QUIET*2(=8) → viewBox 0 0 65 65
        self.assertIn("viewBox=\"0 0 65 65\"", out)


if __name__ == "__main__":
    unittest.main()
