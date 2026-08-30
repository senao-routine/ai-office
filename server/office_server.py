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
    import openclaw_source
    import office_actions
except ModuleNotFoundError:  # importlibでファイルを直接読む既存テスト向け
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import projects_index
        import status_board
        import openclaw_source
        import office_actions
    finally:
        del sys.path[0]

HERE = Path(__file__).resolve().parent          # AI Office/server
ROOT = HERE.parent                               # AI Office/


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
# R86-H: PermissionRequest フック（hooks/office-approval-wait.sh）の掲示板。
# <session>.json = いま実際に聞かれていること（フックが書き、フックが消す）
# <session>.reply.json = こちらからの回答（office_server / relay_agent が書き、フックが取る）
APPROVALS = _HOME / ".claude" / "office_approvals"
DEVICES_FILE = _HOME / ".claude" / "office_devices.json"   # P3: スマホ端末台帳(600・secret平文)
PORT = 4780
SHOW_WINDOW = int(os.environ.get("OFFICE_SHOW_WINDOW", 3 * 3600))  # 3時間以内に動いたセッションを「出勤中」として表示（R23.5退勤早期化・verify.shカナリア/works watchdogの窓と同期）
# R79: inbox の指示の寿命。表示窓と同じにして「画面に出ているのに届かない」を作らない。
INBOX_TTL = float(os.environ.get("OFFICE_INBOX_TTL", SHOW_WINDOW))
TAIL_BYTES = 80_000
TASK_TAIL_BYTES = 8 * 1024 * 1024   # R64: 初回窓。以降は増分読みなのでコストは初回のみ
CACHE_SEC = 2.0
DEFAULT_OFFICE_NAME = "AIオフィス"


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
    """scan外の応答経路（403文言等）用: その場で office_lang() を解決する。
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


AVATAR_MODES = ("session", "project")


def avatar_mode(config=None):
    """R86-A: アバターの粒度。session=1アバター=1セッション（**既定**・ユーザー裁定2026-08-26
    「worksで並走する3〜4セッションを個別に認識したい」）／project=1アバター=1プロジェクト
    （R50-P1の集約表示・旧既定）。env OFFICE_AVATAR_MODE はテスト/一時切替の注入口。不正値=session。"""
    raw = os.environ.get("OFFICE_AVATAR_MODE")
    if raw is None or not str(raw).strip():
        cfg = config if isinstance(config, dict) else load_config()
        raw = cfg.get("avatarMode")
    val = str(raw or "").strip().lower()
    return val if val in AVATAR_MODES else "session"


def edition_features(ed):
    """機能マトリクス＝表示分岐の単一集約点。UI/PWA はこの features だけを見る。

    2026-08-10 ライセンス廃止（ユーザー決定）: 署名鍵による機能ゲートを全廃し、
    **クローンした全員がスマホ連携・Push・遠隔実行・コスト表示まで使える**。
    価値は配布経路（note/Discord）＋更新＋コミュニティで作る（詳細= docs/収益化アーキテクチャ）。
    edition（claude/hybrid/openclaw）は「どの種類のエージェントを表示するか」の**表示モード**として
    のみ残す＝有料ゲートではない（検証器・鍵・/api/license/* は R85-2 で撤去済み）。"""
    return {
        "claudeSessions": ed in ("claude", "hybrid"),
        "openclaw": ed in ("openclaw", "hybrid"),
        "relayPwa": True,
        "push": True,
        "costDash": True,
    }


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


# R85-1: /rename のカスタムセッション名。transcript の {"type":"custom-title"} 行は
# ターン境界ごとに追記され「最後の1件が現在値」（実測で全サンプルEOFから20KB以内＝
# tail窓80KBで拾える）。稀に窓から流れても名前がチラつかないよう記憶する（skillsと同じ流儀・
# メモリのみ）。空文字の customTitle は「リネーム解除」として記憶ごと消す。
_TITLE_MEMORY = {}


def _remembered_title(session_key, seen, now, mtime):
    """tail窓で見た custom-title を記憶へ合流して現在値を返す。seen=None は「窓に無かった」。"""
    if seen is not None:
        if seen:
            _TITLE_MEMORY[session_key] = [seen, mtime]
        else:
            _TITLE_MEMORY.pop(session_key, None)
    entry = _TITLE_MEMORY.get(session_key)
    if entry:
        entry[1] = max(entry[1], mtime)   # 現役セッションを失効させない
    for key in [k for k, v in _TITLE_MEMORY.items() if now - v[1] > SHOW_WINDOW]:
        _TITLE_MEMORY.pop(key, None)      # 退勤セッションの残骸掃除（表示窓と同期）
    entry = _TITLE_MEMORY.get(session_key)
    return entry[0] if entry else ""


# R86-F: 権限モード（transcript の {"type":"permission-mode"} 行・last-wins）。
# custom-title と同型で、ターン境界ごとに追記される（実測: 行を持つ104本すべてで
# 最終行が EOF から max 32,830B＝TAIL_BYTES 80KB 窓に100%収まる）。
_PERM_MEMORY = {}


def _remembered_perm_mode(session_key, seen, now, mtime):
    """窓に permission-mode 行が無かった周でも直前の値を保つ。窓落ちで「不明」へ落ちると
    ❗の誤検知が復活する（＝R86-Fで直したバグの再発）ので、10行の保険を置く。"""
    if seen:
        _PERM_MEMORY[session_key] = [seen, mtime]
    entry = _PERM_MEMORY.get(session_key)
    if entry:
        entry[1] = max(entry[1], mtime)
    for key in [k for k, v in _PERM_MEMORY.items() if now - v[1] > SHOW_WINDOW]:
        _PERM_MEMORY.pop(key, None)
    entry = _PERM_MEMORY.get(session_key)
    return entry[0] if entry else ""


# ~/.claude/settings.json の permissions.ask（Bash(<接頭辞>:*)）。bypassPermissions でも
# **ask ルール該当だけは聞かれる**（公式docs）＝このMacで本物の承認まちが生まれる唯一の源。
# 実測でも裏づけ: ask該当Bashは p90=6,414秒（git push が1.8時間＝人間待ち）に対し、
# それ以外のBashは p90=14.8秒。ファイルは mtime でキャッシュ（毎スキャンでは読まない）。
_ASK_CACHE = {"mtime": -1.0, "rules": ()}
_ASK_RULE_RE = re.compile(r"^Bash\((.+?):\*\)$")
_CMD_SPLIT = re.compile(r"&&|\|\||;|\n|\|")


def _ask_rules():
    p = _HOME / ".claude" / "settings.json"
    try:
        m = p.stat().st_mtime
    except OSError:
        return ()
    if m != _ASK_CACHE["mtime"]:
        rules = []
        try:
            perm = json.loads(p.read_text(encoding="utf-8")).get("permissions") or {}
            for r in (perm.get("ask") or []):
                mm = _ASK_RULE_RE.match(str(r))
                if mm:
                    rules.append(mm.group(1))
        except (OSError, ValueError, TypeError):
            rules = []
        _ASK_CACHE.update(mtime=m, rules=tuple(rules))
    return _ASK_CACHE["rules"]


def can_prompt(mode, tool, tool_input=None):
    """このツール実行が**人間に聞く可能性があるか**（純関数）。

    R86-F: 従来は「最後が tool_use のまま75秒経過＝承認ダイアログ待ち」と推定していたが、
    それは実際には「ツールが走っている」だけのことが多い（実測: 直近7日の❗159回中95回=60%が誤報。
    ❗はPush/macOS通知/トリアージ最優先/日報のトリガなので実害が大きい）。
    透明性の原則: **聞かれ得ないツールは❗にしない**。
    モード不明（permission-mode 行を持たない7割のセッション・旧CLI・合成フィクスチャ）は
    **従来どおり「聞かれ得る」**＝安全側（default運用のユーザーの中核機能を黙って殺さない）。"""
    if tool == "ExitPlanMode":
        return True                    # プラン承認はどのモードでも聞かれる
    if mode != "bypassPermissions":
        return True                    # default/acceptEdits/plan/不明は従来どおり
    if tool != "Bash":
        return False                   # bypass下でBash以外が聞かれることはない
    cmd = str((tool_input or {}).get("command") or "")
    pref = _ask_rules()
    if not pref:
        return False
    return any(part.strip().lstrip("(").strip().startswith(p)
               for part in _CMD_SPLIT.split(cmd) for p in pref)


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


# ── R86-B: シート会話ビューア ─────────────────────────────────────
# 会話本文を返すのは GET /api/session/dialog（loopback+CSRF配下）だけ。
# office_json には載せない＝中継(push_status)へ乗る経路が構造的に存在しない（R83思想・
# redaction 非依存）。スマホ向けは E2EE 化（R87候補）まで出さない。
# R86-C: 深さは**列挙**（bytes/limit の直接指定は受けない）。サーバーが確保する最大量が
# 閉じた集合になり、UIのバグや将来の経路が巨大読みを要求する事故が構造的に起きない
# （遠隔実行の許可リストと同じ思想）。値は実測で決めた:
#   depth0 2MB … 4〜10ms・実セッション11本の中央値45件（300KBだと中央値6件＝会話が見えない）
#   depth1 8MB … ≤32ms・350本の95%以上がここで全会話を回収（TASK_TAIL_BYTES と同じ天井）
#   depth2 32MB … ≤75ms（現状の最大transcript 18.7MBを全部含む）＝終端
# 件数上限は安全弁（全量読みで400件超は350本中3本だけ）。実効的な制約はバイト側。
DIALOG_DEPTHS = ((2_000_000, 60), (8_000_000, 250), (32_000_000, 1000))
DIALOG_MAX_DEPTH = len(DIALOG_DEPTHS) - 1
DIALOG_TAIL_BYTES, DIALOG_LIMIT = DIALOG_DEPTHS[0]   # 旧名は depth0 の別名として温存
DIALOG_CLAMP = 400              # 1メッセージの最大文字数
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # `.`/`/` を排除＝トラバーサル不能
_DEPTH_RE = re.compile(r"^[0-9]{1,2}$")                 # 非数値/負値/小数は 400


def dialog_from_lines(lines, limit=DIALOG_LIMIT, clamp=DIALOG_CLAMP):
    """transcript行 → [{"role": "user"|"ai", "text": …}]（時系列・末尾limit件）。

    採用: user の文字列content（人間の指示）／user 配列の text ブロック／
          assistant の text ブロック連結／AskUserQuestion（❓質問+選択肢）。
    除外: thinking・tool_use（質問以外）・tool_result・`<`始まりの注入行
          （<local-command-stdout>/<system-reminder> 等。ただし <command-name> は
          「/x-post」の形で1行残す＝何を実行したかは会話の一部）・状態ブロック行・壊れ行。"""
    msgs = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("type") not in ("user", "assistant"):
            continue
        content = (d.get("message") or {}).get("content")
        role = "user" if d["type"] == "user" else "ai"
        if role == "user" and isinstance(content, str):
            text = content.strip()
            if not text:
                continue
            if text.startswith("<"):
                m = _COMMAND_NAME_RE.search(text)
                if not m:
                    continue
                text = "/" + m.group(1)
            msgs.append({"role": role, "text": short(text, clamp)})
            continue
        if not isinstance(content, list):
            continue
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and str(b.get("text", "")).strip():
                parts.append(str(b["text"]))
            elif (role == "ai" and b.get("type") == "tool_use"
                    and b.get("name") == "AskUserQuestion"):
                q = _question_text(b)
                if q:
                    parts.append("❓ " + q)
        if parts:
            msgs.append({"role": role, "text": short("\n".join(parts), clamp)})
    return msgs if limit is None else msgs[-limit:]


def dialog_page(lines, depth=0, truncated=False, clamp=DIALOG_CLAMP):
    """R86-C: 1ページ分の会話＋「まだ古いのが在るか」。純関数（I/Oは呼び出し側）。

    hasMore = truncated（バイト窓がファイル先頭に届いていない）or limitHit（件数上限で切った）。
    どちらでもない＝会話の先頭に到達済み＝「もっと見る」を出さない（押せないボタンを作らない）。
    depth は列挙内へ丸める（範囲外の弾きはルート側で400）。"""
    depth = max(0, min(int(depth), DIALOG_MAX_DEPTH))
    _nbytes, limit = DIALOG_DEPTHS[depth]
    msgs = dialog_from_lines(lines, limit=None, clamp=clamp)
    limit_hit = len(msgs) > limit
    return {
        "messages": msgs[-limit:] if limit_hit else msgs,
        "depth": depth,
        "maxDepth": DIALOG_MAX_DEPTH,
        "hasMore": bool(truncated or limit_hit),
        "windowTotal": len(msgs),   # ★この窓の中の総数（会話全体ではない）
    }


# R86-D: 受信待機（Stop hook の office-inbox-wait.sh が生きているか）の判定。
# **心拍方式**: hook が待機ループの毎周 pidfile を touch するので、その mtime の鮮度だけを見る。
# pid の生存確認（os.kill）は使わない — **PID再利用で嘘をつく**（実測: 素朴な判定で
# pidfile 7件中4件が Google Drive の crashpad や login の pid を「待機中」と誤報していた）。
# 心拍なら「今この瞬間そのループが回っている」ことの直接の証拠になり、再利用と無縁。
LISTEN_STALE = float(os.environ.get("OFFICE_LISTEN_STALE", 30))   # hook の interval 5秒の6倍
# 心拍を打たない**旧hook**がまだ待機しているセッション向けの移行フォールバック窓（旧LOOPS=2時間）。
# この窓内に限って pid の生存で補う。PID再利用の実害はここでは無視できる
# （実測の誤検出はいずれも 327〜617時間前の pidfile 由来で、2時間窓には1件も無かった）。
LISTEN_LEGACY = float(os.environ.get("OFFICE_LISTEN_LEGACY", 2 * 3600))


_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


ASK_GRACE = 3.0       # 掲示が出てすぐは❗にしない（下の理由）


def pending_approval(session, now=None, grace=0.0):
    """R86-H: いまそのセッションが**実際に**人間へ聞いていること（フックが publish した事実）。

    75秒ヒューリスティックは「止まっている＝聞かれているかもしれない」という推測にすぎず、
    ユーザーに『❗が出ているのに承認画面が無い』と言わせた原因だった。こちらは一次情報。
    掲示が無い/期限切れ/壊れている＝None（推測側の判定に委ねる＝旧セッションでも暗転しない）。
    """
    if not _SESSION_RE.match(session or ""):
        return None
    try:
        rec = json.loads((APPROVALS / f"{session}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("session") != session:
        return None
    now = now or time.time()
    if float(rec.get("deadline") or 0) < now:
        return None                      # フックが死んだ後の掲示は幽霊
    # ★grace: PermissionRequest フックは「結局そのまま許可される操作」でも一度は発火する
    # （信頼済みフォルダへの Write 等・実機で確認）。掲示は出るがコンマ数秒で消える＝
    # ❗を一瞬光らせてスマホへ誤プッシュする。数秒生き残った掲示だけを「本当に止まっている」と見る。
    if grace and now - float(rec.get("ts") or 0) < grace:
        return None
    kind = "question" if rec.get("kind") == "question" else "permission"
    opts = [str(o) for o in rec.get("options") or [] if isinstance(o, str)][:4]
    return {"tool": short(rec.get("tool") or "", 40), "kind": kind,
            "title": short(rec.get("title") or "", 200), "options": opts,
            "ts": float(rec.get("ts") or now)}


def write_approval_reply(session, behavior, message="", src="local"):
    """フックが待っている回答を置く。allow を通せるのは src="local"（Macの前の人間）だけ
    ＝この関数がどこから呼ばれても、中継経由が実行許可に化けない（最終判定はフック側）。"""
    if not _SESSION_RE.match(session or ""):
        raise ValueError("bad session")
    APPROVALS.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(APPROVALS, 0o700)
    except OSError:
        pass
    rec = {"session": session, "behavior": "allow" if behavior == "allow" else "deny",
           "message": short(message or "", 2000), "src": src, "ts": time.time()}
    tmp = APPROVALS / f".{session}.reply.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, APPROVALS / f"{session}.reply.json")
    return rec


def session_listening(session, now=None):
    """そのセッションが**いま指示を受け取れるか**。判定不能は False（フェイルセーフ＝
    「届く」と嘘をつかない）。False でも投函は無駄にならない: inbox に残り、そのセッションが
    次にターンを終えた瞬間に配達される（TTL内なら）。＝送信をブロックする理由にはならない。"""
    p = INBOX / f".{session}.pid"
    try:
        st = p.stat()
    except OSError:
        return False
    age = (now or time.time()) - st.st_mtime
    if age <= LISTEN_STALE:
        return True                 # 心拍（新hook）＝今この瞬間ループが回っている直接の証拠
    if age > LISTEN_LEGACY:
        return False
    try:                            # 移行期のみ: 旧hookは心拍を打たないので pid 生存で補う
        pid = int(p.read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)             # シグナル0＝存在確認のみ（何も送らない）
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _session_transcript(session):
    """sessionId → 実transcriptパス（PROJECTS配下の glob のみ＝閉じ込め）。無ければ None。
    呼び出し側が _SESSION_ID_RE を通してから呼ぶ（形式不正は400・不在は200+空）。"""
    try:
        for p in PROJECTS.glob(f"*/{session}.jsonl"):
            return p
    except OSError:
        pass
    return None


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
    custom_title = None            # R85-1: 窓に custom-title 行が無ければ None（記憶を使う）
    perm_mode = None               # R86-F: 窓に permission-mode 行が無ければ None＝不明
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if d.get("cwd"):
            cwd = d["cwd"]
        if d.get("gitBranch"):
            branch = d["gitBranch"]
        t = d.get("type")
        if t == "permission-mode":
            # R86-F: 現在の権限モード（last-wins）。❗の誤検知を止める唯一の一次情報。
            pm = d.get("permissionMode")
            if isinstance(pm, str) and pm:
                perm_mode = pm
        if t == "custom-title":
            # /rename のカスタム名。ループで自然に last-wins（＝最後の1件が現在値）。
            # 兄弟の ai-title（自動生成・更新され続ける）と agent-name（自動命名と混在）は
            # 採用しない（docs/transcript-format.md）。非文字列は無視。
            ct = d.get("customTitle")
            if isinstance(ct, str):
                custom_title = short(ct, 30)
        if t in ("user", "assistant"):
            skill_events.append(d)
            parsed.append(d)
            if len(parsed) > 120:
                parsed.pop(0)

    if not parsed:
        return None

    title = _remembered_title(str(path), custom_title, now, mtime)
    perm_mode = _remembered_perm_mode(str(path), perm_mode, now, mtime)
    skills = remembered_skills(str(path), skill_events, now, mtime)
    tasks = _remembered_tasks(str(path), task_lines, now, mtime)
    work = _work_from_tasks(tasks)

    # R86-D: 鮮度は**中身の最終イベント時刻**で測る。Claude Code はアイドルな transcript も
    # 1時間ごとに touch する（実測: サイズがバイト単位で同一のまま mtime だけ +3600秒）ため、
    # mtime を鮮度にすると **90時間前に終わったセッションが「出勤中」に居座る**（実測で
    # 表示13人中7人=53%が幽霊）。ユーザーはその幽霊をタップして「指示が届かない」と感じる。
    # timestamp を持たない行しか無い場合（合成フィクスチャ等）は従来どおり mtime へフォールバック。
    last_event = max((transcript_event_time(d, 0.0) for d in parsed), default=0.0)
    if last_event > 0:
        age = max(0.0, now - last_event)

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
        elif age < 1800 and can_prompt(perm_mode, last_tool_name,
                                       (last_tool_block or {}).get("input")):
            # resting帯(30分超)のtool止まりはクラッシュ/放置残骸＝❗を出し続けない（誤プッシュ通知の門番）
            # R86-F: さらに「そのツールが人間に聞き得るか」で門を絞る。bypassPermissions下で
            # ask ルールに該当しない Bash は聞かれない＝走っているだけなので❗にしない。
            approval_min = max(1, int(age // 60))

    # R86-H: PermissionRequest フックが publish した事実は、上の推測より常に強い。
    # フックが動いていれば「聞かれた瞬間」に❗が立つ（75秒待たない）し、
    # 人間がターミナルで答えた瞬間に掲示が消える（❗が居座らない）。
    ask = pending_approval(path.stem, now, grace=ASK_GRACE)
    if ask:
        # 人間の返事を待って止まっている＝「休憩中」でも「作業中」でもなく **あなた待ち**。
        # （resting のままだとラウンジへ歩いて行き、❗の相手が休んでいるように見える）
        state = "waiting"
        if ask["kind"] == "question":
            question = question or ask["title"]
            if not question_options and ask["options"]:
                # ★形を必ず [{label, desc}] に揃える（掲示は素の文字列で持つ）。
                # 素の文字列のまま載せると **スマホの選択肢ボタンが1つも描かれない**
                # （PWAの questionOptionEntries が option.label を読む）＝R86-H で実際に踏んだ。
                question_options = [{"label": o, "desc": ""} for o in ask["options"]]
        elif not approval_min:
            approval_min = max(1, int((now - ask["ts"]) // 60))

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
        # R85-1: /rename のセッション名（無ければ""）。表示側が title || disp で名前を合成する。
        # disp・採番（N号）は無改変＝既存ピンを壊さない（設計= R85計画）。
        "title": title,
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
        # R86-D: 指示を今すぐ受け取れるか（Stop hook の待機が生きているか）。
        # 出勤中(SHOW_WINDOW=3h)と受信待機(hookの寿命)は別の窓なので、UIで正直に分ける。
        "listening": session_listening(path.stem, now),
        "lastSaid": last_said,
        "lastOrder": last_order,
        "question": question,
        "approvalMin": approval_min,
        "stuckTool": f"{status_verb} {status_target}".strip() if approval_min else "",
    }
    if ask:
        # R86-H: 「推測」ではなく「いま聞かれている事実」。UIはこれがある時だけ
        # 承認/回答ボタンを出す（フックが待っていないのに押せると嘘になる）。
        employee["ask"] = ask
    if question_options:
        employee["questionOptions"] = question_options
    if work is not None:
        employee["work"] = work
    return employee



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
        return (meta.get("name") or Path(cwd).name, meta.get("role", ""))
    base = Path(cwd).name if cwd else dirname.strip("-").split("-")[-1]
    return nfc(base) or "未知のプロジェクト", ""


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


def group_by_project(employees, lang="ja", mode="project"):
    """employees[] を roster[] へ畳む（employees は破壊しない）。

    mode="project"（関数既定＝既存テスト互換）: cwd 単位に集約＝1アバター=1プロジェクト。
    mode="session"（R86-A・scan_office の実既定）: 全セッションを単独グループへ＝
    1アバター=1セッション。session=自分なので投函・❗・Pushがそのセッションへ直接届く。
    代表セッション = ❗を出している中で最新 → 居なければ全体で最新（sessionモードでは自明に自分）。
    """
    order = []
    groups = {}
    for e in employees:
        if e.get("external"):
            # 別Macの稼働体。まとめず1体1プロジェクトとして扱う（専用区画に置くため）。
            key = f"ext:{e.get('session', '')}"
        elif mode == "session":
            # R86-A: 1アバター=1セッション（ext: と同じ流儀の単独グループ）
            key = f"ses:{e.get('session', '')}"
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
        if lead.get("external"):
            pid = lead.get("session", "")
        elif key.startswith("ses:"):
            # R86-A: セッション単独アバターの派生ID= sha1(nfc(cwd+"\n"+session))[:12]。
            # `\n` はパスに出現しない＝cwdグループのIDと衝突不能。パス/セッションID平文を
            # 含まないので中継搬送可。席の永続化・PWAの回答済みフラグ・Push attnstate が独立キー化。
            pid = project_id_for(f"{lead.get('cwd', '')}\n{lead.get('session', '')}",
                                 lead.get("dept", ""))
        else:
            pid = project_id_for(lead.get("cwd", ""), lead.get("dept", ""))
        proj = {
            "projectId": pid,
            "session": lead.get("session", ""),          # 代表＝指示の宛先
            "name": lead.get("dept", ""),
            "role": lead.get("role", ""),
            "cwd": lead.get("cwd", ""),
            # branch/lastOrder/skills/avatar は R85-2 で撤去（roster に表示先ゼロの
            # デッドペイロードだった。employees[] 側は MCP が読むため維持）
            "crew": len(members),
            "state": state,
            "kind": lead.get("kind", "idle"),
            "verb": lead.get("verb", ""),
            "target": lead.get("target", ""),
            "age": min(int(m.get("age") or 0) for m in members),
            "minions": sum(int(m.get("minions") or 0) for m in members),
            "pending": any(bool(m.get("pending")) for m in members),
            # R86-D: 1つでも受信待機していれば「届く」（sessionモードでは自分自身のみ）
            "listening": any(bool(m.get("listening")) for m in members),
            "attention": bool(attn),
            "approvalMin": int(lead.get("approvalMin") or 0),
            "question": lead.get("question", ""),
            "stuckTool": lead.get("stuckTool", ""),
            # R86-H: 代表が「いま聞かれている」なら、その事実ごと運ぶ（本文は中継前に落とす）
            "ask": lead.get("ask") or None,
            "lastSaid": lead.get("lastSaid", ""),
            "feed": lead.get("feed", []),
            "sessions": [_session_brief(m) for m in
                         sorted(members, key=lambda m: int(m.get("age") or 0))],
        }
        # R85-1: /rename のセッション名。lead は❗発生で入れ替わる（=lead依存だと
        # ポーリング毎に名前がチラつく）ので、title を持つメンバのうち sessionId 昇順で
        # 最初の1件を採用する（メンバ集合が変わらない限り不変の決定則）。
        titled = sorted((m for m in members if m.get("title")),
                        key=lambda m: m.get("session", ""))
        proj["title"] = titled[0]["title"] if titled else ""
        if lead.get("external"):
            proj["external"] = lead["external"]
        if lead.get("questionOptions"):
            proj["questionOptions"] = lead["questionOptions"]
        # R69: work の counts はプロジェクト内の全セッションを合算する。
        # 代表(lead)だけを見ると、代表交代のたびにドーナツが急変して
        # 「タスクが完了した」ように見える（実測: 未着手8→2）。now/next/done の
        # リストは代表のものを維持（本文面は1セッション分で十分・宛先とも一致）。
        work_members = [m.get("work") for m in members if isinstance(m.get("work"), dict)]
        if work_members:
            counts = {"pending": 0, "in_progress": 0, "completed": 0}
            for w in work_members:
                c = w.get("counts") or {}
                for k in counts:
                    counts[k] += int(c.get(k) or 0)
            lead_work = lead.get("work") or {}
            proj["work"] = {
                "now": lead_work.get("now") or [],
                "next": lead_work.get("next") or [],
                "done": lead_work.get("done") or [],
                "counts": counts,
            }
        # 表示名: 同名プロジェクトが並ぶことは（cwd単位なので）原則ないが、
        # dept フォールバック時の衝突に備えて採番は残す。
        proj["disp"] = proj["name"]
        projects.append(proj)

    # R69: 採番はグループの不変キー（projectId=cwdハッシュ）昇順で振る。
    # 出現順（employeesの走査順=mtime依存）で振ると、ポーリング間で
    # 「制作本部(works)」↔「制作本部(works) 2号」が入れ替わる（実測）。
    seen = {}
    for p in sorted(projects, key=lambda p: p["projectId"]):
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


_PRUNE_EVERY = 3600.0
_LAST_PRUNE = [0.0]
PID_LITTER = 24 * 3600          # 心拍は5秒毎・hookの寿命は12時間＝1日超の pidfile は必ず死骸


def prune_inbox_litter(now=None):
    """指示ポストの置き場に溜まる死骸を掃除する（1時間に1回・失敗は黙って諦める）。

    実測（2026-08-28）: pidfile が **262個・最古35日前**、さらに宛先セッションが消えた
    51日前の未配達指示が1件残っていた。どちらも実害は無いが、放っておくと増え続ける。
    ★消してよい根拠: pidfile は hook が毎周 touch する（寿命12時間）ので1日以上古いものは
    死骸。指示は TTL=3時間で hook 自身が捨てるので、その倍を過ぎたものは誰も受け取らない。
    """
    now = now or time.time()
    if now - _LAST_PRUNE[0] < _PRUNE_EVERY:
        return 0
    _LAST_PRUNE[0] = now
    removed = 0
    try:
        entries = list(INBOX.iterdir())
    except OSError:
        return 0
    for f in entries:
        try:
            if f.name.startswith(".") and f.name.endswith(".pid"):
                if now - f.stat().st_mtime > PID_LITTER:
                    f.unlink(); removed += 1
            elif f.suffix == ".json" and not f.name.startswith("_"):
                age = now - f.stat().st_mtime
                if age > INBOX_TTL * 2:          # hook が捨てる窓の倍＝誰も受け取らない
                    f.unlink(); removed += 1
        except OSError:
            pass
    return removed


def scan_office():
    now = time.time()
    prune_inbox_litter(now)
    config = load_config()
    ed = edition(config)
    edition_info = {"id": ed, "features": edition_features(ed)}
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
            # R86-D: mtime のゲートを通っても、**中身**が SHOW_WINDOW より古ければ退勤扱い。
            # Claude Code がアイドルな transcript を1時間ごとに touch するため、mtime だけだと
            # 数十時間前に終わったセッションが居座り続ける（実測53%が幽霊）。
            # ★R86-H: ただし「いま人間の返事を待って止まっている」セッションは落とさない。
            # 止まっている＝新しいイベントが出ない＝古く見える、という構造なので、素直に
            # 窓で切ると**承認まちが3時間で画面から消える**（実測: 3時間45分ブロックされた
            # works セッションが❗224分と算出されたまま非表示だった）。掲示があれば生きている。
            if info and info.get("age", 0) > SHOW_WINDOW and not info.get("ask"):
                continue
            if info:
                dept, role = project_label(info["cwd"], proj.name, config)
                info["dept"] = dept
                info["role"] = role
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

    # 送信履歴（配達状況つき・新しい順12件）＋ R86-I「今日のオフィス」の実数
    # （画面に載せるのは件数だけ＝本文は乗らない。台帳は50件保持なので大量に送った日は
    #  頭打ちになりうる＝その旨は表示側で断らず、素直に「直近50件のうち今日ぶん」を出す）
    all_hist = load_history()
    day_start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    today_sent = sum(1 for h in all_hist if float(h.get("ts") or 0) >= day_start)
    last_ts = max((float(h.get("ts") or 0) for h in all_hist), default=0.0)
    today_view = {"sent": today_sent,
                  "lastSentAgo": int(now - last_ts) if last_ts else None,
                  "capped": len(all_hist) >= 50}
    hist = []
    for h in reversed(all_hist[-12:]):
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
    # R86-A: 粒度は avatarMode（既定 session=1アバター=1セッション・ユーザー裁定）。
    mode = avatar_mode(config)
    roster = group_by_project(employees, _LANG, mode=mode)

    # R79-10 遠隔実行: 許可リスト（表示用の最小ビュー＝argv/cwd/envは載せない）と実行結果。
    # caps はUIの機能ゲート（旧Macでは▶実行ボタンを出さない＝版ズレ耐性）。
    recipes, _recipe_errors = office_actions.load_recipes()
    actions_view = {
        "recipes": office_actions.recipes_public(recipes),
        "results": office_actions.results_public(),
        "caps": {"actions": 1, "ws": 1},
    }
    # R80: 中継の今日の使用量（未設定/古い＝None＝UIは何も出さない）
    relay_view = relay_usage()

    return {
        "officeName": config.get("officeName") or default_office_name(_LANG),
        "employees": employees,
        "roster": roster,
        "history": hist,
        "today": today_view,
        "generatedAt": now,
        "setup": setup,
        "edition": edition_info,
        "actions": actions_view,
        "relay": relay_view,
        "res": res_summary(now),
        # R82: 定型文は「遠隔から使うために自分で保存した再利用フレーズ」＝中継搬送が目的。
        # feed/history系のredaction対象にしない（上限8件×120字・label/textのみ）
        "templates": load_templates(),
        "launchable": launchable_projects(now),
        "lang": _LANG,
        "avatarMode": mode,          # R86-A: UIの文言分岐用（session/project・機微なし）
        # counts は MCP office_status の出勤サマリが読む（UI/PWAは world 側で再計算＝読まない）
        "counts": {
            "working": sum(1 for e in employees if e["state"] == "working"),
            "waiting": sum(1 for e in employees if e["state"] == "waiting"),
            "resting": sum(1 for e in employees if e["state"] == "resting"),
        },
        # rosterCounts は R85-2 で撤去（読者ゼロのデッドペイロードだった）
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
    # R66: hint/ph(キー形式の例)/getFrom(発行場所) の文言はここが正本（L_now ja/en）。
    # UIは group A(auto/cli=設定不要・キー入力UIを出さない) / B(key=キーを貼る) に分けて描く。
    claude_connected = PROJECTS.is_dir()
    return {"providers": [
        {
            "id": "claude", "label": "Claude Code", "mode": "auto",
            "connected": claude_connected,
            "hint": (L_now("/login でアカウントを切り替えて使うと、消費は自動で別々に記録されます",
                           "Switch accounts with /login — usage is recorded per account automatically")
                     if claude_connected else
                     L_now("ターミナルで claude を使うと自動でつながります（設定不要）",
                           "Connects automatically once you use claude in a terminal")),
        },
        {
            "id": "codex", "label": "Codex", "mode": "cli",
            "connected": (_HOME / ".codex" / "auth.json").is_file(),
            "hint": L_now("ターミナルで codex にログインすると自動でつながります",
                          "Connects automatically once you log in with codex in a terminal"),
        },
        {
            "id": "gemini", "label": "Gemini CLI", "mode": "cli",
            "connected": (_HOME / ".gemini" / "oauth_creds.json").is_file(),
            "hint": L_now("gemini CLI のログイン状態を表示します",
                          "Shows the gemini CLI login status"),
        },
        {
            "id": "openai_key",
            "label": L_now("OpenAI APIキー（キャラ画像生成）", "OpenAI API key (character art)"),
            "mode": "key",
            "connected": bool(key_values["OPENAI_API_KEY"]),
            "masked": masked("OPENAI_API_KEY"),
            "hint": L_now("➕新プロジェクトのキャラ画像生成に使います",
                          "Used to generate character art for ➕ new projects"),
            "ph": "sk-…", "getFrom": "platform.openai.com/api-keys",
        },
        {
            "id": "x_api",
            "label": L_now("X API（使用量の自動取得）", "X API (usage auto-fetch)"),
            "mode": "key",
            "connected": bool(key_values["X_BEARER_TOKEN"]),
            "masked": masked("X_BEARER_TOKEN"),
            "hint": L_now("Bearer Token（読み取り専用でOK）で使用量を取得します",
                          "Fetches usage with a Bearer token (read-only is fine)"),
            "ph": L_now("AAAA…（Bearer Token）", "AAAA… (Bearer token)"),
            "getFrom": "developer.x.com",
        },
        {
            "id": "openai_usage",
            "label": L_now("OpenAI 使用金額（管理キー）", "OpenAI spend (admin key)"),
            "mode": "key",
            "connected": bool(key_values["OPENAI_ADMIN_KEY"]),
            "masked": masked("OPENAI_ADMIN_KEY"),
            "hint": L_now("organization の実請求額を取得します",
                          "Fetches your organization's actual spend"),
            "ph": "sk-admin-…",
            "getFrom": "platform.openai.com/settings/organization/admin-keys",
        },
        # R63: APIプロバイダ（消費・残高の自動取得）
        {
            "id": "openrouter",
            "label": L_now("OpenRouter（消費・上限）", "OpenRouter (spend & limit)"),
            "mode": "key",
            "connected": bool(key_values["OPENROUTER_API_KEY"]),
            "masked": masked("OPENROUTER_API_KEY"),
            "hint": L_now("当月消費とキー上限を自動取得します",
                          "Fetches this month's spend and the key's limit"),
            "ph": "sk-or-v1-…", "getFrom": "openrouter.ai/settings/keys",
        },
        {
            "id": "moonshot",
            "label": L_now("Kimi / Moonshot（残高）", "Kimi / Moonshot (balance)"),
            "mode": "key",
            "connected": bool(key_values["MOONSHOT_API_KEY"]),
            "masked": masked("MOONSHOT_API_KEY"),
            "hint": L_now("残高を自動取得します（国際版 api.moonshot.ai のキー）",
                          "Fetches balance (international api.moonshot.ai key)"),
            "ph": "sk-…", "getFrom": "platform.kimi.ai",
        },
        {
            "id": "deepseek",
            "label": L_now("DeepSeek（残高）", "DeepSeek (balance)"),
            "mode": "key",
            "connected": bool(key_values["DEEPSEEK_API_KEY"]),
            "masked": masked("DEEPSEEK_API_KEY"),
            "hint": L_now("残高を自動取得します", "Fetches balance"),
            "ph": "sk-…", "getFrom": "platform.deepseek.com/api_keys",
        },
        {
            "id": "groq",
            "label": L_now("Groq（予算のみ）", "Groq (budget only)"),
            "mode": "key",
            "connected": bool(key_values["GROQ_API_KEY"]),
            "masked": masked("GROQ_API_KEY"),
            "hint": L_now("消費APIが無いため、⚡の予算欄で上限を手動設定して使います",
                          "No spend API — set a manual budget in ⚡ instead"),
            "ph": "gsk_…", "getFrom": "console.groq.com/keys",
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
    # R79: TTL を持たせる。旧実装は期限が無く、閉じたセッション宛の指示が**無期限に保留**
    # された（受信フックの寿命は最長2時間・UIの表示窓は3時間・掃除も無し）。結果
    # 「2〜3時間前に止まったセッションは画面に出るのに指示は永遠に届かず📨が残り続ける」。
    # 期限切れは hook 側と _watch_loop の掃除役が捨てる＝古い指示が突然実行されない。
    tmp.write_text(json.dumps({"text": text, "ts": time.time(), "from": "office",
                               "ttl": INBOX_TTL},
                              ensure_ascii=False), encoding="utf-8")
    tmp.rename(INBOX / f"{session}.json")
    _record_instruction_history(session, text)
    return True, "投函しました"


# ---- ➕ 新しいプロジェクト起動（P1） ------------------------------------
# 流れ: pick(フォルダ選択) → new(config先頭に登録 → Terminalでclaude起動)
# （キャラ画像の生成はR80で廃止＝3D化・モノグラム化により生成物を誰も表示しなくなったため。
#  1件あたり約$0.4の画像生成が不可視のPNGを作り続けていた）
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


def add_project(path, name, role, launch=False):
    """office_config.json へ登録し、必要なら Terminal で claude を起動する"""
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
    # read-modify-write を _lock+flock 内で原子的に（P4の daemon/dev 併走プロセスとの lost update 防止）
    with _lock, _file_flock(config_file()):
        try:
            cfg = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {"projects": {}}
        except (OSError, json.JSONDecodeError):
            return False, "office_config.json が読めません（壊れている可能性・手動確認を）", {}
        projects = cfg.get("projects", {})
        existing = pattern in projects
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
        _write_config(cfg)
        _cache["t"] = 0.0
    launched = launch_claude(str(p)) if launch else False
    return True, "登録しました", {
        "pattern": pattern, "existing": existing, "name": name, "launched": launched,
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
        rec["revoked_at"] = int(time.time())
        save_devices(d)
    return True


DEVICE_KEEP_DEAD = 7 * 86400      # 期限切れを台帳に残す期間
DEVICE_KEEP_REVOKED = 3600        # 失効直後は「失効済み」として残す（押した結果が見えるように）


def prune_devices(now=None):
    """失効・期限切れの端末を台帳から落とす（秘密を必要以上に持ち続けない）。

    実測（2026-08-28）: 📱スマホ連携を押すたびに新しい鍵が増え、**57件（うち有効29件）**
    まで溜まっていた。どれも署名付き指示を送れる鍵なので、使い終わったものは消す。
    有効なものには一切触らない（消すのは revoked と、期限切れから DEVICE_KEEP_DEAD 経過）。
    """
    now = int(now or time.time())
    with _lock:
        d = load_devices()
        devs = d.get("devices", {})
        dead = []
        for did, rec in devs.items():
            exp = int(rec.get("expires") or 0)
            if rec.get("revoked"):
                # 押した直後は一覧に「失効済み」として残す（操作の結果が見えないと不安）。
                # revoked_at を持たない旧レコードは即対象（もう使えないので残す意味がない）。
                if now - int(rec.get("revoked_at") or 0) > DEVICE_KEEP_REVOKED:
                    dead.append(did)
            elif exp and exp + DEVICE_KEEP_DEAD < now:
                dead.append(did)
        for did in dead:
            devs.pop(did, None)
        if dead:
            save_devices(d)
    return len(dead)


def list_devices():
    """secret を伏せたデバイス一覧（画面表示用）。状態を明示し、有効なものを先に出す。"""
    prune_devices()
    now = int(time.time())
    d = load_devices()
    out = []
    for did, rec in d.get("devices", {}).items():
        expires = int(rec.get("expires") or 0)
        state = ("revoked" if rec.get("revoked")
                 else "expired" if expires and expires <= now else "active")
        out.append({
            "device_id": did, "label": rec.get("label", ""),
            "created": rec.get("created", 0), "expires": expires,
            "revoked": bool(rec.get("revoked")), "last_used": rec.get("last_used", 0),
            # 「どれが今も使える鍵か」が画面から分かること（29件全部が同じ「スマホ」表示で
            # 見分けられない状態だった）。日数は表示側の計算をサーバーに寄せる。
            "state": state, "daysLeft": max(0, (expires - now) // 86400) if expires else 0,
        })
    out.sort(key=lambda x: (x["state"] != "active", -x["created"]))
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
            # M4: 他GETと同様CSRFゲートを掛ける。office_json は templates全文・recipes・
            # results.output・question本文を含み、かつ res_summary() が**ユーザーのAPIキーで
            # 外部HTTP**を誘発する（純粋な読取ではない）＝任意Webページからの副作用を防ぐ。
            # ローカルUIの api() は GET にも X-Office-Local を付けるのでUI側の変更は不要。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(office_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/external/openclaw":
            # 外部接続の器はローカルUI専用。office_jsonへは混ぜない。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            if not edition_features(edition()).get("openclaw"):
                return self._deny(403, "openclaw is not part of this edition")
            self._send(200, json.dumps(external_openclaw_json(), ensure_ascii=False).encode("utf-8"),
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
            self._send(200, json.dumps(status_board.status_board_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/session/dialog"):
            # R86-B: セッションの実やり取り（シート会話ビューア）。会話本文を返す唯一の経路＝
            # loopback+CSRF配下のみ・office_json 非搭載（中継へ流れる経路が構造的に無い）。
            # 未知sessionは 200+空（fixture worldのシート開でも console error を出さない）。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            qs = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            session = (qs.get("session") or [""])[0]
            if not _SESSION_ID_RE.fullmatch(session or ""):
                return self._deny(400, "bad session")
            raw_depth = (qs.get("depth") or ["0"])[0] or "0"
            if not _DEPTH_RE.fullmatch(raw_depth) or int(raw_depth) > DIALOG_MAX_DEPTH:
                return self._deny(400, "bad depth")
            depth = int(raw_depth)
            p = _session_transcript(session)
            if not p:
                page = {"messages": [], "depth": depth, "maxDepth": DIALOG_MAX_DEPTH,
                        "hasMore": False, "total": 0}
            else:
                nbytes = DIALOG_DEPTHS[depth][0]
                # truncated の述語は tail_lines の欠け行破棄条件と同じ（size > nbytes）。
                # tail_lines も内部で stat するので厳密には二重＝読み中に窓境界を跨いで
                # 追記されると1周だけ hasMore が揺れる（次のポーリングで整合。実害は無い）。
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                page = dialog_page(tail_lines(p, nbytes), depth, truncated=size > nbytes)
            self._send(200, json.dumps({"ok": True, **page},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/projects":
            # cwd を含むローカルパス一覧なので、別オリジンGETには公開しない。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(projects_index.projects_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/templates":
            # R82: 定型文の全文はローカルUIとoffice_json両方に出る（ユーザーが遠隔利用の
            # ために自分で保存した再利用フレーズ＝中継搬送は設計意図）。編集はローカルのみ。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps({"templates": load_templates()},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/recipes":
            # R79-10: 許可リストの全文（argv/cwd/env入り）はローカルUI専用。
            # 中継へ出るのは office_json の recipes_public（id/label/dangerousのみ）だけ。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            recipes, errors = office_actions.load_recipes()
            self._send(200, json.dumps({"recipes": recipes, "errors": errors,
                                        "file": str(office_actions.RECIPES_FILE)},
                                       ensure_ascii=False).encode("utf-8"),
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
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._host_ok():
            return self._deny(403, "invalid host")
        if not self._csrf_ok():
            return self._deny(403, "cross-site request blocked")
        route = self.path.split("?", 1)[0]
        # R42.2 の有料機能ゲートは R84 全機能無料化で撤去（features は常に全ON）。
        try:
            n = int(self.headers.get("Content-Length", 0))
            body_limit = 100_000
            data = json.loads(self.rfile.read(min(max(n, 0), body_limit)).decode("utf-8")) if n else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        extra = {}
        if self.path.startswith("/api/instruct"):
            ok, msg = post_instruction(data.get("session", ""), data.get("text", ""))
        elif route == "/api/approval/reply":
            # R86-H: 止まっているセッションへ「いま」答える（指示ポストは**ターンが終わるまで
            # 届かない**ので、承認まちの相手には構造的に届かなかった＝ユーザー報告の本体）。
            # ここは loopback+CSRF 配下＝Macの前の人間なので、実行の許可(allow)も出せる。
            sess = str(data.get("session") or "")
            ask = pending_approval(sess)
            if not ask:
                ok, msg = False, L_now(
                    "そのセッションはいま何も聞いていません（もう答えられたか、承認フックが未配線です）",
                    "that session is not asking anything right now")
            else:
                behavior = "allow" if data.get("behavior") == "allow" else "deny"
                text = str(data.get("message") or "")
                if behavior == "deny" and not text.strip():
                    ok, msg = False, L_now("回答の内容が空です", "empty answer")
                else:
                    try:
                        write_approval_reply(sess, behavior, text, src="local")
                        ok = True
                        msg = (L_now("許可しました", "approved") if behavior == "allow"
                               else L_now("回答を届けました", "answer delivered"))
                        extra = {"kind": ask["kind"]}
                    except (OSError, ValueError) as e:
                        ok, msg = False, str(e)
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
        elif self.path.startswith("/api/project/pick"):
            ok, msg = pick_folder()
            if ok:
                extra = {"path": msg, "suggest": Path(msg).name}
        elif self.path.startswith("/api/project/new"):
            ok, msg, extra = add_project(
                data.get("path", ""), data.get("name", ""), data.get("role", ""),
                launch=bool(data.get("launch")))
        elif self.path == "/api/projects/launch":
            path = data.get("path")
            # R85-3: PCの「▶ プロジェクト起動」は launchable[]（projectId+名前のみ・パス非搬送）
            # から呼ぶため projectId でも引き当てる。解決は遠隔launch（_action_exec）と同じ
            # projects_index＝「過去に本当に開かれたプロジェクト」だけが対象。
            pid = str(data.get("projectId") or "")
            if not path and pid:
                for prj in (projects_index.projects_json().get("projects") or []):
                    p0 = prj.get("cwd") or ""
                    if p0 and project_id_for(p0) == pid:
                        path = p0
                        break
            try:
                valid_path = isinstance(path, str) and bool(path.strip()) and Path(path).is_dir()
            except (OSError, ValueError):
                valid_path = False
            if not valid_path:
                ok, msg = False, "フォルダが存在しません"
            else:
                ok = launch_claude(path)
                msg = "Terminalを起動できませんでした"
        elif self.path.startswith("/api/pair/new"):
            # secret を返すのは loopback+CSRF 済みのローカルUIのみ（_host_ok/_csrf_ok 配下）
            dev = new_device(data.get("label", ""))
            pu = pair_url(dev)
            ok, msg = True, "発行しました"
            extra = {**dev, "pairUrl": pu, "relayConfigured": bool(pu), "qrSvg": pair_qr_svg(pu)}
        elif self.path.startswith("/api/pair/revoke"):
            ok = revoke_device(data.get("device_id", ""))
            msg = "失効しました" if ok else "端末が見つかりません"
        elif self.path.startswith("/api/templates/set"):
            ok, msg = save_templates(data.get("templates"))
            if ok:
                extra = {"templates": load_templates(), "message": msg}
        elif self.path.startswith("/api/recipes/set"):
            # R79-10: 許可リストの編集は**ここだけ**（loopback+CSRF＝Macの前の人間のみ）。
            # 遠隔からレシピを作る/変える経路は存在しない（電話が持てるのは登録済みidへの参照だけ）。
            recipes, errors = office_actions.validate_recipes(data)
            if errors:
                ok, msg = False, " / ".join(errors[:3])
            else:
                try:
                    office_actions.save_recipes(recipes)
                    ok, msg = True, f"{len(recipes)}件を保存しました"
                except OSError as e:
                    ok, msg = False, f"保存できません: {e}"
            extra = {"recipes": recipes, "errors": errors}
        elif self.path.startswith("/api/action/exec"):
            # R79-10: 実行者は office_server（Automation TCC同意済み＝osascript経路を持つ）。
            # relay_agent は act-封筒を検証してここへ 127.0.0.1 で回すだけの配達員でいる。
            ok, msg, extra = _action_exec(data)
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
        else:
            self.send_response(404)
            self.end_headers()
            return
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


class _OfficeHTTPServer(ThreadingHTTPServer):
    """ブラウザが読み終わる前に閉じただけで25行のトレースバックを吐かない。

    実測（2026-08-28・本番ログ）: ページを閉じる/リロードするたびに socketserver が
    BrokenPipeError/ConnectionResetError のトレースバックを流し、**本物のエラーが
    その中に埋もれていた**（36件中36件がこれ）。切断は異常ではないので1行に畳む。
    それ以外の例外は今までどおり全文出す（握り潰さない）。
    """

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return                      # ブラウザが先に閉じただけ＝正常
        super().handle_error(request, client_address)


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


# ── R80: 中継の使用量（Cloudflare無料枠に対する今日の消費）─────────────────
# relay_agent（別プロセス）が中継から受け取った値をここへ書き、office_json 経由でUIへ出す。
# 「気づけないまま枠を割って中継が止まる」のを防ぐための観測点。秘密は含まない。
RELAY_USAGE_FILE = _HOME / ".claude" / "office_relay_usage.json"


def set_relay_usage(usage):
    """relay_agent から呼ばれる（同プロセスではない＝ファイル経由）。失敗は握る。"""
    if not isinstance(usage, dict):
        return False
    try:
        RELAY_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RELAY_USAGE_FILE.with_name(RELAY_USAGE_FILE.name + ".tmp")
        tmp.write_text(json.dumps({
            "rows": int(usage.get("rows") or 0),
            "limit": int(usage.get("limit") or 100000),
            "pct": int(usage.get("pct") or 0),
            "level": int(usage.get("level") or 0),
            "at": time.time(),
        }), encoding="utf-8")
        tmp.replace(RELAY_USAGE_FILE)
        return True
    except (OSError, TypeError, ValueError):
        return False


TEMPLATES_FILE = _HOME / ".claude" / "office_templates.json"
TEMPLATE_MAX = 8
TEMPLATE_LABEL_MAX = 20
TEMPLATE_TEXT_MAX = 120


def load_templates():
    """R82: ユーザー定義のクイック定型文（label/textのみ・上限つき）。壊れていれば空。"""
    try:
        raw = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    out = []
    if isinstance(raw, list):
        for item in raw[:TEMPLATE_MAX]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()[:TEMPLATE_LABEL_MAX]
            text = str(item.get("text") or "").strip()[:TEMPLATE_TEXT_MAX]
            if label and text:
                out.append({"label": label, "text": text})
    return out


def save_templates(items):
    """編集は loopback+CSRF のローカルUIのみ（レシピと同じ「作るのはMacの前だけ」）。"""
    if not isinstance(items, list):
        return False, "形式が不正です"
    if len(items) > TEMPLATE_MAX:
        return False, f"定型文は{TEMPLATE_MAX}件までです"
    clean = []
    for item in items:
        if not isinstance(item, dict):
            return False, "形式が不正です"
        label = str(item.get("label") or "").strip()
        text = str(item.get("text") or "").strip()
        if not label or not text:
            return False, "ラベルと本文の両方が必要です"
        if len(label) > TEMPLATE_LABEL_MAX or len(text) > TEMPLATE_TEXT_MAX:
            return False, f"ラベル{TEMPLATE_LABEL_MAX}字・本文{TEMPLATE_TEXT_MAX}字までです"
        clean.append({"label": label, "text": text})
    tmp = TEMPLATES_FILE.with_name(TEMPLATES_FILE.name + ".tmp")
    try:
        TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, TEMPLATES_FILE)
    except OSError as e:
        return False, f"保存できません: {e}"
    return True, f"{len(clean)}件を保存しました"


_RES_CACHE = {"ts": 0.0, "data": None}


def res_summary(now=None):
    """R82: office_json["res"] v2。旧キー(fiveHour/sevenDay/staleSec)は後方互換で温存し、
    providers[]（多プロバイダ最小ゲージ）を追加。60秒キャッシュ・fail-soft
    （補助データが office_json 本体を殺さない）。"""
    now = time.time() if now is None else now
    cache = _RES_CACHE
    if now - cache["ts"] < 60:
        return cache["data"]
    data = None
    try:
        data = status_board.claude_gauge_public(now)
    except Exception:
        data = None
    try:
        g = status_board.gauges_public(now)
        if g and g.get("providers"):
            data = dict(data or {})
            data["providers"] = g["providers"]
    except Exception:
        pass
    cache["ts"] = now
    cache["data"] = data
    return data


_LAUNCHABLE_CACHE = {"ts": 0.0, "data": []}


def launchable_projects(now=None):
    """R80.6: スマホの「▶ プロジェクトを起動」用の一覧。projectId＋表示名＋鮮度のみ＝
    **パスは1バイトも載せない**（中継に流れる前提。引き当ては exec_remote_action の launch と
    同じ projects_index＝過去に本当に開かれたプロジェクトだけ）。projects_index はディレクトリ
    走査を伴うので60秒キャッシュ（/api/office は数秒毎に呼ばれる）。"""
    now = time.time() if now is None else now
    cache = _LAUNCHABLE_CACHE
    if now - cache["ts"] < 60:
        return cache["data"]
    out = []
    try:
        for prj in (projects_index.projects_json(now).get("projects") or [])[:24]:
            path = prj.get("cwd") or ""
            if not path:
                continue
            out.append({"projectId": project_id_for(path),
                        "name": prj.get("name") or Path(path).name,
                        "ageSec": int(prj.get("ageSec") or 0)})
    except Exception:   # 補助データが office_json 本体を殺さない（fail-soft）
        out = []
    cache["ts"] = now
    cache["data"] = out[:12]
    return cache["data"]


def relay_usage():
    """UI表示用（24時間以上古い値は出さない＝嘘の%を見せない）。"""
    try:
        d = json.loads(RELAY_USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or time.time() - float(d.get("at") or 0) > 86400:
        return None
    return {"rows": d.get("rows"), "limit": d.get("limit"),
            "pct": d.get("pct"), "level": d.get("level")}


def _launch_target_for(project_id):
    """launch の cwd 引き当て。(target_cwd, label) を返す（不明は ("","")）。

    1) projects_index の cwd 直一致（従来）。
    2) R86-A: セッション単独アバターの派生projectId（sha1(cwd+"\\n"+session)）は index に
       無い → roster から cwd を引き当てる。ただし採用するのは **その cwd が projects_index
       の cwd 集合に在るときだけ**＝「遠隔から任意パスは起動できない」不変条件を維持
       （roster の cwd はローカル scan 由来で遠隔入力ではない）。"""
    index_rows = projects_index.projects_json().get("projects") or []
    for prj in index_rows:
        path = prj.get("cwd") or ""
        if path and project_id_for(path) == project_id:
            return path, prj.get("name") or Path(path).name
    known = {prj.get("cwd") for prj in index_rows if prj.get("cwd")}
    for p in (office_json().get("roster") or []):
        cwd0 = p.get("cwd") or ""
        if p.get("projectId") == project_id and cwd0 in known:
            return cwd0, p.get("title") or p.get("disp") or Path(cwd0).name
    return "", ""


def _action_exec(data):
    """R79-10: act-封筒の中身（署名検証は relay_agent 側で完了済み）を実行する。
    (ok, msg, extra) を返す。**必ず1つの終了状態に落ちる**（denied/busy/running/…）。

    掟:
    - 許可リスト（office_recipes.json）に無いものは実行しない。argv/cwd はレシピ側だけが持つ。
    - kind=launch は「登録済みプロジェクトのセッション起動」＝projectId（cwdのsha1[:12]）で
      config のプロジェクトを引き当てる。**遠隔から任意パスは起動できない**。
    - reqId は冪等キー（at-least-once の再配達で二重起動しない）。
    """
    office_actions.NOTIFY = notify_mac      # 実行系の通知はローカル通知に集約
    act = office_actions.parse_action(json.dumps(data.get("action") or data,
                                                 ensure_ascii=False))
    if not act:
        return False, "アクション形式が不正です", {"state": "denied"}
    device = str(data.get("device_id") or "")[:32]
    recipes, _errors = office_actions.load_recipes()
    if act["kind"] == "run":
        state, rec = office_actions.start_action(act, recipes, device=device)
        return state in ("running", "done"), state, {"state": state, "reqId": rec["reqId"],
                                                     "label": rec["label"]}
    if act["kind"] == "launch":
        # 既知の reqId は再実行しない（Terminalが二重に開く驚きを避ける）
        for r in office_actions.results_public(limit=office_actions.RESULT_KEEP):
            if r["reqId"] == act["reqId"]:
                return True, r["state"], {"state": r["state"], "reqId": r["reqId"]}
        # 引き当ては projects_index（実在する Claude プロジェクトの cwd 一覧）。
        # 「過去に本当に開かれたプロジェクト」だけが対象＝遠隔から任意パスは起動できない。
        target, label = _launch_target_for(act["project"])
        if not target or not Path(target).is_dir():
            rec = office_actions.register_result(act["reqId"], "launch", act["project"],
                                                 "denied", reason="unknown-project",
                                                 device=device)
            notify_mac("📲 遠隔起動 拒否", "未登録プロジェクト")
            return False, "denied", {"state": "denied", "reqId": rec["reqId"]}
        ok = launch_claude(target)
        rec = office_actions.register_result(act["reqId"], "launch", label,
                                             "done" if ok else "failed", device=device)
        notify_mac("📲 遠隔起動", f"{label}: {'起動しました' if ok else '失敗'}")
        return ok, rec["state"], {"state": rec["state"], "reqId": rec["reqId"],
                                  "label": label}
    return False, "未対応のアクションです", {"state": "denied"}


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
            srv = _OfficeHTTPServer(("127.0.0.1", args.port), Handler)
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
