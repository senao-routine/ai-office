#!/bin/bash
# AIオフィス「承認・質問の受け答え」フック（グローバル PermissionRequest hook・R86-H）
#
# Claude Code が許可を求める／AskUserQuestion で聞いてくるたびに起動する。やることは2つ:
#   ①いま何を聞かれているかを ~/.claude/office_approvals/<session>.json に publish する
#     （AIオフィスの❗はこれを「事実」として使う＝トランスクリプトからの推測をやめる）
#   ②AIオフィスからの回答 <session>.reply.json を待ち、届いたらそれを決定として返す
#
# ★最重要の不変条件: 何があってもターミナルの挙動を変えない。
#   異常・タイムアウト・回答なしは **無出力で exit 0**＝素通し（いつもの許可ダイアログが出る）。
#   実測（2026-08-28・実TUI）で以下を確認済み:
#     - 無出力 exit 0 → 通常ダイアログが出て人間が答えられる
#     - フックが待っている間でも **人間がターミナルで答えれば即座にそちらが勝つ**
#     - hook timeout で kill されてもダイアログは生きたまま
#     - allow を返すと人間の操作ゼロで実行され、deny+message はモデルへの回答として届く
#
# ★安全境界: allow（実行の許可）は **src="local"（Macの前の人間・loopback+CSRF）** の回答だけ。
#   中継（スマホ）経由の回答は deny+message＝「言葉を届ける」ことしかできない。
#   スマホから任意コマンドを承認できてしまうと、中継トークンとデバイス秘密の漏洩が
#   そのまま任意コード実行になる。ここは opt-in ですら開けない（開けるなら別プランで）。
set -u
IN=$(cat 2>/dev/null || true)
[ -n "$IN" ] || exit 0

printf '%s' "$IN" | /usr/bin/python3 -c '
import json, os, sys, time

def bail():
    sys.exit(0)                      # 無出力 exit 0 ＝ 素通し

try:
    d = json.load(sys.stdin)
except Exception:
    bail()
if not isinstance(d, dict):
    bail()

sid = str(d.get("session_id") or "")
if not sid or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in sid):
    bail()

home = os.environ.get("OFFICE_HOME") or os.path.expanduser("~")
dirp = os.path.join(home, ".claude", "office_approvals")
pend = os.path.join(dirp, sid + ".json")
reply = os.path.join(dirp, sid + ".reply.json")
WAIT = float(os.environ.get("OFFICE_APPROVAL_WAIT") or 43200)   # 既定12時間（指示ポストと同じ）
POLL = float(os.environ.get("OFFICE_APPROVAL_POLL") or 1.0)
FRESH = 300.0                        # 回答の鮮度窓（古い回答が別の質問に効くのを防ぐ）

tool = str(d.get("tool_name") or "")
ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}

def clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"

# 「何を聞かれているか」を人間の1行に畳む（本文そのものはローカル 0600 のファイルにだけ置く）
kind, title, options = "permission", tool, []
if tool == "AskUserQuestion":
    kind = "question"
    qs = ti.get("questions")
    q = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else {}
    title = clip(q.get("question") or q.get("header") or "質問", 200)
    for o in (q.get("options") or [])[:4]:
        if isinstance(o, dict) and o.get("label"):
            options.append(clip(o["label"], 40))
        elif isinstance(o, str):
            options.append(clip(o, 40))
elif tool == "Bash":
    title = clip(ti.get("command"), 200)
elif tool in ("Write", "Edit", "NotebookEdit", "Read"):
    title = clip(os.path.basename(str(ti.get("file_path") or "")), 120)
elif tool == "ExitPlanMode":
    kind, title = "question", "プランの承認"

rec = {"session": sid, "tool": tool, "kind": kind, "title": title, "options": options,
       "cwd": str(d.get("cwd") or ""), "ts": time.time(), "deadline": time.time() + WAIT,
       "pid": os.getpid()}
try:
    os.makedirs(dirp, exist_ok=True)
    os.chmod(dirp, 0o700)
    fd = os.open(pend + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(pend + ".tmp", pend)
except Exception:
    bail()

def cleanup():
    try:
        cur = json.load(open(pend))
        if cur.get("pid") == os.getpid():     # 後から来た質問の掲示は消さない
            os.unlink(pend)
    except Exception:
        pass

# 人間がターミナルで答えたら**その質問の tool_result** がトランスクリプトへ書かれる。
# それを見たら黙って降りる＝二重回答を構造的に防ぐ。
# ★「サイズが増えたら降りる」ではダメ（実機で踏んだ）: 質問が出ている最中にも
#   attachment / file-history-delta 行が普通に追記されるので、ほぼ即座に降りてしまい
#   オフィスからの回答が二度と間に合わない。増えた**中身**を見て判定する。
tp = str(d.get("transcript_path") or "")
try:
    size0 = os.path.getsize(tp)
except Exception:
    tp, size0 = "", 0

def answered_elsewhere():
    """掲示した後に追記された分に tool_result があれば「もう答えられた」。"""
    global size0
    if not tp:
        return False
    try:
        cur = os.path.getsize(tp)
        if cur <= size0:
            return False
        with open(tp, "rb") as fh:
            fh.seek(size0)
            chunk = fh.read(cur - size0)
        size0 = cur
    except Exception:
        return False
    return b"tool_result" in chunk

DEBUG = os.environ.get("OFFICE_APPROVAL_LOG") or ""

def log(msg):
    if not DEBUG:
        return
    try:
        with open(DEBUG, "a") as fh:
            fh.write("%.2f %s %s\n" % (time.time(), sid[:8], msg))
    except Exception:
        pass

log("published tool=%s kind=%s tp=%s size0=%d" % (tool, kind, bool(tp), size0))

end = time.time() + WAIT
try:
    while time.time() < end:
        if answered_elsewhere():
            log("bail: answered at the terminal")
            cleanup(); bail()
        if os.path.exists(reply):
            # 原子的に「取る」＝並行して2つ質問が出ていても1つの回答が二重に効かない
            claim = reply + ".taken.%d" % os.getpid()
            try:
                os.rename(reply, claim)
            except Exception:
                time.sleep(POLL); continue
            try:
                r = json.load(open(claim))
                os.unlink(claim)
            except Exception:
                try: os.unlink(claim)
                except Exception: pass
                time.sleep(POLL); continue
            ts = float(r.get("ts") or 0)
            ok = (r.get("session") == sid and ts >= rec["ts"] - 5 and time.time() - ts <= FRESH)
            if ok:
                msg = clip(r.get("message"), 2000)
                if r.get("behavior") == "allow" and r.get("src") == "local":
                    out = {"behavior": "allow"}
                    if msg:
                        out["message"] = msg
                else:
                    out = {"behavior": "deny", "message": msg or "AIオフィスから拒否されました"}
                cleanup()
                log("decide %s" % out.get("behavior"))
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PermissionRequest", "decision": out}}, ensure_ascii=False))
                sys.exit(0)
        time.sleep(POLL)
finally:
    cleanup()
bail()
' 2>/dev/null || exit 0
exit 0
