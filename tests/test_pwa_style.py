# -*- coding: utf-8 -*-
"""R101: スマホ PWA の見た目の掟を既定の verify で見る（pwa3d_smoke は RUN_RELAY=1 のときしか走らない）。

docs/art-direction.md §13（ターミナル×ドット絵）: 紫・グラデーション・ガラス（blur）・keyframe・角丸の棒・生の色を置かない。
色は端末の配色（ui/cockpit/palette.js の TERM → 生成の区画）から。生成の区画（TERM_TOKENS・PX_ICONS）は検査の外。
R122: 明るいアプリの配色は APP_TOKENS 区画（Mac ui/app/style.css と同値）も許す。区画内は token 宣言だけか別に検査する。
ホーム・会話・オフィス（body.pwa-app の範囲）だけ、見本の角丸とプロポーショナル字を許す。
R121（§13.11）: 作業ビュー（生成の区画 ACTIVITY と `.act` の中だけの手書きの規則）は例外＝本文はプロポーショナル（--t-sans）・
角丸は吹き出し 14px・カード 12px・札の全丸だけ。その範囲は test_activity_scope_keeps_its_own_rules が別に見る（緩めるのは作業ビューの中だけ）。
R134: APP_TOKENS も生成物（正本= ui/app/style.css の --a-*・--font-ui）。いつも明るい＝テーマの選択と暗いモードの分岐を持たない。
広い画面（開いた Duo・iPad）の形の正本は app.css の --wide。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "ui" / "pwa" / "app.css"
HTML = ROOT / "ui" / "pwa" / "app.html"
GENERATED = (("/* TERM_TOKENS_BEGIN", "/* TERM_TOKENS_END */"), ("/* PX_ICONS_BEGIN", "/* PX_ICONS_END */"))
APP_TOKENS = ("/* APP_TOKENS_BEGIN", "/* APP_TOKENS_END */")
ACTIVITY = ("/* ACTIVITY_BEGIN", "/* ACTIVITY_END */")       # R121: ui/hud/activity.css の展開（作業ビュー）
ACTIVITY_SRC = ROOT / "ui" / "hud" / "activity.css"
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
# ベンダーの点（claude・codex・openclaw・bot の見分け）は意味の色として 4 つだけ許す（理由: 1 文字のバッジの地・R98 から）。
ALLOWED_HEX = {"#e9e0d5", "#787676", "#bb4838", "#5d76a8"}


def _in_activity(selector):
    """規則の全部のセレクタが作業ビューの中（`.act` を含む）＝§13.11 の例外の範囲。"""
    parts = [x.strip() for x in selector.split(",") if x.strip()]
    return bool(parts) and all(re.search(r"\.act\b", x) for x in parts)


def _in_app(selector):
    """R122 の新しい画面だけ。旧いターミナルの規則の例外にはしない。"""
    parts = [x.strip() for x in selector.split(",") if x.strip()]
    return bool(parts) and all(x.startswith("body.pwa-app") for x in parts)


def _split_activity(css):
    """(作業ビューの外の規則の CSS, 作業ビューの中の規則の宣言) — 中の規則は外から抜く。"""
    inside = []

    def pick(m):
        if _in_activity(m.group(1)):
            inside.append(m.group(2))
            return ""
        return m.group(0)
    return _RULE.sub(pick, css), inside


def hand_css():
    s = CSS.read_text(encoding="utf-8")
    for b, e in GENERATED + (ACTIVITY, APP_TOKENS):
        i, j = s.index(b), s.index(e) + len(e)
        s = s[:i] + s[j:]
    return _split_activity(re.sub(r"/\*.*?\*/", "", s, flags=re.S))[0]


def activity_decls():
    """作業ビューの範囲の宣言: 正本 ui/hud/activity.css 全部＋app.css の手書きのうち `.act` の中の規則。"""
    s = CSS.read_text(encoding="utf-8")
    for b, e in GENERATED + (ACTIVITY, APP_TOKENS):
        i, j = s.index(b), s.index(e) + len(e)
        s = s[:i] + s[j:]
    inside = _split_activity(re.sub(r"/\*.*?\*/", "", s, flags=re.S))[1]
    src = re.sub(r"/\*.*?\*/", "", ACTIVITY_SRC.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(inside + [m.group(2) for m in _RULE.finditer(src)])


class PwaStyleTest(unittest.TestCase):
    def test_braces_balance(self):
        """余分な `}` は CSS では「次の規則ごと捨てる」になる（R101 の作業中に ❗ の間ティッカーを畳む規則が黙って消えた）。
        コメントと文字列を除いて、どこでも深さが負にならず最後に 0 へ戻る。"""
        for f in (CSS, ROOT / "ui" / "cockpit" / "style.css"):
            if not f.is_file():
                continue                          # 本番（master）の木にはコックピットが無い
            text = re.sub(r"/\*.*?\*/", "", f.read_text(encoding="utf-8"), flags=re.S)
            text = re.sub(r'"(?:\\.|[^"\\])*"' + r"|'(?:\\.|[^'\\])*'", '""', text)
            depth = 0
            for n, line in enumerate(text.split("\n"), 1):
                for ch in line:
                    depth += (ch == "{") - (ch == "}")
                    self.assertGreaterEqual(depth, 0, f"{f.name}: 余分な }} （コメントを除いた {n} 行目付近）")
            self.assertEqual(depth, 0, f"{f.name}: 閉じていない {{")

    def test_no_banned_decorations(self):
        css = hand_css()
        for pat, why in ((r"gradient\(", "グラデーション"), (r"@keyframes", "keyframe（決定論・動きの設定に従わない）"),
                         (r"backdrop-filter", "ガラス（blur）"), (r"border-left:\s*[2-9]", "色付きの左の帯")):
            self.assertEqual(re.findall(pat, css), [], why)
        for m in _RULE.finditer(css):
            radii = {v.strip() for v in re.findall(r"border-radius:\s*([^;]+)", m.group(2)) if not v.strip().startswith("var(")}
            # R122 の見本だけ: 操作12・カード16・部屋18・入力22・名簿の上端20。旧い窓の門は広げない。
            allowed = {"0", "50%"}
            if _in_app(m.group(1)):
                allowed |= {"12px", "16px", "18px", "20px 20px 0 0", "18px 18px 0 0", "22px", "999px"}
            self.assertTrue(radii <= allowed, (m.group(1), radii))

    def test_app_tokens_match_mac_and_only_contain_tokens(self):
        """R134: 明るい配色の正本は Mac の ui/app/style.css（--a-* と --font-ui）。スマホの区画は生成物で、:root 1 つだけ（暗いモードの
        結び直し・data-theme の分岐を持たない＝いつも明るい）。"""
        css = CSS.read_text(encoding="utf-8")
        begin, end = APP_TOKENS
        block = css[css.index("*/", css.index(begin)) + 2:css.index(end)]
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        mac = (ROOT / "ui" / "app" / "style.css").read_text(encoding="utf-8")
        a_values = dict(re.findall(r"(--a-[\w-]+):\s*(#[0-9A-Fa-f]{6});", block))
        mac_values = dict(re.findall(r"(--a-[\w-]+):\s*(#[0-9A-Fa-f]{6});", mac))
        self.assertEqual(a_values, mac_values)
        self.assertEqual(len(a_values), 16)
        mac_font = re.search(r"--font-ui:\s*([^;]+);", mac).group(1).strip()
        rules = list(_RULE.finditer(block))
        self.assertEqual([r.group(1).strip() for r in rules], [":root"], "明るい配色の区画は :root の 1 つだけ（暗いモードの分岐を持たない）")
        self.assertNotIn("prefers-color-scheme", block)
        self.assertNotIn("data-theme", block)
        for prop, value in re.findall(r"([\w-]+):\s*([^;]+);", rules[0].group(2)):
            self.assertTrue(prop.startswith(("--a-", "--t-")) or prop in ("color-scheme", "--font-ui"), prop)
            if prop == "color-scheme":
                self.assertEqual(value.strip(), "light")
            elif prop == "--font-ui":
                self.assertEqual(value.strip(), mac_font)
            else:
                self.assertRegex(value.strip(), r"^(?:#[0-9A-Fa-f]{6}|var\(--[at]-[\w-]+\)|var\(--font-ui\))$")
                if value.strip().startswith("#"):
                    self.assertIn(prop, a_values)
                    self.assertEqual(value.strip(), a_values[prop])

    def test_no_raw_colors_outside_generated_tokens(self):
        css = hand_css()
        hexes = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)} - ALLOWED_HEX
        self.assertEqual(hexes, set(), "生の色（palette.js の TERM から引く）")
        rgb = re.findall(r"rgba?\([^)]*\)", css)
        self.assertEqual(rgb, ["rgba(0, 0, 0, .55)"], "暗幕（--scrim）以外の rgba")

    def test_fonts_are_the_terminal_mono(self):
        rules = list(_RULE.finditer(hand_css()))
        self.assertTrue(rules)
        for rule in rules:
            allowed = {"var(--t-mono)", "inherit"}
            if _in_app(rule.group(1)):
                allowed.add("var(--t-sans)")
            fams = re.findall(r"font-family:\s*([^;}]+)", rule.group(2))
            self.assertTrue(all(f.strip() in allowed for f in fams), (rule.group(1), fams))

    def test_chrome_has_no_emoji_in_html(self):
        html = HTML.read_text(encoding="utf-8")
        chrome = re.findall(r'<(?:span|button|h3|div)[^>]*id=(?:tb_\w+_lb|st_\w+_lb|st_title|ntogic|su_title|rn_title|ln_title|rs_title)[^>]*>([^<]*)<', html)
        emoji = [c for c in chrome if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", c)]
        self.assertEqual(emoji, [], "ヘッダー・タブ・設定の行頭に絵文字（ドット絵のアイコンと文字で見せる）")

    def test_activity_scope_keeps_its_own_rules(self):
        """R121 §13.11: 作業ビューの中でも、グラデーション・keyframe・blur・左の色帯は禁止のまま。角丸と字の例外は決めた値だけ。"""
        decls = activity_decls()
        self.assertTrue(decls)
        for pat, why in ((r"gradient\(", "グラデーション"), (r"@keyframes", "keyframe"), (r"backdrop-filter", "ガラス（blur）"),
                         (r"border-left:\s*[2-9]", "色付きの左の帯"), (r"box-shadow:\s*(?!none)", "影で浮かせる")):
            self.assertEqual(re.findall(pat, decls), [], why)
        radii = {v.strip() for v in re.findall(r"border-radius:\s*([^;]+)", decls)}
        self.assertTrue(radii <= {"0", "50%", "12px", "14px", "999px"}, radii)
        fams = {v.strip() for v in re.findall(r"font-family:\s*([^;]+)", decls)}
        self.assertTrue(fams <= {"var(--t-sans)", "var(--t-mono)", "inherit"}, fams)
        fonts = re.findall(r"font:\s*([^;]+)", decls)
        self.assertTrue(all(f.strip() == "inherit" or re.search(r"var\(--t-(?:sans|mono)\)$", f.strip()) for f in fonts), fonts)

    def test_always_light_without_theme_choice(self):
        """R134（本人 2026-11-14「配色も PC と同じ明るい色に」）: iPhone の暗いモードに合わせない・テーマの 3 択は撤去。
        meta theme-color は明暗 2 本のまま（gen_pwa_icons の門）だが、head の __applyTheme が両方をヘッダーの地（明るい --t-bg）へ。"""
        html = HTML.read_text(encoding="utf-8")
        app = (ROOT / "ui" / "pwa" / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.__applyTheme", html)
        self.assertIn('<meta name=color-scheme content="light">', html)
        self.assertIn('media="(prefers-color-scheme: light)"', html)
        self.assertIn('media="(prefers-color-scheme: dark)"', html)
        head = html[:html.index("</head>")]
        self.assertNotIn('setAttribute("data-theme"', head, "head のスクリプトがテーマを付けている（いつも明るい）")
        self.assertNotIn('getItem("aioffice.theme")', head + app, "保存されたテーマを読んでいる（いつも明るい）")
        for b in ("sg_th_auto", "sg_th_dark", "sg_th_light", "st_th_lb"):
            self.assertNotIn(b, html + app, "テーマの選択が残っている")
        self.assertNotIn("function setTheme(", app)
        hand = hand_css()
        self.assertNotIn("data-theme", hand, "手書きの CSS がテーマの属性で分岐している")
        self.assertNotIn("prefers-color-scheme", hand, "手書きの CSS が端末の暗いモードに合わせている")

    def test_app_tokens_generator_gate(self):
        """R134: gen_pwa_modules は明るい配色（ui/app/style.css の --a-*）から APP_TOKENS を作り、字と地のコントラストの下限を生成時に見る。
        変異: 薄い字を淡くする（--a-ink-faint #A8A39B＝地の上で 2.4:1）→ 止まる／キーを 1 つ落とす → 止まる。今の正本では区画が app.css と一致。"""
        import importlib.util
        import tempfile
        spec = importlib.util.spec_from_file_location("gen_pwa_modules_r134", ROOT / "tools" / "gen_pwa_modules.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        vals, font = gen.read_app()
        block = gen.render_app_block(vals, font)
        self.assertIn(block, CSS.read_text(encoding="utf-8"), "APP_TOKENS が正本（ui/app/style.css）から作り直した区画と違う")
        mac = (ROOT / "ui" / "app" / "style.css").read_text(encoding="utf-8")
        for bad in (mac.replace("--a-ink-faint: #6B665E;", "--a-ink-faint: #A8A39B;", 1), mac.replace("  --a-idle: #D9D4CB;\n", "", 1)):
            self.assertNotEqual(bad, mac)
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "style.css"
                p.write_text(bad, encoding="utf-8")
                old = gen.APP_SOURCE
                gen.APP_SOURCE = p
                try:
                    with self.assertRaises(SystemExit):
                        gen.read_app()
                finally:
                    gen.APP_SOURCE = old

    def test_wide_layout_shape_lives_in_css(self):
        """R134: 広い画面（開いた Duo・iPad）の形の正本は app.css の --wide（0／2／3）だけ。app.js は値を読むだけ（寸法を書き写さない）。"""
        css = CSS.read_text(encoding="utf-8")
        app = (ROOT / "ui" / "pwa" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"@media \(min-width: (\d+)px\) and \(min-height: (\d+)px\) \{ :root \{ --wide: (\d); \} \}", css),
                         [("600", "600", "2"), ("860", "600", "3")])
        self.assertIn(':root { --wide: 0; }', css)
        self.assertIn('getPropertyValue("--wide")', app)
        self.assertNotRegex(app, r"matchMedia\([^)]*min-height: 600", "app.js が広い画面の寸法を書き写している")


if __name__ == "__main__":
    unittest.main()
