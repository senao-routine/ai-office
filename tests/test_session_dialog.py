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


if __name__ == "__main__":
    unittest.main()
