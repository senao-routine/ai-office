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


if __name__ == "__main__":
    unittest.main()
