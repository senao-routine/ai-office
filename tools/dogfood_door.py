#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出勤の口（Bot）を本番で 1 往復させて、人の操作どおりに確かめる道具（2026-11-14・R127／R128 の配備の後に作った）。

  ⚙ で追加 → 出勤・相談（選択肢 2 つ）→ /api/office の Bot の行 → 中継へ送る形（Bot 専用の鍵だけ）
  → Mac の答えのカード（許可のボタンが無い）→ 選択肢を「押す → データが来るまで待つ → 離す」→ Bot の inbox に choice
  → 報告 → 記録の画面 → ⚙ で消音 → ⚙ の 2 回押しで削除（台帳・状態・記録が消える）。

使い方:
  .venv/bin/python tools/dogfood_door.py               # 本番（127.0.0.1:4780・~/.claude の口）。口に関わる配備の後に 1 回
  .venv/bin/python tools/dogfood_door.py --self-test   # 一時 home の daemon で同じ手順（verify ▶7 が回す＝道具が腐らない）
撮った絵は --out（既定 tests/artifacts/dogfood_door/）。終了コード: 0＝全部合格・1＝どこかで外れた・2＝始める前に断った。

本番で回すときの注意:
  - 試しの Bot（既定 id `dogfood-check`）が台帳に既にあれば始めない（本物の Bot を上書きしない）。終わりと失敗のときは必ず外し、
    その Bot の空のロックファイルも消す。監査（audit.jsonl）には残る。
  - 相談はスマホへも中継される＝スマホに Push が 1 回届くことがある（消音は Mac の音と通知だけ・スマホの Push は R130）。
  - クリックは座標で押す（ヘッドレスの Chromium は右の 3D を CPU で描くので 1 コマが重く、locator.click() の待ちが数秒かかって
    削除の 2 回押しの 3 秒を越えた＝2026-11-14）。
"""
import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
import office_door  # noqa: E402
import relay_agent  # noqa: E402

BOT_ID = "dogfood-check"
BOT_NAME = "動作確認の Bot"
QUESTION = "今日の依頼 3 件のうち、急ぎの 1 件だけ先に返信してよい？"
OPTIONS = ["先に返信する", "全部まとめて夕方に"]
REPORT = "急ぎの 1 件に返信しました"
# 中継へ送る Bot の行に載ってはいけない鍵（本文・パス・会話の材料＝不変条件 8・relay_agent._ALLOW_DOOR_ENTRY）
RELAY_FORBIDDEN = ("cwd", "lastSaid", "lastOrder", "target", "branch", "feed", "sessions", "title")


class Fail(AssertionError):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


class Run:
    def __init__(self, port, home, out):
        self.base = f"http://127.0.0.1:{port}"
        self.home = home
        self.out = out
        self.passed = []
        self.session = f"bot-{BOT_ID}"

    def ok(self, msg):
        self.passed.append(msg)
        print(f"  ✓ {msg}", flush=True)

    def office(self):
        req = urllib.request.Request(f"{self.base}/api/office", headers={"X-Office-Local": "1"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def wait_row(self, pred, what, timeout=30):
        end, rows = time.monotonic() + timeout, []
        while time.monotonic() < end:
            o = self.office()
            rows = [e for e in o.get("employees", []) if e.get("session") == self.session]
            if rows and pred(rows[0]):
                return o, rows[0]
            time.sleep(0.5)
        raise Fail(f"{what}: /api/office に期待の Bot の行が出ない（{rows[:1]}）")

    def ledger(self):
        return {b["bot"]: b for b in office_door.list_bots(home=self.home)}

    def shot(self, page, name, selector=None):
        path = self.out / name
        (page.locator(selector).first if selector else page).screenshot(path=str(path))
        return path

    @staticmethod
    def center(page, selector):
        """座標で押すための中心（見える所までスクロールしてから測る）。"""
        page.evaluate("(s) => document.querySelector(s)?.scrollIntoView({ block: 'center' })", selector)
        page.wait_for_timeout(150)
        box = page.evaluate("""(s) => { const n = document.querySelector(s); if (!n) return null;
            const r = n.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }""", selector)
        check(box, f"押す場所が無い: {selector}")
        return box["x"], box["y"]

    def click(self, page, selector):
        page.mouse.click(*self.center(page, selector))

    def cleanup(self):
        """試しの Bot を外し、その Bot の空のロックファイルも消す（中身のあるものは触らない）。"""
        if BOT_ID in self.ledger():
            print("  （後片づけ）", office_door.remove_bot(BOT_ID, home=self.home).get("text"), flush=True)
        for p in (office_door.state_path(BOT_ID, self.home), office_door.log_path(BOT_ID, self.home)):
            lock = pathlib.Path(str(p) + ".lock")
            try:
                if lock.is_file() and lock.stat().st_size == 0:
                    lock.unlink()
            except OSError:
                pass

    def walk(self, page):
        bot_sel = f'#modal .mbot[data-bot="{BOT_ID}"]'
        page.goto(f"{self.base}/?ui=app", wait_until="domcontentloaded")
        page.wait_for_selector("#btn-settings", timeout=60000)

        # 1. ⚙ → Bot → 追加
        self.click(page, "#btn-settings")
        page.wait_for_selector("#modal .mbots .mbotform", timeout=20000)
        inputs = page.locator("#modal .mbotform input")
        for i, value in enumerate((BOT_ID, BOT_NAME, "検収")):
            inputs.nth(i).fill(value)
        self.click(page, "#modal .mbotform button[type=submit]")
        page.wait_for_selector(bot_sel, timeout=20000)
        led = self.ledger()
        check(led.get(BOT_ID, {}).get("name") == BOT_NAME, f"⚙ で追加しても台帳に載らない: {led}")
        cmd = page.locator(f"{bot_sel} .mbotcmd").inner_text()
        check(cmd.startswith(f"claude mcp add aioffice-door-{BOT_ID} -- ") and cmd.endswith(f"--bot {BOT_ID}"),
              f"登録コマンドの形が違う: {cmd!r}")
        self.shot(page, "1_panel_added.png", "#modal .mbots")
        self.ok("⚙ で追加 → 台帳に載り、登録コマンドが出る")
        page.keyboard.press("Escape")

        # 2. 出勤・相談（Bot の側＝口の関数）→ daemon の行
        check(office_door.checkin(BOT_ID, "受信箱を見ています", home=self.home)["ok"], "出勤できない")
        asked = office_door.ask(BOT_ID, QUESTION, OPTIONS, home=self.home)
        check(asked.get("ok"), f"相談を出せない: {asked}")
        office, row = self.wait_row(lambda e: bool(e.get("ask")), "相談")
        check(row.get("vendor") == "bot" and row["ask"].get("askId") == asked["askId"], f"Bot の相談の行が違う: {row}")
        self.ok("出勤・相談 → /api/office に vendor=bot の行（askId が一致）")

        # 3. 中継へ送る形（Bot 専用の鍵だけ）
        red = relay_agent._redact_office_for_relay(office)
        rrows = [e for e in red.get("employees", []) if e.get("session") == self.session]
        check(len(rrows) == 1, f"中継へ送る形に Bot の行が 1 つでない: {rrows}")
        leaked = [k for k in RELAY_FORBIDDEN if k in rrows[0]]
        check(not leaked and "door" not in (red.get("sources") or {}), f"中継へ送る形に載ってはいけない鍵: {leaked}")
        check(rrows[0].get("question") and (rrows[0].get("ask") or {}).get("id8"), f"中継の行に問いと id8 が無い: {rrows[0]}")
        self.ok("中継へ送る形は Bot 専用の鍵だけ（パス・本文・sources.door 無し）・問いと id8 あり")

        # 4. Mac の答えのカード → 選択肢を「押す → データが来るまで待つ → 離す」
        row_sel = f'#app-attention-list .app-session-row[data-app-session="{self.session}"]'
        page.wait_for_selector(row_sel, timeout=30000)
        self.click(page, row_sel)
        page.wait_for_function("(t) => [...document.querySelectorAll('#app-answer button[data-answer]')].some(b => b.innerText.includes(t))",
                               arg=OPTIONS[1], timeout=20000)
        labels = page.locator("#app-answer button[data-answer]").all_inner_texts()
        check(not [t for t in labels if "許可" in t], f"Bot の相談のカードに許可のボタンがある: {labels}")
        self.shot(page, "2_ask_card.png")
        idx = next(i for i, t in enumerate(labels) if OPTIONS[0] in t)
        x, y = self.center(page, f'#app-answer button[data-answer="{idx}"]')
        page.mouse.move(x, y)
        page.mouse.down()
        page.wait_for_timeout(2500)          # 本番はこの間に /api/office が 2 回ほど届く（押している間は描き直さない＝press_hold）
        page.mouse.up()
        got, end = {}, time.monotonic() + 15
        while time.monotonic() < end:
            got = office_door.inbox(BOT_ID, home=self.home)
            if any(it.get("kind") == "answer" for it in got.get("items", [])):
                break
            time.sleep(0.25)
        answers = [it for it in got.get("items", []) if it.get("kind") == "answer"]
        check(len(answers) == 1 and answers[0].get("askId") == asked["askId"]
              and (answers[0].get("choice") or {}).get("label") == OPTIONS[0],
              f"2.5 秒押して離した答えが Bot の inbox に届かない: {got}")
        self.ok(f"Mac のカード（{' / '.join(labels)}）→ 2.5 秒押して離す → inbox に choice「{OPTIONS[0]}」")
        office_door.inbox(BOT_ID, ack=[it["id"] for it in got["items"]], home=self.home)

        # 5. 報告 → 記録の画面（選んでいる間は 5 秒ごとに差分）
        check(office_door.report(BOT_ID, REPORT, "残り 2 件は夕方にまとめます", home=self.home)["ok"], "報告できない")
        page.wait_for_function("(ts) => ts.every(t => (document.querySelector('#app-activity')?.innerText || '').includes(t))",
                               arg=[QUESTION, OPTIONS[0], REPORT], timeout=30000)
        self.shot(page, "3_record.png")
        self.ok("記録の画面に 相談・選んだ答え・報告 が並ぶ")

        # 6. ⚙ で消音
        self.click(page, "#btn-settings")
        page.wait_for_selector(f"{bot_sel} .mbotmute", timeout=20000)
        self.click(page, f"{bot_sel} .mbotmute")
        page.wait_for_selector(f"{bot_sel} .mbotmuted", timeout=20000)
        check(self.ledger()[BOT_ID].get("muted") is True, "消音が台帳に無い")
        self.wait_row(lambda e: e.get("muted") is True, "消音の印")
        self.shot(page, "4_panel_muted.png", "#modal .mbots")
        self.ok("⚙ で消音 → 台帳と Bot の行に muted")

        # 7. ⚙ の 2 回押しで削除（1 回目は確かめの文だけ・3 秒以内に 2 回目）
        self.click(page, f"{bot_sel} .mbotremove")
        armed = page.locator(f"{bot_sel} .mbotremove").inner_text()
        check(armed.startswith("もう一度押すと削除") and BOT_ID in self.ledger(), f"1 回目で確かめの段にならない: {armed!r}")
        self.click(page, f"{bot_sel} .mbotremove")
        page.wait_for_function("(s) => !document.querySelector(s)", arg=bot_sel, timeout=20000)
        check(BOT_ID not in self.ledger(), "2 回目で台帳から外れない")
        check(not office_door.state_path(BOT_ID, self.home).exists() and not office_door.log_path(BOT_ID, self.home).exists(),
              "削除で状態と記録が消えない")
        self.ok("⚙ の 2 回押しで削除 → 台帳・状態・記録が消える")


def start_server(port, home):
    proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
                            cwd=str(ROOT), env={**os.environ, "OFFICE_HOME": str(home)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return proc
        except OSError:
            time.sleep(.1)
    proc.terminate()
    proc.wait(timeout=5)
    raise RuntimeError("一時の daemon が起動しませんでした")


def main(argv=None):
    ap = argparse.ArgumentParser(description="出勤の口（Bot）を本番で 1 往復させて確かめる")
    ap.add_argument("--port", type=int, default=4780)
    ap.add_argument("--self-test", action="store_true", help="一時 home の daemon で同じ手順を回す（verify 用）")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "tests" / "artifacts" / "dogfood_door")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright
    from ui_shot import SWIFTSHADER, free_port

    args.out.mkdir(parents=True, exist_ok=True)
    temp, proc, home = None, None, None
    if args.self_test:
        tmp_root = ROOT / "tests" / "artifacts" / ".tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix="dogfood-door-", dir=tmp_root)
        home = pathlib.Path(temp.name)
        args.port = free_port()
        proc = start_server(args.port, home)
    run = Run(args.port, home, args.out)
    where = "一時 home の daemon" if args.self_test else f"本番（127.0.0.1:{args.port}）"
    print(f"▶ 出勤の口の 1 往復: {where}・試しの Bot `{BOT_ID}`", flush=True)
    try:
        try:
            run.office()
        except OSError as err:
            print(f"  ✗ {where} に届かない: {err}", flush=True)
            return 2
        if BOT_ID in run.ledger():
            print(f"  ✗ 台帳に `{BOT_ID}` が既にある＝始めない（前の回の残りなら `python3 server/office_door.py remove {BOT_ID}`）", flush=True)
            return 2
        errors = []
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=SWIFTSHADER)
                page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                try:
                    run.walk(page)
                finally:
                    browser.close()
            check(not errors, f"console.error / pageerror: {errors[:5]}")
            run.ok("console.error / pageerror 0")
        except Exception as err:  # noqa: BLE001 - 何で外れても後片づけをして 1 で返す
            print(f"  ✗ {type(err).__name__}: {str(err).splitlines()[0] if str(err) else ''}", flush=True)
            print(f"  （ここまで合格 {len(run.passed)} 件・撮った絵= {args.out}）", flush=True)
            return 1
        finally:
            run.cleanup()
        print(f"✅ 出勤の口の 1 往復: 全部合格（{len(run.passed)} 件・撮った絵= {args.out}）", flush=True)
        return 0
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if temp:
            temp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
