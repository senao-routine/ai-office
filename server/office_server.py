#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIオフィス — このMacで動いている Claude Code セッションを
「AI社員が働くドット絵オフィス」としてライブ表示するローカルサーバー。

データ源: ~/.claude/projects/<プロジェクト>/<セッションID>.jsonl
  各セッションが作業するたび追記されるトランスクリプト。末尾を読み
  「いま何をしているか（実行中: git push / 編集中: xxx.py / 考え中…）」を推定する。
  外部送信なし・読み取りのみ・完全ローカル。

指示出し（📨 指示センター）:
  左パネルから社員(セッション)宛に指示を投函 → ~/.claude/office_inbox/<session>.json
  へ書き込み → グローバル Stop hook（~/.claude/hooks/office-inbox-wait.sh・asyncRewake）が
  それを検知してセッションを起こし、指示がモデルに届く。
  待機中のセッションなら数秒以内・作業中ならターン終了直後に配達される。
  送信履歴は ~/.claude/office_inbox/_history.json（配達済みかどうかも画面に表示）。

起動:  python3 "AI Office/server/office_server.py"   # → http://localhost:4780
停止:  Ctrl+C
表示名・担当のカスタマイズ: office_config.json（正本は OFFICE_DATA 配下。P4常駐インストール後は
  ~/Library/Application Support/AIOffice/data/office_config.json・未インストールなら同フォルダ）
"""
import ast
import base64
import binascii
from datetime import datetime
import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote
try:
    import projects_index
    import status_board
    import license as office_license
    import openclaw_source
except ModuleNotFoundError:  # importlibでファイルを直接読む既存テスト向け
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import projects_index
        import status_board
        import license as office_license
        import openclaw_source
    finally:
        del sys.path[0]

HERE = Path(__file__).resolve().parent          # AI Office/server
ROOT = HERE.parent                               # AI Office/


def _load_office_scene():
    """レイアウト検証の単一正本を起動時に読む。"""
    scene_path = ROOT / "ui" / "office_scene.json"
    try:
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"office scene を読めません: {scene_path}") from exc
    if not isinstance(scene, dict):
        raise RuntimeError("office scene のルートがobjectではありません")
    return scene


SCENE = _load_office_scene()


def _scene_layout_bounds(scene):
    rooms = {
        room.get("id"): room for room in scene.get("rooms", [])
        if isinstance(room, dict) and isinstance(room.get("id"), str)
    }
    main_floor = rooms.get("main", {}).get("floor", {})
    brk_floor = rooms.get("brk", {}).get("floor", {})
    entrance = next(
        (door.get("rect", {}) for door in scene.get("doors", [])
         if isinstance(door, dict) and door.get("id") == "entrance"),
        {},
    )
    catalog = scene.get("catalog", {})
    desk_size = catalog.get("deskset", {}).get("footprint", {})
    sofa_catalog = catalog.get("sofaset", {})
    sofa_default = scene.get("defaultLayout", {}).get("sofa", {})
    sofa_w = sofa_default.get("w", sofa_catalog.get("displayW"))
    sofa_h = sofa_catalog.get("displayH")
    if not all(isinstance(value, int) and not isinstance(value, bool)
               for value in (*main_floor.values(), *brk_floor.values(),
                             *entrance.values(), *desk_size.values(),
                             *sofa_default.values())):
        raise RuntimeError("office scene のレイアウト寸法が不正です")
    if not isinstance(sofa_w, int) or not isinstance(sofa_h, int):
        raise RuntimeError("office scene のソファ寸法が不正です")
    desk_w = desk_size.get("w")
    desk_h = desk_size.get("h")
    if not isinstance(desk_w, int) or not isinstance(desk_h, int):
        raise RuntimeError("office scene の机寸法が不正です")
    return {
        "desk": {
            "w": desk_w,
            "h": desk_h,
            "x": (main_floor["x"], main_floor["x"] + main_floor["w"] - desk_w),
            "y": (main_floor["y"], main_floor["y"] + main_floor["h"] - desk_h),
        },
        "sofa": {
            "w": sofa_w,
            "h": sofa_h,
            "x": (brk_floor["x"], brk_floor["x"] + brk_floor["w"] - sofa_w),
            "y": (brk_floor["y"], brk_floor["y"] + brk_floor["h"] - sofa_h),
        },
        "door": {
            "x": (entrance["x"], entrance["x"] + entrance["w"]),
            "y": (entrance["y"], entrance["y"] + entrance["h"]),
        },
    }


SCENE_LAYOUT_BOUNDS = _scene_layout_bounds(SCENE)
ASSIGNABLE_ROOM_IDS = frozenset(
    room["id"] for room in SCENE.get("rooms", [])
    if isinstance(room, dict) and room.get("assignable") is True
    and isinstance(room.get("id"), str)
)
# OFFICE_HOME はテスト用の注入口（未指定なら実HOME）。~/.claude 配下を読む
_HOME = Path(os.environ.get("OFFICE_HOME", str(Path.home())))
# OFFICE_DATA = config+assets の置き場（P4常駐: ~/Library/Application Support/AIOffice/data を
# 指すことで dev/daemon が同一データを読む＝分岐しない）。未設定なら ROOT＝完全後方互換
DATA = Path(os.environ.get("OFFICE_DATA", str(ROOT)))
PROJECTS = _HOME / ".claude" / "projects"
INBOX = _HOME / ".claude" / "office_inbox"
HISTORY_FILE = INBOX / "_history.json"
# R42.5: oc-宛（OpenClaw・別Mac社員）の転送待ちoutbox。relay_agent が署名して中継の
# site=macmini キューへ運び、mini側 tools/openclaw_agent.py が受ける（office_inboxとは別系統）
OC_OUTBOX = _HOME / ".claude" / "office_oc_outbox"
DEVICES_FILE = _HOME / ".claude" / "office_devices.json"   # P3: スマホ端末台帳(600・secret平文)
PORT = 4780
SHOW_WINDOW = int(os.environ.get("OFFICE_SHOW_WINDOW", 3 * 3600))  # 3時間以内に動いたセッションを「出勤中」として表示（R23.5退勤早期化・verify.shカナリア/works watchdogの窓と同期）
TAIL_BYTES = 80_000
TASK_TAIL_BYTES = 8 * 1024 * 1024   # R64: 初回窓。以降は増分読みなのでコストは初回のみ
CACHE_SEC = 2.0
DEFAULT_OFFICE_NAME = "AIオフィス"

# 未登録プロジェクトの表示名に含まれる役割語。上から先に判定する。
ROLE_KEYWORDS = (
    ("video", ("video", "movie", "動画", "編集", "premiere", "davinci")),
    ("shorts", ("shorts", "short", "ショート", "tiktok", "reel")),
    ("blog", ("blog", "ブログ", "note", "記事", "article")),
    ("xpost", ("xpost", "x-", "sns", "twitter", "tweet", "投稿")),
    ("xrun", ("growth", "marketing", "マーケ", "集客")),
    ("sakutto", ("dev", "app", "api", "server", "tool", "開発", "cli", "bot")),
    ("memo", ("memo", "メモ", "note-taking", "docs", "資料")),
    ("ribbon", ("community", "コミュニティ", "support", "サポート")),
)
GENERIC_POOL = (
    "generic_f", "generic_m", "generic_f2", "generic_m2", "generic_f3",
    "generic_m3", "generic_f4", "generic_m4", "generic_f5", "generic_m5",
)

TOOL_VERB = {
    "Bash": "実行中", "Edit": "編集中", "Write": "執筆中", "Read": "読込中",
    "Grep": "調査中", "Glob": "調査中", "Explore": "調査中",
    "WebFetch": "リサーチ中", "WebSearch": "リサーチ中",
    "Task": "部下に指示中", "Agent": "部下に指示中", "Workflow": "部隊を編成中",
    "Skill": "スキル実行中", "AskUserQuestion": "質問中(返答待ち)",
    "TodoWrite": "段取り整理中", "NotebookEdit": "編集中", "Artifact": "ページ制作中",
}
TOOL_VERB_EN = {
    "Bash": "running", "Edit": "editing", "Write": "writing", "Read": "reading",
    "Grep": "researching", "Glob": "researching", "Explore": "researching",
    "WebFetch": "researching", "WebSearch": "researching",
    "Task": "briefing agents", "Agent": "briefing agents", "Workflow": "orchestrating",
    "Skill": "running a skill", "AskUserQuestion": "asking (needs reply)",
    "TodoWrite": "planning", "NotebookEdit": "editing", "Artifact": "building a page",
}

# R42.2d 言語＝オフィス全体設定（config "lang" > OFFICE_LANG env は逆＝env優先・既定 ja）。
# verb/feed/disp はサーバー生成で PC/PWA/Push が同一スナップショットを共有するため、
# クライアント毎でなくサーバー正本の1言語に揃える。_LANG は scan_office が毎スキャン更新。
LANGS = ("ja", "en")
_LANG = "ja"


def office_lang(config=None):
    """解決順= OFFICE_LANG env > config "lang" > OSロケール(en系のみ) > 既定 ja。不正値は ja。
    ロケール段は R50提案2c: 英語圏のクリーンcloneが設定なしで英語になる（launchd 等
    ロケールenvが無い環境では従来どおり ja）。"""
    raw = os.environ.get("OFFICE_LANG")
    if raw is None or not str(raw).strip():
        cfg = config if isinstance(config, dict) else load_config()
        raw = cfg.get("lang")
    if raw is None or not str(raw).strip():
        loc = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
               or os.environ.get("LANG") or "")
        if str(loc).strip().lower().startswith("en"):
            raw = "en"
    val = str(raw or "").strip().lower()
    return val if val in LANGS else "ja"


def L(ja, en):
    """サーバー生成文字列の言語分岐（_LANG参照・scan_office 配下で使う）。"""
    return en if _LANG == "en" else ja


def L_now(ja, en):
    """scan外の応答経路（403文言・license reason 等）用: その場で office_lang() を解決する。
    _LANG は scan_office が更新するため、scanが一度も走っていない経路（?demo=1 のみ閲覧等）
    では鮮度が保証されない＝ここでは都度解決する（R50提案2c・新UI i18nカナリアが検出）。"""
    return en if office_lang() == "en" else ja


DEFAULT_OFFICE_NAME_EN = "AI Office"


def default_office_name(lang):
    return DEFAULT_OFFICE_NAME_EN if lang == "en" else DEFAULT_OFFICE_NAME


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def config_file():
    # OFFICE_CONFIG はテスト用の注入口（未指定なら OFFICE_DATA 配下＝daemon/devで同一正本）
    return Path(os.environ.get("OFFICE_CONFIG", str(DATA / "office_config.json")))


def layout_file():
    # OFFICE_LAYOUT はテスト用の注入口（未指定なら OFFICE_DATA 配下）。
    return Path(os.environ.get("OFFICE_LAYOUT", str(DATA / "office_layout.json")))


def load_config():
    p = config_file()
    if p.exists():
        try:
            config = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(config, dict):
                config.setdefault("officeName", default_office_name(office_lang(config)))
                return config
        except json.JSONDecodeError:
            pass
    return {"officeName": default_office_name(office_lang({})), "projects": {}}


# R42.1 エディション（claude=Claude Code専用 / openclaw=OpenClaw専用 / hybrid=混合）。
# 商品の分割線はここが単一正本。config正本は office_config.json トップレベル "edition"
# （P4常駐/dev/relay/mcpが同一configを見る機構に乗る）。office_layout.json には置かない。
VALID_EDITIONS = ("claude", "openclaw", "hybrid")


def edition(config=None):
    """解決順= OFFICE_EDITION env > config "edition" > 既定 hybrid（開発リポは無指定=全機能）。
    空文字は未指定扱い（install変数の未展開事故で機能を落とさない）・不正値は claude（安全側）。"""
    raw = os.environ.get("OFFICE_EDITION")
    if raw is None or not str(raw).strip():
        cfg = config if isinstance(config, dict) else load_config()
        raw = cfg.get("edition")
    if raw is None or not str(raw).strip():
        return "hybrid"
    val = str(raw).strip().lower()
    return val if val in VALID_EDITIONS else "claude"


# ライセンスがカバーするedition（hybridライセンスは①claude利用も可＝アップグレード動線）
_LICENSE_COVERS = {"claude": ("claude",), "hybrid": ("claude", "openclaw", "hybrid")}


def edition_features(ed, lic=None):
    """機能マトリクス＝商売ロジックの単一集約点。UI/PWA はこの features だけを見て
    表示分岐する（価格ロジックをUIに持たせない）。lic= license_state() の戻り値（省略=未ライセンス）。
    ②openclaw版は完全無料（editionだけで全開）・①③の中継/Push/コストダッシュは有償。"""
    lic = lic if isinstance(lic, dict) else {}
    licensed = (bool(lic.get("valid"))
                and ed in _LICENSE_COVERS.get(str(lic.get("edition") or ""), ()))
    return {
        "claudeSessions": ed in ("claude", "hybrid"),
        "openclaw": ed in ("openclaw", "hybrid"),
        "relayPwa": ed == "openclaw" or licensed,
        "push": ed == "openclaw" or licensed,
        "costDash": ed in ("claude", "hybrid") and licensed,
    }


def license_file():
    # OFFICE_LICENSE はテスト用の注入口（未指定なら実HOME配下・買い切り恒久）
    return Path(os.environ.get("OFFICE_LICENSE", str(_HOME / ".claude" / "office_license.json")))


_license_cache = {"path": None, "mtime": None, "state": None}


def license_state():
    """ライセンス検証結果（mtimeキャッシュ）。鍵素材・署名値はログ/応答に出さない。
    公開鍵はテスト注入口 OFFICE_LICENSE_PUBKEY_N(hex) で差し替え可（未指定=本番鍵）。"""
    p = license_file()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {"valid": False, "edition": "", "reason": L_now("ライセンス未登録", "no license registered")}
    if (_license_cache["state"] is not None and _license_cache["path"] == str(p)
            and _license_cache["mtime"] == mtime):
        return _license_cache["state"]
    try:
        lic = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        state = {"valid": False, "edition": "", "reason": "ライセンスファイルが読めません"}
    else:
        ok, reason = office_license.verify_license(lic, n=_license_pubkey_override())
        state = {"valid": ok,
                 "edition": str(lic.get("edition") or "") if ok else "",
                 "reason": reason}
    _license_cache["path"] = str(p)
    _license_cache["mtime"] = mtime
    _license_cache["state"] = state
    return state


def _license_pubkey_override():
    raw = os.environ.get("OFFICE_LICENSE_PUBKEY_N")
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None


def apply_license(lic):
    """UIからのライセンス登録/解除。検証してから保存（600・tmp+rename原子置換）。
    lic=None は解除。lic=文字列はJSONとして受ける（コピペUI向け）。"""
    p = license_file()
    if lic is None:
        try:
            p.unlink()
        except OSError:
            pass
        return True, "ライセンスを解除しました", {"license": license_state()}
    if isinstance(lic, str):
        try:
            lic = json.loads(lic)
        except json.JSONDecodeError:
            return False, "JSONとして読めません", {}
    if not isinstance(lic, dict):
        return False, "ライセンス形式が不正です", {}
    okv, reason = office_license.verify_license(lic, n=_license_pubkey_override())
    if not okv:
        return False, f"検証に失敗: {reason}", {}
    keep = {k: lic[k] for k in ("v", "edition", "key_id", "issued", "holder", "alg", "sig")
            if k in lic}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return True, f"ライセンス登録完了（{keep.get('edition')}）", {"license": license_state()}


def tail_lines(path, nbytes=TAIL_BYTES):
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - nbytes))
            data = f.read()
        text = data.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        return lines[1:] if size > nbytes and len(lines) > 1 else lines
    except OSError:
        return []


def short(s, n):
    s = re.sub(r"\s+", " ", nfc(str(s))).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _question_options(question):
    """AskUserQuestion の選択肢をUI向けの後方互換スキーマへ縮める。"""
    if not isinstance(question, dict) or not isinstance(question.get("options"), list):
        return None
    options = []
    for option in question["options"][:4]:
        if not isinstance(option, dict):
            continue
        label = short(option.get("label") or "", 60)
        if not label:
            continue
        options.append({
            "label": label,
            "desc": short(option.get("description") or "", 120),
        })
    return options or None


def _latest_pending_question(parsed, blocks):
    """未回答 AskUserQuestion のうち最新の tool_use ブロックを返す。

    tool_use_id の無い旧形式は、既存の「最後のAskUserQuestion」判定と互換にする。
    """
    questions = []
    by_id = {}
    for event in parsed:
        for block in blocks(event):
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use" \
                    and block.get("name") == "AskUserQuestion":
                item = {"block": block, "answered": False}
                questions.append(item)
                use_id = block.get("id") or block.get("tool_use_id")
                if use_id:
                    by_id[str(use_id)] = item
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                use_id = block.get("tool_use_id")
                if use_id and str(use_id) in by_id:
                    by_id[str(use_id)]["answered"] = True
    for item in reversed(questions):
        if not item["answered"]:
            return item["block"]
    return None


def _question_text(block, fallback=""):
    """AskUserQuestion ブロック → 質問文＋選択肢ラベル連結（120字cap）。"""
    try:
        q = block["input"]["questions"][0]
        opts = " / ".join(o.get("label", "") for o in q.get("options", []))
        return short(q.get("question", "") + (f"（{opts}）" if opts else ""), 120)
    except (KeyError, IndexError, TypeError):
        return fallback


def describe_tool(name, inp):
    """tool_use → (動詞, 対象) の吹き出しテキスト"""
    verbs = TOOL_VERB_EN if _LANG == "en" else TOOL_VERB
    verb = verbs.get(name, L("作業中", "working"))
    inp = inp or {}
    target = ""
    if isinstance(inp, dict):
        if inp.get("file_path"):
            target = Path(str(inp["file_path"])).name
        elif inp.get("description"):
            target = inp["description"]
        elif inp.get("command"):
            target = inp["command"]
        elif inp.get("pattern"):
            target = inp["pattern"]
        elif inp.get("skill"):
            target = "/" + str(inp["skill"])
        elif inp.get("prompt"):
            target = inp["prompt"]
        elif inp.get("url"):
            target = inp["url"]
        elif inp.get("questions"):
            try:
                target = inp["questions"][0].get("question", "")
            except (IndexError, AttributeError, TypeError):
                target = ""
    if name and name.startswith("mcp__"):
        verb = L("外部ツール操作中", "using an external tool")
        target = name.split("__")[-1]
    return verb, short(target, 42)


_COMMAND_NAME_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
_SKILL_SAFE_RE = re.compile(r"[^A-Za-z0-9_:-]")


def transcript_event_time(event, fallback):
    """JSONLイベントのtimestampをepoch秒へ変換する。未観測形式はmtimeへ戻す。"""
    raw = event.get("timestamp") if isinstance(event, dict) else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).timestamp()
        except ValueError:
            pass
    return fallback


def safe_skill_name(value):
    """表示用スキル名。スキル名以外の本文やパスを受け取らない呼び出し側と対にする。"""
    cleaned = _SKILL_SAFE_RE.sub("", str(value))
    return cleaned[:64]


def recent_skill_pairs(events, now, fallback_time):
    """Skill tool_use / command-name の直近30分イベントを (epoch, name) 新しい順で返す。"""
    found = []
    for order, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        event_time = transcript_event_time(event, fallback_time)
        if event_time > now or now - event_time > 30 * 60:
            continue
        content = (event.get("message") or {}).get("content")
        candidates = []
        if event.get("type") == "assistant":
            blocks = content if isinstance(content, list) else []
            candidates.extend(
                block.get("input", {}).get("skill")
                for block in blocks
                if isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "Skill"
                and isinstance(block.get("input"), dict)
                and isinstance(block.get("input", {}).get("skill"), str)
            )
        elif event.get("type") == "user":
            user_text = content if isinstance(content, str) else ""
            candidates.extend(_COMMAND_NAME_RE.findall(user_text))
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        candidates.extend(_COMMAND_NAME_RE.findall(block["text"]))
        for value in candidates:
            name = safe_skill_name(value)
            if name:
                found.append((event_time, order, name))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    pairs = []
    seen = set()
    for event_time, _order, name in found:
        if name in seen:
            continue
        seen.add(name)
        pairs.append((event_time, name))
    return pairs


def recent_skills(events, now, fallback_time):
    """Skill tool_use / command-name の直近30分イベント名を新しい順で最大5件返す。"""
    return [name for _ts, name in recent_skill_pairs(events, now, fallback_time)[:5]]


# tail窓(80KB)からスキル行が流れても30分は表示を保つための、セッション別の見たスキル記憶。
# メモリのみ（daemon再起動でリセット可）。office_json へは名前しか出さない。
_SKILL_MEMORY = {}
_SKILL_WINDOW = 30 * 60


def remembered_skills(session_key, events, now, fallback_time):
    """tail窓で見えたスキルをメモリへ合流し、30分窓の一覧（新しい順・最大5）を返す。"""
    memory = _SKILL_MEMORY.setdefault(session_key, {})
    for event_time, name in recent_skill_pairs(events, now, fallback_time):
        if memory.get(name, float("-inf")) < event_time:
            memory[name] = event_time
    for name in [k for k, ts in memory.items() if now - ts > _SKILL_WINDOW]:
        del memory[name]
    if not memory:
        _SKILL_MEMORY.pop(session_key, None)
        return []
    # 他セッションの失効掃除（閉じたセッションの残骸を溜めない）
    for key in [k for k, entries in _SKILL_MEMORY.items()
                if k != session_key and entries
                and now - max(entries.values()) > _SKILL_WINDOW]:
        _SKILL_MEMORY.pop(key, None)
    ordered = sorted(memory.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, _ts in ordered[:5]]


_TASK_MEMORY = {}
_TASK_WINDOW = 60 * 60
_TASK_MARKERS = ('"TaskCreate"', '"TaskUpdate"', '"todos"')
_TASK_STATUSES = frozenset(("pending", "in_progress", "completed", "deleted"))
_TASK_CREATED_RE = re.compile(
    r"Task\s*#\s*([A-Za-z0-9._-]+)\s+created\s+successfully(?:\s*:\s*(.*))?$",
    re.IGNORECASE,
)


def _task_blocks(event):
    content = (event.get("message") or {}).get("content") if isinstance(event, dict) else None
    return content if isinstance(content, list) else []


def _task_result_text(block):
    content = block.get("content") if isinstance(block, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item.get("text", "") for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return ""


# R64: セッションごとの増分読みオフセット {session_key: {"offset": int, "seen": epoch}}。
# 旧実装は毎回「末尾2MB窓」だけを読むため、長大セッション（実測55MB）ではTaskCreateが
# 窓外へ流れてタスクが丸ごと消えた。前回読んだ位置から増分だけ処理して恒久追跡する。
_TASK_OFFSETS = {}


def _pick_task_lines(lines):
    """行リストからタスク関連行だけを軽量に拾う。

    TaskCreateのIDは直後のtool_result本文にしか出ないため、TaskCreate行の
    次の物理行も候補に含める。その他の行はjson.loadsしない。
    """
    if not lines:
        return []
    indexes = set()
    for index, line in enumerate(lines):
        if any(marker in line for marker in _TASK_MARKERS):
            indexes.add(index)
            if '"TaskCreate"' in line and index + 1 < len(lines):
                indexes.add(index + 1)
    return [(index, lines[index]) for index in sorted(indexes)]


def _task_lines(path, session_key=None, now=None):
    """タスク関連行の取得。session_key があれば増分読み:
    初回=末尾TASK_TAIL_BYTES窓・以降=前回オフセットからの新規行のみ。
    オフセットは「最後に読んだ完全行(\\n終端)の直後」＝書き込み途中の不完全行を跨がない。
    末尾の完全行がTaskCreateなら、その行頭で止める（対になるtool_resultが未着のため
    次回まとめて処理する。同一IDの再処理は"set"上書きで冪等）。ファイル縮小はリセット。"""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    state = _TASK_OFFSETS.get(session_key) if session_key else None
    start = None
    if state and 0 <= state["offset"] <= size:
        start = state["offset"]
    if start is None:
        start = max(0, size - TASK_TAIL_BYTES)
    if start >= size:
        if state is not None and now is not None:
            state["seen"] = now
        return []
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(size - start)
    except OSError:
        return []
    end_of_last_full = data.rfind(b"\n")
    if end_of_last_full < 0:
        return []                      # 完全行がまだ無い＝次回へ持ち越し
    chunk = data[:end_of_last_full + 1]
    text = chunk.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if start > 0 and state is None and lines:
        lines = lines[1:]              # tail窓の先頭は行の途中＝捨てる（従来と同じ）
    consumed = len(chunk)
    if lines and '"TaskCreate"' in lines[-1]:
        consumed -= len(lines[-1].encode("utf-8", errors="ignore")) + 1
        lines = lines[:-1]
    if session_key:
        _TASK_OFFSETS[session_key] = {"offset": start + consumed,
                                      "seen": now if now is not None else 0.0}
    return _pick_task_lines(lines)


def _fallback_task_id(order, used):
    """TaskCreate結果が壊れている場合の、出現順に基づく安定したID。"""
    candidate = str(order)
    if candidate not in used:
        return candidate
    numeric = [int(value) for value in used if str(value).isdigit()]
    candidate = str(max(numeric, default=0) + 1)
    while candidate in used:
        candidate = str(int(candidate) + 1)
    return candidate


def _task_operations(task_lines, fallback_time):
    """抽出済みJSONLから、時系列のタスク操作を作る。"""
    parsed = []
    for index, line in task_lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            parsed.append((index, event))

    operations = []
    pending_creates = []
    used_ids = set()
    create_order = 0

    def create_task(record, task_id=None, result_subject=""):
        nonlocal create_order
        create_order += 1
        if not task_id:
            task_id = _fallback_task_id(create_order, used_ids)
        task_id = str(task_id)
        used_ids.add(task_id)
        subject = record.get("subject") or result_subject or record.get("activeForm") or ""
        operations.append(("set", task_id, {
            "subject": str(subject),
            "activeForm": str(record.get("activeForm") or ""),
            "status": "pending",
            "ts": record.get("ts", fallback_time),
        }))

    for _index, event in parsed:
        event_time = transcript_event_time(event, fallback_time)
        for block in _task_blocks(event):
            if not isinstance(block, dict):
                continue
            name = block.get("name")
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                if name == "TaskCreate":
                    pending_creates.append({
                        "use_id": block.get("id") or block.get("tool_use_id"),
                        "subject": inp.get("subject") or "",
                        "activeForm": inp.get("activeForm") or "",
                        "ts": event_time,
                    })
                elif name == "TaskUpdate":
                    status = inp.get("status")
                    if status not in _TASK_STATUSES:
                        continue
                    task_id = inp.get("taskId")
                    if task_id is None or str(task_id) == "":
                        continue
                    task_id = str(task_id)
                    if status == "deleted":
                        operations.append(("delete", task_id, None))
                    else:
                        operations.append(("update", task_id, {
                            "status": status,
                            "activeForm": inp.get("activeForm"),
                            "subject": inp.get("subject"),
                            "ts": event_time,
                        }))
                elif name == "TodoWrite" and isinstance(inp.get("todos"), list):
                    replacement = []
                    for task_index, todo in enumerate(inp["todos"]):
                        if not isinstance(todo, dict):
                            continue
                        status = todo.get("status")
                        if status not in _TASK_STATUSES or status == "deleted":
                            continue
                        replacement.append((str(task_index), {
                            "subject": str(todo.get("content") or ""),
                            "activeForm": str(todo.get("activeForm") or ""),
                            "status": status,
                            "ts": event_time,
                        }))
                    operations.append(("replace", "", replacement))
            elif event.get("type") == "user" and block.get("type") == "tool_result":
                result = _TASK_CREATED_RE.search(_task_result_text(block).strip())
                if not result:
                    continue
                use_id = block.get("tool_use_id")
                pending = next(
                    (item for item in pending_creates if use_id and item.get("use_id") == use_id),
                    None,
                )
                matched = pending is not None
                if pending is None and pending_creates:
                    pending = pending_creates[0]
                if pending is None:
                    continue
                pending_creates.remove(pending)
                create_task(pending, result.group(1) if matched else None, result.group(2) or "")

    for pending in pending_creates:
        # 成功本文が欠けた場合も、出現順の連番で作業を失わない。
        create_task(pending)
    return operations


def _remembered_tasks(session_key, task_lines, now, fallback_time):
    """タスク表を60分だけメモリに保持し、新しく観測した操作を合流する。"""
    memory = _TASK_MEMORY.setdefault(session_key, {})
    for operation, task_id, value in _task_operations(task_lines, fallback_time):
        if operation == "replace":
            memory.clear()
            for replacement_id, replacement in value:
                memory[replacement_id] = replacement
        elif operation == "delete":
            memory.pop(task_id, None)
        elif operation == "set":
            memory[task_id] = value
        elif operation == "update":
            task = memory.setdefault(task_id, {
                "subject": "", "activeForm": "", "status": "pending", "ts": fallback_time,
            })
            for key in ("subject", "activeForm"):
                if value.get(key) not in (None, ""):
                    task[key] = str(value[key])
            task["status"] = value["status"]
            task["ts"] = value["ts"]

    # R64: 剪定は「完了タスクのみ」60分（作業台の掃除）。pending/in_progress は
    # 時間で消さない＝タスク操作が1時間止まっただけで進捗表示が全消えした実測バグの修正。
    # 未完了の無限滞留は TodoWrite の replace とセッション退勤剪定（下）で有界。
    for task_id in [task_id for task_id, task in memory.items()
                    if task.get("status") == "completed"
                    and now - float(task.get("ts", fallback_time)) > _TASK_WINDOW]:
        del memory[task_id]
    # セッション単位の剪定は「最後にスキャンで観測してから SHOW_WINDOW」＝退勤と同期
    # （タスクtsの新旧で消すと、未完了持ちの静かなセッションが道連れになる）
    for key in [key for key in list(_TASK_MEMORY)
                if key != session_key
                and now - (_TASK_OFFSETS.get(key) or {}).get("seen", 0.0) > SHOW_WINDOW]:
        _TASK_MEMORY.pop(key, None)
        _TASK_OFFSETS.pop(key, None)
    if not memory:
        _TASK_MEMORY.pop(session_key, None)
        return {}
    return memory


def _work_from_tasks(tasks):
    if not tasks:
        return None
    counts = {status: 0 for status in ("pending", "in_progress", "completed")}
    for task in tasks.values():
        if task.get("status") in counts:
            counts[task["status"]] += 1

    def ordered(status, reverse, active_form=False):
        entries = [
            (index, task) for index, task in enumerate(tasks.values())
            if task.get("status") == status
        ]
        entries.sort(key=lambda item: (float(item[1].get("ts", 0)), item[0]), reverse=reverse)
        return [str((task.get("activeForm") if active_form else task.get("subject"))
                    or task.get("subject") or "") for _index, task in entries]

    return {
        "now": ordered("in_progress", True, active_form=True)[:3],
        "next": ordered("pending", False)[:4],
        "done": ordered("completed", True)[:3],
        "counts": counts,
    }


def parse_session(path, now):
    """1セッションのjsonl末尾から社員カード情報を作る"""
    mtime = path.stat().st_mtime
    age = now - mtime
    lines = tail_lines(path)
    task_lines = _task_lines(path, session_key=str(path), now=now)
    if not lines:
        return None

    cwd = branch = ""
    status_verb, status_target = L("待機中", "standing by"), ""
    kind = "idle"
    last_tool_name = ""
    recent_feed = []

    parsed = []
    skill_events = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if d.get("cwd"):
            cwd = d["cwd"]
        if d.get("gitBranch"):
            branch = d["gitBranch"]
        if d.get("type") in ("user", "assistant"):
            skill_events.append(d)
            parsed.append(d)
            if len(parsed) > 120:
                parsed.pop(0)

    if not parsed:
        return None

    skills = remembered_skills(str(path), skill_events, now, mtime)
    tasks = _remembered_tasks(str(path), task_lines, now, mtime)
    work = _work_from_tasks(tasks)

    def blocks(d):
        c = (d.get("message") or {}).get("content")
        return c if isinstance(c, list) else []

    latest_pending_question = _latest_pending_question(parsed, blocks)
    last_tool_block = None
    for d in reversed(parsed):
        bs = blocks(d)
        if d["type"] == "assistant":
            tools = [b for b in bs if isinstance(b, dict) and b.get("type") == "tool_use"]
            if tools:
                last_tool_name = tools[-1].get("name", "")
                last_tool_block = tools[-1]
                status_verb, status_target = describe_tool(
                    last_tool_name, tools[-1].get("input"))
                kind = "tool"
                # AskUserQuestion が未回答のまま止まっている → 質問文を拾う
                if last_tool_name == "AskUserQuestion":
                    status_target = _question_text(tools[-1], status_target)
                break
            texts = [b for b in bs if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()]
            if texts:
                status_verb, status_target = L("報告中", "reporting"), short(texts[-1]["text"], 42)
                kind = "said"
                break
            if any(isinstance(b, dict) and b.get("type") == "thinking" for b in bs):
                status_verb, status_target, kind = L("考え中…", "thinking…"), "", "think"
                break
        else:
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in bs):
                status_verb, status_target, kind = (L("考え中…", "thinking…"),
                                                    L("結果を確認して次の一手", "reviewing results"),
                                                    "think")
                break
            content = (d.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                status_verb, status_target = L("指示を受領", "got instructions"), short(content, 42)
                kind = "order"
                break

    if age < 25:
        state = "working"
    elif age < 240 and kind in ("tool", "think", "order"):
        state = "working"
    elif age < 1800:
        state = "waiting"
        if kind == "said":
            status_verb = L("指示待ち", "awaiting orders")
    else:
        state = "resting"
        status_verb, status_target = L("休憩中", "on break"), ""

    # 10行=スマホ承認判断に足る文脈量（3行では中継の💬除去後1-2行しか残らずユーザーFBで拡大・2026-07-13）
    for d in reversed(parsed):
        if len(recent_feed) >= 10:
            break
        for b in reversed(blocks(d)):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                v, t = describe_tool(b.get("name", ""), b.get("input"))
                recent_feed.append(f"{v} {t}".strip())
            elif b.get("type") == "text" and b.get("text", "").strip():
                recent_feed.append("💬 " + short(b["text"], 100))
            if len(recent_feed) >= 10:
                break

    # 会話ウィンドウ用: 直近の報告(assistantテキスト)と受けている指示(userテキスト)
    last_said = last_order = ""
    for d in reversed(parsed):
        if not last_said and d["type"] == "assistant":
            for b in reversed(blocks(d)):
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                    last_said = short(b["text"], 1000)
                    break
        if not last_order and d["type"] == "user":
            c = (d.get("message") or {}).get("content")
            if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
                last_order = short(c, 600)
        if last_said and last_order:
            break

    # 承認待ち検知: 最後のイベントがツール実行のまま75秒以上止まっている
    # （権限ダイアログ待ち or 長時間コマンドの可能性）
    approval_min = 0
    question = ""
    question_options = None
    if kind == "tool" and age > 75:
        if last_tool_name == "AskUserQuestion" and latest_pending_question is last_tool_block:
            # 未回答の質問はresting帯でも残す（ユーザーの回答待ちは時間で消さない）。
            # status_targetはresting遷移で空になるため、質問文はツールブロックから直接導出する。
            question = _question_text(latest_pending_question, status_target)
            try:
                q = latest_pending_question["input"]["questions"][0]
                question_options = _question_options(q)
            except (KeyError, IndexError, TypeError):
                pass
        elif age < 1800:
            # resting帯(30分超)のtool止まりはクラッシュ/放置残骸＝❗を出し続けない（誤プッシュ通知の門番）
            approval_min = max(1, int(age // 60))

    minions = 0
    subdir = path.parent / path.stem / "subagents"
    if subdir.is_dir():
        try:
            for root, _dirs, files in os.walk(subdir):
                for fn in files:
                    if fn.endswith(".jsonl"):
                        try:
                            if now - os.path.getmtime(os.path.join(root, fn)) < 900:
                                minions += 1
                        except OSError:
                            pass
        except OSError:
            pass

    employee = {
        "session": path.stem,
        "cwd": cwd,
        "branch": branch,
        "age": int(age),
        "mtime": mtime,
        "state": state,
        # R50: 直近イベントの種別。UIが「会議室へ行く/考え込む」を決める材料。
        # テキストではなく分類なので中継へ流しても本文は漏れない。
        "kind": kind,
        "verb": status_verb,
        "target": status_target,
        "feed": recent_feed,
        "skills": skills,
        "minions": minions,
        "pending": (INBOX / f"{path.stem}.json").exists(),
        "lastSaid": last_said,
        "lastOrder": last_order,
        "question": question,
        "approvalMin": approval_min,
        "stuckTool": f"{status_verb} {status_target}".strip() if approval_min else "",
    }
    if question_options:
        employee["questionOptions"] = question_options
    if work is not None:
        employee["work"] = work
    return employee


ASSETS = DATA / "assets"    # OFFICE_DATA 未設定なら ROOT/assets（後方互換）

# ── R50 新UI: ui/ 配下の静的配信 ──────────────────────────────────
# 旧UIは単一HTMLで完結していたが、2スタイル構成では ESM・CSS・フォント・three.js を配る必要がある。
# ESモジュールは file:// から import できない（CORS）ので、この経路が無いと新UIは動かない。
# ui/ は開発者が置いたコードだけ＝ユーザーデータではないので、常に ROOT 側を配る（OFFICE_DATA を見ない）。
UI_DIR = ROOT / "ui"
_UI_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}


def ui_asset(rel):
    """/ui/<rel> を ui/ 配下へ閉じ込めて解決する。範囲外・未知拡張子・不存在は None。"""
    try:
        rel = unquote(rel)
    except (UnicodeError, ValueError):
        return None
    if "\x00" in rel:
        return None
    try:
        base = UI_DIR.resolve(strict=True)
        target = (UI_DIR / rel.lstrip("/")).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        target.relative_to(base)          # ../ で ui/ の外へ出る要求を拒否
    except ValueError:
        return None
    if not target.is_file() or target.suffix not in _UI_MIME:
        return None
    return target


def project_config_key(cwd, config, dirname=""):
    """project_label と同じ部分一致規則で既存の設定キーを返す。"""
    key_src = nfc(cwd) or nfc(dirname)
    for pat in config.get("projects", {}):
        if nfc(pat) in key_src:
            return pat
    return None


def project_label(cwd, dirname, config):
    pat = project_config_key(cwd, config, dirname)
    if pat is not None:
        meta = config["projects"][pat]
        return (meta.get("name") or Path(cwd).name, meta.get("role", ""),
                meta.get("sprite", ""))
    base = Path(cwd).name if cwd else dirname.strip("-").split("-")[-1]
    return nfc(base) or "未知のプロジェクト", "", ""


def sprite_url(name, avatar, hint=""):
    """(立ち絵URL, 歩き絵URL) を返す（無ければ役割別/汎用→空）。"""
    base = None
    if name and (ASSETS / name).exists():
        base = name
    elif not name:
        label = nfc(str(hint)).casefold()
        for stem, keywords in ROLE_KEYWORDS:
            if any(keyword.casefold() in label for keyword in keywords):
                candidate = f"{stem}.png"
                if (ASSETS / candidate).is_file():
                    base = candidate
                break
    if not base:
        existing_pool = tuple(
            stem for stem in GENERIC_POOL if (ASSETS / f"{stem}.png").is_file()
        )
        if existing_pool:
            base = f"{existing_pool[avatar % len(existing_pool)]}.png"
    if not base:
        return "", ""
    walk = base.replace(".png", "_walk.png")
    return (f"/assets/{base}",
            f"/assets/{walk}" if (ASSETS / walk).exists() else "")


def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def hook_installed():
    """~/.claude/settings.json の Stop hook 配線だけを確認する。

    Claude Code の hook 設定以外は判定にもレスポンスにも使わない。設定が
    無い、壊れている、または想定外の型なら未導入として扱う。
    """
    settings_file = _HOME / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    stop_hooks = hooks.get("Stop")
    if isinstance(stop_hooks, dict):
        stop_hooks = [stop_hooks]
    if not isinstance(stop_hooks, list):
        return False
    for group in stop_hooks:
        if not isinstance(group, dict):
            continue
        entries = group.get("hooks")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if (isinstance(entry, dict) and isinstance(entry.get("command"), str)
                    and "office-inbox-wait.sh" in entry["command"]):
                return True
    return False


# ── R50: セッション → プロジェクト集約（1アバター＝1プロジェクト） ──────────
# なぜ: 同じフォルダでターミナルを3本開くと「動画編集部 / 2号 / 3号」が別々の机に座る。
#       ユーザー評価＝「メタ認知の視点でシンプルさがなくなる」。1プロジェクト1人の同僚にする。
# 掟:  employees[] は**変えない**（旧UIの挙動と既存テストを守る＝併走の条件）。
#      集約結果は projects[] として並置し、新UIだけがこちらを読む。
_STATE_RANK = {"working": 0, "waiting": 1, "resting": 2}


def project_id_for(cwd, fallback=""):
    """席の永続化キー。cwd のハッシュなのでパスを含まず、中継へ載せても安全。"""
    src = nfc(cwd) or nfc(fallback)
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]


def _session_brief(e):
    """内訳表示用の最小情報。本文・パスは**構造的に持たない**（redactionに頼らない）。"""
    return {
        "session": e.get("session", ""),
        "state": e.get("state", ""),
        "age": int(e.get("age") or 0),
        "attention": bool(e.get("approvalMin") or e.get("question")),
        "minions": int(e.get("minions") or 0),
        "pending": bool(e.get("pending")),
    }


def group_by_project(employees, lang="ja"):
    """employees[] を cwd 単位に畳んで projects[] を作る（employees は破壊しない）。

    代表セッション = ❗を出している中で最新 → 居なければ全体で最新。
    これが projects[].session になるので、投函・hook・MCP・署名の経路は無改造で動く。
    """
    order = []
    groups = {}
    for e in employees:
        if e.get("external"):
            # 別Macの稼働体。まとめず1体1プロジェクトとして扱う（専用区画に置くため）。
            key = f"ext:{e.get('session', '')}"
        else:
            key = f"cwd:{nfc(e.get('cwd') or '') or 'dept:' + nfc(e.get('dept') or '')}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)

    projects = []
    for key in order:
        members = groups[key]
        attn = [m for m in members if m.get("approvalMin") or m.get("question")]
        pool = attn or members
        lead = min(pool, key=lambda m: (int(m.get("age") or 0), m.get("session", "")))
        state = min((m.get("state", "resting") for m in members),
                    key=lambda s: _STATE_RANK.get(s, 9))
        proj = {
            "projectId": (lead.get("session", "") if lead.get("external")
                          else project_id_for(lead.get("cwd", ""), lead.get("dept", ""))),
            "session": lead.get("session", ""),          # 代表＝指示の宛先
            "name": lead.get("dept", ""),
            "role": lead.get("role", ""),
            "cwd": lead.get("cwd", ""),
            "branch": lead.get("branch", ""),
            "crew": len(members),
            "state": state,
            "kind": lead.get("kind", "idle"),
            "verb": lead.get("verb", ""),
            "target": lead.get("target", ""),
            "age": min(int(m.get("age") or 0) for m in members),
            "minions": sum(int(m.get("minions") or 0) for m in members),
            "pending": any(bool(m.get("pending")) for m in members),
            "attention": bool(attn),
            "approvalMin": int(lead.get("approvalMin") or 0),
            "question": lead.get("question", ""),
            "stuckTool": lead.get("stuckTool", ""),
            "lastSaid": lead.get("lastSaid", ""),
            "lastOrder": lead.get("lastOrder", ""),
            "feed": lead.get("feed", []),
            "skills": lead.get("skills", []),
            "sprite": lead.get("sprite", ""),
            "spriteWalk": lead.get("spriteWalk", ""),
            "avatar": int(lead.get("avatar") or 0),
            "sessions": [_session_brief(m) for m in
                         sorted(members, key=lambda m: int(m.get("age") or 0))],
        }
        if lead.get("external"):
            proj["external"] = lead["external"]
        if lead.get("questionOptions"):
            proj["questionOptions"] = lead["questionOptions"]
        if lead.get("work"):
            proj["work"] = lead["work"]
        # 表示名: 同名プロジェクトが並ぶことは（cwd単位なので）原則ないが、
        # dept フォールバック時の衝突に備えて採番は残す。
        proj["disp"] = proj["name"]
        projects.append(proj)

    seen = {}
    for p in projects:
        n = seen.get(p["name"], 0) + 1
        seen[p["name"]] = n
        if n > 1:
            p["disp"] = f"{p['name']} #{n}" if lang == "en" else f"{p['name']} {n}号"
    return projects


def task_totals(projects):
    """参考画像1の TASKS パネル用。work{counts} の合算＝実データだけを出す（嘘の数値を出さない）。"""
    totals = {"pending": 0, "inProgress": 0, "completed": 0}
    for p in projects:
        counts = (p.get("work") or {}).get("counts") or {}
        totals["pending"] += int(counts.get("pending") or 0)
        totals["inProgress"] += int(counts.get("in_progress") or 0)
        totals["completed"] += int(counts.get("completed") or 0)
    return totals


def scan_office():
    now = time.time()
    config = load_config()
    ed = edition(config)
    lic = license_state()
    edition_info = {"id": ed, "features": edition_features(ed, lic),
                    "license": {"valid": bool(lic.get("valid")),
                                "edition": lic.get("edition", ""),
                                "reason": lic.get("reason", "")}}
    global _LANG
    _LANG = office_lang(config)
    setup = {"hookInstalled": hook_installed()}
    employees = []
    # claudeSessions=false（openclaw専用エディション）や PROJECTS 不在では transcript を読まない
    # （R42.3: 早期returnを廃止＝openclaw社員はPROJECTS不在のMacでも表示できる）
    scan_dirs = (PROJECTS.iterdir()
                 if edition_info["features"]["claudeSessions"] and PROJECTS.is_dir() else ())
    for proj in scan_dirs:
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            try:
                if now - f.stat().st_mtime > SHOW_WINDOW:
                    continue
            except OSError:
                continue
            info = parse_session(f, now)
            if info:
                dept, role, spr = project_label(info["cwd"], proj.name, config)
                info["dept"] = dept
                info["role"] = role
                info["avatar"] = sum(ord(c) for c in info["session"]) % 8
                hint = " ".join(
                    part for part in (dept, role, proj.name, info["cwd"]) if part
                )
                info["sprite"], info["spriteWalk"] = sprite_url(
                    spr, info["avatar"], hint=hint
                )
                employees.append(info)
    employees.sort(key=lambda e: e["mtime"], reverse=True)
    counts = {}
    for e in employees:
        n = counts.get(e["dept"], 0) + 1
        counts[e["dept"]] = n
        e["disp"] = (e["dept"] if n == 1
                     else f"{e['dept']} #{n}" if _LANG == "en"
                     else f"{e['dept']} {n}号")

    # R42.3: OpenClaw社員のマージ（disp採番後に追加＝oc名前空間で独立採番済み。
    # UIは employees[].external の休眠配線で点灯・PWAは机割当から除外済み）
    if edition_info["features"]["openclaw"]:
        oc_emps, _oc_meta = openclaw_source.openclaw_employees(_HOME, now, lang=_LANG)
        employees.extend(oc_emps)

    # 送信履歴（配達状況つき・新しい順12件）
    hist = []
    for h in reversed(load_history()[-12:]):
        hist.append({
            "session": h.get("session", ""),
            "disp": h.get("disp") or h.get("session", "")[:8],
            "text": h.get("text", ""),
            "ts": h.get("ts", 0),
            "pending": (INBOX / f"{h.get('session','')}.json").exists(),
        })

    # R50: 新UIが読む集約ビュー。employees[] は旧UIのために無改造で残す。
    # キー名は roster（"projects" は projects_index のローカルパス一覧が持つ名前。
    # 「office_json に projects を混ぜない」既存の privacy 番人と衝突させない）。
    roster = group_by_project(employees, _LANG)

    return {
        "officeName": config.get("officeName") or default_office_name(_LANG),
        "employees": employees,
        "roster": roster,
        "history": hist,
        "generatedAt": now,
        "setup": setup,
        "edition": edition_info,
        "lang": _LANG,
        "counts": {
            "working": sum(1 for e in employees if e["state"] == "working"),
            "waiting": sum(1 for e in employees if e["state"] == "waiting"),
            "resting": sum(1 for e in employees if e["state"] == "resting"),
        },
        "rosterCounts": {
            "total": len(roster),
            "working": sum(1 for p in roster if p["state"] == "working"),
            "waiting": sum(1 for p in roster if p["state"] == "waiting"),
            "resting": sum(1 for p in roster if p["state"] == "resting"),
            "attention": sum(1 for p in roster if p["attention"]),
        },
        "tasks": task_totals(roster),
    }


_cache = {"t": 0.0, "data": None}
_OPENCLAW_CACHE_SEC = 60.0
_openclaw_cache = {"at": None, "data": None}
_lock = threading.Lock()
_KEY_NAMES = frozenset({
    "OPENAI_API_KEY", "X_BEARER_TOKEN", "OPENAI_ADMIN_KEY",
    # R63: APIプロバイダ統合コスト（保存先・検証は既存経路のまま）
    "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY",
})
_KEY_VALUE = re.compile(r"^[A-Za-z0-9%_\-\.=/+]{20,300}$")


class _file_flock:
    """任意ファイルのプロセス間ロック。daemon(launchd)・dev(officectl)・relay_agent・mcp_office が
    同じ config/history を共有するため、スレッドロック(_lock)に加えて flock で read-modify-write を
    直列化する（lost update 防止）。常に _lock → flock の順で取る（デッドロック回避）。"""
    def __init__(self, target):
        self._lockpath = Path(target).with_name(Path(target).name + ".lock")

    def __enter__(self):
        self._lockpath.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self._lockpath, "w")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self._f

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._f, fcntl.LOCK_UN)
        finally:
            self._f.close()


# 掟: レイアウトはローカル設定＝office_json に混ぜない（中継に載せない）。
_LAYOUT_KEYS = ("desks", "sofa", "door")
_LEGACY_LAYOUT_KEYS = frozenset({"meet", "meetLead", "minions"})


def _layout_int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _layout_fields(value, fields):
    """必須フィールドを検証し、余計なキーを落とした新しいdictを返す。"""
    if not isinstance(value, dict) or any(name not in value for name in fields):
        raise ValueError("missing layout field")
    return {name: value[name] for name in fields}


def _validate_room_pins(room_pins):
    if not isinstance(room_pins, dict):
        raise ValueError("invalid roomPins")
    clean = {}
    used_rooms = set()
    for project_key, room_id in room_pins.items():
        # UIのprojectKeyForと同じく、cwdを識別子にする。キー全体で200字まで。
        if (not isinstance(project_key, str) or len(project_key) > 200 or
                not project_key.startswith("cwd:") or
                not project_key[4:].strip().rstrip("/\\") or
                project_key != "cwd:" + project_key[4:].strip().rstrip("/\\")):
            raise ValueError("invalid roomPins projectKey")
        if (not isinstance(room_id, str) or room_id not in ASSIGNABLE_ROOM_IDS):
            raise ValueError("invalid roomPins roomId")
        if room_id in used_rooms:
            raise ValueError("duplicate roomPins roomId")
        clean[project_key] = room_id
        used_rooms.add(room_id)
    return clean


def validate_layout(layout):
    """desks/sofa/doorと任意のroomPinsを保存可能な形へ正規化する。不正ならNone。"""
    try:
        if (not isinstance(layout, dict) or
                any(name not in layout for name in _LAYOUT_KEYS) or
                _LEGACY_LAYOUT_KEYS.intersection(layout)):
            raise ValueError("incomplete or legacy layout")

        desks = layout["desks"]
        if not isinstance(desks, list) or not 1 <= len(desks) <= 10:
            raise ValueError("invalid desks")
        clean_desks = []
        for raw in desks:
            desk = _layout_fields(raw, ("dx", "dy"))
            bounds = SCENE_LAYOUT_BOUNDS["desk"]
            if (not _layout_int(desk["dx"], *bounds["x"]) or
                    not _layout_int(desk["dy"], *bounds["y"])):
                raise ValueError("desk outside main floor")
            clean_desks.append(desk)

        sofa = _layout_fields(layout["sofa"], ("x", "y", "w"))
        bounds = SCENE_LAYOUT_BOUNDS["sofa"]
        if (not _layout_int(sofa["x"], *bounds["x"]) or
                not _layout_int(sofa["y"], *bounds["y"]) or
                not _layout_int(sofa["w"], bounds["w"], bounds["w"])):
            raise ValueError("sofa outside break corner")

        door = _layout_fields(layout["door"], ("x", "y"))
        bounds = SCENE_LAYOUT_BOUNDS["door"]
        if (not _layout_int(door["x"], *bounds["x"]) or
                not _layout_int(door["y"], *bounds["y"])):
            raise ValueError("door outside entrance")
        room_pins = _validate_room_pins(layout.get("roomPins", {}))
    except (KeyError, TypeError, ValueError):
        return None

    return {"desks": clean_desks, "sofa": sofa, "door": door, "roomPins": room_pins}


def _default_layout():
    """scene.defaultLayoutをvalidate_layoutと同じ形式で返す。"""
    raw = SCENE.get("defaultLayout")
    clean = validate_layout(raw)
    if clean is None:
        raise RuntimeError("office scene のdefaultLayoutが不正です")
    return clean


def layout_json():
    target = layout_file()
    try:
        layout = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(layout, dict):
            raise ValueError("layout must be an object")
        # R25教訓: 間取り改装でvalidate範囲が変わると、保存済みカスタムが新しい部屋に
        # 重なって「ソファが壁を飛び出す/旧会議室へ壁抜け歩行」する。読み込み時にも
        # 現行rangeで検証し、通らない古いレイアウトは既定へフォールバックする。
        layout = validate_layout(layout)
        if layout is None:
            raise ValueError("stale layout out of range")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {"custom": False, "layout": None, "roomPins": {}}
    return {"custom": True, "layout": layout, "roomPins": layout["roomPins"]}


def set_layout(layout):
    target = layout_file()
    if layout is None:
        try:
            with _lock, _file_flock(target):
                target.unlink(missing_ok=True)
        except OSError:
            return False, "レイアウトを削除できませんでした"
        return True, "既定レイアウトに戻しました"

    clean = validate_layout(layout)
    if clean is None:
        return False, "レイアウトが不正です"

    tmp = target.with_name(f".{target.name}.tmp")
    try:
        with _lock, _file_flock(target):
            try:
                tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(tmp, target)
            finally:
                # 固定tmp名なので、次のwriterが作る前にflock保持中のまま片付ける。
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
    except (OSError, UnicodeError):
        return False, "レイアウトを保存できませんでした"
    return True, "保存しました"


def set_room_pins(room_pins):
    """roomPinsだけの更新を既存レイアウトへマージして保存する。"""
    try:
        clean_pins = _validate_room_pins(room_pins)
    except ValueError:
        return False, "roomPinsが不正です"

    target = layout_file()
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        with _lock, _file_flock(target):
            base = _default_layout()
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
                current = validate_layout(current)
            except (OSError, UnicodeError, json.JSONDecodeError):
                current = None
            merged = current or base
            merged["roomPins"] = clean_pins
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            try:
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
    except (OSError, UnicodeError, RuntimeError):
        return False, "roomPinsを保存できませんでした"
    return True, "保存しました"


def _office_secrets_file():
    return _HOME / ".claude" / "office_secrets"


def _office_key_line(name):
    """office_secrets の対象行だけを読み、(行の有無, 値) を返す。"""
    target_name = name
    found = False
    value = ""
    try:
        for line in _office_secrets_file().read_text(encoding="utf-8").splitlines():
            line_name, sep, candidate = line.partition("=")
            if sep and line_name == target_name:
                found = True
                value = candidate
    except (OSError, UnicodeError):
        pass
    return found, value


def _openai_key_line():
    """後方互換用に OpenAI APIキーだけを読む。"""
    return _office_key_line("OPENAI_API_KEY")


def keys_status():
    """ローカル連携状況。秘密値は masked 以外を返さない。"""
    key_values = {name: _office_key_line(name)[1] for name in _KEY_NAMES}

    def masked(name):
        value = key_values[name]
        # 手編集された短い値は全体露出を避けるため表示しない。UIから保存した値は常に20文字以上。
        return f"{value[:5]}…{value[-4:]}" if len(value) >= 20 else ""

    # 掟: このデータを office_json/status_board に混ぜない（中継漏洩防止）。
    return {"providers": [
        {
            "id": "claude", "label": "Claude Code", "mode": "auto",
            "connected": PROJECTS.is_dir(),
            "hint": "~/.claude/projects を読取（設定不要）",
        },
        {
            "id": "codex", "label": "Codex", "mode": "cli",
            "connected": (_HOME / ".codex" / "auth.json").is_file(),
            "hint": "ターミナルで codex login",
        },
        {
            "id": "gemini", "label": "Gemini CLI", "mode": "cli",
            "connected": (_HOME / ".gemini" / "oauth_creds.json").is_file(),
            "hint": "ターミナルで gemini（初回ログイン）",
        },
        {
            "id": "openai_key", "label": "OpenAI APIキー（キャラ画像生成）", "mode": "key",
            "connected": bool(key_values["OPENAI_API_KEY"]),
            "masked": masked("OPENAI_API_KEY"),
            "hint": "下の入力欄から保存（~/.claude/office_secrets・600）",
        },
        {
            "id": "x_api", "label": "X API（使用量の自動取得）", "mode": "key",
            "connected": bool(key_values["X_BEARER_TOKEN"]),
            "masked": masked("X_BEARER_TOKEN"),
            "hint": "X Developer PortalのBearer Tokenを保存（読み取り専用でOK）",
        },
        {
            "id": "openai_usage", "label": "OpenAI 使用金額（管理キー）", "mode": "key",
            "connected": bool(key_values["OPENAI_ADMIN_KEY"]),
            "masked": masked("OPENAI_ADMIN_KEY"),
            "hint": "organization Admin key（sk-admin…）を保存",
        },
        # R63: APIプロバイダ（消費・残高の自動取得）
        {
            "id": "openrouter", "label": "OpenRouter（消費・上限）", "mode": "key",
            "connected": bool(key_values["OPENROUTER_API_KEY"]),
            "masked": masked("OPENROUTER_API_KEY"),
            "hint": "APIキー（sk-or-v1…）を保存＝当月消費とキー上限を取得",
        },
        {
            "id": "moonshot", "label": "Kimi / Moonshot（残高）", "mode": "key",
            "connected": bool(key_values["MOONSHOT_API_KEY"]),
            "masked": masked("MOONSHOT_API_KEY"),
            "hint": "api.moonshot.ai のAPIキーを保存（残高のみ取得可）",
        },
        {
            "id": "deepseek", "label": "DeepSeek（残高）", "mode": "key",
            "connected": bool(key_values["DEEPSEEK_API_KEY"]),
            "masked": masked("DEEPSEEK_API_KEY"),
            "hint": "APIキーを保存（残高のみ取得可）",
        },
        {
            "id": "groq", "label": "Groq（予算のみ）", "mode": "key",
            "connected": bool(key_values["GROQ_API_KEY"]),
            "masked": masked("GROQ_API_KEY"),
            "hint": "消費APIが無いプロバイダ＝手動で予算上限を設定して使う",
        },
    ]}


def set_office_key(name, value):
    """許可済みのキーを office_secrets へ原子的に保存する。値は応答・ログへ出さない。
    R65: value="" は解除＝該当行を削除する（存在しないキーの解除は冪等に成功。
    20文字以上のバリデーションは非空値のみに適用）。"""
    if not isinstance(name, str) or name not in _KEY_NAMES:
        return False, "キー名が不正です"
    delete = value == ""
    if not delete and (not isinstance(value, str) or not _KEY_VALUE.fullmatch(value)):
        return False, "キー値の形式が不正です"

    target = _office_secrets_file()
    tmp = target.with_name(f".{target.name}.tmp")
    with _lock, _file_flock(target):
        try:
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                lines = []

            if delete:
                updated = [line for line in lines
                           if line.partition("=")[:2] != (name, "=")]
            else:
                replacement = f"{name}={value}"
                updated = []
                replaced = False
                for line in lines:
                    if line.partition("=")[:2] == (name, "="):
                        if not replaced:
                            updated.append(replacement)
                            replaced = True
                    else:
                        updated.append(line)
                if not replaced:
                    updated.append(replacement)

            tmp.write_text("\n".join(updated) + ("\n" if updated else ""),
                           encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            os.chmod(target, 0o600)
        except (OSError, UnicodeError):
            return False, "キーを保存できませんでした"
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return True, (L_now("解除しました", "removed") if delete
                  else L_now("保存しました", "saved"))


def office_json():
    with _lock:
        if time.time() - _cache["t"] > CACHE_SEC or _cache["data"] is None:
            _cache["data"] = scan_office()
            _cache["t"] = time.time()
        return _cache["data"]


def external_openclaw_json(now=None):
    """OpenClaw接続状態のビュー（R42.3で openclaw_source 直結・最大60秒キャッシュ）。
    UIの接続バナー用＝要約のみ（employees本体は office_json 経由で運ぶ）。"""
    current = float(time.time() if now is None else now)
    cached_at = _openclaw_cache["at"]
    if (_openclaw_cache["data"] is not None and cached_at is not None
            and 0 <= current - cached_at < _OPENCLAW_CACHE_SEC):
        return _openclaw_cache["data"]
    emps, meta = openclaw_source.openclaw_employees(_HOME, current, lang=office_lang())
    data = {
        "connected": bool(meta.get("connected")),
        "reason": meta.get("reason") or "",
        "site": meta.get("site") or "",
        "employees": [{"session": e["session"], "disp": e["disp"],
                       "state": e["state"], "verb": e["verb"]} for e in emps],
    }
    _openclaw_cache["at"] = current
    _openclaw_cache["data"] = data
    return data


def _record_instruction_history(session, text):
    """投函履歴に1行追記（表示名は直近スキャンから引く）。
    RMW を _lock→flock でプロセス間直列化（daemon/dev/relay_agent/mcp_office 併走の lost update 防止）。
    書きは tmp+rename の原子置換＝ロック無しの表示読者(scan_office→load_history)が破断ファイルを見ない。"""
    disp = ""
    data = _cache.get("data") or {}
    for e in data.get("employees", []):
        if e["session"] == session:
            disp = e["disp"]
            break
    with _lock, _file_flock(HISTORY_FILE):
        h = load_history()
        h.append({"session": session, "disp": disp, "text": text, "ts": time.time()})
        tmp = HISTORY_FILE.with_name(f".{HISTORY_FILE.name}.tmp")
        tmp.write_text(json.dumps(h[-50:], ensure_ascii=False), encoding="utf-8")
        tmp.replace(HISTORY_FILE)
        _cache["t"] = 0.0


def post_instruction(session, text):
    """指示を投函（Stop hook の office-inbox-wait.sh が配達する）。
    R42.5: oc-宛（OpenClaw・別Mac）は office_inbox でなく OC_OUTBOX へ＝relay_agent が
    中継の site=macmini キューへ署名転送する（バリデーション・履歴は両経路共通）。"""
    if not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", session or ""):
        return False, "session id が不正です"
    text = (text or "").strip()
    if not text:
        return False, "指示が空です"
    if len(text) > 4000:
        return False, "指示が長すぎます(4000字まで)"
    if session.startswith("oc-"):
        OC_OUTBOX.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}-000.json"
        seq = 0
        while (OC_OUTBOX / name).exists():          # 同msの連投は連番で衝突回避
            seq += 1
            name = f"{int(time.time() * 1000)}-{seq:03d}.json"
        tmp = OC_OUTBOX / f".{name}.tmp"
        tmp.write_text(json.dumps({"session": session, "text": text, "ts": time.time()},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.rename(OC_OUTBOX / name)
        _record_instruction_history(session, text)
        return True, L_now("OpenClaw へ転送待ちに置きました（中継が配達します）",
                           "Queued for OpenClaw (the relay will deliver it)")
    INBOX.mkdir(parents=True, exist_ok=True)
    tmp = INBOX / f".{session}.tmp"
    tmp.write_text(json.dumps({"text": text, "ts": time.time(), "from": "office"},
                              ensure_ascii=False), encoding="utf-8")
    tmp.rename(INBOX / f"{session}.json")
    _record_instruction_history(session, text)
    return True, "投函しました"


# ---- ➕ 新しいプロジェクト起動（P1） ------------------------------------
# 流れ: pick(フォルダ選択) → new(config先頭に登録 → キャラ生成を裏で開始 → Terminalでclaude起動)
# spriteは生成完了後にconfigへ書く（生成前に書くとsprite実在チェックが壊れる＆
# 完了した瞬間に汎用キャラから専用キャラへ「着替える」演出になる）
GEN_STATUS = {}   # slug -> {"state": "generating"|"done"|"error", "msg"/"note": str}
_RESERVING = set()  # 生成中でまだPNGが無いslug（実在チェックだけでは衝突するため予約する）

# R2 のキャラ変更パネルに常設する出荷スプライト。写真生成ではこれらを上書きせず、
# 既存 sprite がこの集合外のときだけ runtime custom の stem を引き継ぐ。
_STANDARD_SPRITES = frozenset({
    "blog.png", "generic_f.png", "generic_m.png", "memo.png", "ribbon.png",
    "sakutto.png", "shorts.png", "video.png", "works_hq.png", "xpost.png", "xrun.png",
    "generic_f2.png", "generic_m2.png", "generic_f3.png", "generic_m3.png",
    "generic_f4.png", "generic_m4.png", "generic_f5.png", "generic_m5.png",
})
_PHOTO_MAX_BYTES = 5 * 1024 * 1024
_PHOTO_BODY_MAX_BYTES = 8_000_000
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def pick_folder():
    """フォルダ選択ダイアログ。OFFICE_PICK_DIR はテスト用の注入口（ダイアログ省略）"""
    mock = os.environ.get("OFFICE_PICK_DIR")
    if mock:
        p = Path(mock).expanduser()
        return (True, str(p)) if p.is_dir() else (False, "OFFICE_PICK_DIR が実在しません")
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'POSIX path of (choose folder with prompt '
             '"AI Office: 新しいプロジェクトのフォルダを選んでください")'],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"フォルダ選択を開けませんでした ({e})"
    if r.returncode != 0:
        return False, "キャンセルされました"
    path = r.stdout.strip().rstrip("/")
    return (True, path) if path and Path(path).is_dir() else (False, "フォルダを取得できませんでした")


def sprite_slug(pattern, base):
    """スプライトファイル名（ascii化・日本語のみならハッシュ・既存PNGとも生成中slugとも衝突しない）
    ※呼び出し側が _lock を保持している前提（_RESERVING を参照するため）"""
    s = re.sub(r"[^a-z0-9]+", "_", nfc(base).lower()).strip("_")[:24]
    if len(s) < 2:
        s = "proj_" + hashlib.md5(nfc(pattern).encode("utf-8")).hexdigest()[:6]
    slug, i = s, 2
    while (ASSETS / f"{slug}.png").exists() or slug in _RESERVING:
        slug = f"{s}{i}"
        i += 1
    return slug


def project_pattern(path):
    """cwdマッチ用パターン。ホーム配下なら相対形（他Macでも同じ形になる）"""
    p = str(Path(path).expanduser().resolve())
    home = str(Path.home())
    return p[len(home) + 1:] if p.startswith(home + "/") else p


def _write_config(cfg):
    cf = config_file()
    tmp = cf.with_name(cf.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(cf)


def _set_sprite(pattern, sprite):
    """キャラ生成完了後に呼ばれ、configの該当エントリへspriteを書き足す"""
    with _lock, _file_flock(config_file()):
        try:
            cfg = json.loads(config_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if pattern in cfg.get("projects", {}):
            cfg["projects"][pattern]["sprite"] = sprite
            _write_config(cfg)
        _cache["t"] = 0.0


def set_lang(lang):
    """UIの🌐トグルから言語をconfigへ永続化（オフィス全体設定・R42.2d）。"""
    val = str(lang or "").strip().lower()
    if val not in LANGS:
        return False, "lang は ja / en のみ"
    with _lock, _file_flock(config_file()):
        try:
            cfg = json.loads(config_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {"projects": {}}
        if not isinstance(cfg, dict):
            cfg = {"projects": {}}
        cfg["lang"] = val
        _write_config(cfg)
        _cache["t"] = 0.0
    return True, val


def set_sprite(cwd, sprite):
    """既存プロジェクトの vintage 正本スプライトだけを差し替える。"""
    if not isinstance(cwd, str) or not cwd:
        return False, "cwd が不正です"
    if (not isinstance(sprite, str)
            or not re.fullmatch(r"[a-z0-9_]+\.png", sprite)
            or "__" in sprite):
        return False, "sprite 名が不正です"
    if not (ASSETS / sprite).is_file():
        return False, "sprite が見つかりません"
    config = load_config()
    pattern = project_config_key(cwd, config)
    if pattern is None:
        # macOS の /var→/private/var 等の symlink 差でキー不一致になるため realpath でも照合
        pattern = project_config_key(os.path.realpath(cwd), config)
    if pattern is None:
        return False, "プロジェクトが見つかりません"
    _set_sprite(pattern, sprite)
    return True, "変更しました"


def _ready_themes():
    """theme_gen.py を import せず、出荷済みテーマ名だけを軽量に読み取る。"""
    theme_gen = ROOT / "tools" / "theme_gen.py"
    if not theme_gen.is_file():
        theme_gen = ROOT / "app" / "tools" / "theme_gen.py"
    try:
        text = theme_gen.read_text(encoding="utf-8")
        match = re.search(r"^THEMES_READY\s*=\s*(\[.*?\])", text, re.M)
        themes = ast.literal_eval(match.group(1)) if match else None
        if not isinstance(themes, list) or not all(isinstance(t, str) for t in themes):
            raise ValueError("THEMES_READY is not a string list")
        return themes
    except (OSError, SyntaxError, ValueError, AttributeError) as e:
        print(f"[theme custom] THEMES_READY を読めません: {short(e, 200)}")
        return []


# codex_image.sh は「最新PNG収穫」方式のため並列生成が混線する→テーマ経由のcustom生成はプロセス内直列化。
_CUSTOM_GEN_LOCK = threading.Lock()


def _promote_theme_custom(slug, theme):
    """theme_gen 生成物(<slug>__<theme>*.png)を無印（既定表示の正）へ昇格コピー。立ち絵必須・歩き絵はあれば。"""
    source = ASSETS / f"{slug}__{theme}.png"
    if not source.is_file():
        return False
    shutil.copyfile(source, ASSETS / f"{slug}.png")
    walk = ASSETS / f"{slug}__{theme}_walk.png"
    if walk.is_file():
        shutil.copyfile(walk, ASSETS / f"{slug}_walk.png")
    return True


def _generate_sprite(slug, label, pattern):
    """customキャラ生成（同期・テスト可能）。R23.5画風追随:
    主レーン= theme_gen READY先頭テーマ（現行画風・Codexサブスク・参照アンカー付き）→無印へ昇格コピー。
    失敗/未READY時は assets_gen custom（旧画風・OpenAI API）へフォールバック＝Codex障害でも生成は死なない。"""
    ok = False
    detail = ""
    themes = _ready_themes()
    primary = themes[0] if themes else ""
    try:
        if primary:
            try:
                with _CUSTOM_GEN_LOCK:
                    themed = subprocess.run(
                        [sys.executable, str(ROOT / "tools" / "theme_gen.py"),
                         primary, "custom", slug, label],
                        capture_output=True, text=True, timeout=900)
                ok = themed.returncode == 0 and _promote_theme_custom(slug, primary)
                if not ok:
                    detail = short((themed.stdout or "") + (themed.stderr or ""), 200)
                    print(f"[theme custom] {primary} 主生成失敗→assets_genへフォールバック: {detail}")
            except (OSError, subprocess.TimeoutExpired) as e:
                detail = short(str(e), 200)
                print(f"[theme custom] {primary} 主生成例外→assets_genへフォールバック: {detail}")
        if not ok:
            r = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "assets_gen.py"), "custom", slug, label],
                capture_output=True, text=True, timeout=900)
            ok = r.returncode == 0 and (ASSETS / f"{slug}.png").exists()
            if not ok:
                detail = detail or short(r.stdout + r.stderr, 200)
        if ok:
            _set_sprite(pattern, f"{slug}.png")
        GEN_STATUS[slug] = {"state": "done" if ok else "error", "msg": "" if ok else detail}
    except (OSError, subprocess.TimeoutExpired) as e:
        GEN_STATUS[slug] = {"state": "error", "msg": short(str(e), 200)}
    finally:
        _RESERVING.discard(slug)  # PNGが実在するので以後は実在チェックが衝突を防ぐ
    # 追いテーマ生成はしない: 主レーン成功時は__テーマ版が既に在り、失敗時はCodex不調なので
    # 再試行は900秒の無駄玉になる（フォールバック絵は次のREADYテーマ再生成の機会に揃える）。


def gen_sprite_async(slug, label, pattern):
    """tools/assets_gen.py custom を裏で実行（server/はstdlib縛りのためsubprocess経由）"""
    GEN_STATUS[slug] = {"state": "generating", "msg": ""}
    threading.Thread(target=_generate_sprite, args=(slug, label, pattern), daemon=True).start()


def _decode_photo_b64(value):
    """data URL または素の base64 を、許可画像のバイト列へ検証付きで戻す。"""
    if not isinstance(value, str) or not value:
        return False, "写真データが空です"
    encoded = value
    if value.startswith("data:"):
        match = re.fullmatch(r"data:image/(?:png|jpeg);base64,(.*)", value,
                             flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return False, "写真データの形式が不正です"
        encoded = match.group(1)
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return False, "写真データをbase64として読めません"
    if len(raw) > _PHOTO_MAX_BYTES:
        return False, "写真は5MBまでです"
    if not (raw.startswith(b"\x89PNG") or raw.startswith(b"\xff\xd8")):
        return False, "PNGまたはJPEGの写真を選んでください"
    return True, raw


def _custom_sprite_stem(sprite):
    """config の sprite が runtime custom なら安全な stem、それ以外は空文字を返す。"""
    if (not isinstance(sprite, str) or sprite in _STANDARD_SPRITES
            or not re.fullmatch(r"[a-z0-9_]+\.png", sprite) or "__" in sprite):
        return ""
    return Path(sprite).stem


def _photo_output_names(slug):
    return [f"{slug}.png", f"{slug}_walk.png"]


def _run_photo_generation(slug, label, pattern, photo):
    """写真参照の既定スタイルを生成し、写真を消してから最終状態を公開する。"""
    final_status = {"state": "error", "msg": "キャラ生成に失敗しました"}
    try:
        fake_marker = os.environ.get("OFFICE_FAKE_GEN")
        if fake_marker:
            # verify/unittest 専用注入口。実ジェネレータを一切起動せず既定2成果物を再現する。
            ASSETS.mkdir(parents=True, exist_ok=True)
            output_names = _photo_output_names(slug)
            Path(fake_marker).write_text("\n".join(output_names) + "\n", encoding="utf-8")
            for name in output_names:
                (ASSETS / name).write_bytes(_TINY_PNG)
            generated_ok = True
        else:
            generated_ok = False
            # R23.5画風追随: 主=READY先頭テーマ（現行画風・__テーマ版→無印へ昇格）。
            # 未READY/失敗時は旧vintageレーン（無印直書き）へフォールバック。
            themes = _ready_themes()
            lanes = [(theme, True) for theme in themes[:1]] + [("vintage", False)]
            for theme, promote in lanes:
                try:
                    with _CUSTOM_GEN_LOCK:
                        generated = subprocess.run(
                            [sys.executable, str(ROOT / "tools" / "theme_gen.py"),
                             theme, "custom", slug, label, "--photo-ref", str(photo)],
                            capture_output=True, text=True, timeout=900)
                    produced = generated.returncode == 0 and (not promote or _promote_theme_custom(slug, theme))
                    expected = (ASSETS / f"{slug}.png", ASSETS / f"{slug}_walk.png")
                    generated_ok = produced and all(p.is_file() for p in expected)
                    if generated_ok:
                        break
                    # 失敗理由はローカルログのみ（API状態には載せない=写真パス露出防止）
                    print(f"[photo] {theme} 生成失敗: {short(generated.stderr or generated.stdout, 200)}")
                except (OSError, subprocess.TimeoutExpired) as e:
                    generated_ok = False
                    print(f"[photo] {theme} 生成例外: {short(e, 120)}")

        if generated_ok:
            _set_sprite(pattern, f"{slug}.png")
            final_status = {"state": "done", "msg": ""}
        else:
            final_status = {"state": "error", "msg": "キャラ生成に失敗しました"}
    except Exception:
        # 生成コマンドの詳細（写真の一時パスを含み得る）は API 状態へ載せない。
        final_status = {"state": "error", "msg": "キャラ生成に失敗しました"}
    finally:
        cleanup_failed = False
        try:
            photo.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
        finally:
            # プライバシー掟: 成否に関係なく写真は必ずここで削除を試み、
            # 写真データ・一時パスを office_json / relay payload へ決して載せない。
            _RESERVING.discard(slug)
        if cleanup_failed:
            final_status = {"state": "error", "msg": "一時写真を削除できませんでした"}
        # done を観測した時点で upload_tmp が空であることを保証するため、削除後にのみ更新する。
        GEN_STATUS[slug] = final_status


def upload_sprite_photo(cwd, image_b64):
    """既存プロジェクト用の写真を私有一時ファイルへ保存し、生成スレッドを起動する。"""
    if not isinstance(cwd, str) or not cwd:
        return False, "cwd が不正です", {}
    config = load_config()
    pattern = project_config_key(cwd, config)
    if pattern is None:
        # set_sprite と同じく /var→/private/var 等の symlink 差を realpath で吸収する。
        pattern = project_config_key(os.path.realpath(cwd), config)
    if pattern is None:
        return False, "プロジェクトが見つかりません", {}

    valid, decoded = _decode_photo_b64(image_b64)
    if not valid:
        return False, decoded, {}

    meta = config.get("projects", {}).get(pattern, {})
    if not isinstance(meta, dict):
        return False, "プロジェクト設定が不正です", {}
    label = short(meta.get("name") or Path(cwd).name or "オリジナルキャラ", 30)
    with _lock:
        slug = _custom_sprite_stem(meta.get("sprite", ""))
        if not slug:
            # 標準スプライトは共有物なので、部署名を基に custom 用の新規 stem を予約する。
            slug = sprite_slug(pattern, label)
        if slug in _RESERVING:
            return False, "このキャラは生成中です", {}
        _RESERVING.add(slug)

    photo_dir = DATA / "upload_tmp"
    photo = photo_dir / f"{slug}_photo.png"
    try:
        photo_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(photo_dir, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(photo), flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                fd = -1
                f.write(decoded)
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError:
        _RESERVING.discard(slug)
        try:
            photo.unlink(missing_ok=True)
        except OSError:
            pass
        return False, "写真を一時保存できませんでした", {}

    GEN_STATUS[slug] = {"state": "generating"}
    try:
        threading.Thread(target=_run_photo_generation,
                         args=(slug, label, pattern, photo), daemon=True).start()
    except Exception:
        _RESERVING.discard(slug)
        try:
            photo.unlink(missing_ok=True)
        except OSError:
            pass
        GEN_STATUS[slug] = {"state": "error", "msg": "生成処理を開始できませんでした"}
        return False, "生成処理を開始できませんでした", {}
    return True, "生成を開始しました", {"slug": slug, "genStarted": True}


def launch_claude(path):
    """Terminal.app 新規ウィンドウで claude を起動。OFFICE_FAKE_LAUNCH はテスト用マーカー"""
    fake = os.environ.get("OFFICE_FAKE_LAUNCH")
    if fake:
        Path(fake).write_text(path, encoding="utf-8")
        return True
    esc = path.replace("\\", "\\\\").replace('"', '\\"')
    script = ('tell application "Terminal"\n'
              '  activate\n'
              f'  do script "cd " & quoted form of "{esc}" & " && claude"\n'
              'end tell')
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ── R53: ロボ→実ターミナルジャンプ（session → 実プロセス → ホストアプリを前面へ） ──
# claude CLI プロセスはセッションIDの痕跡を持たない（transcriptのfdも掴まない・実測）。
# 対応付けは「cwd一致 ＋ プロセス起動時刻とtranscript先頭行時刻の近さ」のヒューリスティック。
# --resume 再開セッションは時刻が離れるが cwd 一致の最近接へ縮退（同cwd複数でも実用上十分）。

def _claude_procs():
    """tty付きの claude CLI プロセス一覧 [{pid,tty,started,cwd}]（bg-pty-host等のデーモン除外）。"""
    try:
        r = subprocess.run(["ps", "-eo", "pid=,tty=,lstart=,command="],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    procs = []
    for ln in r.stdout.splitlines():
        parts = ln.split(None, 7)          # pid tty [lstart=5語] command...
        if len(parts) < 8 or not parts[1].startswith("ttys"):
            continue
        cmd = parts[7]
        base = cmd.split()[0] if cmd else ""
        if base != "claude" and not base.endswith("/claude"):
            continue
        if "bg-pty-host" in cmd or "bg-spare" in cmd:
            continue
        try:
            started = time.mktime(time.strptime(" ".join(parts[2:7]),
                                                "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            started = 0.0
        cwd = ""
        try:
            out = subprocess.run(["lsof", "-a", "-p", parts[0], "-d", "cwd", "-Fn"],
                                 capture_output=True, text=True, timeout=10).stdout
            for l2 in out.splitlines():
                if l2.startswith("n"):
                    cwd = l2[1:]
        except (OSError, subprocess.TimeoutExpired):
            pass
        procs.append({"pid": parts[0], "tty": parts[1], "started": started, "cwd": cwd})
    return procs


def _match_proc(cwd, first_ts, procs):
    """cwd一致の中から起動時刻が transcript 先頭に最も近いものを選ぶ（純関数・テスト可能）。"""
    cand = [p for p in procs if p.get("cwd") and nfc(p["cwd"]) == nfc(cwd or "")]
    if not cand:
        return None
    if first_ts:
        return min(cand, key=lambda p: abs((p.get("started") or 0) - first_ts))
    return max(cand, key=lambda p: p.get("started") or 0)


def _session_meta(session):
    """(cwd, transcript先頭行のepoch秒|None) を返す。scanキャッシュ→transcript直読の順。"""
    cwd = ""
    data = _cache.get("data") or {}
    for e in (data.get("employees") or []):
        if e.get("session") == session:
            cwd = e.get("cwd") or ""
            break
    first_ts = None
    try:
        for d in PROJECTS.iterdir():
            f = d / f"{session}.jsonl"
            if f.is_file():
                with f.open(encoding="utf-8", errors="replace") as fh:
                    for ln in fh:
                        try:
                            head = json.loads(ln)
                        except json.JSONDecodeError:
                            continue
                        cwd = cwd or head.get("cwd") or ""
                        ts = head.get("timestamp")
                        if isinstance(ts, str):
                            try:
                                first_ts = time.mktime(time.strptime(
                                    ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                            except ValueError:
                                pass
                        break
                break
    except OSError:
        pass
    return cwd, first_ts


def _host_app(pid):
    """親プロセスを辿ってホストアプリ（.appバンドル）のパスを返す（Zed/Terminal/iTerm等）。"""
    for _ in range(8):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
        parts = out.split(None, 1)
        if len(parts) < 2:
            return ""
        ppid, comm = parts[0], parts[1]
        if ".app/" in comm:
            return comm.split(".app/")[0] + ".app"
        if ppid in ("0", "1"):
            return ""
        pid = ppid
    return ""


def focus_terminal(session):
    """そのセッションが動いている実ターミナルを前面へ。(ok, message) を返す。
    Terminal.app はタブ精度（tabのtty一致）・その他のホストアプリはアプリ前面化。
    OFFICE_FAKE_FOCUS はテスト用マーカー（osascript/psを実行しない）。"""
    fake = os.environ.get("OFFICE_FAKE_FOCUS")
    if fake:
        Path(fake).write_text(json.dumps({"session": session}), encoding="utf-8")
        return True, "FAKE"
    cwd, first_ts = _session_meta(session)
    if not cwd:
        return False, L_now("このセッションの作業フォルダを特定できません",
                            "couldn't resolve this session's working directory")
    proc = _match_proc(cwd, first_ts, _claude_procs())
    if not proc:
        return False, L_now("実行中のターミナルが見つかりません（セッション終了済み?）",
                            "no running terminal found (session may have exited)")
    app = _host_app(proc["pid"])
    app_name = Path(app).stem if app else ""
    try:
        if app_name == "Terminal":
            script = ('tell application "Terminal"\n'
                      '  activate\n'
                      '  repeat with w in windows\n'
                      '    repeat with t in tabs of w\n'
                      f'      if (tty of t) is "/dev/{proc["tty"]}" then\n'
                      '        set selected of t to true\n'
                      '        set index of w to 1\n'
                      '        return "ok"\n'
                      '      end if\n'
                      '    end repeat\n'
                      '  end repeat\n'
                      'end tell')
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=15)
        elif app:
            subprocess.run(["open", app], capture_output=True, timeout=15)
        else:
            return False, L_now("ホストアプリを特定できません", "couldn't find the host app")
    except (OSError, subprocess.TimeoutExpired):
        return False, L_now("前面化に失敗しました", "failed to bring the app forward")
    return True, (app_name or "Terminal")


def add_project(path, name, role, gen_sprite=False, launch=False):
    """office_config.json へ登録し、キャラ生成とclaude起動をキックする"""
    if not isinstance(path, str) or not path.strip():
        return False, "フォルダが存在しません", {}
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False, "パスが不正です", {}
    if not p.is_dir():
        return False, "フォルダが存在しません", {}
    # ホーム自体や親フォルダは cwd 部分マッチで全社員を乗っ取るので拒否
    home = Path.home().resolve()
    if p == home or p in home.parents:
        return False, "フォルダが広すぎます（ホームや親フォルダは登録できません）", {}
    name = short(str(name) if name else p.name, 30)
    role = short(str(role) if role else "", 30)
    if not name:
        return False, "名前が空です", {}
    pattern = project_pattern(str(p))
    cf = config_file()
    # read-modify-write を _lock+flock 内で原子的に（キャラ生成完了スレッドの _set_sprite・
    # P4の daemon/dev 併走プロセスとの lost update 防止）
    with _lock, _file_flock(config_file()):
        try:
            cfg = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {"projects": {}}
        except (OSError, json.JSONDecodeError):
            return False, "office_config.json が読めません（壊れている可能性・手動確認を）", {}
        projects = cfg.get("projects", {})
        existing = pattern in projects
        slug = ""
        if existing:
            entry = dict(projects[pattern])
            entry["name"] = name
            if role:
                entry["role"] = role
            projects[pattern] = entry
            cfg["projects"] = projects
        else:
            entry = {"name": name, "role": role}
            # 先頭に挿入＝広いパターン(例: Downloads/works)より先にマッチさせる
            cfg["projects"] = {pattern: entry, **projects}
            if gen_sprite:
                slug = sprite_slug(pattern, p.name)  # _RESERVING を見るのでロック内で確定
                _RESERVING.add(slug)
        _write_config(cfg)
        _cache["t"] = 0.0
    if slug:
        gen_sprite_async(slug, f"{name} {p.name}", pattern)
    launched = launch_claude(str(p)) if launch else False
    return True, "登録しました", {
        "pattern": pattern, "existing": existing, "name": name, "slug": slug,
        "genStarted": bool(slug), "launched": launched,
    }


# ---- 📱 スマホ連携（P3: ペアリング＋HMAC署名） ---------------------------
# 脅威モデル: 中継Worker の共有Bearer は「輸送」専用（DO到達資格）で指示を認可しない。
# 指示の真正性は relay_agent が Mac 上で per-device HMAC-SHA256 を検証して担保する。
# デバイス秘密 S(256bit) はここでローカル生成し ~/.claude/office_devices.json(600) と
# スマホの localStorage にしか存在しない（QR/リンクの光路以外でネットを流れない）。
# ゆえに Bearer が漏れても S が無い限り compare_digest を通る sig を作れず＝指示は偽造不可。
_SIG_LABEL = "aioffice-instruct"     # ドメイン分離ラベル（他用途のHMACと衝突させない）
_SIG_V = 1                           # 署名プロトコルのバージョン
DEVICE_TTL = 30 * 86400              # デバイス有効期限（30日）


def load_devices():
    try:
        d = json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("devices"), dict):
            return d
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "devices": {}}


def save_devices(d):
    """台帳を atomic 保存。secret 平文のため tmp を **書き込み前に 0600 で作る**
    （デフォルトumaskで一瞬 0644 になる窓・.tmp 残置の world-readable を無くす）。"""
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_FILE.with_name(DEVICES_FILE.name + ".tmp")
    body = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    tmp.replace(DEVICES_FILE)   # replace は tmp の 0600 を引き継ぐ（最終ファイルに 0644 窓ができない）


def new_device(label):
    """新しいスマホ端末を発行（device_id＋256bit秘密＋30日TTL）。台帳へ atomic 追記。"""
    label = short(str(label) if label else "スマホ", 40)
    now = int(time.time())
    device_id = "d_" + secrets.token_hex(6)          # d_ + 12 hex
    secret = secrets.token_hex(32)                   # 64 hex = 256bit
    with _lock:
        d = load_devices()
        d.setdefault("devices", {})[device_id] = {
            "secret": secret, "label": label, "created": now,
            "expires": now + DEVICE_TTL, "revoked": False, "last_used": 0,
        }
        save_devices(d)
    return {"device_id": device_id, "secret": secret,
            "label": label, "created": now, "expires": now + DEVICE_TTL}


def revoke_device(device_id):
    with _lock:
        d = load_devices()
        rec = d.get("devices", {}).get(device_id)
        if not rec:
            return False
        rec["revoked"] = True
        save_devices(d)
    return True


def list_devices():
    """secret を伏せたデバイス一覧（画面表示用）。"""
    d = load_devices()
    out = [{
        "device_id": did, "label": rec.get("label", ""),
        "created": rec.get("created", 0), "expires": rec.get("expires", 0),
        "revoked": bool(rec.get("revoked")), "last_used": rec.get("last_used", 0),
    } for did, rec in d.get("devices", {}).items()]
    out.sort(key=lambda x: x["created"], reverse=True)
    return out


def relay_config():
    """~/.claude/office_relay.json から中継URL＋輸送トークンを読む（無ければ空）。"""
    p = _HOME / ".claude" / "office_relay.json"
    try:
        c = json.loads(p.read_text(encoding="utf-8"))
        return (c.get("url") or "").rstrip("/"), c.get("token") or ""
    except (OSError, json.JSONDecodeError):
        return "", ""


def pair_url(device):
    """QR/リンクに載せる /app ディープリンク。中継未設定なら空文字（トークンはhex＝URL安全）。"""
    url, token = relay_config()
    if not url or not token:
        return ""
    return (f"{url}/app#v={_SIG_V}&d={device['device_id']}"
            f"&s={device['secret']}&t={token}&e={device['expires']}")


def pair_qr_svg(pu):
    """pair_url を QR SVG に（tools/qr_gen.py=vendored segno を subprocess・payloadはstdinで渡す＝
    ps に secret を晒さない）。office_server の stdlib純度を保つため import せず subprocess。失敗時は空文字。"""
    if not pu:
        return ""
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "qr_gen.py")],
                           input=pu, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.startswith("<svg"):
            return r.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _canonical(device_id, session, text, ts, nonce):
    """署名対象の正準バイト列。textは生UTF-8のsha256hexに畳む（可変長の区切り注入を封じ、
    NFC正規化しない＝JS TextEncoder と Python encode('utf-8') が同一バイト）。"""
    th = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return "\n".join([_SIG_LABEL, f"v{_SIG_V}", device_id, session,
                      str(ts), nonce, th]).encode("utf-8")


def sign_envelope(secret_hex, device_id, session, text, ts, nonce, v=_SIG_V):
    """署名封筒を生成（テスト/relay_e2e/スマホJS と同一計算の正本）。"""
    sig = hmac.new(bytes.fromhex(secret_hex),
                   _canonical(device_id, session, text, ts, nonce),
                   hashlib.sha256).hexdigest()
    return {"v": v, "device_id": device_id, "session": session, "text": text,
            "ts": ts, "nonce": nonce, "alg": "HS256", "sig": sig}


def verify_envelope(env, devices, now, window):
    """署名封筒を検証する純関数（nonce/rate は触らない＝relay_agent の責務）。
    戻り値 (ok, reason, session, text)。ok=False の reason は棄却理由（ログ/テスト用）。"""
    if not isinstance(env, dict):
        return False, "bad-envelope", "", ""
    if env.get("v") != _SIG_V:
        return False, "bad-version", "", ""
    if env.get("alg") != "HS256":
        return False, "bad-alg", "", ""
    # 非文字列フィールドは空扱い（re.fullmatch に非strを渡すと TypeError で落ちる＝1封筒で常駐死）
    d_id = env.get("device_id") if isinstance(env.get("device_id"), str) else ""
    sess = env.get("session") if isinstance(env.get("session"), str) else ""
    nonce = env.get("nonce") if isinstance(env.get("nonce"), str) else ""
    sig = env.get("sig") if isinstance(env.get("sig"), str) else ""
    ts = env.get("ts")
    text = env.get("text", "")
    # canonical 構築の前に形式検証（\n非包含を保証＝区切り注入の封じ）
    if not re.fullmatch(r"d_[0-9a-f]{12}", d_id):
        return False, "bad-device-id", "", ""
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", sess):
        return False, "bad-session", "", ""
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        return False, "bad-nonce", "", ""
    if not re.fullmatch(r"[0-9a-f]{64}", sig):
        return False, "bad-sig-fmt", "", ""
    if not isinstance(ts, int) or isinstance(ts, bool):
        return False, "bad-ts", "", ""
    if not isinstance(text, str) or not text or len(text) > 4000:
        return False, "bad-text", "", ""
    rec = devices.get("devices", {}).get(d_id)
    if not isinstance(rec, dict):
        return False, "unknown-device", "", ""
    if rec.get("revoked"):
        return False, "revoked", "", ""
    # 台帳レコードが手編集/移行不整合で壊れていても純関数契約を守る（例外を出さず理由を返す＝
    # 1レコードの不正が relay_agent の配達バッチ全体を巻き込まない）
    try:
        expires = int(rec.get("expires", 0))
    except (TypeError, ValueError):
        return False, "bad-record", "", ""
    if now > expires:
        return False, "expired", "", ""
    if abs(now - ts) > window:
        return False, "stale-ts", "", ""
    try:
        secret_key = bytes.fromhex(rec["secret"])
    except (KeyError, TypeError, ValueError):
        return False, "bad-record", "", ""
    expect = hmac.new(secret_key, _canonical(d_id, sess, text, ts, nonce),
                      hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return False, "bad-sig", "", ""
    return True, "ok", sess, text


# 最小静的ページ（旧: ページ全文の複製を内蔵→ドリフト源のため2026-07-14に廃止。
# UIの正本は ui/boot.html＋ui/iso/**）
PAGE_FALLBACK = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Office — UI読込エラー</title>
<style>
body{font-family:system-ui,sans-serif;max-width:42rem;margin:4rem auto;padding:0 1rem;line-height:1.7}
code{background:#f2f2f2;padding:.1rem .3rem}
</style>
</head>
<body>
<main>
<h1>ui/boot.html を読み込めませんでした</h1>
<p>対処:</p>
<ol>
<li>リポジトリの <code>ui/</code> ディレクトリが存在するか確認</li>
<li>常駐（P4）導入済みなら <code>bash macapp/install.sh</code> を再実行して <code>app/</code> を再デプロイ</li>
</ol>
<p>API (/api/office) は稼働しています</p>
</main>
</body>
</html>
"""


# DNSリバインディング/CSRF対策: 受け付けるHost/Originはループバックのみ
_LOOPBACK_HOST = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\]|::1)(:\d+)?$")
_LOOPBACK_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _deny(self, code=403, msg="forbidden"):
        body = json.dumps({"ok": False, "error": msg}, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _host_ok(self):
        # Hostがループバック以外＝別名でこのサーバーに解決させた疑い（DNSリバインディング）
        host = self.headers.get("Host", "")
        return not host or bool(_LOOPBACK_HOST.match(host))

    def _csrf_ok(self):
        # 状態変更POSTは X-Office-Local 必須。カスタムヘッダはCORSプリフライトを誘発し、
        # このサーバーはOPTIONSを許可しない → 別オリジンのWebページからは投げられない。
        if self.headers.get("X-Office-Local") != "1":
            return False
        origin = self.headers.get("Origin")
        return not origin or bool(_LOOPBACK_ORIGIN.match(origin))

    def do_GET(self):
        if not self._host_ok():
            return self._deny(403, "invalid host")
        if self.path.startswith("/api/office"):
            self._send(200, json.dumps(office_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/layout":
            # 座標だけなので /api/office と同格。GETはCSRFヘッダ不要。
            self._send(200, json.dumps(layout_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/external/openclaw":
            # 外部接続の器はローカルUI専用。office_jsonへは混ぜない。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            if not edition_features(edition()).get("openclaw"):
                return self._deny(403, "openclaw is not part of this edition")
            self._send(200, json.dumps(external_openclaw_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/project/gen_status"):
            self._send(200, json.dumps(GEN_STATUS, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/pair/list"):
            # 端末一覧（secretは含まないが label/id を晒すので）別オリジンGETを弾く。
            # UIの api() は GET にも X-Office-Local を付与するので同一オリジンは通る。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps({"devices": list_devices()}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/keys/status"):
            # 接続情報とmaskedキーもローカルUI専用。pair/list と同じCSRF境界で守る。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(keys_status(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/status_board"):
            # ローカル利用状況（プラン残・トークン消費）。secretではないが端末外に出さない値なので
            # pair/list と同様に別オリジンGETを弾く（UIの api() は X-Office-Local を付与）。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            if not edition_features(edition(), license_state()).get("costDash"):
                return self._deny(403, L_now("コストダッシュボードはPro機能です（🔑ライセンス登録から）",
                                             "The cost dashboard is a Pro feature (register a license via 🧾)"))
            self._send(200, json.dumps(status_board.status_board_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/license/status"):
            # ライセンス状態はローカルUI専用（holder等を別オリジンへ出さない）。sigは返さない。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            ed = edition()
            lic = license_state()
            self._send(200, json.dumps({"edition": ed, "license": lic,
                                        "features": edition_features(ed, lic)},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/projects":
            # cwd を含むローカルパス一覧なので、別オリジンGETには公開しない。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(projects_index.projects_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] in ("/", "/index.html"):  # ?style= / ?demo= 等のクエリを許容
            # R52: 旧UI（office_page.html・?ui=legacy）はP7計画どおり削除済み＝常に新UI。
            page_f = ROOT / "ui" / "boot.html"
            try:
                page = page_f.read_text(encoding="utf-8")
            except OSError:
                page = PAGE_FALLBACK
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/ui/"):
            # 新UIのESM/CSS/フォント/three.js。ui_asset が ui/ 配下へ閉じ込める。
            f = ui_asset(self.path.split("?", 1)[0][len("/ui/"):])
            if f:
                self._send(200, f.read_bytes(), _UI_MIME[f.suffix])
            else:
                self.send_response(404)
                self.end_headers()
        elif self.path.startswith("/assets/"):
            name = os.path.basename(self.path.split("?")[0])
            f = ASSETS / name
            if f.is_file() and f.suffix == ".png":
                self._send(200, f.read_bytes(), "image/png")
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._host_ok():
            return self._deny(403, "invalid host")
        if not self._csrf_ok():
            return self._deny(403, "cross-site request blocked")
        route = self.path.split("?", 1)[0]
        # R42.2 有料機能ゲート（強制は全部Mac側・Workerは無強制の掟）。
        if route == "/api/pair/new" or route.startswith("/api/status_board"):
            feats = edition_features(edition(), license_state())
            if route == "/api/pair/new" and not feats.get("relayPwa"):
                return self._deny(403, L_now("スマホ連携はPro機能です（🔑ライセンス登録から）",
                                             "Phone pairing is a Pro feature (register a license via 🧾)"))
            if route.startswith("/api/status_board") and not feats.get("costDash"):
                return self._deny(403, L_now("コストダッシュボードはPro機能です（🔑ライセンス登録から）",
                                             "The cost dashboard is a Pro feature (register a license via 🧾)"))
        try:
            n = int(self.headers.get("Content-Length", 0))
            # base64化で約4/3になる写真JSONだけ8MBまで読む。他のPOSTは従来の100KB上限を維持する。
            body_limit = _PHOTO_BODY_MAX_BYTES if route == "/api/sprite/upload" else 100_000
            data = json.loads(self.rfile.read(min(max(n, 0), body_limit)).decode("utf-8")) if n else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        extra = {}
        if self.path.startswith("/api/instruct"):
            ok, msg = post_instruction(data.get("session", ""), data.get("text", ""))
        elif route == "/api/terminal/focus":
            # R53: ロボ→実ターミナルジャンプ（loopback+CSRF配下・osascriptはローカル操作のみ）
            sess = data.get("session", "")
            if not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", sess or ""):
                ok, msg = False, "session id が不正です"
            elif sess.startswith("oc-"):
                ok, msg = False, L_now("外部エージェントにローカルターミナルはありません",
                                       "external agents have no local terminal")
            else:
                ok, msg = focus_terminal(sess)
                if ok:
                    extra = {"app": msg}
        elif route == "/api/layout":
            if "roomPins" in data and "layout" not in data:
                ok, msg = set_room_pins(data["roomPins"])
            elif (isinstance(data.get("layout"), dict) and
                  set(data["layout"]) == {"roomPins"}):
                ok, msg = set_room_pins(data["layout"]["roomPins"])
            elif "layout" not in data:
                ok, msg = False, "layout またはroomPinsが必要です"
            else:
                ok, msg = set_layout(data["layout"])
        elif self.path.startswith("/api/project/pick"):
            ok, msg = pick_folder()
            if ok:
                extra = {"path": msg, "suggest": Path(msg).name}
        elif self.path.startswith("/api/project/new"):
            ok, msg, extra = add_project(
                data.get("path", ""), data.get("name", ""), data.get("role", ""),
                gen_sprite=bool(data.get("genSprite")), launch=bool(data.get("launch")))
        elif self.path == "/api/projects/launch":
            path = data.get("path")
            try:
                valid_path = isinstance(path, str) and bool(path.strip()) and Path(path).is_dir()
            except (OSError, ValueError):
                valid_path = False
            if not valid_path:
                ok, msg = False, "フォルダが存在しません"
            else:
                ok = launch_claude(path)
                msg = "Terminalを起動できませんでした"
        elif route == "/api/sprite/upload":
            ok, msg, extra = upload_sprite_photo(
                data.get("cwd", ""), data.get("imageB64", ""))
        elif self.path.startswith("/api/sprite/set"):
            ok, msg = set_sprite(data.get("cwd", ""), data.get("sprite", ""))
        elif self.path.startswith("/api/pair/new"):
            # secret を返すのは loopback+CSRF 済みのローカルUIのみ（_host_ok/_csrf_ok 配下）
            dev = new_device(data.get("label", ""))
            pu = pair_url(dev)
            ok, msg = True, "発行しました"
            extra = {**dev, "pairUrl": pu, "relayConfigured": bool(pu), "qrSvg": pair_qr_svg(pu)}
        elif self.path.startswith("/api/pair/revoke"):
            ok = revoke_device(data.get("device_id", ""))
            msg = "失効しました" if ok else "端末が見つかりません"
        elif self.path.startswith("/api/keys/set"):
            ok, msg = set_office_key(data.get("name"), data.get("value"))
        elif self.path.startswith("/api/lang"):
            ok, msg = set_lang(data.get("lang"))
            if ok:
                extra = {"lang": msg}
        elif self.path.startswith("/api/status_board/spend"):
            ok, msg = status_board.spend_apply(data)
        elif self.path.startswith("/api/status_board/fx"):
            ok, msg = status_board.fx_apply(data)
        elif self.path.startswith("/api/status_board/budget"):
            ok, msg = status_board.budget_apply(data)      # R63: 手動予算
        elif self.path.startswith("/api/status_board/ledger"):
            ok, msg = status_board.ledger_apply(data)
        elif self.path.startswith("/api/license/set"):
            ok, msg, extra = apply_license(data.get("license"))
            if ok:
                extra = {**extra, "message": msg}
        else:
            self.send_response(404)
            self.end_headers()
            return
        if ok and route == "/api/layout":
            current = layout_json()
            extra = {"layout": current["layout"], "roomPins": current["roomPins"]}
        body = json.dumps({"ok": ok, **(extra if ok else {"error": msg})},
                          ensure_ascii=False).encode("utf-8")
        self._send(200 if ok else 400, body, "application/json; charset=utf-8")

    def log_message(self, *args):
        pass


class _TsWriter:
    """行頭に時刻を前置するライター（daemonログで再起動・エラーの「いつ」を追えるようにする）。
    print() は本文と改行を別々に write するため、行単位でバッファして完全な行にだけ前置する。"""

    def __init__(self, stream):
        self._s = stream
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._s.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
        return len(text)

    def flush(self):
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


def _rotate_daemon_log(name, limit=5 * 1024 * 1024):
    """起動時ローテ（copy+truncate）。launchd が StandardOutPath の fd を O_APPEND で
    掴んだままなので rename は不可＝ .old へ写して元を truncate する（次の書込は先頭から）。"""
    d = os.environ.get("OFFICE_DATA")
    if not d:
        return
    p = Path(d).parent / "logs" / name
    try:
        if p.is_file() and p.stat().st_size > limit:
            p.with_name(p.name + ".old").write_bytes(p.read_bytes())
            os.truncate(p, 0)
    except OSError:
        pass


def _install_ts_logging(log_name):
    """serve経路のみで呼ぶ（--dump のJSON出力・mcp_office の stdout純度を汚さない）。
    TTY（手動起動の対話ターミナル）では素通し＝daemon/リダイレクト時だけ有効。"""
    if sys.stdout.isatty():
        return
    _rotate_daemon_log(log_name)
    sys.stdout = _TsWriter(sys.stdout)
    sys.stderr = _TsWriter(sys.stderr)


# ── デスクトップ通知と日報（R50-P7d） ─────────────────────────────
DAILY_DIR = _HOME / ".claude" / "office_daily"
DAILY_HOUR = 18                      # この時刻以降の最初の巡回で日報を出す


def notify_mac(title, body):
    """macOS通知（fail-soft）。OFFICE_FAKE_NOTIFY はテスト注入口（ファイルへ追記）。"""
    fake = os.environ.get("OFFICE_FAKE_NOTIFY")
    if fake:
        try:
            with open(fake, "a", encoding="utf-8") as f:
                f.write(f"{title}\t{body}\n")
        except OSError:
            pass
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             'display notification "{}" with title "{}" sound name "Glass"'.format(
                 str(body).replace("\\", "").replace('"', "'")[:120],
                 str(title).replace("\\", "").replace('"', "'")[:60])],
            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass                          # 通知はベストエフォート（本流を殺さない）


def attention_diff(prev_ids, roster):
    """❗（質問/承認まち）のエッジ検出。新規に待ち始めたプロジェクトだけ
    [{disp, question, approvalMin}] で返す（R53.2: 通知本文に質問プレビューを載せるため。
    ローカルMacの通知＝transcript自体が手元にある前提なので本文露出はPWA Pushと別基準）。"""
    cur = {}
    for prj in roster or []:
        if prj.get("question") or (prj.get("approvalMin") or 0) > 0:
            cur[prj.get("projectId") or prj.get("session") or ""] = {
                "disp": prj.get("disp") or "?",
                "question": prj.get("question") or "",
                "approvalMin": prj.get("approvalMin") or 0,
            }
    new_ids = [pid for pid in cur if pid not in (prev_ids or set())]
    return [cur[pid] for pid in new_ids], set(cur)


def _attn_track(seen, roster, now_ts):
    """❗の滞在時間トラッキング（純関数・R54）。seen={projectId: 初見epoch} を更新し、
    解消した分の待たせ秒リストを返す。日報の「答えた❗・平均待たせ時間」の材料。"""
    cur = set()
    for prj in roster or []:
        if prj.get("question") or (prj.get("approvalMin") or 0) > 0:
            cur.add(prj.get("projectId") or prj.get("session") or "")
    new_seen = {pid: ts for pid, ts in (seen or {}).items() if pid in cur}
    for pid in cur:
        new_seen.setdefault(pid, now_ts)
    resolved = [max(0.0, now_ts - ts) for pid, ts in (seen or {}).items()
                if pid not in cur]
    return new_seen, resolved


def _append_daily_stats(resolved_secs, day):
    """DAILY_DIR/<day>.stats.json へ応答実績を積む（best-effort・watcherスレッド専用=競合なし）。"""
    if not resolved_secs:
        return
    p = DAILY_DIR / f"{day}.stats.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        d = {}
    d["answered"] = (d.get("answered") or 0) + len(resolved_secs)
    d["totalWaitSec"] = (d.get("totalWaitSec") or 0) + sum(resolved_secs)
    try:
        DAILY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _load_daily_stats(day):
    try:
        d = json.loads((DAILY_DIR / f"{day}.stats.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_daily_report(office, date_label):
    """今日のオフィス日報。office_json だけから作る（実データ以外は書かない）。"""
    roster = office.get("roster") or []
    hist = office.get("history") or []
    tasks = office.get("tasks") or {}
    done = tasks.get("completed") or 0
    top = max(roster, key=lambda prj: ((prj.get("work") or {}).get("counts") or {})
              .get("completed", 0), default=None)
    lines = [f"# AIオフィス日報 {date_label}", "",
             f"- 出勤プロジェクト: {len(roster)}",
             f"- 完了タスク: {done}",
             f"- 配達した指示: {len(hist)} 件（直近12件枠）"]
    if top and ((top.get("work") or {}).get("counts") or {}).get("completed"):
        lines.append(f"- 最多完了: {top.get('disp')} "
                     f"({top['work']['counts'].get('completed')}件)")
    body = f"完了{done}件 / {len(roster)}プロジェクト稼働"
    # R54: あなたの応答実績（watcherが計測した❗の解消数と平均待たせ時間）
    stats = _load_daily_stats(date_label)
    answered = stats.get("answered") or 0
    if answered:
        avg_min = round((stats.get("totalWaitSec") or 0) / answered / 60)
        lines.append(f"- 答えた❗: {answered} 件（平均待たせ {avg_min}分）")
        body += f" / ❗応答{answered}件"
    return "🏢 今日のAIオフィス", body, "\n".join(lines) + "\n"


def _watch_loop():
    """60秒ごとに❗エッジ検出→通知・18時以降に日報（日1回）。"""
    prev = set()
    seen = {}      # R54: ❗の滞在時間（projectId→初見epoch）＝日報の応答実績
    while True:
        time.sleep(60)
        try:
            office = office_json()
            seen, resolved = _attn_track(seen, office.get("roster"), time.time())
            if resolved:
                _append_daily_stats(resolved, datetime.now().strftime("%Y-%m-%d"))
            new_items, prev = attention_diff(prev, office.get("roster"))
            if new_items:
                top = new_items[0]
                extra = (L_now(f" ほか{len(new_items) - 1}件",
                               f" (+{len(new_items) - 1} more)")
                         if len(new_items) > 1 else "")
                # 質問はプレビューを載せる＝通知だけで「何を聞かれているか」が分かる
                what = (top["question"][:80] if top["question"]
                        else L_now(f"承認まち {top['approvalMin']}分",
                                   f"approval wait {top['approvalMin']}m"))
                notify_mac(f"❗ {top['disp']}{extra}", what)
            now_local = datetime.now()
            if now_local.hour >= DAILY_HOUR:
                day = now_local.strftime("%Y-%m-%d")
                out = DAILY_DIR / f"{day}.md"
                if not out.exists():
                    title, body, md = build_daily_report(office, day)
                    DAILY_DIR.mkdir(parents=True, exist_ok=True)
                    out.write_text(md, encoding="utf-8")
                    notify_mac(title, body)
        except Exception as e:                      # noqa: BLE001 - 監視は死なない
            print(f"⚠ watch_loop: {e}", file=sys.stderr, flush=True)


def start_watcher():
    """常駐時のみ起動。OFFICE_HOME注入（テスト/dev）では通知しない
    （fixtureの❗で本物の通知が鳴る事故を防ぐ）。OFFICE_FAKE_NOTIFY があれば起動。"""
    if os.environ.get("OFFICE_HOME") and not os.environ.get("OFFICE_FAKE_NOTIFY"):
        return
    threading.Thread(target=_watch_loop, daemon=True).start()


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--dump", action="store_true", help="スキャン結果JSONを出力して終了（デバッグ用）")
    args = ap.parse_args()
    if args.dump:
        print(json.dumps(scan_office(), ensure_ascii=False, indent=1))
        return
    _install_ts_logging("office.daemon.log")
    # ポート衝突は数回リトライしてから非ゼロ終了（launchd KeepAlive 下では即時無限ループに
    # なるのを Throttle と合わせて緩和しつつ、原因をログに1行残す）
    srv = None
    for attempt in range(3):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
            break
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
            print(f"⚠ ポート{args.port}が使用中 ({attempt + 1}/3)・2秒待って再試行", file=sys.stderr, flush=True)
            time.sleep(2)
    if srv is None:
        print(f"✗ ポート{args.port}が使用中のまま起動できません。dev起動が残っていれば "
              "`officectl.sh stop`、常駐(launchd)と衝突なら `launchctl bootout` を確認してください",
              file=sys.stderr, flush=True)
        sys.exit(1)
    start_watcher()
    print(f"🏢 AIオフィス起動: http://localhost:{args.port}  (Ctrl+Cで停止)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退勤しました。")


if __name__ == "__main__":
    main()
