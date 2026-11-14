#!/bin/bash
# AIオフィス「承認・質問の受け答え」フック（グローバル PermissionRequest hook・R86-H）
#
# Claude Code が許可を求める／AskUserQuestion で聞いてくるたびに起動する。やることは2つ:
#   ①いま何を聞かれているかを ~/.claude/office_approvals/<session>.<askId>.json に publish する
#     （AIオフィスの❗はこれを「事実」として使う＝トランスクリプトからの推測をやめる）
#   ②AIオフィスからの回答 <session>.<askId>.reply.json を待ち、届いたらそれを決定として返す
#
# ★R100-0（2026-09-29）: 掲示と回答は **1 件ごと（askId）** に分ける。従来は <session>.json の単一枠で、
#   同じセッションで許可が 2 件並ぶ（並列のツール呼び出し・サブエージェント）と、後から来た掲示が
#   前の掲示を上書きし、Mac で「表示された方」に出した allow が**表示されていない方**に効いた（実測で再現）。
#   askId は起動ごとの乱数（secrets.token_hex(16)）＝回答は askId が一致した 1 件にしか効かない。
#
# ★最重要の不変条件: 何があってもターミナルの挙動を変えない。
#   異常・タイムアウト・回答なしは **無出力で exit 0**＝素通し（いつもの許可ダイアログが出る）。
#   実測（2026-08-28・実TUI）で以下を確認済み:
#     - 無出力 exit 0 → 通常ダイアログが出て人間が答えられる
#     - フックが待っている間でも **人間がターミナルで答えれば即座にそちらが勝つ**
#     - hook timeout で kill されてもダイアログは生きたまま
#     - allow を返すと人間の操作ゼロで実行され、deny+message はモデルへの回答として届く
#
# ★安全境界（R100-1b で改めた・不変条件 7）: allow（実行の許可）を返すのは次の 2 つだけ。
#   (1) src="local"（Mac の前の人間・loopback+CSRF+Origin。同一ユーザーのプロセスは区別できない）
#   (2) src="remote" で、以下が**全部**明示的に真のとき（1 つでも欠けたら・例外なら deny）:
#       回答の inputSha ＝ この hook が自分の入力から作り直した SHA-256（表示した文字列そのもの）／
#       危険判定（approval_policy.py・この hook と同じ場所の写し）が自分の入力でスマホから許可できる形（phone_approvable＝
#       remote・review と、R103-1 から mac のうち種類で決まった形。判定できない形は Mac だけ）で、掲示の時点より危険な段へ上がっていない／
#       ON にしている（office_remote_approve.json・R118 から期限なし＝until は 2100 年）／回答が 120 秒以内。
#       daemon は回答を書く前に、端末のペアリング鍵の封筒・askId・封じた時刻などの段を確かめている。
#       R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）: 回答の mode が "tap"（Face ID 無し）でも
#       段（remote・review・mac）を問わず通す（R114〜R116 の "tap-mac" の拒否は廃止）。段の再計算は今までどおり＝掲示の時点より
#       危険な段に上がっていたら・判定できない形（Mac だけ）なら deny。mode は監査に残す。
#   中継の平文回答（src="relay"）は今までどおり deny+message＝「言葉を届ける」だけ。
#   allow に updatedInput／updatedPermissions は絶対に付けない（remote は message も付けない）。
#   判定の部品（approval_policy.py・approval_review.py・office_shell_lex.py）が読めない・壊れている＝remote は一切通さない。
#
# ★R86-H2（2026-09-15・本人「セッションは進んでいるのにオフィスは許可を求めたまま」）:
#   人間が TUI で答えると Claude Code はこの hook をその瞬間に kill する（finally は走らない）＝掲示が
#   12 時間残って❗の幽霊になった。だから毎周 **心拍**（mtime だけ更新）を打ち、daemon は mtime が
#   ASK_STALE より古い掲示を幽霊扱いする。SIGTERM/SIGHUP/SIGINT には handler で即 cleanup。
set -u
# R97-A: python の解決を 1 本化する（従来は /usr/bin/python3 固定・/usr/bin/env python3・python3 の 3 通り混在）。
# `/usr/bin/python3` は Xcode CLT が無い Mac では実体が無く、この hook は**無出力 exit 0** で終わっていた
# ＝「❗は出るのに答えても届かない」がログも無しに起きる（2026-09-17 の棚卸し）。
# 探索結果はキャッシュする（office-event.sh は毎ツール呼び出しで走る＝python の起動を増やさない）。
OFFICE_PY=""
OFFICE_PY_CACHE="${OFFICE_HOME:-$HOME}/.claude/.office_python"
if [ -s "$OFFICE_PY_CACHE" ]; then
  OFFICE_PY=$(cat "$OFFICE_PY_CACHE" 2>/dev/null || true)
  [ -n "$OFFICE_PY" ] && [ -x "$OFFICE_PY" ] || OFFICE_PY=""
fi
if [ -z "$OFFICE_PY" ]; then
  for OFFICE_PY_CAND in /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$OFFICE_PY_CAND" ] && [ -x "$OFFICE_PY_CAND" ] || continue
    "$OFFICE_PY_CAND" -I -c 'import json,sys' >/dev/null 2>&1 || continue
    OFFICE_PY="$OFFICE_PY_CAND"
    mkdir -p "${OFFICE_PY_CACHE%/*}" 2>/dev/null || true
    printf '%s' "$OFFICE_PY" 2>/dev/null > "$OFFICE_PY_CACHE" || true
    break
  done
fi
# R100-1b: 危険判定の部品（approval_policy.py・office_shell_lex.py）はこの hook と同じ場所から読む（cwd の同名を拾わない）
export OFFICE_HOOK_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
if [ -z "$OFFICE_PY" ]; then
  # 黙って消えない。オフィス側が「なぜ届かないか」を言えるように 1 行だけ残す（本文は書かない）。
  OFFICE_EVD="${OFFICE_HOME:-$HOME}/.claude/office_events"
  mkdir -p "$OFFICE_EVD" 2>/dev/null || true
  { printf '{"t":%s,"kind":"hook_no_python"}\n' "$(date +%s 2>/dev/null || echo 0)" >> "$OFFICE_EVD/hook_errors.jsonl"; } 2>/dev/null || true
  exit 0
fi
IN=$(cat 2>/dev/null || true)
[ -n "$IN" ] || exit 0

printf '%s' "$IN" | "$OFFICE_PY" -I -c '
import json, os, secrets, signal, sys, time

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
ask_id = secrets.token_hex(16)       # R100-0: この 1 件の名前（回答はこれが一致したものだけ効く）
pend = os.path.join(dirp, sid + "." + ask_id + ".json")
reply = os.path.join(dirp, sid + "." + ask_id + ".reply.json")
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
shape = {}
if tool == "AskUserQuestion":
    kind = "question"
    qs = ti.get("questions")
    q = qs[0] if isinstance(qs, list) and qs and isinstance(qs[0], dict) else {}
    # R102-11: 問いの数と複数選択（スマホは 2 問以上・複数選択を 1 タップで答えさせない）。掲示は 1 問目しか持たない
    if isinstance(qs, list) and qs:
        shape = {"qn": min(len(qs), 99),
                 "multi": 1 if any(isinstance(x, dict) and x.get("multiSelect") is True for x in qs) else 0}
    title = clip(q.get("question") or q.get("header") or "質問", 200)
    q_opts = q.get("options")
    for o in (q_opts if isinstance(q_opts, list) else [])[:4]:
        if isinstance(o, dict) and o.get("label"):
            options.append(clip(o["label"], 40))
        elif isinstance(o, str):
            options.append(clip(o, 40))
    # R112-H: 全部の問いと選択肢（スマホが 2 問以上・複数選択にも答えられる）。title/options/qn/multi は旧いスマホ向けに今のまま。
    # 1 問 = {q ≤200, options: label ≤40 ×≤4, multi 0|1}・≤8 問（qn は実数のまま）。description は載せない（本文の漏れを広げない）。
    # 空の問いは落とす。形が崩れていても例外で掲示ごと落とさない（掟 3）。上限は office_server.clean_ask_questions と同じ
    try:
        allq = []
        for x in (qs if isinstance(qs, list) else [])[:8]:
            if not isinstance(x, dict):
                continue
            qt = clip(x.get("question") or x.get("header") or "", 200)
            if not qt:
                continue
            xo, ops = x.get("options"), []
            for o in (xo if isinstance(xo, list) else []):
                lab = o.get("label") if isinstance(o, dict) else o
                lab = clip(lab, 40) if isinstance(lab, str) else ""
                if lab:
                    ops.append(lab)
                if len(ops) >= 4:
                    break
            allq.append({"q": qt, "options": ops, "multi": 1 if x.get("multiSelect") is True else 0})
        if allq:
            shape["questions"] = allq
    except Exception:
        shape.pop("questions", None)
elif tool == "Bash":
    title = clip(ti.get("command"), 200)
elif tool in ("Write", "Edit", "NotebookEdit", "Read"):
    title = clip(os.path.basename(str(ti.get("file_path") or "")), 120)
elif tool == "ExitPlanMode":
    kind, title = "question", "プランの承認"

rec = {"session": sid, "askId": ask_id, "tool": tool, "kind": kind, "title": title, "options": options,
       "cwd": str(d.get("cwd") or ""), "ts": time.time(), "deadline": time.time() + WAIT,
       "pid": os.getpid(), **shape}
_agent = d.get("agent_id")
if isinstance(_agent, str) and 0 < len(_agent) <= 64 and all(c.isalnum() or c in "-_" for c in _agent):
    rec["agentId"] = _agent          # サブエージェントの要求（表示の区別用・判定には使わない）

# R100-1b: スマホから許可してよい 1 件か（危険判定）と、表示と署名の正本（inputCanon・その SHA-256）。
# 掲示は 0600 のローカルファイル＝全文を置いてよい（中継へ出るのは daemon が端末の鍵で封じた分だけ）。
def _load_policy():
    import importlib.util
    hd = os.environ.get("OFFICE_HOOK_DIR") or ""
    if not os.path.isabs(hd):
        return None
    for cand in (os.path.join(hd, "approval_policy.py"), os.path.join(hd, "..", "server", "approval_policy.py")):
        if os.path.isfile(cand):
            spec = importlib.util.spec_from_file_location("office_approval_policy", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None

try:
    policy = _load_policy()
except Exception:
    policy = None
cwd = str(d.get("cwd") or "")
canon, in_sha = None, ""
level, reasons, tags = "mac", ["tool"], []
if kind == "permission":
    level, reasons = "mac", ["unreadable"]
    try:
        if policy is not None:
            c = policy.input_canon(tool, ti)
            rec["inputBytes"] = len(c.encode("utf-8"))
            got = policy.classify(tool, ti, cwd, home)
            level, reasons = str(got.get("level")), [str(x) for x in got.get("reasons") or []][:4]
            tags = [str(x)[:24] for x in got.get("tags") or []][:12]
            if rec["inputBytes"] <= policy.MAX_INPUT_BYTES and not policy.bad_chars(tool) and not policy.bad_chars(ti):
                canon, in_sha = c, policy.input_sha(c)
            else:
                level, reasons = "mac", ["too-large" if rec["inputBytes"] > policy.MAX_INPUT_BYTES else "unreadable"]
    except Exception:
        canon, in_sha, level, reasons = None, "", "mac", ["unreadable"]
def _phone_ok(lv, rs):
    """approval_policy.phone_approvable（R103-1）。古い approval_policy（関数が無い）なら今までどおり remote・review だけ。"""
    f = getattr(policy, "phone_approvable", None) if policy is not None else None
    return bool(f(lv, rs)) if callable(f) else lv in ("remote", "review")
if canon is not None and _phone_ok(level, reasons):
    # R103-1（本人裁定 2026-10-02）: 「Mac だけ」の段（mac）も種類で決まった形（削除・外部送信・台本・設定…）は全文を置き、
    # スマホから許可できる（全文確認＋Face ID）。判定できなかった形は今までどおり全文を置かない＝Mac だけ（安全側に倒す）
    rec["inputCanon"], rec["inputSha"] = canon, in_sha
else:
    canon, in_sha, tags = None, "", []
rec["level"], rec["reasons"] = (level if canon is not None else "mac"), reasons
if rec["level"] in ("review", "mac"):
    rec["tags"] = tags                   # R102-2: 画面で目立たせる警告（cwd の外・書く・台本…）・R103-1: mac の段も

HOOK_AUDIT = os.path.join(home, ".claude", "office_approvals_hook.jsonl")

def hook_audit(row):
    """hook 側の監査 1 行（daemon の監査と askId で突合）。本文は書かない。best effort。"""
    try:
        try:
            if os.path.getsize(HOOK_AUDIT) > 5 * 1024 * 1024:
                os.replace(HOOK_AUDIT, HOOK_AUDIT + ".1")
        except OSError:
            pass
        fd = os.open(HOOK_AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(dict(row, v=1, ts=int(time.time()), askId=ask_id), ensure_ascii=False) + "\n")
    except Exception:
        pass

def remote_on():
    """Mac で本人が ON にしているか（R118 から期限なし＝until は 2100 年）。読めない・bool・非数値・期限切れは False。"""
    try:
        with open(os.path.join(home, ".claude", "office_remote_approve.json")) as fh:
            r = json.load(fh)
        u = r.get("until") if isinstance(r, dict) else None
        return bool(r.get("v") == 1 and isinstance(u, int) and not isinstance(u, bool) and u > time.time())
    except Exception:
        return False

def mode_of(r):
    """remote の回答の方式（監査と判定用）: 無印（キーが無い＝R114 より前の daemon）・"sig"＝パスキーの署名／
    "tap"＝ON の間のタップ／それ以外は "other"。★明示の null（"mode": null）は無印と同じにしない＝"other"（未知の方式・
    tap と同じ厳しい扱い）。r.get("mode") だと省略と null がどちらも None になり、null が sig に化ける（R114-S2）。"""
    if not isinstance(r, dict):
        return "other"
    if "mode" not in r:
        return "sig"
    m = r["mode"]
    return "sig" if m == "sig" else ("tap" if m == "tap" else "other")

def remote_ok(r, ts):
    """src="remote" の allow を通してよいか。全部の条件の積（例外は False）。"""
    try:
        if policy is None:
            return False, "no-policy"
        if canon is None or not in_sha:
            return False, "mac-only"                 # 危険判定が掲示の時点で mac と言った形
        sha = r.get("inputSha")
        if not isinstance(sha, str) or sha != in_sha or sha != policy.input_sha(policy.input_canon(tool, ti)):
            return False, "input-mismatch"
        # 決定の瞬間にも危険判定。R103-1: mac もスマホから（全文確認＋Face ID）になったので、掲示の時点の段より危険な段に
        # 上がっていたら拒否（掲示の後にリンクを秘密へ差し替える等＝1 段の許可として見せたものを、危険な中身で通さない）
        rank = {"remote": 0, "review": 1, "mac": 2}
        now = policy.classify(tool, ti, cwd, home)
        now_lv, now_rs = now.get("level"), [str(x) for x in now.get("reasons") or []]
        if now_lv not in rank or rank[now_lv] > rank.get(rec.get("level"), 0) or not _phone_ok(now_lv, now_rs):
            return False, "dangerous"
        # R118（本人裁定 2026-10-13）: タップは段を問わず通す（R114〜R116 の "tap-mac" は廃止）。段のすり替えと判定できない形は上で止める
        if not remote_on():
            return False, "off"
        if not (0 <= time.time() - ts <= 120):
            return False, "stale"
        return True, ""
    except Exception:
        return False, "error"
def publish():
    os.makedirs(dirp, exist_ok=True)
    os.chmod(dirp, 0o700)
    fd = os.open(pend + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(pend + ".tmp", pend)
    return os.stat(pend).st_ino

try:
    ino0 = publish()
except Exception:
    bail()
hook_audit({"ev": "ask", "tool": tool[:64], "kind": kind, "level": rec["level"], "reasons": reasons})

def cleanup():
    try:
        cur = json.load(open(pend))
        if cur.get("askId") == ask_id:        # R100-0: 自分の 1 件だけ消す（所有は pid でなく askId）
            os.unlink(pend)
    except Exception:
        pass

# 人間がターミナルで答えたら**その質問の tool_result** がトランスクリプトへ書かれる。
# それを見たら黙って降りる＝二重回答を構造的に防ぐ。
# ★「サイズが増えたら降りる」ではダメ（実機で踏んだ）: 質問が出ている最中にも
#   attachment / file-history-delta 行が普通に追記されるので、ほぼ即座に降りてしまい
#   オフィスからの回答が二度と間に合わない。増えた**中身**を見て判定する。
# ★R100-0: 見るのは **自分の tool_use_id の** tool_result だけ。「何か tool_result が来たら降りる」だと、
#   並列のツール呼び出しで**先に終わった別の呼び出し**の結果で降り、まだ聞いている要求の掲示が消えた。
#   自分の id は (1) 入力の tool_use_id (2) 無ければ transcript 末尾から name と input が一致し、まだ結果の
#   無い tool_use を 1 件だけ見つける (3) 一意に決まらなければ従来どおり「tool_result が来たら降りる」（安全側）。
# ★サブエージェント（入力に agent_id）: Claude Code は transcript_path に**親**の transcript を渡し、tool_use_id も
#   渡さない（2.1.2xx の本体で確認）。サブエージェントの呼び出しと結果は <sid>/subagents/**/agent-<agent_id>.jsonl に
#   だけ書かれる＝親を見ていると、並列の兄弟の結果で降りて ❗ が消えた（R100-0 のレビュー）。そこで自分の jsonl を
#   探して見る。見つからない間は親の「何か来たら降りる」を使わない（待ち続ける。人間が TUI で答えれば Claude Code が
#   hook を kill し、心拍が止まって ❗ は ASK_STALE で消える）。
parent_tp = str(d.get("transcript_path") or "")
agent_id = rec.get("agentId") or ""
SUB_SEARCH_EVERY = 5.0          # 見つからない間は数秒ごとに探し直す（起動直後は未作成のことがある）
_sub_next = [0.0]

def find_sub_transcript():
    base = os.path.join(os.path.dirname(parent_tp), sid, "subagents") if parent_tp else ""
    want = "agent-" + agent_id + ".jsonl"
    if not base or not os.path.isdir(base):
        return ""
    for root, dirs, files in os.walk(base):
        if want in files:
            return os.path.join(root, want)
        if root.count(os.sep) - base.count(os.sep) >= 3:
            dirs[:] = []                 # 深追いしない（workflows/wf_*/ まで）
    return ""

tp = find_sub_transcript() if agent_id else parent_tp
try:
    size0 = os.path.getsize(tp) if tp else 0
except Exception:
    tp, size0 = "", 0
TAIL = 256 * 1024

def _content(line):
    try:
        row = json.loads(line)
    except Exception:
        return []
    msg = row.get("message") if isinstance(row, dict) else None
    c = msg.get("content") if isinstance(msg, dict) else None
    return [x for x in c if isinstance(x, dict)] if isinstance(c, list) else []

def own_tool_use_id():
    v = d.get("tool_use_id")
    if isinstance(v, str) and 0 < len(v) <= 128:
        return v
    if not tp or size0 <= 0:
        return ""
    try:
        start = max(0, size0 - TAIL)
        with open(tp, "rb") as fh:
            fh.seek(start)
            data = fh.read(size0 - start)
    except Exception:
        return ""
    uses, done = [], set()
    for line in data.split(b"\n"):
        if b"tool_" not in line:
            continue
        for c in _content(line):
            if (c.get("type") == "tool_use" and c.get("name") == tool and c.get("input") == ti
                    and isinstance(c.get("id"), str)):
                uses.append(c["id"])
            elif c.get("type") == "tool_result" and isinstance(c.get("tool_use_id"), str):
                done.add(c["tool_use_id"])
    cand = [u for u in uses if u not in done]
    return cand[0] if len(cand) == 1 else ""

own_id = own_tool_use_id()

def answered_elsewhere():
    """掲示した後に追記された分に**自分の** tool_result があれば「もう答えられた」。"""
    global size0, tp, own_id
    if agent_id and not tp:
        now = time.time()
        if now < _sub_next[0]:
            return False
        _sub_next[0] = now + SUB_SEARCH_EVERY
        found = find_sub_transcript()
        if not found:
            return False
        tp = found
        try:
            # 見つけた時点より前の行も見る（hook の起動と jsonl の作成の順が前後しても自分の tool_use を拾う）
            size0 = os.path.getsize(tp)
        except Exception:
            tp = ""
            return False
        own_id = own_tool_use_id()
        log("subagent transcript found own_id=%s" % bool(own_id))
        return False
    if not tp:
        return False
    try:
        cur = os.path.getsize(tp)
        if cur <= size0:
            return False
        with open(tp, "rb") as fh:
            fh.seek(size0)
            chunk = fh.read(cur - size0)
    except Exception:
        return False
    if not own_id:
        size0 = cur
        if agent_id:
            return False             # サブエージェントで自分が決まらない＝降りない（兄弟の結果で ❗ を消さない）
        return b"tool_result" in chunk
    cut = chunk.rfind(b"\n")
    if cut < 0:
        return False                 # 行の途中まで＝次の周で行ごと読む
    size0 += cut + 1
    key = own_id.encode()
    for line in chunk[:cut].split(b"\n"):
        if b"tool_result" not in line or key not in line:
            continue
        for c in _content(line):
            if c.get("type") == "tool_result" and c.get("tool_use_id") == own_id:
                return True
    return False

DEBUG = os.environ.get("OFFICE_APPROVAL_LOG") or ""

def log(msg):
    if not DEBUG:
        return
    try:
        with open(DEBUG, "a") as fh:
            fh.write("%.2f %s %s\n" % (time.time(), sid[:8], msg))
    except Exception:
        pass

# R86-H2 心拍: 毎周 utime だけ打つ（中身は書かない＝askId/ts は不変・cleanup() の所有判定もそのまま）。
# 掃除やスリープ復帰で掲示が消えていたら再掲示する（同じ askId・同じ ts）。
def heartbeat():
    global ino0
    try:
        if os.stat(pend).st_ino == ino0:
            os.utime(pend, None)
    except FileNotFoundError:
        try:
            ino0 = publish()
        except Exception:
            pass
    except Exception:
        pass

# kill されたときの即掃除（best effort・無出力）。SIGKILL は捕まえられない＝daemon 側の心拍判定が拾う。
def _on_signal(signum, frame):
    log("bail: signal %d" % signum)
    cleanup()
    os._exit(0)

for _sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    try:
        signal.signal(_sig, _on_signal)
    except Exception:
        pass

log("published tool=%s kind=%s tp=%s size0=%d own_id=%s" % (tool, kind, bool(tp), size0, bool(own_id)))

end = time.time() + WAIT
try:
    while time.time() < end:
        heartbeat()
        if answered_elsewhere():
            log("bail: answered at the terminal")
            cleanup(); bail()
        if os.path.exists(reply):
            # 原子的に「取る」＝同じ回答が二重に効かない（名前は askId 付き＝他の要求の回答はそもそも見ない）
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
            ok = (r.get("session") == sid and r.get("askId") == ask_id
                  and ts >= rec["ts"] - 5 and time.time() - ts <= FRESH)
            if ok:
                msg = clip(r.get("message"), 2000)
                src = r.get("src")
                why = ""
                if r.get("behavior") == "allow" and src == "local":
                    out = {"behavior": "allow"}
                    if msg:
                        out["message"] = msg
                elif r.get("behavior") == "allow" and src == "remote":
                    good, why = remote_ok(r, ts)
                    if good:
                        out = {"behavior": "allow"}          # remote の allow は何も付けない
                    else:
                        out = {"behavior": "deny", "message": "スマホからの許可を Mac が受け付けませんでした（" + why + "）"}
                else:
                    out = {"behavior": "deny", "message": msg or "AIオフィスから拒否されました"}
                cleanup()
                row = {"ev": "decide", "src": str(src)[:8], "decision": out["behavior"], "reason": why}
                if src == "remote":
                    row["mode"] = mode_of(r)             # R114: Face ID（sig）かタップ（tap）か
                hook_audit(row)
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
