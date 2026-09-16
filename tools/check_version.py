#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版が 1 箇所（server/office_version.py）から波及していることを機械で確かめる番人。

なぜ要るか（2026-09-17 の棚卸し）:
  版が**どこにも無かった**。git tag 0 個・CHANGELOG 無し・MCP は "1.0.0"・README のタイトルだけ「2.0」。
  「いま動いているのが何版か」を誰も機械で答えられない状態で「更新を価値として配る」ことはできない。
  数値を 2 箇所へ手で書く運用に戻ると、art-direction.md の材質数（59 と書いてあるのに実測 62）と
  同じ嘘がまた生える。だから**手書きの版はここで落とす**。

検査:
  1. server/office_version.py の VERSION が semver
  2. MCP の serverInfo.version が同じ（数字の直書きが無いこと）
  3. README.md / README.ja.md のバッジが同じ
  4. CHANGELOG.md に同じ版の節が在る（[Unreleased] は許す＝未リリースの間）
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import office_version                                   # noqa: E402

V = office_version.VERSION
BADGE = re.compile(r"<!--version-->v([0-9A-Za-z.\-+]+)<!--/version-->")


def main():
    bad = []
    if not re.fullmatch(r"\d+\.\d+\.\d+", V):
        bad.append(f"VERSION が semver でない: {V!r}")

    mcp = (ROOT / "server" / "mcp_office.py").read_text(encoding="utf-8")
    if re.search(r'"version":\s*"\d', mcp):
        bad.append("mcp_office.py に版が直書きされている（office_version から取る）")

    for name in ("README.md", "README.ja.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        m = BADGE.search(text)
        if not m:
            bad.append(f"{name} に版バッジ <!--version-->vX.Y.Z<!--/version--> が無い")
        elif m.group(1) != V:
            bad.append(f"{name} の版 v{m.group(1)} が正本 v{V} と違う")

    ch = ROOT / "CHANGELOG.md"
    if not ch.is_file():
        bad.append("CHANGELOG.md が無い（更新を価値として配るなら、更新の器が要る）")
    else:
        text = ch.read_text(encoding="utf-8")
        heads = re.findall(r"^## \[([^\]]+)\]", text, re.M)
        if not heads:
            bad.append("CHANGELOG.md に節（## [x.y.z]）が無い")
        elif heads[0] not in ("Unreleased", V):
            # 先頭は「まだ出していない分（Unreleased）」か「いま出ている版」のどちらか
            bad.append(f"CHANGELOG.md の先頭節 [{heads[0]}] が正本 v{V} でも [Unreleased] でもない")

    if bad:
        print("✗ 版の食い違い:")
        for b in bad:
            print(f"    {b}")
        return 1
    print(f"✓ 版番人: v{V}（正本 server/office_version.py → MCP・README×2・CHANGELOG）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
