# R85-0/R86-C: ui/hud/strings.js の ja/en 番人（R98-W1 で ui/iso から移設）。
# ①キー集合の一致: tpl_* 9キーが en にだけ追加され、既定言語 ja で T() が生キー名を返して
#   画面に "tpl_add" 等が出た（i18nカナリアは en 側しか見ないため検出不能だった）。
# ②en の値に日本語が混じっていないこと: i18n_iso_smoke はシートを一度も開かないので、
#   dialog_* のようなシート内文言は en に日本語を書いても誰も気づかない（R86-C の監査で発覚）。
# node が無い環境ではスキップ（verify の JS 検査と同じ流儀）。
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRINGS = ROOT / "ui" / "hud" / "strings.js"   # R98-W1: 移設
CJK = re.compile(r"[぀-ヿ一-鿿]")     # ひらがな・カタカナ・漢字


def _eval(node, expr):
    script = (
        "import(%s).then(m => {"
        " console.log(JSON.stringify(%s));"
        "}).catch(e => { console.error(e); process.exit(1); });"
    ) % (json.dumps(STRINGS.as_uri()), expr)
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])


class TestStringsParity(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if not self.node:
            self.skipTest("node なし（verify の JS 検査と同様に省略）")

    def test_ja_en_key_parity(self):
        keys = _eval(self.node, "m.dictKeys()")
        ja, en = set(keys["ja"]), set(keys["en"])
        self.assertEqual(sorted(en - ja), [],
                         "ja に無いキー（既定言語jaで生キー名が画面に出る）")
        self.assertEqual(sorted(ja - en), [],
                         "en に無いキー（英語UIで日本語フォールバックになる）")
        self.assertGreater(len(ja), 100)          # 空辞書で偽greenにならない保険

    def test_en_values_have_no_japanese(self):
        """en の文字列値に日本語が混じっていないこと（シート内文言は誰も見ていない穴）。"""
        strings = _eval(self.node, "m.dictStrings()")
        hits = {k: v for k, v in strings["en"].items() if CJK.search(v)}
        self.assertEqual(hits, {}, f"en に日本語が混入: {hits}")
        self.assertGreater(len(strings["en"]), 80)
        # ja 側は日本語が主体なので逆方向は検査しない（英字のみのラベルも正当）

    def test_apv_reason_covers_policy_reasons(self):
        """b4-mac: 📱「Mac だけの理由」は server/approval_policy.py の REASONS 全部に訳がある（内部コードを画面に出さない）。
        ja は日本語・en は日本語なし。新しい理由を足したら strings.js の APV_REASON_JA/EN にも足す。"""
        import sys
        sys.path.insert(0, str(ROOT / "server"))
        import approval_policy
        keys = list(approval_policy.REASONS)
        expr = ("(() => { const K = %s; const out = {};"
                " for (const l of ['ja', 'en']) { m.setLang(l); out[l] = K.map((k) => m.T('apv_reason', k)); }"
                " m.setLang('ja'); return out; })()") % json.dumps(keys)
        got = _eval(self.node, expr)
        for lang in ("ja", "en"):
            raw = [k for k, v in zip(keys, got[lang]) if v == k]
            self.assertEqual(raw, [], f"{lang}: 訳の無い理由（内部コードのまま出る）")
        self.assertEqual([k for k, v in zip(keys, got["ja"]) if not CJK.search(v)], [], "ja の訳が日本語でない")
        self.assertEqual([k for k, v in zip(keys, got["en"]) if CJK.search(v)], [], "en の訳に日本語")
        self.assertGreater(len(keys), 40)


if __name__ == "__main__":
    unittest.main()
