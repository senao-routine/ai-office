#!/bin/bash
# R97-C: 公開デモを配る（静的ファイルだけ・Cloudflare の無料枠）。
#
# ゲート: 作り直す → スモークで「サーバー無しで動く・/api を叩かない」を確かめる → 禁止語検査 → deploy。
# 公開面なので `~/.claude/scripts/ng_check.sh`（本名・所属の秘匿）を必ず通す。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 1
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY=1

python3 tools/build_demo.py || exit 1

NG_CHECK="$HOME/.claude/scripts/ng_check.sh"
if [ -x "$NG_CHECK" ]; then
  bash "$NG_CHECK" dist/demo-site || { echo "✗ 禁止語ゲートで停止（公開しない）"; exit 1; }
  echo "  ✓ 禁止語ゲート"
else
  echo "✗ 禁止語ゲート（${NG_CHECK}）が無い環境では公開しない"; exit 1
fi

VENV_PY="${VENV_PY:-}"
[ -z "$VENV_PY" ] && [ -f "$ROOT/verify.local" ] && . "$ROOT/verify.local"
if [ -n "${VENV_PY:-}" ] && [ -x "$VENV_PY" ]; then
  "$VENV_PY" tests/demo_site_smoke.py || { echo "✗ デモが成立していないので公開しない"; exit 1; }
else
  echo "  ⚠ Playwright が無い環境: デモの動作確認を省略（bash verify.sh で確認してから公開すること）"
fi

[ -n "$DRY" ] && { echo "✓ --dry-run: ここまで（deploy しない）"; exit 0; }
cd "$HERE" && npx wrangler deploy
