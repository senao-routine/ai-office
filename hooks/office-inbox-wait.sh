#!/bin/bash
# AIオフィス「指示ポスト」受信フック（グローバル Stop hook・asyncRewake）
#
# 仕組み: 各セッションのターン終了時に起動し、~/.claude/office_inbox/<session_id>.json を
# 最長2時間ポーリング。AIオフィス(localhost:4780)から指示が投函されたら、その内容を
# 出力して exit 2 → asyncRewake がセッションを起こし、指示がモデルに届く。
# - 同一セッションで新しいターンが終わると新インスタンスが立ち、古い方は自動退場（pidfile）
# - 失敗・タイムアウトは常に exit 0（セッションを邪魔しない）
set -u
IN=$(cat 2>/dev/null || true)
SID=$(printf '%s' "$IN" | /usr/bin/python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")' 2>/dev/null)
case "$SID" in *[!a-zA-Z0-9-]*|"") exit 0;; esac

DIR="${OFFICE_HOME:-$HOME}/.claude/office_inbox"
mkdir -p "$DIR"
MSGF="$DIR/$SID.json"
PIDF="$DIR/.$SID.pid"
echo $$ > "$PIDF"

LOOPS="${OFFICE_WAIT_LOOPS:-1440}"
INTERVAL="${OFFICE_WAIT_INTERVAL:-5}"
for _ in $(seq 1 "$LOOPS"); do   # 既定 5秒 × 1440 = 2時間（テストは環境変数で短縮）
  # 新しいポーラーに交代していたら退場
  [ "$(cat "$PIDF" 2>/dev/null)" = "$$" ] || exit 0
  if [ -f "$MSGF" ]; then
    # R79: ttl 切れは配達しない（閉じたセッション宛の指示が数時間後に突然実行されるのを防ぐ）。
    # 期限切れ・壊れたJSONは空文字を返し、下で rm されて静かに消える。
    TXT=$(/usr/bin/python3 -c 'import json,sys,time
try:
    d = json.load(open(sys.argv[1]))
    ttl = float(d.get("ttl") or 0)
    ts = float(d.get("ts") or 0)
    print("" if (ttl > 0 and ts > 0 and time.time() - ts > ttl) else d.get("text", ""))
except Exception:
    print("")' "$MSGF" 2>/dev/null)
    rm -f "$MSGF"
    if [ -n "$TXT" ]; then
      echo "📨 AIオフィス（ユーザー）からの指示: $TXT"
      exit 2
    fi
  fi
  sleep "$INTERVAL"
done
exit 0
