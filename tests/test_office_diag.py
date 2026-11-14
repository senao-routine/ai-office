# -*- coding: utf-8 -*-
"""R105: β の「診断情報をコピー」（server/office_diag.py）の門。

- 本文・フォルダ名・プロジェクト名・セッション名・URL・トークンが文に混ざらない（office_json に全部入れて確かめる）
- 数（セッション・プロジェクト・状態・読み取り元・中継の %）が合う
- ログは行の中身を写さず、警告の行数と例外の型の名前だけ
- 壊れた形（None・型違い）でも落ちない
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import office_diag as od  # noqa: E402

SECRET_BITS = [
    "/Users/someone/project/secret-app", "secret-app", "ひみつの企画", "sess-abcdef123456",
    "please refactor the billing module", "sk-ant-api03-XXXXXXXX", "https://example.invalid/hook",
    "パスワードは 1234", "someone@example.invalid",
]


def office():
    emp = lambda i, st: {"session": f"sess-abcdef12345{i}", "state": st, "name": "ひみつの企画",
                         "cwd": "/Users/someone/project/secret-app", "title": "please refactor the billing module",
                         "feed": ["パスワードは 1234 https://example.invalid/hook"], "question": "sk-ant-api03-XXXXXXXX"}
    return {
        "v": 2, "app": {"version": "3.0.0-rc.1"},
        "officeName": "someone@example.invalid",
        "setup": {"hookInstalled": True, "eventsWired": False, "approvalWired": True},
        "counts": {"working": 2, "waiting": 1, "resting": 1},
        "sources": {"claude": {"fg": 3, "bg": 1, "agentsCli": False}, "codex": {"connected": True, "n": 2},
                    "openclaw": {"connected": False}},
        "relay": {"rows": 10, "limit": 100000, "pct": 7, "level": 0},
        "roster": [{"name": "ひみつの企画", "projectId": "abc", "sessions": [{"session": "sess-abcdef123456"}]},
                   {"name": "secret-app", "projectId": "def", "sessions": []}],
        "employees": [emp(0, "working"), emp(1, "working"), emp(2, "waiting"), emp(3, "resting")],
        "history": [{"text": "please refactor the billing module", "to": "secret-app"}],
        "templates": [{"label": "x", "text": "パスワードは 1234"}],
    }


ENV = {"os": "macOS 15.1", "arch": "arm64", "python": "3.14.0", "logs": {}, "now": 1_800_000_000}


class DiagTest(unittest.TestCase):
    def test_no_names_paths_or_bodies(self):
        for lang in ("ja", "en"):
            text = od.build(office(), ENV, lang)
            for bit in SECRET_BITS:
                self.assertNotIn(bit, text, f"{lang}: {bit!r} が診断に混ざった")
            self.assertNotIn("/Users/", text)
            self.assertNotIn("http", text)

    def test_counts(self):
        text = od.build(office(), ENV, "ja")
        self.assertIn("版: 3.0.0-rc.1（データ v2）", text)
        self.assertIn("Mac: macOS 15.1 / arm64 / Python 3.14.0", text)
        self.assertIn("指示の受け取り=OK ／ 動きの記録=未設定 ／ 許可の代理応答=OK", text)
        self.assertIn("スマホ中継: 設定あり ／ 今日の使用量 7%", text)
        self.assertIn("セッション: 4（作業中 2・指示待ち 1・休憩 1）／ プロジェクト 2", text)
        self.assertIn("Claude Code 前面 3・裏 1 ／ Codex 2 ／ OpenClaw なし", text)
        en = od.build(office(), ENV, "en")
        self.assertIn("Sessions: 4 (working 2, waiting 1, resting 1) / projects 2", en)

    def test_relay_unset_and_broken_shapes(self):
        o = office()
        o["relay"] = None
        self.assertIn("スマホ中継: 未設定", od.build(o, ENV, "ja"))
        for bad in (None, [], "x", {"counts": "x", "setup": None, "sources": [], "roster": "y", "employees": 3}):
            text = od.build(bad, {}, "ja")
            self.assertIn("AI Office 診断情報", text)
            self.assertIn("セッション: 0", text)

    def test_logs_count_kinds_without_copying_lines(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "office.daemon.log").write_text(
                "⚠ watch_loop: [Errno 2] No such file: '/Users/someone/project/secret-app/x.jsonl'\n"
                "Traceback (most recent call last):\n"
                "ConnectionResetError: [Errno 54] peer https://example.invalid/hook\n"
                "ok line ひみつの企画\n", encoding="utf-8")
            Path(d, "relay.daemon.log").write_text("[2026-10-03 18:07:29] 🔌 WS接続\nTimeoutError: x\n",
                                                   encoding="utf-8")
            env = dict(ENV, logs=od.log_summary(d))
            self.assertEqual(env["logs"]["office.daemon.log"]["warn"], 3)
            self.assertEqual(env["logs"]["office.daemon.log"]["kinds"], {"ConnectionResetError": 1})
            text = od.build(office(), env, "ja")
            self.assertIn("ログ（本体）: 警告の行 3（ConnectionResetError ×1）", text)
            self.assertIn("ログ（中継の配達員）: 警告の行 1（TimeoutError ×1）", text)
            for bit in SECRET_BITS + ["No such file", "peer", "WS接続"]:
                self.assertNotIn(bit, text)

    def test_auto_recovering_ws_disconnects_are_counted_apart_from_warnings(self):
        """plan.md 追加（E 節の後）: relay_agent.py の『⚠ WS切断（…連続N回目）』は自動で戻る
        （1日15〜20回が平常）。これを素の警告行と同じ数に混ぜると、本人には実際より多く見える。"""
        with tempfile.TemporaryDirectory() as d:
            Path(d, "relay.daemon.log").write_text(
                "⚠ WS切断（120s生存・連続1回目）: ConnectionResetError peer closed\n"
                "⚠ WS切断（45s生存・連続2回目）: TimeoutError\n"
                "Traceback (most recent call last):\n"
                "ConnectionResetError: [Errno 54]\n", encoding="utf-8")
            logs = od.log_summary(d)
            self.assertEqual(logs["relay.daemon.log"]["wsReconnect"], 2)
            self.assertEqual(logs["relay.daemon.log"]["warn"], 2)      # Traceback 行＋ConnectionResetError 行だけ
            env = dict(ENV, logs=logs)
            text = od.build(office(), env, "ja")
            self.assertIn("警告の行 2", text)
            self.assertIn("自動で戻った切断 2 回", text)
            self.assertNotIn("peer closed", text)
            en = od.build(office(), env, "en")
            self.assertIn("auto-recovered disconnects 2", en)

    def test_missing_log_dir(self):
        self.assertEqual(od.log_summary(None), {})
        self.assertEqual(od.log_summary("/nonexistent/dir/for/test"), {})
        self.assertIn("ログ: 見つかりません", od.build(office(), ENV, "ja"))

    def test_collect_env_shape(self):
        env = od.collect_env(None)
        self.assertEqual(set(env), {"os", "arch", "python", "logs", "now"})
        self.assertNotIn("/Users/", repr(env))


if __name__ == "__main__":
    unittest.main()
