#!/bin/bash
# AIオフィス「指示ポスト」受信フック（グローバル Stop hook・asyncRewake）
#
# 仕組み: 各セッションのターン終了時に起動し、~/.claude/office_inbox/<session_id>.json を
# 最長12時間ポーリング。AIオフィス(localhost:4780)から指示が投函されたら、その内容を
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

# R86-D: 既定 5秒 × 8640 = **12時間**（旧 1440=2時間）。実測で「離席→次に指示を出すまで」の
# 間隔は2時間窓では79%しかカバーできず、12時間で92.5%（24時間でも+4%で割に合わない）。
# ★不変条件: settings.json の hook timeout > LOOPS × INTERVAL（verify ▶6 が機械ピン）。
#   timeout に達した hook は Claude Code が**出力を破棄して kill** する（公式docs）ので、
#   下の「読む→出力→消す」の途中で殺されると指示が消える。ループが先に尽きる形を保つ。
LOOPS="${OFFICE_WAIT_LOOPS:-8640}"
INTERVAL="${OFFICE_WAIT_INTERVAL:-5}"
for _ in $(seq 1 "$LOOPS"); do
  # 新しいポーラーに交代していたら退場
  [ "$(cat "$PIDF" 2>/dev/null)" = "$$" ] || exit 0
  # R86-D 心拍: pidfile の mtime を毎周更新する（中身の $$ は変えない＝上の世代交代判定は不変）。
  # オフィス側はこの鮮度だけで「受信待機中か」を判定する。pid の生存確認は**PID再利用で嘘をつく**
  # （実測: 素朴な os.kill 判定で7件中4件が Google Drive や login の pid を「待機中」と誤報）。
  touch "$PIDF" 2>/dev/null || true
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
    if [ -n "$TXT" ]; then
      # ★消すのは出力の**後**。逆順（消してから出力）だと、その隙間で kill されたときに
      #   指示が恒久ロストする。この順なら kill されても出力が捨てられるだけでファイルは残り、
      #   次の周（または次のターン）で再配達される＝at-least-once を保つ。
      echo "📨 AIオフィス（ユーザー）からの指示: $TXT"
      rm -f "$MSGF"
      exit 2
    fi
    rm -f "$MSGF"        # 期限切れ・空・壊れた投函は捨てる（無限ループにしない）
  fi
  sleep "$INTERVAL"
done
exit 0
