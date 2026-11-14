#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcript / jobs / codex / events / codex_rollout を匿名化して tests/fixtures/ 用に切り出す。

既定の transcript は従来どおり <実jsonl> <出力名.jsonl> [行数=12]。
jobs は state.json の許可キーだけ、codex は OFFICE_HOME（既定: ~）の
sqlite を read-only で読み本文列を取得しない。events は末尾行の sid を置換する。
--limit は codex で既定5スレッド、transcript / events で既定12行。
出力先は順に fixtures/、fixtures/claude_bg/、fixtures/codex/、fixtures/events/。
codex_rollout（R113-B）は実 rollout の窓を fixtures/codex_rollout/ へ（本文は先頭十数字＋長さ・id は固定ダミー・
パスは basename・実の日時は持ち込まない）。切り出した後は必ず grep と目視・ng_check を通してから保存する。
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
# python3 tools/capture_fixture.py --kind codex_rollout rollout-x.jsonl exec_custom_tool_call --limit 90

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


# ── R113-B: Codex の rollout（会話の行）。本文は「先頭十数字＋長さ」・id は固定ダミー・パスは basename ──────────
# 形は docs/codex-rollout-format.md。キーで扱いを決め、知らないキーの文字列も既定で縮める（漏れの既定は「縮める」）。
CODEX_ID_KEYS = {"id", "call_id", "turn_id", "thread_id", "session_id", "client_id", "process_id",
                 "agent_thread_id", "parent_thread_id", "root_turn_id", "response_id", "forked_from_id",
                 "threadId", "compaction_response_id", "window_id", "first_window_id", "previous_window_id",
                 "linkId", "connectorId", "pluginId", "hostId", "cell_id", "target", "author", "recipient"}
CODEX_KEEP_KEYS = {"type", "role", "status", "name", "phase", "cli_version", "originator", "model_provider",
                   "model", "effort", "server", "tool", "namespace", "reason", "history_mode", "kind",
                   "collaboration_mode_kind", "approval_policy", "source"}
CODEX_PATH_KEYS = {"path", "file_path", "savedPath", "move_path", "agent_path"}
CODEX_DIR_KEYS = {"cwd", "workdir"}
# 大きい・会話の行に要らない・パスや環境を抱える部分は丸ごと落とす（変換は使わない）
CODEX_DROP_KEYS = {"base_instructions", "git", "dynamic_tools", "internal_chat_message_metadata_passthrough",
                   "text_elements", "rate_limits", "info", "replacement_history", "replacement_history_metadata",
                   "retained_context", "resume_metadata", "guardian_history", "latest_token_usage_record",
                   "_meta", "sandbox_policy", "file_system_sandbox_policy", "permission_profile",
                   "active_permission_profile", "workspace_roots", "formatted_output", "parsed_cmd",
                   "memory_citation", "image_url", "result_url", "comp_hash", "personality", "timezone",
                   "current_date", "collaboration_mode", "thread_settings", "goal", "usage",
                   "thread_token_usage", "turn_token_usage", "action", "query", "revised_prompt"}
# 会話の行に関係しない上の階層の型＝payload は空にして型だけ残す（「知らない型は無視」の門に使う）
CODEX_EMPTY_TYPES = {"world_state", "token_usage_record", "inter_agent_communication_metadata", "realtime_item"}
CODEX_TIME_KEYS = {"started_at", "completed_at", "created_at", "updated_at", "archived_at"}
CODEX_TIME_MS_KEYS = {k + "_ms" for k in CODEX_TIME_KEYS}
CODEX_EPOCH = 1767225600                                   # 2026-01-01T00:00:00Z（実の日時の代わり）
CODEX_NOISE = {("event_msg", "token_count"), ("token_usage_record", None), ("world_state", None),
               ("turn_context", None), ("event_msg", "thread_settings_applied"),
               ("inter_agent_communication_metadata", None)}
_TAG_RE = re.compile(r"^<(/?[A-Za-z_][A-Za-z0-9_-]{0,40})")


class CodexRedactor:
    """1 本の rollout を通して同じ id には同じダミーを振る（道具と結果の印・写しの判定が切り出し後も成り立つ）。"""

    def __init__(self, home=None):
        home = str(home or Path.home())
        self.home = home.rstrip("/")
        self.user = Path(self.home).name
        self.ids = {}

    def dehome(self, s):
        s = str(s).replace(self.home, "/Users/test")
        return s.replace(self.user, "test") if len(self.user) >= 3 else s

    def dummy(self, key, v):
        if not isinstance(v, str) or not v:
            return v
        if v not in self.ids:
            prefix = {"call_id": "call", "turn_id": "turn", "root_turn_id": "turn"}.get(key, "id")
            self.ids[v] = "%s-%04d" % (prefix, len(self.ids) + 1)
        return self.ids[v]

    def text(self, s):
        """本文 → 「先頭 12 字＋長さ」（長さは縮める前の実の字数）。"""
        t = re.sub(r"\s+", " ", self.dehome(s)).strip()
        if t.startswith("gAAAA"):                          # 暗号化された本文（encrypted_content 等）は頭も残さない
            return "gAAAA-REDACTED"
        return f"REDACTED({len(t)}): {t[:12]}…"

    def argv(self, v):
        """[シェル, -lc, 本文] の形は シェルの basename とフラグを残し本文だけ縮める（変換が本文を取り出せるように）。"""
        return [Path(str(x)).name if i == 0 else (str(x) if i == 1 and str(x).startswith("-") else self.text(x))
                for i, x in enumerate(v)]

    def msg_text(self, s):
        """message の本文。変換が見る印（`<タグ` の注入・AGENTS.md）だけは頭に残す（本文は縮める）。"""
        t = self.dehome(s).strip()
        m = _TAG_RE.match(t)
        if m:
            return "<%s> REDACTED(%d)" % (m.group(1), len(t))
        if t.startswith("# AGENTS.md instructions"):
            return "# AGENTS.md instructions REDACTED(%d)" % len(t)
        return self.text(t)

    def lines_of(self, body, cap=200):
        """複数行の本文 → 行数を保った伏せ字（cap 行まで・書き込みの行数の検算用）。"""
        body = str(body)
        n = min(len(body.rstrip("\n").split("\n")) if body.strip("\n") else 0, cap)
        return "\n".join("REDACTED" for _ in range(n)) + ("\n" if n and body.endswith("\n") else "")

    def diff(self, unified, cap=400):
        """unified diff → 構造（@@ の見出し・+/-/空白の記号・行数）を保ち、本文だけ伏せる（cap 行まで）。"""
        out = []
        for line in str(unified).split("\n")[:cap]:
            if line.startswith("@@"):
                out.append(line.split("@@", 2)[1].join(("@@", "@@")) if line.count("@@") >= 2 else "@@")
            elif line[:1] in ("+", "-", " ") and not line.startswith(("+++", "---")):
                out.append(line[:1] + "REDACTED")
            elif line.startswith("\\"):
                out.append(line)
            elif not line:
                out.append("")
        return "\n".join(out)

    def patch(self, text):
        """apply_patch の本文 → 見出し（*** … File: basename）と記号だけ残す。"""
        out = []
        for line in self.dehome(text).split("\n"):
            m = re.match(r"^\*\*\* (Add|Update|Delete) File: (.+)$", line)
            if m:
                out.append("*** %s File: %s" % (m.group(1), Path(m.group(2).strip()).name))
            elif line.startswith("*** "):
                out.append(line.split(":", 1)[0] if ":" in line else line)
            elif line.startswith("@@"):
                out.append("@@")
            elif line[:1] in ("+", "-", " "):
                out.append(line[:1] + "REDACTED")
        return "\n".join(out)

    def value(self, v, key=None):
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items() if k not in CODEX_DROP_KEYS}
        if isinstance(v, list):
            if key == "command" and v and all(isinstance(x, str) for x in v):
                return self.argv(v)
            return [self.value(x, key) for x in v]
        if key in CODEX_TIME_KEYS | CODEX_TIME_MS_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            return CODEX_EPOCH * (1000 if key in CODEX_TIME_MS_KEYS else 1)   # 実の日時は持ち込まない（所要の duration_ms は残す）
        if not isinstance(v, str):
            return v
        if key in CODEX_ID_KEYS:
            return self.dummy(key, v)
        if key in CODEX_KEEP_KEYS:
            return self.dehome(v)[:80]
        if key in CODEX_PATH_KEYS:
            return Path(self.dehome(v)).name
        if key in CODEX_DIR_KEYS:
            return "/Users/test/demo/project"
        if key == "text":
            return self.msg_text(v)
        if key == "encrypted_content":
            return "gAAAA-REDACTED"
        return self.text(v)

    def content(self, c):
        if not isinstance(c, list):
            return self.value(c)
        out = []
        for x in c:
            if isinstance(x, dict):
                y = {k: v for k, v in x.items() if k not in CODEX_DROP_KEYS}
                if isinstance(y.get("text"), str):
                    y["text"] = self.msg_text(y["text"])
                for k in list(y):
                    if k not in ("type", "text"):
                        y[k] = self.value(y[k], k)
                out.append(y)
        return out

    def payload(self, typ, p):
        if not isinstance(p, dict) or typ in CODEX_EMPTY_TYPES:
            return {}
        pt = p.get("type")
        if typ == "event_msg" and pt == "token_count":
            return {"type": "token_count"}
        if typ == "session_meta":
            keep = {k: p[k] for k in ("id", "session_id", "cli_version", "originator", "source",
                                      "model_provider", "history_mode", "cwd", "timestamp") if k in p}
            keep.pop("timestamp", None)
            return self.value(keep)
        if typ == "turn_context":
            return self.value({k: p[k] for k in ("turn_id", "model", "effort", "cwd") if k in p})
        if typ == "compacted":
            return {"message": self.text(p.get("message", ""))}
        out = {}
        for k, v in p.items():
            if k in CODEX_DROP_KEYS:
                continue
            if k == "content":
                out[k] = self.content(v)
            elif k == "arguments" and isinstance(v, str):       # JSON の文字列＝構造を保って値だけ縮める
                try:
                    out[k] = json.dumps(self.value(json.loads(v)), ensure_ascii=False)
                except ValueError:
                    out[k] = self.text(v)
            elif k == "input" and isinstance(v, str):
                out[k] = self.patch(v) if p.get("name") == "apply_patch" else self.text(v)
            elif k == "output":
                if isinstance(v, str) and v.lstrip().startswith("{"):
                    try:
                        out[k] = json.dumps(self.value(json.loads(v)), ensure_ascii=False)
                        continue
                    except ValueError:
                        pass
                out[k] = self.content(v) if isinstance(v, list) else self.value(v, k)
            elif k == "summary":
                out[k] = self.content(v)
            elif k == "item" and isinstance(v, dict):
                out[k] = self.item(v)
            else:
                out[k] = self.value(v, k)
        return out

    def item(self, it):
        ty = it.get("type")
        out = {}
        for k, v in it.items():
            if k in CODEX_DROP_KEYS:
                continue
            if k == "changes" and isinstance(v, dict):         # FileChange: パスは basename・差分は構造だけ
                ch = {}
                for path, c in v.items():
                    c = c if isinstance(c, dict) else {}
                    y = {"type": c.get("type")}
                    if isinstance(c.get("unified_diff"), str):
                        y["unified_diff"] = self.diff(c["unified_diff"])
                    if isinstance(c.get("content"), str):
                        y["content"] = self.lines_of(c["content"])
                    if "move_path" in c:
                        y["move_path"] = Path(self.dehome(c["move_path"])).name if isinstance(c["move_path"], str) else None
                    ch[Path(self.dehome(path)).name] = y
                out[k] = ch
            elif k == "command" and isinstance(v, list):        # CommandExecution: [シェル, -lc, 本文]
                out[k] = self.argv(v)
            elif k in ("content", "summary_text", "raw_content"):
                out[k] = self.content(v)
            elif k == "result" and isinstance(v, dict):
                out[k] = {kk: (self.content(vv) if kk == "content" else self.value(vv, kk))
                          for kk, vv in v.items() if kk not in CODEX_DROP_KEYS}
            elif k == "arguments":
                out[k] = self.value(v, k)
            else:
                out[k] = self.value(v, k)
        out["type"] = ty
        return out

    def line(self, d, i):
        typ = d.get("type")
        slim = {"timestamp": "2026-01-01T00:%02d:%02d.000Z" % (i // 60 % 60, i % 60), "type": typ}
        if "payload" in d:
            slim["payload"] = self.payload(typ, d.get("payload"))
        return slim


def capture_codex_rollout(src, limit, start=None, drop_items=False, home=None):
    """実 rollout の窓（start から limit 行・既定は最初の道具の呼び出しの少し前から）を匿名化して JSONL へ。
    session_meta（先頭）は窓の外でも付ける。同じ種類の雑音（token_count 等）は窓の中で最初の 1 行だけ残す。
    drop_items＝event_msg.item_completed を落とす（item_completed の無い CLI の形を再現）。"""
    raw = []
    for ln in src.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            raw.append(d)
    if start is None:
        first = next((i for i, d in enumerate(raw)
                      if (d.get("payload") or {}).get("type") in ("function_call", "custom_tool_call")), 0)
        start = max(0, first - 4)
    window = raw[start:start + limit]
    if start > 0 and raw and raw[0].get("type") == "session_meta":
        window = [raw[0]] + window
    red = CodexRedactor(home)
    out, seen = [], set()
    for i, d in enumerate(window):
        # 落とす行も先に匿名化する＝ダミーの id と時刻は窓の中の位置で決まる（--drop-items の有無で変わらない）
        slim = red.line(d, i)
        pl = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        key = (d.get("type"), pl.get("type") if d.get("type") == "event_msg" else None)
        if key in CODEX_NOISE:
            if key in seen:
                continue
            seen.add(key)
        if drop_items and d.get("type") == "event_msg" and pl.get("type") == "item_completed":
            continue
        out.append(json.dumps(slim, ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("transcript", "jobs", "codex", "events", "codex_rollout"),
                        default="transcript", help="入力形式（既定: transcript）")
    parser.add_argument("--limit", type=int, help="末尾行数 / 直近スレッド数（codex: 5、codex_rollout: 60、他: 12）")
    parser.add_argument("--start", type=int, help="codex_rollout: 窓の先頭の行（既定: 最初の道具の呼び出しの 4 行前）")
    parser.add_argument("--drop-items", action="store_true",
                        help="codex_rollout: event_msg.item_completed を落とす（item_completed の無い CLI の形）")
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="transcript/events: 入力 出力名 [行数]、jobs/codex_rollout: 入力 出力名、codex: [出力名=recent]")
    args = parser.parse_args()
    if args.kind == "codex_rollout":
        if len(args.paths) != 2:
            parser.error("codex_rollout は入力（rollout-*.jsonl）と出力名を指定してください")
        name = args.paths[1]
        if Path(name).name != name or name in (".", ".."):
            parser.error("出力名にディレクトリは指定できません")
        name += "" if name.endswith(".jsonl") else ".jsonl"
        limit = args.limit if args.limit is not None else 60
        if limit < 1 or (args.start is not None and args.start < 0):
            parser.error("--limit は1以上・--start は0以上で指定してください")
        content = capture_codex_rollout(Path(args.paths[0]), limit, args.start, args.drop_items)
        out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "codex_rollout" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        print(f"✓ {out} ({content.count(chr(10))}行・匿名化済み＝本文は先頭十数字＋長さ・id はダミー・パスは basename)")
        return
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
