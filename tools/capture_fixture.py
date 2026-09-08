#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcript / jobs / codex / events を匿名化して tests/fixtures/ 用に切り出す。

既定の transcript は従来どおり <実jsonl> <出力名.jsonl> [行数=12]。
jobs は state.json の許可キーだけ、codex は OFFICE_HOME（既定: ~）の
sqlite を read-only で読み本文列を取得しない。events は末尾行の sid を置換する。
--limit は codex で既定5スレッド、transcript / events で既定12行。
出力先は順に fixtures/、fixtures/claude_bg/、fixtures/codex/、fixtures/events/。
"""
import argparse
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

# 実行例（既存の位置引数もそのまま使える）:
# python3 tools/capture_fixture.py session.jsonl working.jsonl 12
# python3 tools/capture_fixture.py --kind jobs state.json working
# python3 tools/capture_fixture.py --kind codex recent --limit 2
# python3 tools/capture_fixture.py --kind events hooks.jsonl recent --limit 3

KEEP_INPUT_KEYS = {"file_path", "command", "description", "pattern", "skill",
                   "prompt", "url", "questions"}
JOB_KEYS = {"state", "detail", "tempo", "inFlight", "fan", "tokens", "name", "nameSource",
            "sessionId", "resumeSessionId", "linkScanPath", "cwd", "template", "createdAt",
            "updatedAt", "daemonShort", "cliVersion"}
EVENT_KEYS = {"v", "ts", "ev", "sid", "cwdh", "tool", "tgt", "kind", "nt", "sub",
              "task", "tsub", "ok", "src", "plen"}


def redact_text(s, n=24):
    s = re.sub(r"\s+", " ", str(s)).strip()[:n]
    return f"REDACTED({len(s)}): {s[:12]}…"


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "text":
                out[k] = redact_text(v)
            elif k in ("file_path", "url"):
                out[k] = "/tmp/redacted" + Path(str(v)).suffix
            elif k in ("command", "prompt", "description", "pattern"):
                out[k] = redact_text(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def capture_transcript(src, name, n):
    out_lines = []
    for ln in src.read_text(errors="ignore").splitlines()[-n:]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        # R85-1: custom-title（/rename名）は状態ブロック行だが parse_session が読むので保存する。
        # 名前そのものは機微になりうるため伏せ字化（構造だけ残す）。
        if d.get("type") == "custom-title":
            out_lines.append(json.dumps(
                {"type": "custom-title",
                 "customTitle": redact_text(d.get("customTitle", "")),
                 "sessionId": "sess-aaaa1111"}, ensure_ascii=False))
            continue
        # R86-F: permission-mode は❗判定の一次情報＝落とすと今後のフィクスチャが全部
        # 「モード不明」になり、誤検知の番人が静かに無意味化する（フィクスチャ第一則の穴）。
        if d.get("type") == "permission-mode":
            out_lines.append(json.dumps(
                {"type": "permission-mode",
                 "permissionMode": d.get("permissionMode", ""),
                 "sessionId": "sess-aaaa1111"}, ensure_ascii=False))
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        slim = {"type": d["type"], "cwd": "/Users/test/demo-project", "gitBranch": "main"}
        msg = d.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str):
            slim["message"] = {"role": msg.get("role", "user"), "content": redact_text(c)}
        else:
            slim["message"] = {"role": msg.get("role", ""), "content": redact(c)}
        out_lines.append(json.dumps(slim, ensure_ascii=False))
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / name
    out.write_text("\n".join(out_lines))
    print(f"✓ {out} ({len(out_lines)}行・匿名化済み)")


def capture_jobs(src):
    data = json.loads(src.read_text(encoding="utf-8"))
    slim = {k: v for k, v in data.items() if k in JOB_KEYS}
    for key in ("detail", "name"):
        if key in slim:
            slim[key] = redact_text(slim[key])
    for key, value in (("cwd", "/Users/test/demo/project"),
                       ("linkScanPath", "/tmp/redacted/sess-fixture.jsonl")):
        if key in slim:
            slim[key] = value
    if isinstance(slim.get("inFlight"), dict):
        slim["inFlight"] = {k: v for k, v in slim["inFlight"].items()
                            if k in {"tasks", "queued", "kinds", "drainableMonitors"}}
    if "fan" in slim:
        slim["fan"] = [{k: v for k, v in item.items() if k in {"kind", "startedAt", "doneAt"}}
                       for item in (slim["fan"] or []) if isinstance(item, dict)]
    return slim


def capture_codex(home, limit):
    # 列名を明記し、本文列や item_json / error_json はDBから取得しない。
    with closing(sqlite3.connect(
            (home / ".codex/state_5.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        threads = [dict(row) for row in db.execute("""
            SELECT id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                   sandbox_policy, approval_mode, tokens_used, has_user_event, archived,
                   git_branch, agent_nickname, agent_role, model, name, is_pinned
            FROM threads ORDER BY updated_at DESC, id LIMIT ?
        """, (limit,))]
        ids = [row["id"] for row in threads]
        if not ids:
            return {"threads": [], "thread_spawn_edges": [], "thread_turns": [], "thread_items": []}
        marks = ",".join("?" for _ in ids)
        edges = [dict(row) for row in db.execute(f"""
            SELECT parent_thread_id, child_thread_id, status FROM thread_spawn_edges
            WHERE parent_thread_id IN ({marks}) OR child_thread_id IN ({marks})
            ORDER BY parent_thread_id, child_thread_id
        """, ids + ids)]
    for i, thread in enumerate(threads, 1):
        thread["cwd"] = "/Users/test/demo/project"
        thread["rollout_path"] = f"/tmp/redacted/rollout-{i}.jsonl"
    with closing(sqlite3.connect(
            (home / ".codex/thread_history_1.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        turns = [dict(row) for row in db.execute(f"""
            SELECT thread_id, turn_id, rollout_ordinal, status, started_at, completed_at,
                   duration_ms, first_user_item_id, final_agent_item_id
            FROM thread_turns WHERE thread_id IN ({marks})
            ORDER BY thread_id, rollout_ordinal, turn_id
        """, ids)]
        items = [dict(row) for row in db.execute(f"""
            SELECT thread_id, turn_id, item_id, rollout_ordinal, created_at_ms,
                   item_type, updated_at_ordinal
            FROM thread_items WHERE thread_id IN ({marks})
            ORDER BY thread_id, rollout_ordinal, item_id
        """, ids)]
    for item in items:
        item["item_json"] = json.dumps({"type": item["item_type"]}, ensure_ascii=False)
    return {"threads": threads, "thread_spawn_edges": edges,
            "thread_turns": turns, "thread_items": items}


def capture_events(src, limit):
    sessions = {}
    lines = []
    for line in src.read_text(encoding="utf-8").splitlines()[-limit:]:
        event = json.loads(line)
        sid = event["sid"]
        if sid not in sessions:
            sessions[sid] = f"sess-fixture-{len(sessions) + 1}"
        # 将来フィールドが増えても prompt / command / tool_response は持ち込まない。
        slim = {k: v for k, v in event.items() if k in EVENT_KEYS}
        slim["sid"] = sessions[sid]
        lines.append(json.dumps(slim, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("transcript", "jobs", "codex", "events"),
                        default="transcript", help="入力形式（既定: transcript）")
    parser.add_argument("--limit", type=int, help="末尾行数 / 直近スレッド数（codex: 5、他: 12）")
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="transcript/events: 入力 出力名 [行数]、jobs: 入力 出力名、codex: [出力名=recent]")
    args = parser.parse_args()
    if args.kind == "codex":
        if len(args.paths) > 1:
            parser.error("codex は出力名だけを指定してください")
        name = args.paths[0] if args.paths else "recent"
    else:
        counts = (2, 3) if args.kind in ("transcript", "events") else (2,)
        if len(args.paths) not in counts:
            parser.error(f"{args.kind} は入力パスと出力名を指定してください")
        src, name = Path(args.paths[0]), args.paths[1]
    limit = args.limit if args.limit is not None else (5 if args.kind == "codex" else 12)
    if len(args.paths) == 3:
        if args.limit is not None:
            parser.error("位置引数の行数と --limit は同時に指定できません")
        try:
            limit = int(args.paths[2])
        except ValueError:
            parser.error("行数は整数で指定してください")
    # transcript の従来のスライス挙動（0・負数も含む）は維持する。
    if args.kind != "transcript" and limit < 1:
        parser.error("--limit は1以上で指定してください")
    if args.kind == "transcript":
        capture_transcript(src, name, limit)
        return
    if Path(name).name != name or name in (".", ".."):
        parser.error("出力名にディレクトリは指定できません")
    suffix = ".jsonl" if args.kind == "events" else ".json"
    if not name.endswith(suffix):
        name += suffix
    if args.kind == "jobs":
        data = capture_jobs(src)
    elif args.kind == "codex":
        data = capture_codex(Path(os.environ.get("OFFICE_HOME") or Path.home()), limit)
    else:
        data = capture_events(src, limit)
    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    directory = "claude_bg" if args.kind == "jobs" else args.kind
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / directory / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"✓ {out} (匿名化済み)")


if __name__ == "__main__":
    main()
