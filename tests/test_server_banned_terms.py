#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan.md #22: 用語統一（プロジェクト／セッション／サブエージェントの3語）の禁止語チェックを、
UI文字列（tests/i18n_iso_smoke.py が見る strings.js・worker.js・PWA）だけでなく、
**サーバーが生成する文言**（office_server.py の TOOL_VERB 等）にも広げる。

実例（2026-10-03・本人の実データで発見）: `TOOL_VERB["Task"]`/`["Agent"]` の日本語訳が
「部下に指示中」となっており、Claude Code が Task/Agent ツール（サブエージェント委譲）を使う
たびに台帳の「いま」列・一覧カードへ**必ず**表示されていた（server/office_server.py:141）。

i18n_iso_smoke.py の既存の走査先（ui/hud/strings.js・relay/src/worker.js・ui/pwa/app.js・
ui/pwa/app.html）はクライアント側の文字列だけで、サーバー側で生成してそのまま office_json へ
乗せる文言（TOOL_VERB・TOOL_VERB_EN 以外にも今後増え得る）は対象外だった。ここではサーバー側の
ソースを同じ手法（コメント行は除外・引用符つきの行だけを見る）で走査する。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import office_server  # noqa: E402

# i18n_iso_smoke.py の banned と同じ語（正本は .claude/rules 配下の用語統一方針・コピーは避けたいが、
# 両者が別チームの持ち場のファイルなので、ここでは値だけ独立して持つ＝語を増やすときは両方に足す）。
BANNED = ["社員", "メンバー", "キャラクター", "部署", "部下"]

# サーバーが生成し、そのまま office_json / UI へ乗る文言を持つファイル（server/ の持ち場分だけ）。
SOURCES = [
    ROOT / "server" / "office_server.py",
    ROOT / "server" / "office_diag.py",
    ROOT / "server" / "source_claude_bg.py",
    ROOT / "server" / "office_door.py",      # R126: Bot の行の verb（休憩中・相談の答え待ち）と道具の返事
]


def _leaks(path):
    out = []
    in_doc, doc_marker = False, ""
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if in_doc:
            if doc_marker in line:
                in_doc = False
            continue                                  # docstring（複数行）の中の説明文は対象外
        if stripped.startswith("#"):
            continue                                  # コメントは説明文なので対象外
        is_doc_line = False
        for marker in ('"""', "'''"):
            if stripped.startswith(marker):
                is_doc_line = True
                rest = stripped[3:].rstrip()
                if len(rest) < 3 or not rest.endswith(marker):
                    in_doc, doc_marker = True, marker  # 複数行 docstring の開始
                break
        if is_doc_line:
            continue                                  # 1行 docstring そのものも対象外
        if '"' not in line and "'" not in line:
            continue                                  # 文字列リテラルが無い行は対象外
        for word in BANNED:
            if word in line:
                out.append(f"{path.name}:{i}: {word} — {line.strip()[:80]}")
    return out


class ServerBannedTermsTest(unittest.TestCase):
    def test_no_banned_terms_in_server_generated_strings(self):
        leaks = []
        for src in SOURCES:
            if src.exists():
                leaks.extend(_leaks(src))
        self.assertEqual(leaks, [], f"用語統一の禁止語がサーバー生成文言に混入: {leaks}")

    def test_tool_verb_uses_the_three_term_vocabulary(self):
        """plan.md #22 の実例そのもの: Task/Agent（サブエージェント委譲）の動詞に「部下」を使わない。"""
        for key in ("Task", "Agent"):
            self.assertNotIn("部下", office_server.TOOL_VERB[key], key)
        self.assertIn("サブエージェント", office_server.TOOL_VERB["Task"])


if __name__ == "__main__":
    unittest.main()
