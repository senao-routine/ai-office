"""tools/repo_stats.py（R129・公開リポの数字）: 通信せず、gh api の形から 1 行と記録の形を作れるか。"""
import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import repo_stats  # noqa: E402

FAKE = {
    "repos/x/y": {"stargazers_count": 2, "forks_count": 3, "subscribers_count": 0, "pushed_at": "2026-09-20T12:27:21Z"},
    "repos/x/y/traffic/clones": {"count": 40, "uniques": 10},
    "repos/x/y/traffic/views": {"count": 5, "uniques": 4},
    "repos/x/y/releases": [{"tag_name": "v2.1.0", "assets": [{"download_count": 2}, {"download_count": 1}]},
                           {"tag_name": "v3.0.0", "assets": []}],
    "repos/x/y/traffic/popular/referrers": [{"referrer": "github.com", "count": 7, "uniques": 3}],
}
NOW = dt.datetime(2026, 11, 14, 18, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))


class RepoStatsTest(unittest.TestCase):
    def test_collect_and_line(self):
        s = repo_stats.collect("x/y", api=FAKE.get, now=NOW)
        self.assertEqual((s["stars"], s["forks"], s["clones14"], s["cloners14"], s["views14"]), (2, 3, 40, 10, 5))
        self.assertEqual(s["releases"], [{"tag": "v2.1.0", "dl": 3}, {"tag": "v3.0.0", "dl": 0}])
        text = repo_stats.line(s)
        for part in ("star 2", "fork 3", "clone 14 日 40（10 人）", "閲覧 14 日 5（4 人）", "v2.1.0 3", "github.com 7"):
            self.assertIn(part, text)

    def test_traffic_unreadable_is_dash_not_zero(self):
        """traffic は書き込み権のある人にしか見えない＝読めない欄は 0 ではなく —（無いことと 0 を混ぜない）"""
        s = repo_stats.collect("x/y", api=lambda p: FAKE.get(p) if "traffic" not in p else None, now=NOW)
        self.assertIsNone(s["clones14"])
        self.assertIn("clone 14 日 —（— 人）", repo_stats.line(s))
        self.assertEqual(s["referrers"], [])

    def test_gh_failure_returns_none(self):
        class Res:
            returncode, stdout = 1, ""
        self.assertIsNone(repo_stats.gh_api("repos/x/y", runner=lambda *a, **k: Res()))

        def boom(*a, **k):
            raise OSError("gh が無い")
        self.assertIsNone(repo_stats.gh_api("repos/x/y", runner=boom))


if __name__ == "__main__":
    unittest.main()
