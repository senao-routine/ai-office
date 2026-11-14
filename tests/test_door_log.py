# -*- coding: utf-8 -*-
"""R128 #3: 出勤の口の Bot の記録（~/.claude/office_door/bot/<id>.log.jsonl）→ 作業ビュー／スマホの端末画面の行の門。

正本= docs/door.md の「記録の画面」・実装= server/source_door_log.py（office_term.session_page の bot- の分岐）。
- 7 種類の ev（checkin・report・ask・answer・instruct・expired・withdraw）が行になる（誰が・いつ・何を）
- Bot の文も人の文も伏字（scrub_output＋ホームの下のパス）を通す・HTML にしない（<img onerror> は文字のまま）
- 壊れた行・知らない行・形の違う値・大きすぎる行は黙って飛ばすか切る（落ちない）
- 差分カーソル {off,h}・遡り {off,h,i} は office_term と同じ約束。記録の作り直し（LOG_MAX）の後は reset／遡りを止める
- 読む場所は口のフォルダの中だけ: valid_id・登録した Bot だけ・口のフォルダのリンクは読まない・記録のリンクは辿らない・通常のファイルだけ
- 読むだけ（記録・状態・監査のファイルを 1 バイトも変えない・ロックのファイルも作らない）
- 呼び手（office_server の activity_page と _dialog_sealed）は bot- で transcript を探さず記録を返す・会話（dialog）は notfound のまま
すべて一時 HOME（本物の ~/.claude には触らない）。
"""
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))
import office_door as door  # noqa: E402
import office_term as ot  # noqa: E402
import source_door_log as sdl  # noqa: E402
from office_actions import scrub_output  # noqa: E402

CMD_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
BOT = "mailbox"
SESSION = "bot-" + BOT
POISON = '<img src=x onerror="window.__pwned=1">'
SECRET = "sk-ant-api03-" + "A" * 40


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="door_log_"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.assertTrue(door.add_bot(BOT, "郵便受け", "受信箱", home=self.home)["ok"])

    def page(self, after=None, before=None, session=SESSION):
        return ot.session_page(session, None, after, scrub_output, CMD_RE, before=before, home=self.home)

    def log(self):
        return door.log_path(BOT, self.home)

    def append_raw(self, *lines):
        with open(self.log(), "ab") as f:
            for ln in lines:
                f.write(ln if isinstance(ln, bytes) else (ln + "\n").encode("utf-8"))

    def report(self, i, body=""):
        self.assertTrue(door.report(BOT, f"報告 {i}", body, home=self.home)["ok"])


class RowsTest(_Home):
    def test_the_seven_events_become_rows_who_when_what(self):
        now = time.time()
        self.assertTrue(door.checkin(BOT, "受信箱を見回り中", home=self.home, now=now)["ok"])
        self.assertTrue(door.report(BOT, "請求書を 3 通確認", "送り先は 3 社", kind="warn", home=self.home, now=now + 1)["ok"])
        a = door.ask(BOT, "送ってよい？", ["はい", "いいえ"], home=self.home, now=now + 2)
        self.assertTrue(door.answer(BOT, a["askId"], "選択肢「いいえ」でお願いします。", src="local", home=self.home, now=now + 3)["ok"])
        self.assertTrue(door.instruct(BOT, "棚も見て", src="relay", home=self.home, now=now + 4)["ok"])
        a2 = door.ask(BOT, "次は？", home=self.home, now=now + 5)
        self.assertTrue(door.withdraw(BOT, a2["askId"], home=self.home, now=now + 6)["ok"])
        door.ask(BOT, "期限の相談", ttl_hours=1, home=self.home, now=now + 7)
        door.inbox(BOT, home=self.home, now=now + 7 + 3601)                 # 掃除＝期限切れ（記録に expired）
        self.assertTrue(door.checkin(BOT, "夜は止めます", state="resting", home=self.home, now=now + 3700)["ok"])
        self.assertTrue(door.report(BOT, "お知らせだけ", kind="info", home=self.home, now=now + 3701)["ok"])
        page = self.page()
        rows = page["rows"]
        got = [(r["k"], r.get("e") or r.get("as") or r.get("rk") or "") for r in rows]
        self.assertEqual(got, [("ev", "working"), ("a", "warn"), ("q", ""), ("u", "answer"), ("u", "instruct"),
                               ("q", ""), ("ev", "withdrawn"), ("q", ""), ("ev", "expired"), ("ev", "resting"), ("a", "info")])
        self.assertEqual(rows[0]["t"], "受信箱を見回り中")
        self.assertEqual(rows[1]["t"], "請求書を 3 通確認\n\n送り先は 3 社")     # 題＋空行＋本文（題は 1 行目）
        self.assertEqual((rows[2]["t"], rows[2]["o"]), ("送ってよい？", ["はい", "いいえ"]))
        self.assertEqual((rows[3]["t"], rows[3]["ch"]), ("選択肢「いいえ」でお願いします。", 1))   # 選んだ選択肢（0 始まり）
        self.assertNotIn("ch", rows[4])
        self.assertNotIn("o", rows[5])                                               # 選択肢の無い相談
        self.assertEqual(rows[9]["t"], "夜は止めます")
        self.assertEqual(rows[10]["t"], "お知らせだけ")                              # 本文の無い報告は題だけ
        self.assertTrue(all(isinstance(r["ts"], int) for r in rows))
        self.assertEqual([r["ts"] for r in rows], sorted(r["ts"] for r in rows))
        pub = ot.public(page)
        self.assertEqual((pub["reset"], pub["gap"], pub["top"], pub["beg"]), (True, False, True, None))
        blob = json.dumps(page, ensure_ascii=False)
        self.assertNotIn(a["askId"], blob)                                           # 相談の id は行に載せない
        self.assertNotIn(a2["askId"][:8], blob)

    def test_text_is_scrubbed_but_stays_text(self):
        """Bot の文も人の文も伏字を通す（秘密・ホームの下のパス）。HTML としては何もしない＝毒の文は文字のまま（描く側が textContent）。"""
        self.assertTrue(door.checkin(BOT, "見回り" + POISON, home=self.home)["ok"])
        self.assertTrue(door.report(BOT, "題" + POISON, f"/Users/someone/secret/a.pdf を送った\ntoken={SECRET}", home=self.home)["ok"])
        a = door.ask(BOT, "送る？" + POISON, ["はい" + POISON[:20], "いいえ"], home=self.home)
        self.assertTrue(a["ok"], a)
        self.assertTrue(door.answer(BOT, a["askId"], f"これを使って {SECRET} " + POISON, src="local", home=self.home)["ok"])
        self.assertTrue(door.instruct(BOT, "/Users/someone/.ssh/id_rsa を見ないで " + POISON, src="local", home=self.home)["ok"])
        rows = self.page()["rows"]
        blob = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("/Users/someone", blob)
        self.assertIn("[secret]", blob)
        self.assertIn("a.pdf", rows[1]["t"])
        for r in rows:                                                               # 毒の文は 1 字も変えずに文字のまま
            self.assertTrue(POISON in r["t"] or r["k"] == "q", r)
        self.assertIn(POISON, rows[2]["t"])
        self.assertNotIn("&lt;", blob)

    def test_broken_foreign_and_odd_lines_are_skipped_without_falling_over(self):
        self.report(1)
        self.append_raw(
            "これは JSON ではない", "[]", '"文字列"', "null", '{"ev": "unknown", "ts": 5}', '{"ts": 5}',
            '{"ev": "checkin", "doing": 5, "state": "working"}',
            '{"ev": "checkin", "doing": "寝る", "state": "sleeping"}',
            '{"ev": "checkin", "doing": "配列の状態", "state": [1]}', '{"ev": "checkin", "doing": "表の状態", "state": {"a": 1}}',
            '{"ev": ["report"], "title": "配列の ev"}', '{"ev": "report", "title": "表の種類", "kind": {"x": 1}, "ts": 7}',
            '{"ev": "checkin", "doing": "   ", "state": "working"}',
            '{"ev": "report", "title": ""}', '{"ev": "report", "title": "題", "body": 3}',
            '{"ev": "ask", "question": "q", "options": "はい"}', '{"ev": "ask"}',
            '{"ev": "answer", "text": ""}', '{"ev": "instruct"}',
            '{"ev": "answer", "text": "範囲外の選択肢", "choice": {"index": 9}}',
            '{"ev": "answer", "text": "真偽値の選択肢", "choice": {"index": true}}',
            '{"ev": "instruct", "text": "指示に choice", "choice": {"index": 0}}',
            '{"ev": "ask", "question": "選択肢に毒", "options": ["ok", 5, "", null, {"x": 1}]}',
            '{"ev": "report", "title": "時刻が文字", "ts": "1794612137"}',
            '{"ev": "report", "title": "時刻が NaN", "ts": NaN}',
            '{"ev": "report", "title": "時刻が負", "ts": -5}',
            '{"ev": "report", "title": "時刻が巨大", "ts": 1e300}',
            '{"ev": "report", "title": "時刻が真偽値", "ts": true}',
            "[" * 200000,                                                            # 深い入れ子（RecursionError）
            b"\xff\xfe broken utf-8\n",
            '{"ev": "report", "title": "最後", "kind": "nope", "ts": 1794612137.5}')
        rows = self.page()["rows"]
        self.assertEqual([r["t"] for r in rows],
                         ["報告 1", "表の種類", "範囲外の選択肢", "真偽値の選択肢", "指示に choice", "選択肢に毒",
                          "時刻が文字", "時刻が NaN", "時刻が負", "時刻が巨大", "時刻が真偽値", "最後"])
        by = {r["t"]: r for r in rows}
        self.assertEqual((by["表の種類"]["rk"], by["表の種類"]["ts"]), ("done", 7))           # 文字列でない値で落ちない（[1] で dict を引かない）
        self.assertNotIn("ch", by["範囲外の選択肢"])
        self.assertNotIn("ch", by["真偽値の選択肢"])
        self.assertNotIn("ch", by["指示に choice"])                                    # 選んだ選択肢は答えにだけ
        self.assertEqual(by["選択肢に毒"]["o"], ["ok"])
        for t in ("時刻が文字", "時刻が NaN", "時刻が負", "時刻が巨大", "時刻が真偽値"):
            self.assertNotIn("ts", by[t], t)
        self.assertEqual((by["最後"]["ts"], by["最後"]["rk"]), (1794612137, "done"))   # 知らない種類の報告は done

    def test_rows_from_lines_accepts_str_and_bytes(self):
        line = {"ev": "report", "title": "題", "body": "本文", "kind": "info", "ts": 10.9}
        for ln in (json.dumps(line, ensure_ascii=False), json.dumps(line, ensure_ascii=False).encode("utf-8")):
            self.assertEqual(sdl.rows_from_door_lines([ln], scrub_output),
                             [{"k": "a", "t": "題\n\n本文", "rk": "info", "ts": 10}])

    def test_oversized_lines_are_clipped_and_a_huge_head_is_skipped(self):
        big = "あ" * 20_000                                                          # 口の上限（600・4000 字）を大きく超える
        self.append_raw(json.dumps({"ev": "report", "title": "長い", "body": big, "ts": 1}, ensure_ascii=False),
                        json.dumps({"ev": "instruct", "text": big, "ts": 2}, ensure_ascii=False),
                        json.dumps({"ev": "ask", "question": big, "options": ["い" * 2000] * 9, "ts": 3}, ensure_ascii=False),
                        json.dumps({"ev": "checkin", "doing": big, "state": "working", "ts": 4}, ensure_ascii=False))
        rows = self.page()["rows"]
        self.assertEqual([r["k"] for r in rows], ["a", "u", "q", "ev"])
        self.assertLessEqual(len(rows[0]["t"]), sdl.REPORT_MAX)
        self.assertLessEqual(len(rows[1]["t"]), sdl.HUMAN_MAX)
        self.assertLessEqual(len(rows[2]["t"]), sdl.QUESTION_MAX)
        self.assertEqual(len(rows[2]["o"]), door.ASK_OPT_MAX)                         # 選択肢は 4 本まで
        self.assertTrue(all(len(o) <= sdl.OPTION_MAX for o in rows[2]["o"]))
        self.assertLessEqual(len(rows[3]["t"]), sdl.DOING_MAX)
        # 末尾の窓（TAIL_BYTES）より大きい 1 行が先頭にあっても、後ろの行は読める（途中の行は捨てて gap）。
        # 口の書き手は LOG_MAX で作り直す＝この形は手で置いた（壊れた・手で足した）記録だけに起きる
        self.log().write_bytes(b'{"ev":"report","title":"' + b"x" * (ot.TAIL_BYTES + 10) + b'"}\n'
                               + json.dumps({"ev": "report", "title": "報告 2", "ts": 2}, ensure_ascii=False).encode("utf-8") + b"\n")
        page = self.page()
        self.assertEqual([r["t"] for r in page["rows"]], ["報告 2"])
        self.assertTrue(page["gap"])

    def test_a_page_of_the_largest_rows_still_fits_the_envelope(self):
        """封書 1 通（office_term.ENVELOPE_BUDGET）に入らなければ、封じる側と同じ shrink の回し方で縮み切る（古い方から落として gap）。"""
        # 口の書き手は LOG_MAX（256 KiB）で古い半分を捨てる＝ここでは読み手の上限だけを見るため、行を直に置く
        def noise(i):                                                                # 行ごとに違う・圧縮の効かない字
            rnd = random.Random(i)
            return "".join(chr(0x4e00 + rnd.randrange(20000)) for _ in range(1000))
        self.log().write_bytes(b"".join(json.dumps({"ev": "instruct", "src": "local", "text": f"{i} " + noise(i),
                                                    "ts": 1700000000 + i}, ensure_ascii=False).encode("utf-8") + b"\n"
                                        for i in range(160)))
        page = self.page()
        self.assertEqual(len(page["rows"]), ot.ROWS_MAX)
        self.assertFalse(ot.fits(page))
        first_beg = ot.public(json.loads(json.dumps(page)))["beg"]
        for step in range(ot.SHRINK_STEPS + 1):
            if ot.fits(page):
                break
            self.assertTrue(ot.shrink(page, step))
        self.assertTrue(ot.fits(page))
        pub = ot.public(page)
        self.assertTrue(pub["gap"])
        self.assertGreater(pub["beg"]["off"], first_beg["off"])                      # 落とした分だけ遡りの位置も前へ
        self.assertTrue(pub["rows"][-1]["t"].startswith("159 "))                     # 新しい方が残る


class CursorTest(_Home):
    def test_reset_then_delta_then_nothing_then_half_written(self):
        self.report(1)
        first = self.page()
        self.assertEqual(([r["t"] for r in first["rows"]], first["reset"]), (["報告 1"], True))
        self.report(2)
        self.report(3)
        delta = self.page(after=first["cur"])
        self.assertEqual(([r["t"] for r in delta["rows"]], delta["reset"], delta["gap"]), (["報告 2", "報告 3"], False, False))
        nothing = self.page(after=delta["cur"])
        self.assertEqual((nothing["rows"], nothing["reset"], nothing["cur"]), ([], False, delta["cur"]))
        self.append_raw(b'{"ev":"report","title":"\xe6\x9b\xb8\xe3\x81\x8d\xe3\x81\x8b\xe3\x81\x91')   # 書きかけ（改行なし）
        half = self.page(after=delta["cur"])
        self.assertEqual((half["rows"], half["reset"], half["cur"]), ([], False, delta["cur"]))
        self.append_raw(b'"}\n')
        done = self.page(after=delta["cur"])
        self.assertEqual([r["t"] for r in done["rows"]], ["書きかけ"])

    def test_cursors_that_do_not_match_reset(self):
        self.report(1)
        self.report(2)
        cur = self.page()["cur"]
        for bad in ({"off": cur["off"] + 10 ** 6, "h": cur["h"]}, {"off": cur["off"], "h": "0" * 16},
                    {"off": cur["off"] - 1, "h": cur["h"]}, {"off": 0, "h": cur["h"]}, {"off": "1", "h": cur["h"]},
                    {"off": True, "h": cur["h"]}, {"off": cur["off"], "h": "zz"}, "1:abc", None):
            page = self.page(after=bad)
            self.assertTrue(page["reset"], bad)
            self.assertEqual([r["t"] for r in page["rows"]], ["報告 1", "報告 2"], bad)

    def test_walking_back_reproduces_every_row_then_says_top(self):
        for i in range(ot.ROWS_MAX + 2 * ot.BACK_ROWS + 7):
            self.report(i)
        page = ot.public(self.page())
        self.assertEqual((len(page["rows"]), page["gap"], page["top"]), (ot.ROWS_MAX, True, False))
        got, beg, steps = list(page["rows"]), page["beg"], 0
        while beg:
            older = ot.public(self.page(before=beg))
            self.assertTrue(older["older"])
            got = older["rows"] + got
            beg, steps = older["beg"], steps + 1
            self.assertLess(steps, 20)
        self.assertTrue(older["top"])
        whole = sdl.rows_from_door_lines(self.log().read_bytes().splitlines(), scrub_output)
        self.assertEqual(got, whole)
        self.assertEqual([r["t"] for r in got][:2], ["報告 0", "報告 1"])

    def test_after_the_log_is_rebuilt_the_old_cursors_reset_and_stop_walking_back(self):
        """記録は LOG_MAX を超えると新しい半分だけを残して作り直す（別の inode）＝前の差分カーソルは reset・遡りは None。"""
        cap = 32 * 1024                                                              # 口の書き手（_append_jsonl）と同じ作り直し
        n = 0

        def put_until_rebuilt():
            nonlocal n
            ino = self.log().stat().st_ino
            while self.log().stat().st_ino == ino:
                door._append_jsonl(self.log(), {"ts": 1700000000 + n, "ev": "report", "kind": "done",
                                                "title": f"報告 {n}", "body": "本文" * 5}, cap)
                n += 1
                self.assertLess(n, 5000)
        self.report(0)
        put_until_rebuilt()                                                          # 1 回目の作り直しの後
        before = ot.public(self.page())
        self.assertTrue(before["beg"], "作り直しの後でも行が ROWS_MAX を超えている＝遡りの位置がある")
        put_until_rebuilt()                                                          # もう 1 度作り直される
        after = self.page(after=before["cur"])
        self.assertTrue(after["reset"])
        self.assertEqual(after["rows"][-1]["t"], f"報告 {n - 1}\n\n" + "本文" * 5)
        self.assertIsNone(self.page(before=before["beg"]))                          # 遡りを止める（呼び手は空の頁）


class ConfinementTest(_Home):
    def setUp(self):
        super().setUp()
        self.report(1)

    def test_ids_that_are_not_a_bot_id_are_not_read(self):
        # 口のフォルダの外（office_door/ の 1 つ上と HOME の直下）に、読めてしまえば行になる記録を置いておく＝閉じ込めが破れたら赤
        bait = json.dumps({"ev": "report", "title": "口のフォルダの外", "ts": 1}, ensure_ascii=False) + "\n"
        (self.home / ".claude" / "outside.log.jsonl").write_text(bait, encoding="utf-8")
        (self.home / "outside.log.jsonl").write_text(bait, encoding="utf-8")
        for session in ("bot-AB", "bot-ab", "bot-", "bot-../../outside", "bot-../../../outside", "bot-mail/../mailbox",
                        "bot-mailbox/", "bot-mail box", "bot--mailbox", "bot-" + "a" * 40, "BOT-mailbox", "mailbox"):
            with self.assertRaises(FileNotFoundError, msg=session):
                sdl.term_page(session, None, scrub_output, home=self.home)

    def test_only_registered_bots_are_read(self):
        stray = door.log_path("nobody", self.home)
        stray.write_text(json.dumps({"ev": "report", "title": "登録の無い Bot の記録", "ts": 1}) + "\n", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            sdl.term_page("bot-nobody", None, scrub_output, home=self.home)
        door.ledger_path(self.home).write_text("{ 壊れた台帳", encoding="utf-8")       # 台帳が読めない＝誰も登録していない扱い
        with self.assertRaises(FileNotFoundError):
            self.page()

    def test_a_linked_log_or_folder_is_not_followed(self):
        outside = self.home / "outside"
        outside.mkdir()
        bait = outside / "bait.jsonl"
        bait.write_text(json.dumps({"ev": "report", "title": "口のフォルダの外", "ts": 1}, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        log = self.log()
        log.unlink()
        log.symlink_to(bait)                                                          # 記録そのものがリンク
        with self.assertRaises(FileNotFoundError):
            self.page()
        log.unlink()
        log.write_text(json.dumps({"ev": "report", "title": "中", "ts": 1}, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual([r["t"] for r in self.page()["rows"]], ["中"])
        bot_dir = log.parent                                                          # 口の bot/ がリンク
        moved = outside / "bot"
        bot_dir.rename(moved)
        bot_dir.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(FileNotFoundError):
            self.page()

    def test_a_fifo_in_place_of_the_log_is_refused_without_waiting(self):
        """別モデルレビュー（R128 #3）: 記録の場所が FIFO に替わっていると、open が書き手を待って止まった（通常ファイルの検査へ届かない）。"""
        import threading
        log = self.log()
        log.unlink()
        os.mkfifo(log)
        got = []

        def read():
            try:
                self.page()
                got.append("page")
            except FileNotFoundError:
                got.append("notfound")
            except Exception as ex:                                                   # noqa: BLE001
                got.append(repr(ex))

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(5)
        if t.is_alive():                                                              # 止まった読み手を放す（テストを道連れにしない）
            fd = os.open(str(log), os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            t.join(5)
            self.fail("FIFO の open で読み手が止まった（要求が返らない）")
        self.assertEqual(got, ["notfound"])

    def test_a_huge_integer_time_does_not_take_the_page_down(self):
        """別モデルレビュー（R128 #3）: ts に 10**400 のような整数があると isfinite が OverflowError＝正しい行ごと頁が取れなかった。
        （setUp が「報告 1」を 1 行書いている）"""
        huge = "1" + "0" * 400
        self.append_raw('{"ev": "report", "title": "時刻が大きすぎる", "ts": %s}' % huge,
                        '{"ev": "report", "title": "負の時刻", "ts": -%s}' % huge,
                        '{"ev": "report", "title": "真偽値の時刻", "ts": true}',
                        '{"ev": "report", "title": "無限の時刻", "ts": 1e999}')
        self.report(2)
        rows = self.page()["rows"]
        self.assertEqual([r["t"] for r in rows], ["報告 1", "時刻が大きすぎる", "負の時刻", "真偽値の時刻", "無限の時刻", "報告 2"])
        self.assertEqual(["ts" in r for r in rows], [True, False, False, False, False, True])   # 読めない時刻は付けない（行は残す）

    def test_a_missing_log_or_a_folder_in_its_place_is_notfound(self):
        log = self.log()
        log.unlink()
        with self.assertRaises(FileNotFoundError):
            self.page()
        log.mkdir()
        with self.assertRaises(FileNotFoundError):
            self.page()

    def test_reading_never_writes(self):
        self.report(2)
        a = door.ask(BOT, "相談", ttl_hours=1, home=self.home, now=time.time() - 7200)   # 期限切れのまま（読み手は掃除しない）

        def snap():
            out = {}
            for p in sorted(self.home.rglob("*")):
                st = p.lstat()
                out[str(p)] = (st.st_size, st.st_mtime_ns, st.st_mode, p.read_bytes() if p.is_file() else None)
            return out
        before = snap()
        first = self.page()
        self.page(after=first["cur"])
        self.page(before={"off": 1, "h": "0" * 16, "i": 0})
        sdl.term_page(SESSION, None, scrub_output, home=self.home)
        self.assertEqual(snap(), before, "記録の画面を読むだけで口のファイル（記録・状態・監査・ロック）が変わった")
        self.assertTrue(a["ok"])


class SessionPageTest(_Home):
    def test_bot_sessions_read_the_door_log_and_never_the_given_path(self):
        self.report(1)
        transcript = self.home / "claude.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Claude への指示"}},
                                         ensure_ascii=False) + "\n", encoding="utf-8")
        page = ot.session_page(SESSION, transcript, None, scrub_output, CMD_RE, home=self.home)
        self.assertEqual([r["t"] for r in page["rows"]], ["報告 1"])
        with self.assertRaises(FileNotFoundError):                                   # 形の違う bot- は transcript に落ちない
            ot.session_page("bot-X", transcript, None, scrub_output, CMD_RE, home=self.home)
        old = ot.session_page("sess-claude-1", transcript, None, scrub_output, CMD_RE)  # 従来の経路はそのまま
        self.assertEqual([r["t"] for r in old["rows"]], ["Claude への指示"])


# ── 呼び手（office_server）: Mac の GET /api/session/activity とスマホの封書（kind:"term"）─────────────────────
def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="door_log_server_"))
        (cls.home / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
        cls.env = mock.patch.dict(os.environ, {"OFFICE_HOME": str(cls.home), "OFFICE_DATA": str(cls.home),
                                               "OFFICE_CONFIG": str(cls.home / "office_config.json"),
                                               "OFFICE_LANG": "ja", "OFFICE_SOURCES_CODEX": "0", "OFFICE_AGENTS_CLI": "0"})
        cls.env.start()
        cls.office = _load("office_server_door_log_t", SERVER / "office_server.py")

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        o = self.office
        self.door_home = Path(tempfile.mkdtemp(prefix="door_log_srv_"))
        self.addCleanup(shutil.rmtree, self.door_home, True)
        for target, value in (("_HOME", self.door_home),
                              ("_session_transcript", mock.Mock(side_effect=AssertionError("bot- で transcript を探した")))):
            pt = mock.patch.object(o, target, value)
            pt.start()
            self.addCleanup(pt.stop)
        self.assertTrue(door.add_bot(BOT, "郵便受け", home=self.door_home)["ok"])
        self.assertTrue(door.checkin(BOT, "見回り中", home=self.door_home)["ok"])
        self.assertTrue(door.report(BOT, "題" + POISON, f"token={SECRET}", home=self.door_home)["ok"])

    def activity(self, **qs):
        return self.office.activity_page({k: [v] for k, v in dict(session=SESSION, **qs).items()})

    def test_activity_returns_the_record_and_its_cursors(self):
        ok, page = self.activity()
        self.assertTrue(ok, page)
        self.assertEqual([(r["k"], r["t"]) for r in page["rows"]], [("ev", "見回り中"), ("a", "題" + POISON + "\n\ntoken=[secret]")])
        self.assertEqual((page["reset"], page["top"], page["beg"]), (True, True, None))
        self.assertNotIn(SECRET, json.dumps(page))
        cur = "%d:%s" % (page["cur"]["off"], page["cur"]["h"])
        self.assertTrue(door.instruct(BOT, "棚も見て", src="local", home=self.door_home)["ok"])
        ok, delta = self.activity(after=cur)
        self.assertEqual((ok, [r["t"] for r in delta["rows"]], delta["reset"]), (True, ["棚も見て"], False))
        ok, back = self.activity(before="1:%s:0" % ("0" * 16))                      # 合わない遡り＝止める
        self.assertEqual((ok, back["rows"], back["beg"], back["older"]), (True, [], None, True))

    def test_activity_for_a_bot_it_cannot_read_is_an_empty_page(self):
        for session in ("bot-nobody", "bot-AB", "bot-x"):
            ok, page = self.office.activity_page({"session": [session]})
            self.assertTrue(ok, session)
            self.assertEqual((page["rows"], page["reset"]), ([], True), session)
        ok, why = self.office.activity_page({"session": ["bot-../x"]})               # 形の違う session は 400 のまま
        self.assertEqual((ok, why), (False, "bad session"))

    @unittest.skipUnless(ot.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_the_phone_gets_the_same_rows_sealed_and_dialog_stays_closed(self):
        o, ds = self.office, self.office.dialog_seal
        secret, device = "5e" * 32, "dev-door-0001"
        devices = {"version": 1, "devices": {device: {"secret": secret, "label": "phone", "expires": 4102444799}}}
        config = {"projects": {}, "dialogRelay": True}
        saved = (ds.BUNDLES_FILE,)
        ds.BUNDLES_FILE = self.door_home / ".claude" / "office_dialog_bundles.json"
        ds._RESERVED.clear()
        ds._FILE_CACHE.update(key=None, rows=[])

        def restore():
            ds.BUNDLES_FILE = saved[0]
            ds._RESERVED.clear()
            ds._FILE_CACHE.update(key=None, rows=[])
        self.addCleanup(restore)
        audit = []
        for target, value in (("load_config", lambda: config), ("load_devices", lambda: devices)):
            pt = mock.patch.object(o, target, value)
            pt.start()
            self.addCleanup(pt.stop)
        pt = mock.patch.object(o.office_actions, "_audit", audit.append)
        pt.start()
        self.addCleanup(pt.stop)

        def ask(req, kind="term", session=SESSION, after=None):
            return o._dialog_sealed({"action": {"aioffice": 1, "kind": kind, "reqId": req, "session": session, "after": after},
                                     "device_id": device})
        ok, msg, extra = ask("d1" * 16)
        self.assertEqual((ok, msg), (True, "sealed"))
        self.assertNotIn("題", json.dumps(extra, ensure_ascii=False))                 # 平文は封書の外に無い
        page = ds.unseal(secret, device, SESSION, extra["bundle"])
        _, local = self.activity()
        self.assertEqual(page["rows"], local["rows"])                                 # Mac の中央とスマホが同じ行を見る
        self.assertEqual((page["kind"], page["session"]), ("term", SESSION))
        self.assertNotIn("題", json.dumps(audit, ensure_ascii=False))                 # 監査にも本文は無い
        ok, msg, extra = ask("d2" * 16, kind="dialog")                                 # 会話ビューは Bot には開けない（封じない）
        self.assertEqual((extra["bundle"].get("err"), "b" in extra["bundle"]), ("notfound", False), "Bot の会話ビューを封じた")
        ok, msg, extra = ask("d3" * 16, session="bot-nobody")
        self.assertEqual(extra["bundle"].get("err"), "notfound")
        config["dialogRelay"] = False                                                  # 「スマホに会話を見せる」が OFF＝封じない
        ok, msg, extra = ask("d4" * 16)
        self.assertEqual(extra["bundle"].get("err"), "denied")


if __name__ == "__main__":
    unittest.main()
