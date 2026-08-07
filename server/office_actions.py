#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R79-10 遠隔実行（許可リスト方式）のコア — 標準ライブラリのみ。

スマホからの act- 封筒（署名検証済み・relay_agent 経由）を、Macの前で人間が登録した
レシピに限って実行する。設計の掟（計画 elegant-questing-neumann の B節）:

- **許可リストは ~/.claude/office_recipes.json（0600）だけ**。編集はローカルUI/ローカル
  ファイルのみ＝遠隔からレシピを作る/変える経路は一切無い。電話が持てるのは登録済みidへの
  参照だけ（Bearer+デバイス秘密が両方漏れても、登録済みコマンド以外は実行できない）。
- 実行の掟: `shell=False`・argv配列のみ・`start_new_session=True`（タイムアウトは
  プロセスグループごと kill＝verify.sh が孫に wrangler dev を生むこのリポジトリでは必須）・
  同時2本／同一レシピ1本・cwd は必ずレシピ側・環境変数は最小集合＋許可リスト。
- 出力: 既定 "none"（内容は1バイトも中継に出ない）。"tail"/"full" は scrub_output 通過後
  ≤8000バイトのみ。scrub は生産者側と results_public() の**二重適用**。
- 終了状態は必ず1つに落ちる: denied / busy / running / done / failed / timeout。
- dangerous:true は実行前に osascript ダイアログ（既定キャンセル・10秒）で物理確認。
  テスト注入口= OFFICE_FAKE_CONFIRM（"ok"以外は全て拒否）。
- 監査: 全アクションを ~/.claude/office_actions_audit.jsonl（0600）へ追記＋macOS通知
  （NOTIFY フックは office_server が注入）。
"""
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

_HOME = Path(os.environ.get("OFFICE_HOME", str(Path.home())))
RECIPES_FILE = _HOME / ".claude" / "office_recipes.json"
AUDIT_FILE = _HOME / ".claude" / "office_actions_audit.jsonl"

ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
REQID_RE = re.compile(r"^[A-Za-z0-9-]{8,40}$")
PROJID_RE = re.compile(r"^[0-9a-f]{12}$")
ENVNAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
ACT_SESSION_RE = re.compile(r"^act-[0-9a-f]{16}$")

OUTPUT_LIMIT = 8000          # 中継に載る出力の上限（バイト・scrub後）
CAPTURE_LIMIT = 256 * 1024   # 実行中に保持する生出力の上限（末尾を保持）
MAX_CONCURRENT = 2
RESULT_KEEP = 20             # レジストリに残す完了結果の数（新しい順）

# office_server が注入するフック（未注入でも動く＝単体テストはフック無しで回す）
NOTIFY = None                # callable(title, body)

_LOCK = threading.Lock()
_ACTIONS = {}                # reqId -> record(dict)
_ORDER = []                  # reqId 到着順


# ── 許可リスト ─────────────────────────────────────────────────────────────
def validate_recipes(obj):
    """(正規化済みrecipes, エラー文一覧)。エラーがあっても解釈できた分は返す（UI表示用）。"""
    errors = []
    out = []
    recipes = obj.get("recipes") if isinstance(obj, dict) else None
    if not isinstance(recipes, list):
        return [], ["recipes が配列ではありません"]
    seen = set()
    for i, r in enumerate(recipes):
        where = f"recipes[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where}: オブジェクトではありません")
            continue
        rid = r.get("id")
        if not (isinstance(rid, str) and ID_RE.match(rid)):
            errors.append(f"{where}: id は ^[a-z0-9_]{{1,32}}$（{rid!r}）")
            continue
        if rid in seen:
            errors.append(f"{where}: id 重複 ({rid})")
            continue
        argv = r.get("argv")
        if (not isinstance(argv, list) or not argv
                or not all(isinstance(a, str) and a for a in argv)):
            errors.append(f"{where}({rid}): argv は空でない文字列配列（shell文字列は不可）")
            continue
        cwd = r.get("cwd")
        if not (isinstance(cwd, str) and os.path.isabs(cwd)):
            errors.append(f"{where}({rid}): cwd は絶対パス必須（相対/~は不可・{cwd!r}）")
            continue
        raw_timeout = r.get("timeoutSec")
        try:   # 未指定=600。0/None以外の不正値は素直に落とす（0を600へ昇格させない）
            timeout = 600 if raw_timeout is None else int(raw_timeout)
        except (TypeError, ValueError):
            timeout = -1
        if not 1 <= timeout <= 3600:
            errors.append(f"{where}({rid}): timeoutSec は 1..3600")
            continue
        ret = r.get("returnOutput") or "none"
        if ret not in ("none", "tail", "full"):
            errors.append(f"{where}({rid}): returnOutput は none|tail|full")
            continue
        env = r.get("env") or {}
        if not isinstance(env, dict) or len(env) > 10 or not all(
                isinstance(k, str) and ENVNAME_RE.match(k)
                and isinstance(v, str) and len(v) <= 200 for k, v in env.items()):
            errors.append(f"{where}({rid}): env は {{大文字名: 短い文字列}} 最大10件")
            continue
        seen.add(rid)
        out.append({
            "id": rid,
            "label": str(r.get("label") or rid)[:60],
            "argv": list(argv),
            "cwd": cwd,
            "timeoutSec": timeout,
            "returnOutput": ret,
            "dangerous": bool(r.get("dangerous")),
            "env": dict(env),
        })
    return out, errors


def load_recipes():
    """(recipes, errors)。ファイル無し=空（既定でレシピゼロ＝実行できるものが無い、が正）。"""
    try:
        raw = json.loads(RECIPES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], []
    except (OSError, json.JSONDecodeError) as e:
        return [], [f"office_recipes.json が読めません: {e}"]
    return validate_recipes(raw)


def save_recipes(recipes):
    """検証済み recipes を原子保存（0600）。呼び出し側は validate_recipes を通してから渡す。"""
    RECIPES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECIPES_FILE.with_name(RECIPES_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"v": 1, "recipes": recipes}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(RECIPES_FILE)


# ── act封筒の text 解析 ───────────────────────────────────────────────────
def parse_action(text):
    """署名検証済み text（JSON）→ 正規化 dict / None。canonical には触れない
    （text は sha256hex で署名に畳まれている＝構造化しても暗号強度は不変・計画B節）。"""
    try:
        d = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or d.get("aioffice") != 1:
        return None
    kind = d.get("kind")
    req_id = d.get("reqId")
    if not (isinstance(req_id, str) and REQID_RE.match(req_id)):
        return None
    if kind == "run":
        rid = d.get("recipe")
        if not (isinstance(rid, str) and ID_RE.match(rid)):
            return None
        if d.get("args") not in ([], None):
            return None          # v1: 引数注入は許可しない（レシピ=完全固定のargv）
        return {"kind": "run", "recipe": rid, "reqId": req_id}
    if kind == "launch":
        pid = d.get("project")
        if not (isinstance(pid, str) and PROJID_RE.match(pid)):
            return None
        return {"kind": "launch", "project": pid, "reqId": req_id}
    return None


# ── 出力スクラブ（純関数・最も厚くテストする場所） ─────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_SECRET_RES = [
    re.compile(r"\b(sk|gsk|ghp|gho|ghs|xoxb|xoxp)[-_][A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{64}\b"),                                   # 64hex（秘密鍵/HMAC）
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"),  # JWT
]
_PATH_RE = re.compile(r"(^|[\s=:'\"(\[])((?:/|~/)[^\s:'\"()\[\]]*/[^\s:'\"()\[\]]+)")


def scrub_output(text, limit=OUTPUT_LIMIT):
    """中継へ載せてよい形へ落とす: ANSI除去→パス→basename→秘密の伏字→UTF-8境界安全な
    バイト切り詰め。冪等（二重適用しても壊れない）＝生産者と results_public の両方で掛ける。"""
    t = str(text if text is not None else "")
    t = _ANSI_RE.sub("", t)

    def _basename(m):
        path = m.group(2)
        return m.group(1) + (path.rstrip("/").rsplit("/", 1)[-1] or "…")
    t = _PATH_RE.sub(_basename, t)
    for rx in _SECRET_RES:
        t = rx.sub("[secret]", t)
    raw = t.encode("utf-8")
    if len(raw) > limit:
        cut = raw[-limit:]
        # UTF-8 継続バイト(10xxxxxx)の途中で切らない
        i = 0
        while i < 4 and i < len(cut) and (cut[i] & 0xC0) == 0x80:
            i += 1
        t = "…" + cut[i:].decode("utf-8", "replace")
    return t


# ── 実行レジストリ ─────────────────────────────────────────────────────────
def _audit(record):
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(AUDIT_FILE, 0o600)
    except OSError:
        pass                     # 監査はベストエフォート（実行の本流を殺さない）


def _notify(title, body):
    if callable(NOTIFY):
        try:
            NOTIFY(title, body)
        except Exception:
            pass


def _confirm_dangerous(label):
    """Macの前での物理確認。既定=キャンセル・10秒タイムアウト＝無人なら必ず拒否。"""
    fake = os.environ.get("OFFICE_FAKE_CONFIRM")
    if fake is not None:
        return fake == "ok"
    script = ('display dialog "📲 スマホから危険操作の実行要求:\\n{}" '
              'buttons {{"キャンセル", "実行"}} default button "キャンセル" '
              'giving up after 10').format(str(label).replace("\\", "").replace('"', "'")[:80])
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0 and "実行" in (r.stdout or "") \
            and "gave up:true" not in (r.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return False


def admit(recipe_id, running):
    """受理してよいか（純関数）。同時 MAX_CONCURRENT 本まで・同一レシピは1本まで。
    running は [{"recipe": id}, ...]。純関数なのでプロセスを起こさずに単体で固定できる
    （実プロセス版のテストは daemon スレッドと子プロセスの後始末でフレークの温床になる）。"""
    rs = list(running or [])
    if len(rs) >= MAX_CONCURRENT:
        return False
    return not any((r or {}).get("recipe") == recipe_id for r in rs)


def _register(record):
    with _LOCK:
        _ACTIONS[record["reqId"]] = record
        _ORDER.append(record["reqId"])
        # 完了済みの古い結果を剪定（runningは絶対に消さない）
        done = [r for r in _ORDER if _ACTIONS[r]["state"] != "running"]
        for rid in done[:-RESULT_KEEP]:
            _ORDER.remove(rid)
            del _ACTIONS[rid]


def register_result(req_id, kind, label, state, **extra):
    """run 以外（launch等）の結果もこのレジストリへ一元記録する（office_server から）。"""
    record = {"reqId": req_id, "kind": kind, "recipe": extra.pop("recipe", ""),
              "label": str(label)[:60], "state": state,
              "startedAt": time.time(), "endedAt": time.time(),
              "exitCode": extra.pop("exitCode", None),
              "durationMs": extra.pop("durationMs", 0),
              "bytes": 0, "output": "", **extra}
    _register(record)
    _audit(record)
    return record


def _finish(record, state, exit_code, buf, return_output):
    record["state"] = state
    record["exitCode"] = exit_code
    record["endedAt"] = time.time()
    record["durationMs"] = int((record["endedAt"] - record["startedAt"]) * 1000)
    record["bytes"] = len(buf)
    if return_output != "none" and buf:
        raw = buf[-OUTPUT_LIMIT:] if return_output == "tail" else buf[:OUTPUT_LIMIT]
        record["output"] = scrub_output(raw.decode("utf-8", "replace"))
    record.pop("_proc", None)
    _audit({k: v for k, v in record.items() if k not in ("output", "_proc")} | {
        "outputBytes": record["bytes"]})
    _notify("📲 遠隔実行 完了", f"{record['label']}: {state}"
            + (f" (exit {exit_code})" if exit_code is not None else ""))


def _run_watcher(record, proc, timeout, return_output):
    buf = bytearray()
    deadline = time.time() + timeout
    # ★stdout は必ずノンブロッキングにする。ブロッキングのまま read すると
    # 「出力しないまま長時間走るプロセス」でここが固まり、**タイムアウト判定に到達しない**
    # （= timeoutSec が効かない・R79-10の実装中に実測して発見）。
    try:
        os.set_blocking(proc.stdout.fileno(), False)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                # プロセスグループごと殺す（孫= wrangler dev / node まで確実に）
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
                    proc.wait(timeout=5)
                _finish(record, "timeout", None, bytes(buf), return_output)
                return
            # ノンブロッキング read: データ有り=bytes / まだ無い=None / EOF=b""
            try:
                chunk = proc.stdout.read(4096)
            except (BlockingIOError, ValueError):
                chunk = None
            if chunk:
                buf += chunk
                if len(buf) > CAPTURE_LIMIT:
                    del buf[:len(buf) - CAPTURE_LIMIT]
                continue
            rc = proc.poll()
            if rc is None:
                # 実行中で今は出力なし → 少し待つ（selectでも良いが sleep で十分軽い）
                time.sleep(min(0.05, max(0.0, remain)))
                continue
            # 終了確定。残りを**有限時間だけ**吸って終わる。
            # ★「EOF(b"") を見るまで continue」にすると、パイプの書き込み端を掴んだままの
            #   孫プロセスが居るとき None が返り続けて**無限ループ**になる（実測して発見。
            #   watcherが終わらない＝プロセスが exit しない＝テストが固まる）。
            drain_end = time.time() + 0.5
            while time.time() < drain_end:
                try:
                    extra = proc.stdout.read(4096)
                except (BlockingIOError, ValueError):
                    extra = None
                if extra:
                    buf += extra
                    continue
                if extra == b"":
                    break            # 本物のEOF
                time.sleep(0.02)     # まだ来ていないだけ（孫が掴んでいる可能性）
            if len(buf) > CAPTURE_LIMIT:
                del buf[:len(buf) - CAPTURE_LIMIT]
            _finish(record, "done" if rc == 0 else "failed", rc, bytes(buf), return_output)
            return
    except Exception as e:   # watcherは何があっても状態を1つに落とす
        _finish(record, "failed", None, bytes(buf) + f"\n[watcher error: {e}]".encode(),
                return_output)


def start_action(act, recipes, device=""):
    """kind=run を実行。必ず (state, record) を返す（例外を漏らさない）。
    reqId は冪等キー: 既知なら再実行せず既存recordを返す（at-least-once 再配達の安全網）。"""
    req_id = act["reqId"]
    with _LOCK:
        if req_id in _ACTIONS:
            return _ACTIONS[req_id]["state"], _ACTIONS[req_id]
    recipe = next((r for r in recipes if r["id"] == act["recipe"]), None)
    if recipe is None:
        rec = register_result(req_id, "run", act["recipe"], "denied",
                              recipe=act["recipe"], reason="unknown-recipe", device=device)
        _notify("📲 遠隔実行 拒否", f"未登録レシピ: {act['recipe']}")
        return "denied", rec
    with _LOCK:
        running = [{"recipe": r.get("recipe")} for r in _ACTIONS.values()
                   if r["state"] == "running"]
    if not admit(recipe["id"], running):
        rec = register_result(req_id, "run", recipe["label"], "busy",
                              recipe=recipe["id"], device=device)
        return "busy", rec
    if recipe["dangerous"] and not _confirm_dangerous(recipe["label"]):
        rec = register_result(req_id, "run", recipe["label"], "denied",
                              recipe=recipe["id"], reason="not-confirmed", device=device)
        _notify("📲 遠隔実行 拒否", f"{recipe['label']}: Macの前で確認されませんでした")
        return "denied", rec
    env = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(Path.home()),
        "LANG": "en_US.UTF-8",
        **recipe["env"],
    }
    record = {"reqId": req_id, "kind": "run", "recipe": recipe["id"],
              "label": recipe["label"], "state": "running",
              "startedAt": time.time(), "endedAt": None, "exitCode": None,
              "durationMs": 0, "bytes": 0, "output": "", "device": device}
    try:
        proc = subprocess.Popen(
            recipe["argv"], cwd=recipe["cwd"], shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, env=env)
    except (OSError, ValueError) as e:
        rec = register_result(req_id, "run", recipe["label"], "failed",
                              recipe=recipe["id"], reason=str(e)[:120], device=device)
        _notify("📲 遠隔実行 失敗", f"{recipe['label']}: {e}")
        return "failed", rec
    record["_proc"] = proc      # 内部参照（results_public はキー選択なので外へ出ない）
    _register(record)
    _audit({k: v for k, v in record.items() if k not in ("output", "_proc")})
    _notify("📲 遠隔実行 開始", recipe["label"])
    t = threading.Thread(target=_run_watcher,
                         args=(record, proc, recipe["timeoutSec"], recipe["returnOutput"]),
                         daemon=True)
    t.start()
    return "running", record


def kill_running(reason="shutdown"):
    """走っているアクションをプロセスグループごと落とす（テストのtearDown・将来の停止操作用）。
    落とした数を返す。watcher が timeout/failed へ落とすので状態は必ず1つに定まる。"""
    n = 0
    with _LOCK:
        procs = [r.get("_proc") for r in _ACTIONS.values() if r["state"] == "running"]
    for proc in procs:
        if proc is None:
            continue
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            n += 1
        except (OSError, ProcessLookupError):
            pass
    return n


def results_public(limit=8):
    """office_json に載せる公開ビュー（新しい順）。output はここでも scrub（二重適用の掟）。"""
    with _LOCK:
        recs = [_ACTIONS[r] for r in reversed(_ORDER)][:limit]
        out = []
        for r in recs:
            out.append({
                "reqId": r["reqId"], "kind": r["kind"], "recipe": r.get("recipe", ""),
                "label": r["label"], "state": r["state"],
                "startedAt": r["startedAt"], "durationMs": r.get("durationMs", 0),
                "exitCode": r.get("exitCode"), "bytes": r.get("bytes", 0),
                "output": scrub_output(r.get("output", "")),
            })
        return out


def recipes_public(recipes):
    """スマホ表示用の最小ビュー（argv/cwd/env は載せない＝中継に構成情報を出さない）。"""
    return [{"id": r["id"], "label": r["label"], "dangerous": r["dangerous"],
             "returnOutput": r["returnOutput"]} for r in recipes]
