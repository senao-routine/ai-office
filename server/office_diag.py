#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R105: β の「診断情報をコピー」の中身（Mac の前の人が見てから自分で貼る＝テレメトリではない）。

作るのは数と状態だけの短い文。**会話の本文・フォルダ名・プロジェクト名・セッション名・URL・トークンは入れない**
（office_json から拾うのは counts・setup の真偽・sources の数・relay の % だけ。名前の入る roster/employees は数えるだけ）。
ログは行の中身を写さず、例外の型の名前（`ConnectionResetError` 等）と警告の行数だけ数える。

純関数 `build(office, env, lang)` が文を作り、`collect_env()`／`log_summary()` が Mac の事実を集める。
テスト= tests/test_office_diag.py（名前・パス・本文が混ざらないこと・数が合うこと）。
"""
import platform
import re
import sys
import time
from pathlib import Path

# 例外の型の名前だけを拾う（メッセージ部分は捨てる）。名前に人名やパスは入らない
_EXC_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{2,40}(?:Error|Exception|Warning))\b")
_WARN_RE = re.compile(r"(⚠|Traceback|\bERROR\b|\bError\b)")
# plan.md 追加（E 節の後）: relay_agent.py の WS 切断（server/relay_agent.py:1389 `⚠ WS切断（…生存・連続N回目）`）は
# 自動で戻る（数秒後に再接続・3連続失敗でも10分でHTTP降格→自動復帰・1日15〜20回が平常）。これを素の
# 警告行と同じバケツに数えると「警告 N 行」が本人の目に実際より多く・重く見える（実測: 9日間で診断の
# 警告行の大半がこれだった）。中身（例外メッセージ）は数えない・写さない点は従来どおり。
_WS_RECONNECT_RE = re.compile(r"⚠ WS切断（")
_TAIL_BYTES = 256 * 1024
_LOG_NAMES = ("office.daemon.log", "relay.daemon.log")


def _yn(flag, lang):
    if flag is True:
        return "OK"
    if flag is False:
        return "未設定" if lang == "ja" else "not set"
    return "?"


def log_summary(log_dir):
    """ログの末尾（各 256KiB）から、警告の行数と例外の型の名前の数だけを返す。中身は写さない。"""
    out = {}
    d = Path(log_dir) if log_dir else None
    if not d or not d.is_dir():
        return out
    for name in _LOG_NAMES:
        p = d / name
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - _TAIL_BYTES))
                text = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        warn = 0
        ws_reconnect = 0
        kinds = {}
        for line in text.splitlines():
            if _WS_RECONNECT_RE.search(line):
                ws_reconnect += 1
                continue                      # 自動で戻る切断は別枠＝素の警告行には数えない
            found = _EXC_RE.findall(line)
            if found or _WARN_RE.search(line):
                warn += 1
            for k in found:
                kinds[k] = kinds.get(k, 0) + 1
        out[name] = {"warn": warn, "kinds": kinds, "wsReconnect": ws_reconnect}
    return out


def collect_env(log_dir=None):
    mac = platform.mac_ver()[0] or ""
    return {
        "os": ("macOS " + mac) if mac else platform.system(),
        "arch": platform.machine(),
        "python": "%d.%d.%d" % sys.version_info[:3],
        "logs": log_summary(log_dir),
        "now": time.time(),
    }


def _kinds_text(kinds, limit=4):
    top = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return ", ".join(f"{k} ×{n}" for k, n in top)


def build(office, env, lang="ja"):
    """診断の文（複数行）。office は office_json() の結果、env は collect_env() の結果。"""
    ja = lang == "ja"
    o = office if isinstance(office, dict) else {}
    app = o.get("app") if isinstance(o.get("app"), dict) else {}
    setup = o.get("setup") if isinstance(o.get("setup"), dict) else {}
    counts = o.get("counts") if isinstance(o.get("counts"), dict) else {}
    sources = o.get("sources") if isinstance(o.get("sources"), dict) else {}
    relay = o.get("relay") if isinstance(o.get("relay"), dict) else None
    roster = o.get("roster") if isinstance(o.get("roster"), list) else []
    employees = o.get("employees") if isinstance(o.get("employees"), list) else []

    def num(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0

    lines = []
    lines.append("AI Office 診断情報（本文・フォルダ名・プロジェクト名は入っていません）" if ja
                 else "AI Office diagnostics (no conversation text, folder or project names)")
    ver = app.get("version") if isinstance(app.get("version"), str) else "?"
    data_v = o.get("v") if isinstance(o.get("v"), int) else "?"
    lines.append((f"版: {ver}（データ v{data_v}）") if ja else f"Version: {ver} (data v{data_v})")
    lines.append(f"Mac: {env.get('os', '?')} / {env.get('arch', '?')} / Python {env.get('python', '?')}")
    hook = _yn(setup.get("hookInstalled"), lang)
    events = _yn(setup.get("eventsWired"), lang)
    approve = _yn(setup.get("approvalWired"), lang)
    lines.append((f"つなぎ: 指示の受け取り={hook} ／ 動きの記録={events} ／ 許可の代理応答={approve}") if ja
                 else f"Wiring: instruction hook={hook} / event log={events} / approval hook={approve}")
    if relay is None:
        lines.append("スマホ中継: 未設定（または今日の記録なし）" if ja else "Phone relay: not set (or no record today)")
    else:
        pct = relay.get("pct")
        pct_s = f"{pct}%" if isinstance(pct, int) and not isinstance(pct, bool) else "?"
        lines.append((f"スマホ中継: 設定あり ／ 今日の使用量 {pct_s}") if ja else f"Phone relay: set / today {pct_s}")
    working, waiting, resting = num(counts.get("working")), num(counts.get("waiting")), num(counts.get("resting"))
    lines.append((f"セッション: {len(employees)}（作業中 {working}・指示待ち {waiting}・休憩 {resting}）／ プロジェクト {len(roster)}")
                 if ja else f"Sessions: {len(employees)} (working {working}, waiting {waiting}, resting {resting}) / projects {len(roster)}")
    cl = sources.get("claude") if isinstance(sources.get("claude"), dict) else {}
    cx = sources.get("codex") if isinstance(sources.get("codex"), dict) else {}
    oc = sources.get("openclaw") if isinstance(sources.get("openclaw"), dict) else {}
    cx_s = (str(num(cx.get("n"))) if cx.get("connected") is True else ("なし" if ja else "none"))
    oc_s = ("あり" if ja else "yes") if oc.get("connected") is True else ("なし" if ja else "none")
    lines.append((f"読み取り元: Claude Code 前面 {num(cl.get('fg'))}・裏 {num(cl.get('bg'))} ／ Codex {cx_s} ／ OpenClaw {oc_s}")
                 if ja else f"Sources: Claude Code fg {num(cl.get('fg'))}, bg {num(cl.get('bg'))} / Codex {cx_s} / OpenClaw {oc_s}")
    logs = env.get("logs") if isinstance(env.get("logs"), dict) else {}
    if not logs:
        lines.append("ログ: 見つかりません（開発用の起動など）" if ja else "Logs: not found (e.g. a dev run)")
    for name in _LOG_NAMES:
        s = logs.get(name)
        if not isinstance(s, dict):
            continue
        label = ("本体" if ja else "app") if name.startswith("office") else ("中継の配達員" if ja else "relay agent")
        kinds = _kinds_text(s.get("kinds") or {})
        tail = f"（{kinds}）" if (kinds and ja) else (f" ({kinds})" if kinds else "")
        ws = num(s.get("wsReconnect"))
        # plan.md 追加: 自動で戻る中継の WS 切断は別枠（素の「警告の行」を実際より多く・重く見せない）。
        ws_tail = (f"・自動で戻った切断 {ws} 回" if ja else f", auto-recovered disconnects {ws}") if ws else ""
        lines.append((f"ログ（{label}）: 警告の行 {num(s.get('warn'))}{tail}{ws_tail}") if ja
                     else f"Log ({label}): warning lines {num(s.get('warn'))}{tail}{ws_tail}")
    now = env.get("now")
    if isinstance(now, (int, float)):
        lines.append(("作成: " if ja else "Made: ") + time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(now)))
    return "\n".join(lines)
