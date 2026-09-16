#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R77: スマホPWAの3Dシーン用に ui/ のESMを Worker へ同梱する（sprites_data.js と同じ型）。

なぜ必要か: PWAは Cloudflare Worker が配信する＝Macの 127.0.0.1 には届かない。
デスクトップと同じ three.js シーンをスマホで動かすには、モジュール一式を Worker が
`/ui/...` の**同じパス**で返す必要がある（import 指定子を書き換えないための条件）。

生成物: relay/src/modules_data.js / app_html.js（git追跡必須）。
ui/hud-tokens.css の共通トークンを ui/iso/style.css と ui/pwa/app.css にも展開する。
使い方:
    python3 tools/gen_pwa_modules.py           # 生成
    python3 tools/gen_pwa_modules.py --check   # ui/ との一致（ドリフト検知・verify用）
"""
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "relay" / "src" / "modules_data.js"
APP_OUT = ROOT / "relay" / "src" / "app_html.js"
HUD_SOURCE = ROOT / "ui" / "hud-tokens.css"
HUD_TARGETS = [ROOT / "ui" / "iso" / "style.css", ROOT / "ui" / "pwa" / "app.css"]
HUD_BEGIN = "/* HUD_TOKENS_BEGIN (generated: ui/hud-tokens.css) */"
HUD_END = "/* HUD_TOKENS_END */"
LAYERS_BEGIN = "/* HUD_LAYERS_BEGIN (generated: ui/hud/layers.js) */"
LAYERS_END = "/* HUD_LAYERS_END */"
LAYER_SELECTORS = {
    "tray": ".ui-iso #attn.tray, .ui-iso #viewreset",
    "sheet": ".ui-iso .sheet, .ui-iso .rail-open .rail",
    "toast": ".ui-iso .toast, .ui-iso .rail-toggle",
    "boss": ".ui-iso .boss-onboarding",
    "modal": ".ui-iso .modalwrap",
    "offbar": ".ui-iso .offbar",
    "consent": ".ui-iso .sound-consent",
    "stream": ".ui-iso #stream-subtitle, .ui-iso #stream-attention",
    "digest": ".ui-iso .digest-card, .ui-iso .replay-controls",
}

# 入口＝3Dシーンとワールド構築。ここから import を辿って閉包を作る
# R87: 封書の入口は 3D と独立（リスト表示のままでも会話を読めるようにする）。
ENTRIES = ["/ui/pwa/boot3d.js", "/ui/pwa/dlg.js"]   # boot3dから方向Cのui/isoだけを辿る。
# JSからURLで読む静的アセット（importでは辿れない）。3Dシーンのテクスチャ一式。
ASSET_DIRS = ["ui/iso/tex"]
ASSET_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}

# import 指定子: 絶対(/ui/...) と 相対(./x.js) の両方
_ABS = re.compile(r'from\s*"(/ui/[^"]+)"')
_REL = re.compile(r'from\s*"(\./[^"]+)"')


def _read(url):
    path = ROOT / url.lstrip("/")
    if not path.is_file():
        raise SystemExit(f"✗ 依存が見つからない: {url}")
    return path.read_text(encoding="utf-8")


GEN_DIR = "/ui/iso/gen/"
GEN_MANIFEST = ROOT / "ui" / "iso" / "gen" / "manifest.json"
GEN_STUB = "// PWA には同梱しない（ui/iso/gen/manifest.json の pwa:false）＝procedural へフォールバック\nexport default null;\n"


def _pwa_gen_names():
    """R96-D: 生成什器のうち PWA に同梱する name の集合（manifest の pwa:true）。"""
    if not GEN_MANIFEST.is_file():
        return set()
    manifest = json.loads(GEN_MANIFEST.read_text(encoding="utf-8"))
    items = manifest.get("items", {})
    names = {name for name, meta in items.items() if meta.get("pwa")}
    names.update(manifest.get("rig", {}).get("pwa", []))   # R96-D2: リグ付きロボ（body/clips）
    return names


def _maybe_stub(url, src):
    """生成什器モジュールは manifest で選んだ物だけ本物を同梱し、残りは null に差し替える（import 図は不変）。"""
    if not url.startswith(GEN_DIR) or url == GEN_DIR + "index.js":
        return src
    name = url[len(GEN_DIR):-3]
    return src if name in _pwa_gen_names() else GEN_STUB


def collect():
    """入口から import を辿って {url: source} を作る（テストは含めない）。"""
    seen = {}
    stack = list(ENTRIES)
    while stack:
        url = stack.pop()
        if url in seen:
            continue
        src = _read(url)
        seen[url] = _maybe_stub(url, src)
        base = url.rsplit("/", 1)[0]
        for dep in _ABS.findall(src):
            stack.append(dep)
        for dep in _REL.findall(src):
            stack.append(f"{base}/{dep[2:]}")
    return dict(sorted(seen.items()))


def collect_assets():
    """URL読みのテクスチャを base64 で同梱（importで辿れないので明示収集）。"""
    out = {}
    for d in ASSET_DIRS:
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


def render(mods, assets):
    lines = [
        "// 自動生成: tools/gen_pwa_modules.py（手で編集しない）",
        "// PWAの3Dシーン用ESM。Worker が /ui/... の同じパスで返す＝import指定子を書き換えない。",
        "export const MODULES = Object.assign(Object.create(null), {",
    ]
    lines.insert(2, f"export const BUILD = {json.dumps(build_id(mods, assets))};   // 内容ハッシュ＝ETagの版")
    lines.insert(3, "")
    for url, src in mods.items():
        lines.append(f"  {json.dumps(url)}: {json.dumps(src)},")
    lines.append("});")
    lines.append("")
    lines.append("// テクスチャ（base64）。Worker が /ui/iso*/tex/... で返す")
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


def render_hud_css():
    """同じトークンブロックを両画面へ展開。--check時は書き込まず差分を検知。"""
    block = HUD_BEGIN + "\n" + HUD_SOURCE.read_text(encoding="utf-8").strip() + "\n" + HUD_END
    outputs = {}
    for path in HUD_TARGETS:
        css = path.read_text(encoding="utf-8")
        if css.count(HUD_BEGIN) != 1 or css.count(HUD_END) != 1:
            raise SystemExit(f"✗ {path.relative_to(ROOT)} の HUD_TOKENS マーカーは1組必要")
        begin, end = css.index(HUD_BEGIN), css.index(HUD_END) + len(HUD_END)
        if end <= begin:
            raise SystemExit(f"✗ {path.relative_to(ROOT)} の HUD_TOKENS マーカー順が不正")
        outputs[path] = css[:begin] + block + css[end:]
    path = ROOT / "ui" / "iso" / "style.css"
    outputs[path] = render_layers_css(outputs[path])
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
    lines = [LAYERS_BEGIN, ".ui-iso {"]
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


def render_app_html(hud_css):
    """ローカル配信用の参照タグをインライン化する。空白・改行もそのまま保持。"""
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
    return (
        "// 自動生成: tools/gen_pwa_modules.py（手で編集しない）\n"
        "// ui/pwa/app.html・app.css・app.js をバイトを変えずインライン化。\n"
        f"export const APP_HTML = {json.dumps(html, ensure_ascii=False)};\n"
    )


def main(argv):
    hud_css = render_hud_css()
    mods = collect()
    if "/ui/iso/scene3d.js" not in mods or any(re.match(r"/ui/iso[^/]+/", url) for url in mods):
        raise SystemExit("✗ PWAのシーンは /ui/iso/ だけを同梱する")
    assets = collect_assets()
    body = render(mods, assets)
    app_body = render_app_html(hud_css)
    kb = len(body.encode("utf-8")) // 1024
    if "--check" in argv:
        drift = False
        for path, expected in [*hud_css.items(), (OUT, body), (APP_OUT, app_body)]:
            if not path.is_file():
                print(f"✗ {path.name} が無い（python3 tools/gen_pwa_modules.py で生成）")
                drift = True
            elif path.read_bytes() != expected.encode("utf-8"):
                print(f"✗ {path.name} が ui/ と不一致（再生成してコミット）")
                drift = True
        if drift:
            return 1
        print(f"✓ relay/src/modules_data.js は最新（JS {len(mods)}本＋tex {len(assets)}枚・{kb}KB）")
        print("✓ relay/src/app_html.js は最新（ui/pwa/app.html・app.css・app.js）")
        print("✓ HUDトークンは両画面で最新（ui/hud-tokens.css）")
        print("✓ HUD層は最新（ui/hud/layers.js）")
        return 0
    for path, css in hud_css.items():
        path.write_text(css, encoding="utf-8")
    OUT.write_text(body, encoding="utf-8")
    APP_OUT.write_bytes(app_body.encode("utf-8"))
    print(f"✓ 生成: relay/src/modules_data.js（JS {len(mods)}本＋tex {len(assets)}枚・{kb}KB）")
    print("✓ 生成: relay/src/app_html.js")
    for url in mods:
        print(f"    {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
