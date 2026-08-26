# R85-0: ui/iso/strings.js の ja/en キーパリティ番人。
# 背景: tpl_* 9キーが en にだけ追加され、既定言語 ja で T() が生キー名を返して
# 画面に "tpl_add" 等が表示された（i18nカナリアは en 側しか見ないため検出不能だった）。
# ここで両言語のキー集合一致を機械ピンする。node が無い環境ではスキップ（verify と同じ流儀）。
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STRINGS = ROOT / "ui" / "iso" / "strings.js"


class TestStringsParity(unittest.TestCase):
    def test_ja_en_key_parity(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node なし（verify の JS 検査と同様に省略）")
        script = (
            "import(%s).then(m => {"
            " console.log(JSON.stringify(m.dictKeys()));"
            "}).catch(e => { console.error(e); process.exit(1); });"
        ) % json.dumps(STRINGS.as_uri())
        out = subprocess.run([node, "-e", script], capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        keys = json.loads(out.stdout.strip().splitlines()[-1])
        ja, en = set(keys["ja"]), set(keys["en"])
        self.assertEqual(sorted(en - ja), [],
                         "ja に無いキー（既定言語jaで生キー名が画面に出る）")
        self.assertEqual(sorted(ja - en), [],
                         "en に無いキー（英語UIで日本語フォールバックになる）")
        # 空辞書で偽greenにならない保険
        self.assertGreater(len(ja), 100)


if __name__ == "__main__":
    unittest.main()
