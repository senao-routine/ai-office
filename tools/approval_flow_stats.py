#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R116-S0: スマホの許可の流れの観測（監査から、段×結果×入口の件数と、掲示→決定の秒の中央値と p90 を日ごとに出す）。

なぜ要るか: R116（決裁カード）の効果と「危険な操作の Face ID を形ごとに見直すか」（R116 計画の決めてほしいこと 3）は、
いまの基準値が無いと判定できない。本人の iPhone で何が起きたかを残す手段も無かった（ループの欠け＝観測）。

読むもの（読むだけ・書かない。5MB で回った 1 世代前の `.1` も読む）:
  <home>/.claude/office_approvals_hook.jsonl   hook の監査（ev=ask: 段・理由／ev=decide: 入口 src・allow/deny・mode）＝表の芯
  <home>/.claude/office_approvals_audit.jsonl  daemon の監査（kind=approve: スマホからの許可の受理と拒否の理由・方式）
  <home>/.claude/office_actions_audit.jsonl    遠隔の操作の監査（kind=ask＝全文の要求の状態）
出すもの: 日付・段・結果・入口・件数・秒（中央値と p90）、スマホ側の試みの種類・状態・方式・理由（固定の短い記号だけ）の件数、
  R116-S1 の決裁カードの入口×状態×結果（daemon の監査の ux＝見えてから押すまでの ms・5 秒の待ちの ms の中央値と p90）。
**本文・コマンド・パス・セッション id・askId・端末 id は出さない**（読んでも表に入れない）。

段（掲示 1 件ごと・hook の ev=ask）:
  remote・review（ON の間タップ）／mac（種類で決まった危険＝毎回 Face ID）／unjudged（判定できない形＝Mac だけ。
  approval_policy.phone_approvable と同じ判定＝正本は server/approval_policy.py）。質問（AskUserQuestion・計画の承認）は段に入れず別に数える。
結果（掲示 1 件ごとに 1 つ）:
  allow-tap     hook が src=remote の allow を通した（mode=tap。未知の方式も Face ID 無しなのでここ）
  allow-faceid  hook が src=remote の allow を通した（mode=sig・無印＝R114 より前のパスキーの署名）
  deny          hook が deny を返した（入口 relay＝スマホの言葉・local＝Mac の画面・remote＝スマホの許可を hook が拒否）
  expired       hook の決定が無く、スマホの許可が封じてから 180 秒の窓切れ（daemon の reason=expired）で断られた
  mac-answered  それ以外＝Mac の画面で allow（src=local）か、オフィスを通さずに終わった（ターミナルで答えた・待ちが切れた）
入口: 決定があれば hook の src（remote／relay／local）・無ければ terminal（expired は remote）。
秒: 掲示（ev=ask の ts）→ 決定（ev=decide の ts）。決定の記録が無い結果（terminal・expired）は秒を出さない。

使い方:
    python3 tools/approval_flow_stats.py                 # 実 HOME の直近 7 日
    python3 tools/approval_flow_stats.py --days 1 --json
    python3 tools/approval_flow_stats.py --home <一時 HOME> --now <epoch>   # テスト
"""
import argparse
import datetime
import importlib.util
import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEVELS = ("remote", "review", "mac", "unjudged")
RESULTS = ("allow-tap", "allow-faceid", "deny", "expired", "mac-answered")
SRCS = ("remote", "relay", "local", "terminal")
_ID32 = re.compile(r"^[0-9a-f]{32}$")
_CODE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")          # 表に出してよい記号（理由・状態・方式）の形
UX_ENTRIES = ("home", "split", "tab", "term", "sheet", "notif")   # 正本は server/office_approve.py（形が違う行は数えない）
UX_STATES = ("ready", "ready-faceid")
_SPEC = importlib.util.spec_from_file_location("approval_policy_for_stats", ROOT / "server" / "approval_policy.py")
_POLICY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_POLICY)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _code(v):
    """表に出してよい短い記号だけ（形が違えば "other"・空は "-"）。本文やパスが紛れ込んだ行でも表に漏らさない。"""
    if v in (None, ""):
        return "-"
    return v if isinstance(v, str) and _CODE.match(v) else "other"


def read_jsonl(path):
    """1 行 1 件の JSON を dict だけ返す（壊れた行・途中まで書かれた行は飛ばす）。5MB で回った `.1` を先に読む。"""
    out = []
    for p in (Path(str(path) + ".1"), Path(path)):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        for line in data.split(b"\n"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                out.append(r)
    return out


def level_of(rec):
    """hook の ev=ask の段を 4 つに（mac のうち判定できない形は unjudged＝Mac だけ）。分からない形は None。"""
    lv = rec.get("level")
    if lv in ("remote", "review"):
        return lv
    if lv == "mac":
        reasons = [r for r in (rec.get("reasons") or []) if isinstance(r, str)]
        return "mac" if _POLICY.phone_approvable("mac", reasons) else "unjudged"
    return None


def _ux(v):
    """daemon の監査の ux（R116-S1）を数えてよい形か（office_approve._ux と同じ規則・違えば None）。"""
    if not isinstance(v, dict) or set(v) != {"e", "s", "ms", "q"} or v["e"] not in UX_ENTRIES or v["s"] not in UX_STATES:
        return None
    if not all(isinstance(v[k], int) and not isinstance(v[k], bool) and 0 <= v[k] <= 3600000 for k in ("ms", "q")):
        return None
    return v


def _day(ts):
    return datetime.date.fromtimestamp(ts).isoformat()


def _pct(vals, q):
    """最近順位法の分位（q=0.9 → p90）。空なら None。"""
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def collect(home, days, now):
    """純関数に近い集計（読むのは home の下の監査 3 本だけ）。dict を返す（本文・id は入らない）。"""
    cl = Path(home) / ".claude"
    start_day = datetime.date.fromtimestamp(now) - datetime.timedelta(days=max(1, days) - 1)
    start = datetime.datetime.combine(start_day, datetime.time()).timestamp()

    def inwin(ts):
        return ts is not None and start <= ts <= now + 60

    asks, decides = {}, {}
    for r in read_jsonl(cl / "office_approvals_hook.jsonl"):
        aid, ts = r.get("askId"), _num(r.get("ts"))
        if not (isinstance(aid, str) and _ID32.match(aid)) or ts is None:
            continue
        if r.get("ev") == "ask" and aid not in asks:
            asks[aid] = r
        elif r.get("ev") == "decide" and aid not in decides:
            decides[aid] = r
    expired = set()
    phone, card = {}, {}
    for r in read_jsonl(cl / "office_approvals_audit.jsonl"):
        ts = _num(r.get("ts"))
        if not inwin(ts) or r.get("kind") not in ("approve", "enroll", "remote_set", "sealed"):
            continue
        state = "sealed" if r.get("kind") == "sealed" else _code(r.get("state"))
        mode = _code(r.get("mode")) if r.get("kind") == "approve" else "-"
        key = (_day(ts), r.get("kind"), state, mode, _code(r.get("reason")))
        phone[key] = phone.get(key, 0) + 1
        ux = _ux(r.get("ux")) if r.get("kind") == "approve" else None
        if ux:
            c = card.setdefault((_day(ts), ux["e"], ux["s"], state), {"n": 0, "ms": [], "q": []})
            c["n"] += 1
            c["ms"].append(ux["ms"] / 1000)
            c["q"].append(ux["q"] / 1000)
        aid = r.get("askId")
        if r.get("kind") == "approve" and r.get("state") == "refused" and r.get("reason") == "expired" \
                and isinstance(aid, str) and _ID32.match(aid):
            expired.add(aid)
    asks_req = {}
    for r in read_jsonl(cl / "office_actions_audit.jsonl"):
        ts = _num(r.get("ts")) or _num(r.get("startedAt"))
        if r.get("kind") == "ask" and inwin(ts):
            k = (_day(ts), _code(r.get("state")))
            asks_req[k] = asks_req.get(k, 0) + 1

    rows, questions = {}, {}
    for aid, a in asks.items():
        ts = _num(a.get("ts"))
        if not inwin(ts):
            continue
        d = decides.get(aid)
        src = _code(d.get("src")) if d else "terminal"
        if a.get("kind") == "question":
            k = (_day(ts), src if src in SRCS else "other")
            questions[k] = questions.get(k, 0) + 1
            continue
        level = level_of(a)
        if level is None:
            continue
        secs = None
        if d:
            dts = _num(d.get("ts"))
            secs = dts - ts if dts is not None and dts >= ts else None
            if d.get("decision") == "deny":
                result = "deny"
            elif d.get("decision") == "allow" and d.get("src") == "remote":
                result = "allow-faceid" if d.get("mode") in (None, "sig") else "allow-tap"
            else:
                result = "mac-answered"
        elif aid in expired:
            result, src = "expired", "remote"
        else:
            result = "mac-answered"
        k = (_day(ts), level, result, src if src in SRCS else "other")
        row = rows.setdefault(k, {"n": 0, "secs": []})
        row["n"] += 1
        if secs is not None:
            row["secs"].append(secs)

    table = []
    for (day, level, result, src), row in sorted(rows.items(), key=lambda kv: (
            kv[0][0], LEVELS.index(kv[0][1]), RESULTS.index(kv[0][2]), kv[0][3])):
        med = statistics.median(row["secs"]) if row["secs"] else None
        table.append({"day": day, "level": level, "result": result, "src": src, "n": row["n"],
                      "timed": len(row["secs"]), "median": med, "p90": _pct(row["secs"], 0.9)})
    totals = {lv: sum(r["n"] for r in table if r["level"] == lv) for lv in LEVELS}
    return {
        "days": max(1, days), "from": start_day.isoformat(), "to": datetime.date.fromtimestamp(now).isoformat(),
        "rows": table, "totals": totals,
        "results": {res: sum(r["n"] for r in table if r["result"] == res) for res in RESULTS},
        "questions": [{"day": d, "src": s, "n": n} for (d, s), n in sorted(questions.items())],
        "phone": [{"day": d, "kind": k, "state": st, "mode": m, "reason": rs, "n": n}
                  for (d, k, st, m, rs), n in sorted(phone.items())],
        "askRequests": [{"day": d, "state": s, "n": n} for (d, s), n in sorted(asks_req.items())],
        "card": [{"day": d, "entry": e, "state": s, "result": st, "n": c["n"], "median": statistics.median(c["ms"]),
                  "p90": _pct(c["ms"], 0.9), "wait": statistics.median(c["q"])}
                 for (d, e, s, st), c in sorted(card.items())],
    }


def _sec(v):
    return "-" if v is None else str(int(round(v)))


def render(rep):
    out = [f"許可の流れ（{rep['from']}〜{rep['to']}・{rep['days']} 日・本文なし・数と段と秒だけ）", ""]
    out.append(f"{'日付':<10}  {'段':<8}  {'結果':<12}  {'入口':<8}  {'件数':>4}  {'秒あり':>4}  {'中央値秒':>8}  {'p90秒':>6}")
    for r in rep["rows"]:
        out.append(f"{r['day']:<10}  {r['level']:<8}  {r['result']:<12}  {r['src']:<8}  {r['n']:>4}  {r['timed']:>4}  "
                   f"{_sec(r['median']):>8}  {_sec(r['p90']):>6}")
    if not rep["rows"]:
        out.append("（掲示なし）")
    out.append("")
    t = rep["totals"]
    out.append("掲示 " + str(sum(t.values())) + " 件（" + "・".join(f"{lv} {t[lv]}" for lv in LEVELS) + "）／結果: "
               + "・".join(f"{k} {v}" for k, v in rep["results"].items()))
    q = rep["questions"]
    if q:
        by = {}
        for r in q:
            by[r["src"]] = by.get(r["src"], 0) + r["n"]
        out.append("質問 " + str(sum(by.values())) + " 件（入口: " + "・".join(f"{k} {v}" for k, v in sorted(by.items())) + "）")
    out.append("")
    out.append("スマホ側の試み（daemon の監査）")
    out.append(f"{'日付':<10}  {'種類':<10}  {'状態':<9}  {'方式':<4}  {'理由':<14}  {'件数':>4}")
    for r in rep["phone"]:
        out.append(f"{r['day']:<10}  {r['kind']:<10}  {r['state']:<9}  {r['mode']:<4}  {r['reason']:<14}  {r['n']:>4}")
    if not rep["phone"]:
        out.append("（なし）")
    out.append("")
    out.append("決裁カード（daemon の監査の ux・R116-S1）: 入口×状態×結果・見えてから押すまでの秒・5 秒の待ちの秒")
    out.append(f"{'日付':<10}  {'入口':<6}  {'状態':<12}  {'結果':<9}  {'件数':>4}  {'中央値秒':>8}  {'p90秒':>6}  {'待ち秒':>6}")
    for r in rep.get("card", []):
        out.append(f"{r['day']:<10}  {r['entry']:<6}  {r['state']:<12}  {r['result']:<9}  {r['n']:>4}  {r['median']:>8.1f}  "
                   f"{(r['p90'] if r['p90'] is not None else 0):>6.1f}  {r['wait']:>6.1f}")
    if not rep.get("card"):
        out.append("（なし）")
    if rep["askRequests"]:
        by = {}
        for r in rep["askRequests"]:
            by[r["state"]] = by.get(r["state"], 0) + r["n"]
        out.append("全文の要求（遠隔の操作の監査・kind=ask）: " + "・".join(f"{k} {v}" for k, v in sorted(by.items())))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="スマホの許可の流れ（監査から段×結果×入口の件数と所要秒）。本文は出さない")
    ap.add_argument("--days", type=int, default=7, help="今日を含む直近 N 日（既定 7）")
    ap.add_argument("--home", default=os.environ.get("OFFICE_HOME") or str(Path.home()),
                    help="~/.claude を持つ HOME（テスト注入・既定は OFFICE_HOME か実 HOME）")
    ap.add_argument("--now", type=float, default=None, help="「今」の epoch 秒（テスト用）")
    ap.add_argument("--json", action="store_true", help="表ではなく JSON で出す")
    a = ap.parse_args(argv)
    rep = collect(a.home, a.days, a.now if a.now is not None else time.time())
    print(json.dumps(rep, ensure_ascii=False, indent=1) if a.json else render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
