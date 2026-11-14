#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テスト（`tests/*.mjs` と `tests/*_smoke.py`）と golden が**既定のゲートから呼ばれている**ことを機械で確かめる番人。

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

2026-09-24 の反省（R99 第 1 波の検収）:
  `dev.sh` も既定ゲートに数えていた。dev.sh は手元の下見（commit の門は verify.sh）なので、
  dev.sh の高速レーンにテスト名を書いた途端、verify から抜けても「配線済み」と言い続けた。
  既定ゲートは verify.sh だけにする。行頭 `#` のコメント行も読まない（名前を書いただけで配線済みになる）。
  同じ検収で、golden の表（dev.sh の golden_args・verify ▶7・golden-update SKILL の表）が
  3 か所の書き写しで、揃っているかを誰も見ていなかった＝cockpit の golden を撮ってあったのに
  ▶7 で照合していなかった事故（R99-A1）と同じ種類。golden_wiring() がそれを見る。

除外: `_` で始まるもの（ヘルパ）。

使い方: python3 tools/mjs_wire_lint.py   （verify.sh ▶2b と dev.sh --check から呼ぶ）
"""
import argparse
import pathlib
import re
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 既定で必ず走るゲート（commit 前に必ず green）。ここに名前が出て初めて「番人」になる。
# dev.sh は数えない（下見であって門ではない＝ここに入れると dev.sh にだけ残ったテストを見逃す）
GATES = ["verify.sh"]
# 条件付き（環境変数や道具の有無で省略される）。ここにしか無いものは ⚠ で名前を出す
CONDITIONAL = ["tests/relay_e2e.sh"]
SKILL = ".claude/skills/golden-update/SKILL.md"


def text(p):
    f = ROOT / p
    return f.read_text(encoding="utf-8") if f.is_file() else ""


def read(paths):
    """ゲートの本文。行頭 # のコメント行は除く（行末のコメントまでは剥がさない）。"""
    return "\n".join(line for g in paths for line in text(g).splitlines()
                     if not line.lstrip().startswith("#"))


def golden_wiring():
    """golden の表 3 か所が同じ行か、tests/visual/golden/*.png が全部 ▶7 で照合されるかを見る。"""
    sys.path.insert(0, str(ROOT / "tools"))
    import ui_shot                                     # 名前の決め方は ui_shot が正本（stdlib だけで import できる）
    ap = argparse.ArgumentParser(add_help=False)
    for k, d in (("--style", "iso"), ("--query", ""), ("--entry", "/"), ("--world", None),
                 ("--viewport", None), ("--name", None)):
        ap.add_argument(k, default=d)

    def shot(argstr):                                  # 引数列 → (撮る golden の名前, 引数列の正規形)
        a, _ = ap.parse_known_args(shlex.split(argstr))
        vp = ui_shot.parse_viewport(a.viewport) if a.viewport else None
        return ui_shot.golden_name(a.style, a.entry, a.query, a.name, vp), " ".join(shlex.split(argstr))

    dev = text("dev.sh")
    body = re.search(r"^golden_args\(\) \{\n(.*?)^\}", dev, re.S | re.M)
    tables = {
        "dev.sh の golden_args": [(n, a) for n, a in re.findall(
            r'^\s*([a-z0-9_]+)\)\s+echo "([^"]*)"\s*;;', body.group(1) if body else "", re.M)],
        SKILL + " の表": re.findall(r"^\|[^|\n]*\|\s*`([a-z0-9_]+)\.png`\s*\|\s*`([^`]+)`", text(SKILL), re.M),
        "verify.sh ▶7": [(None, a) for a in re.findall(r"tools/ui_shot\.py (.+?) --check\b", read(["verify.sh"]))],
    }
    # .claude/ は公開物に無い（tools/_publish_excludes.sh）。開発ツリー（tools/publish_snapshot.sh が在る＝公開物・配布物から除外している。CLAUDE.md は受け取った人が /init で作りうるので印にしない）では SKILL も必須
    if not (ROOT / SKILL).is_file() and not (ROOT / "tools/publish_snapshot.sh").is_file():
        print(f"  ⚠ {SKILL} は公開物に含めない＝golden の表は dev.sh と verify.sh の 2 か所だけ突き合わせる")
        del tables[SKILL + " の表"]
    errs, seen = [], {}
    for where, rows in tables.items():
        if not rows:
            errs.append(f"{where}: golden の行が 1 行も読めない（書き方が変わった？＝この番人が空振りする）")
            continue
        got = {}
        for name, argstr in rows:
            derived, norm = shot(argstr)
            if name and derived != name:
                errs.append(f"{where}: {name} の引数（{argstr}）は {derived}.png を撮る"
                            "（--world を付ける行は --name 必須）")
            got[name or derived] = norm
        seen[where] = got
    ref_where, ref = next(iter(seen.items()), (None, {}))

    def show(v):
        return f"「{v}」" if v else "行が無い"

    for where, got in seen.items():
        for n in sorted(set(ref) | set(got)):
            if ref.get(n) != got.get(n):
                errs.append(f"{n}: {ref_where} は {show(ref.get(n))}・{where} は {show(got.get(n))}（3 か所は同じ行）")
    shot_loop = re.search(r"for gname in ([a-z0-9_ ]+); do", dev)
    for n in (shot_loop.group(1).split() if shot_loop else []):
        if n not in ref:
            errs.append(f"dev.sh --shot の {n} が golden_args に無い（黙って既定の iso を撮る）")
    for png in sorted((ROOT / "tests" / "visual" / "golden").glob("*.png")):
        if png.stem not in seen.get("verify.sh ▶7", {}):
            errs.append(f"tests/visual/golden/{png.name} があるのに verify ▶7 が照合していない")
    return errs


def main():
    tests = sorted(p for p in (ROOT / "tests").glob("*.mjs") if not p.name.startswith("_"))
    tests += sorted(p for p in (ROOT / "tests").glob("*_smoke.py") if not p.name.startswith("_"))
    wired, cond = read(GATES), read(CONDITIONAL)
    orphans = [t.name for t in tests if t.name not in wired and t.name not in cond]
    only_cond = [t.name for t in tests if t.name not in wired and t.name in cond]
    gerrs = golden_wiring()
    for e in gerrs:
        print(f"  ✗ golden の配線: {e}")
    if orphans:
        for name in orphans:
            print(f"  ✗ tests/{name} はどのゲート（{'・'.join(GATES + CONDITIONAL)}）からも呼ばれていない"
                  "（dev.sh は数えない＝commit の門は verify.sh）")
        print(f"配線lint: {len(orphans)} 本が未配線（書いただけで走っていない）")
    if orphans or gerrs:
        return 1
    print(f"✓ 配線lint: 対象 {len(tests)}本 すべてゲートから呼ばれている"
          f"（既定 {len(tests) - len(only_cond)}・条件付き {len(only_cond)}）・golden の表 3 か所が一致")
    for name in only_cond:
        print(f"  ⚠ tests/{name} は条件付きゲート（{'・'.join(CONDITIONAL)}）でしか走らない＝既定の verify では未検査")
    return 0


if __name__ == "__main__":
    sys.exit(main())
