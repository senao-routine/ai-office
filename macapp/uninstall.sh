#!/bin/bash
# AI Office 常駐のアンインストール。既定はコード+plistのみ削除（data/=configは温存）。
# 全消し（データ含む）は --purge-data。launchctl bootout は安全のため自動実行せずコマンド表示。
# AIOFFICE_DEST 指定時=テストモード: 実plist/実SwiftBar/launchctlに一切触れない（テスト後片付け用）。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/aioffice.env.sh"
DEST="${AIOFFICE_DEST:-$AIOFFICE_DEST_DEFAULT}"
TESTMODE=0
[ -n "${AIOFFICE_DEST:-}" ] && TESTMODE=1

# --- rm -rf 前の安全ガード: 絶対パス・HOME自体拒否・AIOffice配置の目印必須 ---
case "$DEST" in /*) ;; *) echo "✗ DEST は絶対パス必須: $DEST"; exit 1;; esac
[ "$DEST" != "$HOME" ] && [ "$DEST" != "/" ] || { echo "✗ DEST=$DEST は拒否"; exit 1; }
if [ ! -e "$DEST/app/server/office_server.py" ] && [ ! -e "$DEST/com.senao.aioffice.plist" ] \
   && [ ! -e "$DEST/data/office_config.json" ]; then
  echo "✗ $DEST は AIOffice の配置に見えません（削除中止）"; exit 1
fi

if [ "$TESTMODE" = "0" ]; then
  for lbl in "$AIOFFICE_LABEL" "$AIOFFICE_RELAY_LABEL"; do
    if launchctl print "gui/$(id -u)/$lbl" >/dev/null 2>&1; then
      echo "⚠ 常駐($lbl)がまだ登録されています。先にあなたの操作で外してください:"
      echo "   launchctl bootout gui/\$(id -u)/$lbl"
      exit 1
    fi
  done
  rm -f "$AIOFFICE_PLIST" "$AIOFFICE_RELAY_PLIST" && echo "✓ plist 削除: office + relay"
  rm -f "$HOME/Library/Application Support/SwiftBar/Plugins/aioffice.5s.sh" 2>/dev/null
else
  rm -f "$DEST/com.senao.aioffice.plist" "$DEST/com.senao.aioffice.relay.plist"   # TESTMODE のテスト用plistのみ
fi
rm -rf "$DEST/app" "$DEST/logs" && echo "✓ コード/ログ削除: $DEST/{app,logs}"
if [ "${1:-}" = "--purge-data" ]; then
  rm -rf "$DEST/data" && rmdir "$DEST" 2>/dev/null
  echo "✓ データも削除: $DEST/data"
else
  echo "- data/（config）は温存: $DEST/data（全消しは --purge-data）"
fi
echo "✅ アンインストール完了（~/.claude/office_secrets は触っていません）"
