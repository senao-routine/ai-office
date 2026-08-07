#!/bin/bash
# AI Office P4 — Mac常駐化の deploy-copy インストーラ（Terminal＝FDA有 で対話実行する）
#
# やること:
#   1. コードを ~/Library/Application Support/AIOffice/app/ へ rsync --delete（stale一掃）
#   2. データ(config)を .../AIOffice/data/ へシード（非--delete＝UI追記を守る）
#   3. LaunchAgent plist を生成（launchctl は実行しない＝load はユーザー操作。末尾の手順参照）
#   4. SwiftBar が在ればプラグインを配置（無ければ何もしない）
# （R80 Phase4: assets/ とスプライト生成パイプラインは退役＝アセットシード・OPENAI鍵シードは行わない）
#
# 使い方:
#   bash macapp/install.sh                 # 本番インストール（既定 DEST）
#   bash macapp/install.sh --print-plist   # plist を stdout に出すだけ（verify用・副作用なし）
#   bash macapp/install.sh --seed-config   # data/office_config.json を repo から強制再シード
#   AIOFFICE_DEST=/tmp/x bash install.sh   # テスト用: DEST差替（実plist/SwiftBarはスキップ）
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"          # AI Office/macapp
ROOT="$(cd "$HERE/.." && pwd)"                 # AI Office/
source "$HERE/aioffice.env.sh"

DEST="${AIOFFICE_DEST:-$AIOFFICE_DEST_DEFAULT}"
TESTMODE=0
[ -n "${AIOFFICE_DEST:-}" ] && TESTMODE=1      # DEST差替時=テスト: 実plist/鍵/SwiftBarに触れない
CODE="$DEST/app"; DATA="$DEST/data"; LOGDIR="$DEST/logs"

# --- python3 の実体解決（launchd の PATH は最小・shim/venv だと常駐が死ぬ） ---
PYBIN="$(command -v python3)"
[ -x "${PYBIN:-}" ] || { echo "✗ python3 が見つかりません"; exit 1; }
PYBIN="$("$PYBIN" -c 'import sys; print(sys.executable)')"   # pyenv shim 等を実体へ
if ! "$PYBIN" -c 'import sys; raise SystemExit(0 if sys.prefix == sys.base_prefix else 1)' 2>/dev/null; then
  PYBIN=""                                                    # venv内 → 素のpythonへ脱出
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -x "$c" ] && { PYBIN="$c"; break; }
  done
  [ -n "$PYBIN" ] || { echo "✗ venv外の python3 が見つかりません"; exit 1; }
fi
case "$PYBIN" in
  "$HOME/Downloads/"*|"$HOME/Desktop/"*|"$HOME/Documents/"*)
    echo "✗ python3 がTCC保護フォルダ配下です（launchdからspawn不能）: $PYBIN"; exit 1;;
esac

# --- TCC不変条件の自己assert: 実体パス(シンボリックリンク解決後)で保護フォルダ配下を拒否 ---
DEST_REAL="$("$PYBIN" -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$DEST")"
for p in Downloads Desktop Documents "Library/Mobile Documents"; do
  case "$DEST_REAL/" in
    "$HOME/$p/"*)
      echo "✗ DEST がTCC保護フォルダ配下です（launchdがFDA無しで実行できない・実体: ${DEST_REAL}）"; exit 1;;
  esac
done

gen_plist() {  # 引数: 出力先("-"=stdout)。PYBIN/DEST を焼き込む
  local out="$1"
  local body
  body=$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$AIOFFICE_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PYBIN</string>
    <string>$CODE/server/office_server.py</string>
    <string>--port</string><string>4780</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>OFFICE_DATA</key><string>$DATA</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>WorkingDirectory</key><string>$CODE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGDIR/office.daemon.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/office.daemon.log</string>
</dict></plist>
PLIST
)
  if [ "$out" = "-" ]; then printf '%s\n' "$body"; else printf '%s\n' "$body" > "$out"; fi
}

gen_relay_plist() {  # P4.5: relay_agent 常駐（スマホ配達を常時オン）。--port 無し・アウトバウンドのみ
  local out="$1"
  local body
  # KeepAlive=PathState: office_relay.json が在るときだけ launchd が起動＝未デプロイ時の crash-loop 空転を回避
  body=$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$AIOFFICE_RELAY_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PYBIN</string>
    <string>$CODE/server/relay_agent.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>OFFICE_DATA</key><string>$DATA</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>WorkingDirectory</key><string>$CODE</string>
  <key>RunAtLoad</key><false/>
  <key>KeepAlive</key><dict><key>PathState</key><dict><key>$HOME/.claude/office_relay.json</key><true/></dict></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGDIR/relay.daemon.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/relay.daemon.log</string>
</dict></plist>
PLIST
)
  if [ "$out" = "-" ]; then printf '%s\n' "$body"; else printf '%s\n' "$body" > "$out"; fi
}

if [ "${1:-}" = "--print-plist" ]; then gen_plist -; exit 0; fi
if [ "${1:-}" = "--print-relay-plist" ]; then gen_relay_plist -; exit 0; fi

# R42.5: --edition claude|openclaw|hybrid ＝ data/ config の "edition" キーを設定
# （config正本の単一集約点は変えない・他キー温存のRMW。不正値は副作用前に拒否）
EDITION=""
_argv=("$@")
for ((_i=0; _i<${#_argv[@]}; _i++)); do
  if [ "${_argv[$_i]}" = "--edition" ]; then
    EDITION="${_argv[$((_i+1))]:-}"
    case "$EDITION" in
      claude|openclaw|hybrid) ;;
      *) echo "✗ --edition は claude|openclaw|hybrid のいずれか（指定=${EDITION}）"; exit 1;;
    esac
  fi
done

echo "▶ AI Office 常駐インストール → $DEST"

# --- 1) コード配置（rsync --delete・除外=キャッシュ/秘密） ---
mkdir -p "$CODE" "$DATA" "$LOGDIR"
rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  "$ROOT/server" "$ROOT/ui" "$ROOT/tools" "$ROOT/hooks" "$CODE/" \
  || { echo "✗ rsync 失敗"; exit 1; }
# 多ソース+--delete は app/ トップレベルの stale を消さないので明示剪定（openrsyncのフィルタ互換を避ける）
for e in "$CODE"/*; do
  case "$(basename "$e")" in server|ui|tools|hooks) ;; *) rm -rf "$e";; esac
done
find "$CODE" -type d -exec chmod 755 {} + 2>/dev/null
find "$CODE" -type f -name '*.sh' -exec chmod 755 {} + 2>/dev/null
echo "  ✓ コード → app/（server/ui/tools/hooks・--delete+トップレベル剪定でstale一掃）"

# --- 2) データシード（非--delete: config追記は温存） ---
if [ ! -f "$DATA/office_config.json" ] || [ "${1:-}" = "--seed-config" ]; then
  # R51で office_config.json は個人設定＝git非追跡。クリーンcloneは example からシード
  if [ -f "$ROOT/office_config.json" ]; then _CFG_SRC="$ROOT/office_config.json"
  else _CFG_SRC="$ROOT/office_config.example.json"; fi
  cp -p "$_CFG_SRC" "$DATA/office_config.json"
  echo "  ✓ config をシード（$(basename "$_CFG_SRC")）"
else
  echo "  - config は既存を温存（強制再シード= --seed-config）"
fi
if [ -n "$EDITION" ]; then
  "$PYBIN" - "$DATA/office_config.json" "$EDITION" <<'PYEOF'
import json
import sys
from pathlib import Path
p = Path(sys.argv[1])
try:
    d = json.loads(p.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    d = {"projects": {}}
if not isinstance(d, dict):
    d = {"projects": {}}
d["edition"] = sys.argv[2]
tmp = p.with_name(p.name + ".tmp")
tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
tmp.replace(p)
print(f"  ✓ edition = {sys.argv[2]} を data config へ設定")
PYEOF
fi
echo "  ✓ データ → data/（config追記は非破壊）"

# --- 3) plist 生成＋lint（office_server と relay_agent の2本・実 launchctl はしない） ---
if [ "$TESTMODE" = "0" ]; then
  PLIST_OUT="$AIOFFICE_PLIST"; RELAY_PLIST_OUT="$AIOFFICE_RELAY_PLIST"; mkdir -p "$(dirname "$PLIST_OUT")"
else
  PLIST_OUT="$DEST/com.senao.aioffice.plist"; RELAY_PLIST_OUT="$DEST/com.senao.aioffice.relay.plist"
fi
gen_plist "$PLIST_OUT"
plutil -lint "$PLIST_OUT" >/dev/null || { echo "✗ plist lint 失敗: $PLIST_OUT"; exit 1; }
gen_relay_plist "$RELAY_PLIST_OUT"
plutil -lint "$RELAY_PLIST_OUT" >/dev/null || { echo "✗ relay plist lint 失敗: $RELAY_PLIST_OUT"; exit 1; }
echo "  ✓ LaunchAgent plist ×2 → office + relay（plutil lint合格・python=${PYBIN}）"

# --- 4) SwiftBar プラグイン（在れば配置・無ければ何もしない） ---
SBDIR="$HOME/Library/Application Support/SwiftBar/Plugins"
if [ "$TESTMODE" = "0" ] && [ -d "$SBDIR" ]; then
  cp -p "$HERE/swiftbar/aioffice.5s.sh" "$SBDIR/" && chmod +x "$SBDIR/aioffice.5s.sh"
  echo "  ✓ SwiftBarプラグイン配置"
fi

echo
echo "✅ インストール完了。常駐の登録はあなたの操作で（launchctl は本スクリプトは実行しません）:"
echo "   launchctl enable   gui/\$(id -u)/$AIOFFICE_LABEL      # 先にenable（disabled中のbootstrapはerror 119）"
echo "   launchctl bootstrap gui/\$(id -u) \"$AIOFFICE_PLIST\""
echo "   launchctl kickstart -k gui/\$(id -u)/$AIOFFICE_LABEL   # 即起動 → open http://localhost:4780"
echo "   ※停止は launchctl bootout gui/\$(id -u)/${AIOFFICE_LABEL}（kill はKeepAliveで復活する）"
echo "   ※plist を変えた再デプロイは bootout → bootstrap（kickstart -k では plist 再読込されない）"
echo
echo "── スマホ配達を常時オンにする（P4.5・任意・要 ~/.claude/office_relay.json デプロイ済み）──"
echo "   launchctl enable   gui/\$(id -u)/$AIOFFICE_RELAY_LABEL"
echo "   launchctl bootstrap gui/\$(id -u) \"$AIOFFICE_RELAY_PLIST\""
echo "   launchctl kickstart -k gui/\$(id -u)/$AIOFFICE_RELAY_LABEL   # office_relay.json が在れば起動（無ければ待機）"
echo "   ※停止= launchctl bootout gui/\$(id -u)/${AIOFFICE_RELAY_LABEL}／観測= logs/relay.daemon.log"
