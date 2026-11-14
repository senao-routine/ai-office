#!/bin/bash
# 公開物から落とすもの（内部資料・権利リスク・開発専用）。**ここが唯一の正本**。
# 読む側: tools/publish_snapshot.sh（公開リポ）と tools/pack_release.sh（配布 zip / tarball）。
# 片方だけに足すと、もう片方から内部物が漏れる＝1 箇所に集約して両方が同じリストを食う。
# shellcheck disable=SC2034
EXCLUDES=(
  "_archive"
  "参考画像"
  "CLAUDE.md"
  "docs/HISTORY.md"              # R96-C: CLAUDE.md から退避した内部の歴史（従来は CLAUDE.md ごと非公開）
  "docs/security-notes.md"       # R96-C: 受容リスクの内部メモ
  "docs/ROADMAP.md"
  "docs/uiux-audit-20260723.md"
  "docs/quality-checklist.md"
  "docs/プロダクト構想_20260708.md"
  "docs/進捗ハンドオフ_20260708.md"
  "docs/show-hn-draft.md"
  "docs/r87-facts.md"            # 未出荷（R87）の設計台帳＝配布物に混ぜると
                                 # 「この機能はどこ？」になる。内部の実測記録
  "docs/openclaw-post-draft.md"
  # R80-B7: 配布物は「動かす人のための最小セット」にする。以下は開発者専用で、
  # 公開すると (a)混乱を招く (b)受け取った人の環境で誤爆する (c)手の内を晒す。
  ".claude"                      # ★PostToolUse hook入り。受け取った人がこのリポで
                                 #   Claude Code を開くと**私の開発用hookが発火**する（誤爆）
  ".vscode"
  "demo/.wrangler"               # wrangler のキャッシュ＝アカウントのメールと ID（2026-10-04 に公開リポで発見）
  # ライセンス機構（license.py/license_sign.py/license-runbook等）は R85-2 で全撤去済み
  "tools/publish_snapshot.sh"    # 公開の型そのもの（自己参照を持ち込まない）
  "verify.local"                 # ローカル環境固有の設定
  "docs/BETA.ja.md"               # メンバー先行β（3.0.0-beta.*）のテスター向けの手引き＝3.0.0 の公開物には入れない
  "docs/収益化アーキテクチャ_20260806.md"   # 価格・特典・発行運用＝内部設計
  # R97-C: 内部の作業台帳・計画と、外部有料サービスを叩く開発専用ツール。
  # 「配布物は動かす人のための最小セット」＝受け取った人には読めても意味が無く、混乱を招く。
  "docs/r96-d3-plan.md"
  "docs/tripo-d2-plan.md"
  "docs/tripo-ledger.md"
  "tools/tripo_gen.py"           # Tripo API（有料）を叩く開発専用ツール。作業用の個人パスも入っている
  ".tripo"                       # 直近の Tripo タスク id（開発の副産物）
  # R98-W4: 観察の台帳と、その自動記録。内部の作業台帳＝受け取った人には意味が無い。
  # ★このファイル自体は公開物に残る。落とす中身の要約をここに書くと、除外した意味が消える
  #（別モデルレビューで実測）。理由は一般論にとどめる。
  "docs/observation-20260919.json"
  "tools/observe.py"
  "tools/observe_install.sh"
  "tests/test_observe.py"
  # R99: 見た目の検討用サンプル（本番は ui/ が持つ）。配布物は動かす人のための最小セット。
  "design"
)

# 「作業ツリーが汚れている」の判定から外すもの（自動で毎日書き換わる記録）。
# 公開も配布も HEAD を git archive する＝汚れの検査は「未コミットの変更も出たと思い込む」を防ぐためにある。
# **上の EXCLUDES に在るもの（どうせ出ないもの）だけ**を置ける。無い行は publish_dirty_ok_check が拒否する。
DIRTY_OK=(
  "docs/observation-20260919.json"
)

# DIRTY_OK の各行が EXCLUDES に在ることを確かめる（無ければ 1＝呼び側が中断する）
publish_dirty_ok_check() {
  local x e
  for x in ${DIRTY_OK[@]+"${DIRTY_OK[@]}"}; do
    for e in "${EXCLUDES[@]}"; do [ "$e" = "$x" ] && continue 2; done
    echo "✗ DIRTY_OK の ${x} が EXCLUDES に無い（出るものの汚れを見逃す）: tools/_publish_excludes.sh" >&2
    return 1
  done
  return 0
}

# 作業ツリーかインデックスに、DIRTY_OK 以外の未コミットの変更があれば 0（＝汚れている）
publish_tree_dirty() {
  local x ex=()
  for x in ${DIRTY_OK[@]+"${DIRTY_OK[@]}"}; do ex+=(":(exclude)$x"); done
  ! git diff --quiet -- . ${ex[@]+"${ex[@]}"} || ! git diff --cached --quiet -- . ${ex[@]+"${ex[@]}"}
}
