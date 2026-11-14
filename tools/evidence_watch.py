#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P4: 証拠列の分類の見張り（配備した hook が本当に分類しているかを、本番の記録から見る）。

なぜ要るか: 台帳の証拠列（committed／tested／failed）は、hook（hooks/office-event.sh）が Bash の PostToolUse に付ける
kind（`git:commit`／`git:push`／`test`）だけを根拠にする。9/21〜9/23 は Bash の PostToolUse が 1 日 1,400〜3,000 件
あったのに kind 付きは 0 件で、証拠列は黙って「—」のままだった（この repo だけでもその 3 日で 16 コミット）。
単体テスト（tests/test_event_hook.py）は「書いた形を分類できるか」しか見ない。**配備した後に分類件数 > 0 かを
見る手段が無かった**のが穴なので、本番の記録（~/.claude/office_events/*.jsonl・本文なし）を読んで数える。

判定（直近 24 時間）:
  - Bash の PostToolUse（失敗も含む）が 20 件以上あるのに kind 付きが 0 件 → ⚠（終了 4）
  - kind 付きが 1 件以上 → ✓（終了 0）
  - Bash が 20 件未満・記録が無い → 省略（終了 3。hook を入れていない Mac・公開クローンでは黙る）
読むだけ（書かない）。出力は件数と分類名だけ（パス・本文を出さない）。
呼び口: verify ▶8（実データカナリア）。日次の watchdog から呼ぶときも終了コードで分ける。

使い方:
    python3 tools/evidence_watch.py            # 本番（$HOME）を見る
    OFFICE_HOME=<一時 HOME> python3 tools/evidence_watch.py --now <epoch>   # テスト
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

WINDOW = 24 * 3600
MIN_BASH = 20
BASH_EVS = ("PostToolUse", "PostToolUseFailure")
EXIT = {"ok": 0, "skip": 3, "warn": 4}


def assess(records, now, window=WINDOW, min_bash=MIN_BASH):
    """純関数: 記録の列から {status, bash, kinds} を返す（status＝ok／warn／skip）。"""
    bash, kinds = 0, {}
    for r in records:
        if not isinstance(r, dict):
            continue
        ts = r.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            continue
        if not now - window <= ts <= now:
            continue
        if r.get("tool") != "Bash" or r.get("ev") not in BASH_EVS:
            continue
        bash += 1
        kind = r.get("kind")
        if isinstance(kind, str) and kind:
            kinds[kind] = kinds.get(kind, 0) + 1
    if bash < min_bash:
        status = "skip"
    elif not kinds:
        status = "warn"
    else:
        status = "ok"
    return {"status": status, "bash": bash, "kinds": dict(sorted(kinds.items()))}


def read_records(home, now, window=WINDOW):
    """窓に掛かる日付のファイル（hook はローカル日付で分ける）だけを読む。壊れた行・書きかけの行は飛ばす。"""
    base = Path(home) / ".claude" / "office_events"
    day = datetime.date.fromtimestamp(now - window)
    last = datetime.date.fromtimestamp(now)
    out = []
    while day <= last:
        path = base / f"{day.isoformat()}.jsonl"
        day += datetime.timedelta(days=1)
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"Bash"' not in line:              # 1 日 2 万行ある＝Bash の行だけ解析する
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    return out


def message(r, window=WINDOW, min_bash=MIN_BASH):
    hours = window // 3600
    if r["status"] == "ok":
        detail = "・".join(f"{k} {v}" for k, v in r["kinds"].items())
        return f"証拠列の分類 OK（直近 {hours} 時間: Bash {r['bash']} 件のうち {detail}）"
    if r["status"] == "warn":
        return (f"証拠列の分類が直近 {hours} 時間で 0 件（Bash の PostToolUse は {r['bash']} 件）"
                "＝配備した hook の分類が死んでいる疑い。台帳の証拠列（committed／tested）が黙って空になる。"
                "~/.claude/hooks/office-event.sh が repo の hooks/ と同じか・自分の打ち方で git commit と"
                " bash dev.sh --check を 1 回ずつ試して kind が付くかを見る（.claude/rules/hooks.md）")
    return f"証拠列の分類の見張り省略（直近 {hours} 時間の Bash が {r['bash']} 件 < {min_bash}）"


def main(argv=None):
    ap = argparse.ArgumentParser(description="証拠列の分類の見張り（読むだけ）")
    ap.add_argument("--now", type=float, default=None, help="判定の時刻（epoch 秒・テスト用）")
    args = ap.parse_args(argv)
    now = time.time() if args.now is None else args.now
    home = os.environ.get("OFFICE_HOME") or os.path.expanduser("~")
    r = assess(read_records(home, now), now)
    print(message(r))
    return EXIT[r["status"]]


if __name__ == "__main__":
    sys.exit(main())
