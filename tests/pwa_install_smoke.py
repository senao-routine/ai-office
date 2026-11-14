#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P4-7: スマホの PWA が「ホーム画面のアプリ」として名乗れることを、中継の**実配信**で確かめる（stdlib のみ）。

使い方: python3 tests/pwa_install_smoke.py <base_url>      （relay_e2e.sh が wrangler dev に向けて呼ぶ）

なぜ要るか（2026-09-26 の棚卸しで実測した穴）:
  - アイコンが絵文字の SVG の data URI だった。iOS のホーム画面は PNG しか使わない（apple-touch-icon も SVG だった）。
  - start_url が /app で、SW（/app/sw.js）の範囲 /app/ の外。範囲外のページでは navigator.serviceWorker.ready が
    永久に解決せず、🔔（Web Push の購読）が押しても何も起きない。
  - 色が 3 通り（マニフェスト #23213a・meta #f4f4fb・アイコンの地 #241f18）、名前が 2 通り。

見るもの（1 つでも違えば exit 1）:
  1. /app/ が 200 のシェル。/app は /app/ へ 308（古いリンク・QR の /app#… は、ブラウザがフラグメントを引き継いで届く）
  2. マニフェストの start_url・scope が SW の範囲（register の scope・無指定なら sw.js のディレクトリ）に入る。
     id は start_url と同じオリジン（範囲の中は求めない＝統括の裁定で id は "/app"・旧 WebAPK と同じアプリのまま）
  3. マニフェストの各アイコンが 200・image/png・PNG の IHDR の寸法が sizes と一致・ETag 付き・If-None-Match で 304。
     maskable が 1 枚以上ある
  4. apple-touch-icon の href が PNG で 200・180×180
  5. 名前が 1 つ（name・short_name・apple-mobile-web-app-title・<title>）
  6. 色は明暗 2 つ（R101: theme_color・background_color・暗の meta・各アイコンの四隅＝ダークの地／明の meta＝ライトの地）
"""
import json
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):   # 308 をそのまま見る（追いかけない）
        return None


_OPEN = urllib.request.build_opener(_NoRedirect)


def fetch(url, headers=None):
    """(status, headers(dict・小文字), body bytes)。3xx/4xx も例外にしない。"""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with _OPEN.open(req, timeout=15) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


class _Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metas, self.links, self.title, self._in_title = [], [], "", False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def png_info(data):
    """PNG を読む（stdlib）。(幅, 高さ, 四隅の RGB) を返す。読めなければ ValueError。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG の署名ではない")
    pos, ihdr, plte, idat = 8, None, None, b""
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if not ihdr:
        raise ValueError("IHDR が無い")
    w, h, depth, ctype, _, _, interlace = ihdr
    if depth != 8 or interlace != 0 or ctype not in (2, 3, 6):
        raise ValueError(f"想定外の形式（depth={depth} type={ctype} interlace={interlace}）")
    bpp = {2: 3, 3: 1, 6: 4}[ctype]
    raw, stride, rows, prev = zlib.decompress(idat), w * bpp, [], bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line

    def px(x, y):
        v = rows[y][x * bpp:(x + 1) * bpp]
        return plte[v[0]] if ctype == 3 else tuple(v[:3])
    return w, h, [px(0, 0), px(w - 1, 0), px(0, h - 1), px(w - 1, h - 1)]


def hexrgb(s):
    s = (s or "").strip().lower()
    if len(s) == 7 and s.startswith("#"):
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
    return None


def main(argv):
    if len(argv) != 2:
        print("使い方: pwa_install_smoke.py <base_url>")
        return 2
    base = argv[1].rstrip("/")
    ng = []

    def ok(msg):
        print(f"  ✓ {msg}")

    # 1. シェルの URL（正は /app/・/app は 308 で寄せる）
    st, hd, body = fetch(base + "/app/")
    html = body.decode("utf-8", "replace")
    if st == 200 and "text/html" in hd.get("content-type", "") and "AI Office" in html:
        ok("P4-7 /app/ が 200 のシェル")
    else:
        ng.append(f"/app/ がシェルを返さない（{st} {hd.get('content-type')}）")
    st2, hd2, _ = fetch(base + "/app?x=1")
    loc = hd2.get("location", "")
    if st2 in (301, 308) and urllib.parse.urljoin(base + "/app", loc) == base + "/app/?x=1":
        ok(f"P4-7 /app は {st2} で /app/ へ（クエリを保つ・フラグメントはブラウザが引き継ぐ）")
    else:
        ng.append(f"/app が /app/ へ寄らない（{st2} Location={loc!r}）＝範囲外のページが SW に制御されず ready が解決しない")

    head = _Head()
    head.feed(html)

    # 2. SW の範囲とマニフェストの URL
    m = re.search(r'serviceWorker\.register\("([^"]+)"(?:\s*,\s*\{\s*scope\s*:\s*"([^"]+)"\s*\})?\)', html)
    if not m:
        ng.append("シェルに serviceWorker.register(...) が無い")
        sw_scope = None
    else:
        sw_url = urllib.parse.urljoin(base + "/app/", m.group(1))
        sw_st, sw_hd, _ = fetch(sw_url)
        max_scope = urllib.parse.urljoin(sw_url, sw_hd.get("service-worker-allowed") or "./")
        sw_scope = urllib.parse.urljoin(base + "/app/", m.group(2)) if m.group(2) else urllib.parse.urljoin(sw_url, "./")
        if sw_st == 200 and "javascript" in sw_hd.get("content-type", "") and sw_scope.startswith(max_scope):
            ok(f"P4-7 SW {urllib.parse.urlparse(sw_url).path} が 200・範囲 {urllib.parse.urlparse(sw_scope).path}")
        else:
            ng.append(f"SW が配られない／範囲が許容外（{sw_st} {sw_hd.get('content-type')} scope={sw_scope} max={max_scope}）")

    man_links = [ln for ln in head.links if ln.get("rel") == "manifest"]
    if len(man_links) != 1:
        ng.append(f"<link rel=manifest> が 1 本でない（{len(man_links)}）")
        return _finish(ng)
    man_url = urllib.parse.urljoin(base + "/app/", man_links[0]["href"])
    st, hd, body = fetch(man_url)
    try:
        man = json.loads(body.decode("utf-8"))
    except ValueError:
        ng.append(f"マニフェストが JSON でない（{st}）")
        return _finish(ng)
    if st == 200 and "manifest+json" in hd.get("content-type", "") and man.get("display") == "standalone":
        ok("P4-7 マニフェストが 200・application/manifest+json・standalone")
    else:
        ng.append(f"マニフェストの配信が違う（{st} {hd.get('content-type')} display={man.get('display')}）")
    start = urllib.parse.urljoin(man_url, man.get("start_url") or "")
    scope = urllib.parse.urljoin(man_url, man.get("scope") or "./")
    if sw_scope and start.startswith(sw_scope) and start.startswith(scope) and scope.startswith(sw_scope):
        ok(f"P4-7 start_url・scope が SW の範囲 {urllib.parse.urlparse(sw_scope).path} に入る"
           f"（{man.get('start_url')}・{man.get('scope')}）")
    else:
        ng.append(f"マニフェストの URL が SW の範囲の外（start_url={man.get('start_url')!r} scope={man.get('scope')!r} "
                  f"SW の範囲={sw_scope}）＝ready が解決しない・インストールの条件を満たさない")
    # id（統括の裁定＝"/app"）: 識別子なので範囲の中は求めない。W3C の仕様どおり start_url のオリジンで解決し、同じオリジンで
    # あること（違うオリジンの id はブラウザが捨てて start_url に戻す＝旧 WebAPK との継続性が黙って切れる）。
    ident = urllib.parse.urljoin(start, man.get("id") or start)
    same_origin = lambda a, b: urllib.parse.urlparse(a)[:2] == urllib.parse.urlparse(b)[:2]   # noqa: E731
    if man.get("id") and same_origin(ident, start):
        ok(f"P4-7 id {man.get('id')!r} が start_url と同じオリジン（{ident}）")
    else:
        ng.append(f"マニフェストの id が無い／start_url と別のオリジン（id={man.get('id')!r} → {ident}・start_url={start}）")

    # 3. マニフェストのアイコン（PNG・宣言どおりの寸法・ETag）
    colors = {}
    icons = man.get("icons") or []
    purposes = set()
    for ic in icons:
        src = urllib.parse.urljoin(man_url, ic.get("src") or "")
        purposes.update((ic.get("purpose") or "any").split())
        why = _check_png(src, ic.get("sizes", ""), ic.get("type"), colors)
        if why:
            ng.append(f"マニフェストのアイコン {ic.get('src', '')[:60]}: {why}")
    if icons and not any("アイコン" in x for x in ng):
        ok(f"P4-7 マニフェストのアイコン {len(icons)} 枚が 200・image/png・IHDR の寸法が sizes どおり・ETag で 304")
    if "maskable" in purposes and "any" in purposes and {"192x192", "512x512"} <= {ic.get("sizes") for ic in icons}:
        ok("P4-7 any（192・512）と maskable がそろう")
    else:
        ng.append(f"アイコンの組が足りない（purpose={sorted(purposes)} sizes={[ic.get('sizes') for ic in icons]}）")

    # 4. apple-touch-icon（iOS のホーム画面は PNG しか使わない）
    apple = [ln for ln in head.links if ln.get("rel") == "apple-touch-icon"]
    if len(apple) != 1:
        ng.append(f"<link rel=apple-touch-icon> が 1 本でない（{len(apple)}）")
    else:
        src = urllib.parse.urljoin(base + "/app/", apple[0].get("href", ""))
        why = _check_png(src, "180x180", "image/png", colors)
        if why:
            ng.append(f"apple-touch-icon {apple[0].get('href', '')[:60]}: {why}")
        else:
            ok("P4-7 apple-touch-icon が PNG で 200・180×180")

    # 5. 名前は 1 つ
    names = {"name": man.get("name"), "short_name": man.get("short_name"),
             "apple-mobile-web-app-title": next((mt.get("content") for mt in head.metas
                                                 if mt.get("name") == "apple-mobile-web-app-title"), None),
             "<title>": head.title.strip()}
    if len(set(names.values())) == 1 and names["name"]:
        ok(f"P4-7 名前が 1 つ（{names['name']}）")
    else:
        ng.append(f"名前が割れている: {names}")

    # 6. 色は明暗 2 つ（R101: 端末の地）＝マニフェスト・暗の meta・アイコンの地はダークの地、明の meta はライトの地
    metas = [mt for mt in head.metas if mt.get("name") == "theme-color"]
    want = {"theme_color": man.get("theme_color"), "background_color": man.get("background_color")}
    light = {}
    for mt in metas:
        key = "meta theme-color" + (f"[{mt['media']}]" if mt.get("media") else "")
        (light if "prefers-color-scheme: light" in (mt.get("media") or "") else want)[key] = mt.get("content")
    rgbs = {k: hexrgb(v) for k, v in want.items()}
    rgbs.update(colors)
    lrgbs = {k: hexrgb(v) for k, v in light.items()}
    if (metas and light and None not in rgbs.values() and len(set(rgbs.values())) == 1
            and None not in lrgbs.values() and len(set(lrgbs.values())) == 1 and set(lrgbs.values()) != set(rgbs.values())):
        ok(f"R101 色は明暗 2 つ（暗 {man.get('theme_color')}＝マニフェスト・アイコン {len(colors)} 枚の地／明 {next(iter(light.values()))}）")
    else:
        ng.append(f"色が割れている／明の meta が無い: 暗={ {k: v for k, v in rgbs.items()} } 明={lrgbs}")
    return _finish(ng)


def _check_png(src, sizes, typ, colors):
    """1 枚のアイコンを取って確かめる。失敗の理由（成功なら空）。四隅の色を colors に積む。"""
    if not src.startswith(("http://", "https://")):
        return f"URL ではない（{src[:40]}…）＝iOS・Android のホーム画面に載らない"
    if typ != "image/png":
        return f"type が image/png でない（{typ}）"
    st, hd, body = fetch(src)
    if st != 200 or hd.get("content-type") != "image/png":
        return f"{st} {hd.get('content-type')}"
    try:
        w, h, corners = png_info(body)
    except (ValueError, zlib.error, struct.error, IndexError) as e:
        return f"PNG として読めない（{e}）"
    if f"{w}x{h}" != sizes:
        return f"IHDR {w}x{h} ≠ 宣言 {sizes}"
    et = hd.get("etag")
    if not et:
        return "ETag が無い"
    st304, _, b304 = fetch(src, {"If-None-Match": et})
    if st304 != 304 or b304:
        return f"If-None-Match で 304 にならない（{st304}）"
    if len(set(corners)) != 1:
        return f"四隅の色が揃わない（地が 1 色でない: {corners}）"
    colors[urllib.parse.urlparse(src).path] = corners[0]
    return ""


def _finish(ng):
    for x in ng:
        print(f"  ✗ P4-7 {x}")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
