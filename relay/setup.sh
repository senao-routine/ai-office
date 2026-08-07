#!/bin/bash
# スマホ連携（中継）のセットアップ（1コマンド）— あなた自身の Cloudflare アカウントに
# 小さな中継を1つ立てて、Mac と iPhone を繋ぐ。ここを通ればスマホから承認・遠隔実行ができる。
#
#   bash relay/setup.sh          # 一気通貫（ログイン確認→鍵生成→deploy→設定書込→疎通確認）
#   bash relay/setup.sh --check  # いまの状態だけ診断（何も変更しない）
#
# 設計の意図（R80）:
#   従来は README の3行の裏に**実際は13手順**が隠れていた（トークン生成・secret put・
#   VAPID鍵・設定ファイルの手書き・常駐登録）。特に VAPID を設定しないと🔔を押しても
#   **通知は絶対に来ない**のに、それがどこにも書かれていなかった。ここで全部やる。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CONF="$HOME/.claude/office_relay.json"
UID_NUM="$(id -u)"
ok=0; ng=0
say()  { printf '%s\n' "$*"; }
good() { printf '  ✅ %s\n' "$*"; ok=$((ok+1)); }
bad()  { printf '  ❌ %s\n' "$*"; ng=$((ng+1)); }
info() { printf '  ・ %s\n' "$*"; }

say ""
say "📱 スマホ連携（中継）のセットアップ"
say "───────────────────────────────────────────────"

if [ "${1:-}" = "--check" ]; then
  say "いまの状態"
  command -v node >/dev/null 2>&1 && good "Node.js $(node -v)" || bad "Node.js が未導入（https://nodejs.org）"
  if [ -f "$CONF" ]; then
    U="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("url",""))' "$CONF" 2>/dev/null || echo "")"
    good "中継の設定あり: ${U:-（url未設定）}"
    if [ -n "$U" ]; then
      T="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("token",""))' "$CONF")"
      code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $T" \
        -H 'User-Agent: aioffice-setup' "$U/status" || echo 000)"
      [ "$code" = "200" ] && good "中継に到達できる（/status 200）" \
        || bad "中継に到達できない（HTTP ${code}）→ bash relay/setup.sh で作り直せます"
    fi
  else
    info "中継は未設定（bash relay/setup.sh で作成します）"
  fi
  launchctl print "gui/$UID_NUM/com.senao.aioffice.relay" >/dev/null 2>&1 \
    && good "配達エージェントが常駐登録済み" || info "配達エージェントは未登録"
  say ""
  exit 0
fi

# ── 1. 前提 ────────────────────────────────────────────────────────────
say "1. 環境を確認します"
if ! command -v node >/dev/null 2>&1; then
  bad "Node.js が必要です（Cloudflare の wrangler を動かすため）→ https://nodejs.org からインストール"
  say ""; say "❌ Node.js を入れてから、もう一度実行してください。"; exit 1
fi
good "Node.js $(node -v)"
if [ ! -d "$HERE/node_modules" ]; then
  say "  ・ wrangler を取得します（初回のみ・1〜2分）"
  (cd "$HERE" && npm install --silent) >/tmp/aioffice_relay_npm.log 2>&1 \
    && good "wrangler を取得しました" \
    || { bad "npm install に失敗 → /tmp/aioffice_relay_npm.log"; exit 1; }
else
  good "wrangler は取得済み"
fi

# ── 2. Cloudflare ログイン ────────────────────────────────────────────
say ""
say "2. Cloudflare にサインインします（無料アカウントでOK）"
if (cd "$HERE" && npx --no-install wrangler whoami >/tmp/aioffice_whoami.log 2>&1); then
  who="$(grep -oE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+' /tmp/aioffice_whoami.log | head -1 || true)"
  good "サインイン済み${who:+（${who}）}"
else
  say "  ・ ブラウザが開きます。許可すると戻ってきます"
  if (cd "$HERE" && npx --no-install wrangler login); then
    good "サインインしました"
  else
    bad "サインインに失敗しました"; exit 1
  fi
fi

# ── 3. 秘密の生成（トークンと通知鍵）────────────────────────────────────
say ""
say "3. この中継だけで使う秘密を作ります"
TOKEN=""
[ -f "$CONF" ] && TOKEN="$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("token",""))
except Exception: print("")' "$CONF" 2>/dev/null || echo "")"
if [ -n "$TOKEN" ]; then
  good "既存のトークンを再利用します（ペアリング済みのiPhoneがそのまま使えます）"
else
  TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  good "新しいトークンを生成しました"
fi
printf '%s' "$TOKEN" | (cd "$HERE" && npx --no-install wrangler secret put RELAY_TOKEN) \
  >/tmp/aioffice_secret.log 2>&1 && good "トークンを中継へ登録しました" \
  || { bad "トークン登録に失敗 → /tmp/aioffice_secret.log"; exit 1; }

# VAPID: これが無いと🔔を押しても**通知は絶対に来ない**（旧READMEが触れていなかった最大の穴）
if (cd "$HERE" && npx --no-install wrangler secret list 2>/dev/null | grep -q VAPID_JWK); then
  good "通知鍵（VAPID）は登録済み"
else
  VAPID="$(node -e 'const c=require("crypto");const {privateKey}=c.generateKeyPairSync("ec",{namedCurve:"P-256"});console.log(JSON.stringify(privateKey.export({format:"jwk"})))')"
  printf '%s' "$VAPID" | (cd "$HERE" && npx --no-install wrangler secret put VAPID_JWK) \
    >/tmp/aioffice_vapid.log 2>&1 && good "通知鍵（VAPID）を登録しました＝iPhoneへ通知が届きます" \
    || bad "通知鍵の登録に失敗 → /tmp/aioffice_vapid.log（通知以外は使えます）"
fi

# ── 4. デプロイ ────────────────────────────────────────────────────────
say ""
say "4. 中継をあなたのCloudflareへ配置します"
DEPLOY_LOG=/tmp/aioffice_deploy.log
if bash "$HERE/deploy.sh" >"$DEPLOY_LOG" 2>&1; then
  URL="$(grep -oE 'https://[a-z0-9.-]+\.workers\.dev' "$DEPLOY_LOG" | tail -1 || true)"
  if [ -n "$URL" ]; then good "配置しました: $URL"; else bad "URLを取得できません → $DEPLOY_LOG"; fi
else
  bad "配置に失敗 → $DEPLOY_LOG"; exit 1
fi

# ── 5. Mac側の設定（手書きさせない）────────────────────────────────────
say ""
say "5. Mac側の設定を書きます"
python3 - "$CONF" "$URL" "$TOKEN" <<'PYEOF'
import json, os, sys
from pathlib import Path
conf, url, token = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
data = {}
if conf.exists():
    try:
        data = json.loads(conf.read_text(encoding="utf-8"))
    except Exception:
        data = {}
data.update({"url": url, "token": token})     # 既存のocDeviceId等は温存する
conf.parent.mkdir(parents=True, exist_ok=True)
tmp = conf.with_name(conf.name + ".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
os.chmod(tmp, 0o600)
tmp.replace(conf)
PYEOF
[ -f "$CONF" ] && good "設定を書きました（${CONF}・600）" || bad "設定の書き込みに失敗"

# 配達エージェントの常駐（ここも従来は手打ちだった）
PLIST="$HOME/Library/LaunchAgents/com.senao.aioffice.relay.plist"
if [ -f "$PLIST" ]; then
  launchctl enable "gui/$UID_NUM/com.senao.aioffice.relay" 2>/dev/null || true
  launchctl bootout "gui/$UID_NUM/com.senao.aioffice.relay" 2>/dev/null || true
  if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/tmp/aioffice_relay_launchctl.log; then
    launchctl kickstart -k "gui/$UID_NUM/com.senao.aioffice.relay" 2>/dev/null || true
    good "配達エージェントを常駐にしました"
  else
    bad "常駐の登録に失敗 → /tmp/aioffice_relay_launchctl.log"
  fi
else
  info "配達エージェントのLaunchAgentが未生成（先に bash setup.sh を実行してください）"
fi

# ── 6. 疎通確認 ────────────────────────────────────────────────────────
say ""
say "6. 通信を確認します"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" \
  -H 'User-Agent: aioffice-setup' "$URL/status" || echo 000)"
[ "$code" = "200" ] && good "中継に到達できました（/status 200）" \
  || bad "中継に到達できません（HTTP ${code}）"

say ""
say "───────────────────────────────────────────────"
if [ "$ng" -eq 0 ]; then
  say "✅ 中継の準備ができました（$ok 項目）"
  say ""
  say "  次の3ステップでiPhoneと繋がります:"
  say "   1. オフィス画面の「📱 スマホ連携」でデバイスを発行"
  say "   2. 表示されたリンクをiPhoneで開く（QRを読むかAirDrop）"
  say "   3. iPhoneで「ホーム画面に追加」→ 開いて 🔔 をタップ（通知が有効になります）"
  say ""
  say "  ※ 📱スマホ連携は有料機能です。ライセンス未登録なら 🧾 から登録してください。"
  exit 0
fi
say "⚠️ $ng 件が未完了です。上の ❌ を解消してから、もう一度実行してください。"
say "   状態だけ見る: bash relay/setup.sh --check"
exit 1
