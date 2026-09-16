#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テスト（`tests/*.mjs` と `tests/*_smoke.py`）が**既定のゲートから呼ばれている**ことを機械で確かめる番人。

なぜ必要か（2026-09-08 実発生・R90 の監査で発覚）:
  R90 で node テストを6本作ったのに、`verify.sh` に配線したのは1本だけで、
  残り5本（33件）は**どのゲートからも一度も走っていなかった**。ファイルは存在し、
  中身も緑になるので、「テストがある」と思い込んだまま回帰を素通りさせる。
  テストは「書いた」ではなく「毎回走る」で初めて番人になる。

2026-09-17 の反省（R97-B）:
  この番人自身が `tests/relay_e2e.sh` を「ゲート」に数えていた。あれは `RUN_RELAY=1` の
  ときだけ走る**条件付き**なので、実際には既定 verify で一度も走らない 7 本（JS 署名 KAT・
  Web Push KAT・パリティ 4 本）を「配線済み」と報告していた＝**穴を無いと言う計器**。
  既定ゲートと条件付きゲートを分けて数え、条件付きにしか居ないものは ⚠ として名前を出す。
  Python のスモーク（`tests/*_smoke.py`）にも同じ穴が空いていたので対象に含める。

除外: `_` で始まるもの（ヘルパ）。

使い方: python3 tools/mjs_wire_lint.py   （verify.sh ▶2b と dev.sh --check から呼ぶ）
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 既定で必ず走るゲート。ここに名前が出て初めて「番人」になる
GATES = ["verify.sh", "dev.sh"]
# 条件付き（環境変数や道具の有無で省略される）。ここにしか無いものは ⚠ で名前を出す
CONDITIONAL = ["tests/relay_e2e.sh"]


def read(paths):
    return "\n".join((ROOT / g).read_text(encoding="utf-8")
                     for g in paths if (ROOT / g).is_file())


def main():
    tests = sorted(p for p in (ROOT / "tests").glob("*.mjs") if not p.name.startswith("_"))
    tests += sorted(p for p in (ROOT / "tests").glob("*_smoke.py") if not p.name.startswith("_"))
    if not tests:
        print("  - 対象テストがありません → 省略")
        return 0
    wired, cond = read(GATES), read(CONDITIONAL)
    orphans = [t.name for t in tests if t.name not in wired and t.name not in cond]
    only_cond = [t.name for t in tests if t.name not in wired and t.name in cond]
    if orphans:
        for name in orphans:
            print(f"  ✗ tests/{name} はどのゲート（{'・'.join(GATES + CONDITIONAL)}）からも呼ばれていない")
        print(f"配線lint: {len(orphans)} 本が未配線（書いただけで走っていない）")
        return 1
    print(f"✓ 配線lint: 対象 {len(tests)}本 すべてゲートから呼ばれている"
          f"（既定 {len(tests) - len(only_cond)}・条件付き {len(only_cond)}）")
    for name in only_cond:
        print(f"  ⚠ tests/{name} は条件付きゲート（{'・'.join(CONDITIONAL)}）でしか走らない＝既定の verify では未検査")
    return 0


if __name__ == "__main__":
    sys.exit(main())
