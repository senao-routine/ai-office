#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開リポの「効いたか」を同じ物差しで読む（R129・2026-11-14）。

  python3 tools/repo_stats.py            # 今の数字を 1 行で出し、logs/repo_stats.jsonl に 1 行足す
  python3 tools/repo_stats.py --no-log   # 出すだけ
  python3 tools/repo_stats.py --history  # 記録した行を古い順に並べる（公開の前後の比べ）

読むもの（`gh api`・本人のログイン）: star・fork・watch・最後の push／clone と閲覧（GitHub が持つ直近 14 日）／
Release ごとの zip の DL 数／どこから来たか（referrer 上位 5）。traffic は書き込み権のある人にしか見えない。
記録は手元の logs/（gitignore）。公開の前後の節目の数字は ROADMAP の R129 にも写す（ROADMAP は公開物に入らない）。
"""
import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = "senao-routine/ai-office"
LOG = ROOT / "logs" / "repo_stats.jsonl"


def gh_api(path, runner=subprocess.run):
    """gh api の JSON。読めなければ None（traffic の権限なし・通信の失敗＝その欄だけ空にする）。"""
    try:
        res = runner(["gh", "api", path], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return None


def collect(repo=REPO, api=gh_api, now=None):
    base = api(f"repos/{repo}") or {}
    clones = api(f"repos/{repo}/traffic/clones") or {}
    views = api(f"repos/{repo}/traffic/views") or {}
    releases = api(f"repos/{repo}/releases") or []
    refs = api(f"repos/{repo}/traffic/popular/referrers") or []
    when = now or dt.datetime.now(dt.timezone.utc).astimezone()
    return {
        "at": when.isoformat(timespec="minutes"),
        "repo": repo,
        "stars": base.get("stargazers_count"),
        "forks": base.get("forks_count"),
        "watchers": base.get("subscribers_count"),
        "pushed": base.get("pushed_at"),
        "clones14": clones.get("count"), "cloners14": clones.get("uniques"),
        "views14": views.get("count"), "viewers14": views.get("uniques"),
        "releases": [{"tag": r.get("tag_name"), "dl": sum(int(a.get("download_count") or 0) for a in r.get("assets") or [])}
                     for r in releases if isinstance(r, dict)],
        "referrers": [{"from": r.get("referrer"), "n": r.get("count"), "who": r.get("uniques")}
                      for r in refs[:5] if isinstance(r, dict)],
    }


def line(s):
    dash = lambda v: "—" if v is None else str(v)  # noqa: E731
    rel = "・".join(f"{r['tag']} {r['dl']}" for r in s["releases"]) or "なし"
    ref = "・".join(f"{r['from']} {r['n']}" for r in s["referrers"]) or "—"
    return (f"{s['at']}  star {dash(s['stars'])}・fork {dash(s['forks'])}・watch {dash(s['watchers'])}"
            f"｜clone 14 日 {dash(s['clones14'])}（{dash(s['cloners14'])} 人）・閲覧 14 日 {dash(s['views14'])}（{dash(s['viewers14'])} 人）"
            f"｜zip の DL {rel}｜来た所 {ref}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="公開リポの数字を読む（star・fork・clone・閲覧・DL）")
    ap.add_argument("--no-log", action="store_true", help="logs/repo_stats.jsonl に足さない")
    ap.add_argument("--history", action="store_true", help="記録した行を古い順に並べる")
    args = ap.parse_args(argv)
    if args.history:
        if not LOG.exists():
            print("記録はまだありません")
            return 0
        for raw in LOG.read_text(encoding="utf-8").splitlines():
            try:
                print(line(json.loads(raw)))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return 0
    s = collect()
    if s["stars"] is None:
        print(f"✗ {REPO} を読めません（gh のログイン・通信を確かめる）", file=sys.stderr)
        return 1
    print(line(s))
    if not args.no_log:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
