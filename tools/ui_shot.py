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
  python3 tools/ui_shot.py --update                       # 全スタイルの golden を撮り直す
  python3 tools/ui_shot.py --check                        # golden と比較（差分率で判定）

注意:
  - 見栄えの確認用に実GPUで撮りたいときは --gpu（golden とはハッシュが一致しない）。
  - tools/ は外部依存OKの領域（playwright / Pillow を使う）。server/ の stdlib 縛りとは無関係。
"""
import argparse
import json
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

STYLES = ("iso",)          # ドット絵スタイルはユーザー判断で撤去（2026-07-30）
VIEWPORT = {"width": 1440, "height": 900}
# 回帰テストは必ずこのバックエンドで撮る（実測: 2回実行でスクショhashが完全一致）
SWIFTSHADER = ["--use-gl=swiftshader", "--disable-gpu"]
GPU = ["--use-angle=metal", "--enable-gpu"]
DIFF_LIMIT = 0.005          # 0.5%（旧scene_diff.py から引き継いだ基準・R52で唯一のビジュアルゲートに）
# 性能ゲート: 素朴に Mesh を並べると 19体で 4700 ドローに達して 60fps が出ない。
# InstancedMesh とジオメトリ統合を外した瞬間にここで落ちる。
DRAW_CALL_LIMIT = 300


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    """静的ファイル配信のためだけにサーバーを立てる。API は fixture で差し替えるので
    実データには一切依存しない。"""
    env = {**os.environ, "OFFICE_HOME": str(ROOT / ".ui_shot_home")}
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


def shoot(style, t, seed, world, out, gpu=False, attempts=3):
    """SwiftShader は 3D シーンを描くと稀にブラウザごと落ちる（TargetClosedError）。
    フレークするゲートは「落ちても無視する」文化を生んで嘘greenより有害なので、
    ここで吸収する。描画結果自体は決定論なのでリトライしても絵は変わらない（実測）。"""
    last = None
    for i in range(attempts):
        try:
            return _shoot_once(style, t, seed, world, out, gpu)
        except Exception as exc:                      # noqa: BLE001 - 落ち方を問わず再試行
            last = exc
            if i + 1 < attempts:
                print(f"    （{style}: 描画が落ちたので再試行 {i + 2}/{attempts}）")
                time.sleep(1.0)
    raise last


def _shoot_once(style, t, seed, world, out, gpu=False):
    from playwright.sync_api import sync_playwright

    payload = json.dumps(world, ensure_ascii=False)
    port = free_port()
    proc = start_server(port)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=GPU if gpu else SWIFTSHADER)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            # /api/office を fixture で差し替える＝実セッションの状態に左右されない
            page.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            page.goto(f"http://127.0.0.1:{port}/?ui={style}&t={t}&seed={seed}")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(300)          # フォントとCSSの適用待ち
            dump = page.evaluate("window.__office.dumpWorld()")
            stats = page.evaluate("window.__office.stats && window.__office.stats()")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            # 品質採点（tools/style_score.py）は3Dステージだけを見る。
            # フルページには白いクロームが入り、空き床率と輝度が実態より悪く出るため。
            stage = page.query_selector("#stage")
            if stage:
                ARTIFACTS.mkdir(parents=True, exist_ok=True)
                stage.screenshot(path=str(ARTIFACTS / f"ui_{style}_stage.png"))
            # 品質採点は3Dキャンバスだけを透過付きで撮る。
            # ステージ背景（CSSグラデーション）まで写すと、明るい背景が
            # 「空き床」として数えられ、床の色を参考画像に寄せるほど悪化する（実測で踏んだ）。
            canvas = page.query_selector("#viewport canvas")
            if canvas:
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
                canvas.screenshot(path=str(ARTIFACTS / f"ui_{style}_scene.png"))
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
    return dump, real, stats


def compare(a, b):
    from PIL import Image, ImageChops
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return 1.0, f"サイズ不一致 {ia.size} != {ib.size}"
    gray = ImageChops.difference(ia, ib).convert("L")
    changed = sum(gray.histogram()[1:])          # 輝度0以外＝差があった画素数
    return changed / (ia.size[0] * ia.size[1]), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=[*STYLES, "all"], default="all")
    ap.add_argument("--t", type=float, default=3.2)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--world", default="basic", help="tests/fixtures/world/<name>.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--gpu", action="store_true", help="実GPUで撮る（見栄え確認用・goldenとは一致しない）")
    ap.add_argument("--update", action="store_true", help="golden を撮り直す")
    ap.add_argument("--check", action="store_true", help="golden と比較して差分率で判定")
    args = ap.parse_args()

    world_path = WORLD_FIXTURES / f"{args.world}.json"
    world = json.loads(world_path.read_text(encoding="utf-8"))
    styles = STYLES if args.style == "all" else (args.style,)

    ng = 0
    for style in styles:
        golden = GOLDEN / f"{style}.png"
        out = pathlib.Path(args.out) if args.out else (
            golden if args.update else ARTIFACTS / f"ui_{style}.png")
        dump, errors, stats = shoot(style, args.t, args.seed, world, out, gpu=args.gpu)

        if errors:
            print(f"  ✗ {style}: JSエラー {errors[:3]}")
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
        print(f"  ✓ {style}: {shown} (agents={len(agents)}){perf}")
        if stats and stats.get("drawCalls", 0) > DRAW_CALL_LIMIT:
            print(f"  ✗ {style}: drawCalls {stats['drawCalls']} > {DRAW_CALL_LIMIT}"
                  f"（InstancedMesh/ジオメトリ統合が外れている可能性）")
            ng += 1

        if args.check:
            if not golden.is_file():
                print(f"  ✗ {style}: golden がありません（--update で作成）: {golden.relative_to(ROOT)}")
                ng += 1
                continue
            ratio, err = compare(out, golden)
            if err:
                print(f"  ✗ {style}: {err}")
                ng += 1
            elif ratio > DIFF_LIMIT:
                print(f"  ✗ {style}: golden と {ratio * 100:.2f}% 差分（上限 {DIFF_LIMIT * 100:.1f}%）")
                ng += 1
            else:
                print(f"    golden 一致（差分 {ratio * 100:.3f}%）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
