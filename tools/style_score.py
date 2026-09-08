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


# R90-AD2: 見た目の方向が決まったら「参考＝そのボード」に切り替える（プロファイル）。
#   legacy = 旧 iso（紫青のガラス UI・アクセントは紫）＝ S1 で ui/iso を消すまでの互換。
#   c      = 方向C「写実寄り北欧オフィス」（2026-09-07 本人裁定）。参考= docs/art/board_C_overview.jpg。
# c で測る指標が legacy と違うのは意図的:
#   ・アクセント（セージ緑）は**面積が小さい**のが正しい（ボード実測 0.6%）。だから
#     「アクセントが光る面積」ではなく「少しは在るか」の下限として使う。彩度の床も下げる
#     （セージ #7c9a73 は max-min=39 しかなく、旧 55 では 1 画素も拾えない＝測定が壊れる）。
#   ・代わりに**紫青ネオンの不在**を独立したゲートにする（legacy では「在ること」が下限だった）。
PROFILES = {
    "legacy": {
        "reference": REFERENCE,
        "gates": GATES,
    },
    "c": {
        # docs/art/board_C_overview.jpg の実測（--calibrate --profile c）
        "reference": {
            "empty_floor": 0.191, "color_count": 92, "neon_cool": 0.000,
            "accent_area": 0.006, "luma_std": 0.221, "luma_mean": 0.665,
        },
        "gates": {
            "empty_floor": ("明るい一様面の割合", 0.0, 0.22, "少ないほど良い・参考は0.191"),
            "color_count": ("色の種類数", 60, 400, "多いほど良い・参考は92"),
            # 禁止色（シアン〜青〜紫）は「無い」が正解
            # ＝旧UIの #7c5cff / #4f8dff / #53e0c4 が画面から消えたことの機械証明
            "neon_cool": ("禁止色(青紫シアン)の面積", 0.0, 0.02, "無いほど良い・参考は0.000"),
            # セージ緑が画面に少しは在る（椅子・葉・ラグ）。多すぎ＝緑一色も弾く。
            "accent_area": ("アクセント(セージ)の面積", 0.001, 0.08, "参考は0.006・小さくてよい"),
            # 下限は 0.17（参考ボードの全景 0.221 から）→ **0.15**（2026-09-08 実測で較正し直し）。
            # 参考の 0.221 は「白い周囲＋外壁＋レイトレの室内」を含む全景の値で、部屋の中身だけを
            # 切ると 0.189。さらに我々は俯瞰 40° の平行投影で**床の面積が参考より大きく壁が小さい**うえ
            # GI が無い＝同じ絵でも構造的に散らばりが小さい。壁を明るく（#f6f2ec）し床AOをゾーン天面へ
            # 焼いた状態の実測が 0.165 で、そこから落ちたら「のっぺりへの退行」と言える線として 0.15 を採る。
            "luma_std": ("明度の標準偏差", 0.15, 0.40, "高いほど立体的・実測0.165/参考0.189(室内)"),
            "luma_mean": ("平均輝度", 0.58, 0.80, "白飛び/暗すぎを弾く・参考は0.665"),
        },
        "accent_hue": 105.0,      # セージ緑
        "accent_sat": 26,         # 低彩度のセージを拾う床（既定55だと 0 画素）
    },
}


def _hue(r, g, b):
    """RGB→色相（度・0..360）。彩度ゼロは 0 を返す（呼び出し側が彩度で先に切る）。"""
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0
    d = float(mx - mn)
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h * 60.0) % 360.0


def analyze(path, accent_hue=None, hue_width=30.0, accent_sat=55):
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

    # 発光（アクセント色で明るい画素）
    # ネオン＝明るく、かつ彩度が高いアクセント色。青みがかった白を拾わないよう彩度で切る。
    # R90-H: 既定（accent_hue=None）は従来どおり「青が最強＝紫〜青系」。新しい見た目では
    # docs/art-direction.md のアクセント色相を --accent-hue で渡す（±hue_width 度）。
    # accent_sat は彩度の床。低彩度のアクセント（セージ緑など）は 55 だと 1 画素も拾えず、
    # 「測っているつもりで常に 0」になる（R90-AD2 で実測して踏んだ）。プロファイルが下げる。
    glow = 0            # 従来の指標（accent_hue 未指定なら紫青）
    cool = 0            # 禁止色（シアン〜青〜紫）の面積。accent_hue に関係なく常に測る
    accent = 0          # アクセント色の面積（accent_hue 指定時のみ意味を持つ）
    for (r, g, b), m in zip(px, mask):
        if not m:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        sat = mx - mn
        # 禁止色は「青が最強」だけでは足りない。旧UIのシアン #53e0c4 は緑が最強（色相168°）で
        # すり抜ける（Astra レビュー指摘・R90-AD2）。色相の帯 160〜300° で切る。
        if mx >= 110 and sat >= 55 and 160.0 <= _hue(r, g, b) <= 300.0:
            cool += 1
        if accent_hue is not None and mx >= 110 and sat >= accent_sat:
            if abs(((_hue(r, g, b) - accent_hue + 180) % 360) - 180) <= hue_width:
                accent += 1
        # glow_area（legacy 指標）の彩度の床も accent_sat に従う（既定 55＝従来と同値）。
        if mx < 110 or sat < accent_sat:
            continue
        if accent_hue is None:
            if b >= mx and b > g + 20:        # 青が最強＝紫〜青系
                glow += 1
        else:
            d = abs(((_hue(r, g, b) - accent_hue + 180) % 360) - 180)
            if d <= hue_width:
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
        "neon_cool": cool / total,
        "accent_area": accent / total,
        "luma_std": std,
        "luma_mean": mean,
    }


def calibrate(path, accent_hue, hue_width, profile="legacy", accent_sat=55):
    """R90-H: 採用したコンセプトボード（参考画像）を実測し、ゲート案を出す。
    「参考画像自身が落ちるゲートは較正が間違っている」の原則を機械に置き換える:
    参考の実測値が全項目通る範囲を、現行ゲートの幅を保ったまま提案する。
    書き換えは人間が REFERENCE/GATES に写す（自動で書かない＝意図の無い緩和を防ぐ）。"""
    gates = PROFILES[profile]["gates"]
    got = analyze(path, accent_hue, hue_width, accent_sat)
    print(f"較正: {path.name}（profile={profile}・"
          f"accent_hue={accent_hue if accent_hue is not None else '紫青(既定)'}）")
    print("  REFERENCE = {")
    for key in gates:
        v = got[key]
        print(f'      "{key}": {v:.3f},' if v < 10 else f'      "{key}": {v:.0f},')
    print("  }")
    print("  ゲート案（参考が通る範囲・現行の幅を維持）:")
    for key, (label, lo, hi, note) in gates.items():
        v = got[key]
        if key == "empty_floor":
            lo2, hi2 = 0.0, max(hi, round(v + 0.01, 3))
        elif key == "color_count":
            lo2, hi2 = min(lo, int(v * 0.5)), hi
        else:
            span = hi - lo
            lo2 = max(0.0, min(lo, round(v - span * 0.25, 3)))
            hi2 = max(hi, round(v + span * 0.25, 3))
        mark = "✓" if lo <= v <= hi else "→"
        print(f"    {mark} {label:<16} 実測 {v:.3f}  現行 {lo:g}〜{hi:g}  案 {lo2:g}〜{hi2:g}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", default=str(ROOT / "tests/artifacts/ui_iso.png"))
    ap.add_argument("--shot", action="store_true", help="撮ってから採点する")
    ap.add_argument("--style", default="iso", help="--shot で撮るスタイル（iso）")
    ap.add_argument("--accent-hue", type=float, default=None,
                    help="発光判定のアクセント色相（度）。未指定=従来の紫青。例: 緑 150")
    ap.add_argument("--hue-width", type=float, default=30.0, help="色相の許容幅（±度・既定30）")
    ap.add_argument("--profile", default="legacy", choices=sorted(PROFILES),
                    help="ゲートの組（legacy=旧iso / c=方向C 写実寄り北欧・docs/art-direction.md）")
    ap.add_argument("--accent-sat", type=int, default=None,
                    help="アクセント判定の彩度の床（既定はプロファイル値・legacy は55）")
    ap.add_argument("--calibrate", action="store_true",
                    help="参考画像を実測して REFERENCE/GATES の案を出す（書き換えはしない）")
    args = ap.parse_args()

    if args.shot:
        subprocess.run([sys.executable, str(ROOT / "tools/ui_shot.py"), "--style", args.style],
                       cwd=str(ROOT), check=False)
        if args.image == str(ROOT / "tests/artifacts/ui_iso.png"):
            args.image = str(ROOT / f"tests/artifacts/ui_{args.style}_scene.png")

    path = pathlib.Path(args.image)
    if not path.is_file():
        print(f"画像がありません: {path}")
        return 1

    prof = PROFILES[args.profile]
    gates, reference = prof["gates"], prof["reference"]
    # プロファイルの既定（アクセント色相・彩度の床）。明示指定があればそちらが勝つ。
    accent_hue = args.accent_hue if args.accent_hue is not None else prof.get("accent_hue")
    accent_sat = args.accent_sat if args.accent_sat is not None else prof.get("accent_sat", 55)

    if args.calibrate:
        return calibrate(path, accent_hue, args.hue_width, args.profile, accent_sat)

    got = analyze(path, accent_hue, args.hue_width, accent_sat)
    ng = 0
    print(f"採点: {path.name}" + (f"（profile={args.profile}）" if args.profile != "legacy" else ""))
    for key, (label, lo, hi, note) in gates.items():
        v = got[key]
        ok = lo <= v <= hi
        mark = "✓" if ok else "✗"
        if not ok:
            ng += 1
        ref = reference.get(key)
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
