# -*- coding: utf-8 -*-
"""R116-S0: 許可の流れの観測（tools/approval_flow_stats.py）の番人。合成の監査 3 本（一時 HOME＝tests/artifacts/.tmp）で:

  ① 段（remote／review／mac／unjudged）×結果（allow-tap／allow-faceid／deny／expired／mac-answered）×入口の件数
  ② 掲示→決定の秒の中央値と p90（最近順位法）・決定の記録が無いもの（ターミナル・窓切れ）は秒を出さない
  ③ 窓（--days・今日を含む）の外は数えない・壊れた行／dict でない行で落ちない・5MB で回った `.1` も読む
  ④ 出力に本文・コマンド・パス・セッション id・askId・端末 id が 1 つも出ない（表と JSON の両方・CLI を本当に起動）
  ⑤ 読むだけ（監査のファイルを 1 バイトも変えない）
本番の ~/.claude には触らない（--home と --now を必ず渡す）。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "approval_flow_stats.py"
sys.path.insert(0, str(ROOT / "tools"))
import approval_flow_stats as afs  # noqa: E402

NOW = float(int(datetime.datetime(2026, 10, 9, 15, 0, 0).timestamp()))   # 固定の「今」（ローカル時刻の午後）
TODAY = datetime.date.fromtimestamp(NOW).isoformat()
TWO_AGO = (datetime.date.fromtimestamp(NOW) - datetime.timedelta(days=2)).isoformat()
SECRET_SESSION = "sess-SECRET-1234"
SECRET_DEVICE = "d_0123456789ab"
SECRET_BODY = "rm -rf /Users/someone/secret-project"


def aid(n):
    return f"{n:02x}" * 16


class ApprovalFlowStatsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / "tests" / "artifacts" / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        cls.home = Path(tempfile.mkdtemp(prefix="flowstats_", dir=str(base)))
        cl = cls.home / ".claude"
        cl.mkdir()
        t = NOW - 3600

        def ask(n, level, reasons=(), kind="permission", ts=t):
            return {"v": 1, "ev": "ask", "tool": "Bash", "kind": kind, "level": level, "reasons": list(reasons),
                    "ts": int(ts), "askId": aid(n), "session": SECRET_SESSION}

        def decide(n, src, decision, after, ts=t, **kw):
            return dict({"v": 1, "ev": "decide", "src": src, "decision": decision, "reason": "", "ts": int(ts + after),
                         "askId": aid(n)}, **kw)

        hook = [
            ask(1, "remote"), decide(1, "remote", "allow", 12, mode="tap"),
            ask(2, "review"), decide(2, "remote", "allow", 30),                      # 無印＝R114 より前のパスキーの署名
            ask(3, "mac", ["delete"]), decide(3, "remote", "allow", 50, mode="sig"),
            ask(4, "mac", ["pipe-exec"]),                                            # 判定できない形＝Mac だけ・決定なし
            ask(5, "review"), decide(5, "relay", "deny", 20),
            ask(6, "review"), decide(6, "local", "allow", 5),
            ask(7, "remote"),                                                        # スマホの許可が窓切れ（daemon）
            ask(8, "review"), decide(8, "remote", "deny", 40, mode="tap", reason="off"),
            ask(9, "mac", kind="question"), decide(9, "relay", "deny", 9),
            ask(10, "remote", ts=NOW - 10 * 86400), decide(10, "remote", "allow", 3, ts=NOW - 10 * 86400, mode="tap"),
            ask(11, "review"), decide(11, "relay", "deny", 100),
            ask(12, "review"), decide(12, "relay", "deny", 60),
            ask(14, "review"), decide(14, "/Users/someone/x", "deny", 7),            # 入口が記号でない＝other
        ]
        lines = [json.dumps(r, ensure_ascii=False) for r in hook]
        lines[3:3] = ["not json", '{"ev": "ask", "askId": "' + aid(99)[:10], "[1, 2]", ""]
        (cl / "office_approvals_hook.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        rotated = [ask(13, "remote", ts=NOW - 2 * 86400), decide(13, "remote", "allow", 8, ts=NOW - 2 * 86400, mode="tap")]
        (cl / "office_approvals_hook.jsonl.1").write_text(
            "\n".join(json.dumps(r) for r in rotated) + "\n", encoding="utf-8")
        audit = [
            {"v": 1, "kind": "approve", "state": "refused", "reason": "expired", "askId": aid(7), "session": SECRET_SESSION,
             "device": SECRET_DEVICE, "summary": SECRET_BODY, "inputSha": "f" * 64, "reqId": "e" * 32, "ts": int(t + 200)},
            {"v": 1, "kind": "approve", "state": "granted", "mode": "sig", "askId": aid(3), "level": "mac",
             "session": SECRET_SESSION, "device": SECRET_DEVICE, "summary": SECRET_BODY, "ts": int(t + 50),
             "ux": {"e": "notif", "s": "ready-faceid", "ms": 4000, "q": 1500}},
            {"v": 1, "kind": "approve", "state": "refused", "reason": "/Users/someone/leak", "device": SECRET_DEVICE,
             "ts": int(t + 60), "ux": {"e": "notif", "s": "ready", "ms": 1, "q": 0, "text": SECRET_BODY}},
            {"v": 1, "kind": "enroll", "state": "granted", "fp": "AB12-CD34", "device": SECRET_DEVICE, "ts": int(t)},
            {"v": 1, "kind": "remote_set", "state": "refused", "reason": "not-enrolled", "device": SECRET_DEVICE,
             "ts": int(t)},
            {"v": 1, "kind": "sealed", "askId": aid(2), "session": SECRET_SESSION, "device": SECRET_DEVICE,
             "level": "review", "ts": int(t + 10)},
            {"v": 1, "kind": "approve", "state": "granted", "ts": int(NOW - 10 * 86400)},                  # 窓の外
        ]
        (cl / "office_approvals_audit.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in audit) + "\n{broken\n", encoding="utf-8")
        actions = [
            {"v": 1, "kind": "ask", "state": "sealed", "session": SECRET_SESSION, "device": SECRET_DEVICE, "ts": int(t)},
            {"v": 1, "kind": "ask", "state": "unenrolled", "session": SECRET_SESSION, "device": SECRET_DEVICE, "ts": int(t)},
            {"v": 1, "kind": "term", "state": "sealed", "session": SECRET_SESSION, "ts": int(t)},
            {"kind": "hire", "state": "done", "label": SECRET_BODY, "startedAt": int(t)},
        ]
        (cl / "office_actions_audit.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in actions) + "\n", encoding="utf-8")
        cls.before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in cl.iterdir()}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def rows(self, rep):
        return {(r["day"], r["level"], r["result"], r["src"]): r for r in rep["rows"]}

    def test_levels_results_and_entrances(self):
        rep = afs.collect(self.home, 7, NOW)
        rows = self.rows(rep)
        self.assertEqual(rep["totals"], {"remote": 3, "review": 7, "mac": 1, "unjudged": 1})
        self.assertEqual(rep["results"], {"allow-tap": 2, "allow-faceid": 2, "deny": 5, "expired": 1, "mac-answered": 2})
        self.assertEqual(rows[(TODAY, "remote", "allow-tap", "remote")]["n"], 1)
        self.assertEqual(rows[(TODAY, "review", "allow-faceid", "remote")]["n"], 1)       # 無印＝Face ID
        self.assertEqual(rows[(TODAY, "mac", "allow-faceid", "remote")]["n"], 1)
        self.assertEqual(rows[(TODAY, "unjudged", "mac-answered", "terminal")]["n"], 1)
        self.assertEqual(rows[(TODAY, "review", "mac-answered", "local")]["n"], 1)
        self.assertEqual(rows[(TODAY, "remote", "expired", "remote")]["n"], 1)
        self.assertEqual(rows[(TODAY, "review", "deny", "remote")]["n"], 1)               # スマホの許可を hook が拒否
        self.assertEqual(rows[(TODAY, "review", "deny", "other")]["n"], 1)                # 記号でない入口は other
        self.assertEqual(rows[(TWO_AGO, "remote", "allow-tap", "remote")]["n"], 1)        # `.1`（回った世代）も読む
        self.assertEqual(rep["questions"], [{"day": TODAY, "src": "relay", "n": 1}])     # 質問は段に入れない

    def test_seconds_median_and_p90(self):
        rows = self.rows(afs.collect(self.home, 7, NOW))
        relay_deny = rows[(TODAY, "review", "deny", "relay")]
        self.assertEqual((relay_deny["n"], relay_deny["timed"], relay_deny["median"], relay_deny["p90"]), (3, 3, 60, 100))
        tap = rows[(TODAY, "remote", "allow-tap", "remote")]
        self.assertEqual((tap["median"], tap["p90"]), (12, 12))
        for key in ((TODAY, "unjudged", "mac-answered", "terminal"), (TODAY, "remote", "expired", "remote")):
            self.assertEqual((rows[key]["timed"], rows[key]["median"], rows[key]["p90"]), (0, None, None), key)
        self.assertEqual(afs._pct([5, 1, 3, 2, 4, 6, 7, 8, 9, 10], 0.9), 9)

    def test_window_and_phone_side(self):
        one = afs.collect(self.home, 1, NOW)
        self.assertNotIn(TWO_AGO, {r["day"] for r in one["rows"]})
        self.assertEqual(one["totals"]["remote"], 2)
        rep = afs.collect(self.home, 7, NOW)
        phone = {(r["kind"], r["state"], r["mode"], r["reason"]): r["n"] for r in rep["phone"]}
        self.assertEqual(phone, {("approve", "refused", "-", "expired"): 1, ("approve", "granted", "sig", "-"): 1,
                                 ("approve", "refused", "-", "other"): 1, ("enroll", "granted", "-", "-"): 1,
                                 ("remote_set", "refused", "-", "not-enrolled"): 1, ("sealed", "sealed", "-", "-"): 1})
        self.assertEqual({(r["state"], r["n"]) for r in rep["askRequests"]}, {("sealed", 1), ("unenrolled", 1)})

    def test_card_ux_entries_states_and_seconds(self):
        """R116-S1: daemon の監査の ux（形が正しい行だけ）を入口×状態×結果で数え、見えてから押すまでと 5 秒の待ちの秒を出す。"""
        rep = afs.collect(self.home, 7, NOW)
        self.assertEqual([(r["entry"], r["state"], r["result"], r["n"], r["median"], r["wait"]) for r in rep["card"]],
                         [("notif", "ready-faceid", "granted", 1, 4.0, 1.5)])
        self.assertNotIn(SECRET_BODY, afs.render(rep))

    def test_cli_prints_numbers_only_and_reads_only(self):
        env = dict(os.environ, NO_COLOR="1")
        outs = []
        for extra in ([], ["--json"]):
            r = subprocess.run([sys.executable, str(TOOL), "--home", str(self.home), "--now", str(NOW), "--days", "7"] + extra,
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertIn("review", outs[0])
        self.assertIn("allow-faceid", outs[0])
        self.assertEqual(json.loads(outs[1])["totals"]["review"], 7)
        for out in outs:
            for leak in (SECRET_SESSION, "SECRET", SECRET_DEVICE, "/Users", "someone", "rm -rf", "Bash", "AB12-CD34",
                         "f" * 64, "e" * 32) + tuple(aid(n) for n in range(1, 15)) + tuple(aid(n)[:8] for n in range(1, 15)):
                self.assertNotIn(leak, out, leak)
        after = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in (self.home / ".claude").iterdir()}
        self.assertEqual(after, self.before, "監査のファイルを書き換えた／足した")


if __name__ == "__main__":
    unittest.main()
