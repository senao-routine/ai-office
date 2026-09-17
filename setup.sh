#!/bin/bash
# AI Office セットアップ（1コマンド） — これを実行すれば「使える状態」まで到達する。
#
#   bash setup.sh              # 配線 → 常駐登録 → 起動 → 疎通確認 → 画面を開く
#   bash setup.sh --demo       # **何も入れずに**デモのオフィスを見るだけ（Ctrl-C で全部消える）
#   bash setup.sh --no-daemon  # 常駐にはせず、この場で起動して試すだけ
#   bash setup.sh --check      # 何もインストールせず、現在の状態だけ診断する
#   bash setup.sh --help       # 使い方だけ表示する
#
# 設計の意図（R80）:
#   配布して初めて分かったのは、詰まる場所が機能ではなく**手順**だということ。
#   従来は README の3行のあとに launchctl を最大5本手打ちさせていた（意味を知らない人は
#   ここで止まる）。このスクリプトは**最後まで実行して、動いていることを確かめて終わる**。
#   失敗したときは「何が駄目で、次に何をすればいいか」を必ず1行で言う。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-}"
PORT="${OFFICE_PORT:-4780}"
UID_NUM="$(id -u)"
ok=0; ng=0
say()  { printf '%s\n' "$*"; }
good() { printf '  ✅ %s\n' "$*"; ok=$((ok+1)); }
bad()  { printf '  ❌ %s\n' "$*"; ng=$((ng+1)); }
info() { printf '  ・ %s\n' "$*"; }

usage() {
  say ""
  say "🏢 AI Office セットアップ"
  say ""
  say "  bash setup.sh              配線 → 常駐登録 → 起動 → 画面を開く（通常はこれ）"
  say "  bash setup.sh --demo       何も入れずにデモのオフィスを見る（Ctrl-C で全部消える）"
  say "  bash setup.sh --no-daemon  常駐にはせず、この場で起動する"
  say "  bash setup.sh --check      何もインストールせず、いまの状態を診断する"
  say "  bash setup.sh --help       この説明"
  say ""
}
# R97-A: 未知の引数で**フルインストールが走っていた**（`--help` のつもりが常駐まで入る）。
case "$MODE" in
  ""|--demo|--no-daemon|--check) ;;
  -h|--help) usage; exit 0 ;;
  *) usage; say "  ❌ 知らない指定です: ${MODE}"; exit 2 ;;
esac

# ── 0. デモ（何も入れない・何も残さない） ─────────────────────────────────
# R97-C: 「まず見たい」人のための 60 秒経路。~/.claude も LaunchAgent も Cloudflare も触らない。
# 使うのは同梱の ui/demo/world.json だけで、実セッションは読まない（OFFICE_HOME を一時領域へ逃がす）。
if [ "$MODE" = "--demo" ]; then
  PY="$(command -v python3 || true)"
  [ -x "/usr/bin/python3" ] && PY="/usr/bin/python3"
  [ -n "$PY" ] || { say "  ❌ python3 が見つかりません（xcode-select --install）"; exit 1; }
  DEMO_HOME="$(mktemp -d /tmp/aioffice-demo.XXXXXX)"
  mkdir -p "$DEMO_HOME/.claude"
  DEMO_PID=""
  # 非対話 bash から起動した子は SIGINT を無視して生き残る＝Ctrl-C で親だけ消えて
  # サーバーがポートを掴んだまま残っていた（別モデルレビューで再現）。明示的に止める。
  demo_stop() {
    [ -n "$DEMO_PID" ] && kill "$DEMO_PID" 2>/dev/null
    [ -n "$DEMO_PID" ] && wait "$DEMO_PID" 2>/dev/null
    rm -rf "$DEMO_HOME"
    say ""; say "  デモを終了しました（この Mac には何も残していません）"
  }
  trap 'demo_stop; exit 0' INT TERM
  trap 'demo_stop' EXIT
  DEMO_PORT="$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
  say ""
  say "🎬 デモのオフィスを開きます（何も入れません・Ctrl-C で終了）"
  # HOME も一時領域へ逃がす: サーバーはセッション検出のため `claude` を呼ぶことがあり、
  # 子は OFFICE_HOME ではなく HOME を継ぐ＝**実セッションを読み、実の ~/.claude に触れてしまう**
  # （デモの約束は「何も入れない・何も見ない」・別モデルレビュー）。
  HOME="$DEMO_HOME" OFFICE_HOME="$DEMO_HOME" OFFICE_DATA="$DEMO_HOME" "$PY" "$HERE/server/office_server.py" \
    --port "$DEMO_PORT" >"$DEMO_HOME/server.log" 2>&1 &
  DEMO_PID=$!
  for _ in $(seq 1 20); do
    sleep 0.4
    curl -sf -o /dev/null -H "X-Office-Local: 1" "http://127.0.0.1:$DEMO_PORT/api/office" 2>/dev/null && break
  done
  if ! kill -0 "$DEMO_PID" 2>/dev/null; then
    say "  ❌ 起動できませんでした → $(tail -1 "$DEMO_HOME/server.log" 2>/dev/null)"
    exit 1
  fi
  URL="http://127.0.0.1:$DEMO_PORT/?demo=1"
  say "  ✅ ${URL}"
  say ""
  say "  自分のセッションを出勤させるときは、同じ場所で bash setup.sh を実行してください。"
  command -v open >/dev/null 2>&1 && open "$URL" 2>/dev/null || true
  wait "$DEMO_PID"
  exit 0
fi

say ""
say "🏢 AI Office セットアップ"
say "───────────────────────────────────────────────"

# ── 1. 前提の確認（ここで落ちる原因を先に潰す） ───────────────────────────
say "1. 環境を確認します"
if [ "$(uname)" != "Darwin" ]; then
  bad "macOS 専用です（このMacは $(uname)）"
fi
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  bad "python3 が見つかりません → Xcode Command Line Tools を入れてください: xcode-select --install"
else
  PYV="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "?")"
  good "python3 $PYV ($PY)"
  # 常駐（launchd）は Downloads/Desktop/Documents 配下の python3 を起動できない（macOSのTCC保護）
  case "$PY" in
    "$HOME/Downloads"/*|"$HOME/Desktop"/*|"$HOME/Documents"/*)
      bad "python3 が保護フォルダ配下にあり常駐に使えません（Homebrewのpython3を推奨）" ;;
  esac
fi
if [ -d "$HOME/.claude/projects" ]; then
  n="$(find "$HOME/.claude/projects" -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
  good "Claude Code のプロジェクト履歴を検出（${n} 件）"
else
  info "Claude Code の履歴がまだありません（初回はデモ画面 /?demo=1 で動きを確認できます）"
fi

if [ "$MODE" = "--check" ]; then
  say ""
  say "2. いまの状態"
  [ -f "$HOME/.claude/hooks/office-inbox-wait.sh" ] && good "指示配達フックは配布済み" \
    || bad "指示配達フックが未配布（bash setup.sh で配線されます）"
  grep -q "office-inbox-wait" "$HOME/.claude/settings.json" 2>/dev/null \
    && good "Stop hook が settings.json に配線済み" || bad "Stop hook が未配線"
  launchctl print "gui/$UID_NUM/com.senao.aioffice" >/dev/null 2>&1 \
    && good "常駐（オフィス本体）が登録済み" || info "常駐は未登録（この場で起動する運用も可）"
  if curl -sf -o /dev/null -H "X-Office-Local: 1" "http://127.0.0.1:$PORT/api/office"; then
    good "サーバー応答あり（http://localhost:${PORT}）"
  else
    info "サーバーは停止中"
  fi
  # R86-H: 承認・質問への回答は Stop hook とは別の口（PermissionRequest）。
  # ここが未配線だと「❗は出るのに答えても届かない」＝いちばん分かりにくい壊れ方をする。
  if python3 - "$HOME/.claude/settings.json" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
groups = (d.get("hooks") or {}).get("PermissionRequest") or []
hit = any("office-approval-wait" in (h.get("command") or "")
          for g in groups if isinstance(g, dict)
          for h in (g.get("hooks") or []) if isinstance(h, dict))
sys.exit(0 if hit else 1)
PYEOF
  then
    good "承認・質問への回答フックが配線済み（PermissionRequest）"
  else
    bad "承認フックが未配線（bash setup.sh で配線されます）"
  fi
  # R90-D5: イベント記録 hook（即時反映・留守中ダイジェストの材料。async なのでターンを止めない）
  grep -q "office-event.sh" "$HOME/.claude/settings.json" 2>/dev/null \
    && good "イベント記録 hook が配線済み（即時反映・ダイジェスト）" \
    || info "イベント記録 hook は未配線（bash setup.sh で配線されます）"
  [ -f "$HOME/.claude/office_relay.json" ] \
    && good "スマホ中継の設定あり（bash relay/setup.sh で作成済み）" \
    || info "スマホ連携は未設定（任意・bash relay/setup.sh で設定。自分のCloudflare無料枠で動きます）"
  say ""
  say "診断のみ実行しました（インストールはしていません）。"
  exit 0
fi
[ "$ng" -gt 0 ] && { say ""; say "❌ 前提が整っていません。上の ❌ を解消してからもう一度実行してください。"; exit 1; }

# ── 2. 指示配達の配線（この製品の心臓＝回答が実セッションへ届く経路） ─────────
say ""
say "2. 指示配達を配線します（回答があなたのセッションへ届くようにする）"
# 出力はそのまま見せる（「新しいセッションから有効」「statusline は別配線」が /tmp に消えていた）。
# ただし成否は**パイプの先頭**で見る（${PIPESTATUS[0]}）。素直に書くと最後の sed の終了コードになり、
# 配線に失敗しても「配線しました」と言ってしまう（別モデルレビューで再現）。
bash "$HERE/hooks/install.sh" --wire 2>&1 | tee /tmp/aioffice_hook.log | sed 's/^/    /'
HOOK_RC=${PIPESTATUS[0]}
if [ "$HOOK_RC" = "0" ]; then
  good "Stop hook を配線しました（~/.claude/settings.json・バックアップあり）"
else
  bad "配線に失敗しました → 詳細: /tmp/aioffice_hook.log"
fi

# ── 3. 常駐化（ログイン時に自動起動・再起動後も生き続ける） ────────────────
if [ "$MODE" = "--no-daemon" ]; then
  say ""
  say "3. 常駐にはしません（--no-daemon）。この場で起動します"
  ("$PY" "$HERE/server/office_server.py" --port "$PORT" >/tmp/aioffice_server.log 2>&1 &)
else
  say ""
  say "3. 常駐として登録します（ログイン時に自動起動）"
  if bash "$HERE/macapp/install.sh" >/tmp/aioffice_install.log 2>&1; then
    good "アプリを配置しました（~/Library/Application Support/AIOffice）"
  else
    bad "配置に失敗しました → 詳細: /tmp/aioffice_install.log"
  fi
  # ★従来はここから先を README で手打ちさせていた（launchctl を最大5本）。
  #   意味を知らない人が止まる場所なので、このスクリプトが最後まで実行する。
  PLIST="$HOME/Library/LaunchAgents/com.senao.aioffice.plist"
  if [ -f "$PLIST" ]; then
    launchctl enable "gui/$UID_NUM/com.senao.aioffice" 2>/dev/null || true
    launchctl bootout "gui/$UID_NUM/com.senao.aioffice" 2>/dev/null || true   # 再実行の冪等性
    if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/tmp/aioffice_launchctl.log; then
      launchctl kickstart -k "gui/$UID_NUM/com.senao.aioffice" 2>/dev/null || true
      good "常駐を登録して起動しました"
    else
      bad "常駐の登録に失敗 → 詳細: /tmp/aioffice_launchctl.log"
    fi
  else
    bad "LaunchAgent が生成されていません → /tmp/aioffice_install.log を確認"
  fi
fi

# ── 4. 疎通確認（「入れた」ではなく「動いている」ことを確かめて終わる） ──────
say ""
say "4. 動作を確認します"
up=""
for _ in $(seq 1 20); do
  if curl -sf -o /dev/null -H "X-Office-Local: 1" "http://127.0.0.1:$PORT/api/office"; then up=1; break; fi
  sleep 0.5
done
if [ -n "$up" ]; then
  good "サーバーが応答しました（http://localhost:${PORT}）"
else
  bad "サーバーが応答しません → ログ: \"$HOME/Library/Application Support/AIOffice/logs/office.daemon.log\"（常駐時）／/tmp/aioffice_server.log（--no-daemon 時）"
fi

say ""
say "───────────────────────────────────────────────"
if [ "$ng" -eq 0 ]; then
  say "✅ セットアップ完了（$ok 項目）"
  say ""
  say "  オフィスを開く:   open http://localhost:$PORT"
  say "  誰も居ないときは: open 'http://localhost:$PORT/?demo=1'  ← デモ"
  say ""
  say "  外出先からスマホで見る/答える場合（任意）:"
  say "     bash relay/setup.sh    ← 自分のCloudflare無料枠に中継を置きます（1コマンド）"
  command -v open >/dev/null 2>&1 && open "http://localhost:$PORT" 2>/dev/null || true
  # R90-U4: optional first question; Enter/EOF skips, and failure never fails setup.
  say ""
  say "  いま1体出勤させて❗を体験しますか？ [y/N]（Enter はスキップ）"
  FIRST_SESSION=""
  read -r FIRST_SESSION || FIRST_SESSION=""
  case "$FIRST_SESSION" in
    y|Y|yes|YES|Yes)
      if ! command -v claude >/dev/null 2>&1; then
        info "claude コマンドが見つかりません（Claude Code を入れてから試してください）。セットアップは完了しています。"
      elif (cd "$HERE" && claude --bg "README を1行で要約し、AskUserQuestion で続けるか聞いて"); then
        info "起動しました。オフィスで❗が出るのをお待ちください。"
      else
        info "体験セッションは起動できませんでした。セットアップは完了しています。"
      fi
      ;;
  esac
  exit 0
fi
say "⚠️ $ng 件が未完了です。上の ❌ の行を解消してから、もう一度 bash setup.sh を実行してください。"
say "   状態だけ見たいときは: bash setup.sh --check"
exit 1
