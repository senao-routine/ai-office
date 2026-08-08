#!/bin/bash
# AI Office 本番サーバー運用: start / stop / status / log
# 注意: P4常駐(launchd)登録中は 4780 は daemon が唯一の所有者。start/stop は拒否する
#       （stop=kill は KeepAlive で即復活する偽成功・直後の start は daemon を EADDRINUSE ループに落とすため）。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/office.log"
PORT=4780
# P4: 常駐インストール済み（plist実在＋configシード済）なら dev も同じ data/ を読む＝分岐させない。
# 明示 export 済みの OFFICE_DATA が最優先（OFFICE_CONFIG/OFFICE_HOME と同じ注入慣例）。
if [ -f "$ROOT/macapp/aioffice.env.sh" ]; then
  source "$ROOT/macapp/aioffice.env.sh"
else
  echo "⚠ macapp/aioffice.env.sh が無い: OFFICE_DATA 切替をスキップ" >&2
fi
LABEL="${AIOFFICE_LABEL:-com.senao.aioffice}"
if [ -z "${OFFICE_DATA:-}" ] && [ -f "${AIOFFICE_PLIST:-}" ] \
   && [ -n "${AIOFFICE_DEST_DEFAULT:-}" ] && [ -f "$AIOFFICE_DEST_DEFAULT/data/office_config.json" ]; then
  export OFFICE_DATA="$AIOFFICE_DEST_DEFAULT/data"
  echo "data: $OFFICE_DATA (P4常駐と共有)"
fi
daemon_guard() {
  if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    echo "⚠ 常駐(launchd)が登録中です。dev の起動/停止の前に外してください:"
    echo "   launchctl bootout gui/\$(id -u)/$LABEL"
    exit 1
  fi
}
case "${1:-status}" in
  start)
    daemon_guard
    if lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null 2>&1; then echo "既に起動中: http://localhost:$PORT"; exit 0; fi
    mkdir -p "$ROOT/logs"
    nohup python3 "$ROOT/server/office_server.py" --port $PORT >> "$LOG" 2>&1 &
    sleep 1
    lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null && echo "🏢 起動: http://localhost:$PORT (log: logs/office.log)" \
      || { echo "✗ 起動失敗 (tail: )"; tail -5 "$LOG"; exit 1; }
    ;;
  stop)
    daemon_guard
    # -sTCP:LISTEN: リスナーだけを殺す（UIを開いているブラウザ等の接続中クライアントを巻き込まない）
    lsof -ti tcp:$PORT -sTCP:LISTEN | xargs kill 2>/dev/null && echo "退勤しました" || echo "起動していません"
    ;;
  status)
    if lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
      curl -s -H "X-Office-Local: 1" "http://127.0.0.1:$PORT/api/office" | python3 -c \
        'import json,sys; d=json.load(sys.stdin); c=d["counts"]; print("🏢 稼働中: 出勤%d 作業中%d 待機%d" % (len(d["employees"]), c["working"], c["waiting"]))'
    else
      echo "停止中 (start で起動)"
    fi
    ;;
  log) tail -n "${2:-30}" "$LOG" ;;
  *) echo "usage: officectl.sh start|stop|status|log [n]"; exit 1 ;;
esac
