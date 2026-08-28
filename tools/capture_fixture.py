#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""実セッションの jsonl 末尾を匿名化して tests/fixtures/ 用に切り出す。
トランスクリプト形式が変わった時のフィクスチャ更新を10分作業にする道具。

使い方: python3 tools/capture_fixture.py <実jsonl> <出力名.jsonl> [行数=12]
（テキストは伏せ字化・パスはダミー化・cwd/gitBranchは固定値に置換）
"""
import json
import re
import sys
from pathlib import Path

KEEP_INPUT_KEYS = {"file_path", "command", "description", "pattern", "skill",
                   "prompt", "url", "questions"}


def redact_text(s, n=24):
    s = re.sub(r"\s+", " ", str(s)).strip()[:n]
    return f"REDACTED({len(s)}): {s[:12]}…"


def redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "text":
                out[k] = redact_text(v)
            elif k in ("file_path", "url"):
                out[k] = "/tmp/redacted" + Path(str(v)).suffix
            elif k in ("command", "prompt", "description", "pattern"):
                out[k] = redact_text(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def main():
    src, name = Path(sys.argv[1]), sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 12
    out_lines = []
    for ln in src.read_text(errors="ignore").splitlines()[-n:]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        # R85-1: custom-title（/rename名）は状態ブロック行だが parse_session が読むので保存する。
        # 名前そのものは機微になりうるため伏せ字化（構造だけ残す）。
        if d.get("type") == "custom-title":
            out_lines.append(json.dumps(
                {"type": "custom-title",
                 "customTitle": redact_text(d.get("customTitle", "")),
                 "sessionId": "sess-aaaa1111"}, ensure_ascii=False))
            continue
        # R86-F: permission-mode は❗判定の一次情報＝落とすと今後のフィクスチャが全部
        # 「モード不明」になり、誤検知の番人が静かに無意味化する（フィクスチャ第一則の穴）。
        if d.get("type") == "permission-mode":
            out_lines.append(json.dumps(
                {"type": "permission-mode",
                 "permissionMode": d.get("permissionMode", ""),
                 "sessionId": "sess-aaaa1111"}, ensure_ascii=False))
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        slim = {"type": d["type"], "cwd": "/Users/test/demo-project", "gitBranch": "main"}
        msg = d.get("message") or {}
        c = msg.get("content")
        if isinstance(c, str):
            slim["message"] = {"role": msg.get("role", "user"), "content": redact_text(c)}
        else:
            slim["message"] = {"role": msg.get("role", ""), "content": redact(c)}
        out_lines.append(json.dumps(slim, ensure_ascii=False))
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / name
    out.write_text("\n".join(out_lines))
    print(f"✓ {out} ({len(out_lines)}行・匿名化済み)")


if __name__ == "__main__":
    main()
