#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.sh 用: 使い捨ての OFFICE_HOME を組み立ててパスを出力する"""
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

FX = Path(__file__).resolve().parent / "fixtures"
home = Path(tempfile.mkdtemp(prefix="office_verify_home_"))
proj = home / ".claude" / "projects" / "-Users-test-demo-project"
proj.mkdir(parents=True)
now = time.time()
for name, fixture, age in [
    ("sess-verify0001.jsonl", "working_tool.jsonl", 10),
    ("sess-verify0002.jsonl", "waiting_said.jsonl", 600),
]:
    p = proj / name
    shutil.copy(FX / fixture, p)
    os.utime(p, (now - age, now - age))
# 6時間の出勤窓より十分古いプロジェクトも、R3一覧には出す。
old_proj = home / ".claude" / "projects" / "-Users-test-old-project"
old_proj.mkdir()
old_session = old_proj / "sess.jsonl"
old_session.write_text('{"cwd":"/Users/test/old/project"}\n', encoding="utf-8")
old_time = now - 8 * 24 * 3600
os.utime(old_session, (old_time, old_time))
os.utime(old_proj, (old_time, old_time))
# 会議状態のフィクスチャ: working セッションに部下(サブエージェント)3体
# → UIで meetingLead + .minionEl が必ず描画される（会議室のz順退行をスモークで検知するため）
sub = proj / "sess-verify0001" / "subagents"
sub.mkdir(parents=True)
for i in range(3):
    sp = sub / f"agent-{i}.jsonl"
    sp.write_text('{"type":"assistant"}\n', encoding="utf-8")
    os.utime(sp, (now - 30, now - 30))
(home / ".claude" / "office_inbox").mkdir(parents=True)
# P3ペアリング用: 中継設定を注入（pair_url が /app#... を組み立てられるように）
(home / ".claude" / "office_relay.json").write_text(
    '{"url": "https://relay.example.workers.dev", "token": "%s", "interval": 5}\n' % ("ab" * 32),
    encoding="utf-8")
# P1テスト用: 空config（OFFICE_CONFIG注入先）
(home / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
# pickme=UIフロー用のフォルダ選択モック先（OFFICE_PICK_DIR・UIは新規入社になる）
# curltest=verify.shのcurl登録用（別フォルダにしてUIフローが必ずexisting=falseになるようにする）
(home / "pickme").mkdir()
(home / "curltest").mkdir()

# リソースモニター用: Codex セッション（当日・fresh mtime）
d = datetime.date.today()
codex_sessions = home / ".codex" / "sessions" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
codex_sessions.mkdir(parents=True)
codex_fixture = Path(__file__).resolve().parent / "fixtures" / "codex_rollout.jsonl"
(codex_sessions / "rollout-test.jsonl").write_text(
    codex_fixture.read_text(encoding="utf-8"), encoding="utf-8")

# リソースモニター用: Gemini OAuth（有効期限は1時間後）
gemini = home / ".gemini"
gemini.mkdir()
(gemini / "oauth_creds.json").write_text(json.dumps({
    "access_token": "fake",
    "refresh_token": "fake-refresh",
    "expiry_date": int((time.time() + 3600) * 1000),
}), encoding="utf-8")

# リソースモニター用: 手動台帳
(home / ".claude" / "office_resources.json").write_text(json.dumps({
    "version": 1,
    "entries": [{
        "id": "higgsfield",
        "label": "Higgsfield",
        "plan": "Basic",
        "remaining": 1192,
        "total": 3000,
        "unit": "cr",
        "note": "",
        "updatedAt": int(time.time()),
    }],
}), encoding="utf-8")

# R90-H3: 次のアダプタ用。既存の transcript / rollout はそのまま残す。
def iso_at(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


job = home / ".claude" / "jobs" / "j0000001"
job.mkdir(parents=True)
job_state = {
    "state": "working", "detail": "verify を実行中", "tempo": "idle",
    "inFlight": {"tasks": 1, "queued": 0, "kinds": ["local_bash"], "drainableMonitors": 0},
    "fan": [
        {"id": "b1", "kind": "shell", "label": "bash verify.sh", "startedAt": int((now - 60) * 1000)},
        {"id": "todo:1", "kind": "todo", "label": "テストを確認", "startedAt": 0, "doneAt": 0},
    ],
    "tokens": 1200, "output": None, "children": None,
    "linkScanOffset": 0, "linkScanPath": str((proj / "sess-verify0001.jsonl").resolve()),
    "template": "bg", "intent": "動作を確認", "name": "verify-bot", "nameSource": "auto",
    "sessionId": "sess-verify0001", "resumeSessionId": "sess-verify0001",
    "daemonShort": "j0000001", "cliVersion": "2.1.258",
    "cwd": "/Users/test/demo/project", "originCwd": "/Users/test/demo/project",
    "backend": "daemon", "createdAt": iso_at(now - 600), "updatedAt": iso_at(now),
}
(job / "state.json").write_text(json.dumps(job_state, ensure_ascii=False), encoding="utf-8")
(job / "state.json").chmod(0o600)
(job / "timeline.jsonl").write_text("".join(
    json.dumps({"at": iso_at(ts), "state": "working", "detail": detail, "text": "確認中"},
               ensure_ascii=False) + "\n"
    for ts, detail in [(now - 60, "開始"), (now, "verify を実行中")]
), encoding="utf-8")
daemon = home / ".claude" / "daemon"
daemon.mkdir()
(daemon / "roster.json").write_text(json.dumps({
    "proto": 1, "supervisorPid": 123, "updatedAt": int(now * 1000),
    "workers": {"j0000001": {
        "pid": 456, "procStart": iso_at(now - 600), "sessionId": "sess-verify0001",
        "cliVersion": "2.1.258", "startedAt": int((now - 600) * 1000),
        "attempt": 1, "cwd": "/Users/test/demo/project",
    }},
}), encoding="utf-8")
(job.parent / "agents.json").write_text(json.dumps([
    {"cwd": "/Users/test/demo/project", "kind": kind, "startedAt": int((now - 600) * 1000),
     "id": short, "state": state, "pid": pid, "status": status, "waitingFor": waiting,
     "sessionId": sid, "name": name}
    for kind, short, state, pid, status, waiting, sid, name in [
        ("background", "j0000001", "working", 456, "running", "", "sess-verify0001", "verify-bot"),
        ("interactive", "i0000001", "blocked", 789, "waiting", "input needed", "sess-verify0002", "demo"),
    ]
]), encoding="utf-8")

events = home / ".claude" / "office_events"
events.mkdir(mode=0o700)
event_file = events / f"{d.isoformat()}.jsonl"
event_file.write_text("".join(json.dumps({
    "v": 1, "ts": now - 2 + i, "ev": ev, "sid": "sess-verify0001",
    "cwdh": hashlib.sha1(b"/Users/test/demo/project").hexdigest()[:12],
    "tool": tool, "tgt": target, "kind": kind, "nt": "", "sub": "", "task": "",
    "tsub": "", "ok": True, "src": "", "plen": 0,
}) + "\n" for i, (ev, tool, target, kind) in enumerate([
    ("PostToolUse", "Edit", "report.md", ""),
    ("PostToolUse", "Bash", "", "git:commit"),
    ("Stop", "", "", ""),
])), encoding="utf-8")
event_file.chmod(0o600)

epoch = int(now)
codex = home / ".codex"
rollout = str((codex_sessions / "rollout-test.jsonl").resolve())
spawn_source = json.dumps({"subagent": {"thread_spawn": {
    "parent_thread_id": "cx-parent-a", "depth": 1, "agent_path": "/root/x",
    "agent_nickname": "Kant", "agent_role": None,
}}})
db = sqlite3.connect(codex / "state_5.sqlite")
try:
    with db:
        db.execute("""CREATE TABLE threads (
            id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
            source TEXT NOT NULL, model_provider TEXT NOT NULL, cwd TEXT NOT NULL,
            title TEXT NOT NULL, sandbox_policy TEXT NOT NULL, approval_mode TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0, has_user_event INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0, git_branch TEXT,
            first_user_message TEXT NOT NULL DEFAULT '', agent_nickname TEXT, agent_role TEXT,
            model TEXT, preview TEXT NOT NULL DEFAULT '', name TEXT,
            is_pinned INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("""CREATE TABLE thread_spawn_edges (
            parent_thread_id TEXT NOT NULL, child_thread_id TEXT NOT NULL PRIMARY KEY,
            status TEXT NOT NULL
        )""")
        db.executemany("""INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
            sandbox_policy, approval_mode, tokens_used, has_user_event,
            first_user_message, agent_nickname, preview, name
        ) VALUES (?, ?, ?, ?, ?, 'openai', '/Users/test/demo/project', '',
                  'workspace-write', 'never', 1200, 1, '', ?, '', ?)""", [
            ("cx-parent-a", rollout, epoch - 120, epoch, "cli", None, "Codex A"),
            ("cx-parent-b", rollout, epoch - 800, epoch - 600, "exec", None, "Codex B"),
            ("cx-child-c", rollout, epoch - 90, epoch - 30, spawn_source, "Kant", "Codex C"),
        ])
        db.execute("INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                   ("cx-parent-a", "cx-child-c", "open"))
finally:
    db.close()

db = sqlite3.connect(codex / "thread_history_1.sqlite")
try:
    with db:
        db.execute("""CREATE TABLE thread_turns (
            thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, rollout_ordinal INTEGER NOT NULL,
            status TEXT NOT NULL, error_json TEXT, started_at INTEGER, completed_at INTEGER,
            duration_ms INTEGER, first_user_item_id TEXT, final_agent_item_id TEXT,
            PRIMARY KEY(thread_id, turn_id)
        )""")
        db.execute("""CREATE TABLE thread_items (
            thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, item_id TEXT NOT NULL,
            rollout_ordinal INTEGER NOT NULL, created_at_ms INTEGER NOT NULL,
            item_json TEXT NOT NULL, item_type TEXT NOT NULL DEFAULT '',
            updated_at_ordinal INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(thread_id, turn_id, item_id)
        )""")
        db.executemany("""INSERT INTO thread_turns (
            thread_id, turn_id, rollout_ordinal, status, started_at, completed_at, duration_ms
        ) VALUES (?, 'turn-1', 1, ?, ?, ?, ?)""", [
            ("cx-parent-a", "inProgress", epoch - 60, None, None),
            ("cx-parent-b", "completed", epoch - 700, epoch - 620, 80000),
            ("cx-child-c", "completed", epoch - 90, epoch - 30, 60000),
        ])
        db.executemany("""INSERT INTO thread_items (
            thread_id, turn_id, item_id, rollout_ordinal, created_at_ms, item_json, item_type
        ) VALUES (?, 'turn-1', 'item-1', 1, ?, ?, ?)""", [
            (tid, (epoch - age) * 1000, json.dumps({"type": item_type}), item_type)
            for tid, age, item_type in [
                ("cx-parent-a", 60, "commandExecution"),
                ("cx-parent-b", 620, "agentMessage"),
                ("cx-child-c", 30, "reasoning"),
            ]
        ])
finally:
    db.close()
sys.stdout.write(str(home))
