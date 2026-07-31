# -*- coding: utf-8 -*-
"""R42.3 OpenClaw情報源アダプタ（標準ライブラリのみ・server/同dir）。

mini→mac の契約スキーマ（docs/openclaw-status-schema.md・v1）を employee 互換へ変換する。
OpenClaw本体の非公開フォーマットはここでは読まない（mini側 openclaw_push.py の責務）。

掟:
- プライバシーはソースで満たす: cwd/branch/lastSaid/lastOrder/target は常に空・feedはverb行のみ
  （relay redaction に頼らず、最初から本文を載せない）
- 不正行は黙って捨てる（1行の毒で全滅させない）・stale(>600秒)は切断扱い
- session は "oc-<id>" 名前空間＝Claudeセッションと衝突しない（PWA/dispの分離）
"""
import json
import os
import re
from pathlib import Path

STALE_SEC = 600.0
_ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
_STATES = ("working", "waiting", "resting")


def _source_file(home):
    override = os.environ.get("OFFICE_OPENCLAW_FIXTURE")
    if override:
        return Path(override)
    return Path(home) / ".claude" / "openclaw_status.json"


def parse_openclaw_status(raw, now, lang="ja"):
    """契約JSON → (employees, meta)。純関数（ファイルIOなし・テスト正本）。"""
    meta = {"connected": False, "reason": "", "site": ""}
    if not isinstance(raw, dict) or raw.get("v") != 1:
        meta["reason"] = "形式不正（v1でない）"
        return [], meta
    meta["site"] = str(raw.get("site") or "")
    try:
        generated = float(raw.get("generatedAt") or 0)
    except (TypeError, ValueError):
        generated = 0.0
    if generated <= 0 or now - generated > STALE_SEC:
        meta["reason"] = "stale（mini側のpush停止）"
        return [], meta
    employees = []
    for agent in (raw.get("agents") or []):
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("id") or "")
        if not _ID_RE.fullmatch(agent_id):
            continue
        state = agent.get("state")
        if state not in _STATES:
            continue
        name = str(agent.get("name") or "OpenClaw").strip()[:24] or "OpenClaw"
        verb = str(agent.get("verb") or "")[:42]
        channel = str(agent.get("channel") or "")[:16]
        try:
            age = max(0, int(agent.get("age") or 0))
        except (TypeError, ValueError):
            age = 0
        try:
            minions = max(0, int(agent.get("minions") or 0))
        except (TypeError, ValueError):
            minions = 0
        employees.append({
            "session": f"oc-{agent_id}",
            "external": "openclaw",
            "site": meta["site"],
            "dept": name,
            "role": channel,
            "state": state,
            "verb": verb,
            "target": "",
            "cwd": "", "branch": "",
            "lastSaid": "", "lastOrder": "",
            "question": "", "approvalMin": 0, "stuckTool": "",
            "feed": [verb] if verb else [],
            "skills": [],
            "age": age,
            "mtime": now - age,
            "minions": minions,
            "pending": False,
            "avatar": 0,
            "sprite": "/assets/agent_bot.png",
            "spriteWalk": "",
        })
    # 同名の採番（Claude側 disp 規則と同型・oc名前空間内で完結）
    counts = {}
    for e in employees:
        n = counts.get(e["dept"], 0) + 1
        counts[e["dept"]] = n
        if n == 1:
            e["disp"] = e["dept"]
        else:
            e["disp"] = f"{e['dept']} #{n}" if lang == "en" else f"{e['dept']} {n}号"
    meta["connected"] = True
    return employees, meta


def openclaw_employees(home, now, lang="ja"):
    """供給源（fixture env → ~/.claude/openclaw_status.json → なし）を読んで変換する。"""
    p = _source_file(home)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return [], {"connected": False, "reason": "未接続（statusファイルなし）", "site": ""}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [], {"connected": False, "reason": "statusファイルが読めません", "site": ""}
    return parse_openclaw_status(raw, now, lang=lang)
