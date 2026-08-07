#!/bin/bash
# AI Office relay — 公開前ゲート付きデプロイ（wrangler deploy はこのスクリプト経由で行う）
#
# ゲート（公開ポリシー: 本名・所属の秘匿・~/.claude/CLAUDE.md 冒頭）:
#   Worker(workers.dev) と同梱PWA/スプライト索引は「すでに公開面」。
#   deploy 前に禁止語（本名・旧所属・実パス文字列）を機械検査し、ヒットしたら失敗する。
#   - パターンはSHA-256でのみ保持（本スクリプトは公開リポに含まれる＝可逆な保持をしない）
#
# 使い方:
#   bash deploy.sh              # 禁止語ゲート(ハッシュ照合) → node構文check → wrangler deploy
#   bash deploy.sh --dry-run    # ゲートとcheckだけ（deployしない・検証用）
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# R80-B8: 禁止語は **ハッシュでしか持たない**（このスクリプトは公開リポに含まれる）。
# 旧実装は base64 で保持していたが base64 は暗号ではなく、デコードすれば本名・旧所属が読めた
# ＝公開ポリシー（本名・所属の秘匿）に自分で違反していた。平文もbase64も置かない。
# 照合は python3（本製品の必須要件）で1パス＝1.2MBのバンドルでも一瞬。
BANNED_SHA="c55fd8a7f68dd354 5d1f2281b3389cb5 bda574c0a28461f7 4dcaad6bf9930ef3 4b262af2a4c3fded 74acd9c3a32df2fa"

echo "▶ 公開前 禁止語ゲート（relay/src + wrangler.jsonc・ハッシュ照合）"
if ! BANNED_SHA="$BANNED_SHA" python3 - "$HERE/src" "$HERE/wrangler.jsonc" <<'PYGATE'
import hashlib, os, re, sys
from pathlib import Path
banned = set(os.environ.get("BANNED_SHA", "").split())
targets = []
for arg in sys.argv[1:]:
    p = Path(arg)
    targets += sorted(p.glob("*.js")) if p.is_dir() else [p]
word = re.compile(r"[A-Za-z\u00c0-\uffff]+")
hits = []
for f in targets:
    try:
        text = f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for w in set(word.findall(text)):
        d = hashlib.sha256(w.lower().encode("utf-8")).hexdigest()[:16]
        if d in banned:
            hits.append(f"{f.name}: {w[:2]}…（伏字）")
if hits:
    print("✗ 禁止語を検出（公開ポリシー違反）— deploy を中止します:")
    for h in hits[:20]:
        print("   " + h)
    sys.exit(1)
PYGATE
then
  exit 1
fi
echo "  ✓ 禁止語なし"


echo "▶ worker.js 構文チェック"
node --check "$HERE/src/worker.js" || { echo "✗ worker.js 構文エラー"; exit 1; }
echo "  ✓ 構文OK"

if [ "${1:-}" = "--dry-run" ]; then
  echo "✅ dry-run: ゲート通過（deployはしていません）"
  exit 0
fi

echo "▶ wrangler deploy"
cd "$HERE" && npx wrangler deploy
