#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「入れてから画面が出るまで何秒か」を計る（R129・2026-11-14）。この Mac の本番には触らない。

  .venv/bin/python tools/measure_first_run.py        # 2 つの道を計って 1 行ずつ出し、logs/first_run.jsonl に足す

計る道（どちらも HEAD を git archive で一時フォルダへ出した「clone したて」の木で回す）:
  A. 見るだけ  `bash setup.sh --demo`      … 打ってから URL が出るまで／その URL の画面が描けるまで
  B. 入れる    `bash setup.sh --no-daemon` … 一時 HOME に配線 → この場で起動 → 応答まで／画面が描けるまで
     ＝本物の `bash setup.sh` から launchd の登録（macapp/install.sh・launchctl）だけを抜いた道。
       本物は launchd の com.senao.aioffice を入れ直す＝この Mac で回すと本番の常駐を止めるので、ここでは計らない。

計れないもの（README と動画で言葉で補う）: python3 が無い Mac の Xcode の道具の導入（5〜15 分）／launchd の登録／
配線は新しいセッションから効く＝自分のセッションが出るのは Claude Code を 1 本開き直してから／スマホ（Cloudflare のログイン）。

守ること: HOME・OFFICE_HOME は一時フォルダ・ポートは空いている番号（OFFICE_PORT）・`open` は何もしない偽物（利用者の
ブラウザを開かない）・標準入力は空（最後の「1 体出勤させますか？」は既定の No）。起動したサーバーは自分が出した木の
office_server.py であることを確かめてから止め、一時フォルダを消す。
"""
import json
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
LOG = ROOT / "logs" / "first_run.jsonl"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def responds(port):
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/office", headers={"X-Office-Local": "1"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def screen_ready(url, timeout=90):
    """ブラウザで開いて、画面の probe が ready になるまでの秒（3D は CPU で描くので遅め＝実機より長く出る）。"""
    from playwright.sync_api import sync_playwright
    from ui_shot import SWIFTSHADER
    t0 = time.monotonic()
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=SWIFTSHADER)
        try:
            page = b.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_function("window.__office && window.__office.ready", timeout=timeout * 1000)
            rows = page.locator(".app-session-row[data-app-session], .pxrow, .cprow").count()
            return round(time.monotonic() - t0, 1), rows
        finally:
            b.close()


def fake_bin(base):
    """利用者のブラウザを開かない `open`（何もせず 0 で返す）。"""
    d = base / "fakebin"
    d.mkdir()
    f = d / "open"
    f.write_text("#!/bin/sh\nexit 0\n")
    f.chmod(0o755)
    return d


def tree_of_head(base):
    src = base / "src"
    src.mkdir()
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
    return src


def measure_demo(src, base):
    env = {**os.environ, "AIOFFICE_NO_OPEN": "1", "HOME": str(base / "home-demo"), "TMPDIR": str(base)}
    (base / "home-demo").mkdir()
    t0 = time.monotonic()
    proc = subprocess.Popen(["bash", "setup.sh", "--demo"], cwd=str(src), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    url, out = None, []
    try:
        while time.monotonic() - t0 < 60:
            raw = proc.stdout.readline()
            if not raw:
                break
            out.append(raw.rstrip())
            m = re.search(r"(http://127\.0\.0\.1:\d+/\?demo=1)", raw)
            if m:
                url = m.group(1)
                break
        t_url = round(time.monotonic() - t0, 1)
        if not url:
            return {"path": "demo", "ok": False, "why": "URL が出ない: " + " / ".join(out[-3:])}
        ready, rows = screen_ready(url)
        return {"path": "demo", "ok": True, "url_s": t_url, "screen_s": round(t_url + ready, 1), "rows": rows}
    finally:
        os.killpg(proc.pid, signal.SIGINT)        # デモは Ctrl-C で全部消える約束＝同じ止め方
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)


def server_pids(port, src):
    """そのポートを聞いている、自分が出した木の office_server.py の pid だけ（別の物は止めない）。"""
    r = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    mine = []
    for pid in r.stdout.split():
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        if str(src / "server" / "office_server.py") in cmd:
            mine.append(int(pid))
    return mine


def measure_install(src, base):
    home = base / "home-install"
    home.mkdir()
    port = free_port()
    env = {**os.environ, "HOME": str(home), "OFFICE_PORT": str(port),
           "PATH": f"{fake_bin(base)}:{os.environ.get('PATH', '')}"}
    env.pop("OFFICE_HOME", None)
    env.pop("OFFICE_DATA", None)
    t0 = time.monotonic()
    try:
        r = subprocess.run(["bash", "setup.sh", "--no-daemon"], cwd=str(src), env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=180)
        t_setup = round(time.monotonic() - t0, 1)
        wired = (home / ".claude" / "hooks" / "office-inbox-wait.sh").is_file() \
            and "office-inbox-wait" in (home / ".claude" / "settings.json").read_text(encoding="utf-8")
        if r.returncode != 0 or not responds(port):
            tail = " / ".join(r.stdout.strip().splitlines()[-3:])
            return {"path": "install", "ok": False, "why": f"rc={r.returncode}・応答={responds(port)}: {tail}"}
        ready, rows = screen_ready(f"http://127.0.0.1:{port}/")
        return {"path": "install", "ok": True, "setup_s": t_setup, "screen_s": round(t_setup + ready, 1),
                "rows": rows, "wired": wired}
    finally:
        for pid in server_pids(port, src):
            os.kill(pid, signal.SIGTERM)
        end = time.monotonic() + 10
        while server_pids(port, src) and time.monotonic() < end:
            time.sleep(0.2)
        for pid in server_pids(port, src):
            os.kill(pid, signal.SIGKILL)


def main():
    load = os.getloadavg()[0]
    results = []
    with tempfile.TemporaryDirectory(prefix="first-run-") as tmp:
        base = pathlib.Path(tmp)
        src = tree_of_head(base)
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        for fn in (measure_demo, measure_install):
            try:
                results.append(fn(src, base))
            except Exception as err:  # noqa: BLE001 - 片方が落ちても、もう片方は計る
                results.append({"path": fn.__name__.replace("measure_", ""), "ok": False, "why": f"{type(err).__name__}: {err}"})
    print(f"▶ 入れてから画面が出るまで（HEAD {head}・この Mac の 1 分の負荷 {load:.0f}・3D は CPU 描画）")
    for r in results:
        if not r["ok"]:
            print(f"  ✗ {r['path']}: {r['why']}")
        elif r["path"] == "demo":
            print(f"  ✓ 見るだけ（setup.sh --demo）: URL まで {r['url_s']} 秒・画面が描けるまで {r['screen_s']} 秒（一覧の行 {r['rows']}）")
        else:
            print(f"  ✓ 入れる（setup.sh --no-daemon・launchd 抜き）: 配線と起動と応答まで {r['setup_s']} 秒・画面まで {r['screen_s']} 秒"
                  f"（配線 {'済' if r['wired'] else '無'}・一覧の行 {r['rows']}＝一時 HOME にはセッションが無い）")
    print("  計れないもの: python3 の導入（Xcode の道具・5〜15 分）・launchd の登録・自分のセッション（配線は新しいセッションから＝1 本開き直す）・スマホ")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M%z"), "head": head, "load": round(load, 1),
                            "results": results}, ensure_ascii=False) + "\n")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
