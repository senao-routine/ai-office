#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIオフィス用アセット生成（GPT Image 2.0・スプライトは透過背景）
使い方: python assets_gen.py <ジョブ名...>       ジョブ名=all / bg / works_hq / blog / ...
       python assets_gen.py custom <slug> <ラベル>  # ➕新プロジェクト用（立ち絵+歩き絵の2枚）
       python assets_gen.py walkframes [名前...]    # 歩きフレームだけ再生成
キー: OPENAI_API_KEY（環境変数 → works/.env(SSOT・読めるとき=devのTerminal) →
     ~/.claude/office_secrets の順に自動探索。works/.env は Downloads=TCC保護なので
     launchd常駐(FDA無し)からは読めず OSError → office_secrets へフォールバックする。
     office_secrets は install.sh が works/.env から一度だけシードする（daemon用の写し）
"""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

# requests は OpenAI API 直叩き（gen/gen_furniture/gen_walk）でのみ必要。
# theme_gen（Codexレーン）は chroma_key/pad_to_canvas 等のヘルパーだけを import するため、
# requests 不在の python（デーモンのシステムpython等）でも module import は通す（2026-07-16 実障害）。
try:
    import requests
except ImportError:
    requests = None

HERE = Path(__file__).resolve().parent
# OFFICE_DATA 追従（P4常駐: 生成spriteの書込先を office_server と同じ data/assets に揃える）。
# env 未指定でも常駐インストール済みなら data/ を自動採用（手動再生成が repo/assets へ書いて
# サーバーから見えなくなる事故を防ぐ。officectl.sh の切替と同じ既定）
_DATA_DEFAULT = Path.home() / "Library" / "Application Support" / "AIOffice" / "data"
ASSETS = Path(os.environ.get("OFFICE_DATA")
              or (_DATA_DEFAULT if _DATA_DEFAULT.is_dir() else HERE.parent)) / "assets"
WORKS_ENV = HERE.parents[1] / ".env"
REPO_ASSETS = HERE.parent / "assets"   # repo正本（app/ deploy-copyには存在しない→同期は自動スキップ）


def _sync_repo(filenames):
    """生成先がOFFICE_DATA(data側)のとき、標準アセットを repo assets/（SSOT）へ自動同期し、
    PWA同梱索引（relay/src/sprites_data.js）も再生成する。cp忘れ＝PWAスプライト欠落・
    クリーンclone破壊の根を断つ（2026-07-11・手動cpルールの自動化）。
    custom キャラ（利用者ランタイムデータ）は呼び出し側で対象外にしている。"""
    if not REPO_ASSETS.is_dir():
        return                                # app/ 配下から実行＝repoが無い環境
    try:
        if REPO_ASSETS.resolve() == ASSETS.resolve():
            return                            # 生成先がすでにrepo（未インストール環境）
    except OSError:
        return
    import shutil
    copied = []
    for fn in filenames:
        src = ASSETS / fn
        dst = REPO_ASSETS / fn
        if not src.is_file():
            continue
        # 生成失敗時の stale 上書きガード: repo側が新しい（別Macでpull済み等）なら写さない。
        # copy2 は mtime を保存するので「今回生成した絵」だけが repo より新しくなる
        if dst.exists() and src.stat().st_mtime <= dst.stat().st_mtime:
            continue
        try:
            shutil.copy2(src, dst)
            copied.append(fn)
        except OSError as e:
            print(f"⚠ repo同期失敗 {fn}: {e}（手動で cp してから verify ▶3b を確認）",
                  file=sys.stderr, flush=True)
    if not copied:
        return
    print(f"↻ repo assets/ へ自動同期: {' '.join(copied)}", flush=True)
    import subprocess
    r = subprocess.run([sys.executable, str(HERE / "gen_pwa_sprites.py")])
    if r.returncode == 0:
        print("↻ relay/src/sprites_data.js 再生成済み（git commit を忘れずに・検証= verify ▶3b）",
              flush=True)
    else:
        print("⚠ gen_pwa_sprites.py 失敗 → 手動再生成してから verify ▶3b を確認",
              file=sys.stderr, flush=True)

STYLE = ("retro 16-bit pixel art game sprite, cute chibi office worker about 2.5 heads tall, "
         "front facing, standing straight, full body centered, big expressive eyes, rosy cheeks, "
         "clean thick dark-brown outline, limited warm palette (cream, amber gold #b9791a, "
         "teal #2f6f68, terracotta, wood brown), crisp large pixels, no anti-aliasing, "
         "no text, no watermark, single character only, isolated on a plain flat solid "
         "bright magenta background (#FF00FF), the character itself contains no magenta or pink colors")

JOBS = {
    "bg": {
        "size": "1536x1024", "transparent": False, "slim": True,
        "prompt": ("retro 16-bit pixel art office room, top-down slightly angled 3/4 view, "
                   "cozy warm palette (cream, amber gold, teal, terracotta, wood browns). "
                   "LEFT TWO THIRDS: wooden plank floor work area with exactly 12 identical empty desks "
                   "arranged in a neat grid of 3 rows and 4 columns, each desk has a small computer monitor "
                   "on top and an empty chair placed ABOVE/BEHIND the desk (so a character can stand there), "
                   "generous even spacing. TOP EDGE: brown brick wall with two bookshelves full of colorful "
                   "books, two bright windows, one blank dark wooden signboard centered, a round wall clock. "
                   "RIGHT TOP QUARTER: glass-walled meeting room with one large wooden oval table, empty chairs "
                   "around it, a whiteboard on the wall, muted teal carpet. RIGHT BOTTOM QUARTER: break lounge "
                   "with two terracotta-red sofas facing a low wooden coffee table, a water cooler, potted plants, "
                   "black-and-cream checkered tile floor strip along the bottom, a doorway with welcome mat "
                   "between work area and lounge. Absolutely NO people, NO characters, NO text, NO letters, "
                   "NO logos. clean crisp pixels, game background asset")},
    "works_hq": {"prompt": STYLE + ", female studio director, long chestnut hair, small red beret, gold scarf, holding a clipboard"},
    "blog":     {"prompt": STYLE + ", young woman editor, round glasses, neat hair bun, teal cardigan, holding an open notebook and pen"},
    "video":    {"prompt": STYLE + ", young man video editor, big black headphones on ears, holding a small film clapperboard"},
    "shorts":   {"prompt": STYLE + ", energetic girl, black twin-tail hair with red ribbons, holding a smartphone on a mini tripod"},
    "xrun":     {"prompt": STYLE + ", young man, blue baseball cap, a tiny blue bird perched on his shoulder, holding a small megaphone"},
    "sakutto":  {"prompt": STYLE + ", hoodie-wearing software developer man, messy brown hair, holding a wrench and a small laptop"},
    "memo":     {"prompt": STYLE + ", serious young man in navy business suit with necktie, holding a memo pad and pencil"},
    "ribbon":   {"prompt": STYLE + ", professional businesswoman in dark blazer, elegant, big decorative red knot ribbon brooch on chest, holding a document folder"},
    "xpost":    {"prompt": STYLE + ", cheerful salesman, short black hair, carrying a briefcase and a rolled-up poster under his arm"},
    "generic_f": {"prompt": STYLE + ", female office worker, chin-length bob hair, plum colored blouse, holding a coffee mug"},
    "generic_m": {"prompt": STYLE + ", male office worker, short dark hair, forest green shirt, holding some papers"},
    "generic_f2": {"prompt": STYLE + ", young woman with a high ponytail, mint hoodie, sneakers, energetic"},
    "generic_m2": {"prompt": STYLE + ", young man with curly dark hair, casual white tee and chinos, friendly"},
    "generic_f3": {"prompt": STYLE + ", woman with round glasses and low bun, lavender blouse, calm professional"},
    "generic_m3": {"prompt": STYLE + ", slim young man with glasses, mustard sweater vest over shirt, studious"},
    "generic_f4": {"prompt": STYLE + ", woman with short pixie cut, denim jacket, creative vibe"},
    "generic_m4": {"prompt": STYLE + ", tall man with tied-back hair, olive shirt, relaxed"},
    "generic_f5": {"prompt": STYLE + ", woman with side braid, coral cardigan, warm smile"},
    "generic_m5": {"prompt": STYLE + ", young man with a gray beanie, plaid flannel shirt, maker vibe"},
}


# ➕新プロジェクトのキャラ: ラベルのハッシュから決定論的に組み立てる（同名なら同じ見た目）
CUSTOM_HAIR = ["short black hair", "chestnut bob hair", "blonde ponytail", "curly dark hair",
               "silver-gray short hair", "auburn medium hair", "navy-blue short hair",
               "dark hair in a small topknot"]
CUSTOM_WEAR = ["mustard yellow cardigan", "teal hoodie", "terracotta work apron", "navy blazer",
               "olive utility shirt", "plum sweater vest", "amber striped shirt", "denim jacket"]
CUSTOM_ITEM = ["holding a small laptop", "holding a clipboard", "holding a steaming coffee mug",
               "holding a tablet device", "holding a small toolbox", "holding a notebook and pen",
               "holding a magnifying glass", "holding a small potted plant"]


def custom_spec(label):
    h = int(hashlib.md5(label.encode("utf-8")).hexdigest(), 16)
    gender = "female" if h % 2 else "male"
    parts = [f"{gender} office worker",
             CUSTOM_HAIR[(h >> 4) % len(CUSTOM_HAIR)],
             CUSTOM_WEAR[(h >> 8) % len(CUSTOM_WEAR)],
             CUSTOM_ITEM[(h >> 12) % len(CUSTOM_ITEM)]]
    return {"prompt": STYLE + ", " + ", ".join(parts), "resize": 256}


def _read_env_key(path):
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return None


def api_key():
    """env → works/.env（SSOT・キーローテーションが即反映。TCC保護なので daemon からは
    OSError→スキップ）→ ~/.claude/office_secrets（非保護＝launchd常駐の置き場）の順。
    office_secrets を先にすると中央.envのローテーション後も旧キーを無言で使い続けるので
    SSOT優先を崩さない。OFFICE_HOME はテスト注入口。"""
    k = os.environ.get("OPENAI_API_KEY")
    if k:
        return k
    home = Path(os.environ.get("OFFICE_HOME", str(Path.home())))
    return (_read_env_key(WORKS_ENV)
            or _read_env_key(home / ".claude" / "office_secrets"))


def slim_bg(path):
    """不透過の大きな背景PNGを256色パレット+optimizeで軽量化（レトロピクセルアート＝実質ロスレス・
    2.7MB→~0.9MB）。生成パイプラインに組み込む＝再生成で肥大が復活しない。"""
    from PIL import Image
    img = Image.open(path).convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=256)
    img.save(path, optimize=True)


def chroma_key(path):
    """マゼンタ背景を透過に（ドット絵なので単純な色距離しきい値で十分）＋余白トリム"""
    from PIL import Image
    img = Image.open(path).convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            # マゼンタ系（赤・青が強く緑が弱い）を抜く
            if r > 150 and b > 150 and g < 110 and abs(r - b) < 90:
                px[x, y] = (0, 0, 0, 0)
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    img.save(path)


def gen(name, spec, key):
    if requests is None:
        print(f"✗ {name}: requests 未導入のpythonではAPI生成不可（pip install requests）", flush=True)
        return False
    body = {"model": "gpt-image-2", "prompt": spec["prompt"], "n": 1,
            "size": spec.get("size", "1024x1024"), "quality": "high"}
    r = requests.post("https://api.openai.com/v1/images/generations",
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json=body, timeout=300)
    if r.status_code != 200:
        print(f"✗ {name}: HTTP {r.status_code} {r.text[:200]}", flush=True)
        return False
    b64 = r.json()["data"][0]["b64_json"]
    out = ASSETS / f"{name}.png"
    out.write_bytes(base64.b64decode(b64))
    if spec.get("transparent", True):
        try:
            chroma_key(out)
            if spec.get("resize"):
                from PIL import Image
                img = Image.open(out)
                if img.height > spec["resize"]:
                    ratio = spec["resize"] / img.height
                    img.resize((int(img.width * ratio), spec["resize"]),
                               Image.NEAREST).save(out)
        except Exception as e:  # PIL無しでも生成物は残す
            print(f"⚠ {name}: 透過処理スキップ ({e})", flush=True)
    if spec.get("slim"):
        try:
            slim_bg(out)
        except Exception as e:
            print(f"⚠ {name}: 軽量化スキップ ({e})", flush=True)
        if out.stat().st_size > 1_200_000:   # verify ▶3 と同じ上限＝生成段でも肥大を失敗扱い
            print(f"✗ {name}: slim後も {out.stat().st_size//1024}KB > 上限（PIL不在?）＝未完成", flush=True)
            return False
    print(f"✓ {name}.png ({out.stat().st_size//1024}KB)", flush=True)
    return True


WALK_PROMPT = ("same exact pixel art character as the input image, identical colors, style, "
               "proportions and held items, but now in a MID-WALK pose: one leg clearly stepped "
               "forward and lifted, arms swinging slightly, body leaning a touch forward. "
               "retro 16-bit pixel art, crisp pixels, no anti-aliasing, single character, "
               "no text, plain flat solid bright magenta background (#FF00FF)")


# ---- 家具スプライト（UI整列対応: 背景に家具を焼き込まず、個別スプライトをDOM配置する） ----
# いずれも既存 bg.png を入力にした images/edits で派生生成＝元絵とスタイル・パレットが揃う。
# デスク等は透過スプライト（マゼンタ背景→chroma_key）。bg2 だけ不透過の空部屋。
FURN_JOBS = {
    # canvas=[W,H]: 生成後にこの透過キャンバスへ下端中央で貼り付け＝再生成しても寸法が決定論的
    # （chroma_keyのbboxクロップは毎回サイズが揺れる。UI側の表示定数(DESK_H等)がこの寸法に依存）
    "bg2": {
        "size": "1536x1024", "transparent": False, "slim": True,
        "prompt": ("same exact pixel art office room as the input image, identical art style, "
                   "colors, walls, windows, bookshelves, signboard, wall clock, whiteboard, "
                   "plants, water cooler, door and welcome mat, identical layout and lighting — "
                   "but with ALL movable furniture removed: no desks, no office chairs, no "
                   "monitors, no oval meeting table, no meeting chairs, no sofas, no coffee "
                   "table. The wooden plank work floor is completely clean and empty, the teal "
                   "meeting room carpet is completely clean and empty, the lounge checkered "
                   "floor area is clean with only the water cooler and plants remaining. "
                   "no text, no characters, crisp pixels")},
    "deskset": {
        "size": "1024x1024", "canvas": [280, 220],
        "prompt": ("draw ONLY one single wooden office desk with a dark computer monitor "
                   "standing on top (monitor back facing the viewer), exactly in the pixel art "
                   "style, colors and slight top-down front angle of the desks in the input "
                   "image. one desk only, complete and not cropped, centered, no chair, "
                   "no character, no text, isolated on a plain flat solid bright magenta "
                   "background (#FF00FF), the desk contains no magenta colors")},
    "deskchair": {
        "size": "1024x1024", "canvas": [80, 130],
        "prompt": ("draw ONLY one single dark-teal office chair seen from behind (backrest "
                   "facing the viewer), exactly in the pixel art style and colors of the office "
                   "chairs in the input image. one chair only, complete and not cropped, "
                   "centered, no desk, no character, no text, isolated on a plain flat solid "
                   "bright magenta background (#FF00FF), the chair contains no magenta colors")},
    "meetset": {
        "size": "1024x1024", "canvas": [460, 300],
        "prompt": ("draw ONLY one large oval wooden meeting table with exactly six dark-teal "
                   "office chairs neatly and evenly tucked around it (two on top, two on the "
                   "bottom, one on each side), exactly in the pixel art style, colors and "
                   "slight top-down angle of the input image. chairs perfectly aligned to the "
                   "table, one complete group, not cropped, centered, no characters, no text, "
                   "isolated on a plain flat solid bright magenta background (#FF00FF), "
                   "the furniture contains no magenta colors")},
    "sofaset": {
        "size": "1024x1024", "canvas": [460, 300],
        "prompt": ("draw ONLY one cozy lounge furniture group: two terracotta-red sofas facing "
                   "each other (left sofa facing right, right sofa facing left) with one low "
                   "wooden coffee table with a small potted plant between them, exactly in the "
                   "pixel art style, colors and slight top-down angle of the input image. "
                   "one complete group, neatly aligned, not cropped, centered, no characters, "
                   "no text, isolated on a plain flat solid bright magenta background (#FF00FF), "
                   "the furniture contains no magenta colors")},
}


def gen_furniture(names, key):
    """bg.png を入力に家具スプライト/空部屋bgを派生生成（images/edits＝スタイル連続）"""
    src = ASSETS / "bg.png"
    if not src.exists():
        print("✗ bg.png が無い（先に bg を生成）", flush=True)
        return 0
    ok = 0
    for name in names:
        spec = FURN_JOBS[name]
        r = requests.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {key}"},
            data={"model": "gpt-image-2", "prompt": spec["prompt"],
                  "size": spec["size"], "quality": "high", "n": "1"},
            files=[("image", ("bg.png", src.read_bytes(), "image/png"))],
            timeout=300)
        if r.status_code != 200:
            print(f"✗ {name}: HTTP {r.status_code} {r.text[:200]}", flush=True)
            continue
        out = ASSETS / f"{name}.png"
        out.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
        if spec.get("transparent", True):
            try:
                chroma_key(out)
                if spec.get("canvas"):
                    pad_to_canvas(out, spec["canvas"])
            except Exception as e:
                # マゼンタ残り/寸法未固定の生成物は「成功」に数えない（UIの表示定数が寸法依存）
                print(f"✗ {name}: 透過/寸法処理に失敗＝未完成として扱う ({e})", flush=True)
                continue
        if spec.get("slim"):
            try:
                slim_bg(out)
            except Exception as e:
                print(f"⚠ {name}: 軽量化スキップ ({e})", flush=True)
            if out.stat().st_size > 1_200_000:
                print(f"✗ {name}: slim後も {out.stat().st_size//1024}KB > 上限＝未完成", flush=True)
                continue   # ok++ しない
        print(f"✓ {name}.png ({out.stat().st_size//1024}KB)", flush=True)
        ok += 1
    return ok


def pad_to_canvas(path, canvas):
    """スプライトを固定サイズ透過キャンバスに下端中央で貼り付け＝再生成しても寸法が変わらない。
    （chroma_keyのbboxクロップは毎回サイズが揺れ、UI側の表示定数との整合が崩れるため）"""
    from PIL import Image
    cw, ch = canvas
    img = Image.open(path).convert("RGBA")
    if img.width > cw or img.height > ch:
        r = min(cw / img.width, ch / img.height)
        img = img.resize((max(1, int(img.width * r)), max(1, int(img.height * r))), Image.NEAREST)
    cnv = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    cnv.paste(img, ((cw - img.width) // 2, ch - img.height))
    cnv.save(path)


def gen_walk(name, key):
    """既存スプライトを入力に「歩きポーズ」フレームを生成（images/edits）"""
    src = ASSETS / f"{name}.png"
    if not src.exists():
        print(f"✗ {name}: 元スプライトなし", flush=True)
        return False
    r = requests.post(
        "https://api.openai.com/v1/images/edits",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": "gpt-image-2", "prompt": WALK_PROMPT,
              "size": "1024x1024", "quality": "high", "n": "1"},
        files=[("image", (f"{name}.png", src.read_bytes(), "image/png"))],
        timeout=300)
    if r.status_code != 200:
        print(f"✗ {name}_walk: HTTP {r.status_code} {r.text[:150]}", flush=True)
        return False
    out = ASSETS / f"{name}_walk.png"
    out.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
    try:
        chroma_key(out)
        from PIL import Image
        img = Image.open(out)
        if img.height > 256:
            ratio = 256 / img.height
            img.resize((int(img.width * ratio), 256), Image.NEAREST).save(out)
    except Exception as e:
        print(f"⚠ {name}_walk: 後処理スキップ ({e})", flush=True)
    print(f"✓ {name}_walk.png ({out.stat().st_size//1024}KB)", flush=True)
    return True


def main():
    try:
        ASSETS.mkdir(exist_ok=True)
    except FileNotFoundError:
        print(f"✗ OFFICE_DATA の親が存在しません: {ASSETS.parent}（install.sh 実行 or OFFICE_DATA を確認）",
              file=sys.stderr)
        sys.exit(1)
    key = api_key()
    if not key:
        print("✗ OPENAI_API_KEY が見つかりません", file=sys.stderr)
        sys.exit(1)
    # 無引数=全JOBS再生成(十数枚・数ドル課金)は誤爆しやすいので明示 "all" を要求する
    if not sys.argv[1:]:
        print(__doc__, file=sys.stderr)
        print("✗ 対象を指定してください（全再生成は明示的に all）", file=sys.stderr)
        sys.exit(1)
    targets = sys.argv[1:]
    if targets[0] == "custom":
        if len(targets) < 3:
            print("使い方: assets_gen.py custom <slug> <ラベル>", file=sys.stderr)
            sys.exit(1)
        slug, label = targets[1], " ".join(targets[2:])
        if not gen(slug, custom_spec(label), key):
            sys.exit(1)
        gen_walk(slug, key)  # 歩き絵の失敗は致命ではない（立ち絵だけで出社できる）
        return
    if targets[0] == "furniture":
        bad = [t for t in targets[1:] if t not in FURN_JOBS]
        if bad:
            print(f"✗ 不明な家具ジョブ: {' '.join(bad)}（候補: {' '.join(FURN_JOBS)}）", file=sys.stderr)
            sys.exit(1)
        req = [t for t in targets[1:] if t in FURN_JOBS] or list(FURN_JOBS)
        n = gen_furniture(req, key)
        print(f"完了: {n}/{len(req)}")
        _sync_repo([f"{t}.png" for t in req])
        if n < len(req):
            sys.exit(1)   # 部分失敗を自動化から検知できるように
        return
    if targets[0] == "walkframes":
        req = targets[1:]
        names = [t for t in req if t in JOBS or (ASSETS / f"{t}.png").exists()]
        if req and not names:
            # 明示指定が全部不明のとき全JOBS再生成($2弱)に化けないよう明示エラー
            print(f"✗ 指定名が見つかりません: {' '.join(req)}", file=sys.stderr)
            sys.exit(1)
        if not req:
            names = [n for n in JOBS if n != "bg"]
        ok = sum(gen_walk(n, key) for n in names)
        print(f"完了: {ok}/{len(names)}")
        # custom スラグ（利用者ランタイムデータ）はrepoへ写さない＝標準キャラ(JOBS)のみ同期
        _sync_repo([f"{n}_walk.png" for n in names if n in JOBS])
        return
    names = list(JOBS) if "all" in targets else [t for t in targets if t in JOBS]
    if not names:
        print(f"✗ 指定名が JOBS にありません: {' '.join(targets)}", file=sys.stderr)
        sys.exit(1)
    if "bg" in names:
        print("⚠ bg を再生成すると bg2/deskset/deskchair/meetset/sofaset は旧bg派生のまま stale になります。"
              "追随するには: python3 tools/assets_gen.py furniture（≈$1.2）", file=sys.stderr)
    ok = 0
    for n in names:
        ok += gen(n, JOBS[n], key)
    print(f"完了: {ok}/{len(names)}")
    _sync_repo([f"{n}.png" for n in names])


if __name__ == "__main__":
    main()
