#!/bin/bash
# ──────────────────────────────────────────────────────────────
# 新UI(R50)の開発ループ。「実行 → 観測 → 修正」を1コマンドで回す。
#
#   bash dev.sh            fixtureサーバー(:4797)を起動 → 両スタイルを開く → 保存を監視
#   bash dev.sh --check    監視なしで高速レーンだけ走らせる（コミット前の下見）
#   bash dev.sh --shot     両スタイルの現在の絵を tests/artifacts/ へ撮る
#   bash dev.sh --golden   golden を撮り直す（見た目を意図的に変えたとき）
#
# 高速レーン = 構文 / stdlib番人 / 層lint / core ユニット。数秒で返ることを目標にする。
# フルレーンは bash verify.sh。
# ──────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")"
PORT=${DEV_PORT:-4797}
VENV_PY="${VENV_PY:-}"
[ -z "$VENV_PY" ] && [ -f verify.local ] && . ./verify.local
NG=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
ng() { printf '  \033[31m✗\033[0m %s\n' "$1"; NG=$((NG + 1)); }

fast_lane() {
  NG=0
  local out
  out=$(python3 -m py_compile server/*.py tools/*.py tests/*.py 2>&1) \
    && ok "python 構文" || ng "python 構文: $out"
  out=$(python3 tools/check_stdlib.py server/*.py 2>&1) \
    && ok "stdlib番人" || ng "stdlib番人: $out"
  out=$(python3 tools/js_layer_lint.py 2>&1) \
    && ok "層lint（core が DOM/通信/乱数に触っていない）" || { ng "層lint"; echo "$out"; }
  local jsng=0
  for f in $(find ui -name '*.js' -not -path 'ui/vendor/*' 2>/dev/null); do
    node --check "$f" 2>/dev/null || { echo "    構文エラー: $f"; jsng=1; }
  done
  [ "$jsng" = "0" ] && ok "JS 構文" || ng "JS 構文"
  if ls ui/core/*.test.js >/dev/null 2>&1; then
    out=$(node --test ui/core/*.test.js 2>&1)
    echo "$out" | grep -q "^# fail 0\|ℹ fail 0" \
      && ok "core ユニット ($(echo "$out" | grep -oE 'ℹ pass [0-9]+' | grep -oE '[0-9]+') 件)" \
      || { ng "core ユニット"; echo "$out" | tail -20; }
  fi
  return $NG
}

start_server() {
  if lsof -ti tcp:$PORT >/dev/null 2>&1; then
    echo "  :$PORT に先客がいます（既存のサーバーを使います）"
    return 0
  fi
  VHOME=$(python3 tests/make_home.py)
  mkdir -p "$VHOME/data"
  OFFICE_HOME="$VHOME" OFFICE_CONFIG="$VHOME/office_config.json" OFFICE_DATA="$VHOME/data" \
    python3 server/office_server.py --port $PORT >logs/dev.log 2>&1 &
  SPID=$!
  # 片付けは EXIT に、INT/TERM は「exitする」だけにする。
  # INT/TERM に片付けを直接書くと handler 実行後にスクリプトが続行し、
  # Ctrl+C がサーバーだけ殺して監視ループが不死身になる（実際にターミナルが返らなくなった）。
  trap 'kill $SPID 2>/dev/null; rm -rf "$VHOME"' EXIT
  trap 'exit 130' INT TERM
  for _ in $(seq 40); do
    curl -sf -o /dev/null -H "X-Office-Local: 1" "http://127.0.0.1:$PORT/api/office" && break
    sleep .1
  done
  ok "fixtureサーバー :$PORT (log: logs/dev.log)"
}

case "${1:-}" in
  --check)
    echo "▶ 高速レーン"
    fast_lane
    [ "$NG" = "0" ] && { echo "✅ 高速レーン green"; exit 0; } || { echo "❌ $NG 件"; exit 1; }
    ;;
  --shot|--golden)
    [ -x "$VENV_PY" ] || { echo "Playwright入りのpythonが要ります（verify.local の VENV_PY）"; exit 1; }
    if [ "${1:-}" = "--golden" ]; then
      "$VENV_PY" tools/ui_shot.py --update
    else
      "$VENV_PY" tools/ui_shot.py --check
    fi
    exit $?
    ;;
esac

mkdir -p logs
echo "▶ 起動"
start_server
echo "  新UI(3D)     http://127.0.0.1:$PORT/?ui=iso"
command -v open >/dev/null && open "http://127.0.0.1:$PORT/?ui=iso" 2>/dev/null

echo "▶ 初回チェック"
fast_lane
echo "▶ 監視中（Ctrl+C で終了）— server/ tools/ ui/ の保存を検知したら高速レーンを回します"
SIG=""
while true; do
  NEW=$(find server tools ui tests -type f \( -name '*.py' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' \) \
        -not -path 'ui/vendor/*' -not -path '*/__pycache__/*' -newermt '-2 seconds' 2>/dev/null | sort | tr '\n' ' ')
  if [ -n "$NEW" ] && [ "$NEW" != "$SIG" ]; then
    SIG="$NEW"
    echo ""
    echo "▶ $(date '+%H:%M:%S') 変更: $(echo "$NEW" | tr ' ' '\n' | grep -c . ) ファイル"
    fast_lane
    if [ "$NG" != "0" ] && command -v osascript >/dev/null; then
      osascript -e "display notification \"高速レーンで $NG 件\" with title \"AI Office dev\"" 2>/dev/null
    fi
  fi
  sleep 1.5
done
