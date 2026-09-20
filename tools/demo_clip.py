#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R98-W3: フロア帯の 6 秒クリップを**毎回同じ絵で**撮る（X / README 用）。

なぜ record_video を使わないか（プランからの意図的な変更）:
  Playwright の `record_video` は実時間で回る＝マシンの負荷でコマが前後し、撮るたびに違う動画になる。
  ここは「帯が動く」を見せる素材なので、1 コマずつ**時刻を送って**撮る方が正しい。
  入口は `window.__office.debug.step(t)`（固定時刻のまま時計を送って描き直す唯一の口）。
  同じ引数で 2 回撮れば 1 バイト違わない（--verify がそれを機械で確かめる）。

筋書き（9 秒）。プランは 6 秒だったが、**机から受付までの実測が片道 2.9 秒**（論理 279px ÷ 96px/秒）。
6 秒だと受付に着く前に折り返し、**挙手のコマが 1 度も出ない**（別モデルレビューで実測）。歩く速さは
製品の挙動なのでクリップの都合で変えず、尺の方を伸ばした。
  0.0s  机で打鍵・ラウンジで休憩・会議室に 2 人
  1.0s  1 人が席を立ち、受付へ歩く（台帳の行も同時に最上段へ来る）
  3.9s  受付に着いて ❗ を上げる（点滅は歩いている間も出ている）
  5.0s  answered ＝ ❗ が消え、机へ歩いて戻る
  8.2s  席に着いて打鍵を再開。証拠列に「✓ commit たった今」が点く
  9.0s  そのまま静止

出力:
  dist/clip/clip.gif / clip.mp4   1280×560＝帯（部屋まるごと）＋台帳 9 行（証拠列つき）
  dist/clip/full.gif              画面全体（1440×900 を 1/2）＝最後に全景を見せたいとき
  dist/clip/frames/*.png          元コマ（--frames のとき）
  mp4 は ffmpeg が在るときだけ（無くても gif は出る）。

使い方:
  python3 tools/demo_clip.py                 # 撮る（要 Playwright・verify.local の VENV_PY）
  python3 tools/demo_clip.py --verify        # 2 回撮ってバイト一致を確かめる
  python3 tools/demo_clip.py --fps 12 --seconds 9
"""
import argparse
import hashlib
import json
import math
import pathlib
import shutil
import sys
from io import BytesIO

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
OUT = ROOT / "dist" / "clip"
# **1280 で撮る**。理由は 2 つとも実測:
#   ・834（iPad 縦）だと `max-width: 1024px` の則で**証拠列が消える**＝5 秒の「✓ commit が点く」が写らない。
#   ・1440 だと部屋が論理 703 まで広がり、16:9 に切ると受付しか入らず「机から受付まで歩く」が画角の外。
# 1280 なら部屋（論理 623）が丸ごと入り、台帳も証拠列まで出る。**横は切らない**＝証拠列は
# 表のいちばん右にあるので、少しでも切ると「✓ commit が点く」瞬間が画角の外へ出る（実測）。
# 帯の上端から 560px＝帯・❗・台帳 9 行がちょうど入る高さ（16:9 に伸ばすと下が白で埋まる）。
# 拡大はしない＝X が 1920 幅へ落とすときにドットが滲まないし、実際の画面と同じ大きさで見せられる。
VIEWPORT = {"width": 1280, "height": 860}
ZOOM = 1
CLIP_W, CLIP_H = 1280, 560


def scenario(world, target_id):
    """時刻 → その瞬間の office_json を返す表を作る。

    3 箇所（roster / sessions[] / employees）とも書かないと行に届かない（ui/hud/board.js の重なり順）。
    """
    def variant(**fields):
        doc = json.loads(json.dumps(world))
        sids = set()
        for entry in doc["roster"]:
            if entry.get("projectId") != target_id and entry.get("session") != target_id:
                continue
            entry.update(fields)
            sids.add(entry.get("session"))
            for sub in entry.get("sessions", []):
                sub.update(fields)
                sids.add(sub.get("session"))
        for emp in doc.get("employees", []):
            if emp.get("session") in sids:
                emp.update(fields)
        return doc

    # fixture は最初から evidence を持っている＝そのままだと「✓ commit 41分前」が冒頭から出て、
    # 5 秒の「成果が点く」瞬間が起きない（別モデルレビューで実測）。だから最初は消しておく。
    # fixture の verb は「休憩中」。state だけ working にすると**状態=作業／いま=休憩中**という
    # 矛盾した行が写る（別モデルレビューで実測）。作業中の姿にするときは verb と target も揃える。
    busy = {"state": "working", "attention": False, "question": "", "approvalMin": 0,
            "verb": "", "target": "", "kind": "tool"}
    working = variant(**busy, evidence=None)
    asking = variant(state="waiting", attention=True, approvalMin=3, evidence=None,
                     question="この設計で確定していいですか？")
    done = variant(**busy, evidence={"kind": "committed", "ago": 0})
    return [
        (0.0, working),
        (1.0, asking),      # 席を立って受付へ歩き（2.9 秒）、着いたら ❗ を上げる
        (5.0, working),     # 答えた＝机へ歩いて戻る
        (8.2, done),        # 席に着いてから証拠列に「✓ commit」が点く
    ]


def capture(fps, seconds, frames_dir=None):
    from playwright.sync_api import sync_playwright
    from PIL import Image

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    target = next(p for p in world["roster"] if p["disp"] == "制作本部(works)")
    target_id = target.get("projectId") or target["session"]
    script = scenario(world, target_id)
    sb = '{"providers":[]}'

    port = free_port()
    proc = start_server(port)
    band_frames, full_frames = [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            body = [json.dumps(script[0][1], ensure_ascii=False)]
            page.route("**/api/office*", lambda r: r.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=body[0]))
            page.route("**/api/status_board*", lambda r: r.fulfill(
                status=200, content_type="application/json", body=sb))
            page.route("**/api/digest*", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body='{"available":false,"since":0,"totals":{"tasksDone":0}}'))
            page.goto(f"http://127.0.0.1:{port}/?ui=pixel&t=0&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            page.wait_for_timeout(400)

            shown = -1
            total = int(round(fps * seconds))
            for i in range(total):
                t = i / fps
                # **先に時計を送る**。inject は中で描き直すので、順序が逆だと移動の開始時刻に
                # 前のコマの時刻が入り、fps によって位置が変わる（別モデルレビューで実測）。
                page.evaluate("(t) => window.__office.debug.step(t)", t)
                cue = max(j for j, (at, _) in enumerate(script) if at <= t + 1e-9)
                if cue != shown:
                    shown = cue
                    body[0] = json.dumps(script[cue][1], ensure_ascii=False)
                    page.evaluate("(w) => window.__office.inject(w)", script[cue][1])
                    page.evaluate("(t) => window.__office.debug.step(t)", t)
                full = Image.open(BytesIO(page.screenshot(type="png"))).convert("RGB")
                y0 = page.evaluate(
                    "() => Math.round(document.querySelector('#stage').getBoundingClientRect().top)")
                clip = full.crop((0, y0, CLIP_W, y0 + CLIP_H))
                if ZOOM != 1:
                    clip = clip.resize((clip.width * ZOOM, clip.height * ZOOM), Image.NEAREST)
                band_frames.append(clip)
                full_frames.append(full.resize((full.width // 2, full.height // 2), Image.LANCZOS))
                if frames_dir:
                    clip.save(frames_dir / f"clip_{i:03d}.png")
            browser.close()
    finally:
        proc.terminate()
    return band_frames, full_frames


def frame_durations(count, fps):
    """GIF の 1 コマの表示時間は **10ms 単位**。端数を捨てると尺が縮む
    （12fps だと 83ms → 80ms ＝ 6 秒が 5.76 秒になる。別モデルレビューで実測）。
    累積時間の方を丸めて配り、合計が指定どおりになるようにする。"""
    out, prev = [], 0
    for i in range(1, count + 1):
        at = int(round(i * 1000 / fps / 10)) * 10
        out.append(max(10, at - prev))
        prev = at
    return out


# GIF の下限は 1 コマ 20ms（50fps）。10ms を書くと**ブラウザが 100ms に直す**ので、
# それより速い指定では GIF だけ 10 倍遅く再生される（別モデルレビューで実測）。速い指定のときは
# コマを間引いて 50fps 以下に落とす＝尺は保ったまま、mp4 と同じ速さで見える。
GIF_MAX_FPS = 50


def save_gif(frames, path, fps):
    step = max(1, math.ceil(fps / GIF_MAX_FPS))
    keep = frames[::step]
    keep_fps = fps / step
    keep[0].save(path, save_all=True, append_images=keep[1:],
                 duration=frame_durations(len(keep), keep_fps), loop=0, optimize=True,
                 disposal=2)
    return path.stat().st_size


def to_mp4(frames, path, fps):
    """ffmpeg が在れば mp4 も出す（X はこちらの方が綺麗で軽い）。無ければ None。"""
    if not shutil.which("ffmpeg"):
        return None
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for i, fr in enumerate(frames):
            fr.save(pathlib.Path(tmp) / f"{i:04d}.png")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
               "-i", str(pathlib.Path(tmp) / "%04d.png"),
               # ドット絵なので縮小補間を入れない。yuv420p は SNS 側の互換のため
               "-vf", "scale=iw:ih:flags=neighbor,format=yuv420p",
               "-c:v", "libx264", "-preset", "slow", "-crf", "18", str(path)]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            return None
    return path


def main():
    ap = argparse.ArgumentParser(description="フロア帯の 6 秒クリップ（決定論）")
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--seconds", type=float, default=9.0)
    ap.add_argument("--frames", action="store_true", help="元コマも残す")
    ap.add_argument("--verify", action="store_true", help="2 回撮ってバイト一致を確かめる")
    args = ap.parse_args()

    try:
        import PIL  # noqa: F401
    except ImportError:
        print("✗ Pillow が要る: pip install Pillow")
        return 1

    if OUT.exists():
        shutil.rmtree(OUT)
    frames_dir = None
    if args.frames:
        frames_dir = OUT / "frames"
        frames_dir.mkdir(parents=True)
    OUT.mkdir(parents=True, exist_ok=True)

    band, full = capture(args.fps, args.seconds, frames_dir)
    band_path, full_path = OUT / "clip.gif", OUT / "full.gif"
    b = save_gif(band, band_path, args.fps)
    f = save_gif(full, full_path, args.fps)
    print(f"  ✓ 本編 {band[0].width}×{band[0].height} × {len(band)} コマ → {band_path} ({b // 1024}KB)")
    print(f"  ✓ 全景 {full[0].width}×{full[0].height} × {len(full)} コマ → {full_path} ({f // 1024}KB)")
    mp4 = to_mp4(band, OUT / "clip.mp4", args.fps)
    if mp4:
        print(f"  ✓ mp4（X はこちらの方が綺麗）→ {mp4} ({mp4.stat().st_size // 1024}KB)")
    else:
        print("  - ffmpeg が無いので mp4 は出していない（gif だけで足りる）")

    if args.verify:
        first = hashlib.sha256(band_path.read_bytes()).hexdigest()
        band2, _ = capture(args.fps, args.seconds, None)
        tmp = OUT / "band.verify.gif"
        save_gif(band2, tmp, args.fps)
        second = hashlib.sha256(tmp.read_bytes()).hexdigest()
        tmp.unlink()
        if first == second:
            print(f"  ✓ 2 回撮ってバイト一致（{first[:12]}）＝毎回同じ動画")
        else:
            print(f"  ✗ 撮るたびに違う: {first[:12]} / {second[:12]}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
