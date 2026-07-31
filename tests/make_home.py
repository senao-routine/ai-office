#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify.sh 用: 使い捨ての OFFICE_HOME を組み立ててパスを出力する"""
import datetime
import json
import os
import shutil
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
sys.stdout.write(str(home))
