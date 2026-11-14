#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新UI（R50）の決定論スクリーンショット。

なぜ必要か:
  3Dは目視だけでは退行に気づけない。だが実測の結果、Playwright の headless Chromium は
  SwiftShader（ソフトウェアラスタライザ）にフォールバックし、**同じ入力なら
  ビット一致で同じ絵を返す**（GPU経路だけハッシュが変わる）。
  したがって「バックエンドを SwiftShader に固定 + 時刻と乱数を ?t=&seed= で固定 +
  /api/office を fixture で差し替え」の3点を揃えれば、ピクセル golden が使える。

使い方:
  python3 tools/ui_shot.py --style iso                    # 撮って tests/artifacts/ へ
  python3 tools/ui_shot.py --style iso --t 3.2 --seed 11
  python3 tools/ui_shot.py --update                       # iso の golden を撮り直す
  python3 tools/ui_shot.py --check                        # golden と比較（差分率で判定）
  python3 tools/ui_shot.py --check --exact                # 全画面も許容 0（--update の次の回で決定論を確かめる・同じ回には付けられない）

注意:
  - 見栄えの確認用に実GPUで撮りたいときは --gpu（golden とはハッシュが一致しない）。
  - tools/ は外部依存OKの領域（playwright / Pillow を使う）。server/ の stdlib 縛りとは無関係。
"""
import argparse
import json
import math
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "tests" / "artifacts"
GOLDEN = ROOT / "tests" / "visual" / "golden"
WORLD_FIXTURES = ROOT / "tests" / "fixtures" / "world"

# R90-S1: 方向Cの見た目を iso 1本に統合。
STYLES = ("iso", "pixel", "cockpit", "app")   # R122: 明るい 3 列の画面
VIEWPORT = {"width": 1440, "height": 900}
# 回帰テストは必ずこのバックエンドで撮る（実測: 2回実行でスクショhashが完全一致）
SWIFTSHADER = ["--use-gl=swiftshader", "--disable-gpu"]
GPU = ["--use-angle=metal", "--enable-gpu"]
DIFF_LIMIT = 0.005          # 0.5%（旧scene_diff.py から引き継いだ基準・R52で唯一のビジュアルゲートに）
# 性能ゲート: 素朴に Mesh を並べると 19体で 4700 ドローに達して 60fps が出ない。
# InstancedMesh とジオメトリ統合を外した瞬間にここで落ちる。
DRAW_CALL_LIMIT = 300
# R99-A5: drawCalls を実測で返す約束の様式。ここでは値が無い・数でないことも赤にする
#（stats から落ちると上の門が 0 と読んで黙って緑になる）。cockpit は 2D canvas だが、
# ロボを焼き置かずに直接塗ると 1 体 約 150 回 × 人数で同じ上限を超える＝同じ門で止める。
DRAW_CALL_REQUIRED = ("cockpit",)
# R90-H: マテリアル数の上限。stats() が返す materials を検査する。
# マテリアルを増やすほど merge.js の静的バッチが割れて drawCalls が増える＝上流で止める。
MATERIAL_LIMIT = 64
# R99-A1: 画面の一部だけを**ビット一致**で見張る様式。全画面 0.5% の門は、cockpit の部屋
# （画面の約 2 割）では目が粗すぎる: ロボの腕 1 本ぶん（360px）のポーズ違いは全画面の 0.03% で、
# 門を素通りする（実測）。部屋は整数倍の 2D canvas＝文字を描かない（文字は DOM）ので、
# 文字の縁の環境揺れも入らず 0 画素で比べられる。矩形は撮った画面から取る＝golden と
# 配置がずれたら切り出しがずれて赤になる（配置の変更も撮り直しの合図）。
STRICT_REGIONS = {"cockpit": "#viewport canvas"}


def parse_viewport(s):
    """'1440x900' → dict。不正なら既定。"""
    try:
        w, h = s.lower().split("x")
        return {"width": int(w), "height": int(h)}
    except (ValueError, AttributeError):
        return dict(VIEWPORT)


def golden_name(style, entry, query, explicit=None, viewport=None):
    """golden/成果物のファイル名。既定はスタイル名。entry/query/viewport を付けた撮影は
    'iso_stream1' / 'iso_390x844' のようにスラグを足して**別 golden** にする（R90-H）。
    既定の viewport 以外で撮った絵が既定 golden（iso.png 等）を上書きしない、が契約。"""
    if explicit:
        return explicit
    parts = [style]
    if entry and entry != "/":
        parts.append(pathlib.Path(entry).stem)
    if query:
        parts.append("".join(ch for ch in query if ch.isalnum()))
    if viewport and viewport != VIEWPORT:
        parts.append(f"{viewport['width']}x{viewport['height']}")
    return "_".join(parts)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port, extra_env=None):
    """静的ファイル配信のためだけにサーバーを立てる。API は fixture で差し替えるので
    実データには一切依存しない。extra_env はスモーク側の FAKE 注入口（R53）。"""
    env = {**os.environ, "OFFICE_HOME": str(ROOT / ".ui_shot_home"), **(extra_env or {})}
    (ROOT / ".ui_shot_home").mkdir(exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return proc
        except OSError:
            time.sleep(.1)
    proc.terminate()
    raise SystemExit("サーバーが起動しませんでした")


def shoot(style, t, seed, world, out, gpu=False, attempts=3, entry="/", query="",
          viewport=None, name=None):
    """SwiftShader は 3D シーンを描くと稀にブラウザごと落ちる（TargetClosedError）。
    フレークするゲートは「落ちても無視する」文化を生んで嘘greenより有害なので、
    ここで吸収する。描画結果自体は決定論なのでリトライしても絵は変わらない（実測）。"""
    last = None
    for i in range(attempts):
        try:
            return _shoot_once(style, t, seed, world, out, gpu, entry=entry, query=query,
                               viewport=viewport, name=name or style)
        except Exception as exc:                      # noqa: BLE001 - 落ち方を問わず再試行
            last = exc
            if i + 1 < attempts:
                print(f"    （{style}: 描画が落ちたので再試行 {i + 2}/{attempts}）")
                time.sleep(1.0)
    raise last


def _shoot_once(style, t, seed, world, out, gpu=False, entry="/", query="", viewport=None,
                name=None):
    from playwright.sync_api import sync_playwright

    name = name or style
    payload = json.dumps(world, ensure_ascii=False)
    port = free_port()
    proc = start_server(port)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=GPU if gpu else SWIFTSHADER)
            page = browser.new_page(viewport=viewport or VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            # /api/office を fixture で差し替える＝実セッションの状態に左右されない
            page.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            # R90-H: entry（/ui/pwa/app.html 等）と追加 query（stream=1&hour=18 等）で
            # 同じスタイルの別 golden を撮れる。?ui= は PWA 入口では無視されるだけ。
            url = f"http://127.0.0.1:{port}{entry}?ui={style}&t={t}&seed={seed}"
            if query:
                url += "&" + query.lstrip("&?")
            page.goto(url)
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(300)          # フォントとCSSの適用待ち
            dump = page.evaluate("window.__office.dumpWorld()")
            stats = page.evaluate("window.__office.stats && window.__office.stats()")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            region = None
            if style in STRICT_REGIONS:
                el = page.query_selector(STRICT_REGIONS[style])
                bb = el.bounding_box() if el and el.is_visible() else None
                if bb:
                    # 端の画素を取りこぼさないよう外側へ丸める（小数の配置でも 1px 内側に逃げない）
                    region = (math.floor(bb["x"]), math.floor(bb["y"]),
                              math.ceil(bb["x"] + bb["width"]), math.ceil(bb["y"] + bb["height"]))
            # 品質採点（tools/style_score.py）は3Dステージだけを見る。
            # フルページには白いクロームが入り、空き床率と輝度が実態より悪く出るため。
            stage = page.query_selector("#stage")
            if stage and stage.is_visible():        # R98: pixel の W1 は帯（#stage）を畳んでいる
                ARTIFACTS.mkdir(parents=True, exist_ok=True)
                stage.screenshot(path=str(ARTIFACTS / f"ui_{name}_stage.png"))
            # 品質採点は3Dキャンバスだけを透過付きで撮る。
            # ステージ背景（CSSグラデーション）まで写すと、明るい背景が
            # 「空き床」として数えられ、床の色を参考画像に寄せるほど悪化する（実測で踏んだ）。
            canvas = page.query_selector("#viewport canvas")
            # R98-W3: スマホ幅（≤480）では帯ごと畳む。存在だけで判定すると、見えない要素に
            # screenshot() を呼んで可視待ちが 30 秒 ×3 タイムアウトする（別モデルレビューで実測）。
            if canvas and canvas.is_visible():
                # 採点用は「3Dの中身だけ」を透過PNGで撮る。
                # ステージのCSSグラデーションが写ると、その明るい面が
                # 「明るい一様面」として数えられ、シーンをどう直しても数値が動かない
                # （ヒートマップで実際にこの状態を踏んだ）。背景を消してから撮る。
                page.evaluate("""() => {
                  const st = document.querySelector('#stage');
                  if (st) { st.dataset.bgSaved = st.style.background; st.style.background = '#ff00ff'; }
                  for (const sel of ['#labels', '#attn', '.bottom']) {
                    const el = document.querySelector(sel);
                    if (el) el.style.visibility = 'hidden';
                  }
                }""")
                # 背景をマゼンタで塗ってから撮り、style_score 側でその色を除外する。
                # omit_background は element screenshot では alpha を出さなかった（実測）。
                canvas.screenshot(path=str(ARTIFACTS / f"ui_{name}_scene.png"))
                page.evaluate("""() => {
                  const st = document.querySelector('#stage');
                  if (st) st.style.background = st.dataset.bgSaved || '';
                  for (const sel of ['#labels', '#attn', '.bottom']) {
                    const el = document.querySelector(sel);
                    if (el) el.style.visibility = '';
                  }
                }""")
            browser.close()
    finally:
        proc.terminate()
    real = [e for e in errors if "Failed to load resource" not in e]
    return dump, real, stats, region


def compare(a, b, box=None, exact=False):
    """差があった画素の割合。box=(x0,y0,x1,y1) を渡すとその矩形だけを両方から切り出して比べる。

    exact=False（全画面の既定）: 差分を輝度（L）に畳んで 0 以外を数える。iso/pixel の 0.5% の
      閾値はこの尺度で較正してあるので据え置く。
    exact=True（STRICT_REGIONS と --exact）: R/G/B の**どれか 1 つでも**違えば数える＝本当のビット一致。
      輝度に畳むと Pillow の L=(R*19595+G*38470+B*7471+0x8000)>>16 で R の ±1・B の ±4 が 0 に
      丸まり、部屋全体の色がずれても「ビット一致」と言って緑になった（R99 第 1 波の検収で実測）。"""
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0, f"サイズ不一致 {ia.size} != {ib.size}"
    if box:
        ia, ib = ia.crop(box), ib.crop(box)
    diff = ImageChops.difference(ia, ib)
    if exact:
        r, g, bl = diff.split()
        mask = ImageChops.lighter(ImageChops.lighter(r, g), bl)   # 画素ごとのチャンネル差の最大
    else:
        mask = diff.convert("L")
    changed = sum(mask.histogram()[1:])          # 0 以外＝差があった画素数
    return changed / (ia.size[0] * ia.size[1]), None


def _pixels(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size[0] * im.size[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=[*STYLES, "all"], default="iso")
    ap.add_argument("--t", type=float, default=3.2)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--world", default="basic", help="tests/fixtures/world/<name>.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--gpu", action="store_true", help="実GPUで撮る（見栄え確認用・goldenとは一致しない）")
    ap.add_argument("--update", action="store_true", help="golden を撮り直す")
    ap.add_argument("--check", action="store_true", help="golden と比較して差分率で判定")
    # R99: 撮り直した直後の確認（dev.sh --golden）用。全画面 0.5% の門は「決定論でない絵」を
    # 通してしまう（0.5% 未満の揺れがあっても緑）ので、ここだけ全画面を許容 0・チャンネル単位で見る。
    # 日々の --check には付けない（iso/pixel は環境による文字の縁の揺れを 0.5% で吸収している）。
    ap.add_argument("--exact", action="store_true",
                    help="--check の全画面も許容 0（R/G/B のどれか 1 つでも違う画素があれば赤）")
    # R90-H: 同じスタイルの別 golden（配信モード・夕方・PWA 縦画面）を撮るための3口
    ap.add_argument("--query", default="", help="追加クエリ（例: 'stream=1' / 'hour=18'）")
    ap.add_argument("--viewport", default=None, help="'幅x高さ'（例: 390x844）。既定 1440x900")
    ap.add_argument("--entry", default="/", help="入口パス（例: /ui/pwa/app.html）。既定 /")
    ap.add_argument("--name", default=None, help="golden/成果物名の明示（既定はスタイル＋クエリ由来）")
    args = ap.parse_args()

    world_path = WORLD_FIXTURES / f"{args.world}.json"
    world = json.loads(world_path.read_text(encoding="utf-8"))
    styles = STYLES if args.style == "all" else (args.style,)
    viewport = parse_viewport(args.viewport) if args.viewport else None
    if args.exact and not args.check:
        print("  ✗ --exact は --check と一緒に使う（比べない撮影に許容は無い）")
        return 2
    if args.update and args.check:
        # --update は撮った絵を golden へ書く＝同じ回の --check は golden をそれ自身と比べ、
        # 決定論でない絵にも「ビット一致」と言って緑になる（R99 第 1 波の検収で実測）。2 回に分けて回す
        print("  ✗ --update と --check（--exact）は同時に使えない（撮った golden を自分自身と比べて必ず一致と出る）。"
              "撮り直しは bash dev.sh --golden <名前>")
        return 2
    if args.name and len(styles) != 1:
        # 複数スタイルに同じ名前は付けられない（黙って捨てると別 golden の契約が破れる・Astra レビュー指摘）
        print("  ✗ --name は --style <1つ> と一緒に使う（--style all では名前が衝突する）")
        return 2

    ng = 0
    for style in styles:
        name = golden_name(style, args.entry, args.query, args.name, viewport)
        golden = GOLDEN / f"{name}.png"
        out = pathlib.Path(args.out) if args.out else (
            golden if args.update else ARTIFACTS / f"ui_{name}.png")
        dump, errors, stats, region = shoot(style, args.t, args.seed, world, out, gpu=args.gpu,
                                            entry=args.entry, query=args.query,
                                            viewport=viewport, name=name)

        if errors:
            print(f"  ✗ {name}: JSエラー {errors[:3]}")
            ng += 1
            continue
        agents = (dump or {}).get("agents") or []
        perf = ""
        if stats:
            # stats の中身はスタイルごとに違う（iso=drawCalls/tri・pixel=sprites/actors）
            perf = " " + " ".join(
                f"{k}={v:,}" if isinstance(v, int) else f"{k}={v}"
                for k, v in sorted(stats.items()))
        shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
        print(f"  ✓ {name}: {shown} (agents={len(agents)}){perf}")
        dc = (stats or {}).get("drawCalls")
        if style in DRAW_CALL_REQUIRED and (isinstance(dc, bool) or not isinstance(dc, int) or dc <= 0):
            print(f"  ✗ {name}: stats().drawCalls が実測の数でない（{dc!r}）＝描画コストの門が見えていない")
            ng += 1
        elif stats and stats.get("drawCalls", 0) > DRAW_CALL_LIMIT:
            print(f"  ✗ {name}: drawCalls {stats['drawCalls']} > {DRAW_CALL_LIMIT}"
                  f"（InstancedMesh/ジオメトリ統合が外れている可能性）")
            ng += 1
        if stats and isinstance(stats.get("materials"), int) and stats["materials"] > MATERIAL_LIMIT:
            print(f"  ✗ {name}: materials {stats['materials']} > {MATERIAL_LIMIT}"
                  f"（マテリアルを増やすと静的バッチが割れる。既存キーを共有すること）")
            ng += 1

        if args.check:
            if not golden.is_file():
                print(f"  ✗ {name}: golden がありません（--update で作成）: {golden.relative_to(ROOT)}")
                ng += 1
                continue
            ratio, err = compare(out, golden, exact=args.exact)
            if err:
                print(f"  ✗ {name}: {err}")
                ng += 1
            elif args.exact and ratio > 0:
                with_px = round(ratio * _pixels(out))
                print(f"  ✗ {name}: 全画面が golden と {with_px} 画素（{ratio * 100:.4f}%）違う"
                      f"（--exact は許容 0。撮り直した直後の比較なら＝その絵は決定論でない）")
                ng += 1
            elif ratio > DIFF_LIMIT:
                print(f"  ✗ {name}: golden と {ratio * 100:.2f}% 差分（上限 {DIFF_LIMIT * 100:.1f}%）")
                ng += 1
            elif args.exact:
                print("    golden と全画面ビット一致（--exact・チャンネル単位で 0 画素）")
            else:
                print(f"    golden 一致（差分 {ratio * 100:.3f}%）")
            if style in STRICT_REGIONS and not err:
                sel = STRICT_REGIONS[style]
                if not region:
                    # 見張る矩形が無いのに緑にしない（畳まれた・消えた＝それ自体が回帰）
                    print(f"  ✗ {name}: ビット一致で見張る {sel} が画面に無い")
                    ng += 1
                    continue
                rratio, _ = compare(out, golden, region, exact=True)
                w, h = region[2] - region[0], region[3] - region[1]
                changed = round(rratio * w * h)
                if changed:
                    print(f"  ✗ {name}: {sel}（{w}×{h}）が golden と {changed} 画素違う"
                          f"（ここはビット一致を求める＝全画面 {DIFF_LIMIT * 100:.1f}% の門では"
                          f"ポーズ 1 つの違いが見えない）")
                    ng += 1
                else:
                    print(f"    {sel}（{w}×{h}）ビット一致")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
