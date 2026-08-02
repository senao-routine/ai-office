#!/bin/bash
# 公開リポ更新の型（R52.1で確立・R65でスクリプト化）:
#   dev（ai-office-dev・全履歴）から「現在のHEADのスナップショット」を作り、
#   内部専用物を除外し、禁止語ゲートを通してから public（ai-office）へ1コミットで積む。
#   履歴は持ち込まない（fresh-start公開の原則＝過去の内部情報・顧客名を構造的に漏らさない）。
#
# 使い方:  bash tools/publish_snapshot.sh            # 検査つきで公開リポを更新
#          bash tools/publish_snapshot.sh --dry-run  # 何が出るかの確認のみ（pushしない）
set -euo pipefail
cd "$(dirname "$0")/.."
DRY="${1:-}"

PUBLIC_URL="https://github.com/senao-routine/ai-office.git"
# 公開しないもの（内部資料・権利リスク・開発専用）。追加はここに一元化する
EXCLUDES=(
  "_archive"
  "参考画像"
  "CLAUDE.md"
  "docs/ROADMAP.md"
  "docs/uiux-audit-20260723.md"
  "docs/quality-checklist.md"
  "docs/プロダクト構想_20260708.md"
  "docs/進捗ハンドオフ_20260708.md"
  "docs/show-hn-draft.md"
)

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "✗ ワーキングツリーが汚れています。commit してから実行してください" >&2
  exit 1
fi

STAGE="$(mktemp -d /tmp/aioffice-publish.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
git archive HEAD | tar -x -C "$STAGE"
for x in "${EXCLUDES[@]}"; do
  rm -rf "$STAGE/$x"
done

# 禁止語ゲート（公開の絶対条件・失敗したら即中断）
bash ~/.claude/scripts/ng_check.sh "$STAGE"
# 顧客名スクラブの回帰（R52.1で全面改名済み＝再混入をここで止める）。
# パターンはbase64で保持（deploy.shの前例）＝このスクリプト自身が検査に引っかかる自己参照を防ぐ
SCRUB_PAT="$(echo bXVzdWJp | base64 -d)"
if grep -riq "$SCRUB_PAT" "$STAGE"; then
  echo "✗ 顧客名の再混入を検出。公開を中断します" >&2
  exit 1
fi

echo "── 公開スナップショット: $(find "$STAGE" -type f | wc -l | tr -d ' ') files / $(du -sh "$STAGE" | cut -f1)"
if [ "$DRY" = "--dry-run" ]; then
  find "$STAGE" -maxdepth 1 | sed "s|$STAGE|.|"
  echo "（--dry-run: push しません）"
  exit 0
fi

cd "$STAGE"
git init -q -b master
git config user.name senao
git config user.email "75591276+senao-routine@users.noreply.github.com"
git remote add origin "$PUBLIC_URL"
git fetch -q origin master
git add -A
SRC_SHA="$(cd - >/dev/null && git rev-parse --short HEAD)"
git commit -q -m "Update AI Office (snapshot ${SRC_SHA})

See README.md for what's inside. Built from the private development
repository as a squashed snapshot (no internal history is published)."
# 公開履歴は「スナップショットの積み重ね」＝以前の公開コミットの上に積む
git reset -q --soft origin/master
git commit -q -m "Update AI Office (snapshot ${SRC_SHA})"
git push -q origin master
echo "✓ 公開リポを更新しました: ${PUBLIC_URL%.git} (snapshot ${SRC_SHA})"
