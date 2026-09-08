#!/bin/bash
# R90-D4: Claude Code の hook イベントを ~/.claude/office_events/<YYYY-MM-DD>.jsonl へ**記録するだけ**の hook。
#
# なぜ: AI Office はこれまで transcript を2〜3秒おきに読み直して状態を推測していた。Claude Code の hooks は
#   SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop / SubagentStart / SubagentStop /
#   TaskCreated / TaskCompleted / Notification / SessionEnd … と増え、"async": true で**ターンを1ミリ秒も
#   止めずに**呼べる。ここで1行落としておけば office_server が tail して即時に状態を更新し、タイムライン
#   （留守中ダイジェスト・XP）の材料にもなる。
#
# 掟（office-inbox-wait.sh / office-statusline-capture.sh と同じ）:
#   - どんな失敗でも無出力 exit 0（セッションを絶対に邪魔しない・stderr も沈黙）
#   - python3 起動は1回だけ（毎ツール呼び出しで走るので軽く保つ。実測 <150ms が受入条件）
#   - **本文を1バイトも書かない**: prompt 本文・コマンド文字列・tool_response・transcript_path は読まない。
#     書くのは種別（hook 名・ツール名）と、ファイル名の basename・スキル名・エージェント種別・
#     「git commit/push だったか」の分類・既知の分類値・件数・長さだけ（タスク件名や reason の自由記述も書かない）。allowlist 抽出は**この hook 側**で行う
#     （サーバー側の redaction に頼らない＝ファイルに本文が存在しない）
#   - 1行 ≤ 1500 バイト・O_APPEND で単一 write（APFS の append 原子性の実用範囲）・dir 0700 / file 0600
#   - 心拍（pidfile）や inbox には一切触れない（配達の不変条件は office-inbox-wait.sh が持つ）
#
# OFFICE_HOME はテスト注入口（既定 ${HOME}）。
set +e
H="${OFFICE_HOME:-$HOME}"
# payload は python が stdin から直接読む（環境変数経由だと大きな tool_response で ARG_MAX を超え、
# python が起動できずイベントが落ちる＝Astra レビュー指摘）。プログラム本体は -c で渡す。
read -r -d '' PY <<'PYEOF'
import datetime
import hashlib
import json
import os
import re
import sys
import time

MAX_LINE = 1500
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
sid = str(d.get("session_id") or "")
if not sid or len(sid) > 64 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in sid):
    sys.exit(0)
ev = str(d.get("hook_event_name") or "")
if not ev or len(ev) > 40 or not re.fullmatch(r"[A-Za-z]+", ev):
    sys.exit(0)


def clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
tool = clip(d.get("tool_name"), 60)
tgt = ""
if tool in ("Edit", "Write", "Read", "NotebookEdit", "MultiEdit"):
    tgt = os.path.basename(str(ti.get("file_path") or ti.get("notebook_path") or ""))
elif tool == "Skill":
    tgt = clip(ti.get("skill"), 40)
elif tool in ("Agent", "Task"):
    tgt = clip(ti.get("subagent_type") or ti.get("agent_type"), 40)
elif tool in ("Glob", "Grep"):
    tgt = ""                                   # パターンは検索語＝本文扱い。書かない
kind = ""
if tool == "Bash":
    cmd = str(ti.get("command") or "")
    m = re.search(r"\bgit\s+(commit|push)\b", cmd)
    kind = "git:" + m.group(1) if m else ""    # 分類だけ。コマンド本文は捨てる
# 自由記述（task_input.subject/description・reason 等）は秘密やパスを含みうるので記録しない。
# src は Claude Code が使う既知の分類値だけ（それ以外は空）。
SRC_ALLOWED = {"startup", "resume", "clear", "compact", "logout", "prompt_input_exit", "other", "manual", "auto"}
src_raw = d.get("source") or d.get("reason") or d.get("end_reason") or d.get("trigger")
rec = {
    "v": 1,
    "ts": round(time.time(), 3),
    "ev": ev,
    "sid": sid,
    "cwdh": hashlib.sha1(str(d.get("cwd") or "").encode("utf-8")).hexdigest()[:12],
    "tool": tool,
    "tgt": clip(tgt, 80),
    "kind": kind,
    "nt": clip(d.get("notification_type"), 40),
    "sub": clip(d.get("agent_id"), 40) if ev.startswith("Subagent") else "",
    "task": clip(d.get("task_id"), 40),
    "ok": ev not in ("PostToolUseFailure", "StopFailure", "PermissionDenied"),
    "src": src_raw if isinstance(src_raw, str) and src_raw in SRC_ALLOWED else "",
    "plen": len(str(d.get("prompt_text") or d.get("prompt") or "")) if ev == "UserPromptSubmit" else 0,
}
line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
for drop in ("tgt", "nt", "sub"):          # 長すぎたら情報の薄い順に落として 1500B に収める
    if len(line.encode("utf-8")) <= MAX_LINE:
        break
    rec[drop] = ""
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
if len(line.encode("utf-8")) > MAX_LINE:
    sys.exit(0)

home = os.environ.get("OFFICE_HOME") or os.path.expanduser("~")
dirp = os.path.join(home, ".claude", "office_events")
try:
    os.makedirs(dirp, mode=0o700, exist_ok=True)
    try:
        os.chmod(dirp, 0o700)
    except OSError:
        pass
    day = datetime.date.today().isoformat()
    fd = os.open(os.path.join(dirp, day + ".jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
except Exception:
    pass
PYEOF
OFFICE_HOME="$H" /usr/bin/env python3 -c "$PY" >/dev/null 2>&1
exit 0
