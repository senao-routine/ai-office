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
    import office_actions
except ModuleNotFoundError:  # importlibでファイルを直接読む既存テスト向け
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import projects_index
        import status_board
        import license as office_license
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


# R80: このプロダクトの識別子（ライセンスの product と照合する）
PRODUCT_ID = "ai-office"

# R80-B1: ライセンスの有効判定を単純化した。
# 旧実装は「ライセンスのedition」×「動作中のedition」の入れ子表で、**新規cloneの既定edition
# (hybrid) を claude(Pro)ライセンスがカバーしない**ため、買った人が素のまま使うと解錠されず
# 「有効なライセンスを登録済みなのに『Pro機能です』で403」になっていた（実測確認）。
# 回避策は config への手書きのみで、READMEにもUIにも記述が無い＝返金と信頼喪失の直行便。
# 正しい約束は「買った鍵は、どの版で動かしていても、その鍵が示す機能を開ける」。
# hybrid鍵は上位互換なので、判定は「有効な鍵があるか」だけで足りる。
_LICENSE_EDITIONS = ("claude", "hybrid")   # 鍵として受理する edition 名（license.py と対）


def edition_features(ed, lic=None):
    """機能マトリクス＝表示分岐の単一集約点。UI/PWA はこの features だけを見る。

    2026-08-10 ライセンス廃止（ユーザー決定）: 署名鍵による機能ゲートを全廃し、
    **クローンした全員がスマホ連携・Push・遠隔実行・コスト表示まで使える**。
    価値は配布経路（note/Discord）＋更新＋コミュニティで作る（詳細= docs/収益化アーキテクチャ）。
    edition（claude/hybrid/openclaw）は「どの種類のエージェントを表示するか」の**表示モード**として
    のみ残す＝有料ゲートではない。lic 引数は後方互換のため残すが判定には使わない。"""
    return {
        "claudeSessions": ed in ("claude", "hybrid"),
        "openclaw": ed in ("openclaw", "hybrid"),
        "relayPwa": True,
        "push": True,
        "costDash": True,
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
        return {"valid": False, "edition": "", "product": "",
                "reason": L_now("ライセンス未登録", "no license registered")}
    if (_license_cache["state"] is not None and _license_cache["path"] == str(p)
            and _license_cache["mtime"] == mtime):
        return _license_cache["state"]
    try:
        lic = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        state = {"valid": False, "edition": "", "product": "",
                 "reason": "ライセンスファイルが読めません"}
    else:
        ok, reason = office_license.verify_license(lic, n=_license_pubkey_override())
        # R80: 1組の署名鍵で複数プロダクト（他アプリ・有料スキル）を扱うため、
        # **自分宛ての鍵しか受理しない**。v1（product無し）は AI Office 専用の既発行分。
        if ok and office_license.license_product(lic) != PRODUCT_ID:
            ok = False
            reason = L_now("この鍵は AI Office 用ではありません",
                           "This license is for another product")
        state = {"valid": ok,
                 "edition": str(lic.get("edition") or "") if ok else "",
                 "product": office_license.license_product(lic),
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
    # ★保存する項目は **canonical（署名対象）を1つも落とさない**こと。
    #   R80で product を v2 の署名対象へ足したとき、ここに追加し忘れて
    #   「登録し直すと検証が落ちる」実バグを作った（verify ▶5 の復帰E2Eが検出）。
    keep = {k: lic[k] for k in ("v", "product", "edition", "key_id", "issued",
                                "holder", "alg", "sig")
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
            "avatar": int(lead.get("avatar") or 0),
            "sessions": [_session_brief(m) for m in
                         sorted(members, key=lambda m: int(m.get("age") or 0))],
        }
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
            body_limit = 100_000
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
        elif self.path.startswith("/api/license/set"):
            ok, msg, extra = apply_license(data.get("license"))
            if ok:
                extra = {**extra, "message": msg}
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
        target, label = "", ""
        for prj in (projects_index.projects_json().get("projects") or []):
            path = prj.get("cwd") or ""
            if path and project_id_for(path) == act["project"]:
                target, label = path, prj.get("name") or Path(path).name
                break
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
