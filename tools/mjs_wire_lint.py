#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`tests/*.mjs` が**どこかのゲートから呼ばれている**ことを機械で確かめる番人。

なぜ必要か（2026-09-08 実発生・R90 の監査で発覚）:
  R90 で node テストを6本作ったのに、`verify.sh` に配線したのは1本だけで、
  残り5本（33件）は**どのゲートからも一度も走っていなかった**。ファイルは存在し、
  中身も緑になるので、「テストがある」と思い込んだまま回帰を素通りさせる。
  テストは「書いた」ではなく「毎回走る」で初めて番人になる。

除外: `_` で始まるもの（ヘルパ）。

使い方: python3 tools/mjs_wire_lint.py   （verify.sh ▶2b と dev.sh --check から呼ぶ）
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GATES = ["verify.sh", "dev.sh", "tests/relay_e2e.sh"]


def main():
    tests = sorted(p for p in (ROOT / "tests").glob("*.mjs") if not p.name.startswith("_"))
    if not tests:
        print("  - tests/*.mjs がありません → 省略")
        return 0
    wired = "\n".join((ROOT / g).read_text(encoding="utf-8")
                      for g in GATES if (ROOT / g).is_file())
    orphans = [t.name for t in tests if t.name not in wired]
    if orphans:
        for name in orphans:
            print(f"  ✗ tests/{name} はどのゲート（{'・'.join(GATES)}）からも呼ばれていない")
        print(f"配線lint: {len(orphans)} 本が未配線（書いただけで走っていない）")
        return 1
    print(f"✓ 配線lint: tests/*.mjs {len(tests)}本 すべてゲートから呼ばれている")
    return 0


if __name__ == "__main__":
    sys.exit(main())
