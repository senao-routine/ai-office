# -*- coding: utf-8 -*-
"""R86-B: シート会話ビューアの抽出純関数 dialog_from_lines と _session_transcript。

設計: 会話本文を返すのは GET /api/session/dialog だけ＝office_json に載せない
（中継へ乗る経路が構造的に存在しない・redaction 非依存）。フィクスチャ第一則により
実装より先に書いた。
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

os.environ.setdefault("OFFICE_HOME", tempfile.mkdtemp(prefix="office_dialog_home_"))
spec = importlib.util.spec_from_file_location(
    "office_server_dialog", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)

FIXTURE_LINES = (FX / "dialog_transcript.jsonl").read_text(encoding="utf-8").splitlines()


class DialogFromLinesTest(unittest.TestCase):
    def test_extraction_from_fixture(self):
        """user文字列/配列text・assistant text連結・AskUserQuestion採用、
        thinking/tool_use/tool_result/注入行/状態ブロック/壊れ行/空白のみを除外。"""
        msgs = office.dialog_from_lines(FIXTURE_LINES)
        self.assertEqual([m["role"] for m in msgs], ["user", "ai", "user", "user", "ai"])
        self.assertEqual(msgs[0]["text"], "デプロイを進めてください")
        self.assertIn("了解です。", msgs[1]["text"])
        self.assertIn("まずテストを回します。", msgs[1]["text"])
        self.assertEqual(msgs[2]["text"], "/x-post")           # コマンド実行は1行で残す
        self.assertEqual(msgs[3]["text"], "配列形式の指示です")
        self.assertIn("❓", msgs[4]["text"])
        self.assertIn("この方針で進めていい?", msgs[4]["text"])
        self.assertIn("はい / いいえ", msgs[4]["text"])
        joined = json.dumps(msgs, ensure_ascii=False)
        self.assertNotIn("内部思考", joined)                    # thinking除外
        self.assertNotIn("secret-cmd", joined)                  # tool_use除外
        self.assertNotIn("秘密のツール結果", joined)            # tool_result除外
        self.assertNotIn("ノイズ出力", joined)                  # 注入行スキップ
        self.assertNotIn("状態ブロック行は無視", joined)

    def test_clamp_and_limit(self):
        lines = [json.dumps({"type": "user",
                             "message": {"role": "user", "content": f"指示{i} " + "あ" * 500}},
                            ensure_ascii=False) for i in range(40)]
        msgs = office.dialog_from_lines(lines, limit=30, clamp=400)
        self.assertEqual(len(msgs), 30)
        self.assertTrue(msgs[0]["text"].startswith("指示10"))   # 末尾30件＝古い方が落ちる
        for m in msgs:
            self.assertLessEqual(len(m["text"]), 400)
        self.assertTrue(msgs[-1]["text"].endswith("…"))

    def test_empty_and_garbage_input(self):
        self.assertEqual(office.dialog_from_lines([]), [])
        self.assertEqual(office.dialog_from_lines(["not json", "[1,2]", '"str"']), [])


class DialogPageTest(unittest.TestCase):
    """R86-C: depth ページネーション。hasMore は「まだ古いのが在る」の唯一の根拠なので、
    2系統（バイト窓で切れた / 件数上限で切った）を独立にピンする。"""

    def test_depths_are_monotonic(self):
        """窓と件数は深いほど広い。誰かが狭く書き換えたらここで落ちる。"""
        for a, b in zip(office.DIALOG_DEPTHS, office.DIALOG_DEPTHS[1:]):
            self.assertLess(a[0], b[0], "tail bytes が単調増加でない")
            self.assertLess(a[1], b[1], "件数上限が単調増加でない")
        self.assertEqual(office.DIALOG_MAX_DEPTH, len(office.DIALOG_DEPTHS) - 1)
        # 旧名は depth0 の別名として温存（既存テスト・呼び出しを壊さない）
        self.assertEqual((office.DIALOG_TAIL_BYTES, office.DIALOG_LIMIT),
                         office.DIALOG_DEPTHS[0])

    def test_has_more_when_window_truncated(self):
        page = office.dialog_page(FIXTURE_LINES, 0, truncated=True)
        self.assertTrue(page["hasMore"], "バイト窓で切れているのに hasMore=false")
        self.assertEqual(page["depth"], 0)
        self.assertEqual(page["maxDepth"], office.DIALOG_MAX_DEPTH)

    def test_has_more_when_limit_hit(self):
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": f"m{i}"}})
                 for i in range(office.DIALOG_DEPTHS[0][1] + 5)]
        page = office.dialog_page(lines, 0, truncated=False)
        self.assertTrue(page["hasMore"], "件数上限で切ったのに hasMore=false")
        self.assertEqual(len(page["messages"]), office.DIALOG_DEPTHS[0][1])
        self.assertEqual(page["windowTotal"], office.DIALOG_DEPTHS[0][1] + 5)

    def test_no_more_at_start_of_conversation(self):
        """窓に全部収まり件数も上限未満＝会話の先頭に到達＝押せないボタンを出さない。"""
        page = office.dialog_page(FIXTURE_LINES, 0, truncated=False)
        self.assertFalse(page["hasMore"])

    def test_deeper_depth_is_superset_suffix(self):
        """深い応答は浅い応答を末尾に含む（UIが丸ごと置換しても重複/欠落が出ない根拠）。"""
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": f"m{i}"}})
                 for i in range(80)]
        shallow = office.dialog_page(lines, 0)["messages"]
        deep = office.dialog_page(lines, 1)["messages"]
        self.assertGreater(len(deep), len(shallow))
        self.assertEqual(shallow, deep[-len(shallow):], "suffix でない＝置換で会話が飛ぶ")
        self.assertEqual(shallow[-1], deep[-1], "最新メッセージが深さで変わった")

    def test_depth_is_clamped_into_range(self):
        for d in (-5, 99):
            page = office.dialog_page(FIXTURE_LINES, d)
            self.assertIn(page["depth"], range(0, office.DIALOG_MAX_DEPTH + 1))

    def test_limit_none_returns_everything(self):
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": f"m{i}"}})
                 for i in range(50)]
        self.assertEqual(len(office.dialog_from_lines(lines, limit=None)), 50)


class SessionTranscriptTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_dialog_tr_"))
        self.orig = office.PROJECTS
        office.PROJECTS = self.home / ".claude" / "projects"
        d = office.PROJECTS / "-Users-test-demo-project"
        d.mkdir(parents=True)
        (d / "sess-dlg00001.jsonl").write_text("{}", encoding="utf-8")

    def tearDown(self):
        office.PROJECTS = self.orig
        shutil.rmtree(self.home, ignore_errors=True)

    def test_resolves_known_session(self):
        p = office._session_transcript("sess-dlg00001")
        self.assertIsNotNone(p)
        self.assertTrue(str(p).endswith("sess-dlg00001.jsonl"))

    def test_unknown_session_returns_none(self):
        self.assertIsNone(office._session_transcript("sess-nothere"))

    def test_session_id_format_gate(self):
        """`.`/`/` を含むIDは正規表現で弾く（ルート側で400）。トラバーサル不能。"""
        for bad in ("../evil", "a/b", "sess.jsonl", "", "a" * 65, "日本語"):
            self.assertIsNone(office._SESSION_ID_RE.fullmatch(bad), bad)
        self.assertIsNotNone(office._SESSION_ID_RE.fullmatch("sess-dlg00001"))
        self.assertIsNotNone(office._SESSION_ID_RE.fullmatch("e5769d42-66b4-4936-a42e-7a57d75c0c6a"))


class ActivityPageTest(unittest.TestCase):
    """R121: GET /api/session/activity の中身 activity_page（作業ビュー＝スマホの封書 term と同じ office_term の行）。
    門（loopback+CSRF）は verify ▶5 のスモークが実際の HTTP で見る。ここはクエリの形・カーソル・未知 session・伏字。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_activity_"))
        self.orig = office.PROJECTS
        office.PROJECTS = self.home / ".claude" / "projects"
        d = office.PROJECTS / "-Users-test-demo-project"
        d.mkdir(parents=True)
        lines = [
            {"type": "user", "uuid": "a", "message": {"role": "user", "content": "テストを回して"}},
            {"type": "assistant", "uuid": "b", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "TOKEN=sk-ant-api03-" + "B" * 40 + " npm test", "description": "テストを回す"}}]}},
            {"type": "user", "uuid": "c", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}]}},
            {"type": "assistant", "uuid": "d", "message": {"role": "assistant", "content": [{"type": "text", "text": "通りました"}]}},
        ]
        self.path = d / "sess-act00001.jsonl"
        self.path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")

    def tearDown(self):
        office.PROJECTS = self.orig
        shutil.rmtree(self.home, ignore_errors=True)

    def page(self, **q):
        return office.activity_page({k: [v] for k, v in q.items()})

    def test_rows_carry_the_description_and_are_scrubbed(self):
        ok, p = self.page(session="sess-act00001")
        self.assertTrue(ok)
        self.assertTrue(p["reset"])
        self.assertEqual([r["k"] for r in p["rows"]], ["u", "tool", "res", "a"])
        tool = p["rows"][1]
        self.assertEqual(tool["ds"], "テストを回す")
        self.assertNotIn("B" * 20, json.dumps(p, ensure_ascii=False))     # 命令の本文の秘密は伏字
        self.assertNotIn("_offs", p)                                        # 内部の項目は外す（public）

    def test_delta_after_cursor_and_bad_cursors(self):
        ok, p = self.page(session="sess-act00001")
        cur = "%d:%s" % (p["cur"]["off"], p["cur"]["h"])
        ok, d = self.page(session="sess-act00001", after=cur)
        self.assertTrue(ok)
        self.assertEqual((d["rows"], d["reset"]), ([], False))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "uuid": "e", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "次へ"}]}}, ensure_ascii=False) + "\n")
        ok, d = self.page(session="sess-act00001", after=cur)
        self.assertEqual([r["t"] for r in d["rows"]], ["次へ"])
        for bad in ("0:" + "a" * 16, "1:" + "A" * 16, "x:y", "1:" + "a" * 15, "-1:" + "a" * 16):
            self.assertEqual(self.page(session="sess-act00001", after=bad), (False, "bad cursor"), bad)
        self.assertEqual(self.page(session="sess-act00001", after="1:" + "a" * 16, before="1:" + "a" * 16 + ":0"),
                         (False, "bad cursor"))
        ok, d = self.page(session="sess-act00001", after="1:" + "a" * 16)   # 形は合うが中身が違う＝末尾から読み直し
        self.assertTrue(ok and d["reset"])

    def test_unknown_and_malformed_sessions(self):
        self.assertEqual(self.page(session="../evil"), (False, "bad session"))
        self.assertEqual(office.activity_page({}), (False, "bad session"))
        ok, p = self.page(session="sess-nothere99")
        self.assertTrue(ok)
        self.assertEqual((p["rows"], p["cur"], p["top"]), ([], None, True))


class SealedDialogTest(unittest.TestCase):
    """R87-S2: `POST /api/dialog/sealed` の中身 `_dialog_sealed`。

    封じる前の門（許可・失効・存在）を1つずつ固定し、封じた中身が PC の `dialog_page` と
    同じであることを **unseal で確かめる**（生の page を返す枝は存在しないので、復号でしか見えない）。
    """

    def setUp(self):
        from unittest.mock import patch
        self.home = Path(tempfile.mkdtemp(prefix="office_dialog_seal_"))
        self.orig_projects = office.PROJECTS
        office.PROJECTS = self.home / ".claude" / "projects"
        d = office.PROJECTS / "-Users-test-demo-project"
        d.mkdir(parents=True)
        (d / "sess-dlg00001.jsonl").write_text("\n".join(FIXTURE_LINES), encoding="utf-8")
        self.secret = "5e" * 32
        self.device = "dev-seal-0001"
        self.devices = {"version": 1, "devices": {
            self.device: {"secret": self.secret, "label": "phone", "expires": 4102444799},
            "dev-gone-0002": {"secret": "aa" * 32, "revoked": True},
            "dev-old-0003": {"secret": "bb" * 32, "expires": 1},
        }}
        self.config = {"projects": {}, "dialogRelay": True}
        ds = office.dialog_seal
        self.saved = (ds.BUNDLES_FILE,)
        ds.BUNDLES_FILE = self.home / ".claude" / "office_dialog_bundles.json"
        ds._RESERVED.clear(); ds._FILE_CACHE.update(key=None, rows=[])
        for target, value in (("load_config", lambda: self.config),
                              ("load_devices", lambda: self.devices)):
            pt = patch.object(office, target, value); pt.start(); self.addCleanup(pt.stop)
        pt = patch.object(office.office_actions, "_audit", lambda rec: self.audit.append(rec))
        pt.start(); self.addCleanup(pt.stop)
        self.audit = []

    def tearDown(self):
        office.PROJECTS = self.orig_projects
        office.dialog_seal.BUNDLES_FILE = self.saved[0]
        office.dialog_seal._RESERVED.clear(); office.dialog_seal._FILE_CACHE.update(key=None, rows=[])
        shutil.rmtree(self.home, ignore_errors=True)

    def _ask(self, req_id="a" * 32, session="sess-dlg00001", device=None, **extra):
        data = {"action": {"aioffice": 1, "kind": "dialog", "reqId": req_id,
                           "session": session, "depth": 0, **extra},
                "device_id": self.device if device is None else device}
        return office._dialog_sealed(data)

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_sealed_page_equals_the_local_dialog_page_and_never_leaves_plaintext(self):
        ok, msg, extra = self._ask()
        self.assertEqual((ok, msg), (True, "sealed"))
        b = extra["bundle"]
        self.assertEqual(set(b), {"v", "id", "s", "i", "e", "n", "c", "b"})
        self.assertNotIn("messages", json.dumps(extra, ensure_ascii=False))   # 生の page は返らない
        page = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", b)
        local = office.dialog_page(FIXTURE_LINES, 0, truncated=False)
        self.assertEqual(page["messages"], local["messages"])
        self.assertEqual(page["session"], "sess-dlg00001")
        self.assertFalse(page["clipped"])
        self.assertEqual(self.audit[-1]["state"], "sealed")
        self.assertNotIn("text", json.dumps(self.audit[-1]))              # 監査にも本文は無い

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac（unavailable が先に返る）")
    def test_off_means_denied_and_the_capability_says_so(self):
        self.config["dialogRelay"] = False
        ok, msg, extra = self._ask()
        self.assertEqual((ok, msg, extra["bundle"]["err"]), (True, "denied", "denied"))
        self.assertNotIn("b", extra["bundle"])
        # caps.dialog は「封じられる Mac」かつ「本人が ON」のときだけ 1
        self.assertEqual(1 if (office.dialog_seal.AVAILABLE and self.config.get("dialogRelay") is True) else 0, 0)

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac（unavailable が先に返る）")
    def test_revoked_or_expired_devices_get_denied_at_the_second_gate(self):
        for dev in ("dev-gone-0002", "dev-old-0003", "dev-unknown-9"):
            ok, msg, extra = self._ask(req_id=f"{hash(dev) & 0xffffffff:032x}", device=dev)
            self.assertEqual((ok, extra["bundle"]["err"]), (True, "denied"), dev)

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac（unavailable が先に返る）")
    def test_unknown_session_is_notfound_and_malformed_is_refused_outright(self):
        ok, msg, extra = self._ask(session="sess-nothere")
        self.assertEqual(extra["bundle"]["err"], "notfound")
        for bad in ({"action": {"aioffice": 1, "kind": "run", "reqId": "a" * 32, "session": "s"}},
                    {"action": {"aioffice": 1, "kind": "dialog", "reqId": "zz", "session": "s"}},
                    {"action": {"aioffice": 1, "kind": "dialog", "reqId": "b" * 32, "session": "../x"}}):
            ok, msg, extra = office._dialog_sealed({**bad, "device_id": self.device})
            self.assertFalse(ok, bad)

    def test_approval_bundles_and_their_reasons_are_urgent_but_dialogs_are_not(self):
        """中継の自動減速（R80）でも許可の全文（kind:"ask"）とその理由は送る＝急ぎの印を付けて保存する。
        会話ビュー（dialog）は従来どおり減速中は保留（印なし）。"""
        ds = office.dialog_seal
        ok, msg, extra = self._ask(req_id="d" * 32)                     # dialog
        self.assertTrue(ok)
        ok, msg, extra = self._ask(req_id="e" * 32, kind="ask")         # 掲示が無い＝理由の封筒（noask/off…）
        self.assertTrue(ok)
        self.assertIn("err", extra["bundle"])
        ds._FILE_CACHE.update(key=None, rows=[])                         # ＝relay_agent 側
        self.assertEqual(ds.urgent_ids(), {"e" * 32})

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_a_sealed_approval_page_is_urgent(self):
        from unittest.mock import patch
        ask_id = "9" * 32
        raw = {"askId": ask_id, "session": "sess-dlg00001", "tool": "Bash", "inputCanon": "{}", "inputSha": "0" * 64,
               "inputBytes": 2, "cwd": "/Users/test/demo-project", "ts": 1, "level": "remote", "reasons": []}
        with patch.object(office, "pending_approvals", lambda session: [{"askId": ask_id}]), \
                patch.object(office, "_ask_raw", lambda session, a: raw if a == ask_id else None), \
                patch.object(office.office_approve, "seal_gate", lambda *a, **k: None), \
                patch.object(office.office_approve, "mark_seen", lambda *a, **k: None), \
                patch.object(office.office_approve, "audit", lambda *a, **k: None):
            ok, msg, extra = self._ask(req_id="f" * 32, kind="ask", askId=ask_id)
        self.assertEqual((ok, msg), (True, "sealed"))
        office.dialog_seal._FILE_CACHE.update(key=None, rows=[])
        self.assertEqual(office.dialog_seal.urgent_ids(), {"f" * 32})

    def test_errors_are_only_the_fixed_enum(self):
        for err in ("unavailable", "denied", "notfound", "toolarge", "expired"):
            self.assertIn(err, office.dialog_seal.ERRORS)
        self.config["dialogRelay"] = False
        _, _, extra = self._ask()
        self.assertIn(extra["bundle"]["err"], office.dialog_seal.ERRORS)

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_redelivery_returns_the_same_bundle_without_rereading_the_transcript(self):
        from unittest.mock import patch
        calls = []
        real = office.tail_lines
        with patch.object(office, "tail_lines", lambda *a, **k: (calls.append(1), real(*a, **k))[1]):
            _, m1, e1 = self._ask(req_id="c" * 32)
            _, m2, e2 = self._ask(req_id="c" * 32)
        self.assertEqual((m1, m2), ("sealed", "cached"))
        self.assertEqual(e1["bundle"], e2["bundle"])
        self.assertEqual(len(calls), 1)                                     # 二重に読まない

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_depth_above_one_is_clamped_to_the_configured_remote_depth(self):
        self.config["dialogRelayDepth"] = 5                                 # 不正 → 0
        _, _, extra = self._ask(req_id="d" * 32, depth=2)
        page = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", extra["bundle"])
        self.assertEqual(page["depth"], 0)


    # ── R100-1a: ターミナル画面（kind:"term"・差分カーソル・端末ごとに 1 通） ──────────────────
    def _term(self, req_id, after=None, device=None, before=None):
        act = {"aioffice": 1, "kind": "term", "reqId": req_id, "session": "sess-dlg00001", "after": after}
        if before is not None:
            act["before"] = before
        data = {"action": act, "device_id": self.device if device is None else device}
        return office._dialog_sealed(data)

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_term_walks_back_before_the_first_row(self):
        """R104-2: 開く前の行を遡る（before＝画面のいちばん上の行の位置）。封書の中だけ・内部の印（_offs・_l）は外へ出さない。"""
        f = office.PROJECTS / "-Users-test-demo-project" / "sess-dlg00001.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            if not f.read_text(encoding="utf-8").endswith("\n"):
                fh.write("\n")
            for i in range(office.office_term.ROWS_MAX + 30):
                fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "古い指示 %d" % i}},
                                    ensure_ascii=False) + "\n")
        _, _, extra = self._term("a1" * 16)
        page = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", extra["bundle"])
        self.assertTrue(page["reset"])
        self.assertIsNotNone(page["beg"])
        self.assertNotIn("_offs", page)
        _, _, e2 = self._term("b2" * 16, before=page["beg"])
        older = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", e2["bundle"])
        self.assertTrue(older["older"])
        self.assertTrue(older["rows"])
        self.assertTrue(all("_l" not in r for r in older["rows"]))
        self.assertLessEqual(len(older["rows"]), office.office_term.BACK_ROWS)
        _, _, e3 = self._term("c3" * 16, before=dict(page["beg"], h="0" * 16))       # ファイルが差し替わった
        stop = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", e3["bundle"])
        self.assertEqual((stop["rows"], stop["beg"], stop["older"]), ([], None, True))

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_term_page_is_sealed_with_a_cursor_and_the_next_request_gets_only_new_rows(self):
        ok, msg, extra = self._term("e" * 32)
        self.assertEqual((ok, msg), (True, "sealed"))
        page = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", extra["bundle"])
        self.assertEqual((page["kind"], page["reset"], page["session"]), ("term", True, "sess-dlg00001"))
        self.assertTrue(page["rows"])
        self.assertTrue({r["k"] for r in page["rows"]} <= {"u", "a", "tool", "res", "q", "cmd", "th"})   # R113-B: 考えは印 th だけ
        self.assertNotIn("内部思考は会話に出さない", json.dumps(page, ensure_ascii=False))       # 考えの本文は封書の中にも載せない
        self.assertNotIn("rows", json.dumps(extra, ensure_ascii=False))       # 平文は返らない
        self.assertEqual(self.audit[-1]["kind"], "term")
        f = office.PROJECTS / "-Users-test-demo-project" / "sess-dlg00001.jsonl"
        with f.open("a", encoding="utf-8") as fh:
            if not f.read_text(encoding="utf-8").endswith("\n"):
                fh.write("\n")                                               # 実際の transcript は行末が改行
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "追記した指示"}},
                                ensure_ascii=False) + "\n")
        _, _, e2 = self._term("f" * 32, after=page["cur"])
        p2 = office.dialog_seal.unseal(self.secret, self.device, "sess-dlg00001", e2["bundle"])
        self.assertEqual((p2["reset"], [r["t"] for r in p2["rows"]]), (False, ["追記した指示"]))

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_term_keeps_one_bundle_per_device_and_does_not_evict_dialogs(self):
        self._ask(req_id="1" * 32)                                            # 会話ビューの封書
        for i in range(6):                                                    # ターミナル画面は数秒おきに要求する
            self._term(f"{i + 2:x}" * 32)
        ids = [b["id"] for b in office.dialog_seal.live_bundles()]
        self.assertIn("1" * 32, ids, "ターミナル画面の要求が会話ビューの封書を追い出した（MAX_LIVE の食い合い）")
        self.assertEqual(len([i for i in ids if i != "1" * 32]), 1, f"同じ端末の term 封書が 1 通に置き換わらない: {ids}")
        self.assertEqual(office.dialog_seal.MAX_LIVE, 4)

    def test_sealed_kinds_are_a_closed_set(self):
        self.assertEqual(office.SEALED_KINDS, frozenset({"dialog", "term", "ask"}))   # R100-1b: ask＝許可の全文
        ok, _, _ = office._dialog_sealed({"action": {"aioffice": 1, "kind": "bogus", "reqId": "9" * 32,
                                                     "session": "sess-dlg00001"}, "device_id": self.device})
        self.assertFalse(ok)

    # ── R113-B-I: Codex（cx-）のターミナル画面＝rollout から封書（dialog は従来どおり notfound） ──────────
    CX_TID = "019a0000-0000-7000-8000-0000000000c1"
    CX_KEY = "sk-" + "q1W2e3R4t5Y6u7I8o9P0" * 2

    def _codex_home(self, extra=()):
        """self.home に Codex の置き場（state_5.sqlite の threads＋rollout）を作り、office._HOME をそこへ向ける。
        rollout＝匿名化フィクスチャ（exec_custom_tool_call）＋extra の行。"""
        import sqlite3
        from unittest.mock import patch
        roll = (self.home / ".codex" / "sessions" / "2026" / "10" / "08"
                / ("rollout-2026-10-08T01-00-00-%s.jsonl" % self.CX_TID))
        roll.parent.mkdir(parents=True, exist_ok=True)
        body = (FX / "codex_rollout" / "exec_custom_tool_call.jsonl").read_text(encoding="utf-8")
        roll.write_text(body.rstrip("\n") + "\n" + "".join(x + "\n" for x in extra), encoding="utf-8")
        db = sqlite3.connect(str(self.home / ".codex" / "state_5.sqlite"))
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL)")
                db.execute("INSERT OR REPLACE INTO threads VALUES (?, ?)", (self.CX_TID, str(roll)))
        finally:
            db.close()
        pt = patch.object(office, "_HOME", self.home); pt.start(); self.addCleanup(pt.stop)
        return roll

    def _cx(self, req_id, kind="term", session=None, after=None, before=None):
        act = {"aioffice": 1, "kind": kind, "reqId": req_id, "session": session or "cx-" + self.CX_TID, "after": after}
        if before is not None:
            act["before"] = before
        return office._dialog_sealed({"action": act, "device_id": self.device})

    def _secret_line(self, n=0):
        return json.dumps({"timestamp": "2026-10-08T02:00:%02d.000Z" % n, "type": "event_msg", "payload": {
            "type": "item_completed", "item": {
                "type": "CommandExecution", "id": "e-secret-%d" % n, "status": "completed", "exit_code": 0,
                "command": ["zsh", "-lc", "export OPENAI_API_KEY=%s && cat %s/.codex/auth.json" % (self.CX_KEY, self.home)],
                "aggregated_output": "token=%s" % self.CX_KEY}}})

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_codex_term_is_sealed_from_the_rollout(self):
        from unittest.mock import patch
        self._codex_home([self._secret_line()])
        session = "cx-" + self.CX_TID
        with patch.object(office, "_session_transcript",
                          side_effect=AssertionError("cx- で Claude の transcript を探した")):
            ok, msg, extra = self._cx("c1" * 16)
        self.assertEqual((ok, msg), (True, "sealed"))
        self.assertNotIn("rows", json.dumps(extra, ensure_ascii=False))           # 平文は返らない（封書だけ）
        page = office.dialog_seal.unseal(self.secret, self.device, session, extra["bundle"])
        self.assertEqual((page["kind"], page["session"], page["reset"]), ("term", session, True))
        kinds = {r["k"] for r in page["rows"]}
        self.assertTrue({"tool", "res", "th"} <= kinds, kinds)
        self.assertTrue(kinds <= set(office.office_term.KINDS), kinds)
        self.assertTrue(any(r.get("n") == "Shell" and "OPENAI_API_KEY" in r["t"] for r in page["rows"]))
        self.assertTrue(any(r.get("n") == "exec" and r["t"] for r in page["rows"]))   # SHOW_EXEC_INPUT＝True の既定
        text = json.dumps(page, ensure_ascii=False)
        self.assertNotIn(self.CX_KEY, text, "rollout の秘密が封書の本文に出た")
        self.assertNotIn(str(self.home), text, "ホームの絶対パスが封書の本文に出た")
        self.assertNotIn("_offs", page)
        self.assertEqual((self.audit[-1]["kind"], self.audit[-1]["state"], self.audit[-1]["session"]),
                         ("term", "sealed", session))
        self.assertNotIn(self.CX_KEY, json.dumps(self.audit))
        # 差分カーソル: 追記した行だけが次の封書に載る
        roll = next((self.home / ".codex" / "sessions").rglob("rollout-*.jsonl"))
        with roll.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-10-08T02:01:00.000Z", "type": "response_item", "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "追記した返事"}]}},
                ensure_ascii=False) + "\n")
        _, _, e2 = self._cx("c2" * 16, after=page["cur"])
        p2 = office.dialog_seal.unseal(self.secret, self.device, session, e2["bundle"])
        self.assertEqual((p2["reset"], [r["t"] for r in p2["rows"]]), (False, ["追記した返事"]))
        # 遡りのカーソルが合わない＝遡りを止める空の頁（Claude と同じ）
        _, _, e3 = self._cx("c3" * 16, before={"off": 1, "h": "0" * 16})
        stop = office.dialog_seal.unseal(self.secret, self.device, session, e3["bundle"])
        self.assertEqual((stop["rows"], stop["beg"], stop["older"]), ([], None, True))

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac")
    def test_codex_term_shrinks_into_one_envelope(self):
        """上限いっぱいの Shell の対が続く rollout も、封じる側のループ（office_term.shrink）で 1 通に入る。"""
        self._codex_home([self._secret_line(i % 60) for i in range(office.office_term.ROWS_MAX)])
        big = []
        for i in range(80):
            big.append(json.dumps({"timestamp": "2026-10-08T03:00:00.000Z", "type": "event_msg", "payload": {
                "type": "item_completed", "item": {
                    "type": "CommandExecution", "id": "big-%d" % i, "status": "failed", "exit_code": 1,
                    "command": ["zsh", "-lc", " ".join("cmd%d-%d" % (i, j) for j in range(900))],
                    "aggregated_output": "\n".join("out%d-%d %s" % (i, j, "w%d" % (j * 7 % 101)) for j in range(1500))}}}))
        roll = next((self.home / ".codex" / "sessions").rglob("rollout-*.jsonl"))
        with roll.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(big) + "\n")
        ok, msg, extra = self._cx("d1" * 16)
        self.assertEqual((ok, msg), (True, "sealed"))
        page = office.dialog_seal.unseal(self.secret, self.device, "cx-" + self.CX_TID, extra["bundle"])
        self.assertTrue(page["rows"])
        self.assertTrue(page["gap"])
        self.assertTrue(all(len(r["t"]) <= office.office_term.ARG_MAX for r in page["rows"] if r.get("n") == "Shell"),
                        "Codex の Shell の入力が縮める段（CMD_TOOLS）を通っていない")

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac（unavailable が先に返る）")
    def test_codex_unknown_thread_is_notfound_and_dialog_stays_refused(self):
        self._codex_home()
        # 形は正しいが SQLite に無い thread＝rollout が引けない＝notfound（Claude の transcript は探さない）
        _, msg, extra = self._cx("e1" * 16, session="cx-019a0000-0000-7000-8000-0000000000ff")
        self.assertEqual(extra["bundle"]["err"], "notfound")
        self.assertNotIn("b", extra["bundle"])
        # 形の違う cx-（下線は source_codex の session の形に無い）も notfound
        _, _, extra = self._cx("e2" * 16, session="cx-bad_thread_id")
        self.assertEqual(extra["bundle"]["err"], "notfound")
        # cx- の dialog（会話ビュー）は今回は開けない＝従来どおり notfound（rollout が在っても）
        _, msg, extra = self._cx("e3" * 16, kind="dialog")
        self.assertEqual((msg, extra["bundle"]["err"]), ("notfound", "notfound"))
        self.assertNotIn("b", extra["bundle"])
        self.assertEqual([a["state"] for a in self.audit[-3:]], ["notfound"] * 3)

    @unittest.skipUnless(office.dialog_seal.AVAILABLE, "封緘バックエンドが無い Mac（unavailable が先に返る）")
    def test_codex_term_keeps_the_dialog_relay_and_device_gates(self):
        """cx- でも門は同じ（本人の許可 dialogRelay・端末の失効の再検査）＝rollout を読む前に断る。"""
        from unittest.mock import patch
        self._codex_home()
        with patch.object(office.office_term, "session_page", side_effect=AssertionError("門の前で rollout を読んだ")):
            self.config["dialogRelay"] = False
            _, _, extra = self._cx("f1" * 16)
            self.assertEqual(extra["bundle"]["err"], "denied")
            self.config["dialogRelay"] = True
            for i, dev in enumerate(("dev-gone-0002", "dev-old-0003", "dev-unknown-9")):
                act = {"aioffice": 1, "kind": "term", "reqId": "f%d" % (i + 2) * 16, "session": "cx-" + self.CX_TID}
                _, _, extra = office._dialog_sealed({"action": act, "device_id": dev})
                self.assertEqual(extra["bundle"]["err"], "denied", dev)

    def test_show_flags_default_to_true(self):
        """本人裁定（2026-10-08）: exec の input（JS のコード）と Grep/Glob の検索語は伏字を通して載せる＝既定 True。"""
        import sys
        sys.path.insert(0, str(ROOT / "server"))
        try:
            import source_codex_rollout
        finally:
            del sys.path[0]
        self.assertIs(source_codex_rollout.SHOW_EXEC_INPUT, True)
        self.assertIs(office.office_term.SHOW_SEARCH_TERMS, True)


if __name__ == "__main__":
    unittest.main()
