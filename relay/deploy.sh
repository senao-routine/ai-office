#!/bin/bash
# AI Office relay — 公開前ゲート付きデプロイ（wrangler deploy はこのスクリプト経由で行う）
#
# ゲート（公開ポリシー: 本名・所属の秘匿・~/.claude/CLAUDE.md 冒頭）:
#   Worker(workers.dev) と同梱PWA/スプライト索引は「すでに公開面」。
#   deploy 前に禁止語（本名・旧所属・実パス文字列）を機械検査し、ヒットしたら失敗する。
#   - 禁止語パターン自体は base64 で保持（本スクリプトも将来のリポ公開対象＝実名リテラルを置かない）
#   - sprites_data.js は base64 連鎖(200文字以上)を「部分除去」してから検査＝
#     データ内の偶然一致は無視しつつ、同一行のキー名（スプライト名）は検査対象に残す
#   - あわせて assets/ ⇄ sprites_data.js のドリフト（--check）も deploy 前に固定
#
# 使い方:
#   bash deploy.sh              # ゲート → sprites drift check → node構文check → wrangler deploy
#   bash deploy.sh --dry-run    # ゲートとcheckだけ（deployしない・検証用）
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# 禁止語regex（base64・平文を置かない）。更新時: printf '<新パターン>' | base64
BANNED="$(printf 'c2hvdGF8b3RvYmV8aGFuYXpvbm985LmZ6YOofOiKseWcknznnIHlpKo=' | base64 -d)"

echo "▶ 公開前 禁止語ゲート（relay/src + wrangler.jsonc）"
hits="$( {
  grep -rinE "$BANNED" "$HERE/src" --exclude=sprites_data.js 2>/dev/null
  grep -rinE "$BANNED" "$HERE/wrangler.jsonc" 2>/dev/null
  # sprites_data.js: base64連鎖だけを行内から除去してから検査（キー名は検査に残る）
  sed -E 's#[A-Za-z0-9+/=]{200,}##g' "$HERE/src/sprites_data.js" 2>/dev/null | grep -inE "$BANNED" | sed 's#^#sprites_data.js:#'
} || true )"
if [ -n "$hits" ]; then
  echo "✗ 禁止語を検出（公開ポリシー違反）— deploy を中止します:"
  echo "$hits" | head -20
  exit 1
fi
echo "  ✓ 禁止語なし"

echo "▶ スプライト索引ドリフト検査（assets/ ⇄ sprites_data.js）"
python3 "$ROOT/tools/gen_pwa_sprites.py" --check || { echo "✗ ドリフト検出 → python3 tools/gen_pwa_sprites.py で再生成してから"; exit 1; }
echo "  ✓ ドリフトなし"

echo "▶ worker.js 構文チェック"
node --check "$HERE/src/worker.js" || { echo "✗ worker.js 構文エラー"; exit 1; }
echo "  ✓ 構文OK"

if [ "${1:-}" = "--dry-run" ]; then
  echo "✅ dry-run: ゲート通過（deployはしていません）"
  exit 0
fi

echo "▶ wrangler deploy"
cd "$HERE" && npx wrangler deploy
