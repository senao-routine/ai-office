#!/bin/bash
# PostToolUse(Edit|Write): 編集したファイルの種類に応じて即時チェック（場所非依存）
#   server|tools の .py  → 構文 ＋ stdlib番人（server のみ）
#   ui/**.js             → 構文 ＋ 層lint ＋ core ユニット
#   ui/**.json           → JSONパース
# 失敗は exit 2 でモデルにフィードバック（フルverifyはコミット前に別途）
set -u
IN=$(cat 2>/dev/null || true)
FP=$(printf '%s' "$IN" | /usr/bin/python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")' 2>/dev/null)

case "$FP" in
  */ui/vendor/*)              exit 0 ;;   # vendor は検査対象外（three.js 等の配布物）
  */server/*.py|*/tools/*.py) KIND=py ;;
  */ui/*.js)                  KIND=js ;;
  */ui/*.json)                KIND=json ;;
  *)                          exit 0 ;;
esac

# リポジトリのルートを file_path から逆算する（作業ディレクトリに依存させない）
ROOT="${FP%%/server/*}"; ROOT="${ROOT%%/tools/*}"; ROOT="${ROOT%%/ui/*}"

case "$KIND" in
  py)
    ERR=$(python3 -m py_compile "$FP" 2>&1) || { echo "⛔ 構文エラー: $ERR"; exit 2; }
    case "$FP" in
      *"/server/"*.py)
        ERR=$(python3 "$ROOT/tools/check_stdlib.py" "$FP" 2>&1) \
          || { echo "⛔ $ERR (server/はstdlibのみ)"; exit 2; }
        ;;
    esac
    ;;
  js)
    ERR=$(node --check "$FP" 2>&1) || { echo "⛔ JS構文エラー: $ERR"; exit 2; }
    # 層の逆流（core が DOM/通信/乱数に触る等）はここで止める＝core のテスト可能性を守る
    ERR=$(python3 "$ROOT/tools/js_layer_lint.py" 2>&1) || { echo "⛔ 層lint:"; echo "$ERR"; exit 2; }
    if ls "$ROOT"/ui/core/*.test.js >/dev/null 2>&1; then
      ERR=$(cd "$ROOT" && node --test ui/core/*.test.js 2>&1) || {
        echo "⛔ core ユニット失敗:"; echo "$ERR" | tail -25; exit 2; }
    fi
    ;;
  json)
    ERR=$(python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$FP" 2>&1) \
      || { echo "⛔ JSONが壊れています: $ERR"; exit 2; }
    ;;
esac
exit 0
