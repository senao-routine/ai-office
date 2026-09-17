#!/bin/bash
# R97-D: 配布物（zip / tar.gz）を作る。note の「すぐ動く zip」・Discord へのドロップ・
# GitHub Release の添付を、この 1 本で賄う。
#
# 型は公開リポ更新（tools/publish_snapshot.sh）と同じ:
#   git archive HEAD → 内部物を除外（正本= tools/_publish_excludes.sh）→ 禁止語ゲート →
#   顧客名スクラブの回帰 → 除外の機械検査。**そのうえで、展開して実際に起動するところまで確かめる**
#   （R80 で確立した「配布物そのものを実測する」型。素の /usr/bin/python3 で回す＝配布先で死なない）。
#
# 公開リポとの違い（run プロファイル）: 動かす人に要らない開発ハーネスを落とす。
#   tests/ は golden PNG で 4MB 超あり、verify.sh / dev.sh は tests/ が無いと動かない＝**同時に**落とす
#   （片方だけ残すと「動かない検証器」を配ることになる）。
#
# 使い方:
#   bash tools/pack_release.sh              # dist/ai-office-<version>.{tar.gz,zip} を作って実測
#   bash tools/pack_release.sh --dry-run    # 中身の一覧とサイズだけ（作らない）
#   bash tools/pack_release.sh --check      # 除外が効くかだけ（HEAD を見る・汚れたツリーでも可・verify 用）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
DRY="${1:-}"
VERSION="$(python3 server/office_version.py)"
NAME="ai-office-$VERSION"

. tools/_publish_excludes.sh
# run プロファイルの追加除外（開発ハーネスと美術の内部素材）
RUN_EXCLUDES=(
  "tests"            # golden PNG が重い。配布先は verify を回さない
  "verify.sh"        # tests/ が無いと動かない＝tests と必ずセットで落とす
  "dev.sh"
  "verify.local"
  "docs/art"         # 内部レビュー用の美術ボード
  ".github"          # Issue テンプレは公開リポの機能（zip には要らない）
)

# --check は「除外の仕組みが効いているか」だけを見る（HEAD が対象なので汚れていてもよい）
if [ "$DRY" != "--check" ] && { ! git diff --quiet || ! git diff --cached --quiet; }; then
  echo "✗ ワーキングツリーが汚れています。commit してから実行してください" >&2
  exit 1
fi

STAGE="$(mktemp -d /tmp/aioffice-pack.XXXXXX)"
RUNPID=""
pack_cleanup() {
  # 回収済みなら RUNPID は空。set -e の下では「失敗する kill」で後始末ごと落ちるので必ず || true
  if [ -n "$RUNPID" ]; then
    kill "$RUNPID" 2>/dev/null || true
    wait "$RUNPID" 2>/dev/null || true
    RUNPID=""
  fi
  rm -rf "$STAGE" || true
}
trap 'pack_cleanup; exit 130' INT TERM
trap 'pack_cleanup' EXIT
mkdir -p "$STAGE/$NAME"
git archive HEAD | tar -x -C "$STAGE/$NAME"
for x in "${EXCLUDES[@]}" "${RUN_EXCLUDES[@]}"; do
  case "$x" in \#*) continue ;; esac
  rm -rf "${STAGE:?}/$NAME/$x"
done

# 公開の絶対条件（公開リポと同じゲート）。ただし**配るときだけ**必須にする:
# --check は「除外の仕組みが効くか」を見るだけで、配布物は作らない。ここで個人環境専用の
# スクリプトを要求すると、それを持たない人の verify が必ず落ちる（別モデルレビュー・exit 127）。
NG_CHECK="$HOME/.claude/scripts/ng_check.sh"
if [ -x "$NG_CHECK" ]; then
  bash "$NG_CHECK" "$STAGE/$NAME"
elif [ "$DRY" = "--check" ]; then
  echo "  ・ 禁止語ゲートは省略（この環境には無い・配布物は作らないので可）"
else
  echo "✗ 禁止語ゲート（${NG_CHECK}）が無い環境では配布物を作らない" >&2
  exit 1
fi
# 顧客名の再混入ガード（R52.1 の全面改名の回帰）。**パターンはハッシュでしか持たない**:
# このスクリプト自身が公開物と配布物に入るので、base64 のような可逆な保持をすると
# そこから顧客名が読める（relay/deploy.sh が同じ理由で SHA-256 にしている）。
if ! SCRUB_SHA="9a892562e86e35a6" SCRUB_LEN=6 python3 - "$STAGE/$NAME" <<'PYSCRUB'
import hashlib, os, re, sys
from pathlib import Path
banned = {os.environ["SCRUB_SHA"]}
word = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
# 語**そのもの**だけを見ると `project_<語>-app` のような埋め込みを取りこぼす（旧実装の部分一致は拾えていた）。
# 語の中を長さ WINDOW で走査して同じ照合をする（長さが分かってもハッシュから復元はできない）。
WINDOW = int(os.environ["SCRUB_LEN"])
hits = []
for f in Path(sys.argv[1]).rglob("*"):
    if not f.is_file() or f.suffix in {".png", ".webp", ".gif", ".jpg", ".mp4", ".sqlite"}:
        continue
    try:
        text = f.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for w in set(word.findall(text)):
        lw = w.lower()
        for i in range(0, max(0, len(lw) - WINDOW) + 1):
            if hashlib.sha256(lw[i:i + WINDOW].encode("utf-8")).hexdigest()[:16] in banned:
                hits.append(f"{f.name}: {w[:2]}…（伏字）")
                break
if hits:
    print("✗ 顧客名の再混入を検出。配布物の作成を中断します:")
    for h in hits[:10]:
        print("   " + h)
    sys.exit(1)
PYSCRUB
then
  exit 1
fi
leak=0
for x in "${EXCLUDES[@]}" "${RUN_EXCLUDES[@]}"; do
  case "$x" in \#*) continue ;; esac
  [ -e "$STAGE/$NAME/$x" ] && { echo "✗ 除外対象が残っています: $x" >&2; leak=1; }
done
[ "$leak" = "0" ] || { echo "✗ 中断します（除外の取りこぼし）" >&2; exit 1; }
COUNT="$(find "$STAGE/$NAME" -type f | wc -l | tr -d ' ')"
SIZE="$(du -sk "$STAGE/$NAME" | cut -f1)"
echo "  ✓ 除外 $(( ${#EXCLUDES[@]} + ${#RUN_EXCLUDES[@]} )) 項目すべて不在（機械検査）"
echo "  ✓ 中身: ${COUNT} ファイル / ${SIZE}KB"

if [ "$DRY" = "--check" ]; then
  echo "✓ 配布物の除外が効いている（${COUNT} ファイル / ${SIZE}KB・実際の作成は bash tools/pack_release.sh）"
  exit 0
fi
if [ "$DRY" = "--dry-run" ]; then
  (cd "$STAGE/$NAME" && find . -maxdepth 1 -mindepth 1 | sort | sed 's|^\./|    |')
  echo "✓ --dry-run: ここまで（作らない）"
  exit 0
fi

# ── 展開して実際に起動する（配布先で死なないことを、配る前に自分で確かめる）──────────
RUNHOME="$STAGE/home"; mkdir -p "$RUNHOME/.claude"
PORT="$(/usr/bin/python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
HOME="$RUNHOME" OFFICE_HOME="$RUNHOME" OFFICE_DATA="$RUNHOME" \
  /usr/bin/python3 "$STAGE/$NAME/server/office_server.py" --port "$PORT" >"$STAGE/run.log" 2>&1 &
RUNPID=$!
ok=0
for _ in $(seq 1 20); do
  sleep 0.4
  if curl -sf -H "X-Office-Local: 1" "http://127.0.0.1:$PORT/api/office" 2>/dev/null | grep -q '"employees"'; then ok=1; break; fi
done
demo=0
[ "$ok" = "1" ] && curl -sf -o /dev/null "http://127.0.0.1:$PORT/?demo=1" 2>/dev/null && demo=1
kill "$RUNPID" 2>/dev/null || true
wait "$RUNPID" 2>/dev/null || true
RUNPID=""     # 回収済み。EXIT トラップで二重に kill しない
[ "$ok" = "1" ] || { echo "✗ 配布物が素の python3 で起動しない → $(tail -1 "$STAGE/run.log")" >&2; exit 1; }
[ "$demo" = "1" ] || { echo "✗ 配布物の ?demo=1 が開かない" >&2; exit 1; }
echo "  ✓ 展開 → 素の /usr/bin/python3 で起動 → /api/office と ?demo=1 が応答"

mkdir -p dist
rm -f "dist/$NAME.tar.gz" "dist/$NAME.zip"
(cd "$STAGE" && tar -czf "$ROOT/dist/$NAME.tar.gz" "$NAME")
(cd "$STAGE" && zip -qr "$ROOT/dist/$NAME.zip" "$NAME")
for f in "dist/$NAME.tar.gz" "dist/$NAME.zip"; do
  echo "  $(shasum -a 256 "$f" | cut -d' ' -f1)  $f  （$(du -k "$f" | cut -f1)KB）"
done
echo "✓ 配布物: dist/${NAME}.tar.gz / dist/${NAME}.zip（v${VERSION}）"
