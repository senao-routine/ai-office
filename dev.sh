#!/bin/bash
# ──────────────────────────────────────────────────────────────
# 新UI(R50)の開発ループ。「実行 → 観測 → 修正」を1コマンドで回す。
#
#   bash dev.sh            fixtureサーバー(:4797)を起動 → 両スタイルを開く → 保存を監視
#   bash dev.sh --check    監視なしで高速レーンだけ走らせる（コミット前の下見）
#   bash dev.sh --shot     app / iso / pixel / cockpit の今の絵を撮って golden と比べる（書かない）
#   bash dev.sh --app-smoke 明るい画面の操作と 4 形状を Playwright で検査する
#   bash dev.sh --golden [名前]
#                          名指しした golden 1 枚を撮り直す（見た目を意図的に変えたとき・既定 iso）。
#                          名前は下の golden_args の表（verify ▶7・golden-update SKILL の表と同じ行）。
#                          撮った直後にもう一度撮って全画面を許容 0 で比べ、合わなければ元の golden に戻す。
#
# 高速レーン = 構文 / stdlib番人 / 層lint / 配線lint / core ユニット / cockpit の node テスト / design ゲート。
# 数秒で返ることを目標にする。
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
  out=$(python3 tools/check_py39.py 2>&1) \
    && ok "py39番人（素の Mac の python3 で server/ が import できる）" || { ng "py39番人"; echo "$out"; }
  out=$(python3 tools/check_version.py 2>&1) \
    && ok "版番人（正本 1 箇所から README・MCP・CHANGELOG へ）" || { ng "版番人"; echo "$out"; }
  out=$(python3 tools/js_layer_lint.py 2>&1) \
    && ok "層lint（core が DOM/通信/乱数に触っていない）" || { ng "層lint"; echo "$out"; }
  out=$(python3 tools/css_var_lint.py 2>&1) \
    && ok "CSS変数lint（未定義の var() は黙って無視される）" || { ng "CSS変数lint"; echo "$out"; }
  out=$(python3 tools/mjs_wire_lint.py 2>&1) \
    && ok "配線lint（tests/*.mjs・スモークが verify から走る・golden の表 3 か所が一致）" || { ng "配線lint"; echo "$out"; }
  local jsng=0
  for f in $(find ui -name '*.js' -not -path 'ui/vendor/*' 2>/dev/null); do
    node --check "$f" 2>/dev/null || { echo "    構文エラー: $f"; jsng=1; }
  done
  [ "$jsng" = "0" ] && ok "JS 構文" || ng "JS 構文"
  # R99-A3: design/ の受入ゲート（verify ▶2b と同じ呼び出し）。委譲の成果を受け取る前の下見に効く。
  # 何をどのファイルで見るかの正本は `python3 design/check.py --list`（ここに掟を書き写さない）
  if [ -d design ]; then
    out=$(python3 design/check.py 2>&1) \
      && ok "design ゲート（cockpit のドットの作り方の掟）" || { ng "design ゲート"; echo "$out"; }
  elif [ -f tools/publish_snapshot.sh ]; then
    ng "design/ が無い（開発ツリーでは design ゲートは必須）"
  else
    printf '  ⚠ %s\n' "design/ は公開物に含めない＝design ゲートは省略"
  fi
  # R99 Phase 3: 俯瞰の部屋のドットの門（verify ▶3b と同じ呼び出し・何を見るかの正本は tools/px_lint.py の①〜⑦）。
  # 委譲先（Codex）が生成器を走らせた直後の下見に効く（生成器の編集は Stop hook の lint に乗らない）
  out=$(python3 tools/px_lint.py --cockpit 2>&1) \
    && ok "ドット lint（cockpit・寸法の三点一致／全ベンダー×全コマ／歩行の差／タイル／配置表／契約／画像 0）" \
    || { ng "ドット lint（cockpit）"; echo "$out"; }
  # R99: cockpit の node テスト（verify ▶2b の node --test にもある分の下見）。tests/ の .mjs は
  # 下の core ユニットの glob（ui/core/*.test.js）に乗らない＝ここで名指しする。
  # ★配線の正本は verify ▶2b。配線 lint は dev.sh を数えない（ここにだけ書いても「未配線」で赤）
  out=$(node --test tests/pxroom_tiles.test.mjs tests/activity.test.mjs tests/activity_view.test.mjs tests/activity_feed.test.mjs tests/cockpit_fixture.test.mjs tests/cockpit_room.test.mjs \
    tests/cockpit_cells.test.mjs 2>&1) \
    && ok "cockpit の node テスト（家具の表とドット・固定ワールドの語彙・席の帳簿・俯瞰のコマ表とドット）" \
    || { ng "cockpit の node テスト"; echo "$out" | tail -20; }
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

# golden の名前 → ui_shot の引数。verify ▶7 と .claude/skills/golden-update の表と同じ行（足すときは 3 か所・
# 揃っているかは配線 lint＝tools/mjs_wire_lint.py が見る。1 か所でも足し忘れると --check で赤）。
# --world を付ける行は --name 必須（world は golden の名前に入らない＝忘れると既定の 1 枚を上書きする）。
golden_args() {
  case "$1" in
    app)            echo "--style app" ;;
    iso)            echo "--style iso" ;;
    iso_stream1)    echo "--style iso --query stream=1" ;;
    xl22)           echo "--style iso --world xl22 --name xl22" ;;
    pixel)          echo "--style pixel" ;;
    pixel_xl22)     echo "--style pixel --world xl22 --name pixel_xl22" ;;
    pixel_834x1112) echo "--style pixel --world xl22 --viewport 834x1112 --name pixel_834x1112" ;;
    cockpit)        echo "--style cockpit" ;;
    cockpit_xl22)   echo "--style cockpit --world xl22 --name cockpit_xl22" ;;
    cockpit_fx)     echo "--style cockpit --world cockpit --name cockpit_fx" ;;
    cockpit_1920x1080) echo "--style cockpit --world cockpit --viewport 1920x1080 --name cockpit_1920x1080" ;;
    *) return 1 ;;
  esac
}

case "${1:-}" in
  --app-smoke)
    "${VENV_PY:-python3}" tests/app_smoke.py
    exit $?
    ;;
  --check)
    echo "▶ 高速レーン"
    fast_lane
    [ "$NG" = "0" ] && { echo "✅ 高速レーン green"; exit 0; } || { echo "❌ $NG 件"; exit 1; }
    ;;
  --shot|--golden)
    [ -x "$VENV_PY" ] || { echo "Playwright入りのpythonが要ります（verify.local の VENV_PY）"; exit 1; }
    if [ "${1:-}" = "--golden" ]; then
      # R99-A1: 撮り直すのは名指しした 1 枚だけ（既定 iso）。全部を一度に撮り直すと、
      # 別の golden に紛れていたずれまで黙って正にしてしまう。
      gname="${2:-iso}"
      gargs=$(golden_args "$gname") \
        || { echo "--golden の名前は $(sed -n '/^golden_args() {/,/^}/s/^ *\([a-z0-9_][a-z0-9_]*\)) .*/\1/p' dev.sh | tr '\n' ' ')のどれか（いま: ${gname}）"; exit 2; }
      gpath="tests/visual/golden/${gname}.png"
      gbak=""
      if [ -f "$gpath" ]; then
        # cp -p: mktemp の 0600 のまま戻すと golden の権限が黙って 0600 に変わる（第 1 波の検収で実測）
        gbak=$(mktemp -t aioffice_golden) && cp -p "$gpath" "$gbak" || { echo "golden の退避に失敗: $gpath"; exit 1; }
      fi
      # 撮影が赤（JS エラー・drawCalls 等）でも ui_shot は先に golden を書いてしまう＝赤なら必ず元に戻す
      grestore() { if [ -n "$gbak" ]; then mv -f "$gbak" "$gpath"; else rm -f "$gpath"; fi; }
      trap 'grestore; exit 130' INT TERM
      # shellcheck disable=SC2086  # gargs は空白区切りの引数列（値に空白を含む行は表に置かない）
      if ! "$VENV_PY" tools/ui_shot.py $gargs --update; then
        grestore; echo "❌ 撮影が赤（上の行）→ golden を撮る前の状態に戻した: $gpath"; exit 1
      fi
      # 撮った直後にもう一度撮り、全画面を許容 0（チャンネル単位）で比べる＝2 回目とビット一致
      # しない（決定論でない）絵は golden にしない。日々の --check の 0.5% では揺れが素通りする。
      # shellcheck disable=SC2086
      if ! "$VENV_PY" tools/ui_shot.py $gargs --check --exact; then
        grestore; echo "❌ 2 回目の撮影とビット一致しない（決定論でない）→ golden を撮る前の状態に戻した: $gpath"; exit 1
      fi
      trap - INT TERM
      rm -f "$gbak"
      echo "✅ golden を撮り直した: ${gpath}（2 回目の撮影と全画面ビット一致）"
      exit 0
    fi
    # 比べるだけ（書かない）。既定の 3 様式＋cockpit の追加 3 枚（部屋を詰めた xl22・語彙を揃えた fixture・R110 の 1920×1080＝部屋 ×4）。
    # 残りの別 golden（iso_stream1 / xl22 / pixel_xl22 / pixel_834x1112）は verify ▶7 が見る。
    rc=0
    for gname in app iso pixel cockpit cockpit_xl22 cockpit_fx cockpit_1920x1080; do
      # shellcheck disable=SC2086
      "$VENV_PY" tools/ui_shot.py $(golden_args "$gname") --check || rc=1
    done
    exit $rc
    ;;
esac

mkdir -p logs
echo "▶ 起動"
start_server
echo "  明るい画面     http://127.0.0.1:$PORT/?ui=app"
command -v open >/dev/null && open "http://127.0.0.1:$PORT/?ui=app" 2>/dev/null

echo "▶ 初回チェック"
fast_lane
echo "▶ 監視中（Ctrl+C で終了）— server/ tools/ ui/ tests/ design/ の保存を検知したら高速レーンを回します"
SIG=""
while true; do
  NEW=$(find server tools ui tests design -type f \( -name '*.py' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' \) \
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
