#!/bin/bash
# AI Office セットアップ（1コマンド） — これを実行すれば「使える状態」まで到達する。
#
#   bash setup.sh              # 配線 → 常駐登録 → 起動 → 疎通確認 → 画面を開く
#   bash setup.sh --no-daemon  # 常駐にはせず、この場で起動して試すだけ
#   bash setup.sh --check      # 何もインストールせず、現在の状態だけ診断する
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
if bash "$HERE/hooks/install.sh" --wire >/tmp/aioffice_hook.log 2>&1; then
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
  bad "サーバーが応答しません → ログ: /tmp/aioffice_server.log または officectl.sh log"
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
  exit 0
fi
say "⚠️ $ng 件が未完了です。上の ❌ の行を解消してから、もう一度 bash setup.sh を実行してください。"
say "   状態だけ見たいときは: bash setup.sh --check"
exit 1
