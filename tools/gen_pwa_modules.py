#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R77: スマホPWAの部屋用に ui/ のESMを Worker へ同梱する（sprites_data.js と同じ型）。

なぜ必要か: PWAは Cloudflare Worker が配信する＝Macの 127.0.0.1 には届かない。
デスクトップと同じ部屋をスマホで動かすには、モジュール一式を Worker が
`/ui/...` の**同じパス**で返す必要がある（import 指定子を書き換えないための条件）。
R122: 通常の束はドット絵・封書・近況だけ。3D は別の modules_3d.js に閉じ、初回の 3D 表示でだけ読む。

生成物: relay/src/modules_data.js / modules_3d.js / app_html.js（git追跡必須）。
ui/hud-tokens.css の共通トークンを ui/hud/hud.css（Mac の iso・pixel）へ展開する。
R101: 端末の配色（ui/cockpit/palette.js の TERM・dark/light）を `--t-*` にして ui/pwa/app.css と ui/cockpit/style.css へ、
ドット絵のアイコン（ui/cockpit/icons.css＝design/gen_icons.py の生成物）を ui/pwa/app.css へ展開する（PWA は 1 枚の HTML）。
R121: ui/hud/activity.css の作業ビューを Mac と PWA の印の間へ展開し、本文用 --t-sans も共有する。
R134: スマホの地と字の色は Mac の明るい画面（ui/app/style.css の --a-*・--font-ui）から ui/pwa/app.css の APP_TOKENS へ（いつも明るい側）。
使い方:
    python3 tools/gen_pwa_modules.py           # 生成
    python3 tools/gen_pwa_modules.py --check   # ui/ との一致（ドリフト検知・verify用）
"""
import base64
import hashlib
import json
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "relay" / "src" / "modules_data.js"
OUT_3D = ROOT / "relay" / "src" / "modules_3d.js"
APP_OUT = ROOT / "relay" / "src" / "app_html.js"
HUD_SOURCE = ROOT / "ui" / "hud-tokens.css"
HUD_TARGETS = [ROOT / "ui" / "hud" / "hud.css"]      # R101: PWA は端末の配色（TERM）へ移った＝iso と色を共有しない
TERM_SOURCE = ROOT / "ui" / "cockpit" / "palette.js"
TERM_TARGETS = [ROOT / "ui" / "pwa" / "app.css", ROOT / "ui" / "cockpit" / "style.css"]
TERM_BEGIN = "/* TERM_TOKENS_BEGIN (generated: ui/cockpit/palette.js TERM) */"
TERM_END = "/* TERM_TOKENS_END */"
ICONS_SOURCE = ROOT / "ui" / "cockpit" / "icons.css"
ICONS_TARGETS = [ROOT / "ui" / "pwa" / "app.css"]
ICONS_BEGIN = "/* PX_ICONS_BEGIN (generated: design/gen_icons.py) */"
ICONS_END = "/* PX_ICONS_END */"
ACTIVITY_SOURCE = ROOT / "ui" / "hud" / "activity.css"
ACTIVITY_TARGETS = [*TERM_TARGETS, ROOT / "ui" / "app" / "style.css"]
ACTIVITY_BEGIN = "/* ACTIVITY_BEGIN (generated: ui/hud/activity.css) */"
ACTIVITY_END = "/* ACTIVITY_END */"
# R134（本人 2026-11-14「スマホ版の配色も PC と同じに」）: スマホの色は Mac の明るい画面（ui/app/style.css の .hud.ui-app の --a-* と
# 字 --font-ui）が正本。PWA はいつも明るい側＝iPhone の暗いモードに合わせない。TERM（端末の配色）はコックピットと部屋の窓（.t-dark）だけ。
APP_SOURCE = ROOT / "ui" / "app" / "style.css"
APP_TARGETS = [ROOT / "ui" / "pwa" / "app.css"]
APP_BEGIN = "/* APP_TOKENS_BEGIN (generated: ui/app/style.css の .hud.ui-app) */"
APP_END = "/* APP_TOKENS_END */"
APP_KEYS = ("bg", "side", "surface", "line", "line-soft", "ink", "ink-soft", "ink-faint", "accent", "accent-soft",
            "attn", "attn-ink", "attn-line", "bubble", "selected", "idle")
# スマホの古い名前（--t-<キー>）を明るい画面の --a-* へ結ぶ表（PWA だけ。Mac は自分の .hud.ui-app で結ぶ）。stage・mono は TERM のまま
APP_TERM_MAP = (("bg", "bg"), ("panel", "surface"), ("panel-2", "bubble"), ("line", "line"), ("line-soft", "line-soft"),
                ("line-strong", "idle"), ("ink", "ink"), ("ink-soft", "ink-soft"), ("ink-faint", "ink-faint"),
                ("accent", "accent"), ("on-accent", "surface"), ("ok", "accent"), ("attn", "attn"), ("warn", "attn-ink"),
                ("danger", "attn-ink"))
# 明るい配色のコントラストの下限（字と地の組だけ。罫の明度差は TERM の門と違い「面の差」で見せる＝ここでは測らない）
APP_CONTRAST_MIN = (
    ("ink", ("bg", "side", "surface", "bubble", "selected"), 7.0),
    ("ink-soft", ("bg", "side", "surface", "bubble"), 4.5),
    ("ink-faint", ("bg", "side", "surface", "bubble"), 4.5),
    ("accent", ("bg", "side", "surface", "accent-soft"), 4.5),
    ("surface", ("accent",), 4.5),                    # 主操作（緑の地）の白い字
    ("attn", ("bg", "side", "surface"), 4.5),
    ("attn-ink", ("bg", "side", "surface"), 4.5),
)
SANS_FONT = '-apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif'
# 端末の配色のキー（dark と light で同じ組・値は #rrggbb）。--t-<キー> になる。
TERM_KEYS = ("bg", "panel", "panel-2", "line", "line-strong", "line-soft", "ink", "ink-soft", "ink-faint", "accent", "on-accent",
             "ok", "attn", "warn", "danger", "stage")
# コントラストの下限（WCAG の比）。足りない組があれば生成も --check も止める（見た目の門を生成時に持つ）。
CONTRAST_MIN = (
    ("ink", ("bg", "panel", "panel-2"), 7.0),
    ("ink-soft", ("bg", "panel", "panel-2"), 4.5),
    ("ink-faint", ("bg", "panel", "panel-2"), 4.5),
    ("accent", ("bg", "panel", "panel-2"), 4.5),
    ("line-strong", ("bg", "panel"), 1.8),
    ("panel-2", ("panel",), 1.15),
    ("on-accent", ("accent",), 4.5),
    ("attn", ("bg", "panel"), 4.5),
    ("warn", ("bg", "panel"), 4.5),
    ("danger", ("bg", "panel"), 4.5),
)
HUD_BEGIN = "/* HUD_TOKENS_BEGIN (generated: ui/hud-tokens.css) */"
HUD_END = "/* HUD_TOKENS_END */"
LAYERS_BEGIN = "/* HUD_LAYERS_BEGIN (generated: ui/hud/layers.js) */"
LAYERS_END = "/* HUD_LAYERS_END */"
LAYER_SELECTORS = {
    "tray": ".hud #attn.tray, .hud #viewreset",
    "sheet": ".hud .sheet, .hud .rail-open .rail",
    "toast": ".hud .toast, .hud .rail-toggle",
    "boss": ".hud .boss-onboarding",
    "modal": ".hud .modalwrap",
    "offbar": ".hud .offbar",
    "consent": ".hud .sound-consent",
    "stream": ".hud #stream-subtitle, .hud #stream-attention",
    "digest": ".hud .digest-card, .hud .replay-controls",
}

# 入口＝部屋（ドット絵）とワールド構築。ここから import を辿って閉包を作る
# R87: 封書の入口は部屋と独立（リスト表示のままでも会話を読めるようにする）。
ENTRIES = ["/ui/pwa/bootpx.js", "/ui/pwa/dlg.js", "/ui/core/session_line.js"]
ENTRIES_3D = ["/ui/pwa/boot3d.js"]
# JSからURLで読む静的アセット（importでは辿れない）。ドット絵は全部コード（cells.js・tiles.js）＝今は無し。
ASSET_DIRS = []
ASSET_DIRS_3D = ["ui/iso/tex"]
ASSET_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}

# import 指定子: 絶対(/ui/...) と 相対(./x.js) の両方
_ABS = re.compile(r'from\s*"(/ui/[^"]+)"')
_REL = re.compile(r'from\s*"(\.\.?/[^"]+)"')


def _read(url):
    path = ROOT / url.lstrip("/")
    if not path.is_file():
        raise SystemExit(f"✗ 依存が見つからない: {url}")
    return path.read_text(encoding="utf-8")


def collect(entries=None, versioned=False):
    """入口から import を辿って {url: source} を作る（テストは含めない）。"""
    seen = {}
    stack = list(entries or ENTRIES)
    while stack:
        url = stack.pop()
        if url in seen:
            continue
        src = _read(url)
        seen[url] = versioned_source(url, src) if versioned else src
        base = url.rsplit("/", 1)[0]
        for dep in _ABS.findall(src):
            stack.append(dep)
        for dep in _REL.findall(src):
            stack.append(posixpath.normpath(f"{base}/{dep}"))
    return dict(sorted(seen.items()))


def versioned_source(url, source):
    """3D だけを /u/<3D版>/ の内側で閉じる。Mac の正本は変更しない。"""
    base = posixpath.dirname(url)
    def relative(m):
        path = posixpath.relpath(m.group(1), base)
        return 'from "' + (path if path.startswith(".") else "./" + path) + '"'
    source = _ABS.sub(relative, source)
    if url == "/ui/iso/scene3d.js":
        source = source.replace('`/ui/iso/tex/${file}`', 'new URL(`./tex/${file}`, import.meta.url).href')
    return source


def collect_assets(directories=None):
    """URL読みのテクスチャを base64 で同梱（importで辿れないので明示収集）。"""
    out = {}
    for d in ASSET_DIRS if directories is None else directories:
        for f in sorted((ROOT / d).glob("*")):
            if f.is_file() and f.suffix in ASSET_MIME:
                out[f"/{d}/{f.name}"] = (ASSET_MIME[f.suffix],
                                         base64.b64encode(f.read_bytes()).decode("ascii"))
    return out


def build_id(mods, assets):
    """内容から決まる版ID。Worker の ETag に使う＝内容が変われば必ず新版になる。
    実行時に計算すると1.2MBを毎コールドスタートで舐めるので、生成時に確定させる。"""
    h = hashlib.sha256()
    for url, src in mods.items():
        h.update(url.encode("utf-8")); h.update(b"\x00")
        h.update(src.encode("utf-8")); h.update(b"\x00")
    for url, (mime, b64) in assets.items():
        h.update(url.encode("utf-8")); h.update(b"\x00")
        h.update(b64.encode("ascii")); h.update(b"\x00")
    return h.hexdigest()[:12]


def file_id(url, body):
    """ファイル単位の版ID。1 本直しても他は 304 で済む＝転送を払い直さない（R96-D3 項目 6）。
    実測: 64 URL 全部に同じ ETag を返していたので、1 行の修正で raw 2.4MB／gzip 1.07MB を再送していた。"""
    h = hashlib.sha256()
    h.update(url.encode("utf-8")); h.update(b"\x00")
    h.update(body.encode("utf-8") if isinstance(body, str) else body)
    return h.hexdigest()[:12]


def render(mods, assets, label="ドット絵"):
    lines = [
        "// 自動生成: tools/gen_pwa_modules.py（手で編集しない）",
        f"// PWAの部屋（{label}）用ESM。Worker が /ui/... と /u/<BUILD>/... から配信する。",
        "export const MODULES = Object.assign(Object.create(null), {",
    ]
    lines.insert(2, f"export const BUILD = {json.dumps(build_id(mods, assets))};   // 内容ハッシュ＝ETagの版")
    lines.insert(3, "")
    for url, src in mods.items():
        lines.append(f"  {json.dumps(url)}: {json.dumps(src)},")
    lines.append("});")
    lines.append("")
    lines.append("// URL で読む静的アセット（base64）。ドット絵の束では空")
    lines.append("export const ASSETS = Object.assign(Object.create(null), {")
    for url, (mime, b64) in assets.items():
        lines.append(f"  {json.dumps(url)}: [{json.dumps(mime)}, {json.dumps(b64)}],")
    lines.append("});")
    lines.append("")
    lines.append("// ファイル単位の ETag（内容ハッシュ）。無い URL は BUILD へ落ちる＝旧挙動。")
    lines.append("export const ETAGS = Object.assign(Object.create(null), {")
    for url, src in mods.items():
        lines.append(f"  {json.dumps(url)}: {json.dumps(file_id(url, src))},")
    for url, (mime, b64) in assets.items():
        lines.append(f"  {json.dumps(url)}: {json.dumps(file_id(url, b64))},")
    lines.append("});")
    lines.append("")
    return "\n".join(lines)


def _inject(css, path, begin_mark, end_mark, block, label):
    if css.count(begin_mark) != 1 or css.count(end_mark) != 1:
        raise SystemExit(f"✗ {path.relative_to(ROOT)} の {label} マーカーは1組必要")
    begin, end = css.index(begin_mark), css.index(end_mark) + len(end_mark)
    if end <= begin:
        raise SystemExit(f"✗ {path.relative_to(ROOT)} の {label} マーカー順が不正")
    return css[:begin] + block + css[end:]


def _json_const(src, name):
    """palette.js の `export const NAME = Object.freeze({...});`（厳密な JSON の書き方）を読む。"""
    m = re.search(r"export const " + name + r" = Object\.freeze\((\{.*?\})\);\n", src, re.S)
    if not m:
        raise SystemExit(f"✗ ui/cockpit/palette.js に {name} が無い（厳密な JSON の書き方で export する）")
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        raise SystemExit(f"✗ palette.js の {name} が JSON として読めない: {e}")


def _lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def read_term():
    """TERM（dark/light）と MONO_FONT。キーの組・値の形・コントラストの下限を確かめてから返す。"""
    src = TERM_SOURCE.read_text(encoding="utf-8")
    term = _json_const(src, "TERM")
    m = re.search(r'export const MONO_FONT = (".*?");\n', src)
    if not m:
        raise SystemExit("✗ ui/cockpit/palette.js に MONO_FONT が無い")
    mono = json.loads(m.group(1))
    bad = []
    if set(term) != {"dark", "light"}:
        bad.append(f"TERM のテーマは dark と light の 2 つ: {sorted(term)}")
    for theme, t in term.items():
        if set(t) != set(TERM_KEYS):
            bad.append(f"TERM.{theme} のキーが違う: 足りない {sorted(set(TERM_KEYS) - set(t))}・余分 {sorted(set(t) - set(TERM_KEYS))}")
            continue
        for k, v in t.items():
            if not (isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v)):
                bad.append(f"TERM.{theme}.{k} が #rrggbb でない: {v!r}")
        if bad:
            continue
        for fg, bgs, need in CONTRAST_MIN:
            for bg in bgs:
                c = contrast(t[fg], t[bg])
                if c + 1e-9 < need:
                    bad.append(f"TERM.{theme}: {fg} on {bg} のコントラスト {c:.2f} < {need}")
    if bad:
        raise SystemExit("✗ 端末の配色（TERM）が門を通らない:\n  " + "\n  ".join(bad))
    return term, mono


def render_term_block(term, mono):
    """:root はダーク（属性が無いときもダーク＝既定）。[data-theme=light] と、auto の端末設定がライトのときだけ明るい側。
    .t-dark は常にダーク（部屋の窓の中＝CRT）。色と color-scheme とフォントの並びだけを持つ。"""
    def decls(t):
        return "".join(f"  --t-{k}: {t[k]};\n" for k in TERM_KEYS)
    dark, light = decls(term["dark"]), decls(term["light"])
    return (TERM_BEGIN + "\n"
            + ":root, .t-dark {\n" + dark + f"  --t-mono: {mono};\n  --t-sans: {SANS_FONT};\n  color-scheme: dark;\n}}\n"
            + ":root[data-theme=\"light\"] {\n" + light + "  color-scheme: light;\n}\n"
            + "@media (prefers-color-scheme: light) {\n  :root[data-theme-auto] {\n"
            + "".join("  " + line + "\n" for line in light.rstrip("\n").split("\n")) + "    color-scheme: light;\n  }\n}\n"
            + ":root[data-theme=\"light\"] .t-dark, :root[data-theme-auto] .t-dark {\n" + dark + "  color-scheme: dark;\n}\n"
            + TERM_END)


def read_app():
    """R134: Mac の明るい画面（ui/app/style.css の `.hud.ui-app { … }`）の --a-* と --font-ui。キーの組・値の形・コントラストを確かめてから返す。"""
    css = APP_SOURCE.read_text(encoding="utf-8")
    m = re.search(r"^\.hud\.ui-app \{\n(.*?)\n\}", css, re.S | re.M)
    if not m:
        raise SystemExit("✗ ui/app/style.css に `.hud.ui-app {` の区画が無い（スマホの明るい配色の正本）")
    body = m.group(1)
    vals = dict(re.findall(r"^\s*--a-([\w-]+):\s*(#[0-9A-Fa-f]{6});", body, re.M))
    font = re.search(r"^\s*--font-ui:\s*([^;]+);", body, re.M)
    bad = []
    if set(vals) != set(APP_KEYS):
        bad.append(f"--a-* のキーが違う: 足りない {sorted(set(APP_KEYS) - set(vals))}・余分 {sorted(set(vals) - set(APP_KEYS))}")
    if not font:
        bad.append("--font-ui が無い")
    if not bad:
        for fg, bgs, need in APP_CONTRAST_MIN:
            for bg in bgs:
                c = contrast(vals[fg], vals[bg])
                if c + 1e-9 < need:
                    bad.append(f"--a-{fg} on --a-{bg} のコントラスト {c:.2f} < {need}")
    if bad:
        raise SystemExit("✗ 明るい配色（ui/app/style.css の --a-*）が門を通らない:\n  " + "\n  ".join(bad))
    return {k: vals[k] for k in APP_KEYS}, font.group(1).strip()


def render_app_block(vals, font):
    """スマホの :root（いつも明るい）。--a-* は Mac の値をそのまま・--t-* は APP_TERM_MAP で --a-* へ結ぶ。
    TERM の区画（:root はダーク）より後ろに置く＝同じ詳細度で後ろが勝つ。部屋の窓（.t-dark）は TERM のダークのまま。"""
    lines = [APP_BEGIN, ":root {"]
    lines += [f"  --a-{k}: {vals[k]};" for k in APP_KEYS]
    lines.append(f"  --font-ui: {font};")
    lines.append("  --t-sans: var(--font-ui);")
    lines += [f"  --t-{t}: var(--a-{a});" for t, a in APP_TERM_MAP]
    lines += ["  color-scheme: light;", "}", APP_END]
    return "\n".join(lines)


def render_hud_css():
    """トークンのブロックを各画面へ展開。--check時は書き込まず差分を検知。"""
    block = HUD_BEGIN + "\n" + HUD_SOURCE.read_text(encoding="utf-8").strip() + "\n" + HUD_END
    outputs = {}
    for path in HUD_TARGETS:
        outputs[path] = _inject(path.read_text(encoding="utf-8"), path, HUD_BEGIN, HUD_END, block, "HUD_TOKENS")
    path = ROOT / "ui" / "hud" / "hud.css"
    outputs[path] = render_layers_css(outputs[path])
    term, mono = read_term()
    tblock = render_term_block(term, mono)
    for path in TERM_TARGETS:
        if not path.is_file():
            continue                              # 本番（master）の木にはコックピットが無い＝PWA だけに入れる
        outputs[path] = _inject(outputs.get(path) or path.read_text(encoding="utf-8"), path, TERM_BEGIN, TERM_END,
                                tblock, "TERM_TOKENS")
    ablock_app = render_app_block(*read_app())
    for path in APP_TARGETS:
        outputs[path] = _inject(outputs.get(path) or path.read_text(encoding="utf-8"), path, APP_BEGIN, APP_END,
                                ablock_app, "APP_TOKENS")
    if not ICONS_SOURCE.is_file():
        raise SystemExit("✗ ui/cockpit/icons.css が無い（python3 design/gen_icons.py で生成）")
    iblock = ICONS_BEGIN + "\n" + ICONS_SOURCE.read_text(encoding="utf-8").strip() + "\n" + ICONS_END
    for path in ICONS_TARGETS:
        outputs[path] = _inject(outputs.get(path) or path.read_text(encoding="utf-8"), path, ICONS_BEGIN, ICONS_END,
                                iblock, "PX_ICONS")
    ablock = ACTIVITY_BEGIN + "\n" + ACTIVITY_SOURCE.read_text(encoding="utf-8").strip() + "\n" + ACTIVITY_END
    for path in ACTIVITY_TARGETS:
        outputs[path] = _inject(outputs.get(path) or path.read_text(encoding="utf-8"), path, ACTIVITY_BEGIN, ACTIVITY_END,
                                ablock, "ACTIVITY")
    return outputs


def render_layers_css(css):
    """層の数値も正本から展開し、HUDトークン外のvar()を増やさない。"""
    source = (ROOT / "ui" / "hud" / "layers.js").read_text(encoding="utf-8")
    match = re.search(r"export const Z = Object\.freeze\(\{([^}]+)\}\)", source)
    if not match:
        raise SystemExit("✗ layers.js の Z 表が見つかりません")
    values = dict(re.findall(r"(\w+):\s*(\d+)", match.group(1)))
    if values.keys() != LAYER_SELECTORS.keys():
        raise SystemExit("✗ layers.js と CSS 層の登録が不一致")
    lines = [LAYERS_BEGIN, ".hud {"]
    lines.extend(f"  --z-{key}: {value};" for key, value in values.items())
    lines.append("}")
    lines.extend(f"{LAYER_SELECTORS[key]} {{ z-index: {value}; }}" for key, value in values.items())
    lines.append(LAYERS_END)
    block = "\n".join(lines)
    if LAYERS_BEGIN not in css and LAYERS_END not in css:
        return css.rstrip() + "\n\n" + block + "\n"
    if css.count(LAYERS_BEGIN) != 1 or css.count(LAYERS_END) != 1:
        raise SystemExit("✗ HUD_LAYERS マーカーは1組必要")
    begin, end = css.index(LAYERS_BEGIN), css.index(LAYERS_END) + len(LAYERS_END)
    if end <= begin:
        raise SystemExit("✗ HUD_LAYERS マーカー順が不正")
    return css[:begin] + block + css[end:]


def app_ver(html_blank, build):
    """R102-1: シェルの版（12 桁）。版の置き場を空にしたインライン後の HTML＋\0＋モジュール束の BUILD の SHA-256。
    モジュールだけ直しても版が変わる。配信するバイトは生成時に決まる（実行時に計算しない＝KAT と本番 /app の照合を崩さない）。"""
    return hashlib.sha256((html_blank + "\0" + build).encode("utf-8")).hexdigest()[:12]


UIPARTS_BLANK = '<script type=application/json id=uiparts>{}</script>'


def render_uiparts(build, build_3d=""):
    """R102-10: 部屋の部品の版（モジュール束の BUILD）。app.js は bootpx.js を版つきの道 /u/<BUILD>/pwa/bootpx.js で読み、
    部品どうしの相対 import も全部その下で解決される＝版が変わると全部品の URL が変わり、端末に残った古い写しには当たらない。
    本人の iPhone は 8 月（R77〜R79-0）の 1 年キャッシュ（immutable）の頃の /ui/core/world.js を 2 か月後も手元から使い、
    cache:"reload" で取り直しても置き換わらなかった（2026-10-01 実機のログ）。importmap は Playwright の WebKit で読み込みが止まった。"""
    parts = {"b": build}
    if build_3d:
        parts["b3"] = build_3d
    return '<script type=application/json id=uiparts>' + json.dumps(parts, separators=(",", ":")) + '</script>'


def render_app_html(hud_css, build="", build_3d=""):
    """ローカル配信用の参照タグをインライン化する。空白・改行もそのまま保持。
    R102-1: `<html data-build="">` に版（app_ver）を入れ、同じ値を APP_VER として書き出す（Worker が status に載せる）。
    R102-10: `<script type=application/json id=uiparts>{}</script>` を部屋の部品の一覧と版で埋める（build が無ければ空のまま）。"""
    pwa = ROOT / "ui" / "pwa"
    html = (pwa / "app.html").read_bytes().decode("utf-8")
    for name, placeholder, tag in (
        ("app.css", '<link rel="stylesheet" href="./app.css" data-pwa-inline>', "style"),
        ("app.js", '<script src="./app.js" data-pwa-inline></script>', "script"),
    ):
        if html.count(placeholder) != 1:
            raise SystemExit(f"✗ app.html の {name} プレースホルダは1個必要")
        content = hud_css[pwa / name] if name == "app.css" else (pwa / name).read_bytes().decode("utf-8")
        html = html.replace(placeholder, f"<{tag}>{content}</{tag}>")
    if html.count(UIPARTS_BLANK) != 1:
        raise SystemExit("✗ app.html の部屋の部品の一覧の置き場（" + UIPARTS_BLANK + "）は1個必要")
    if build:
        html = html.replace(UIPARTS_BLANK, render_uiparts(build, build_3d), 1)
    if html.count('data-build=""') != 1:
        raise SystemExit('✗ app.html の版の置き場（<html data-build="">）は1個必要')
    ver = app_ver(html, build)
    html = html.replace('data-build=""', f'data-build="{ver}"', 1)
    return (
        "// 自動生成: tools/gen_pwa_modules.py（手で編集しない）\n"
        "// ui/pwa/app.html・app.css・app.js をバイトを変えずインライン化（版の置き場 data-build だけ埋める）。\n"
        f"export const APP_HTML = {json.dumps(html, ensure_ascii=False)};\n"
        f"export const APP_VER = {json.dumps(ver)};\n"
    )


def main(argv):
    # 知らない引数（--help・打ち間違い）では何も書かずに止まる（引数を見ずに生成物を書き出していた＝使い方を見るつもりの
    # 実行が relay/src を書き換え、並走中の wrangler dev を再読み込みさせた）
    unknown = [a for a in argv if a != "--check"]
    if unknown:
        print(__doc__)
        print(f"✗ 知らない引数: {' '.join(unknown)}（何も書いていない）")
        return 2
    hud_css = render_hud_css()
    mods = collect()
    if "/ui/cockpit/room.js" not in mods:
        raise SystemExit("✗ PWAの部屋（/ui/cockpit/room.js）が閉包に無い")
    three_d = [url for url in mods if url.startswith(("/ui/iso", "/ui/vendor/three"))]
    if three_d:
        raise SystemExit(f"✗ PWAに3Dが混ざった（R99-P4-6 で外した）: {three_d[:5]}")
    assets = collect_assets()
    body = render(mods, assets)
    mods_3d = collect(ENTRIES_3D, versioned=True)
    if "/ui/iso/scene3d.js" not in mods_3d or "/ui/vendor/three/three.module.min.js" not in mods_3d:
        raise SystemExit("✗ 3D の束に scene3d.js / three.js が無い")
    assets_3d = collect_assets(ASSET_DIRS_3D)
    body_3d = render(mods_3d, assets_3d, "3D・遅延読込")
    app_body = render_app_html(hud_css, build_id(mods, assets), build_id(mods_3d, assets_3d))
    kb = len(body.encode("utf-8")) // 1024
    if "--check" in argv:
        drift = False
        for path, expected in [*hud_css.items(), (OUT, body), (OUT_3D, body_3d), (APP_OUT, app_body)]:
            if not path.is_file():
                print(f"✗ {path.name} が無い（python3 tools/gen_pwa_modules.py で生成）")
                drift = True
            elif path.read_bytes() != expected.encode("utf-8"):
                print(f"✗ {path.name} が ui/ と不一致（再生成してコミット）")
                drift = True
        if drift:
            return 1
        print(f"✓ relay/src/modules_data.js は最新（JS {len(mods)}本＋アセット {len(assets)}個・{kb}KB）")
        print(f"✓ relay/src/modules_3d.js は最新（JS {len(mods_3d)}本＋アセット {len(assets_3d)}個・{len(body_3d.encode('utf-8')) // 1024}KB）")
        print("✓ relay/src/app_html.js は最新（ui/pwa/app.html・app.css・app.js）")
        print("✓ HUDトークンは最新（ui/hud-tokens.css → ui/hud/hud.css）")
        print("✓ 端末の配色は最新（palette.js の TERM → ui/pwa/app.css・ui/cockpit/style.css）・アイコンも最新")
        print("✓ スマホの明るい配色は最新（ui/app/style.css の --a-*・--font-ui → ui/pwa/app.css・コントラストの下限つき）")
        print("✓ HUD層は最新（ui/hud/layers.js）")
        print("✓ 作業ビューは最新（ui/hud/activity.css → ui/cockpit/style.css・ui/pwa/app.css・ui/app/style.css）")
        return 0
    for path, css in hud_css.items():
        path.write_text(css, encoding="utf-8")
    OUT.write_text(body, encoding="utf-8")
    OUT_3D.write_text(body_3d, encoding="utf-8")
    APP_OUT.write_bytes(app_body.encode("utf-8"))
    print(f"✓ 生成: relay/src/modules_data.js（JS {len(mods)}本＋アセット {len(assets)}個・{kb}KB）")
    print(f"✓ 生成: relay/src/modules_3d.js（JS {len(mods_3d)}本＋アセット {len(assets_3d)}個）")
    print("✓ 生成: relay/src/app_html.js")
    for url in mods:
        print(f"    {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
