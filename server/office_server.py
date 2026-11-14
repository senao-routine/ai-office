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
import copy
from datetime import datetime, timedelta
import errno
import hashlib
import hmac
import importlib
import json
import os
import queue
import re
import secrets
import shutil
import socket
import signal
import subprocess
import sys
import threading
import time
import uuid
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote


def _load_office_events():
    """D6 の任意アダプタ。未同梱なら従来の状態推定だけを使う。"""
    try:
        return importlib.import_module("office_events")
    except ModuleNotFoundError as exc:
        if exc.name != "office_events":
            raise
        return None


try:
    import office_common
    import office_version
    import projects_index
    import status_board
    import openclaw_source
    import office_actions
    import dialog_seal      # R87: 封書（stdlib のみ）
    import office_term      # R100-1a: スマホのターミナル画面の行（純関数）
    import office_approve   # R100-1b: スマホから許可（パスキーの検証・期限つき ON・登録）
    import source_claude_bg
    import source_codex
    import office_timeline
    import office_pty       # R99: コックピットの本物の端末（stdlib のみ・出力はどこにも保存しない）
    import office_diag      # R105: β の診断情報（数と状態だけ）
    import office_door      # R126: 出勤の口（常駐 Bot・スクリプト＝vendor "bot" の行・台帳と状態はファイル）
    office_events = _load_office_events()
except ModuleNotFoundError:  # importlibでファイルを直接読む既存テスト向け
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import office_common
        import office_version
        import projects_index
        import status_board
        import openclaw_source
        import office_actions
        import dialog_seal      # R87: 封書（stdlib のみ）
        import office_term
        import office_approve
        import source_claude_bg
        import source_codex
        import office_timeline
        import office_pty
        import office_diag
        import office_door
        office_events = _load_office_events()
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
# b4-mac: 端末ごとの「最後に使った時刻」。書くのは署名を検証した relay_agent（別プロセス）だけ＝秘密の台帳とは
# 別のファイルにする（台帳を 2 プロセスで読み書きすると、失効の直後に古い写しで上書きして失効を取り消しうる）。
DEVICES_SEEN_FILE = _HOME / ".claude" / "office_devices_seen.json"
# R97: 「同時に何人居たか」の最大値。オフィスの広さ（机の数＝ティア）はこれで決まる。
# これまで**ブラウザの localStorage にしか無かった**ので、別のブラウザで開く・保存領域が消える
# といったことで部屋が縮んで見えた（本人の報告「机がだいぶ無くなっている」）。
# 不変条件 #9「office_json に載せる状態は必ずファイルに落とす」に従ってここに持つ。
PEAK_FILE = _HOME / ".claude" / "office_peak.json"
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
    # R106（plan.md #22）: 「部下」は用語統一（プロジェクト/セッション/サブエージェントの3語）の禁止語。
    # EN の "briefing agents" に合わせ、依頼先を指す中立語へ。
    "Task": "サブエージェントに指示中", "Agent": "サブエージェントに指示中", "Workflow": "部隊を編成中",
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


tail_lines = office_common.tail_lines
_file_flock = office_common.file_flock


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
    pending = _pending_questions(parsed, blocks)
    return pending[-1] if pending else None


def _pending_questions(parsed, blocks):
    """未回答 AskUserQuestion の tool_use ブロック（古い順）。"""
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
    return [item["block"] for item in questions if not item["answered"]]


def _question_shape(pending, title):
    """掲示（ask）の質問に当たる transcript の AskUserQuestion から (問いの数, 複数選択 0/1)。
    掲示は 1 問目の文（hook が空白を畳んで 200 字に切る）しか持たない＝その文で古い順に照合する。
    決まらなければ None（サブエージェントの質問は親の transcript に無い）＝スマホは従来の形のまま。"""
    norm = lambda x: " ".join(nfc(str(x or "")).split())    # noqa: E731  掲示の title は nfc 済み（short）＝両側をそろえる
    want = norm(title).rstrip("…")[:40]
    for block in pending or []:
        try:
            qs = block["input"]["questions"]
            if not isinstance(qs, list) or not qs or not isinstance(qs[0], dict):
                continue
            head = norm(qs[0].get("question") or qs[0].get("header") or "質問")
        except (KeyError, TypeError):
            continue
        if want and head.startswith(want):
            multi = any(isinstance(q, dict) and q.get("multiSelect") is True for q in qs)
            return min(len(qs), 99), 1 if multi else 0
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
# R123-A: Claude Code の自動タイトル（{"type":"ai-title"}・Claude アプリの会話名と同じもの）。
# /rename の名前（title）とは別の欄 aiTitle に置く＝名前の無いセッションだけ一覧がこれで見分けられる
#（R122 の一覧で組の見出しと行の名前が同じ「20260714 - ai-office」になっていた）。記憶は title と同じ流儀。
_AI_TITLE_MEMORY = {}


def _remembered_title(session_key, seen, now, mtime, memory=None):
    """tail窓で見た custom-title（memory を渡せば ai-title）を記憶へ合流して現在値を返す。seen=None は「窓に無かった」。"""
    memory = _TITLE_MEMORY if memory is None else memory
    if seen is not None:
        if seen:
            memory[session_key] = [seen, mtime]
        else:
            memory.pop(session_key, None)
    entry = memory.get(session_key)
    if entry:
        entry[1] = max(entry[1], mtime)   # 現役セッションを失効させない
    for key in [k for k, v in memory.items() if now - v[1] > SHOW_WINDOW]:
        memory.pop(key, None)             # 退勤セッションの残骸掃除（表示窓と同期）
    entry = memory.get(session_key)
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
    if mode == "dontAsk":
        return False                   # 聞かないモード（事前に許した道具以外は聞かずに拒否する）＝走っているだけ
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
_TASK_TAIL = office_common.IncrementalTail(TASK_TAIL_BYTES)
_TASK_OFFSETS = _TASK_TAIL.offsets


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
    reader = _TASK_TAIL if session_key else office_common.IncrementalTail(TASK_TAIL_BYTES)
    reader.initial_bytes = TASK_TAIL_BYTES
    lines = reader.read(path, session_key, now=now,
                        hold_last=lambda line: '"TaskCreate"' in line)
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
        _TASK_TAIL.drop(key)
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


MINIONS_LIVE = 60.0   # minionsLive: 直近この秒数に書いたサブエージェントの jsonl だけ（終わった子は 60 秒で数えない）
ASK_GRACE = 3.0       # 掲示が出てすぐは❗にしない（下の理由）
# R86-H2: 掲示の生死は**心拍**（hook が毎 POLL に mtime だけ更新）で判定する。deadline は最後の砦。
# 人間が TUI で答えると Claude Code は hook をその場で kill し finally は走らない＝掲示が 12 時間残り、
# ❗の幽霊・デスクトップの回答が誰にも読まれない・スマホの指示が inbox に届かない、が同時に起きた（実測 3 件）。
ASK_STALE = float(os.environ.get("OFFICE_ASK_STALE", 10))   # POLL 1s × 10・スリープ復帰のジッタ込み
ASK_LITTER = 3600.0          # 心拍が止まって 1 時間＝死骸（prune で消す）
REPLY_LITTER = 600.0         # hook の FRESH(300s) の倍＝誰も受け取らない回答


_ASK_ID_RE = re.compile(r"[0-9a-f]{32}")


def _ask_postings(session):
    """R100-0: そのセッションの掲示ファイル [(askId|None, Path)]。
    新しい名前は <session>.<askId>.json（hook が 1 件ごとに作る）。旧名 <session>.json は移行窓
    （配備の瞬間にすでに待っていた旧 hook の掲示）だけ＝askId は None。"""
    out = [(None, APPROVALS / f"{session}.json")]
    pat = re.compile(re.escape(session) + r"\.([0-9a-f]{32})\.json")
    try:
        for f in APPROVALS.glob(f"{session}.*.json"):
            m = pat.fullmatch(f.name)
            if m:
                out.append((m.group(1), f))
    except OSError:
        pass
    return out


def _is_bot_session(session):
    """R126: 出勤の口の Bot（"bot-<id>"）。Bot の相談は承認の掲示板に置かない＝ここに掲示があっても読まない・書かない（不変条件 7）。"""
    return isinstance(session, str) and session.startswith(office_door.SESSION_PREFIX)


def pending_approvals(session, now=None, grace=0.0):
    """R100-0: いまそのセッションが人間へ聞いていること**全部**（古い順）。
    同じセッションで許可が 2 件並ぶことがある（並列のツール呼び出し・サブエージェント）。"""
    if not _SESSION_RE.match(session or "") or _is_bot_session(session):
        return []
    now = now or time.time()
    asks = [a for a in (_read_ask(session, ask_id, f, now, grace)
                        for ask_id, f in _ask_postings(session)) if a]
    asks.sort(key=lambda a: (a["ts"], a["askId"] or ""))
    return asks


def pending_approval(session, now=None, grace=0.0):
    """R86-H: いまそのセッションが**実際に**人間へ聞いていること（フックが publish した事実）。

    75秒ヒューリスティックは「止まっている＝聞かれているかもしれない」という推測にすぎず、
    ユーザーに『❗が出ているのに承認画面が無い』と言わせた原因だった。こちらは一次情報。
    掲示が無い/期限切れ/壊れている＝None（推測側の判定に委ねる＝旧セッションでも暗転しない）。
    R100-0: 複数並んでいるときは**最古の 1 件**＋`askCount`（答える側は必ず askId で 1 件を指す）。
    """
    asks = pending_approvals(session, now, grace)
    if not asks:
        return None
    return dict(asks[0], askCount=len(asks))


# R112-H: 掲示の questions の上限（hook と同じ値）。R126 から正本は office_common（出勤の口の相談も同じ値）
ASK_Q_MAX, ASK_Q_LEN, ASK_OPT_MAX, ASK_OPT_LEN = (office_common.ASK_Q_MAX, office_common.ASK_Q_LEN,
                                                  office_common.ASK_OPT_MAX, office_common.ASK_OPT_LEN)


def clean_ask_questions(v):
    """R112-H: 掲示の `questions`（AskUserQuestion の全部の問いと選択肢）を形で検証して写す。
    `[{q: str 1..200, options: [str 1..40] ≤4, multi: 0|1}] 1..8 件` だけ通す。1 件でも形が違えば None（何も足さない
    ＝問いの番号がずれた一部だけを見せない）。知らないキーは写さない。office_json の ask と中継の ask（relay_agent）が同じこれを通す。"""
    if not isinstance(v, list) or not 0 < len(v) <= ASK_Q_MAX:
        return None
    out = []
    for x in v:
        if not isinstance(x, dict):
            return None
        q, ops, multi = x.get("q"), x.get("options"), x.get("multi")
        if not (isinstance(q, str) and 0 < len(q) <= ASK_Q_LEN):
            return None
        if not (isinstance(ops, list) and len(ops) <= ASK_OPT_MAX
                and all(isinstance(o, str) and 0 < len(o) <= ASK_OPT_LEN for o in ops)):
            return None
        if type(multi) is not int or multi not in (0, 1):
            return None
        out.append({"q": q, "options": list(ops), "multi": multi})
    return out


def _read_ask(session, ask_id, path, now, grace):
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("session") != session:
        return None
    if (rec.get("askId") or None) != ask_id:
        return None                      # 名前と中身の askId が食い違う掲示は信じない
    if float(rec.get("deadline") or 0) < now:
        return None                      # フックが死んだ後の掲示は幽霊
    try:
        if now - path.stat().st_mtime > ASK_STALE:
            return None                  # 心拍が止まった＝hook が kill された幽霊（R86-H2）
            # 移行: 配備前から待機中の旧 hook（心拍なし）も 10 秒で幽霊扱い＝その質問はターミナルで答える。
            # 次の PermissionRequest からは新 hook（毎回スクリプトを読み直す）が心拍を打つ。互換経路は作らない。
    except OSError:
        return None
    # ★grace: PermissionRequest フックは「結局そのまま許可される操作」でも一度は発火する
    # （信頼済みフォルダへの Write 等・実機で確認）。掲示は出るがコンマ数秒で消える＝
    # ❗を一瞬光らせてスマホへ誤プッシュする。数秒生き残った掲示だけを「本当に止まっている」と見る。
    if grace and now - float(rec.get("ts") or 0) < grace:
        return None
    kind = "question" if rec.get("kind") == "question" else "permission"
    opts = [str(o) for o in rec.get("options") or [] if isinstance(o, str)][:4]
    # R102-11: hook が掲示に書いた問いの数・複数選択（整数だけ）。無い（旧 hook）なら transcript から推す
    shape = {k: rec[k] for k, hi in (("qn", 100), ("multi", 2))
             if kind == "question" and type(rec.get(k)) is int and 0 <= rec[k] < hi}
    # R112-H: AskUserQuestion の全部の問いと選択肢（形を検証して写す・違う形は何も足さない）。
    # 許可（Bash のコマンド全文）や計画の承認には付けない
    qs = clean_ask_questions(rec.get("questions")) if (kind == "question" and rec.get("tool") == "AskUserQuestion") else None
    if qs:
        shape["questions"] = qs
    return {"tool": short(rec.get("tool") or "", 40), "kind": kind,
            "title": short(rec.get("title") or "", 200), "options": opts, **shape,
            "ts": float(rec.get("ts") or now), "askId": ask_id,
            # R100-1b: hook の危険判定が「スマホから許可できる形」と言ったか（中継へは 0/1 だけ・中身は封書）。
            # R102-2: review（全文を読んで 2 段）も 1。段と警告は封書（kind:"ask"）の中だけ
            # R103-1: mac の段も全文が読めればスマホから（全文確認＋Face ID）
            "remote": 1 if (kind == "permission" and rec.get("level") in ("remote", "review", "mac")
                            and isinstance(rec.get("inputCanon"), str)) else 0}


def _ask_raw(session, ask_id, now=None):
    """R100-1b: 生きている掲示 1 件の中身（inputCanon を含む・0600 のローカルファイル）。許可の検査と封書だけが読む。
    生死の判定は _read_ask と同じ（deadline・心拍）＝ hook が kill された掲示に許可を書かない。"""
    if (not _SESSION_RE.match(session or "") or _is_bot_session(session)
            or not (isinstance(ask_id, str) and _ASK_ID_RE.fullmatch(ask_id))):
        return None
    now = now or time.time()
    path = APPROVALS / f"{session}.{ask_id}.json"
    if not _read_ask(session, ask_id, path, now, 0.0):
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("askId") == ask_id else None


def write_approval_reply(session, behavior, message="", *, src, ask_id, extra=None):
    """フックが待っている回答を置く。allow を通せるのは src="local"（Macの前の人間）だけ
    ＝この関数がどこから呼ばれても、中継経由が実行許可に化けない（最終判定はフック側）。
    R100-0: `src` と `ask_id` はキーワード専用・既定値なし（呼び出し側が必ず「誰が・どの 1 件に」を言う）。
    ask_id=None は旧名 <session>.reply.json（移行窓の旧 hook 宛）＝remote からは書かない。
    R126: Bot（"bot-<id>"）は拒む＝Bot の相談は掲示板に置かない・Bot への答えは言葉だけ（出勤の口の inbox）＝allow を渡す道が無い。"""
    if not _SESSION_RE.match(session or ""):
        raise ValueError("bad session")
    if _is_bot_session(session):
        raise ValueError("bot sessions have no approvals (answers go through office_door)")
    if ask_id is None:
        if src == "remote":
            raise ValueError("remote reply needs askId")
        name = f"{session}.reply.json"
    elif isinstance(ask_id, str) and _ASK_ID_RE.fullmatch(ask_id):
        name = f"{session}.{ask_id}.reply.json"
    else:
        raise ValueError("bad askId")
    APPROVALS.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(APPROVALS, 0o700)
    except OSError:
        pass
    rec = {"session": session, "behavior": "allow" if behavior == "allow" else "deny",
           "message": short(message or "", 2000), "src": src, "ts": time.time()}
    if ask_id:
        rec["askId"] = ask_id
    # R100-1b: remote の回答に添える照合値（hook が自分の入力から作り直した値と比べる）。決まったキーだけ。
    # R114: mode（"tap"＝ON の間のタップ・Face ID 無し）＝hook は自分の再計算が remote／review でなければ拒否する
    for k in ("inputSha", "device", "reqId", "mode"):
        v = (extra or {}).get(k)
        if isinstance(v, str) and len(v) <= 64:
            rec[k] = v
    tmp = APPROVALS / f".{name}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, APPROVALS / name)
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


# R102-4: 合成のエラー行の種類（本文は持たない・enum だけ）。利用上限＝時間をおくかモデルを替える／会話の長さ＝/compact か
# 新しいセッション／それ以外の API エラー（過負荷・レート・通信）＝「続けて」で再開できることが多い
_ERR_LIMIT_RE = re.compile(r"reached your .{0,40}limit|usage limit|limit reached|out of (?:extra )?usage|"
                           r"credit balance|usage credits", re.I)
_ERR_CTX_RE = re.compile(r"prompt is too long|context (?:window|length)|too many tokens|input length", re.I)
PERM_MODES = frozenset({"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"})
_ERR_VERB = {
    "limit": lambda: L("利用上限で停止", "stopped at the usage limit"),
    "ctx": lambda: L("会話の長さの上限で停止", "stopped: conversation too long"),
    "api": lambda: L("API エラーで停止", "stopped by an API error"),
}


def _api_error_kind(text):
    if _ERR_LIMIT_RE.search(text or ""):
        return "limit"
    if _ERR_CTX_RE.search(text or ""):
        return "ctx"
    return "api"


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
    ai_title = None                # R123-A: 窓に ai-title 行が無ければ None（記憶を使う）
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
        if t == "ai-title":
            # R123-A: 自動タイトルは title に混ぜず aiTitle へ（表示側が title → aiTitle → プロジェクト名の順で使う）
            at = d.get("aiTitle")
            if isinstance(at, str):
                ai_title = short(" ".join(at.split()), 40)
        if t in ("user", "assistant"):
            skill_events.append(d)
            parsed.append(d)
            if len(parsed) > 120:
                parsed.pop(0)

    if not parsed:
        return None

    title = _remembered_title(str(path), custom_title, now, mtime)
    ai_title = _remembered_title(str(path), ai_title, now, mtime, memory=_AI_TITLE_MEMORY)
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

    # R102-4: 最後の行で「止まった理由」を読む（本人「作業中なのに指示待ち」「止まっているのに分からない」）。
    #   合成のエラー（isApiErrorMessage＝API エラー・利用上限・会話の長さの上限）→ err（種類の enum だけ・本文は持たない）
    #   中断（Esc＝「[Request interrupted by user…」・Stop フックは走らない）→ interruptedAt（ターンの追跡がこれより前なら閉じたもの）
    err = None
    interrupted_at = 0.0
    last = parsed[-1]
    if last.get("type") == "assistant" and last.get("isApiErrorMessage") is True:
        etext = " ".join(str(b.get("text") or "") for b in blocks(last)
                         if isinstance(b, dict) and b.get("type") == "text")
        err = {"k": _api_error_kind(etext), "ago": int(age)}
        status_verb, status_target, kind = _ERR_VERB[err["k"]](), "", "idle"
    elif last.get("type") == "user":
        c = (last.get("message") or {}).get("content")
        texts = [c] if isinstance(c, str) else [str(b.get("text") or "") for b in (c if isinstance(c, list) else [])
                                               if isinstance(b, dict) and b.get("type") == "text"]
        if any(t.startswith("[Request interrupted by user") for t in texts):
            interrupted_at = transcript_event_time(last, 0.0) or mtime
            status_verb, status_target, kind = L("中断しました", "interrupted"), "", "idle"

    if err or interrupted_at:
        state = "waiting" if age < 1800 else "resting"
    elif age < 25:
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
            # R100-0（Codex レビュー）: 答える先は最古の掲示（ask.askId）＝表示する質問も同じ 1 件に揃える。
            # transcript の質問（最新）が掲示と別物なら、掲示の質問文と選択肢を出す（見えている質問と違う要求へ答えさせない）。
            norm = lambda x: " ".join(nfc(str(x or "")).split())    # noqa: E731
            if question and ask["title"] and not norm(question).startswith(norm(ask["title"])[:40]):
                question, question_options = ask["title"], None
            question = question or ask["title"]
            if not question_options and ask["options"]:
                # ★形を必ず [{label, desc}] に揃える（掲示は素の文字列で持つ）。
                # 素の文字列のまま載せると **スマホの選択肢ボタンが1つも描かれない**
                # （PWAの questionOptionEntries が option.label を読む）＝R86-H で実際に踏んだ。
                question_options = [{"label": o, "desc": ""} for o in ask["options"]]
        elif not approval_min:
            approval_min = max(1, int((now - ask["ts"]) // 60))
        # 掲示されてからの分（質問・プランでは approvalMin が立たない＝スマホが「いま」「0分」と出していた）
        ask["min"] = max(0, int((now - ask["ts"]) // 60))
        if ask["kind"] == "question" and ask["tool"] == "AskUserQuestion" and "qn" not in ask:
            shape = _question_shape(_pending_questions(parsed, blocks), ask["title"])
            if shape:
                ask["qn"], ask["multi"] = shape        # 2 問以上・複数選択は 1 タップで答えさせない（スマホ）

    minions = 0
    minions_live = 0
    # 子 transcript の発見規則は office_term.subagent_files が正本（R113-B: スマホの kids と同じ規則）
    for fp in office_term.subagent_files(path):
        try:
            since = now - os.path.getmtime(fp)
            if since < 900:
                minions += 1
            if since < MINIONS_LIVE:
                minions_live += 1
        except OSError:
            pass

    employee = {
        "session": path.stem,
        "vendor": "claude",
        "cwd": cwd,
        "branch": branch,
        # R85-1: /rename のセッション名（無ければ""）。表示側が title || disp で名前を合成する。
        # disp・採番（N号）は無改変＝既存ピンを壊さない（設計= R85計画）。
        "title": title,
        # R123-A: Claude Code の自動タイトル（無ければ""）。Mac の画面だけ＝中継には載せない（relay_agent._LOCAL_ONLY_ENTRY）
        "aiTitle": ai_title,
        "age": int(age),
        "mtime": mtime,
        "state": state,
        # R50: 直近イベントの種別。UIが「会議室へ行く/考え込む」を決める材料。
        # テキストではなく分類なので中継へ流しても本文は漏れない。
        "kind": kind,
        "verb": status_verb,
        # R110: 最後に呼んだ工具の名前（Bash/Edit/Read/Grep/WebFetch/TodoWrite …）。本文ではなく分類＝部屋の
        # 「用事」（本棚へ・掲示板へ・机で打鍵）の根拠。言語に依らない（verb は表示用の訳語）。中継の allowlist には足さない。
        "tool": short(last_tool_name, 40),
        "target": status_target,
        "feed": recent_feed,
        "skills": skills,
        "minions": minions,
        # 直近 60 秒に書いた子だけ（minions は終わった子も 15 分数える＝「作業中」の根拠にはこちらを使う）
        "minionsLive": minions_live,
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
    if err:
        employee["err"] = err
    if interrupted_at:
        employee["interruptedAt"] = interrupted_at          # ローカルだけ（中継の allowlist に無い）＝ターンの追跡が読む
    if perm_mode in PERM_MODES:
        employee["permMode"] = perm_mode                    # R102-4: 端末の見出し（plan・自動で編集・確認なし）
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


def project_label(cwd, dirname, config, home_cwd=""):
    pat = project_config_key(cwd, config, dirname)
    if pat is None and home_cwd:
        pat = project_config_key(home_cwd, config)
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


def observed_peak(current):
    """同時に居たセッション数の最大値を更新して返す（増えたときだけ書く）。

    オフィスの広さはこの値で決まる（S≦8 / M≦12 / L≦18 / XL）。**増える方向にしか動かさない**:
    セッションを閉じるたびに机が消えると、動いている物が壊れたように見える。

    daemon と relay_agent は別プロセスで併走するので、読み→書きは flock で直列化する。
    直列化しないと 12 を書いた直後に 9 で上書きできる＝**部屋が縮む**（別モデルレビューが別プロセスで再現）。

    ここで `_lock`（スレッドロック）は**取らない**: この関数は `office_json()` が `_lock` を握ったまま
    呼ぶ経路にあり、`threading.Lock` は再入できないので自分で自分を待って**サーバーごと止まる**
    （実際に固まった）。flock は open ごとに効くのでスレッド間の直列化もこれで足りる。
    R115-S1 から走査は `_lock` の外（`_scan_lock` の中）で回る＝今は取っても固まらないが、要らないのは同じ。
    """
    try:
        cur = max(0, int(current))
    except (TypeError, ValueError):
        cur = 0

    def _read():
        try:
            data = json.loads(PEAK_FILE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return 0
        if not isinstance(data, dict):      # null / [] / "x" でも壊れない（手編集・復元の事故）
            return 0
        try:
            return max(0, int(data.get("maxSeen") or 0))
        except (TypeError, ValueError):
            return 0

    if cur <= _read():                      # 大半はここで終わる（読みだけ＝安い）
        return _read()
    try:
        with _file_flock(PEAK_FILE):
            peak = _read()                  # ロックの中で読み直す（これが無いと lost update）
            if cur <= peak:
                return peak
            PEAK_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = PEAK_FILE.with_name(f".{PEAK_FILE.name}.tmp")
            tmp.write_text(json.dumps({"maxSeen": cur}), encoding="utf-8")
            tmp.replace(PEAK_FILE)
            return cur
    except OSError:
        return max(_read(), cur)            # 書けなくても、この周の値は返す（画面は縮まない）


def events_wired(event="Stop"):
    """R90-D5: イベント記録 hook（hooks/office-event.sh）が ~/.claude/settings.json に配線済みか。
    Stop に1つでも office-event.sh の entry があれば true（17 イベント全部の検査は install.sh の仕事）。
    hook_installed() と同じく設定以外は読まない・壊れていれば false。
    event: 別のイベントの配線を見る（PermissionRequest を拾えるか＝許可の保留が分かるか）。"""
    settings_file = _HOME / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    groups = hooks.get(event) if isinstance(hooks, dict) else None
    if isinstance(groups, dict):
        groups = [groups]
    if not isinstance(groups, list):
        return False
    ours = 'bash "$HOME/.claude/hooks/office-event.sh"'      # hooks/install.sh の EV_CMD と同一文字列
    return any(isinstance(h, dict) and h.get("command") == ours
               and (not str(g.get("matcher") or "").strip() or g.get("matcher") == "*")   # installer の _unrestricted と同じ
               for g in groups if isinstance(g, dict)
               for h in (g.get("hooks") if isinstance(g.get("hooks"), list) else []))


def approval_wired():
    """R97-A: 許可プロンプトに答える hook（PermissionRequest・office-approval-wait.sh）が配線済みか。

    なぜ要るか: これまで office_json は Stop hook（配達）と 17 イベント（記録）しか見ておらず、
    UI の「📱 接続チェック」は承認 hook が未配線でも「✓ フックは配線済み」と言っていた。
    README は「権限プロンプトで止まった端末には PermissionRequest フックが答える」と書いているので、
    未配線の人は**書いてある機能が黙って効かない**状態に置かれる（2026-09-17 の棚卸し）。
    """
    settings_file = _HOME / ".claude" / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    groups = hooks.get("PermissionRequest") if isinstance(hooks, dict) else None
    if isinstance(groups, dict):
        groups = [groups]
    if not isinstance(groups, list):
        return False
    return any(isinstance(h, dict) and "office-approval-wait" in str(h.get("command") or "")
               for g in groups if isinstance(g, dict)
               for h in (g.get("hooks") if isinstance(g.get("hooks"), list) else []))


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


def present_growth(growth, roster, employees):
    """R123-C: growth.byProject を「いま居る人」の分だけにした**新しい** dict（growth_json の結果はキャッシュ＝書き換えない）。

    byProject は timeline に一度でも出たプロジェクト全部（session 単位の既定では 1 セッション 1 件）を持ち、
    使うほど際限なく太る（2026-11-09 実測 4,682 件・office_json 1.66 MB 中 1.59 MB・中継の荷物 231 KB 中 204 KB）。
    読み手（一覧のレベル・スマホのレベル・レベルアップの通知・3D の祝い）は全部いま居る人の id だけを引く。
    office（全体のレベル）・streak15 は全件から計算済みのまま。"""
    present = set()
    for row in [*(roster or []), *(employees or [])]:
        if isinstance(row, dict):
            present.update(v for v in (row.get("projectId"), row.get("session")) if isinstance(v, str) and v)
    by = growth.get("byProject") if isinstance(growth.get("byProject"), dict) else {}
    return {**growth, "byProject": {pid: row for pid, row in by.items() if pid in present}}


def _session_brief(e):
    """内訳表示用の最小情報。本文・パスは**構造的に持たない**（redactionに頼らない）。"""
    return {
        "session": e.get("session", ""),
        "vendor": e.get("vendor", "claude"),
        "state": e.get("state", ""),
        "age": int(e.get("age") or 0),
        "attention": bool(e.get("approvalMin") or e.get("question")),
        "minions": int(e.get("minions") or 0),
        "pending": bool(e.get("pending")),
        # R96-B 残: 非代表セッションを開いたときの配達表示（📴/届く）が代表の値にならないように、
        # 受信待機と「承認フックが待っている」だけを運ぶ。ask は tool 名と kind だけ（本文は持たない）。
        **({"listening": e["listening"]} if isinstance(e.get("listening"), bool) else {}),
        **({"ask": {"tool": str(e["ask"].get("tool") or ""), "kind": str(e["ask"].get("kind") or "")}}
           if isinstance(e.get("ask"), dict) else {}),
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
        elif mode == "session" or e.get("vendor") == "bot":
            # R126: Bot（出勤の口）は avatarMode=project でも常に 1 体 1 組（cwd が空＝同じ名前で畳まれるのを防ぐ）
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
            **({"arch": lead["arch"]} if "arch" in lead else {}),
            **({"color": lead["color"]} if "color" in lead else {}),
            "session": lead.get("session", ""),          # 代表＝指示の宛先
            "vendor": lead.get("vendor", "claude"),
            "name": lead.get("dept", ""),
            "role": lead.get("role", ""),
            "cwd": lead.get("cwd", ""),
            # branch/lastOrder/skills/avatar は R85-2 で撤去（roster に表示先ゼロの
            # デッドペイロードだった。employees[] 側は MCP が読むため維持）
            "crew": len(members),
            "state": state,
            "kind": lead.get("kind", "idle"),
            "verb": lead.get("verb", ""),
            "tool": lead.get("tool", ""),
            "target": lead.get("target", ""),
            "age": min(int(m.get("age") or 0) for m in members),
            "minions": sum(int(m.get("minions") or 0) for m in members),
            "minionsLive": sum(int(m.get("minionsLive") or 0) for m in members),
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
        if "bg" in lead:
            proj["bg"] = {field: copy.deepcopy(lead["bg"][field]) for field in
                          ("id", "name", "state", "detail", "tempo", "inFlight", "fan")
                          if field in lead["bg"]}
            if lead["bg"].get("detail"):
                proj["detail"] = lead["bg"]["detail"]
        if lead.get("external"):
            proj["external"] = lead["external"]
        if lead.get("questionOptions"):
            proj["questionOptions"] = lead["questionOptions"]
        # R102-4: 止まった理由（API エラー・上限・会話の長さ）と権限モードは代表の値（enum だけ）
        if isinstance(lead.get("err"), dict):
            proj["err"] = dict(lead["err"])
        if lead.get("permMode") in PERM_MODES:
            proj["permMode"] = lead["permMode"]
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
    R86-H2（2026-09-15）: 承認の掲示板 office_approvals/ も対象（幽霊の掲示・未消費の回答が残っていた）。
    ★消してよい根拠: pidfile は hook が毎周 touch する（寿命12時間）ので1日以上古いものは
    死骸。指示は TTL=3時間で hook 自身が捨てるので、その倍を過ぎたものは誰も受け取らない。
    """
    now = now or time.time()
    if now - _LAST_PRUNE[0] < _PRUNE_EVERY:
        return 0
    _LAST_PRUNE[0] = now
    if office_events is not None:
        office_events.prune(_HOME)
    removed = 0
    try:
        entries = list(INBOX.iterdir())
    except OSError:
        entries = []                     # inbox が無くても承認掲示板は掃除する（別モデルレビュー 2026-09-15）
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
    # R86-H2: 承認の掲示板も掃除する。掲示は心拍が止まって ASK_LITTER、回答/claim/tmp は REPLY_LITTER。
    # `_` 始まり（_e2e.log 等）は触らない。
    try:
        approvals = list(APPROVALS.iterdir())
    except OSError:
        approvals = []
    for f in approvals:
        try:
            if f.name.startswith("_"):
                continue
            age = now - f.stat().st_mtime
            if ".reply." in f.name or f.suffix == ".tmp":
                if age > REPLY_LITTER:
                    f.unlink(); removed += 1
            elif f.suffix == ".json":
                if age > ASK_LITTER:
                    f.unlink(); removed += 1
        except OSError:
            pass
    # R126: 出勤の口（期限の切れた相談を閉じて Bot の未読に expired を 1 件・14 日を過ぎた未読を捨てる）。
    # Bot が道具を呼ぶたびにも同じ掃除が走る＝ここは呼ばない Bot の分（数は removed に足さない＝ファイルを消した数ではない）
    try:
        office_door.sweep_all(_HOME, now)
    except Exception:
        pass
    return removed


def scan_office():
    now = time.time()
    prune_inbox_litter(now)
    # R102-4: 中継の配達員のプロセスでもイベントを読む（daemon ではスレッドが読むので何もしない）。OFFICE_EVENTS=0 は読まない
    #（main() がスレッドを起こさない条件と同じ＝無効化の設定を同期の読み取りで破らない・別モデルレビュー）
    refresh = getattr(office_events, "refresh", None) if office_events is not None else None
    if refresh is not None and os.environ.get("OFFICE_EVENTS") != "0":
        refresh(_HOME)
    config = load_config()
    global _LANG
    _LANG = office_lang(config)
    bg = source_claude_bg.bg_index(_HOME, now, lang=_LANG)  # plan.md #23: detail は ja のときだけ通す
    agents = (source_claude_bg.agents_cli(_HOME, now)
              if os.environ.get("OFFICE_AGENTS_CLI") != "0" else {})
    sources = {
        "claude": {"fg": 0, "bg": 0, "agentsCli": bool(agents)},
        "codex": {"connected": False, "n": 0, "reason": "disabled"},
        "openclaw": {"connected": False},
        # R126: 出勤の口。ok＝台帳が読めた・bots＝登録数・n＝載せた行・asks＝開いている相談（数だけ・中継には載せない）
        "door": {"ok": True, "bots": 0, "n": 0, "asks": 0},
    }
    setup = {"hookInstalled": hook_installed(), "eventsWired": events_wired(),
             "approvalWired": approval_wired()}
    # 許可の保留が記録に出る配線か（出ない配線で「道具が走っている」と言い切ると本物の許可待ちを隠す）
    holds_seen = setup["eventsWired"] and events_wired("PermissionRequest")
    employees = []
    scan_dirs = PROJECTS.iterdir() if PROJECTS.is_dir() else ()
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
                job = bg.lookup(info["session"]) or bg.lookup(f.stem)
                info = source_claude_bg.overlay(info, job, agents.get(info["session"]), now)
                # D6 接続口: overlay(info, home, now)。公式状態より強く、承認掲示より弱い。
                # 心拍だけが listening の正本なのでイベントでは上書きさせない。
                if office_events is not None and not info.get("ask"):
                    listening = info["listening"]
                    info = office_events.overlay(info, _HOME, now, asks_wired=setup["approvalWired"],
                                                 holds_seen=holds_seen)
                    if info.get("gone"): continue
                    info["listening"] = listening
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
                dept, role = project_label(info["cwd"], proj.name, config,
                                           home_cwd=(job or {}).get("cwd", ""))
                info["dept"] = dept
                info["role"] = role
                employees.append(info)
                sources["claude"]["bg" if info.get("bg") else "fg"] += 1
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
    oc_emps, _oc_meta = openclaw_source.openclaw_employees(_HOME, now, lang=_LANG)
    employees.extend(oc_emps)
    sources["openclaw"]["connected"] = bool(_oc_meta.get("connected"))

    # R126: 出勤の口（登録した常駐 Bot・スクリプト＝session "bot-<id>"・vendor "bot"・external は付けない）。
    # 行は office_door が作る（本文とパスの鍵は空・feed も空・名前は bot- の中で採番済み）。口のファイルが壊れていても
    # オフィスは落とさない（例外は握る＝sources.door.ok が False）。R127: 中継には relay_agent の Bot 用の狭い allowlist で載る
    #（名前・役・状態・verb の縮約・問いと選択肢・ask の id8＝不変条件 8 の例外）。sources.door は中継に出さない
    try:
        door_emps, door_meta = office_door.door_employees(_HOME, now, lang=_LANG, show_window=SHOW_WINDOW)
        sources["door"] = {"ok": bool(door_meta.get("ok")), "bots": int(door_meta.get("bots") or 0),
                           "n": len(door_emps), "asks": int(door_meta.get("asks") or 0)}
    except Exception:
        door_emps = []
        sources["door"] = {"ok": False, "bots": 0, "n": 0, "asks": 0}
    employees.extend(door_emps)

    source_config = config.get("sources")
    if not isinstance(source_config, dict):
        source_config = {}
    if (source_config.get("codex", True)
            and os.environ.get("OFFICE_SOURCES_CODEX") != "0"):
        codex_emps, codex_meta = source_codex.codex_employees(_HOME, now, lang=_LANG)
        codex_counts = {}
        for e in codex_emps:
            n = codex_counts.get(e["dept"], 0) + 1
            codex_counts[e["dept"]] = n
            e["disp"] = (e["dept"] if n == 1
                         else f"{e['dept']} #{n}" if _LANG == "en"
                         else f"{e['dept']} {n}号")
        employees.extend(codex_emps)
        sources["codex"] = {"connected": bool(codex_meta.get("connected")),
                            "n": len(codex_emps), "reason": codex_meta.get("reason", "")}

    # 送信履歴（配達状況つき・新しい順12件）＋ R86-I「今日のオフィス」の実数
    # （画面に載せるのは件数だけ＝本文は乗らない。台帳は50件保持なので大量に送った日は
    #  頭打ちになりうる＝その旨は表示側で断らず、素直に「直近50件のうち今日ぶん」を出す）
    all_hist = load_history()
    day_start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
    today_sent = sum(1 for h in all_hist if float(h.get("ts") or 0) >= day_start)
    last_ts = max((float(h.get("ts") or 0) for h in all_hist), default=0.0)
    today_view = {"sent": today_sent,
                  "answered": _today_answered(),      # R96-B: 今日答えた❗（daemon 側の一次情報＝端末ごとに数字が違わない）
                  "lastSentAgo": int(now - last_ts) if last_ts else None,
                  "capped": len(all_hist) >= 50}
    # plan.md #13: disp が引けなかった古い行は `session[:8]`（生のIDの断片）を名前のように見せていた＝
    # 「プロジェクト名(works)」形式の他の行と体裁が違う古い別形式の行として混ざって見えた（実データで確認）。
    # まず現在のスキャンから名前を引き直し（セッションがまだ在籍していれば直る）、それも無ければ
    # 生のID断片を出さずに行ごと「最近の指示」の対象から外す（正規の名前を言えないなら無言）。
    hist = []
    for h in reversed(all_hist[-12:]):
        session = h.get("session", "")
        disp = h.get("disp") or next((e["disp"] for e in employees if e["session"] == session), "")
        if not disp:
            continue
        hist.append({
            "session": session,
            "disp": disp,
            "text": h.get("text", ""),
            "ts": h.get("ts", 0),
            "pending": (INBOX / f"{session}.json").exists(),
        })

    # R50: 新UIが読む集約ビュー。employees[] は旧UIのために無改造で残す。
    # キー名は roster（"projects" は projects_index のローカルパス一覧が持つ名前。
    # 「office_json に projects を混ぜない」既存の privacy 番人と衝突させない）。
    # R86-A: 粒度は avatarMode（既定 session=1アバター=1セッション・ユーザー裁定）。
    mode = avatar_mode(config)
    # Explicit accessory overrides also apply to Codex and background worktrees.
    for e in employees:
        pat = project_config_key(e.get("cwd", ""), config)
        if pat is None:
            pat = project_config_key((e.get("bg") or {}).get("cwd", ""), config)
        meta = config.get("projects", {}).get(pat, {})
        if "arch" in meta and (meta["arch"] is None or
                               isinstance(meta["arch"], str) and meta["arch"] in PROJECT_ARCHES):
            e["arch"] = meta["arch"]
        if isinstance(meta.get("color"), str) and meta["color"] in PROJECT_COLORS:
            e["color"] = meta["color"]
    roster = group_by_project(employees, _LANG, mode=mode)
    # R91: 個体（セッション）単位の見た目はフォルダ設定より強い。本人指摘
    # 「統一で全部変わるぐらいだったらそこまでいらない」＝1体ずつ変えられるのが要件。
    avatars = config.get("avatars")
    if isinstance(avatars, dict):
        for proj in roster:
            style = avatars.get(proj.get("projectId"))
            if not isinstance(style, dict):
                continue
            # 型を確かめてから集合に照合する（手編集で [] が入ると
            # unhashable の TypeError で /api/office ごと落ち、UIから直せなくなる）
            if "arch" in style and (style["arch"] is None
                                    or (isinstance(style["arch"], str)
                                        and style["arch"] in PROJECT_ARCHES)):
                proj["arch"] = style["arch"]
            # arch と同じく None は「既定へ戻す」＝フォルダ設定も解除する
            #（さもないと「既定」を押しても色が残り、押しても何も起きないUIになる）
            if "color" in style:
                if style["color"] is None:
                    proj.pop("color", None)
                elif isinstance(style["color"], str) and style["color"] in PROJECT_COLORS:
                    proj["color"] = style["color"]

    # R79-10 遠隔実行: 許可リスト（表示用の最小ビュー＝argv/cwd/envは載せない）と実行結果。
    # caps はUIの機能ゲート（旧Macでは▶実行ボタンを出さない＝版ズレ耐性）。
    recipes, _recipe_errors = office_actions.load_recipes()
    devices_now = load_devices()
    actions_view = {
        "recipes": office_actions.recipes_public(recipes),
        "results": office_actions.results_public(),
        # R87: 会話ビューアは「封じられる Mac」かつ「本人が ON にした」ときだけ出す。
        # 使えない環境では機能ごと消す＝平文で送るフォールバックは存在しない。
        "caps": {"actions": 1, "ws": 1,
                 "dialog": 1 if (dialog_seal.AVAILABLE and config.get("dialogRelay") is True) else 0,
                 # R100-1b: 封じられる ∧ ON の期限内 ∧ 有効な許可鍵が 1 本以上（relay_agent のプロセスで組む＝
                 # 未再起動の旧い relay_agent からは出ない＝版ズレは自動で「出さない」側）
                 "approve": office_approve.caps_approve(devices_now, dialog_seal.AVAILABLE)},
        # R100-1b: ON か・有効な許可鍵の照合コード（秘密ではない）。端末は自分の fp があるかで「登録済みか」を知る
        # R116-S1b: rk＝Mac で［延長］が押されて窓が開いている端末 id（スマホは自分の id があれば新しい秘密を求める）
        "approve": {**office_approve.approve_public(devices_now), "rk": rekey_pending(devices_now)},
    }
    # R80: 中継の今日の使用量（未設定/古い＝None＝UIは何も出さない）
    relay_view = relay_usage()
    event_seq = office_events.seq() if office_events is not None else 0
    last_events = office_events.recent(since_seq=event_seq - 1, limit=1) if event_seq else []

    # D8: local-only material for digest/XP; failures never affect office_json.
    # R126: Bot（出勤の口）の稼働は自己申告＝セッションの稼働分・XP に混ぜない（Bot の数え方は R132 で別に足す）
    try:
        timeline = sys.modules.get("office_timeline")
        if timeline is not None:
            project_ids = {member["session"]: project["projectId"]
                           for project in roster for member in project["sessions"]}
            timeline.record_states([dict(e, projectId=project_ids.get(e["session"],
                project_id_for(e.get("cwd", ""), e.get("dept", "")))) for e in employees
                if e.get("vendor") != "bot"], now)
    except Exception:
        pass

    # R98: 証拠列（台帳の「証拠」）。直近 24h の hook 記録から committed/tested/failed を付ける。
    # 中継の allowlist には載せない（既定で非搬送）。失敗しても office_json は落とさない。
    try:
        evidence = office_timeline.evidence_for(_HOME, [e.get("session", "") for e in employees], now)
        for e in employees:
            hit = evidence.get(e.get("session", ""))
            if hit:
                e["evidence"] = hit
    except Exception:
        pass

    try:
        growth = office_timeline.growth_json(_HOME, now)
    except Exception:
        growth = office_timeline.growth_from_events([])
    if isinstance(growth, dict):
        growth = present_growth(growth, roster, employees)
        growth["maxSeen"] = observed_peak(len(employees))

    return {
        # R90-D12: スキーマ版。docs/office-json.md が正本（v2 = sources/events/growth を持つ形）。
        "v": 2,
        # R97-D: 動いているコードの版。正本は server/office_version.py の 1 箇所だけ
        #（`"v"` はデータ形式の版で別物）。**中継の allowlist には足さない**＝新フィールドは
        # 既定でスマホに載せない、という不変条件をここでも守る（docs/office-json.md）。
        "app": {"version": office_version.VERSION},
        "officeName": config.get("officeName") or default_office_name(_LANG),
        "employees": employees,
        "sources": sources,
        "roster": roster,
        "history": hist,
        "today": today_view,
        "generatedAt": now,
        "setup": setup,
        "events": {"seq": event_seq, "wired": setup["eventsWired"],
                   "lastTs": last_events[0]["ts"] if last_events else 0},
        "growth": growth,
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
        # R102-5: Mac の前に人がいる（1）・いない（0）。読めなければキーごと無し
        **({"macActive": ma} if (ma := mac_active(now)) is not None else {}),
    }


_cache = {"t": 0.0, "data": None}
_OPENCLAW_CACHE_SEC = 60.0
_openclaw_cache = {"at": None, "data": None}
_lock = threading.Lock()
# R115-S1: 走査（scan_office）は request thread のロックの外で回す。`_lock` は `_cache` と `_SCAN` の読み書きだけを守り、
# 走査の single flight（同時に 1 本だけ）は `_scan_lock` が持つ（順序は常に _scan_lock → _lock・逆は取らない）。
# _SWR（stale-while-revalidate）は main() が serve する間だけ True＝relay_agent・tests・--dump は従来どおり同期。
_SWR = False
# 古い値を返してよいのは、最後の走査が終わってから STALE_MAX 秒まで（UI・プローブの短い間隔をつなぐだけ）。それより後なら
# 呼び手が走査を待つ＝60 秒おきの見張り（_watch_loop の ❗ 通知）や間を空けた 1 回の読みが、古いままの値を掴まない。
STALE_MAX = 5.0
_scan_lock = threading.Lock()
# seq＝終わった走査の数（走査を待っていた呼び手が同じ 1 回の結果を受け取る印）・at＝最後の走査が終わった時刻・
# busy＝裏の走査が予約/実行中・thread＝その thread（テストが join する）・wseq＝ローカルの書き込みの数（_mark_written）・
# dseq＝今の値の走査を始めた時点の wseq（wseq と違えば、その値は書き込みの前＝古い値として返さない）・
# fails＝失敗した走査の数（poke の待ちと追いかけが「待つ間に失敗した＝起こし直さない」を見る印・差だけを見る）・
# slow＝SCAN_SLOW 秒を超えた走査の数
_SCAN = {"seq": 0, "at": 0.0, "busy": False, "thread": None, "wseq": 0, "dseq": 0, "fails": 0, "slow": 0}
_SCANNING = -1.0     # 走査中の _cache["t"]（古い扱い）。走査の間に t=0（無効化）が来たら、その結果を新鮮と言わない
# R115-S1b: 走査が終わった（値・busy が変わった）合図。SSE の poke の前に新しい値を待つ _wait_fresh が使う（ロックは _lock）
_scan_done = threading.Condition(_lock)
POKE_FRESH_WAIT = 2.0
# 反証 R115 #2: 走査し直しが POKE_FRESH_WAIT に間に合わずに先に送った poke は、走査し直しが終わったら同じ seq でもう 1 回送る
# （UI は poke ごとに取り直す）。追うのは POKE_FOLLOW_WAIT 秒まで（UI のポーリングが 15 秒）・購読キューを TICK 秒おきに見る
POKE_FOLLOW_WAIT = 15.0
POKE_FOLLOW_TICK = 0.1
# 反証 R115 #5: これを超えた走査は「office の走査が遅い」を 1 行ログに出して数える。1 秒おきのプローブは SWR の速い道
# （STALE_MAX の内側＝前の値を即返す）しか通らず走査の遅さを測れない＝受け入れの「1 秒超 0 回」は走査の側で数える
SCAN_SLOW = 1.0


def _invalidate_cache():
    with _lock:
        _cache["t"] = 0.0


def _mark_written():
    """ローカルの書き込み（設定・送信履歴）の直後に **`_lock` の中で**呼ぶ。R115-S1: 次の office_json は書き込みの後に
    始めた走査の値を返す（read-your-writes＝言語を切り替えた直後の画面が前の言語に戻らない）。古い値を返す SWR は
    イベント（_invalidate_cache）と時間切れのときだけ。"""
    _cache["t"] = 0.0
    _SCAN["wseq"] += 1


# R103-5: AI サービス・API の鍵の名前は provider_registry が正本（新しいプロバイダは表に 1 行足すだけで保存できる）
_KEY_NAMES = frozenset({"OPENAI_API_KEY", "X_BEARER_TOKEN"}) | status_board.provider_registry.KEY_NAMES
_KEY_VALUE = re.compile(r"^[A-Za-z0-9%_\-\.=/+]{20,300}$")


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
        # R103-5: AI サービス・API の行は provider_registry から作る（keyName＝保存する鍵の名前・kind・measures）
        *[{"id": pr["id"], "label": L_now(*pr["panel"]), "mode": "key", "kind": pr["kind"],
           "measures": list(pr["measures"]), "keyName": pr["key"],
           "connected": bool(key_values[pr["key"]]), "masked": masked(pr["key"]),
           "hint": L_now(*pr["hint"]), "ph": pr["ph"], "getFrom": pr["getFrom"],
           "budget": pr["id"] in status_board.API_PROVIDER_IDS}
          for pr in status_board.provider_registry.PROVIDERS],
    ]}


_PROVIDER_TEST_AT = {}


def provider_test(data):
    """R103-5: POST /api/providers/test {id, value?}。保存の前に 1 回だけ相手へ問い合わせて「つながるか」を返す。
    value 無し＝保存済みの鍵で。鍵は応答・ログに出さない（結果は ok と言葉だけ）。同じプロバイダは 3 秒に 1 回。"""
    reg = status_board.provider_registry
    pid = data.get("id") if isinstance(data, dict) else None
    pr = reg.spec(pid) if isinstance(pid, str) else None
    if not pr:
        return False, "プロバイダが不正です", {}
    value = data.get("value")
    if value in (None, ""):
        value = _office_key_line(pr["key"])[1]
        if not value:
            return False, L_now("鍵がまだ保存されていません", "No key saved yet"), {}
    elif not isinstance(value, str) or not _KEY_VALUE.fullmatch(value):
        return False, "キー値の形式が不正です", {}
    now = time.time()
    with _lock:
        if now - _PROVIDER_TEST_AT.get(pid, 0) < 3:
            return False, L_now("少し待ってからもう一度", "Wait a moment and try again"), {}
        _PROVIDER_TEST_AT[pid] = now
    fake = os.environ.get("OFFICE_FAKE_PROVIDER_TEST")      # テスト専用（UI スモークが外へ問い合わせない）: ok / fail
    if fake in ("ok", "fail"):
        ok, msg = (True, L_now("つながりました（テスト）", "Connected (test)")) if fake == "ok" else \
            (False, reg.error_text("HTTP 401", L_now("ja", "en") == "ja"))
    else:
        try:
            ok, msg = reg.test(pid, value, status_board._fetch_json, status_board.api_fetcher(pid), now,
                               lang_ja=L_now("ja", "en") == "ja")
        except Exception:                                   # 例外の本文（鍵を含みうる）は返さない・応答は必ず JSON
            ok, msg = False, reg.error_text("", L_now("ja", "en") == "ja")
    return True, "", {"result": {"ok": bool(ok), "message": msg}}


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
    """いまの office（CACHE_SEC 秒のキャッシュ）。R115-S1: 走査の間 `_lock` を握らない（全 request thread が待っていた）。
    新鮮 → そのまま返す／古くて前の値がある → daemon（_SWR）では裏で 1 本だけ走査を起こして前の値を即返す（鮮度は generatedAt）／
    値が無い（冷間）・前の値がローカルの書き込みより前・STALE_MAX より古い・_SWR でない（relay_agent・tests・--dump）
    → 呼び手が走査する（同時の呼び手は同じ 1 回の結果を受け取る）。"""
    with _lock:
        data = _cache["data"]
        now = time.time()
        if data is not None and now - _cache["t"] <= CACHE_SEC:
            return data
        seq = _SCAN["seq"]
        stale_ok = (_SWR and data is not None and _SCAN["dseq"] == _SCAN["wseq"]
                    and now - _SCAN["at"] <= STALE_MAX)
        kick = stale_ok and not _SCAN["busy"]
        if kick:
            _SCAN["busy"] = True
    if not stale_ok:
        return _scan_into_cache(seq)
    if kick:
        try:
            th = threading.Thread(target=_revalidate, args=(seq,), name="office-scan", daemon=True)
            _SCAN["thread"] = th
            th.start()
        except RuntimeError:                    # スレッドを起こせない＝前の値を返し、次の呼び出しで再試行
            with _lock:
                _SCAN["busy"] = False
    return data


def _scan_into_cache(seq, bg=False):
    """走査して `_cache` に置き、その結果を返す（single flight＝走査は同時に 1 本）。走査を待っている間に別の走査が
    終わったら（seq が進んだら）その結果を返す＝冷間の同時呼び出しは 1 回の走査を共有する（その走査の途中にイベントの
    無効化が来ていても。無効化は次の呼び出しが拾う）。ただしローカルの書き込みより前に始めた走査の結果は共有しない。
    走査の例外は前の値と時刻を保ったまま呼び手へ（fails を 1 つ進める）。SCAN_SLOW 秒を超えた走査は 1 行ログに出して
    slow を数える（bg＝裏の走査・それ以外は呼び手が待った同期の走査）。"""
    with _scan_lock:
        with _lock:
            data = _cache["data"]
            if data is not None and _SCAN["dseq"] == _SCAN["wseq"] and (
                    _SCAN["seq"] != seq or time.time() - _cache["t"] <= CACHE_SEC):
                return data
            before = _cache["t"]
            _cache["t"] = _SCANNING
            wseq = _SCAN["wseq"]
        started = time.monotonic()
        try:
            data = scan_office()
        except BaseException:
            with _lock:
                _SCAN["fails"] += 1
                if _cache["t"] == _SCANNING:
                    _cache["t"] = before
                _scan_done.notify_all()
            raise
        took = time.monotonic() - started
        with _lock:
            done = time.time()
            _cache["data"] = data
            _SCAN.update(seq=_SCAN["seq"] + 1, at=done, dseq=wseq, slow=_SCAN["slow"] + int(took > SCAN_SLOW))
            if _cache["t"] == _SCANNING:        # 走査の間に無効化（t=0）が来ていれば古いまま＝次の呼び出しで走査し直す
                _cache["t"] = done
            _scan_done.notify_all()
    if took > SCAN_SLOW:
        print(f"⚠ office の走査が遅い: {took:.2f} 秒（{'裏' if bg else '同期'}）", file=sys.stderr, flush=True)
    return data


def _revalidate(seq):
    """裏の走査（_SWR のときだけ office_json が 1 本起こす）。失敗しても前の値は保ち、旗を戻して次の呼び出しで再試行する。"""
    try:
        _scan_into_cache(seq, bg=True)
    except Exception as e:                      # noqa: BLE001 - 裏の走査は request thread を巻き込まない
        print(f"⚠ office の裏の走査が失敗（前の値を返し続け、次の呼び出しで再試行）: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
    finally:
        with _lock:
            _SCAN["busy"] = False
            _scan_done.notify_all()


def _wait_fresh(timeout):
    """R115-S1b: SSE の poke（UI はこれを受けて /api/office を取り直す）の前に、無効化された値（t=0）・走査中（_SCANNING）が
    走査し直されるのを待つ（最大 timeout 秒・走査が起きていなければ 1 本起こす）。office_events._ingest は「キャッシュを失効させて
    から poke」の順を守る（Astra レビュー）＝poke の後の取り直しはイベントの後の値を受け取る、という約束を SWR でも保つ。
    待つのは SSE の thread だけ（GET は待たせない）。時間切れだけで古い値（t>0）は待たない。旗 OFF は何もしない。
    反証 R115 #1: 待つ間に走査が失敗したら（fails が進んだら）起こし直さずに False（再試行は次の GET）。前は失敗した走査を
    期限まで起こし直し続けた＝走査が毎回落ちる状態（UTF-8 でない office_config.json・手編集の型の誤り）では poke 1 回で
    数万回の走査とログ数 MB・CPU 1 コア。今、1 回の待ちで起こし直すのは「走査は成功したが、その途中に次の失効が来た」ときだけ。"""
    if not _SWR:
        return True
    deadline = time.monotonic() + timeout
    with _scan_done:
        fails = _SCAN["fails"]
        while _cache["t"] <= 0.0:
            if _SCAN["fails"] != fails:
                return False
            if not _SCAN["busy"]:
                _SCAN["busy"] = True
                try:
                    th = threading.Thread(target=_revalidate, args=(_SCAN["seq"],), name="office-scan", daemon=True)
                    _SCAN["thread"] = th
                    th.start()
                except RuntimeError:
                    _SCAN["busy"] = False
                    return False
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _scan_done.wait(left)
    return True


def _scan_marks():
    """(終わった走査の数, 失敗した走査の数)＝poke の追いかけ（_fresh_since）の起点。"""
    with _lock:
        return _SCAN["seq"], _SCAN["fails"]


def _fresh_since(marks):
    """反証 R115 #2: 時間切れで先に送った poke を追いかけるかの判定（SSE の thread が POKE_FOLLOW_TICK 秒おきに呼ぶ・走査は起こさない）。
    True＝起点の後に走査が成功し、値が失効していない（t>0 は失効の後に始めた走査の値＝取り直しはイベントの後の値）／
    False＝起点の後に走査が失敗した（追わない・再試行は次の GET）／None＝まだ。失敗が前の時刻（t>0）を戻しても成功とは言わない。"""
    seq, fails = marks
    with _lock:
        if _SCAN["seq"] != seq and _cache["t"] > 0.0:
            return True
        if _SCAN["fails"] != fails:
            return False
        return None


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
        _mark_written()


# R90-D10: Codex CLI のセッション（session="cx-<thread id>"）への配達。
# Stop hook（office_inbox）は Claude Code 固有の経路なので使えない。Codex CLI 0.149+ の
# `codex queue --thread <id> --message <text>` が「実行中セッションへ外部から指示を積む」公式の口。
#   - argv 固定・shell=False・timeout・start_new_session（office_actions と同じ Popen の掟）
#   - text は `--message=<text>` の結合形で渡す（先頭が `-` の本文をオプションと誤認させない）
#   - rc≠0 は正直に失敗を返す（セッション未起動＝届かない。inbox に溜めて「あとで届く」ふりをしない）
#   - 常駐（launchd）の PATH には codex が無いことが多いので実体を自前で解決する
CODEX_QUEUE_TIMEOUT = float(os.environ.get("OFFICE_CODEX_QUEUE_TIMEOUT") or 20)
SSE_WRITE_TIMEOUT = float(os.environ.get("OFFICE_SSE_WRITE_TIMEOUT") or 20)   # R90-D7: 読まないクライアントを切る
_FIXED_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")


class CodexUnavailable(OSError):
    """R90-D10: Codex への**一時的な**配達失敗（応答なし・起動不能）。OSError 派生にしておくと
    relay_agent の既存規則「OSError＝残置して次周で再配達（ack しない）」に乗る＝指示を失わない。
    rc≠0（セッション未起動）や入力不正は恒久＝False で返す（relay は ack して落とす）。"""


def _child_env(bin_):
    """子プロセスの最小 env。PATH は「実体のディレクトリ → 親の PATH → 既知の固定 dir」の順（重複除去）。
    npm 版の codex/claude は `#!/usr/bin/env node` の Node ラッパーなので、nvm 等で入れた node が
    固定 PATH から見えないと必ず失敗する（Astra レビュー指摘）。親の PATH を残せば daemon（launchd）
    でも plist の PATH が引き継がれる。秘密になりうる他の env は渡さない。"""
    dirs = []
    for d in ([os.path.dirname(bin_)] if bin_ else []) + os.environ.get("PATH", "").split(":") + list(_FIXED_PATH):
        if d and d not in dirs:
            dirs.append(d)
    node = shutil.which("node")
    if node and os.path.dirname(node) not in dirs:
        dirs.append(os.path.dirname(node))
    return {"PATH": ":".join(dirs), "HOME": str(Path.home()), "LANG": "en_US.UTF-8", "TERM": "dumb"}


def _run_child(argv, timeout, cwd=None):
    """argv 固定・shell=False・独立プロセスグループで実行し (rc, stdout, stderr) を返す。
    タイムアウト時は**プロセスグループごと** SIGKILL（npm ラッパーが spawn する実体＝孫まで止める）して
    subprocess.TimeoutExpired を上げる。OSError（起動不能）はそのまま上げる。"""
    proc = subprocess.Popen(argv, cwd=cwd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, start_new_session=True, env=_child_env(argv[0]))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

_CODEX_BIN_CANDIDATES = ("~/.npm-global/bin/codex", "/opt/homebrew/bin/codex", "/usr/local/bin/codex")


def _codex_bin():
    """codex の実体。OFFICE_CODEX_BIN（テスト注入口）> PATH > 既知の置き場。無ければ None。"""
    override = os.environ.get("OFFICE_CODEX_BIN")
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("codex")
    if found:
        return found
    for cand in _CODEX_BIN_CANDIDATES:
        p = os.path.expanduser(cand)
        if os.access(p, os.X_OK):
            return p
    return None


def _deliver_codex(session, text):
    thread = session[3:]
    if not re.fullmatch(r"[A-Za-z0-9-]{4,61}", thread):
        return False, "session id が不正です"
    fake = os.environ.get("OFFICE_FAKE_CODEX")
    if fake:
        Path(fake).write_text(json.dumps({"thread": thread, "text": text}, ensure_ascii=False),
                              encoding="utf-8")
        _record_instruction_history(session, text)
        return True, L_now("Codex のキューに積みました（次のターンで読まれます）",
                           "Queued to Codex (read on its next turn)")
    bin_ = _codex_bin()
    if not bin_:
        # 未導入は恒久（再試行しても直らない）＝False で返す
        return False, L_now("codex コマンドが見つかりません（Codex CLI が未導入か PATH 外）",
                            "codex CLI not found (not installed or outside PATH)")
    argv = [bin_, "queue", "--thread", thread, "--message=" + text]
    try:
        rc, _out, _err = _run_child(argv, CODEX_QUEUE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise CodexUnavailable("codex queue timed out")
    except OSError as e:
        raise CodexUnavailable(f"codex queue could not start: {e}")
    if rc != 0:
        return False, L_now("Codex へ届けられませんでした（セッションが起動していない可能性）",
                            "Could not reach Codex (the session may not be running)")
    _record_instruction_history(session, text)
    return True, L_now("Codex のキューに積みました（次のターンで読まれます）",
                       "Queued to Codex (read on its next turn)")


# R126: post_instruction の呼び手。local＝Mac の画面（/api/instruct・loopback＋CSRF）・relay＝スマホ（署名を確かめた中継の封筒）・
# mcp＝セッション（MCP の office_instruct）。Bot（bot-）へ渡せるのは office_door.HUMAN_SRCS（local・R127 から relay も）だけ
# ＝セッションの文を Bot に入れない。
INSTRUCT_SRCS = ("local", "relay", "mcp")


def post_instruction(session, text, *, src, req_id=None):
    """指示を投函（Stop hook の office-inbox-wait.sh が配達する）。
    R42.5: oc-宛（OpenClaw・別Mac）は office_inbox でなく OC_OUTBOX へ＝relay_agent が
    中継の site=macmini キューへ署名転送する（バリデーション・履歴は両経路共通）。
    R126: `src`（誰が投函したか）はキーワード専用・既定値なし（write_approval_reply と同じ流儀＝呼び忘れた経路が
    黙って「人」にならない）。bot-（出勤の口の Bot）宛は口の inbox へ＝人の呼び手（office_door.HUMAN_SRCS）だけ。
    R127: req_id は bot- 宛だけが使う 1 通ごとの冪等の id（relay_agent がスマホの封筒の端末と nonce から作る＝再配達で二重に入れない）。"""
    if src not in INSTRUCT_SRCS:
        raise ValueError(f"unknown instruction source: {src!r}")
    if not re.fullmatch(r"[a-zA-Z0-9-]{8,64}", session or ""):
        return False, "session id が不正です"
    text = (text or "").strip()
    if not text:
        return False, "指示が空です"
    if len(text) > 4000:
        return False, "指示が長すぎます(4000字まで)"
    if "\x00" in text:
        return False, "指示に使えない文字（NUL）が含まれています"
    if _is_bot_session(session):
        # R126: セッション（MCP）の文が「人の指示」として Bot に入り、のちに中継で外へ出るのを止める（構造で）。
        # R127: スマホ（relay）も人＝相談が閉じているときの言葉は指示として口へ（開いている間は口が断る＝答えは id8 の枝だけ）。
        if src not in office_door.HUMAN_SRCS:
            office_door.audit_event("refuse", session[len(office_door.SESSION_PREFIX):], home=_HOME,
                                    op="instruct", why="src", src=src)
            return False, L_now("Bot へはセッション（MCP）からは指示できません（Mac の画面かスマホから）",
                                "Bots take instructions only from a person (the Mac screen or the phone), not from sessions")
        bot_id = office_door.bot_of_session(session)
        if bot_id is None:
            return False, "session id が不正です"
        res = office_door.instruct(bot_id, text, src=src, home=_HOME, req_id=req_id, lang=office_lang())
        if res.get("ok") and not res.get("dup"):          # 同じ封筒の再配達（dup）は履歴にも二度書かない
            _record_instruction_history(session, text)
        return bool(res.get("ok")), str(res.get("text") or "")
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
    if session.startswith("cx-"):
        return _deliver_codex(session, text)
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


def answer_bot(session, data):
    """R127: Mac の質問カードから Bot（出勤の口）の相談へ答える（/api/approval/reply の bot- の枝）。→ (ok, msg, extra)。
    言葉だけ＝behavior が "deny"（カードの文章の答え）以外は断る＝allow は 400（Bot は許可を出せない・不変条件 7・監査に refuse）。
    答える相手は askId（32 桁）で 1 件に縛る＝表示していない相談・もう閉じた相談への答えは口が捨てて監査に残す（指示に落とさない）。
    掲示板（office_approvals/）は読まない・書かない。選択肢の文は口が choice に解決する。"""
    bot = session[len(office_door.SESSION_PREFIX):]
    behavior = data.get("behavior")
    if behavior not in (None, "", "deny"):
        office_door.audit_event("refuse", bot, home=_HOME, op="answer", src="local",
                                why="allow" if behavior == "allow" else "behavior")
        return False, L_now("Bot には許可を出せません（答えは言葉だけ）",
                            "A bot cannot be given permission (answer in words)"), {}
    bot_id = office_door.bot_of_session(session)
    if bot_id is None:
        return False, "session id が不正です", {}
    ask_id = data.get("askId")
    if not (isinstance(ask_id, str) and office_door.ASK_ID_RE.fullmatch(ask_id)):
        return False, L_now("画面を読み込み直してください（どの相談への答えか分かりません）",
                            "reload the page (the answer does not name its question)"), {}
    text = data.get("message")
    res = office_door.answer(bot_id, ask_id, text if isinstance(text, str) else "", src="local",
                             home=_HOME, lang=office_lang())
    if not res.get("ok"):
        return False, str(res.get("text") or ""), {}
    with _lock:
        _mark_written()           # 答えた直後の画面に、閉じた相談をもう出さない（read-your-writes）
    extra = {"kind": "question", "askId": res.get("askId")}
    if res.get("choice"):
        extra["choice"] = res["choice"]
    return True, L_now("答えを届けました", "answer delivered"), extra


# ---- R128 #4: Bot パネル（⚙ の一覧・追加・削除・消音＝Mac の前の人だけ・正本= docs/door.md §10） ----
# 一覧は GET /api/door/bots（loopback＋CSRF）。書き換えは下の 3 本の POST（_STRICT_POST＝loopback＋X-Office-Local＋Origin 必須）。
# 中継（relay_agent）・MCP・Bot の道具には道が無い（Bot の登録・削除・消音は人の操作）。入力の検査と断りの理由は office_door
#（code と人が読む文をそのまま返す）。office_json には載せない＝登録コマンドのパスも中継へ流れる経路が無い。
DOOR_BOT_ROUTES = ("/api/door/bot/add", "/api/door/bot/remove", "/api/door/bot/mute")


def door_bots_json(now=None):
    """GET /api/door/bots の中身。{ok, ledger（台帳が読めたか）, max, bots:[{bot, session, name, role, added, lastSeen, age, ask,
    unread, muted, mcp}]}。age＝最後に動いてからの秒（一度も動いていなければ None）・mcp＝その Bot を MCP として登録する 1 行
    （office_door.mcp_command＝この daemon の python3 の実体と隣の mcp_office.py・valid_id を通った id だけ）。読むだけ。"""
    now = time.time() if now is None else now
    view = office_door.bots_overview(home=_HOME, now=now)
    bots = []
    for b in view["bots"]:
        seen = b.get("lastSeen") or 0.0
        bots.append(dict(b, age=max(0, int(now - seen)) if seen > 0 else None, mcp=office_door.mcp_command(b["bot"])))
    return {"ok": True, "ledger": bool(view["ok"]), "max": view["max"], "bots": bots}


def door_bot_post(route, data):
    """POST /api/door/bot/{add,remove,mute} → (HTTP の番号, 返す JSON)。断りは 400 と {ok:false, code, error＝口の文}。
    add＝{bot, name?, role?}（同じ id は名前と役の直し）・remove＝{bot}（状態と記録も消す）・mute＝{bot, on: true|false}。"""
    lang = office_lang()
    bot = data.get("bot")
    if route == "/api/door/bot/add":
        res = office_door.add_bot(bot, data.get("name"), data.get("role"), home=_HOME, lang=lang)
        if res.get("ok"):
            res["mcp"] = office_door.mcp_command(res["bot"])
    elif route == "/api/door/bot/remove":
        res = office_door.remove_bot(bot, home=_HOME, lang=lang)
    else:
        res = office_door.set_muted(bot, data.get("on"), home=_HOME, lang=lang)
    if not res.get("ok"):
        return 400, {"ok": False, "code": str(res.get("error") or "refused"), "error": str(res.get("text") or "")}
    with _lock:
        _mark_written()           # 直した直後の /api/office に今の登録と消音を出す（read-your-writes）
    return 200, res


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


def set_lang(value):
    """言語設定をプロセス間で直列化して保存し、表示キャッシュを失効する。"""
    if value not in LANGS:
        return False, "lang must be ja/en"
    with _lock, _file_flock(config_file()):
        cf = config_file()
        try:
            cfg = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {"projects": {}}
        except (OSError, json.JSONDecodeError):
            return False, "office_config.json が読めません（壊れている可能性・手動確認を）"
        if not isinstance(cfg, dict):
            return False, "office_config.json が読めません（壊れている可能性・手動確認を）"
        cfg["lang"] = value
        _write_config(cfg)
        _mark_written()
    return True, value


def set_dialog_relay(on):
    """R87: スマホの会話ビューアを許可するか（既定 OFF）。loopback+CSRF 配下からしか変えられない
    ＝遠隔から設定を変える経路は無い（許可リストと同じ思想）。"""
    if not isinstance(on, bool):
        return False, "dialogRelay must be true/false"
    with _lock, _file_flock(config_file()):
        cf = config_file()
        try:
            cfg = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {"projects": {}}
        except (OSError, json.JSONDecodeError):
            return False, "office_config.json が読めません（壊れている可能性・手動確認を）"
        if not isinstance(cfg, dict):
            return False, "office_config.json が読めません（壊れている可能性・手動確認を）"
        cfg["dialogRelay"] = on
        _write_config(cfg)
        _mark_written()
    return True, on


# R100-1a: 封書の種類（閉じた集合）。relay_agent の DLG_KINDS と同じ値（test_relay_agent がピン）。
SEALED_KINDS = frozenset({"dialog", "term", "ask"})     # R100-1b: ask＝許可の全文（登録した端末だけ）


def _dialog_sealed(data):
    """R87: dlg-封筒（署名検証は relay_agent 側で完了済み）に応えて、会話を**封じて**返す。
    (ok, msg, extra)。err は固定 enum（dialog_seal.ERRORS）だけ＝自由文字列を中継へ出さない。

    順序（1つでも外れたら封じない）:
      形式 → session 形式 → 封緘バックエンド → 本人の許可（dialogRelay）→ 端末の失効を**再検査**
      → 同じ reqId は保存済みを返す（再配達で二重に読まない）→ transcript → page → seal → 保存 → 監査。
    **生の page を返す枝は作らない**（relay_agent に平文を載せない）。
    """
    act = data.get("action") if isinstance(data.get("action"), dict) else data
    req_id = str(act.get("reqId") or "")
    session = str(act.get("session") or "")
    device = str(data.get("device_id") or "")[:32]
    kind = act.get("kind")
    # R100-1a: 封書の種類は閉じた集合（dialog＝会話ビュー／term＝ターミナル画面）。他は恒久拒否。
    if (act.get("aioffice") != 1 or kind not in SEALED_KINDS
            or not dialog_seal.REQID_RE.match(req_id)):
        return False, "dialog request malformed", {"state": "denied"}
    if not _SESSION_ID_RE.fullmatch(session):
        return False, "bad session", {"state": "denied"}
    iat = int(time.time())

    # R100-1a: ターミナル画面は数秒おきに要求する＝同じ端末の term 封書は最新 1 通だけ残す
    # （MAX_LIVE=4 の枠を会話ビューと食い合わない）。R100-1b: 許可の全文（ask）も端末ごとに 1 通。
    replace_key = f"{kind}:{device}" if kind in ("term", "ask") else None

    def refuse(err):
        bundle = {"v": 1, "id": req_id, "e": iat + dialog_seal.BUNDLE_TTL, "err": err}
        # 端末に「理由」が届くように保存する（許可の全文の理由は急ぎ＝中継の減速中でも届ける）
        dialog_seal.store_bundle(req_id, bundle, replace_key=replace_key, urgent=kind == "ask")
        office_actions._audit({"kind": kind, "reqId": req_id, "device": device,
                               "session": session, "ts": iat, "state": err})
        return True, err, {"bundle": bundle}

    if not dialog_seal.AVAILABLE:
        return refuse("unavailable")
    config = load_config()
    # R100-1b: 許可の全文は「会話を見せる」が OFF でも、スマホから許可が ON で登録済みの端末になら見せる（_ask_sealed の門）
    if kind != "ask" and config.get("dialogRelay") is not True:
        return refuse("denied")
    dev = load_devices().get("devices", {}).get(device) if device else None
    if (not isinstance(dev, dict) or dev.get("revoked")
            or not isinstance(dev.get("secret"), str)
            or (isinstance(dev.get("expires"), (int, float)) and dev["expires"] < iat)):
        return refuse("denied")                       # 失効した端末には封を作らない（二度目の門）
    known, created = dialog_seal.reserve_dialog(req_id)
    if known is not None:
        return True, "cached", {"bundle": known}
    if not created:
        return True, "pending", {"bundle": None}      # 封緘中（次の周で出る）
    if kind == "ask":
        return _ask_sealed(act, dev, device, req_id, session, iat, refuse, replace_key)
    if session.startswith("cx-") or _is_bot_session(session):
        # R113-B-I: Codex のセッションは term（ターミナル画面）だけ開ける＝rollout は office_term.session_page →
        # source_codex_rollout が SQLite（読み取り専用）の rollout_path を ~/.codex/sessions 配下へ閉じ込めて読む。
        # R128: 出勤の口の Bot（bot-）も term だけ＝記録（~/.claude/office_door/bot/<id>.log.jsonl）を source_door_log が
        # id を確かめ・登録した Bot だけ・口のフォルダの中だけ読む（本文は封書の中だけ＝中継の平文に鍵を足さない）。
        # transcript の検索（_session_transcript）は飛ばす。dialog（会話ビュー）は従来どおり notfound。
        if kind == "term":
            return _term_sealed(None, act, dev, device, req_id, session, iat, refuse, replace_key)
        return refuse("notfound")
    p = _session_transcript(session)
    if not p:
        return refuse("notfound")
    if kind == "term":
        return _term_sealed(p, act, dev, device, req_id, session, iat, refuse, replace_key)
    depth = config.get("dialogRelayDepth", 0)
    depth = depth if isinstance(depth, int) and 0 <= depth <= 1 else 0
    nbytes = DIALOG_DEPTHS[depth][0]
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    page = dialog_page(tail_lines(p, nbytes), depth, truncated=size > nbytes)
    page.update({"session": session, "reqId": req_id, "iat": iat, "clipped": False})
    for _ in range(6):                                # 収まらなければ古い方から半分落とす
        try:
            bundle = dialog_seal.seal(dev["secret"], device, req_id, session, page, iat=iat)
            break
        except ValueError as e:
            if str(e) != "toolarge" or len(page.get("messages") or []) < 2:
                return refuse("toolarge")
            page["messages"] = page["messages"][len(page["messages"]) // 2:]
            page["clipped"] = True
    else:
        return refuse("toolarge")
    dialog_seal.store_bundle(req_id, bundle)
    office_actions._audit({"kind": "dialog", "reqId": req_id, "device": device,
                           "session": session, "ts": iat, "state": "sealed",
                           "frames": bundle["n"], "clipped": page["clipped"]})
    return True, "sealed", {"bundle": bundle}


_ACT_AFTER_RE = re.compile(r"^([1-9][0-9]{0,15}):([0-9a-f]{16})$")
_ACT_BEFORE_RE = re.compile(r"^([1-9][0-9]{0,15}):([0-9a-f]{16}):([0-9]{1,4})$")
_ACT_EMPTY = {"rows": [], "cur": None, "reset": True, "gap": False, "beg": None, "top": True}


def activity_page(qs):
    """R121: GET /api/session/activity のクエリ → (ok, 頁 | 400 の理由)。
    session（形は _SESSION_ID_RE）・after=<off>:<h16>（差分）・before=<off>:<h16>:<i>（遡り）。カーソルの検算は office_term の
    valid_after／valid_before が正本（ここは文字の形だけ）。未知 session・cx- の rollout・bot- の記録が引けない＝空の頁（200）。
    R128: bot-（出勤の口の Bot）は transcript を探さず、office_term.session_page の bot- の分岐（source_door_log）で記録を読む。"""
    session = (qs.get("session") or [""])[0]
    if not _SESSION_ID_RE.fullmatch(session or ""):
        return False, "bad session"
    raw_after, raw_before = (qs.get("after") or [""])[0], (qs.get("before") or [""])[0]
    after = before = None
    if raw_after:
        m = _ACT_AFTER_RE.fullmatch(raw_after)
        if not m:
            return False, "bad cursor"
        after = {"off": int(m.group(1)), "h": m.group(2)}
    if raw_before:
        m = _ACT_BEFORE_RE.fullmatch(raw_before)
        if not m or after is not None:
            return False, "bad cursor"
        before = {"off": int(m.group(1)), "h": m.group(2), "i": int(m.group(3))}
    own = session.startswith("cx-") or _is_bot_session(session)   # 読み手が自分で閉じ込めて読む（Codex の rollout・Bot の記録）
    path = None if own else _session_transcript(session)
    if path is None and not own:
        return True, dict(_ACT_EMPTY)
    try:
        page = office_term.session_page(session, path, after, office_actions.scrub_output, _COMMAND_NAME_RE,
                                        before=before, home=_HOME)
    except OSError:
        return True, dict(_ACT_EMPTY)
    if page is None:                                  # 遡りのカーソルが合わない（ファイルが差し替わった）＝遡りを止める
        return True, {"rows": [], "older": True, "beg": None, "top": False, "reset": False, "gap": False, "cur": None}
    return True, office_term.public(page)


def _term_sealed(path, act, dev, device, req_id, session, iat, refuse, replace_key):
    """R100-1a: ターミナル画面の 1 枚（差分カーソル付き）を封じる。行の組み立ては server/office_term.py（純関数）。
    道具の引数と結果は scrub_output を通してから載せる（封書は中継対策・伏字は端末の盗難対策）。
    R113-B-I: 頁は office_term.session_page で選ぶ（cx-＝Codex の rollout・bot-＝出勤の口の Bot の記録（R128）・path は None／
    それ以外＝閉じ込め済みの transcript）。引けない・閉じ込めの外＝FileNotFoundError＝下の OSError で notfound。"""
    try:
        if act.get("before") is not None and act.get("after") is None:
            # R104-2: 画面のいちばん上より前（開く前の行）を遡る。カーソルが合わない＝ファイルが差し替わった＝遡りを止める
            page = office_term.session_page(session, path, None, office_actions.scrub_output, _COMMAND_NAME_RE,
                                            before=act.get("before"), home=_HOME)
            if page is None:
                page = {"rows": [], "older": True, "beg": None, "reset": False, "gap": False, "cur": None}
        else:
            page = office_term.session_page(session, path, act.get("after"), office_actions.scrub_output,
                                            _COMMAND_NAME_RE, home=_HOME)
    except OSError:
        return refuse("notfound")
    page.update({"kind": "term", "v": 2, "session": session, "reqId": req_id, "iat": iat})
    # R102-3: 収まらなければ office_term.shrink の順で縮める（R113-B: kids→差分を半分→従来相当→古い差分の本文→結果の本文を 2 行
    # →古い方から半分落として gap）。回数は段の数に追従（直書きの 10 だと段を足したとき 1 行まで縮まずに toolarge）
    step = 0
    for _ in range(office_term.SHRINK_STEPS + 1):
        try:
            bundle = dialog_seal.seal(dev["secret"], device, req_id, session, office_term.public(page), iat=iat)
            break
        except ValueError as e:
            if str(e) != "toolarge" or not office_term.shrink(page, step):
                return refuse("toolarge")
            step += 1
    else:
        return refuse("toolarge")
    dialog_seal.store_bundle(req_id, bundle, replace_key=replace_key)
    office_actions._audit({"kind": "term", "reqId": req_id, "device": device,
                           "session": session, "ts": iat, "state": "sealed", "v": 2,
                           "frames": bundle["n"], "rows": len(page["rows"]), "reset": page.get("reset"),
                           "older": bool(page.get("older"))})
    return True, "sealed", {"bundle": bundle}


ASK_SEAL_ERR = {"off": "off", "not-enrolled": "unenrolled", "no-ask": "noask", "scope": "scope"}


def _ask_project(session, ask):
    """許可の範囲（登録時の projects）と突き合わせるプロジェクト ID＝掲示の cwd のフォルダ。"""
    return project_id_for(str((ask or {}).get("cwd") or ""))


def _ask_sealed(act, dev, device, req_id, session, iat, refuse, replace_key):
    """R100-1b: 許可を待っている 1 件の全文（inputCanon）を、その端末の鍵で封じる。
    門（office_approve.seal_gate）: ON・この端末のペアリングが有効（R118: 許可鍵は要らない）・掲示が生きている・範囲（projects）の中。
    封じた事実（端末・askId・iat）を seen に積む＝許可は封じてから 180 秒以内（Mac の時計）しか受け付けない。"""
    want = act.get("askId")
    asks = pending_approvals(session)
    raw = None
    for a in asks:
        if a.get("askId") and (want is None or a["askId"] == want):
            raw = _ask_raw(session, a["askId"])
            if raw:
                break
    gate = office_approve.seal_gate(device, session, raw, load_devices(), _ask_project, now=iat)
    if gate:
        return refuse(ASK_SEAL_ERR.get(gate, "denied"))
    canon = raw.get("inputCanon") if isinstance(raw.get("inputCanon"), str) else ""
    cwd = str(raw.get("cwd") or "")
    page = {"kind": "ask", "session": session, "reqId": req_id, "iat": iat,
            "askId": raw["askId"], "tool": short(str(raw.get("tool") or ""), 64),
            "inputCanon": canon, "inputSha": str(raw.get("inputSha") or "") if canon else "",
            "inputBytes": int(raw.get("inputBytes") or 0) if isinstance(raw.get("inputBytes"), int) else len(canon.encode("utf-8")),
            "cwdBase": os.path.basename(cwd.rstrip("/"))[:80], "askTs": int(float(raw.get("ts") or 0)),
            # R103-1: mac の段も全文があればそのまま（スマホは Face ID で許可・危険の理由を言葉で出す・R114 で全文を読む義務は廃止）。
            # 全文が無い（読めない・大きすぎる）ときだけ "macOnly"＝本当に Mac だけで許可する形
            "level": raw.get("level") if raw.get("level") in ("remote", "review", "mac") and canon else "mac",
            "macOnly": 0 if canon else 1,
            "reasons": [str(r)[:24] for r in (raw.get("reasons") or [])][:4],
            "tags": [str(t)[:24] for t in (raw.get("tags") or [])][:12] if raw.get("level") in ("review", "mac") else [],
            "agentId": str(raw.get("agentId") or "")[:64], "askCount": len(asks)}
    try:
        bundle = dialog_seal.seal(dev["secret"], device, req_id, session, page, iat=iat)
    except ValueError:
        return refuse("toolarge")
    # 急ぎ: 人が「スマホで確認して許可」を押して待っている＝中継の自動減速（R80）でも保留しない
    dialog_seal.store_bundle(req_id, bundle, replace_key=replace_key, urgent=True)
    office_approve.mark_seen(device, raw["askId"], iat)
    office_approve.audit({"kind": "sealed", "reqId": req_id, "device": device, "session": session,
                          "askId": raw["askId"], "tool": page["tool"], "inputSha": page["inputSha"],
                          "level": page["level"], "reasons": page["reasons"]})
    return True, "sealed", {"bundle": bundle}


def _relay_rp():
    """WebAuthn の rpId と origin＝中継の URL（~/.claude/office_relay.json）。https か localhost だけ。"""
    url, _tok = relay_config()
    try:
        from urllib.parse import urlsplit
        u = urlsplit(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if not host or (u.scheme != "https" and host != "localhost"):
        return None
    origin = f"{u.scheme}://{host}" + (f":{u.port}" if u.port else "")
    return host, origin


def _approve_remote(data):
    """R100-1b: /api/approval/remote（relay_agent が apv- の封筒をそのまま回す）。(ok, msg, extra)。"""
    env = data.get("envelope")
    if not isinstance(env, dict):
        return False, "envelope required", {}

    def write_reply(session, ask_id, extra):
        write_approval_reply(session, "allow", "", src="remote", ask_id=ask_id, extra=extra)

    state, reason, info = office_approve.approve(
        env, devices=load_devices(), verify=verify_envelope, rp=_relay_rp(), read_ask=_ask_raw,
        write_reply=write_reply, project_of=_ask_project, home=str(_HOME), available=dialog_seal.AVAILABLE,
        notify=_notify_security)
    rid = info.get("reqId") or ""
    if rid and office_actions.REQID_RE.match(rid) and reason not in ("replay", "bad-sig"):
        label = L_now(f"{info.get('tool') or '操作'} の許可", f"Allow {info.get('tool') or 'action'}")
        # R114: 方式（sig＝Face ID／tap＝ON の間のタップ）も控える（office_actions の監査に残る・公開ビューには載らない）
        office_actions.register_result(rid, "approve", label, state, output=reason, device=info.get("device", ""),
                                       mode=info.get("mode", ""))
    return True, state, {"state": state, "reason": reason, "mode": info.get("mode", "")}


def _approve_remote_set(data):
    """R103-1: /api/approval/remote_set（relay_agent が rst- の封筒をそのまま回す）。スマホから ON/OFF（本人裁定 2026-10-02）。"""
    env = data.get("envelope")
    if not isinstance(env, dict):
        return False, "envelope required", {}
    state, reason, info = office_approve.remote_set(env, devices=load_devices(), verify=verify_envelope,
                                                    rp=_relay_rp(), notify=_notify_security)
    rid = info.get("reqId") or ""
    if rid and office_actions.REQID_RE.match(rid) and reason not in ("replay", "bad-sig"):
        label = (L_now("スマホから許可を ON", "Phone approvals on") if info.get("on")
                 else L_now("スマホから許可を OFF", "Phone approvals off"))
        office_actions.register_result(rid, "remote_set", label, state, output=reason or str(info.get("until") or ""),
                                       device=info.get("device", ""))
    return True, state, {"state": state, "reason": reason, "until": info.get("until", 0)}


def _approve_enroll(data):
    """R100-1b: /api/approve/enroll（relay_agent が enr- の封筒をそのまま回す）。R103-1 から Mac の確認は無い（即時有効・通知と監査だけ）。"""
    env = data.get("envelope")
    if not isinstance(env, dict):
        return False, "envelope required", {}
    # R103-1: Mac の確認ダイアログは出さない（本人裁定 2026-10-02「登録も ON もスマホだけで完結」）。通知と監査だけ
    state, reason, fp = office_approve.enroll(
        env, devices=load_devices(), verify=verify_envelope, rp=_relay_rp(), confirm=None,
        notify=_notify_security)
    sess = str(env.get("session") or "")
    ticket = sess[4:] if sess.startswith("enr-") else ""
    if ticket and office_actions.REQID_RE.match(ticket) and reason not in ("replay", "bad-sig"):
        office_actions.register_result(ticket, "enroll", L_now("許可鍵の登録", "Approval key"), state,
                                       output=reason or fp)
    return True, state, {"state": state, "reason": reason}


def approve_status():
    """デスクトップの 📱 スマホ連携（loopback だけ）: ON の期限・端末ごとの許可鍵・今日の件数・中継の設定。"""
    devs = load_devices()
    return {"until": office_approve.remote_until(), "always": office_approve.REMOTE_ALWAYS,
            "keys": office_approve.keys_local(devs), "today": office_approve.today_summary(),
            # b4-mac: 登録の段階（受付の期限・Mac の確認待ち・直近の結果）＝Mac のモーダルが結果を知らせる
            "enroll": office_approve.enroll_local(), "enrollTries": office_approve.ENROLL_TRIES,
            "confirmWait": office_approve.ENROLL_CONFIRM_WAIT,
            "relay": bool(_relay_rp()), "available": bool(dialog_seal.AVAILABLE)}


PROJECT_ARCHES = frozenset(("phones", "cap", "beret", "pencil", "bowtie",
                           "mortar", "headset", "hardhat", "eyeshade"))
# R91: 殻の色（アクセサリと直交する「もう一段細かい」個体差）。
# 名前だけを持ち、実際の色は ui/core/archetype.js の SHELL_COLORS が正本
#（サーバーは配色を知らない＝見た目の正本は docs/art-direction.md 側に残す）。
PROJECT_COLORS = frozenset(("cream", "oak", "sage", "clay", "slate", "sand", "moss", "rose"))
# 個体（セッション）単位の見た目を持つ office_config.json の枠。上限を超えたら古い順に捨てる
#（設定ファイルが無限に太らない＝セッションIDは使い捨てなので放置すると溜まる一方）。
AVATAR_STYLE_MAX = 200


def _validated_style(fields):
    """{"arch":…, "color":…} のうち、送られてきたキーだけを検証して返す。"""
    out = {}
    if "arch" in fields:
        arch = fields["arch"]
        if arch is not None and (not isinstance(arch, str) or arch not in PROJECT_ARCHES):
            return None, "invalid accessory"
        out["arch"] = arch
    if "color" in fields:
        color = fields["color"]
        if color is not None and (not isinstance(color, str) or color not in PROJECT_COLORS):
            return None, "invalid color"
        out["color"] = color
    if not out:
        return None, "nothing to save"
    return out, ""


def set_avatar_style(avatar_id, scope, fields):
    """アバターの見た目を保存する。

    R91: 従来は cwd（プロジェクト）にしか保存できず、avatarMode=session（実既定）では
    「1体に付けたら同じフォルダの全員が同じ帽子になる」＝本人指摘の「統一で全部変わる」。
    scope="session" は roster の projectId（= sha1(cwd + "\n" + session)）で個体に保存する。
    """
    if not isinstance(avatar_id, str) or not re.fullmatch(r"[0-9a-f]{12}", avatar_id):
        return False, "invalid avatarId"
    if scope not in ("session", "project"):
        return False, "invalid scope"
    style, err = _validated_style(fields if isinstance(fields, dict) else {})
    if style is None:
        return False, err
    roster = office_json().get("roster", [])       # ★_lock の外で1回（office_json は同じ Lock を取る）
    entry = next((p for p in roster
                  if p.get("projectId") == avatar_id and not p.get("external")), None)
    if scope == "project":
        cwd = (entry or {}).get("cwd", "")
        if not cwd:
            cwd = next((p.get("cwd") for p in projects_index.projects_json().get("projects", [])
                        if p.get("cwd") and project_id_for(p["cwd"]) == avatar_id), "")
        if not cwd:
            return False, "unknown project"
    elif entry is None:
        # 出勤していない個体には保存しない（消えたセッションのゴミが config に溜まる）
        return False, "unknown avatar"
    with _lock, _file_flock(config_file()):
        cf = config_file()
        try:
            cfg = json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else {"projects": {}}
            if not isinstance(cfg, dict) or not isinstance(cfg.get("projects", {}), dict):
                return False, "invalid office_config.json"
            if scope == "project":
                projects = cfg.setdefault("projects", {})
                key = project_config_key(cwd, cfg) or project_pattern(cwd)
                meta = projects.get(key, {})
                if not isinstance(meta, dict):
                    return False, "invalid project config"
                if key in projects:
                    projects[key] = {**meta, **style}
                else:
                    cfg["projects"] = {key: dict(style), **projects}
                # 個体側の上書きが残っていると「フォルダ全員」を選んでも変わらなく見える。
                # ★roster（いま出勤している人）で消すと足りない＝鮮度の窓から外れて
                # 一時的に非表示の個体が、再稼働したときに古い帽子を蘇らせる。
                # だから保存時に控えた key（フォルダ）で消す。
                avatars = cfg.get("avatars")
                if isinstance(avatars, dict):
                    for pid, meta in list(avatars.items()):
                        if isinstance(meta, dict) and meta.get("cwd") == cwd:
                            avatars.pop(pid, None)
            else:
                avatars = cfg.setdefault("avatars", {})
                if not isinstance(avatars, dict):
                    return False, "invalid avatars config"
                prev = avatars.get(avatar_id)
                # cwd=どのフォルダの個体か（「フォルダ全員」で確実に上書きを解除するため）。
                # ★設定パターン（project_config_key）ではなく実フォルダを控える＝
                # 後から add_project でそのフォルダを個別登録するとパターンが変わり、
                # パターンで突き合わせると解除できなくなる（Astra レビュー指摘）。
                avatars[avatar_id] = {**(prev if isinstance(prev, dict) else {}),
                                      **style, "cwd": entry.get("cwd", ""),
                                      "at": int(time.time())}
                if len(avatars) > AVATAR_STYLE_MAX:
                    def saved_at(item):
                        """手編集で at が null/文字列でも剪定が落ちないようにする。"""
                        v = item[1].get("at") if isinstance(item[1], dict) else 0
                        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
                    stale = sorted(avatars.items(), key=saved_at)
                    for old_id, _ in stale[:len(avatars) - AVATAR_STYLE_MAX]:
                        avatars.pop(old_id, None)
            _write_config(cfg)
            _mark_written()
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False, "could not save office_config.json"
    return True, "saved"


def set_project_arch(project_id, arch):
    """Back-compat wrapper: the folder-wide accessory (pre-R91 API)."""
    return set_avatar_style(project_id, "project", {"arch": arch})

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
        _mark_written()
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
# R116-S1b つながりの延長（本人裁定 2026-10-26・計画 ~/.claude/plans/r116-s1b-pair-extend.md）: Mac の［延長］は REKEY_WINDOW の間だけ
# 「作り直してよい」を出す。スマホが今の秘密で求めたら、新しい秘密（next）を今の秘密で封じて渡す＝新しい秘密は URL に一度も載らない。
# スマホが next で署名した受領（ack）を返したら昇格し、古い秘密（prev）は取りこぼし防止に PREV_GRACE だけ受ける。台帳の書き手は daemon だけ。
REKEY_WINDOW = 24 * 3600
PREV_GRACE = 600
REKEY_TARGET = "pair-rekey"          # 延長の封書の AAD の対象（スマホも同じ文字列で開く）


# R100-1b 後（本人「オンにしても何時までかが出ない」）: 端末の台帳は専用の鍵で守る。全体の _lock は office_json の組み立て
# （transcript の読み・外部の費用 API）が握り続けるので、📱 スマホ連携の一覧（prune_devices）が 7〜12 秒待たされていた。
_devices_lock = threading.RLock()


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
    with _devices_lock:
        d = load_devices()
        d.setdefault("devices", {})[device_id] = {
            "secret": secret, "label": label, "created": now,
            "expires": now + DEVICE_TTL, "revoked": False, "last_used": 0,
        }
        save_devices(d)
    return {"device_id": device_id, "secret": secret,
            "label": label, "created": now, "expires": now + DEVICE_TTL}


def revoke_device(device_id):
    with _devices_lock:
        d = load_devices()
        rec = d.get("devices", {}).get(device_id)
        if not rec:
            return False
        rec["revoked"] = True
        rec["revoked_at"] = int(time.time())
        for slot in ("next", "prev", "rekey_until"):   # R116-S1b: 延長の受け渡し中の秘密も一緒に捨てる
            rec.pop(slot, None)
        save_devices(d)
    office_approve.revoke_key(device_id)             # R100-1b: 端末を失効させたら許可鍵も
    return True


def _next_expires(rec):
    """R116-S1b: 受け渡し中の新しい秘密の期限（無い・壊れていれば 0）。"""
    nxt = rec.get("next") if isinstance(rec, dict) else None
    try:
        return int(nxt.get("expires") or 0) if isinstance(nxt, dict) else 0
    except (TypeError, ValueError):
        return 0


def _primary_live(rec, now):
    """R116-S1b: 今の秘密が使えるか（失効でない・期限内・秘密の形が正しい）。延長の求めを封じるのはこれが真のときだけ。"""
    if not isinstance(rec, dict) or rec.get("revoked"):
        return False
    try:
        bytes.fromhex(rec["secret"])
        return now <= int(rec.get("expires", 0))
    except (KeyError, TypeError, ValueError):
        return False


def _device_live(rec, now):
    """R116-S1b: 延長できる（＝まだ指示を送れる）端末か。今の秘密が期限内か、受け渡し中の新しい秘密（next）が生きている
    （別モデルレビュー 2 巡目 medium: 受領が届かないまま元の期限を過ぎても、next の署名は受ける＝一覧は有効のまま・延長と失効を押せる）。"""
    if _primary_live(rec, now):
        return True
    return (isinstance(rec, dict) and not rec.get("revoked")
            and _only_next({"devices": {"x": rec}}, "x", now) is not None)


def rekey_allow(device_id, now=None):
    """R116-S1b: Mac の［延長］。その端末に REKEY_WINDOW の間だけ「作り直してよい」を出す（秘密はまだ作らない）。
    (ok, msg, until)。失効・期限切れ・不明の端末は延ばせない。呼び手（/api/pair/extend）が物理確認・通知・監査をする。"""
    now = int(now or time.time())
    with _devices_lock:
        d = load_devices()
        rec = d.get("devices", {}).get(device_id) if isinstance(device_id, str) else None
        if not _device_live(rec, now):
            return False, L_now("延長できない端末です（失効・期限切れ・見つからない）",
                                "This phone can't be extended (revoked, expired or unknown)"), 0
        rec["rekey_until"] = now + REKEY_WINDOW
        save_devices(d)
    return True, L_now("延長を受け付けました（スマホでアプリを開くと完了します）",
                       "Extension armed — open the app on the phone to finish"), now + REKEY_WINDOW


def rekey_pending(devices=None, now=None):
    """R116-S1b: office_json.actions.approve.rk（中継へ出る）＝延長の窓が開いている端末 id（秘密ではない・上限 8）。"""
    now = int(now or time.time())
    devs = (devices if devices is not None else load_devices()).get("devices", {})
    out = []
    for did, rec in devs.items():
        try:
            if _device_live(rec, now) and int(rec.get("rekey_until") or 0) > now:
                out.append(did)
        except (TypeError, ValueError):
            continue
    return sorted(out)[:8]


def _only_next(devices, device_id, now):
    """R116-S1b: next の秘密だけを持つ台帳の写し（受領が next で署名されたか＝新しい秘密を持っている証明か、を確かめる）。"""
    rec = devices.get("devices", {}).get(device_id)
    nxt = rec.get("next") if isinstance(rec, dict) else None
    if not isinstance(nxt, dict) or rec.get("revoked"):
        return None
    try:
        exp = int(nxt.get("expires", 0))
        bytes.fromhex(nxt["secret"])
    except (KeyError, TypeError, ValueError):
        return None
    if now > exp:
        return None
    return {"devices": {device_id: {"secret": nxt["secret"], "expires": exp, "revoked": False}}}


def _pair_rekey(data, now=None):
    """R116-S1b: /api/pair/rekey（relay_agent が rky- の封筒をそのまま回す）。封筒は**ここで**確かめ直す（relay_agent を信じない）。
    step "req": 今の秘密で署名された求め＋延長の窓が開いている → next を作る（あれば同じものを使う＝冪等）→ 今の秘密で封じて置く。
    step "ack": next で署名された受領だけが昇格させる（今の秘密の ack は新しい秘密を持つ証明にならない）。(ok, msg, extra)。"""
    now = int(now or time.time())
    env = data.get("envelope") if isinstance(data, dict) else None
    devices = load_devices()
    ok, _reason, sess, text = verify_envelope(env, devices, now, 300)
    if not ok or not re.fullmatch(r"rky-[0-9a-f]{32}", sess):
        return False, "bad envelope", {"state": "denied"}
    device = env["device_id"]
    try:
        act = json.loads(text)
    except ValueError:
        act = None
    req_id = sess[4:]
    if (not isinstance(act, dict) or act.get("aioffice") != 1 or act.get("kind") != "rekey"
            or act.get("reqId") != req_id or act.get("step") not in ("req", "ack")):
        return False, "bad rekey", {"state": "denied"}
    if act["step"] == "ack":
        return _rekey_done(env, device, req_id, now)
    # 別モデルレビュー high 2: 新しい秘密（next）で署名された求め＝スマホはもう新しい秘密を持っている（受領が届かなかった）＝受領と同じく昇格
    nview = _only_next(devices, device, now)
    if nview is not None and verify_envelope(env, nview, now, 300)[0]:
        return _rekey_done(env, device, req_id, now)
    # step "req": 今の秘密（primary）で署名され、窓が開いているときだけ封じる
    with _devices_lock:
        d = load_devices()
        rec = d.get("devices", {}).get(device)
        primary = {"devices": {device: {"secret": rec.get("secret"), "expires": rec.get("expires"), "revoked": False}}} \
            if _primary_live(rec, now) else None
        if primary is None or not verify_envelope(env, primary, now, 300)[0]:
            return False, "not current", {"state": "denied"}
        try:
            window = int(rec.get("rekey_until") or 0)
        except (TypeError, ValueError):
            window = 0
        if window <= now:
            return False, "not armed", {"state": "denied"}
        nxt = rec.get("next")
        if _only_next(d, device, now) is None or not re.fullmatch(r"[0-9a-f]{64}", str(nxt.get("secret"))):
            # 無い・切れた・壊れた next は作り直す（有効な next は取り直しても同じものを渡す＝届いた方と食い違わない）
            nxt = {"secret": secrets.token_hex(32), "expires": now + DEVICE_TTL, "created": now}
            rec["next"] = nxt
            save_devices(d)
        secret, payload = rec["secret"], {"s": nxt["secret"], "e": int(nxt["expires"])}
    # 別モデルレビュー high 1: 許可鍵の期限は求めの時点（鍵がまだ有効なうち）で新しい期限へ。受領が元の期限の後に届くと、昇格の時点では
    # 鍵が切れていて延ばせず、approver が範囲つきの許可を全プロジェクトへ広げた。端末が昇格しなければ _device_ok が鍵ごと止める
    office_approve.extend_key(device, payload["e"], now)
    if not dialog_seal.AVAILABLE:
        return False, "unavailable", {"state": "unavailable"}
    bundle = dialog_seal.seal(secret, device, req_id, REKEY_TARGET, payload, iat=now)
    dialog_seal.store_bundle(req_id, bundle, replace_key=f"rekey:{device}", urgent=True)
    office_actions._audit({"kind": "rekey", "reqId": req_id, "device": device, "ts": now, "state": "sealed"})
    return True, "sealed", {"state": "sealed"}


def _rekey_done(env, device, req_id, now):
    """R116-S1b: 受領（ack）と、新しい秘密で署名された求めの受け口。Mac が「済み」をスマホの今の秘密で封じて返す
    （別モデルレビュー 3 巡目: スマホは状態や時刻から完了を推さず、この封書を開けたときだけ受領待ちを終える）。
    - next で署名されている → next を今の秘密へ昇格（古い秘密は PREV_GRACE だけ受ける・許可鍵の期限も追う・通知と監査）→「済み」を新しい秘密で
    - 今の秘密で署名され、受け渡し中の秘密が無い → もう昇格済み（スマホが「済み」を受け取り損ねた）→「済み」だけ返す（台帳は替えない）
    - それ以外（受け渡し中なのに今の秘密の受領＝新しい秘密を持つ証明にならない・失効・知らない鍵）→ 何もしない
    確かめ直しは台帳の鍵の中で（確かめた後に台帳が替わる隙を作らない）。"""
    promoted = False
    with _devices_lock:
        d = load_devices()
        rec = d.get("devices", {}).get(device)
        view = _only_next(d, device, now)
        if view is not None and verify_envelope(env, view, now, 300)[0]:
            nxt = rec["next"]
            old_secret, old_exp = rec.get("secret"), int(rec.get("expires") or 0)
            rec["secret"], rec["expires"] = nxt["secret"], int(nxt["expires"])
            rec["prev"] = {"secret": old_secret, "until": min(now + PREV_GRACE, max(old_exp, now))}
            rec["extended_at"] = now
            for slot in ("next", "rekey_until"):
                rec.pop(slot, None)
            save_devices(d)
            promoted = True
        elif not (view is None and _primary_live(rec, now) and verify_envelope(
                env, {"devices": {device: {"secret": rec["secret"], "expires": rec["expires"], "revoked": False}}}, now, 300)[0]):
            return True, "noop", {"state": "noop"}
        secret, new_exp, label = rec["secret"], int(rec["expires"]), rec.get("label", "")
        try:
            armed = 1 if int(rec.get("rekey_until") or 0) > now else 0   # 新しい窓（もう一度［延長］）＝スマホはすぐ求めてよい
        except (TypeError, ValueError):
            armed = 0
    if dialog_seal.AVAILABLE:
        dialog_seal.store_bundle(req_id, dialog_seal.seal(secret, device, req_id, REKEY_TARGET,
                                                          {"done": 1, "e": new_exp, "rk": armed}, iat=now),
                                 replace_key=f"rekey:{device}", urgent=True)
    if not promoted:
        return True, "done", {"state": "done"}
    office_approve.extend_key(device, new_exp, now)
    office_actions._audit({"kind": "rekey", "device": device, "ts": now, "state": "extended", "expires": new_exp})
    with _lock:
        _mark_written()
    md = time.strftime("%m/%d", time.localtime(new_exp)).lstrip("0").replace("/0", "/")
    _notify_security(L_now(f"📱 スマホのつながりを延長しました（{md} まで）", f"📱 Phone pairing extended (until {md})"),
                     label or device)
    return True, "extended", {"state": "extended", "expires": new_exp}


DEVICE_KEEP_DEAD = 7 * 86400      # 期限切れを台帳に残す期間
DEVICE_KEEP_REVOKED = 3600        # 失効直後は「失効済み」として残す（押した結果が見えるように）


def prune_devices(now=None):
    """失効・期限切れの端末を台帳から落とす（秘密を必要以上に持ち続けない）。

    実測（2026-08-28）: 📱スマホ連携を押すたびに新しい鍵が増え、**57件（うち有効29件）**
    まで溜まっていた。どれも署名付き指示を送れる鍵なので、使い終わったものは消す。
    有効なものには一切触らない（消すのは revoked と、期限切れから DEVICE_KEEP_DEAD 経過）。
    """
    now = int(now or time.time())
    with _devices_lock:
        d = load_devices()
        devs = d.get("devices", {})
        dead = []
        tidied = False
        for did, rec in devs.items():
            exp = int(rec.get("expires") or 0)
            if rec.get("revoked"):
                # 押した直後は一覧に「失効済み」として残す（操作の結果が見えないと不安）。
                # revoked_at を持たない旧レコードは即対象（もう使えないので残す意味がない）。
                if now - int(rec.get("revoked_at") or 0) > DEVICE_KEEP_REVOKED:
                    dead.append(did)
            elif exp and max(exp, _next_expires(rec)) + DEVICE_KEEP_DEAD < now:
                # R116-S1b（別モデルレビュー medium）: 新しい秘密（next）が生きている間は、元の期限から 7 日を過ぎても端末ごと消さない
                #（受領を送れないまま長くオフラインだったスマホが戻ったとき unknown-device にしない）
                dead.append(did)
            # R116-S1b: 切れた延長の窓・受け渡し中の秘密・猶予の切れた古い秘密は項目ごと捨てる（壊れた形も捨てる）
            for slot, lim in (("rekey_until", None), ("next", "expires"), ("prev", "until")):
                if slot not in rec:
                    continue
                v = rec[slot]
                try:
                    stale = int(v if lim is None else v.get(lim, 0)) < now
                except (AttributeError, TypeError, ValueError):
                    stale = True
                if stale:
                    rec.pop(slot, None)
                    tidied = True
        for did in dead:
            devs.pop(did, None)
        if dead or tidied:
            save_devices(d)
    return len(dead)


def list_devices():
    """secret を伏せたデバイス一覧（画面表示用）。状態を明示し、有効なものを先に出す。"""
    prune_devices()
    now = int(time.time())
    d = load_devices()
    seen = load_device_seen()
    out = []
    for did, rec in d.get("devices", {}).items():
        expires = int(rec.get("expires") or 0)
        last = rec.get("last_used", 0)
        last = max(last if isinstance(last, (int, float)) and not isinstance(last, bool) else 0, seen.get(did, 0))
        # R116-S1b（別モデルレビュー 2 巡目 medium）: 受け渡し中の新しい秘密（next）が生きていれば、元の期限を過ぎても有効
        # （その秘密で指示を送れる＝画面から失効できないと困る）。残り日数は長い方の期限で数える
        expires = max(expires, _next_expires(rec)) if _only_next({"devices": {"x": rec}}, "x", now) else expires
        state = ("revoked" if rec.get("revoked")
                 else "expired" if expires and expires <= now else "active")
        # R116-S1b: 延長の進み具合（wait＝［延長］を押した・スマホがまだ求めていない／pending＝新しい秘密を渡した・受領待ち）
        rekey = ""
        if state == "active":
            try:
                armed = int(rec.get("rekey_until") or 0) > now
            except (TypeError, ValueError):
                armed = False
            rekey = "pending" if isinstance(rec.get("next"), dict) else "wait" if armed else ""
        out.append({
            "device_id": did, "label": rec.get("label", ""),
            "created": rec.get("created", 0), "expires": expires,
            "revoked": bool(rec.get("revoked")), "last_used": int(last),
            # 「どれが今も使える鍵か」が画面から分かること（29件全部が同じ「スマホ」表示で
            # 見分けられない状態だった）。日数は表示側の計算をサーバーに寄せる。
            "state": state, "daysLeft": max(0, (expires - now) // 86400) if expires else 0,
            "rekey": rekey,
        })
    out.sort(key=lambda x: (x["state"] != "active", -x["created"]))
    return out


DEVICE_SEEN_MAX = 200             # 「最後に使った時刻」を持つ端末の上限（古い順に捨てる＝ファイルが太り続けない）
_DEVICE_ID_RE = re.compile(r"d_[0-9a-f]{12}")


def load_device_seen():
    """{device_id: 最後に署名を検証できた epoch 秒}。読めない・壊れている値は捨てる（一覧の飾りなので失敗は黙る）。"""
    try:
        d = json.loads(DEVICES_SEEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: int(v) for k, v in d.items()
            if isinstance(k, str) and _DEVICE_ID_RE.fullmatch(k)
            and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}


def mark_device_used(device_id, now=None):
    """端末の署名を検証できた時刻を控える（relay_agent が配達の前に呼ぶ・間引きは呼び手）。
    秘密は書かない・0600・flock で daemon の読みと relay_agent の書きを直列化する。"""
    if not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
        return False
    now = int(now or time.time())
    DEVICES_SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _file_flock(DEVICES_SEEN_FILE):
        d = load_device_seen()
        d[device_id] = max(now, d.get(device_id, 0))
        if len(d) > DEVICE_SEEN_MAX:
            d = dict(sorted(d.items(), key=lambda kv: kv[1])[-DEVICE_SEEN_MAX:])
        office_common.atomic_write_json(DEVICES_SEEN_FILE, d, mode=0o600)
    return True


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
    # R116-S1b: 延長の受け渡し中の新しい秘密（next・その期限まで）と、昇格の直後の古い秘密（prev・PREV_GRACE の間）も受ける。
    # 失効は上で全部止めている。壊れた next/prev は無いものとして扱う（今の秘密の検証を巻き込まない）。
    alts = []
    for slot, lim in (("next", "expires"), ("prev", "until")):
        alt = rec.get(slot)
        if isinstance(alt, dict):
            try:
                if now <= int(alt.get(lim, 0)):
                    alts.append(bytes.fromhex(alt["secret"]))
            except (KeyError, TypeError, ValueError):
                continue
    if now > expires and not alts:
        return False, "expired", "", ""
    if abs(now - ts) > window:
        return False, "stale-ts", "", ""
    keys = alts
    if now <= expires:
        try:
            keys = [bytes.fromhex(rec["secret"])] + alts
        except (KeyError, TypeError, ValueError):
            return False, "bad-record", "", ""
    canon = _canonical(d_id, sess, text, ts, nonce)
    for secret_key in keys:
        expect = hmac.new(secret_key, canon, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expect, sig):
            return True, "ok", sess, text
    return False, "bad-sig", "", ""


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
# R100-0: Origin 必須の POST（Handler._csrf_strict）。relay_agent が回す /api/dialog/sealed・/api/action/exec は
# 封筒の署名で守る別経路なのでここに入れない。
_STRICT_POST = ("/api/approval/reply", "/api/pair/new", "/api/pair/revoke",
                # R116-S1b: つながりの延長（中継から指示を出せる期間を延ばす＝画面の操作だけ・物理確認と通知）
                "/api/pair/extend",
                "/api/recipes/set", "/api/config/dialog_relay",
                # R100-1b: スマホから許可の ON・許可鍵の登録チケット・失効（画面の操作だけ）
                "/api/config/remote_approve", "/api/approve/enroll_ticket", "/api/approve/revoke",
                # R103-5: 接続テスト（鍵を相手へ送る＝画面の操作だけ）
                "/api/providers/test",
                # R128 #4: Bot パネル（Bot の登録・直し・削除・消音＝画面の操作だけ・DOOR_BOT_ROUTES と同じ 3 本＝test_door_panel がピン）
                "/api/door/bot/add", "/api/door/bot/remove", "/api/door/bot/mute")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_sse_headers(self):
        # HTTP/1.0: Content-Length を付けず、切断までストリームを送る。
        # 読まないクライアントで送信バッファが満杯になると write が無期限に止まり、購読キューも溜まる
        # （Astra レビュー指摘）→ ソケットに書き込みタイムアウトを付け、timeout で unsubscribe へ落とす。
        try:
            self.connection.settimeout(SSE_WRITE_TIMEOUT)
        except (OSError, AttributeError):
            pass
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _events(self):
        if office_events is None or os.environ.get("OFFICE_EVENTS") == "0":
            return self._deny(503, "events unavailable")
        qs = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        raw_since = qs.get("since", [None])
        if (len(raw_since) != 1 or raw_since[0] is not None
                and not re.fullmatch(r"[0-9]{1,20}", raw_since[0])):
            return self._deny(400, "bad since")
        subscriber = office_events.subscribe()
        if subscriber is None:
            return self._deny(429, "too many event subscribers")

        def send(kind, data):
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: {kind}\ndata: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            self._send_sse_headers()
            current = office_events.seq()
            # 再起動前の大きなカーソルでも、その後のライブ通知を捨てない。
            last = current if raw_since[0] is None else min(int(raw_since[0]), current)
            send("hello", {"seq": current, "v": 2})

            follow = None   # 反証 R115 #2: 走査し直しに間に合わずに先に送った poke の追いかけ（中身・走査の印・期限）

            def poke(event, live=False):
                nonlocal last, follow
                if event["seq"] > last:
                    marks = None
                    # R115-S1b: ライブの poke で続きが積まれていない（連続の最後）なら、失効した値の走査し直しを待ってから
                    # 知らせる（最大 POKE_FRESH_WAIT 秒）＝poke を受けた UI の取り直しが SWR の古い値を掴まない。
                    # 間に合わない（遅い・失敗）ときは先に送り、この後に走査が成功したら同じ poke をもう 1 回送る
                    if live and subscriber.empty() and not _wait_fresh(POKE_FRESH_WAIT):
                        marks = _scan_marks()
                    # hook の追加フィールドや本文は SSE に載せない。
                    data = {key: event[key] for key in ("seq", "ts", "ev", "sid")}
                    send("poke", data)
                    last = event["seq"]
                    if live:        # 次のライブの poke が前の追いかけを引き継ぐ（その poke が自分で走査し直しを待つ）
                        follow = None if marks is None else (data, marks, time.monotonic() + POKE_FOLLOW_WAIT)

            # 先に subscribe 済みなので、履歴取得との間のイベントもキューに残る。
            for event in office_events.recent(last, limit=office_events.RING):
                poke(event)
            while True:
                try:
                    event = subscriber.get(timeout=15 if follow is None else POKE_FOLLOW_TICK)
                except queue.Empty:
                    if follow is None:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    fresh = _fresh_since(follow[1])     # 走査は起こさない（再試行は次の GET）
                    if fresh:
                        send("poke", follow[0])         # UI は poke ごとに取り直す（seq で重複を捨てない）
                    if fresh is not None or time.monotonic() >= follow[2]:
                        follow = None
                    continue
                poke(event, live=True)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout, OSError):
            pass
        finally:
            office_events.unsubscribe(subscriber)

    def _loopback(self):
        return getattr(self, "client_address", ("",))[0] in ("127.0.0.1", "::1")

    def _term_stream(self):
        """R99: 端末 1 本の出力を SSE で流す。出力は**ここでしか外へ出ない**（保存も中継もしない）。
        EventSource はヘッダを付けられないので、UI は /api/events と同じく fetch＋getReader で読む。"""
        qs = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        tid = (qs.get("id") or [""])[0]
        if not re.fullmatch(r"[0-9a-f]{16}", tid or ""):
            return self._deny(400, "bad id")
        raw_from = (qs.get("from") or [""])[0]
        if raw_from and not re.fullmatch(r"[0-9]{1,15}", raw_from):
            return self._deny(400, "bad from")
        term = office_pty.get(tid)
        if term is None:
            return self._deny(404, "no such terminal")
        # from＝この視聴者が読み終えた通し番号。リングの中なら続きだけ（reset しない＝画面モードが残る）
        backlog, start, reset, prefix, viewer = term.subscribe(int(raw_from) if raw_from else None)

        def send(kind, payload):
            self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode("ascii"))
            self.wfile.flush()

        try:
            self._send_sse_headers()
            hello = {**term.public(), "from": start, "reset": reset}
            if prefix:
                hello["modes"] = office_pty.b64(prefix)   # 頭から流し直すときの画面モードの立て直し
            send("hello", json.dumps(hello, separators=(",", ":")))
            for i in range(0, len(backlog), 32768):
                send("out", office_pty.b64(backlog[i:i + 32768]))
            while not viewer.dropped:
                try:
                    # 3 秒ごとに ping＝切れた視聴者を早く見つけて枠を空ける（15 秒だと素早いタブ切替で
                    # 古い購読が枠を埋め、見ている画面の更新が止まった）
                    item = viewer.q.get(timeout=3)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if item is None:
                    break                            # 遅すぎて切られた＝UI が再接続して backlog から戻る
                if isinstance(item, tuple):
                    send("exit", json.dumps({"code": item[1]}))
                    break
                # 溜まっているぶんを 1 つにまとめて送る（全画面 TUI は細切れに大量に書く）
                chunk = bytearray(item)
                while len(chunk) < 65536:
                    try:
                        nxt = viewer.q.get_nowait()
                    except queue.Empty:
                        break
                    if not isinstance(nxt, (bytes, bytearray)):
                        viewer.q.put(nxt)            # exit / 切断の印は次の周で扱う
                        break
                    chunk += nxt
                send("out", office_pty.b64(bytes(chunk)))
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout, OSError):
            pass
        finally:
            term.unsubscribe(viewer)

    def _term_streams(self):
        """R111-B0: 複数の端末（最大 MAX_TERMS 本）の出力を **1 本の SSE** で流す（多重化）。
        なぜ多重化か: このサーバーは HTTP/1.0＝短い要求（/api/office・一覧・打鍵の POST・resize）も毎回 1 接続を
        食う。ブラウザの同一オリジンは 6 本まで＝4 ペインを 1 本ずつ購読すると /api/events と合わせて 5 本が
        塞がり、打鍵の POST が後ろで待つ。1 本に束ねれば長い接続はペイン数に依らず 2 本・スレッドも 1。

        契約（単一の /api/term/stream に id を足しただけ・出力はここでしか外へ出ない＝保存も中継もしない）:
          GET /api/term/streams?ids=<id>,<id>…&from=<id>:<n>,…（from は任意・ids に無い id の from は無視）
          event: hello  data: {…term.public()（id を含む）, "from": n, "reset": bool, "modes"?: b64}
          event: out    data: <id> <base64>          （JSON にしない＝id と空白 1 つ）
          event: exit   data: {"id": <id>, "code": int|null}   （未知の id は 404 にせずこれだけ・code null）
          : ping（3 秒）／全 id が exit したら閉じる／どれかの視聴者が切られたら接続ごと閉じる
          （クライアントは全 id の from で繋ぎ直す＝単一 stream の再接続と同じ意味）。"""
        qs = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        raw_ids = qs.get("ids") or [""]
        ids = raw_ids[0].split(",") if len(raw_ids) == 1 and raw_ids[0] else []
        if (not 1 <= len(ids) <= office_pty.MAX_TERMS or len(set(ids)) != len(ids)
                or not all(re.fullmatch(r"[0-9a-f]{16}", t) for t in ids)):
            return self._deny(400, "bad ids")
        raw_from = qs.get("from") or [""]
        if len(raw_from) != 1:
            return self._deny(400, "bad from")
        froms = {}
        for part in raw_from[0].split(",") if raw_from[0] else ():
            m = re.fullmatch(r"([0-9a-f]{16}):([0-9]{1,15})", part)
            if not m or m.group(1) in froms:
                return self._deny(400, "bad from")
            froms[m.group(1)] = int(m.group(2))
        # 共有キュー 1 本＝(tid, item) を端末をまたいで到着順に積む（端末ごとの順序は FIFO が守る）
        shared = queue.Queue(maxsize=office_pty.VIEWER_QUEUE * len(ids))
        subs = {}          # tid → (term, viewer, backlog, start, reset, prefix)
        try:
            for tid in ids:
                term = office_pty.get(tid)
                if term is None:
                    continue                         # 一覧との競合＝その id だけ exit null（接続全体は殺さない）
                backlog, start, reset, prefix, viewer = term.subscribe(
                    froms.get(tid), viewer=office_pty.mux_viewer(shared, tid))
                subs[tid] = (term, viewer, backlog, start, reset, prefix)

            def send(kind, payload):
                self.wfile.write(f"event: {kind}\ndata: {payload}\n\n".encode("ascii"))
                self.wfile.flush()

            def send_exit(tid, code):
                send("exit", json.dumps({"id": tid, "code": code}, separators=(",", ":")))

            self._send_sse_headers()
            for tid in ids:
                if tid not in subs:
                    send_exit(tid, None)
                    continue
                term, _viewer, backlog, start, reset, prefix = subs[tid]
                hello = {**term.public(), "from": start, "reset": reset}
                if prefix:
                    hello["modes"] = office_pty.b64(prefix)
                send("hello", json.dumps(hello, separators=(",", ":")))
                for i in range(0, len(backlog), 32768):
                    send("out", f"{tid} {office_pty.b64(backlog[i:i + 32768])}")
            live = set(subs)                         # まだ exit が届いていない id
            carry = None                             # まとめ送りで引いた「次の周で扱う」1 件
            while live:
                if any(subs[tid][1].dropped for tid in live):
                    break                            # 遅すぎて切られた＝全 id の from で繋ぎ直してもらう
                if carry is not None:
                    item, carry = carry, None
                else:
                    try:
                        item = shared.get(timeout=3)  # 3 秒ごとに ping（単一 stream と同じ）
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                tid, payload = item
                if tid not in live:
                    continue                         # exit の後に残った印（追い出しの None 等）
                if payload is None:
                    break                            # 追い出された／溢れた＝接続ごと閉じる
                if isinstance(payload, tuple):
                    send_exit(tid, payload[1])
                    live.discard(tid)
                    continue
                # 同じ端末の出力が続く間だけ 1 つにまとめる。違う端末・exit を引いたら put で戻さず carry へ
                # （戻すとキューの末尾に回り、その端末の後続より後になる＝順序が崩れる）
                chunk = bytearray(payload)
                while len(chunk) < 65536:
                    try:
                        nxt = shared.get_nowait()
                    except queue.Empty:
                        break
                    if nxt[0] == tid and isinstance(nxt[1], (bytes, bytearray)):
                        chunk += nxt[1]
                    else:
                        carry = nxt
                        break
                send("out", f"{tid} {office_pty.b64(bytes(chunk))}")
        except (BrokenPipeError, ConnectionResetError, TimeoutError, socket.timeout, OSError):
            pass
        finally:
            for term, viewer, *_ in subs.values():
                term.unsubscribe(viewer)

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

    def _csrf_strict(self):
        """R100-0: 権限を動かす POST（承認への回答・スマホの登録/失効・遠隔実行の許可リスト・封書の ON）は
        **Origin 必須**（loopback）＋ Sec-Fetch-Site があれば same-origin。
        loopback＋X-Office-Local は「同じユーザーの任意のプロセス」であって「Mac の前の人間」ではない
        （curl 1 本で付けられる）。Origin を要求すると、ブラウザの画面操作以外の素朴な呼び出しは止まる。
        ★Origin も偽装はできる＝完全な境界ではない（docs/security-notes.md）。だから allow と登録は通知も出す。"""
        if not self._csrf_ok():
            return False
        if not _LOOPBACK_ORIGIN.match(self.headers.get("Origin") or ""):
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return site is None or site == "same-origin"

    def _activity(self, route):
        if self.client_address[0] not in ("127.0.0.1", "::1") or not self._csrf_ok():
            return self._deny(403, "cross-site request blocked")
        qs = parse_qs(self.path.partition("?")[2], keep_blank_values=True)
        allowed = {"since", "sid", "limit"} if route == "/api/timeline" else {"day", "since"}
        if any(key not in allowed or len(values) != 1 or not values[0] or len(values[0]) > 128
               for key, values in qs.items()):
            return self._deny(400, "bad query")
        try:
            since = float(qs["since"][0]) if "since" in qs else None
            if route == "/api/timeline":
                data = office_timeline.timeline_json(_HOME, since=since if since is not None else 0,
                    sid=qs.get("sid", [""])[0], limit=int(qs.get("limit", ["200"])[0]))
            else:
                data = office_timeline.digest_json(_HOME, day=qs.get("day", [None])[0], since=since)
        except (ValueError, OverflowError, OSError):
            return self._deny(400, "bad query")
        self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._deny(403, "invalid host")
        route = self.path.split("?", 1)[0]
        if route in ("/api/timeline", "/api/digest"):
            return self._activity(route)
        if self.path.split("?", 1)[0] == "/api/events":
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            return self._events()
        if route in ("/api/term/list", "/api/term/stream", "/api/term/streams"):
            # R99: 端末はこの Mac の前の人だけ（loopback＋CSRF）。中継・別オリジンからは届かない。
            if not self._loopback() or not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            if route == "/api/term/stream":
                return self._term_stream()
            if route == "/api/term/streams":
                return self._term_streams()          # R111-B0: 複数の端末を 1 接続で（多重化）
            # 一覧を取りに来た＝コックピットが開いている（見えていない端末も「見られている」に数える）
            return self._send(200, json.dumps({"terms": office_pty.list_public(touch=True),
                                               "max": office_pty.MAX_TERMS}).encode("utf-8"),
                              "application/json; charset=utf-8")
        elif self.path.startswith("/api/office"):
            # M4: 他GETと同様CSRFゲートを掛ける。office_json は templates全文・recipes・
            # results.output・question本文を含み、かつ res_summary() が**ユーザーのAPIキーで
            # 外部HTTP**を誘発する（純粋な読取ではない）＝任意Webページからの副作用を防ぐ。
            # ローカルUIの api() は GET にも X-Office-Local を付けるのでUI側の変更は不要。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(office_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif route == "/api/diagnostics":
            # R105: β の「診断情報をコピー」。数と状態だけ（本文・フォルダ名・プロジェクト名は入れない＝office_diag）。
            # 見せてから本人が貼る＝テレメトリではない。ローカル UI 専用（office と同じ CSRF 境界）
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            log_dir = next((d for d in (ROOT.parent / "logs", ROOT / "logs") if d.is_dir()), None)
            # plan.md #5: この本文だけ _LANG（サーバーの設定言語）固定で、UIの表示言語（デモの既定=英語・
            # 設定で切替えた言語）と食い違っていた。クライアントが今見ている言語を ?lang= で渡す
            # （beta.js）。不正値・無指定はサーバーの既定（_LANG）へ落とす。
            qs_lang = parse_qs(self.path.partition("?")[2]).get("lang", [""])[0]
            office = office_json()                     # 先に読む＝設定の言語（_LANG）が反映されてから既定を決める
            diag_lang = qs_lang if qs_lang in LANGS else (office.get("lang") if office.get("lang") in LANGS else _LANG)
            text = office_diag.build(office, office_diag.collect_env(log_dir), diag_lang)
            self._send(200, json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.split("?", 1)[0] == "/api/external/openclaw":
            # 外部接続の器はローカルUI専用。office_jsonへは混ぜない。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(external_openclaw_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif route == "/api/door/bots":
            # R128 #4: ⚙ の Bot パネルの一覧（名前・役・最後に動いた時刻・相談の有無・未読の数・消音・登録コマンド）。
            # 名前と役は Bot の行と同じ（中継にも載る）が、登録コマンドはこの Mac のパスを含む＝ローカル UI 専用（loopback＋CSRF・office_json に載せない）
            if not self._loopback() or not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(door_bots_json(), ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/pair/list"):
            # 端末一覧（secretは含まないが label/id を晒すので）別オリジンGETを弾く。
            # UIの api() は GET にも X-Office-Local を付与するので同一オリジンは通る。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            # R87: 会話共有（dialogRelay）の状態も同じ画面で見せる＝📱スマホ連携が唯一の切替 UI
            cfg = load_config()
            self._send(200, json.dumps({"devices": list_devices(),
                                        "dialogRelay": cfg.get("dialogRelay") is True,
                                        "dialogAvailable": bool(dialog_seal.AVAILABLE)},
                                       ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif self.path.startswith("/api/approve/status"):
            # R100-1b: スマホから許可の状態（許可鍵の照合コード・今日の件数）もローカル UI 専用
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            self._send(200, json.dumps(approve_status(), ensure_ascii=False).encode("utf-8"),
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
        elif self.path.split("?", 1)[0] == "/api/session/activity":
            # R121: 作業ビュー（Claude Code アプリ型）の行＝スマホの封書 term と同じ office_term の行（伏字済み）を Mac へ。
            # 門は /api/session/dialog と同じ（loopback+CSRF・office_json 非搭載＝中継へ流れる経路が無い）。未知 session は 200+空。
            if not self._csrf_ok():
                return self._deny(403, "cross-site request blocked")
            ok, out = activity_page(parse_qs(self.path.split("?", 1)[1] if "?" in self.path else ""))
            if not ok:
                return self._deny(400, out)
            self._send(200, json.dumps({"ok": True, **out}, ensure_ascii=False).encode("utf-8"),
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

    def _term_post(self, route, data):
        """R99: 端末の操作。do_POST の門（Host＋CSRF）を通ったあと、ここでも loopback を見る。"""
        if not self._loopback():
            return self._deny(403, "loopback required")

        def reply(code, obj):
            return self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")

        if route == "/api/term/open":
            argv, cwd, env, session, err = term_spec(data.get("kind"), data.get("projectId"))
            if err:
                code, msg = _TERM_ERRORS[err]
                return reply(code, {"ok": False, "error": err, "message": msg})
            term, err = office_pty.open_term(argv, cwd=cwd, env=env, cols=data.get("cols"),
                                             rows=data.get("rows"), kind=data.get("kind"),
                                             session=session, project_id=data.get("projectId") or None)
            if term is None:
                code, msg = _TERM_ERRORS[err]
                return reply(code, {"ok": False, "error": err, "message": msg})
            return reply(200, {"ok": True, "term": term.public()})
        term = office_pty.get(data.get("id"))
        if term is None:
            return reply(404, {"ok": False, "error": "no_term", "message": "端末が見つかりません"})
        if route == "/api/term/input":
            raw = office_pty.unb64(data.get("data"))
            if raw is None:
                return reply(400, {"ok": False, "error": "bad_input"})
            return reply(200, {"ok": term.write(raw)})
        if route == "/api/term/resize":
            return reply(200, {"ok": term.resize(data.get("cols"), data.get("rows"))})
        if route == "/api/term/close":
            office_pty.close(term.id)            # 止めて、一覧からも外す
            return reply(200, {"ok": True})
        return self._deny(404, "not found")

    def do_POST(self):
        if not self._host_ok():
            return self._deny(403, "invalid host")
        if not self._csrf_ok():
            return self._deny(403, "cross-site request blocked")
        route = self.path.split("?", 1)[0]
        # 振り分けは self.path の前方一致（startswith）なので、判定も前方一致にそろえる
        # （完全一致だと /api/pair/newX が Origin 無しで振り分けの枝に入った・R100-0 のレビュー）。
        if route.startswith(_STRICT_POST) and not self._csrf_strict():
            return self._deny(403, "browser origin required")
        try:
            n = int(self.headers.get("Content-Length", 0))
            body_limit = 100_000
            data = json.loads(self.rfile.read(min(max(n, 0), body_limit)).decode("utf-8")) if n else {}
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        extra = {}
        if route.startswith("/api/term/"):
            return self._term_post(route, data)
        if route in DOOR_BOT_ROUTES:
            # R128 #4: Bot パネル（上の _STRICT_POST の門＝Origin 必須を通った後・ここでも loopback を見る）。断りは code と口の文
            if not self._loopback():
                return self._deny(403, "loopback required")
            code, out = door_bot_post(route, data)
            return self._send(code, json.dumps(out, ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")
        if route == "/api/seen":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            try:
                result = office_timeline.mark_seen(_HOME)
            except OSError:
                return self._deny(500, "could not save seen time")
            return self._send(200, json.dumps(result).encode("utf-8"),
                              "application/json; charset=utf-8")
        elif route == "/api/project/arch":
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            if "arch" not in data:
                return self._deny(400, "accessory required")
            ok, msg = set_project_arch(data.get("projectId"), data["arch"])
        elif route == "/api/avatar/style":
            # R91: アバター1体の見た目（アクセサリ・殻の色）。scope で個体/フォルダを選ぶ。
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            fields = {k: data[k] for k in ("arch", "color") if k in data}
            ok, msg = set_avatar_style(data.get("avatarId"), data.get("scope"), fields)
        elif self.path.startswith("/api/instruct"):
            try:
                ok, msg = post_instruction(data.get("session", ""), data.get("text", ""), src="local")
            except OSError:
                # R90-D10: Codex への一時障害（応答なし・起動不能）。relay は残置して再試行するが、
                # ローカル UI には正直に失敗を返す（例外を握って接続を切らない）
                ok, msg = False, L_now("Codex が応答しません（少し待ってからもう一度）",
                                       "Codex did not respond (try again shortly)")
        elif route == "/api/approval/reply" and _is_bot_session(str(data.get("session") or "")):
            # R127: Bot（出勤の口）の相談への答え。掲示板は使わない＝言葉だけを口の inbox へ（allow は 400・askId で 1 件）
            ok, msg, extra = answer_bot(str(data.get("session")), data)
        elif route == "/api/approval/reply":
            # R86-H: 止まっているセッションへ「いま」答える（指示ポストは**ターンが終わるまで
            # 届かない**ので、承認まちの相手には構造的に届かなかった＝ユーザー報告の本体）。
            # ここは loopback+CSRF 配下＝Macの前の人間なので、実行の許可(allow)も出せる。
            sess = str(data.get("session") or "")
            want = str(data.get("askId") or "") or None
            asks = pending_approvals(sess)
            # R100-0: 答える相手は askId で 1 件に決める（同じセッションで 2 件並ぶと、表示されていない方に
            # allow が効いた＝実測）。askId 無しで通すのは、並んでいるのが移行窓の旧 hook の 1 件だけのとき。
            ask = next((a for a in asks if a["askId"] == want), None)
            if not asks:
                ok, msg = False, L_now(
                    "そのセッションはいま何も聞いていません（もう答えられたか、承認フックが未配線です）",
                    "that session is not asking anything right now")
            elif not ask:
                ok, msg = False, (L_now("その要求はもう終わっています（表示を更新してください）",
                                        "that request is already over (refresh the view)") if want
                                  else L_now("画面を読み込み直してください（どの要求への回答か分かりません）",
                                             "reload the page (the answer does not name its request)"))
            else:
                behavior = "allow" if data.get("behavior") == "allow" else "deny"
                text = str(data.get("message") or "")
                if behavior == "deny" and not text.strip():
                    ok, msg = False, L_now("回答の内容が空です", "empty answer")
                else:
                    try:
                        write_approval_reply(sess, behavior, text, src="local", ask_id=ask["askId"])
                        if behavior == "allow":
                            # R100-0: 許可は必ず Mac に見える形で残す（同じユーザーの別プロセスが黙って allow を
                            # 書いても気づける）。本文はローカルの通知だけ（中継には出ない）。
                            _notify_security(L_now("✅ Mac で許可", "✅ Approved on the Mac"),
                                             f"{ask['tool']}: {short(ask['title'], 60)}")
                        ok = True
                        msg = (L_now("許可しました", "approved") if behavior == "allow"
                               else L_now("回答を届けました", "answer delivered"))
                        extra = {"kind": ask["kind"], "askId": ask["askId"]}
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
            # secret を返すのは loopback+CSRF 済みのローカルUIのみ（_host_ok/_csrf_strict 配下）
            # R100-0: スマホの登録は「中継から指示を出せる端末」を増やす操作＝Mac の前での物理確認と通知
            # （同じユーザーの別プロセスが黙って端末を足せない）。
            label = short(str(data.get("label") or ""), 40)
            if not office_actions._confirm_local(
                    L_now(f"📱 新しいスマホを登録しますか？\n{label or '（名前なし）'}",
                          f"📱 Register a new phone?\n{label or '(unnamed)'}"),
                    L_now("登録", "Register")):
                ok, msg = False, L_now("Mac の確認でキャンセルされました", "cancelled on the Mac")
            else:
                dev = new_device(data.get("label", ""))
                pu = pair_url(dev)
                ok, msg = True, "発行しました"
                extra = {**dev, "pairUrl": pu, "relayConfigured": bool(pu), "qrSvg": pair_qr_svg(pu)}
                _notify_security(L_now("📱 スマホを登録しました", "📱 Phone registered"), label or dev.get("device_id", ""))
        elif self.path.startswith("/api/pair/revoke"):
            ok = revoke_device(data.get("device_id", ""))
            msg = "失効しました" if ok else "端末が見つかりません"
        elif self.path.startswith("/api/pair/extend"):
            # R116-S1b: つながりの延長＝中継から指示を出せる期間を延ばす操作＝新しい登録と同じく Mac の前での物理確認と通知。
            # ここでは「作り直してよい」の窓を開くだけ（秘密は作らない）。スマホがアプリを開くと rk を見て求め、封書で受け取る。
            dev_id = str(data.get("device_id") or "")
            rec = load_devices().get("devices", {}).get(dev_id)
            label = short(str((rec or {}).get("label") or dev_id), 40) if isinstance(rec, dict) else dev_id
            if not _device_live(rec, int(time.time())):
                ok, msg = False, L_now("延長できない端末です（失効・期限切れ・見つからない）",
                                       "This phone can't be extended (revoked, expired or unknown)")
            elif not office_actions._confirm_local(
                    L_now(f"📱 このスマホのつながりを 30 日延長しますか？\n{label}",
                          f"📱 Extend this phone's pairing by 30 days?\n{label}"),
                    L_now("延長", "Extend")):
                ok, msg = False, L_now("Mac の確認でキャンセルされました", "cancelled on the Mac")
            else:
                ok, msg, until = rekey_allow(dev_id)
                if ok:
                    extra = {"rekeyUntil": until}
                    with _lock:
                        _mark_written()
                    office_actions._audit({"kind": "rekey", "device": dev_id, "ts": int(time.time()), "state": "armed"})
                    _notify_security(L_now("📱 スマホの延長を受け付けました（スマホでアプリを開くと完了）",
                                           "📱 Phone extension armed (open the app on the phone to finish)"), label)
        elif self.path.startswith("/api/pair/rekey"):
            # R116-S1b: relay_agent が rky- の封筒をそのまま回す。署名も段（req／ack）もここで確かめ直す（relay_agent を信じない）。
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            ok, msg, extra = _pair_rekey(data)
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
        elif self.path == "/api/hire":
            # R90-D11: ローカル（loopback+CSRF）からの雇用。パスは受け取らず projectId で引き当てる
            ok, msg, extra = hire_session(data.get("projectId"), data.get("prompt"), data.get("name") or "",
                                          worktree=bool(data.get("worktree")))
        elif self.path.startswith("/api/dialog/sealed"):
            # R87: 封緘するのは daemon（transcript を読める唯一のプロセス）。relay_agent は
            # dlg-封筒を検証してここへ 127.0.0.1 で回し、返る暗号文だけを中継へ載せる。
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            ok, msg, extra = _dialog_sealed(data)
        elif self.path.startswith("/api/approval/remote_set"):
            # R103-1: rst-（スマホから ON/OFF）。/api/approval/remote の前方一致より先に振り分ける
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            ok, msg, extra = _approve_remote_set(data)
        elif self.path.startswith("/api/approval/remote"):
            # R100-1b: relay_agent が apv- の封筒をそのまま回す。device_id も署名も**ここで**確かめ直す
            # （relay_agent を信じない・office_approve.approve の 14 段）。allow を書くのはその最後だけ。
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            ok, msg, extra = _approve_remote(data)
        elif self.path.startswith("/api/approve/enroll_ticket"):
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            dev_id = str(data.get("device") or "")
            ok, msg, ticket = office_approve.new_ticket(dev_id, data.get("projects", "*"), load_devices())
            if ok:
                url, _tok = relay_config()
                link = f"{url}/app/#v=1&enr={ticket}&d={dev_id}" if url else ""
                extra = {"ticket": ticket, "link": link, "qrSvg": pair_qr_svg(link),
                         "expires": int(time.time()) + office_approve.TICKET_TTL}
        elif self.path.startswith("/api/approve/enroll"):
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            ok, msg, extra = _approve_enroll(data)
        elif self.path.startswith("/api/approve/revoke"):
            ok = office_approve.revoke_key(str(data.get("device") or ""))
            msg = "失効しました" if ok else "許可鍵が見つかりません"
        elif self.path.startswith("/api/config/remote_approve"):
            # R100-1b: ON は Mac の前での物理確認（既定キャンセル・10 秒）＋通知。OFF は確認なし（いつでも止められる）。
            # R118（本人裁定 2026-10-13「常に ON」）: ON は期限なし（hours は読まない）
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            on = data.get("on")
            if on is True and not office_actions._confirm_local(
                    # R116-S0: 同意の文の正本は office_approve.remote_on_consent（各行 120 字以下・例は classify と突き合わせ済み）
                    office_approve.remote_on_consent(office_lang()),
                    L_now("ON にする", "Turn on")):
                ok, msg = False, L_now("Mac の確認でキャンセルされました", "cancelled on the Mac")
            else:
                ok, msg, until = office_approve.set_remote(on)
                if ok:
                    extra = {"until": until}
                    _notify_security(L_now("📱 スマホから許可", "📱 Phone approvals"),
                                     L_now("ON にしました（期限なし）", "turned on (no time limit)") if on is True
                                     else L_now("OFF にしました", "turned off"))
        elif self.path.startswith("/api/config/dialog_relay"):
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._deny(403, "loopback required")
            ok, msg = set_dialog_relay(data.get("on"))
            if ok:
                extra = {"dialogRelay": msg}
        elif self.path.startswith("/api/action/exec"):
            # R79-10: 実行者は office_server（Automation TCC同意済み＝osascript経路を持つ）。
            # relay_agent は act-封筒を検証してここへ 127.0.0.1 で回すだけの配達員でいる。
            ok, msg, extra = _action_exec(data)
        elif self.path.startswith("/api/keys/set"):
            ok, msg = set_office_key(data.get("name"), data.get("value"))
        elif self.path.startswith("/api/providers/test"):
            ok, msg, extra = provider_test(data)                # R103-5: 接続テスト（Origin 必須・鍵は返さない）
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

    listen キュー（backlog）は 128（既定は 5）。macOS は溢れた接続に即座に RST を返し、ブラウザは UI の
    ES モジュール（約 100 本）を HTTP/1.0（1 要求 1 接続）で一斉に取りに来る。負荷で accept が遅れると
    ERR_CONNECTION_RESET → 動的 import が落ちて「Couldn't load the UI」で止まっていた
    （2026-09-26 実測: 高負荷で 12 回中 2 回が起動失敗 → 128 で 12/12・リセット 0）。番人= tests/test_listen_backlog.py
    """
    request_queue_size = 128

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
# R115-S3: 切れたキャッシュの更新（status_board の初回の全読み・外部 HTTPS＝プロバイダごとに最大 6 秒）を呼び手の外へ。
# daemon と relay_agent の常駐の main() が True（relay_agent の配達ループも外部のタイムアウトで止まる＝スマホの許可の配達が遅れる）。
# --once・--dump・tests は同期のまま。裏の更新は同時に 1 本だけ（_RES_BG・_RES_LOCK で守る・thread はテストが join する）
_ASYNC_RES = False
_RES_LOCK = threading.Lock()
_RES_BG = {"busy": False, "thread": None}


# R102-5: Mac の前に人がいるか（キーボード・マウスの無操作が MAC_ACTIVE_IDLE 秒未満）。いる間はスマホへ通知しない
# （Worker の sendAttnPushes が office.macActive を見る）。ioreg の HIDIdleTime（ナノ秒）を 5 秒ごとに 1 回（1 回 約 7ms）。
# 読めなければ None＝キーごと載せない（通知は従来どおり）。テストは OFFICE_FAKE_MAC_IDLE（秒）で差し替える。
MAC_ACTIVE_IDLE = 120
_MAC_ACTIVE = {"t": -1e9, "v": None}
_HID_IDLE_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def mac_active(now=None):
    fake = os.environ.get("OFFICE_FAKE_MAC_IDLE")
    if fake is not None:
        try:
            return 1 if float(fake) < MAC_ACTIVE_IDLE else 0
        except ValueError:
            return None
    now = time.time() if now is None else now
    if 0 <= now - _MAC_ACTIVE["t"] < 5:
        return _MAC_ACTIVE["v"]
    v = None
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4", "-k", "HIDIdleTime"],
                             capture_output=True, text=True, timeout=2).stdout
        m = _HID_IDLE_RE.search(out or "")
        if m:
            v = 1 if int(m.group(1)) / 1e9 < MAC_ACTIVE_IDLE else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        v = None
    _MAC_ACTIVE.update(t=now, v=v)
    return v


def res_summary(now=None):
    """R82: office_json["res"] v2。旧キー(fiveHour/sevenDay/staleSec)は後方互換で温存し、
    providers[]（多プロバイダ最小ゲージ）を追加。60秒キャッシュ・fail-soft
    （補助データが office_json 本体を殺さない）。
    R115-S3: キャッシュが切れていれば、_ASYNC_RES（daemon・relay_agent の常駐）では裏で 1 本だけ更新して旧値（無ければ None
    ＝office-json.md の「取得不可時は null」）を即返す＝走査（と、その呼び手）が外部 HTTPS を待たない。旗 OFF は同期で更新する。"""
    now = time.time() if now is None else now
    cache = _RES_CACHE
    if now - cache["ts"] < 60:
        return cache["data"]
    if not _ASYNC_RES:
        return _res_refresh(now)
    with _RES_LOCK:
        if not _RES_BG["busy"] and now - cache["ts"] >= 60:     # 待つ間に裏の更新が終わっていたら起こし直さない
            _RES_BG["busy"] = True
            try:
                th = threading.Thread(target=_res_refresh_bg, args=(now,), name="office-res", daemon=True)
                _RES_BG["thread"] = th
                th.start()
            except RuntimeError:                # スレッドを起こせない＝旧値を返し、次の呼び出しで再試行
                _RES_BG["busy"] = False
    return cache["data"]


def _res_refresh(now):
    """res の値を作り直してキャッシュへ置く（外部 HTTPS を含みうる・例外は外へ出さない）。"""
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
    cache = _RES_CACHE
    cache["data"] = data                        # 値を先に・時刻を後に（裏の更新の途中で読んだ呼び手が新しい時刻と古い値を組まない）
    cache["ts"] = now
    return data


def _res_refresh_bg(now):
    try:
        _res_refresh(now)
    finally:
        with _RES_LOCK:
            _RES_BG["busy"] = False


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



# ── R99 Phase 2: コックピットの本物の端末 ─────────────────────────────────────────────
# 掟:
#   - **argv はサーバーが組む**。クライアントが渡せるのは kind（shell/claude/codex）と projectId だけ
#     ＝画面から任意のコマンドを起こす口は無い（起こした後に打つのは本人＝それが端末の仕事）。
#   - cwd は projects_index に在る登録済みプロジェクトだけ（_launch_target_for＝launch/hire と同じ）。
#     shell だけはプロジェクト無しでホームに開ける。
#   - claude は `--session-id <uuid>` で起こす＝**どの端末がどのセッションかを推測でなく確定で持つ**
#     （外で起きたセッションの pty は奪えないので、対応が取れるのはここで起こしたものだけ）。
#   - 出力は office_pty のメモリにだけ在る。office_json にも /api/events にも載せない。
#   - loopback+CSRF のみ（do_GET/do_POST の門＋ここでも loopback を見る）。中継からは届かない。
TERM_KINDS = ("shell", "claude", "codex")


def _login_shell():
    """本人のログインシェル。OFFICE_PTY_SHELL（テスト注入口）> パスワードDB > /bin/zsh。"""
    override = os.environ.get("OFFICE_PTY_SHELL")
    if override:
        return override if os.access(override, os.X_OK) else None
    try:
        import pwd
        sh = pwd.getpwuid(os.getuid()).pw_shell
    except (ImportError, KeyError):
        sh = ""
    if sh and os.path.isabs(sh) and os.access(sh, os.X_OK):
        return sh
    return "/bin/zsh" if os.access("/bin/zsh", os.X_OK) else None


def _pty_env(bin_, shell):
    """端末の子の env。_child_env（最小集合・秘密は渡さない）に、端末として要るものだけ足す。"""
    env = _child_env(bin_)
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        try:
            import pwd
            user = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            user = ""
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor",
                "LANG": os.environ.get("LANG") or "en_US.UTF-8"})
    if shell:
        env["SHELL"] = shell
    if user:
        env["USER"] = env["LOGNAME"] = user
    return env


def term_spec(kind, project_id=None):
    """(argv, cwd, env, session, error) を返す。error が空でなければ起こさない。純粋にほぼ決定的
    （claude の session だけ新しい uuid）。argv にクライアント由来の文字列は 1 つも入らない。"""
    if kind not in TERM_KINDS:
        return None, None, None, None, "bad_kind"
    cwd = ""
    if project_id:
        cwd, _label = _launch_target_for(str(project_id))
    if not cwd and kind != "shell":
        # claude/codex はプロジェクトの中でだけ起こす（どの仕事か分からないセッションを作らない）
        return None, None, None, None, "no_project"
    # シェルはフォルダが引き当てられなくてもホームで開く（Codex のセッションなど、
    # projects_index に無いフォルダは普通に在る。そこで「開けない」にすると＋が死ぬ）
    cwd = cwd or str(Path.home())
    shell = _login_shell()
    if kind == "shell":
        if not shell:
            return None, None, None, None, "no_shell"
        return [shell, "-il"], cwd, _pty_env(None, shell), None, ""
    if kind == "claude":
        bin_ = _claude_bin()
        if not bin_:
            return None, None, None, None, "no_claude"
        sid = str(uuid.uuid4())
        return [bin_, "--session-id", sid], cwd, _pty_env(bin_, shell), sid, ""
    bin_ = _codex_bin()
    if not bin_:
        return None, None, None, None, "no_codex"
    return [bin_], cwd, _pty_env(bin_, shell), None, ""


_TERM_ERRORS = {
    "bad_kind": (400, "端末の種類が不正です"),
    "no_project": (404, "プロジェクトが見つかりません（過去に開いたフォルダだけ起こせます）"),
    "no_shell": (500, "ログインシェルが見つかりません"),
    "no_claude": (404, "claude が見つかりません"),
    "no_codex": (404, "codex が見つかりません"),
    "too_many": (429, f"端末は同時に {office_pty.MAX_TERMS} 本までです"),
    "spawn_failed": (500, "端末を起動できませんでした"),
}

# ── R90-D11: 雇う（新しいバックグラウンドセッションを起こす）＝ `claude --bg -n <name> "<prompt>"` ──
# 掟:
#   - cwd は projects_index に在る登録済みプロジェクトだけ（_launch_target_for＝「任意パスは起動できない」不変条件）
#   - argv 固定・shell=False・timeout・start_new_session・最小 env（office_actions と同じ Popen の掟）
#   - ローカル（loopback+CSRF）からのみ。中継からは config remoteHire:true のときだけ（既定 OFF＝
#     新規セッションは承認フックを通らない bypass 設定で起動されうるため、遠隔の既定は閉じる）
#   - 同時に走らせるのは 2 本まで（claude --bg は数秒で返るが、連打で Terminal を溢れさせない）
_HIRE_LOCK = threading.Lock()
_HIRE_ACTIVE = {"n": 0}
HIRE_MAX_ACTIVE = 2
HIRE_TIMEOUT = float(os.environ.get("OFFICE_HIRE_TIMEOUT") or 30)
_CLAUDE_BIN_CANDIDATES = ("~/.npm-global/bin/claude", "~/.local/bin/claude", "/opt/homebrew/bin/claude",
                          "/usr/local/bin/claude", "~/.claude/local/claude")
_HIRE_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,30}$")


def _claude_bin():
    """claude CLI の実体。OFFICE_CLAUDE_BIN（テスト注入口）> PATH > 既知の置き場。無ければ None。"""
    override = os.environ.get("OFFICE_CLAUDE_BIN")
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("claude")
    if found:
        return found
    for cand in _CLAUDE_BIN_CANDIDATES:
        c = os.path.expanduser(cand)
        if os.access(c, os.X_OK):
            return c
    return None


def hire_session(project_id, prompt, name="", worktree=False, req_id=None, device=""):
    """(ok, msg, extra) を返す。extra.state ∈ denied/busy/done/failed/duplicate。
    device は遠隔（act 封筒）の検証済み端末ID＝監査記録に残す（ローカルは空）。"""
    project_id = str(project_id or "")
    if not re.fullmatch(r"[0-9a-f]{12}", project_id):
        return False, "projectId が不正です", {"state": "denied"}
    prompt = " ".join(str(prompt or "").split())
    if not prompt:
        return False, "何をしてもらうか（指示）が空です", {"state": "denied"}
    if "\x00" in prompt or "\x00" in str(name or ""):
        return False, "使えない文字（NUL）が含まれています", {"state": "denied"}
    if len(prompt) > 4000:
        return False, "指示が長すぎます(4000字まで)", {"state": "denied"}
    name = str(name or "").strip()
    if name and not _HIRE_NAME_RE.match(name):
        return False, "名前は英数字・空白・_- で30字までです", {"state": "denied"}
    target, label = _launch_target_for(project_id)
    if not target:
        return False, "未登録のプロジェクトです（過去に開いたプロジェクトだけ雇えます）", {"state": "denied"}
    req_id = req_id or uuid.uuid4().hex[:16]
    # reqId を原子的に予約（同じ reqId の再配達・並列検証で二重起動しない＝Astra レビュー指摘）
    rec, created = office_actions.reserve_result(req_id, "hire", name or label, device=device)
    if not created:
        return rec["state"] in ("done", "running"), rec["state"], {"state": rec["state"], "reqId": req_id,
                                                                  "bgId": rec.get("bgId", "")}
    with _HIRE_LOCK:
        if _HIRE_ACTIVE["n"] >= HIRE_MAX_ACTIVE:
            office_actions.finish_result(rec, "busy", reason="max-active")
            return False, "いま雇用中です（少し待ってから）", {"state": "busy", "reqId": req_id}
        _HIRE_ACTIVE["n"] += 1
    try:
        fake = os.environ.get("OFFICE_FAKE_HIRE")
        if fake:
            Path(fake).write_text(json.dumps({"cwd": target, "prompt": prompt, "name": name,
                                              "worktree": bool(worktree)}, ensure_ascii=False),
                                  encoding="utf-8")
            bg_id, rc = "fake0001", 0
        else:
            bin_ = _claude_bin()
            if not bin_:
                office_actions.finish_result(rec, "failed", reason="claude-not-found")
                return False, "claude コマンドが見つかりません", {"state": "failed", "reqId": req_id}
            # ★ `--` でオプションを終端してから本文を渡す。これが無いと `--exec=<cmd>` のような本文が
            #   claude のオプション（--exec はシェル実行モード）に化け、`-w` は次の引数を worktree 名として食う
            #   （Astra レビューで実証）。
            argv = [bin_, "--bg"]
            if name:
                argv += ["-n", name]
            if worktree:
                argv += ["-w"]
            argv += ["--", prompt]
            try:
                rc, out, err = _run_child(argv, HIRE_TIMEOUT, cwd=target)
            except subprocess.TimeoutExpired:
                office_actions.finish_result(rec, "failed", reason="timeout")
                return False, "起動が応答しませんでした", {"state": "failed", "reqId": req_id}
            except OSError as e:
                office_actions.finish_result(rec, "failed", reason=str(e)[:80])
                return False, "claude を起動できませんでした", {"state": "failed", "reqId": req_id}
            m = re.search(r"\b([0-9a-f]{8})\b", out + "\n" + err)
            bg_id = m.group(1) if m else ""
        if rc != 0:
            office_actions.finish_result(rec, "failed", exitCode=rc)
            return False, "起動に失敗しました（claude --bg が非0で終了）", {"state": "failed", "reqId": req_id}
        office_actions.finish_result(rec, "done", bgId=bg_id, exitCode=0)
        notify_mac(L_now("🧑‍💼 入社", "🧑‍💼 New hire"), f"{label}: {name or prompt[:40]}")
        return True, L_now("雇いました（入口から出勤します）", "Hired (arriving at the entrance)"), \
            {"state": "done", "reqId": req_id, "bgId": bg_id}
    finally:
        with _HIRE_LOCK:
            _HIRE_ACTIVE["n"] -= 1


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
    if act["kind"] == "hire":
        # R90-D11: 遠隔からの雇用は config remoteHire:true のときだけ（既定 OFF）
        for r in office_actions.results_public(limit=office_actions.RESULT_KEEP):
            if r["reqId"] == act["reqId"]:
                return True, r["state"], {"state": r["state"], "reqId": r["reqId"]}
        if load_config().get("remoteHire") is not True:
            office_actions.register_result(act["reqId"], "hire", act.get("name") or "hire", "denied",
                                           reason="remote-hire-off", device=device)
            return False, "denied", {"state": "denied", "reqId": act["reqId"], "reason": "remote-hire-off"}
        ok, msg, extra = hire_session(act["project"], act["prompt"], act.get("name", ""),
                                      worktree=bool(act.get("worktree")), req_id=act["reqId"], device=device)
        return ok, extra.get("state", "failed"), {**extra, "label": act.get("name") or ""}
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


def _notify_security(title, body):
    """R100-0: 許可・登録の通知。テスト／dev（OFFICE_HOME 注入）では鳴らさない（start_watcher と同じ掟）。"""
    if os.environ.get("OFFICE_HOME") and not os.environ.get("OFFICE_FAKE_NOTIFY"):
        return
    notify_mac(title, body)


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
                 str(body).replace("\\", "").replace('"', "'")[:500],
                 str(title).replace("\\", "").replace('"', "'")[:60])],
            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass                          # 通知はベストエフォート（本流を殺さない）


def _bot_ask_id(prj):
    """R127: Bot（出勤の口）の行が持つ開いている相談の askId（32 桁）。Bot でない・相談が無い・形が違えば None。"""
    if prj.get("vendor") != "bot" and not _is_bot_session(prj.get("session")):
        return None
    ask = prj.get("ask")
    aid = ask.get("askId") if isinstance(ask, dict) else None
    return aid if isinstance(aid, str) and office_door.ASK_ID_RE.fullmatch(aid) else None


def _attn_key(prj):
    """❗ の鍵＝projectId（無ければ session）。R127: Bot の相談は askId も足す（Bot は 1 体 1 件＝同じ行の 2 件目は
    鍵が変わらず「新しく待ち始めた」にならなかった＝答えた直後に次の相談が来ても鳴らす・数える）。
    セッションの鍵は変えない（続けて出る許可の掲示のたびに鳴らさない・日報の数え方を変えない）。"""
    key = prj.get("projectId") or prj.get("session") or ""
    aid = _bot_ask_id(prj)
    return f"{key}#{aid}" if aid else key


def _quiet_sessions(employees):
    """R128 #4: 消音の Bot（⚙ の Bot パネル＝台帳の muted）の session の集合。Mac の知らせ（新しい ❗・待たせすぎ・朝のまとめ）は
    この Bot の相談では鳴らさない＝**鳴らさないだけ**（行・あなたの番・記録・日報の数には今までどおり出る）。
    Bot の行（session "bot-<id>"・id は valid_id の形）の muted が真偽の True のものだけ（ほかの行に muted が付いていても効かない
    ＝セッションは今までどおり鳴る）。"""
    return frozenset(e["session"] for e in employees or []
                     if isinstance(e, dict) and e.get("muted") is True and office_door.bot_of_session(e.get("session")))


def attention_diff(prev_ids, roster, quiet=frozenset()):
    """❗（質問/承認まち）のエッジ検出。新規に待ち始めたプロジェクトだけ
    [{disp, question, approvalMin}] で返す（R53.2: 通知本文に質問プレビューを載せるため。
    ローカルMacの通知＝transcript自体が手元にある前提なので本文露出はPWA Pushと別基準）。
    R127: 鍵は _attn_key（Bot の相談は askId つき）。
    R128 #4: quiet（_quiet_sessions＝消音の Bot）の相談は知らせ（1 つ目の戻り値）に出さない。鍵の集合（2 つ目）には入れる
    ＝消音を解いた瞬間に、前から開いている相談で鳴らさない（次の新しい相談から鳴る）。"""
    cur, loud = {}, set()
    for prj in roster or []:
        if prj.get("question") or (prj.get("approvalMin") or 0) > 0:
            key = _attn_key(prj)
            cur[key] = {
                "disp": prj.get("disp") or "?",
                "question": prj.get("question") or "",
                "approvalMin": prj.get("approvalMin") or 0,
            }
            if prj.get("session") not in quiet:
                loud.add(key)
    new_ids = [pid for pid in cur if pid not in (prev_ids or set()) and pid in loud]
    return [cur[pid] for pid in new_ids], set(cur)


# R125: 秘書の芽（本人 2026-11-10「秘書の層を小さく試す」）。新しい ❗ の知らせ（attention_diff）と朝夕のまとめでは
# 知らせていなかった 2 つだけを Mac に知らせる: ①エラーで止まった人（利用上限・API エラー・会話の長さの上限）＝止まった直後に 1 回
# ②❗ を長く待たせている人＝NUDGE_WAIT_MIN の段ごとに 1 回。沈黙が既定（朝のまとめと同じ）: 同じ人の同じ段は 2 度鳴らさない・
# 解消したら記憶を消す・最初の 1 回（daemon の起動直後）は今の状態を覚えるだけで鳴らさない。OFFICE_NUDGES=0 で止める。
NUDGE_WAIT_MIN = (15, 60)
NUDGE_ERR_FRESH_SEC = 600            # これより前に止まったエラーは「いま止まった」とは言わない（起動直後・古い残骸）


def _nudge_wait_min(e):
    """待たせている分（ui/core/kind.js の waitMinutes と同じ順: 掲示の分 → approvalMin → 質問なら最後に書いてからの分）。"""
    ask = e.get("ask") if isinstance(e.get("ask"), dict) else {}
    if isinstance(ask.get("min"), (int, float)) and ask["min"] >= 0:
        return int(ask["min"])
    if int(e.get("approvalMin") or 0) > 0:
        return int(e["approvalMin"])
    if e.get("question") or ask.get("kind") == "question":
        return int((e.get("age") or 0) // 60)
    return 0


def secretary_nudges(memory, employees, quiet=frozenset()):
    """→ (新しい記憶, [(題, 本文)])。memory=None は最初の 1 回＝覚えるだけ。純関数（通知は呼び出し側）。
    R128 #4: quiet（_quiet_sessions＝消音の Bot）の分は知らせに出さない。記憶（段）は今までどおり進める＝消音を解いた瞬間に、
    もう過ぎた段（15 分・60 分）で鳴らさない（次の段から鳴る）。"""
    first = memory is None
    memory = dict(memory or {})
    out, alive = [], set()
    err_word = {
        "limit": L_now("利用上限に達して止まりました", "Stopped at the usage limit"),
        "ctx": L_now("会話が長すぎて止まりました（/compact か新しいセッションへ）",
                     "Stopped: the conversation is too long (/compact or a new session)"),
        "api": L_now("API エラーで止まりました（もう一度頼むと続きます）",
                     "Stopped on an API error (ask again to continue)"),
    }
    for e in employees or []:
        sid = e.get("session") or ""
        if not sid or e.get("external"):
            continue
        alive.add(sid)
        name = short(e.get("title") or e.get("aiTitle") or e.get("disp") or "?", 30)
        mem = dict(memory.get(sid) or {})
        err = e.get("err") if isinstance(e.get("err"), dict) else None
        kind = err.get("k") if err and err.get("k") in err_word else None
        if kind and mem.get("err") != kind:
            if not first and int(err.get("ago") or 0) <= NUDGE_ERR_FRESH_SEC and sid not in quiet:
                out.append((f"⚠ {name}", err_word[kind]))
            mem["err"] = kind
        elif not kind:
            mem.pop("err", None)
        waiting = bool(e.get("question") or int(e.get("approvalMin") or 0) > 0 or e.get("ask"))
        if waiting:
            minutes = _nudge_wait_min(e)
            stage = max([m for m in NUDGE_WAIT_MIN if minutes >= m], default=0)
            if stage > int(mem.get("wait") or 0):
                if not first and sid not in quiet:
                    ask = e.get("ask") if isinstance(e.get("ask"), dict) else {}
                    what = (short(e["question"], 80) if e.get("question")
                            else L_now(f"許可を待っています: {ask.get('tool') or '?'}",
                                       f"Waiting for approval: {ask.get('tool') or '?'}"))
                    out.append((L_now(f"⏳ {name} が {minutes}分 待っています", f"⏳ {name} has waited {minutes}m"), what))
                mem["wait"] = stage
        else:
            mem.pop("wait", None)
        if mem:
            memory[sid] = mem
        else:
            memory.pop(sid, None)
    memory = {sid: m for sid, m in memory.items() if sid in alive}
    return memory, out


def _bot_answered(value, now_ts):
    """R127: seen に控えた Bot の相談（{session:"bot-…", askId}）が、人の答えで閉じたか（口の状態の閉じ方＝読むだけ）。"""
    bot_id = office_door.bot_of_session(value.get("session"))
    try:
        return bool(bot_id) and office_door.closed_how(bot_id, value.get("askId"), home=_HOME, now=now_ts) == "answered"
    except Exception:                                     # 口が読めない＝数えない（日報を捏造しない・監視を止めない）
        return False


def _attn_track(seen, roster, now_ts):
    """❗の滞在時間トラッキング。seen に初見epochと質問元sessionを保持し、
    解消した分の待たせ秒リストを返す。日報の「答えた❗・平均待たせ時間」の材料。
    R127: Bot（出勤の口）の相談は askId つきで控え（_attn_key）、消えたときは口が「人が答えた（answered）」と言う分だけ数える
    ＝期限切れ・取り下げ・登録の取り消しは「答えた」に数えない（R126 は Bot を丸ごと数えなかった）。Bot の分は時間軸
    （office_timeline）へは送らない（時間軸は Bot の行を持たない＝R126 の「Bot の稼働を混ぜない」・数え方の続きは R132）。"""
    cur = {}
    for prj in roster or []:
        if not (prj.get("question") or (prj.get("approvalMin") or 0) > 0):
            continue
        aid = _bot_ask_id(prj)
        if aid:
            cur[_attn_key(prj)] = (prj.get("session", ""), aid)
        elif prj.get("vendor") != "bot" and not _is_bot_session(prj.get("session")):
            cur[prj.get("projectId") or prj.get("session") or ""] = (prj.get("session", ""), None)
        # askId の無い Bot の行（手組み・壊れた形）は控えない＝答えたかを口に確かめられないものは数えない
    new_seen = {pid: value for pid, value in (seen or {}).items() if pid in cur}
    for pid, (sid, aid) in cur.items():
        if aid:
            new_seen.setdefault(pid, {"ts": now_ts, "session": sid, "askId": aid})
        else:
            new_seen.setdefault(pid, {"ts": now_ts, "session": sid} if sid else now_ts)
    # Accept the historical timestamp-only values too. With no known owner,
    # the writer resolves the existing ask span instead of using the new lead.
    finished, bots = [], []
    for pid, value in (seen or {}).items():
        if pid in cur:
            continue
        waited = max(0.0, now_ts - (value["ts"] if isinstance(value, dict) else value))
        if isinstance(value, dict) and value.get("askId"):
            if _bot_answered(value, now_ts):
                bots.append(waited)
            continue
        finished.append((pid, value.get("session", "") if isinstance(value, dict) else "", waited))
    resolved = [waited for _, _, waited in finished] + bots
    try:
        timeline = sys.modules.get("office_timeline")
        if timeline is not None:
            for pid, sid, waited in finished:
                timeline.record_ask_resolved(pid, sid, waited, now_ts)
    except Exception:
        pass
    return new_seen, resolved


def _today_answered(day=None):
    """今日解消した❗の数（`_append_daily_stats` が積む日次 stats から）。無ければ 0。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    try:
        d = json.loads((DAILY_DIR / f"{day}.stats.json").read_text(encoding="utf-8"))
        return int(d.get("answered") or 0)
    except (OSError, ValueError, TypeError):
        return 0


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
        digest = office_timeline.digest_json(_HOME, day=day, since=0)
        if digest["available"]:
            totals = digest["totals"]
            return {"answered": totals["asksAnswered"],
                    "totalWaitSec": totals["avgWaitSec"] * totals["asksAnswered"],
                    "medianWaitSec": totals["medianWaitSec"]}
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    try:
        d = json.loads((DAILY_DIR / f"{day}.stats.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _notification_digest(day, since=0, now=None):
    """Optional timeline reader; unavailable days retain the stats-only report."""
    try:
        data = office_timeline.digest_json(_HOME, day=day, since=since, now=now)
        return data if data.get("available") else None
    except Exception:
        return None


def _notification_projects(office, attention=False):
    projects = {}
    for index, row in enumerate(office.get("roster") or office.get("employees") or []):
        if attention and not (row.get("attention") or row.get("ask")
                              or row.get("question") or (row.get("approvalMin") or 0) > 0):
            continue
        projects[row.get("projectId") or row.get("session") or index] = row
    return list(projects.values())


def _notification_ask(row):
    ask = row.get("ask") or {}
    if ask.get("kind") == "permission":
        # Only a tool identifier, never the command/question/title from the ask.
        tool = ask.get("tool") or row.get("stuckTool") or ""
        tool = tool if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}", tool) else ""
        return L_now(f"{tool} の許可" if tool else "許可",
                     f"{tool} permission" if tool else "permission")
    if ask.get("kind") == "question" or row.get("question"):
        return L_now("質問", "an answer")
    return L_now("承認", "approval")


def _notification_asked_at(row, now):
    ask = row.get("ask") or {}
    if isinstance(ask.get("ts"), (int, float)):
        return ask["ts"]
    # Project age belongs to the most recently active member, not necessarily
    # the representative who is asking. Session briefs retain that member's age.
    lead = next((s for s in row.get("sessions", [])
                 if s.get("session") == row.get("session")), row)
    age = (row.get("approvalMin") or 0) * 60 or lead.get("age") or 0
    return now - age


def _notification_wait(row, now):
    return max(0, int((now - _notification_asked_at(row, now)) // 60))


def build_morning_report(office, now=None):
    """One unread summary across local midnights; None means deliberate silence."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    try:
        digest = office_timeline.digest_json(_HOME, day=today.isoformat(), now=now)
        since = digest["since"]   # Same server-owned timestamp as /api/seen.
    except Exception:
        return None              # No unread evidence in legacy cumulative stats.
    done = digest["totals"].get("tasksDone", 0)
    # The timeline retains 90 days. Include yesterday's evening even when the
    # last visit was before midnight; a day digest alone would miss that work.
    first = max(datetime.fromtimestamp(since).date(), today - timedelta(days=90))
    day = first
    while day < today:
        previous = _notification_digest(day.isoformat(), since=since, now=now)
        if previous:
            done += previous["totals"].get("tasksDone", 0)
        day += timedelta(days=1)
    # R128 #4: 消音の Bot の相談は朝のまとめを鳴らす根拠にも、本文の待ち人にもしない（鳴らさないだけ＝画面には出ている）
    quiet = _quiet_sessions(office.get("employees"))
    waiting = [row for row in _notification_projects(office, attention=True) if row.get("session") not in quiet]
    new_attention = any(since < _notification_asked_at(row, now) <= now for row in waiting)
    if not done and not new_attention:
        # An ask can arise and be answered before 08:30. Query existence only,
        # without the timeline endpoint's 500-event limit or any message text.
        try:
            db = office_timeline._reader(_HOME)
            try:
                new_attention = bool(db.execute("""SELECT 1 FROM spans
                    WHERE start>? AND start<=? AND kind='ask' AND label='confirmed'
                    LIMIT 1""", (since, now)).fetchone())
            finally:
                db.close()
        except Exception:
            pass
    if not done and not new_attention:
        return None
    title = L_now("🏢 おはようございます", "🏢 Good morning")
    body = L_now(f"{title} — 夜のあいだに ✅{done}件 終了、❗{len(waiting)}体 が待っています",
                 f"{title} — Overnight: ✅{done} completed, ❗{len(waiting)} waiting")
    if waiting:
        top = max(waiting, key=lambda row: _notification_wait(row, now))
        minutes = _notification_wait(top, now)
        elapsed = (L_now(f"{minutes // 60}時間", f"{minutes // 60}h") if minutes >= 60
                   else L_now(f"{minutes}分", f"{minutes}m"))
        disp = " ".join(str(top.get("title") or top.get("disp") or "?").split())[:80]
        body += L_now(f"（{disp}・{_notification_ask(top)}・{elapsed}）",
                      f" ({disp} · {_notification_ask(top)} · {elapsed})")
    return title, body


def _build_digest_report(office, date_label, digest):
    totals = digest["totals"]
    answered = totals.get("asksAnswered", 0)
    wait_min = round(totals.get("medianWaitSec", 0) / 60)
    done = totals.get("tasksDone", 0)
    roster = _notification_projects(office)
    waiting = len(_notification_projects(office, attention=True))
    start = datetime.strptime(date_label, "%Y-%m-%d")
    previous = _notification_digest((start - timedelta(days=1)).strftime("%Y-%m-%d"))
    comparison = ""
    if previous and previous["totals"].get("asksAnswered"):
        minutes = round(previous["totals"].get("medianWaitSec", 0) / 60)
        comparison = L_now(f"・昨日 {minutes}分", f"; yesterday {minutes} min")
    title = L_now("🏢 今日のオフィス", "🏢 Today's office")
    body = L_now(f"{title} — ❗{answered}件に答えた（中央値 {wait_min}分{comparison}）"
                 f" · ✅{done}件 · 稼働 {len(roster)}",
                 f"{title} — Answered ❗{answered} (median {wait_min} min{comparison})"
                 f" · ✅{done} completed · Active {len(roster)}")
    try:
        before = office_timeline.growth_json(_HOME, now=start.timestamp() - 0.000001)["office"]["level"]
        now = office.get("generatedAt") or time.time()
        end = min(now, (start + timedelta(days=1)).timestamp() - 0.000001)
        growth = (office.get("growth") if start.timestamp() <= now <= end else None)
        after = (growth or office_timeline.growth_json(_HOME, now=end))["office"]["level"]
        if after > before:
            body += f" · Lv.{before}→{after}"
    except Exception:
        pass
    if waiting:
        body += L_now(f" · 止まったまま帰る子: {waiting}体", f" · Still waiting at closing: {waiting}")
    lines = [f"# AIオフィス日報 {date_label}", "", body, "",
             f"- 出勤プロジェクト: {len(roster)}", f"- 完了タスク: {done}",
             f"- 答えた❗: {answered} 件（待たせ中央値 {wait_min}分）",
             f"- 稼働時間: {totals.get('activeMin', 0):g}分"]
    return title, body, "\n".join(lines) + "\n"


def build_daily_report(office, date_label):
    """日報。応答実績は digest を優先し、未観測日は従来の stats を読む。"""
    digest = _notification_digest(date_label)
    if digest:
        return _build_digest_report(office, date_label, digest)
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
        if "medianWaitSec" in stats:
            wait_min = round(stats["medianWaitSec"] / 60)
            lines.append(f"- 答えた❗: {answered} 件（待たせ中央値 {wait_min}分）")
        else:
            avg_min = round((stats.get("totalWaitSec") or 0) / answered / 60)
            lines.append(f"- 答えた❗: {answered} 件（平均待たせ {avg_min}分）")
        body += f" / ❗応答{answered}件"
    return "🏢 今日のAIオフィス", body, "\n".join(lines) + "\n"


def _watch_state():
    """_watch_loop が周をまたいで持つ記憶。prev＝❗の鍵・seen＝R54 の❗の滞在時間（projectId→初見epoch＝日報の応答実績）・
    nudge＝R125 秘書の芽（None＝最初の 1 回は覚えるだけ）・morning＝朝のまとめを済ませた日。"""
    return {"prev": set(), "seen": {}, "nudge": None, "morning": None}


def _watch_once(st, now_local=None):
    """_watch_loop の 1 周（60 秒ごと）: ❗エッジ検出・秘書の芽（止まった人・待たせすぎ＝R125）・08:30 の未読まとめ・18 時以降の日報。
    st（_watch_state）は呼び手が持ち回る＝例外で抜けても、そこまでに進めた記憶は残る（1 本のループだった頃と同じ）。
    R128 #4: 消音の Bot（_quiet_sessions）の相談では新しい ❗・待たせすぎ・朝のまとめを鳴らさない（記憶には入れる＝解いても古い相談で
    鳴らさない）。テストが 1 周だけ回せる（now_local で時刻を渡す＝朝のまとめと日報の時間帯に入らない時刻を選べる）。"""
    office = office_json()
    quiet = _quiet_sessions(office.get("employees"))
    st["seen"], resolved = _attn_track(st["seen"], office.get("roster"), time.time())
    if resolved:
        _append_daily_stats(resolved, datetime.now().strftime("%Y-%m-%d"))
    new_items, st["prev"] = attention_diff(st["prev"], office.get("roster"), quiet)
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
    if os.environ.get("OFFICE_NUDGES", "1") not in ("0", "false"):
        st["nudge"], nudges = secretary_nudges(st["nudge"], office.get("employees"), quiet)
        if nudges:
            title, body = nudges[0]
            if len(nudges) > 1:
                title += L_now(f" ほか{len(nudges) - 1}件", f" (+{len(nudges) - 1} more)")
            notify_mac(title, body)
    now_local = datetime.now() if now_local is None else now_local
    day = now_local.strftime("%Y-%m-%d")
    if now_local.hour == 8 and now_local.minute >= 30 and st["morning"] != day:
        marker = DAILY_DIR / f"{day}.morning"
        if not marker.exists():
            report = build_morning_report(office, now=now_local.timestamp())
            DAILY_DIR.mkdir(parents=True, exist_ok=True)
            marker.touch()  # Also remember silence; later events get ordinary edge pushes.
            if report:
                notify_mac(*report)
        st["morning"] = day
    if now_local.hour >= DAILY_HOUR:
        out = DAILY_DIR / f"{day}.md"
        if not out.exists():
            title, body, md = build_daily_report(office, day)
            DAILY_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            notify_mac(title, body)


def _watch_loop():
    """60秒ごとに _watch_once（❗エッジ検出・秘書の芽・08:30の未読まとめ・18時以降の日報）。監視は死なない。"""
    st = _watch_state()
    while True:
        time.sleep(60)
        try:
            _watch_once(st)
        except Exception as e:                      # noqa: BLE001 - 監視は死なない
            print(f"⚠ watch_loop: {e}", file=sys.stderr, flush=True)


def start_watcher():
    """常駐時のみ起動。OFFICE_HOME注入（テスト/dev）では通知しない
    （fixtureの❗で本物の通知が鳴る事故を防ぐ）。OFFICE_FAKE_NOTIFY があれば起動。"""
    if os.environ.get("OFFICE_HOME") and not os.environ.get("OFFICE_FAKE_NOTIFY"):
        return
    threading.Thread(target=_watch_loop, daemon=True).start()


def main():
    global _SWR, _ASYNC_RES
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
    print(f"🏢 AIオフィス起動: http://localhost:{args.port}  (Ctrl+Cで停止)")
    events_enabled = office_events is not None and os.environ.get("OFFICE_EVENTS") != "0"
    timeline = None
    try:
        # R115-S1: serve の間だけ、走査を request thread の外へ（SWR）。S3: res の更新（外部 HTTPS）も裏へ（_ASYNC_RES）。
        # 下の finally で戻す（main() を呼ぶテストへ旗を残さない）。--dump・relay_agent・tests は同期のまま
        _SWR = _ASYNC_RES = True
        if os.environ.get("OFFICE_TIMELINE") != "0":
            try:
                timeline = importlib.import_module("office_timeline")
                timeline.start(_HOME)
            except Exception:
                timeline = None
        if events_enabled:
            office_events.start(_HOME, on_change=lambda kind: _invalidate_cache())
        start_watcher()
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n退勤しました。")
    finally:
        _SWR = _ASYNC_RES = False
        if events_enabled:
            office_events.stop()
        if timeline is not None:
            timeline.stop()


if __name__ == "__main__":
    main()
