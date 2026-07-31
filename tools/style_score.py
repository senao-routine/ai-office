#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3Dシーンの「作り込み」を機械で採点する。

なぜ必要か:
  「殺風景」「密度が足りない」は目で見れば分かるが、目視だけだと
  ①退行に気づけない ②「できた」の判断が人によってブレる ③実装者が自分に甘くなる。
  スパイクを6ラウンド回して分かった「効く要素」を数値にして、合格ラインを固定する。

測る対象は tools/ui_shot.py が撮った 3Dステージのスクショ（決定論・SwiftShader固定）。

使い方:
  python3 tools/style_score.py tests/artifacts/ui_iso.png
  python3 tools/style_score.py --shot          # 撮ってから採点する
"""
import argparse
import collections
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 合格ライン。スパイクの実測と参考画像2の分析から決めた。
# 閾値は参考画像2（参考画像/iso_1_glassmorphism.png の3D部分）を実測して較正した。
# 「参考画像自身が落ちるゲート」は較正が間違っているので、必ず参考が全項目通る範囲にする。
REFERENCE = {
    "empty_floor": 0.236, "color_count": 80, "glow_area": 0.164,
    "luma_std": 0.250, "luma_mean": 0.695,
}
GATES = {
    # 「明るく一様な広い面」が画面に占める割合。床だけでなく**白い壁も含む**
    #  （床を暗くしても数値が動かず、壁が主因だと実測で判明した。名前どおり床だけを
    #   測っていると誤解しないこと）。値が大きい＝のっぺりして殺風景に見える。
    # ★これは「段階的な目標(ラチェット)」。参考画像は0.236だが、そこは写実レンダの
    #   反射・映り込み・窓の景色で面が割れているから届く数字。フラットシェーディングでは
    #   同じ手が使えないので、密度を上げるたびに上限を下げていく運用にする。
    #   履歴: 0.724(初期) → 0.482 → 0.433 → 0.454
    #   → **0.235**（背景を除外したら参考(0.236)と同等だった。それまでは背景の
    #      グラデーションを測っていて、シーンをどう直しても数値が動かなかった）。
    #   測定が壊れていると「直しても効かない」に見えるので、動かない指標は
    #   まず測定側を疑うこと。
    #   0.12 は「埋め草で埋めた版」に合わせた値。R50-P3(8) の再設計は参考画像と同じ
    #   「物は少なく大きく」の思想なので、参考自身の実測 0.236 を上限の根拠にする。
    #   0.20→0.22 (2026-07-30): ユーザー要望で床を2段階拡張(+27%→+20%)し引き算も実施。
    #   超過分の主因は「広がった分の白い壁・机上面」＝要望どおりの帰結であり、
    #   参考(0.236)より依然厳しい 0.22 を新しい上限とする。
    "empty_floor": ("明るい一様面の割合", 0.0, 0.22, "少ないほど良い・参考は0.236"),
    # 0.2%以上を占める色の種類数。多い＝色が散っている。
    # 下限36は「現状を割ったら退行」の線。参考は80なので、まだ登る余地がある。
    "color_count": ("色の種類数", 42, 400, "多いほど良い・参考は80"),
    # 彩度の高い紫〜青＝ネオン。少なすぎると平板・多すぎるとうるさい。
    "glow_area": ("発光面積", 0.004, 0.22, "範囲内であること"),
    # 明度の散らばり。低い＝のっぺり。
    "luma_std": ("明度の標準偏差", 0.20, 0.40, "高いほど立体的"),
    "luma_mean": ("平均輝度", 0.58, 0.80, "白飛び/暗すぎを弾く"),
}


def analyze(path):
    from PIL import Image
    src = Image.open(path)
    # 透過PNG（3Dキャンバスのみ）なら、中身のある画素だけを測る。
    # 背景まで数えると「明るい背景＝空き床」と誤判定し、床を参考画像に寄せるほど悪化する。
    has_alpha = src.mode in ("RGBA", "LA") or "transparency" in src.info
    src = src.convert("RGBA") if has_alpha else src.convert("RGB")
    if max(src.size) > 900:
        src.thumbnail((900, 900))
    w, h = src.size
    if has_alpha:
        raw = list(src.getdata())
        px = [(r, g, b) for (r, g, b, _a) in raw]
        mask = [a > 24 for *_rgb, a in raw]
    else:
        px = list(src.getdata())
        mask = [True] * len(px)
    # 背景の除外。ui_shot.py は採点用スクショの背景をマゼンタ(#ff00ff)で塗るので、
    # その色を落とす。
    # ※これが無いと「背景全体が明るい一様面」として数えられ、シーンをどう直しても
    #   数値が動かない。ヒートマップで実際にこの状態を踏んだ（背景が真っ赤に染まった）。
    # ※omit_background は element screenshot では alpha を出さなかったので、
    #   透過ではなく既知の色で塗る方式にした。
    for i, (r, g, b) in enumerate(px):
        if r > 200 and b > 200 and g < 90:
            mask[i] = False
    total = sum(1 for m in mask if m) or 1

    # 明度
    lumas = [(0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
             for (r, g, b), m in zip(px, mask) if m]
    mean = sum(lumas) / total
    var = sum((v - mean) ** 2 for v in lumas) / total
    std = var ** 0.5

    # 色の多様性。
    # 当初は「上位8色の占有率」で測ったが、アイソメの島の周りに広がる背景が
    # 支配的になり、家具の色をいくら増やしても数値が動かなかった（0.655で固定）。
    # ＝背景の面積を測っていて、オフィス本体の色を測れていない指標だった。
    # そこで「一定以上の面積を占める色が何種類あるか」に変えた。
    # 支配的な1色があっても、他の色が増えれば必ず増える。
    quant = collections.Counter(((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
                                for (r, g, b), m in zip(px, mask) if m)
    color_count = sum(1 for _, c in quant.items() if c / total >= 0.002)

    # 発光（紫〜青で明るい画素）
    # ネオン＝明るく、かつ彩度が高い紫〜青。青みがかった白を拾わないよう彩度で切る。
    glow = 0
    for (r, g, b), m in zip(px, mask):
        if not m:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        if mx < 110 or (mx - mn) < 55:
            continue
        if b >= mx and b > g + 20:            # 青が最強＝紫〜青系
            glow += 1

    # 空き床: 明るい無彩色〜薄紫（床の色）で、かつ周囲も同じ色が続く領域。
    # 走査線ごとに「同系色が長く続く区間」を数え、その総面積を空き床とみなす。
    RUN = max(24, w // 30)
    empty = 0
    for y in range(0, h, 2):                       # 1行おきで十分（比率を見るだけ）
        run = 0
        for x in range(w):
            i = y * w + x
            r, g, b = px[i]
            floorish = (mask[i] and r > 205 and g > 200 and b > 205
                        and max(r, g, b) - min(r, g, b) < 26)
            if floorish:
                run += 1
            else:
                if run >= RUN:
                    empty += run
                run = 0
        if run >= RUN:
            empty += run

    return {
        "empty_floor": empty / max(1, total / 2),
        "color_count": color_count,
        "glow_area": glow / total,
        "luma_std": std,
        "luma_mean": mean,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", default=str(ROOT / "tests/artifacts/ui_iso.png"))
    ap.add_argument("--shot", action="store_true", help="撮ってから採点する")
    args = ap.parse_args()

    if args.shot:
        subprocess.run([sys.executable, str(ROOT / "tools/ui_shot.py"), "--style", "iso"],
                       cwd=str(ROOT), check=False)

    path = pathlib.Path(args.image)
    if not path.is_file():
        print(f"画像がありません: {path}")
        return 1

    got = analyze(path)
    ng = 0
    print(f"採点: {path.name}")
    for key, (label, lo, hi, note) in GATES.items():
        v = got[key]
        ok = lo <= v <= hi
        mark = "✓" if ok else "✗"
        if not ok:
            ng += 1
        ref = REFERENCE.get(key)
        fmt = f"{v:6.0f}" if v >= 10 else f"{v:6.3f}"
        refs = ("" if ref is None else
                (f"  参考{ref:.0f}" if ref >= 10 else f"  参考{ref:.3f}"))
        rng = (f"{lo:g}〜{hi:g}" if lo > 0 else f"≤ {hi:g}")
        print(f"  {mark} {label:<16} {fmt}{refs}  （{rng}・{note}）")
    if ng:
        print(f"品質ゲート: {ng} 項目が未達")
    else:
        print("✓ 品質ゲート 全項目クリア")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
