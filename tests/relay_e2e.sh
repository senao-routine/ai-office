#!/bin/bash
# AI Office 中継の E2E（P3・署名版・回すループの正本）:
#   wrangler dev(miniflare) 起動 → curlスマホ役が署名封筒を /instruct 投函 →
#   relay_agent.py --once が /pull → HMAC検証 → 集約配達 → ack。
#   併せて: JS/Python署名パリティKAT・無署名400・改竄reject・リプレイ非再配達 を検証。
# 使い方: bash "AI Office/tests/relay_e2e.sh"   （wrangler 無ければ省略して exit 0）
set -u
cd "$(dirname "$0")/.."   # AI Office/
NG=0
ng(){ echo "  ✗ $1"; NG=$((NG+1)); }
ok(){ echo "  ✓ $1"; }
PORT=""   # wrangler dev を起こす直前に空いているポートを選ぶ（下の「ポートを選ぶ」）
TOKEN="e2e-relay-token"
DID="d_0123456789ab"
SECRET="00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

# R97-B: この塊は wrangler も node_modules も要らない＝**既定の verify で毎回走る**（verify.sh ▶2d）。
# ここは単体実行（bash tests/relay_e2e.sh）のための保険で、verify.sh から来た回は二重実行しない。
if [ "${KAT_ALREADY:-}" = "1" ]; then
  echo "  - JS の KAT とパリティは verify.sh ▶2d で実行済み"
else
  # JS↔Python 署名パリティ＋APP_HTML canonical 検査（node のみ・node_modules不要＝クローン直後でも走る）
  if command -v node >/dev/null 2>&1; then
    node tests/js_sign_kat.mjs && ok "JS署名KAT一致 (canonical相互運用)" || ng "JS署名KAT不一致"
    # R65: PWAへ移植した gloss が正本 ui/core/world.js と同一出力（片方だけ直すと落ちる）
    node tests/gloss_parity.mjs >/dev/null 2>&1 && ok "R65 gloss parity (PWA↔core 同一出力)" || ng "R65 gloss parity 不一致 (node tests/gloss_parity.mjs で詳細)"
    # R80-A11: ❗の「最優先の1件」がMacとスマホで一致すること（順序の正本を2つ持たない）
    node tests/triage_parity.mjs >/dev/null 2>&1 && ok "R80 ❗順序パリティ (PWA↔core 同一の先頭)" || ng "R80 ❗順序パリティ不一致 (node tests/triage_parity.mjs で詳細)"
    # R86-G: 「届かない」の判定を Mac/スマホで同一に保つ（片方だけ直すと❗中に嘘をつく）
    node tests/ismute_parity.mjs >/dev/null 2>&1 && ok "R86-G isMute パリティ (PWA↔core 同一)" || ng "R86-G isMute パリティ不一致 (node tests/ismute_parity.mjs で詳細)"
    node tests/badge_parity.mjs >/dev/null 2>&1 && ok "R86-I 識別バッジ パリティ (PWA↔core 同一・常に一意)" || ng "R86-I バッジ パリティ不一致 (node tests/badge_parity.mjs で詳細)"
    # P7: Web Push暗号KAT（RFC8291 Appendix A公式ベクタ＋VAPID自己検証・wrangler不要）
    node tests/webpush_kat.mjs >/dev/null 2>&1 && ok "Web Push KAT (RFC8291ベクタ+VAPID)" || ng "Web Push KAT失敗 (node tests/webpush_kat.mjs で詳細)"
    # R5: Cloudflare依存を読み込まず、worker.js から純関数だけを抽出して購読フィルタを固定。
    node -e 'const fs=require("fs"),a=require("assert");const s=fs.readFileSync("relay/src/worker.js","utf8"),i=s.indexOf("function pushTargets("),j=s.indexOf("// R5_PUSH_TARGETS_END",i);if(i<0||j<0)throw Error("pushTargets not found");eval(s.slice(i,j));const row=(d)=>({v:JSON.stringify(d)});a.strictEqual(pushTargets([row({depts:[]})],"開発").length,1);a.strictEqual(pushTargets([row({depts:["開発"]})],"開発").length,1);a.strictEqual(pushTargets([row({depts:["営業"]})],"開発").length,0);a.strictEqual(pushTargets([row({endpoint:"https://legacy"})],"開発").length,1)' \
      && ok "R5 pushTargets 空/一致/不一致/レガシー" || ng "R5 pushTargets フィルタ判定失敗"
  else
    echo "  - node 無し → JS署名KAT省略"
  fi
fi

if [ ! -d relay/node_modules ]; then
  echo "  - relay/node_modules 無し（cd relay && npm install）→ wrangler E2E省略（KATは実行済み）"
  [ $NG -eq 0 ] && exit 0 || { echo "❌ relay 事前検査 ${NG}件失敗"; exit 1; }
fi

# 署名封筒を office_server.sign_envelope で生成（正本と同一計算）
sign_env(){   # 引数: session text ts nonce
  python3 - "$DID" "$SECRET" "$1" "$2" "$3" "$4" <<'EOF'
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("o", "server/office_server.py")
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
did, sec, sess, text, ts, nonce = sys.argv[1:7]
print(json.dumps(o.sign_envelope(sec, did, sess, text, int(ts), nonce)))
EOF
}

# ポートを選ぶ（2026-10-25）: 8789 固定のころ、別のツール（works のコスト管理ダッシュボード）が 8789 を使っていて wrangler dev が
# 起動せず、後片付けの「ポートを LISTEN しているプロセスを落とす」がそのツールまで止めた（launchd が起こし直した）。
# → 始める時点で空いているポートだけを使う＝後片付けで落とす相手は自分が起こした wrangler/workerd だけ。固定したいときは RELAY_E2E_PORT。
port_busy(){ lsof -ti tcp:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
for P in ${RELAY_E2E_PORT:-8789 8790 8791 8792 8793 8794 8795 8796 8797 8798}; do
  port_busy "$P" || { PORT=$P; break; }
done
if [ -z "$PORT" ]; then
  ng "中継E2E: 空いているポートが無い（${RELAY_E2E_PORT:-8789〜8798} が使用中・何も止めていません）"; echo "❌ relay E2E 中断"; exit 1
fi
if [ -z "${RELAY_E2E_PORT:-}" ] && [ "$PORT" != 8789 ]; then
  echo "  - 8789 は使用中（$(lsof -nP -iTCP:8789 -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $1}')）→ ${PORT} で起動（その相手は止めない）"
fi

POST_TOKEN="e2e-post-token"
MACMINI_TOKEN="e2e-macmini-token"
# P7: e2e用の使い捨てVAPID鍵（本番は wrangler secret put VAPID_JWK）
VAPID_JWK=$(node -e 'const c=require("crypto");const {privateKey}=c.generateKeyPairSync("ec",{namedCurve:"P-256"});console.log(JSON.stringify(privateKey.export({format:"jwk"})))')
# 開発者の既存 .dev.vars を壊さない（退避→cleanupで復元）
[ -f relay/.dev.vars ] && mv relay/.dev.vars relay/.dev.vars.e2e-bak
printf 'RELAY_TOKEN=%s\nRELAY_POST_TOKEN=%s\nRELAY_MACMINI_TOKEN=%s\nVAPID_JWK=%s\n' "$TOKEN" "$POST_TOKEN" "$MACMINI_TOKEN" "$VAPID_JWK" > relay/.dev.vars
( cd relay && npx wrangler dev --port $PORT --ip 127.0.0.1 >/tmp/relay_e2e_dev.log 2>&1 ) &
WPID=$!
VHOME=$(mktemp -d)
# R85-2: 旧ライセンスゲート（テスト鍵で解錠）は R84 全機能無料化で撤去済み＝素で通る
# wrangler dev は子に workerd を spawn する。親subshellをkillしても workerd が :PORT に残り
# 次回起動が古いコードの残骸を叩く（＝嘘green/嘘fail）。ポート専有プロセスも明示的に落とす。
cleanup(){ kill ${WPID:-} 2>/dev/null; wait ${WPID:-} 2>/dev/null;
           # R116-S0 の節が途中で落ちても、常駐の relay_agent・承認フック・スマホ役を残さない（一時 HOME も消す）
           for P in ${R116_PIDS:-}; do pkill -P "$P" 2>/dev/null; kill "$P" 2>/dev/null; done
           [ -n "${R116_HOME:-}" ] && rm -rf "$R116_HOME";
           # wrangler dev の子孫(npm/node/workerd 2階層)は別プロセス群で残るのでポート名で確実に落とす
           pkill -f "wrangler dev --port $PORT" 2>/dev/null;
           pkill -f "workerd.*:$PORT" 2>/dev/null;
           # LISTEN 限定＝接続中の無関係プロセスを巻き込まない。$PORT は始める時点で空いていた（上の「ポートを選ぶ」）＝いま LISTEN しているのは自分の workerd
           lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null;
           rm -f relay/.dev.vars; [ -f relay/.dev.vars.e2e-bak ] && mv relay/.dev.vars.e2e-bak relay/.dev.vars;
           rm -rf "${VHOME:-}"; }
trap cleanup EXIT

# VHOME にデバイス台帳を仕込む（relay_agent の verify_envelope が引く）
mkdir -p "$VHOME/.claude/office_inbox"
python3 - "$VHOME" "$DID" "$SECRET" <<'EOF'
import json, os, sys, time
from pathlib import Path
vh, did, sec = sys.argv[1:4]
p = Path(vh) / ".claude" / "office_devices.json"
now = int(time.time())
p.write_text(json.dumps({"version": 1, "devices": {did: {
    "secret": sec, "label": "e2e", "created": now, "expires": now + 86400,
    "revoked": False, "last_used": 0}}}))
os.chmod(p, 0o600)
EOF

# 起動待ち（最大60秒）
UP=0
for i in $(seq 1 60); do
  curl -s "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q "ok" && { UP=1; break; }
  sleep 1
done
[ "$UP" = "1" ] && ok "wrangler dev 起動" || { ng "wrangler dev 起動せず (tail: $(tail -3 /tmp/relay_e2e_dev.log | tr '\n' ' '))"; echo "❌ relay E2E 中断"; exit 1; }

B="http://127.0.0.1:$PORT"
NOW=$(date +%s)

# PWAアプリシェルは無認証で配信される（GET /app 200・HTMLマーカー）
# R99-P4-7: シェルの URL は /app/。/app（古いリンク・QR）は 308 で /app/ へ寄る＝-L で追って同じシェルに着くことを見る
curl -sL "$B/app" | grep -q "AI Office" && ok "PWA /app 無認証配信 (200・/app/ へ 308 を追って)" || ng "PWA /app 配信異常"
curl -s "$B/app/manifest.webmanifest" | grep -q "standalone" && ok "PWA manifest 配信" || ng "PWA manifest 異常"
# R99-P4-7: ホーム画面のアプリとして名乗れる（start_url・scope が SW の範囲・id は start_url と同じオリジン・PNG アイコンの寸法・apple-touch-icon・名前 1 つ・色 1 つ）
python3 tests/pwa_install_smoke.py "$B" || ng "R99-P4-7 PWA のインストールの形（python3 tests/pwa_install_smoke.py で詳細）"

# 無署名 {session,text} → Worker が 400（中継経路は署名必須）
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"session":"relaytest-0001","text":"x"}')
[ "$CODE" = "400" ] && ok "無署名投函拒否 (400)" || ng "無署名拒否失敗 ($CODE)"

# 署名済みでも Bearer 無し → 401（輸送ゲート）
ENV_A=$(sign_env "relaytest-0001" "署名指示アルファ" "$NOW" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/instruct" \
  -H "Content-Type: application/json" -d "$ENV_A")
[ "$CODE" = "401" ] && ok "トークン無し拒否 (401)" || ng "認証拒否失敗 ($CODE)"

# 不正session → 400（Worker regex）
BADSESS=$(sign_env "../evil" "x" "$NOW" "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee0")
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$BADSESS")
[ "$CODE" = "400" ] && ok "不正session拒否 (400)" || ng "不正session拒否失敗 ($CODE)"

# スマホ役: 同一session2件（distinct nonce）を署名投函
ENV_B=$(sign_env "relaytest-0001" "署名指示ベータ" "$NOW" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$ENV_A" >/dev/null
R=$(curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$ENV_B")
echo "$R" | grep -q '"ok":true' && ok "スマホ→中継 署名投函(同一session2件)" || ng "投函失敗: $R"

# ack前: peek は消さない（2回叩いても同じ＝リース方式）
P1=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
P2=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
[ "$P1" = "$P2" ] && echo "$P1" | grep -q '署名指示アルファ' \
  && ok "peek方式 (ack前は/pullで消えない)" || ng "peekで消えている: $P1"

# Mac役: relay_agent が pull → HMAC検証 → 集約配達 → ack
OUT=$(OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once 2>&1)
F="$VHOME/.claude/office_inbox/relaytest-0001.json"
if [ -f "$F" ] && grep -q "署名指示アルファ" "$F" && grep -q "署名指示ベータ" "$F"; then
  ok "中継→Mac 署名検証＋集約配達 (2件が両方1通に)"
else
  ng "署名配達失敗（未生成 or 集約されず）: $OUT / $( [ -f "$F" ] && cat "$F")"
fi

# ack 済み → 2周目 pull は0件
R=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
echo "$R" | grep -q '"items":\[\]' && ok "ack確認 (配達後は空)" || ng "ack効かず残留: $R"

# 改竄: 署名後に本文を差し替えた封筒 → relay_agent が bad-sig で拒否（office_inbox未生成）
TENV=$(sign_env "relaytest-0002" "正しい本文" "$NOW" "cccccccccccccccccccccccccccccccc")
TENV2=$(echo "$TENV" | python3 -c 'import sys,json; e=json.load(sys.stdin); e["text"]="改竄後の悪意ある指示"; print(json.dumps(e))')
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$TENV2" >/dev/null
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once >/dev/null 2>&1
[ ! -f "$VHOME/.claude/office_inbox/relaytest-0002.json" ] && ok "改竄封筒→配達拒否 (bad-sig)" || ng "改竄が配達された"

# リプレイ: 配達済み nonce を再投函 → 既視で drop（再配達されない＝二重配達しない）
rm -f "$F"
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$ENV_A" >/dev/null
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once >/dev/null 2>&1
[ ! -f "$F" ] && ok "リプレイ(既視nonce)→再配達なし" || ng "リプレイが再配達された"

# Worker が署名対象を verbatim 転送する（trim すると sig 不一致で脱落）: 前後空白入り text
WENV=$(sign_env "relaytest-0003" " 前後に空白のある指示 " "$NOW" "dddddddddddddddddddddddddddddddd")
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$WENV" >/dev/null
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once >/dev/null 2>&1
[ -f "$VHOME/.claude/office_inbox/relaytest-0003.json" ] && ok "前後空白入りtext→配達成功 (Worker trim無し)" || ng "空白入りtextが trim で bad-sig 脱落"

# status: relay_agent が push 済み → スマホ役が取得できる
S=$(curl -s "$B/status" -H "Authorization: Bearer $TOKEN")
echo "$S" | grep -q '"ok":true' && ok "status中継 (Mac push→スマホ取得)" || ng "status中継失敗: $S"

# ── R50提案4: /sync（1周1リクエスト統合）のwire検査 ──────────────────
# GET /status に agentSeenAgo が同乗（relay_agent の生死をスマホが直接知る）
echo "$S" | grep -q '"agentSeenAgo"' && ok "R51 /status に agentSeenAgo 同乗" || ng "R51 agentSeenAgo 欠落: $(echo "$S" | head -c 120)"
# office=null の /sync は保存済みstatusを更新しない（変化時のみpushの土台）
TS1=$(curl -s "$B/status" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("ts"))')
sleep 1.1
R=$(curl -s -X POST "$B/sync" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"office":null,"ackIds":[],"wantOpenclaw":false}')
echo "$R" | grep -q '"ok":true' && ok "R51 POST /sync 応答" || ng "R51 /sync 失敗: $R"
TS2=$(curl -s "$B/status" -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("ts"))')
[ "$TS1" = "$TS2" ] && ok "R51 office=null は status.ts を進めない（変化時のみpush）" || ng "R51 nullでもts更新: ${TS1}→$TS2"
# /sync は輸送フルBearer専用（POST_TOKEN=OpenClaw限定トークンは403）
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/sync" -H "Authorization: Bearer $POST_TOKEN" \
  -H "Content-Type: application/json" -d '{"office":null,"ackIds":[]}')
[ "$CODE" = "403" ] && ok "R51 /sync はPOST_TOKENを403" || ng "R51 /sync 認可漏れ ($CODE)"
# sync経由の配達＝投函→relay_agent --once（sync経路）→inbox→キュー掃除（--once終了時ack flush）
SENV=$(sign_env "relaytest-0004" "sync経由の指示" "$(date +%s)" "abcdabcdabcdabcdabcdabcdabcdabcd")
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$SENV" >/dev/null
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once >/dev/null 2>&1
[ -f "$VHOME/.claude/office_inbox/relaytest-0004.json" ] && ok "R51 sync経路の配達" || ng "R51 sync配達失敗"
R=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
echo "$R" | grep -q '"items":\[\]' && ok "R51 --once 終了時ack flush（キュー掃除）" || ng "R51 ack持ち越しが残留: $R"

# P5: OpenClaw用 RELAY_POST_TOKEN=POST /instruct と GET /status のみ許可（/pull /ack POST/status は403）
PENV=$(sign_env "relaytest-0009" "OpenClaw経由" "$(date +%s)" "0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f")
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/instruct" -H "Authorization: Bearer $POST_TOKEN" \
  -H "Content-Type: application/json" -d "$PENV")
[ "$CODE" = "200" ] && ok "POSTトークン: /instruct 許可 (200)" || ng "POSTトークン /instruct 失敗 ($CODE)"
CG=$(curl -s -o /dev/null -w "%{http_code}" "$B/status" -H "Authorization: Bearer $POST_TOKEN")
[ "$CG" = "200" ] && ok "POSTトークン: GET /status 許可 (200)" || ng "POSTトークン /status 失敗 ($CG)"
for R in "GET /pull" "POST /ack" "POST /status"; do
  M=${R% *}; P=${R#* }
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X "$M" "$B$P" -H "Authorization: Bearer $POST_TOKEN" \
    -H "Content-Type: application/json" -d '{}')
  [ "$CODE" = "403" ] && ok "POSTトークン: $R 拒否 (403)" || ng "POSTトークン $R が403でない ($CODE)"
done

# ---- R42.4 site分割（mini→mac片方向集約・アグリゲータ方式） ----
CONTRACT=$(python3 -c 'import json,time;print(json.dumps({"v":1,"site":"macmini","generatedAt":time.time(),"agents":[{"id":"main","name":"OpenClaw","state":"working","verb":"replying on WhatsApp","age":5,"minions":1}]}))')
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/status?site=macmini" \
  -H "Authorization: Bearer $MACMINI_TOKEN" -H "Content-Type: application/json" -d "$CONTRACT")
[ "$CODE" = "200" ] && ok "R42.4 miniトークン: POST /status?site=macmini 許可 (200)" || ng "R42.4 mini status push失敗 ($CODE)"
for R in "POST /status" "POST /status?site=mac" "GET /status?site=macmini" "POST /instruct" "GET /pull"; do
  M=${R%% *}; P=${R#* }
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -X "$M" "$B$P" -H "Authorization: Bearer $MACMINI_TOKEN" \
    -H "Content-Type: application/json" -d '{}')
  [ "$CODE" = "403" ] && ok "R42.4 miniトークン: $R 拒否 (403)" || ng "R42.4 miniトークン $R が403でない ($CODE)"
done
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$B/status?site=BAD_SITE!" -H "Authorization: Bearer $TOKEN")
[ "$CODE" = "400" ] && ok "R42.4 不正site名は400" || ng "R42.4 不正siteが通った ($CODE)"
# site分離: mac側statusは契約に汚れず・macmini側は契約が読める
C=$(curl -s "$B/status" -H "Authorization: Bearer $TOKEN")
echo "$C" | python3 -c 'import sys,json;d=json.load(sys.stdin);s=json.loads(d["json"]);assert "agents" not in s, "mac statusに契約が混入"; print("  ✓ R42.4 site分離 (macは契約に汚れない)")' \
  || ng "R42.4 site分離失敗: $(echo "$C" | head -c 120)"
C=$(curl -s "$B/status?site=macmini" -H "Authorization: Bearer $TOKEN")
echo "$C" | python3 -c 'import sys,json;d=json.load(sys.stdin);s=json.loads(d["json"]);assert s.get("v")==1 and s.get("site")=="macmini", s; print("  ✓ R42.4 macmini側は契約v1が読める")' \
  || ng "R42.4 macmini status取得失敗: $(echo "$C" | head -c 120)"
# relay_agent集約: --once で契約が ~/.claude/openclaw_status.json へ保存される
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once >/dev/null 2>&1
python3 - "$VHOME" <<'EOF' && ok "R42.4 relay_agentが契約を集約保存" || ng "R42.4 集約保存失敗"
import json, sys
from pathlib import Path
d = json.loads((Path(sys.argv[1]) / ".claude" / "openclaw_status.json").read_text())
assert d.get("v") == 1 and d.get("site") == "macmini", d
EOF
# mini実機の代役: openclaw_push.py --input が契約を送れる
printf '{"url":"%s","token":"%s","site":"macmini","interval":15}' "$B" "$MACMINI_TOKEN" > "$VHOME/office_push.json"
printf '[{"id":"pushbot","name":"PushBot","state":"waiting","verb":"idle","age":3}]' > "$VHOME/oc_agents.json"
OFFICE_PUSH_CONFIG="$VHOME/office_push.json" python3 tools/openclaw_push.py --input "$VHOME/oc_agents.json" --once >/dev/null 2>&1 \
  && ok "R42.4 openclaw_push.py --once 送信成功" || ng "R42.4 openclaw_push送信失敗"
C=$(curl -s "$B/status?site=macmini" -H "Authorization: Bearer $TOKEN")
echo "$C" | python3 -c 'import sys,json;d=json.load(sys.stdin);s=json.loads(d["json"]);assert s["agents"][0]["id"]=="pushbot", s; print("  ✓ R42.4 push経由の契約が反映")' \
  || ng "R42.4 push反映失敗: $(echo "$C" | head -c 120)"

# ---- R42.5 双方向（oc-宛転送 → miniのpeek/ack） ----
# mac→mini方向の署名鍵（verify_envelope形式= d_[0-9a-f]{12} / 64hex）
OC_DID="d_$(python3 -c 'import secrets;print(secrets.token_hex(6))')"
OC_SEC="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
sign_oc(){ DID="$OC_DID" SECRET="$OC_SEC" sign_env "$@"; }
# (a) post_instruction の oc-分岐: outbox生成・孤児inboxを作らない
python3 - "$VHOME" <<'EOF'
import importlib.util, os, sys
os.environ["OFFICE_HOME"] = sys.argv[1]
spec = importlib.util.spec_from_file_location("o", "server/office_server.py")
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
ok, msg = o.post_instruction("oc-lobster-1", "R42.5 双方向テスト指示", src="local")
assert ok, msg
EOF
[ -n "$(ls "$VHOME/.claude/office_oc_outbox/"*.json 2>/dev/null)" ] \
  && ok "R42.5 oc-投函→OC_OUTBOX生成" || ng "R42.5 outboxが生えない"
[ ! -f "$VHOME/.claude/office_inbox/oc-lobster-1.json" ] \
  && ok "R42.5 oc-はoffice_inboxに書かない（孤児根絶）" || ng "R42.5 孤児inboxが生えた"
# (b) relay_agent --once が署名転送 → macminiキュー到達（macキューへは漏れない）
printf '{"url":"%s","token":"%s","ocDeviceId":"%s","ocSecret":"%s"}' \
  "$B" "$TOKEN" "$OC_DID" "$OC_SEC" > "$VHOME/.claude/office_relay.json"
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once >/dev/null 2>&1
R=$(curl -s "$B/pull?site=macmini" -H "Authorization: Bearer $TOKEN")
echo "$R" | grep -q "oc-lobster-1" && ok "R42.5 転送→macminiキュー到達" || ng "R42.5 転送されず: $(echo "$R" | head -c 120)"
[ -z "$(ls "$VHOME/.claude/office_oc_outbox/"*.json 2>/dev/null)" ] \
  && ok "R42.5 転送成功分はoutboxから削除" || ng "R42.5 outbox残留"
R=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
echo "$R" | grep -q "oc-lobster-1" && ng "R42.5 site分離破れ（macキューに漏出）" || ok "R42.5 macキューへは漏れない（site分離）"
# (c) miniトークン: /pull・/ack は site=macmini のみ許可（site無し403は上のマトリクスで維持）
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$B/pull?site=macmini" -H "Authorization: Bearer $MACMINI_TOKEN")
[ "$CODE" = "200" ] && ok "R42.5 miniトークン: GET /pull?site=macmini 許可 (200)" || ng "R42.5 mini pull不可 ($CODE)"
# (d) mini側 openclaw_agent --once: 検証→openclaw_inbox実ファイル→ackでキュー空
printf '{"url":"%s","token":"%s","ocDeviceId":"%s","ocSecret":"%s"}' \
  "$B" "$MACMINI_TOKEN" "$OC_DID" "$OC_SEC" > "$VHOME/.claude/office_push.macmini.json"
OFFICE_HOME="$VHOME" python3 tools/openclaw_agent.py --once >/dev/null 2>&1
F="$VHOME/.claude/openclaw_inbox/oc-lobster-1.json"
if [ -f "$F" ] && grep -q "双方向テスト指示" "$F"; then
  ok "R42.5 mini受信→openclaw_inbox実ファイル"
else
  ng "R42.5 mini配達失敗: $( [ -f "$F" ] && cat "$F" || echo 未生成)"
fi
R=$(curl -s "$B/pull?site=macmini" -H "Authorization: Bearer $MACMINI_TOKEN")
echo "$R" | grep -q '"items":\[\]' && ok "R42.5 ackでmacminiキュー掃除" || ng "R42.5 ack残留: $R"
# (e) リプレイ: 同一nonce封筒の再投函は配達されない（既視nonce・at-least-onceの安全網）
ENV_OC=$(sign_oc "oc-lobster-1" "リプレイ試行" "$(date +%s)" "abcdef0123456789abcdef0123456789")
curl -s -X POST "$B/instruct?site=macmini" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$ENV_OC" >/dev/null
OFFICE_HOME="$VHOME" python3 tools/openclaw_agent.py --once >/dev/null 2>&1
rm -f "$F"
curl -s -X POST "$B/instruct?site=macmini" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$ENV_OC" >/dev/null
OFFICE_HOME="$VHOME" python3 tools/openclaw_agent.py --once >/dev/null 2>&1
[ ! -f "$F" ] && ok "R42.5 リプレイ(既視nonce)→再配達なし" || ng "R42.5 リプレイが再配達された"

# ---- R99-P4-5: スマホから ❗ に答える（本物の承認フック × 中継 × relay_agent） ----
# 承認フック hooks/office-approval-wait.sh を一時 HOME で**本当に起動**して掲示を出させ、スマホ役が
# 署名封筒で /instruct へ答える → relay_agent --once（HTTP の /sync 経路）→ 回答ファイル → フックの標準出力。
# 守る性質（不変条件 7）: 質問（AskUserQuestion）は deny＋回答の文言そのもの＝モデルへの回答として届く／
# 許可（Bash）と計画の承認（ExitPlanMode）は deny＋言葉だけ＝**中継からは allow が絶対に出ない**。
# 受信箱には書かない（相手は止まっている＝Stop hook が起動せず届かない）。
# wrangler 無しの同じ鎖は test_relay_agent の test_r86h_chain_through_real_hook（既定の verify で毎回）。
ASK_DIR="$VHOME/.claude/office_approvals"
HOOK_PIDS=""
ASK_CASES="e2e-ask-q0001|AskUserQuestion|選択肢「B案」でお願いします。
e2e-ask-p0002|Bash|はい、承認します
e2e-ask-x0003|ExitPlanMode|はい、そのまま進めてください。"
while IFS='|' read -r S TOOL _TXT; do
  python3 - "$S" "$TOOL" > "$VHOME/hook_in_$S.json" <<'EOF'
import json, sys
s, tool = sys.argv[1:3]
ti = {"AskUserQuestion": {"questions": [{"question": "どちらで進めますか?", "header": "方式",
                                          "options": [{"label": "A案"}, {"label": "B案"}]}]},
      "Bash": {"command": "git push origin main", "description": "push"},
      "ExitPlanMode": {"plan": "手順を3つに分けます"}}[tool]
print(json.dumps({"session_id": s, "cwd": "/tmp", "hook_event_name": "PermissionRequest",
                  "permission_mode": "default", "tool_name": tool, "tool_input": ti,
                  "transcript_path": "/nonexistent/transcript.jsonl"}, ensure_ascii=False))
EOF
  OFFICE_HOME="$VHOME" OFFICE_APPROVAL_WAIT=60 OFFICE_APPROVAL_POLL=0.2 \
    bash hooks/office-approval-wait.sh < "$VHOME/hook_in_$S.json" > "$VHOME/hook_out_$S.json" 2>/dev/null &
  HOOK_PIDS="$HOOK_PIDS $!"
done <<< "$ASK_CASES"
ASK_UP=0
# R100-0: 掲示は 1 件ごとの名前 <session>.<askId>.json
ask_posted() { ls "$ASK_DIR"/"$1".*.json 2>/dev/null | grep -qv '\.reply\.'; }
for i in $(seq 1 60); do
  ask_posted e2e-ask-q0001 && ask_posted e2e-ask-p0002 && ask_posted e2e-ask-x0003 \
    && { ASK_UP=1; break; }
  sleep 0.25
done
[ "$ASK_UP" = "1" ] && ok "R99-P4-5 実フックが掲示を出した（AskUserQuestion・Bash・ExitPlanMode）" \
  || ng "R99-P4-5 フックの掲示が出ない: $(ls "$ASK_DIR" 2>/dev/null | tr '\n' ' ')"
# スマホ役: 3 件それぞれに署名封筒で答える（PWA の選択肢ボタン／承認ボタンが送る文言と同じ形）。
# R102-11: スマホは表示していた掲示の印（askId の先頭 8 桁）を添えて答える＝{"aioffice":1,"kind":"answer","ask","text"}
while IFS='|' read -r S _TOOL TXT; do
  ID8=$(ls "$ASK_DIR"/"$S".*.json 2>/dev/null | grep -v '\.reply\.' | head -1 | sed -E 's#.*/[^.]+\.([0-9a-f]{8})[0-9a-f]*\.json#\1#')
  ATXT=$(python3 -c 'import json,sys;print(json.dumps({"aioffice":1,"kind":"answer","ask":sys.argv[1],"text":sys.argv[2]},ensure_ascii=False,separators=(",",":")))' "$ID8" "$TXT")
  AENV=$(sign_env "$S" "$ATXT" "$(date +%s)" "$(python3 -c 'import secrets;print(secrets.token_hex(16))')")
  curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$AENV" >/dev/null
done <<< "$ASK_CASES"
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" python3 server/relay_agent.py --once > /tmp/relay_ask.log 2>&1
for i in $(seq 1 60); do
  ALIVE=0
  for P in $HOOK_PIDS; do kill -0 "$P" 2>/dev/null && ALIVE=1; done
  [ "$ALIVE" = "0" ] && break
  sleep 0.25
done
for P in $HOOK_PIDS; do                 # 残っていたら（＝回答が届かなかった）子の python ごと止める
  kill -0 "$P" 2>/dev/null && { pkill -P "$P" 2>/dev/null; kill "$P" 2>/dev/null; }
  wait "$P" 2>/dev/null
done
python3 - "$VHOME" <<'EOF' && ok "R99-P4-5 中継から ❗ に答える: 質問＝deny＋回答そのもの／許可・計画の承認＝deny＋言葉（allow は出ない・掲示は片付く）" \
  || ng "R99-P4-5 中継から ❗ に答える経路が壊れている（上の AssertionError・relay_agent: $(tail -3 /tmp/relay_ask.log 2>/dev/null | tr '\n' ' ')）"
import json, os, sys
from pathlib import Path
vh = Path(sys.argv[1])
def decision(s):
    raw = (vh / f"hook_out_{s}.json").read_text(encoding="utf-8").strip()
    assert raw, f"{s}: フックが無出力で終わった（回答が届かず素通し）"
    return json.loads(raw)["hookSpecificOutput"]["decision"]
q, p, x = decision("e2e-ask-q0001"), decision("e2e-ask-p0002"), decision("e2e-ask-x0003")
assert q == {"behavior": "deny", "message": "選択肢「B案」でお願いします。"}, q
assert p["behavior"] == "deny", f"スマホ経由で allow が出た（不変条件 7）: {p}"
assert p["message"].startswith("はい、承認します") and "実行されていません" in p["message"], p
assert x["behavior"] == "deny", f"スマホ経由で計画が承認された: {x}"
assert x["message"].startswith("はい、そのまま進めてください。"), x
assert "計画は承認されていません" in x["message"], f"計画の承認への答えに『承認されていない』事実が無い: {x}"
ad = vh / ".claude" / "office_approvals"
for s in ("e2e-ask-q0001", "e2e-ask-p0002", "e2e-ask-x0003"):
    assert not (vh / ".claude" / "office_inbox" / f"{s}.json").exists(), f"{s}: 受信箱にも置かれた"
    left = sorted(f.name for f in ad.glob(f"{s}.*"))
    assert not [n for n in left if ".reply." not in n], f"{s}: 掲示が残った（❗の幽霊）: {left}"
    assert not [n for n in left if ".reply." in n], f"{s}: 回答が読まれずに残った: {left}"
    assert not (ad / f"{s}.reply.json").exists(), f"{s}: 旧名に回答を置いた（新しい hook は読まない）"
EOF
[ "$(grep -c '承認まちへ回答を配達' /tmp/relay_ask.log 2>/dev/null)" = "3" ] \
  && ok "R99-P4-5 relay_agent が 3 件とも承認まちへ回した（受信箱へは回していない）" \
  || ng "R99-P4-5 relay_agent の回し先が違う: $(tail -4 /tmp/relay_ask.log 2>/dev/null | tr '\n' ' ')"
R=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
echo "$R" | grep -q '"items":\[\]' && ok "R99-P4-5 回答した封筒は ack 済み（キューに残らない）" || ng "R99-P4-5 回答の封筒が残った: $(echo "$R" | head -c 160)"
rm -f "$VHOME"/hook_in_e2e-ask-* "$VHOME"/hook_out_e2e-ask-* /tmp/relay_ask.log

# ---- P7 Web Push（購読の保存/削除・VAPID公開鍵・❗遷移トリガの生存） ----
V=$(curl -s "$B/push/vapid" -H "Authorization: Bearer $TOKEN")
echo "$V" | grep -qE '"key":"[A-Za-z0-9_-]{87}"' && ok "P7 /push/vapid 公開鍵 (65バイトb64u)" || ng "P7 vapid鍵異常: $V"
# 実P-256公開鍵＋16バイトauthの偽購読（endpointは到達不能→送信失敗しても購読が消えないことも検証）
SUBKEYS=$(node -e 'const c=require("crypto");const {publicKey}=c.generateKeyPairSync("ec",{namedCurve:"P-256"});const j=publicKey.export({format:"jwk"});const b=(s)=>Buffer.from(s,"base64url");const p=Buffer.concat([Buffer.from([4]),b(j.x),b(j.y)]).toString("base64url");console.log(JSON.stringify({p256dh:p,auth:c.randomBytes(16).toString("base64url")}))')
SUB=$(python3 -c "import json,sys;k=json.loads('$SUBKEYS');print(json.dumps({'subscription':{'endpoint':'https://127.0.0.1:9/e2e-push','keys':k},'depts':['E2E検証部']}))")
R=$(curl -s -X POST "$B/push/subscribe" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$SUB")
echo "$R" | grep -q '"ok":true' && ok "R5 depts付きsubscribe 保存" || ng "R5 depts付きsubscribe失敗: $R"
C=$(curl -s "$B/push/subs" -H "Authorization: Bearer $TOKEN")
python3 -c 'import json,sys;d=json.loads(sys.argv[1]);assert d["count"]==1 and d["subs"]==[{"depts":["E2E検証部"]}]' "$C" \
  && ok "R5 /push/subs にdepts保存" || ng "R5 depts台帳異常: $C"
# deptsが不正型でも購読本体は後方互換で成功し、フィルタ無し（全通知）へ正規化される。
BAD_DEPTS_SUB=$(python3 -c "import json,sys;k=json.loads('$SUBKEYS');print(json.dumps({'subscription':{'endpoint':'https://127.0.0.1:9/e2e-push','keys':k},'depts':'E2E検証部'}))")
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/push/subscribe" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$BAD_DEPTS_SUB")
[ "$CODE" = "200" ] && ok "R5 depts不正型は空扱いで購読成功 (200)" || ng "R5 depts不正型が購読失敗 ($CODE)"
C=$(curl -s "$B/push/subs" -H "Authorization: Bearer $TOKEN")
python3 -c 'import json,sys;d=json.loads(sys.argv[1]);assert d["count"]==1 and d["subs"]==[{"depts":[]}]' "$C" \
  && ok "R5 depts不正型を空配列へ正規化" || ng "R5 depts不正型の空扱い失敗: $C"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$B/push/subscribe" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"subscription":{"endpoint":"http://insecure/","keys":{"p256dh":"x","auth":"y"}}}')
[ "$CODE" = "400" ] && ok "P7 不正購読拒否 (400)" || ng "P7 不正購読が通った ($CODE)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$B/push/vapid" -H "Authorization: Bearer $POST_TOKEN")
[ "$CODE" = "403" ] && ok "P7 POSTトークンは /push/* 不可 (403)" || ng "P7 POSTトークンが /push/vapid に通った ($CODE)"
# ❗遷移トリガ: 承認まち社員入りstatusをpush→200＋（到達不能endpointへの送信失敗でも）購読が残る
P7_STATUS='{"office":{"officeName":"E2Eオフィス","tasks":{"pending":2,"inProgress":1,"completed":3},"res":{"fiveHour":21,"sevenDay":58,"staleSec":30,"providers":[{"id":"claude","label":"Claude","bars":[{"k":"5h","pct":21},{"k":"7d","pct":58}],"staleSec":30},{"id":"codex","label":"Codex","bars":[{"k":"7d","pct":7}]}]},"templates":[{"label":"ビルド確認","text":"verifyを回して結果を1行で報告して"}],"avatarMode":"session","employees":[{"session":"e2e-attn-0001","disp":"E2E検証部","state":"waiting","listening":false,"verb":"検証中","question":"どの案で進めますか?","questionOptions":[{"label":"案A (Recommended)","desc":"推奨案"},{"label":"案B","desc":"別案"},{"label":"案C","desc":"保留案"}]},{"session":"e2e-busy-0002","disp":"E2E稼働部","state":"working","listening":false,"verb":"実行中"},{"session":"e2e-perm-0003","disp":"E2E許可部","state":"waiting","listening":false,"verb":"実行中","approvalMin":4,"ask":{"tool":"Bash","kind":"permission"}},{"session":"oc-e2e","disp":"OpenClaw","dept":"OpenClaw","state":"working","verb":"replying","external":"openclaw"}]}}'
push_p7(){ curl -s -X POST "$B/status" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$P7_STATUS"; }
R=$(push_p7)
echo "$R" | grep -q '"ok":true' && ok "P7 ❗入りstatus push 生存 (通知は非同期)" || ng "P7 ❗status pushで死んだ: $R"

# status push済みの社員がスマホPWAに描画され、社員タップ→シートまで通ることを確認。
PWA_PY="${VENV_PY:-}"
if [ -n "$PWA_PY" ] && [ -x "$PWA_PY" ] && "$PWA_PY" -c 'import playwright' >/dev/null 2>&1; then
  mkdir -p tests/artifacts
  # R77: 既定は部屋の経路（pwa3d_smoke・R99-P4-6 からドット絵）・一覧への退避経路は pwa_smoke が担当（/ui/**を落として検査）
  "$PWA_PY" tests/pwa3d_smoke.py "$B" "$DID" "$SECRET" "$TOKEN" tests/artifacts/pwa3d_smoke.png \
    || ng "PWA の部屋のスモーク失敗（pwa3d_smoke）"
  # R99-P4-6: pwa_smoke の直前に同じ ❗ 入りの status（P7）を push し直し、/sync（office=null＝status は変えない）で
  # Mac の生存（agentseen）も刻み直す。pwa3d_smoke は形状ごとの検査で数分かかる＝P7 の push（と、その前の relay_agent の
  # /sync）から 180 秒を超えると、PWA は同期切れ（status の ts か agentSeenAgo が 180 秒超・app.js の updateStale）として
  # 送信前に confirm() を出し、Playwright がそれを閉じて /instruct が飛ばない（負荷の高い日の偽の赤・実測）。
  # 同じ ❗ の集合なので通知の遷移（notifyAttn の newly）は起きない。
  R=$(push_p7)
  RS=$(curl -s -X POST "$B/sync" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"office":null,"ackIds":[],"wantOpenclaw":false}')
  FRESH=$(curl -s "$B/status" -H "Authorization: Bearer $TOKEN" | python3 -c 'import json,sys,time
d = json.load(sys.stdin); age = int(time.time() - (d.get("ts") or 0) / 1000); ago = d.get("agentSeenAgo")
fresh = age <= 30 and isinstance(ago, int) and ago <= 30 and "e2e-attn-0001" in (d.get("json") or "")
print("ok" if fresh else "ng", age, ago)' 2>/dev/null)
  read -r FR_OK FR_AGE FR_AGO <<< "$FRESH"
  if [ "$FR_OK" = "ok" ]; then
    ok "R99-P4-6 pwa_smoke の直前に ❗ 入りの status を push し直した（status ${FR_AGE} 秒前・agentSeenAgo ${FR_AGO} 秒）"
  else
    ng "R99-P4-6 pwa_smoke の直前の push し直しが効いていない（status ${FR_AGE:-?} 秒前・agentSeenAgo ${FR_AGO:-?}・push: ${R}・sync: $(echo "$RS" | head -c 80)）"
  fi
  "$PWA_PY" tests/pwa_smoke.py "$B" "$DID" "$SECRET" "$TOKEN" tests/artifacts/pwa_smoke.png \
    || ng "PWAスモーク(2D退避)失敗"
else
  echo "  - Playwright無し→PWAスモーク省略"
fi

sleep 2
C=$(curl -s "$B/push/subs" -H "Authorization: Bearer $TOKEN")
echo "$C" | grep -q '"count":1' && ok "P7 送信失敗(非410)で購読を消さない" || ng "P7 購読が消えた: $C"
R=$(curl -s -X POST "$B/push/unsubscribe" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"endpoint":"https://127.0.0.1:9/e2e-push"}')
C=$(curl -s "$B/push/subs" -H "Authorization: Bearer $TOKEN")
echo "$C" | grep -q '"count":0' && ok "P7 unsubscribe 削除" || ng "P7 unsubscribe失敗: $C"

# ---- R79-7 WebSocket（認証マトリクス/扇形配信/wake/"p"→"P"/appOnline） ----
# statusを上書きするためPWAスモークより後に置く。HTTP退避経路（/sync /pull /ack /status）の
# 既存アサーションは上に全て残っている＝WSはあくまで「追加」（退避削除はWS本番30日後に判断）。
if node -e 'if(typeof WebSocket!=="function")process.exit(1)' 2>/dev/null; then
  node tests/ws_e2e.mjs "$B" "$TOKEN" "$POST_TOKEN" "$MACMINI_TOKEN" || ng "R79-7/9 WS E2E失敗"
else
  echo "  - node組込WebSocket無し（node<22）→ WS E2E省略"
fi

# ---- R79-8 実daemonドッグフーディング（ws_client.py が実workerdへWS接続→wake→配達） ----
# --once はHTTP固定なので、ここだけ本物の常駐モード（WS）を数秒だけ走らせて
# 「投函の瞬間に配達される」＝ポーリング無しの本流を実測する。
OFFICE_HOME="$VHOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" \
  python3 server/relay_agent.py > /tmp/relay_ws_agent.log 2>&1 &
WS_AGENT_PID=$!
WS_UP=0
for i in $(seq 1 20); do
  grep -q "WS接続" /tmp/relay_ws_agent.log 2>/dev/null && { WS_UP=1; break; }
  sleep 0.5
done
[ "$WS_UP" = "1" ] && ok "R79-8 relay_agent がWS常時接続（ws_client.py→実workerd）" \
  || ng "R79-8 daemonがWS接続しない: $(tail -3 /tmp/relay_ws_agent.log 2>/dev/null)"
WSNONCE=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
ENV_WSD=$(sign_env "e2e-ws-agent-01" "WSドッグフード指示" "$(date +%s)" "$WSNONCE")
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$ENV_WSD" >/dev/null
FWD="$VHOME/.claude/office_inbox/e2e-ws-agent-01.json"
WS_DELIVERED=0
for i in $(seq 1 20); do
  [ -f "$FWD" ] && { WS_DELIVERED=1; break; }
  sleep 0.5
done
if [ "$WS_DELIVERED" = "1" ] && grep -q "WSドッグフード指示" "$FWD"; then
  ok "R79-8 wake駆動の配達（投函→WS経由で即時・HTTPポーリング無し）"
else
  ng "R79-8 WS配達失敗: $(tail -5 /tmp/relay_ws_agent.log 2>/dev/null)"
fi
kill "$WS_AGENT_PID" 2>/dev/null; wait "$WS_AGENT_PID" 2>/dev/null
# 積み残し掃除（ackはdaemonが即flush済みのはずだが、失敗時もキューを次の検査へ持ち越さない）
R=$(curl -s "$B/pull" -H "Authorization: Bearer $TOKEN")
IDS=$(python3 -c 'import json,sys;d=json.loads(sys.argv[1]);print(json.dumps([i["id"] for i in d.get("items",[])]))' "$R")
[ "$IDS" != "[]" ] && curl -s -X POST "$B/ack" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "{\"ids\":$IDS}" >/dev/null

# ---- R116-S0 本物の ws_loop（WS 常駐）を何周回しても、中継の status に掲示の印 id8 が載り、status の送信が揺れない ----
# 以前は _redact_office_for_relay が office_json の 2 秒キャッシュの dict をその場で削っていた＝ローカル走査と直後の sync が
# 同じ dict に 2 回かけ、中継へ送る方から id8 が落ちた（R102-11 の答え先の指定とスマホの全文の先読みが本番で効かない）うえ、
# 印あり／なしの指紋が交互に立って 2 秒の走査のたびに sync を送った。P7・pwa3d は status を直接注入する＝この経路を通らない。
# 鎖: 一時 HOME の transcript ＋ 本物の承認フックの掲示（relay_agent の初回の sync の後に出す＝ローカル走査が変化を見つける経路）
# → relay_agent（WS 常駐・スマホ役の app 接続があるので走査は 2 秒）→ DO の status（スマホ役の WS フレームと GET /status）。
# sync の回数は DO の書込行数で数える（sync 1 回ごとに必ず 2 行以上増え、relay_agent が office_relay_usage.json に写す）。
# wrangler 無しの同じ鎖（偽の WS で本物の ws_loop）は test_relay_agent の test_r116_ws_loop_keeps_id8_and_does_not_resync_every_scan。
if node -e 'if(typeof WebSocket!=="function")process.exit(1)' 2>/dev/null; then
  R116_HOME="$PWD/tests/artifacts/.tmp/r116_id8_$$"
  R116_SESS="e2e-r116-id8-0001"
  R116_TR="$R116_HOME/.claude/projects/-Users-test-r116/$R116_SESS.jsonl"
  R116_USAGE="$R116_HOME/.claude/office_relay_usage.json"
  R116_WIN=12
  rm -rf "$R116_HOME"
  mkdir -p "$(dirname "$R116_TR")" "$R116_HOME/.claude/office_inbox"
  cp tests/fixtures/working_tool.jsonl "$R116_TR"
  # 本番の形（反証）: 送信履歴を 1 行（本番の _history.json は 50 件を期限なしで持つ＝today.lastSentAgo は常に数で、走査のたびに秒で増える）。
  # 履歴の無い HOME では lastSentAgo が null のまま＝指紋に秒が入っても緑になり、本番の「走査のたびの sync」を見逃した
  python3 -c 'import json,sys,time;json.dump([{"session":sys.argv[2],"disp":"demo-project","text":"t","ts":time.time()-100}],open(sys.argv[1],"w"))' \
    "$R116_HOME/.claude/office_inbox/_history.json" "$R116_SESS"
  # スマホ役（role=app の WS）: 届いた status フレームを 1 行ずつ書く（受け取った時刻と中身の json だけ）
  node - "$B" "$TOKEN" "$R116_HOME/frames.jsonl" 90 > "$R116_HOME/app.log" 2>&1 <<'EOF' &
const fs = require("fs");
const [base, token, out, secs] = process.argv.slice(2);
const put = (o) => fs.appendFileSync(out, JSON.stringify(o) + "\n");
const ws = new WebSocket(base.replace(/^http/, "ws") + "/ws?role=app",
  ["aioffice.v1", "bearer." + Buffer.from(token, "utf-8").toString("base64url")]);
ws.onopen = () => put({ at: Date.now(), open: 1 });
ws.onerror = () => put({ at: Date.now(), error: 1 });
ws.onmessage = (ev) => {
  let d = null;
  try { d = JSON.parse(String(ev.data)); } catch (_) { return; }
  if (d && d.t === "status") put({ at: Date.now(), json: typeof d.json === "string" ? d.json : "" });
};
setTimeout(() => { try { ws.close(); } catch (_) { /* 閉じ済み */ } process.exit(0); }, Number(secs) * 1000);
EOF
  R116_PIDS="$!"
  for i in $(seq 1 40); do grep -q '"open":1' "$R116_HOME/frames.jsonl" 2>/dev/null && break; sleep 0.25; done
  # 本物の常駐（--once ではない＝WS の ws_loop）。Mac の前の在否は固定（本物の HID の空き時間で macActive が揺れない）
  OFFICE_HOME="$R116_HOME" OFFICE_FAKE_MAC_IDLE=0 RELAY_URL="$B" RELAY_TOKEN="$TOKEN" \
    python3 server/relay_agent.py > "$R116_HOME/agent.log" 2>&1 &
  R116_PIDS="$R116_PIDS $!"
  for i in $(seq 1 60); do [ -s "$R116_USAGE" ] && break; sleep 0.25; done     # 初回の sync（掲示の前）が済んだ
  sleep 3                                  # 起動直後に 1 回だけ起きる変化（res の裏の読み込みなど）を掲示の前に済ませる
  touch "$R116_TR"                         # 経過（age）を 0 から数え直す＝窓の間に分の境を跨がない
  python3 - "$R116_SESS" > "$R116_HOME/hook_in.json" <<'EOF'
import json, sys
print(json.dumps({"session_id": sys.argv[1], "cwd": "/Users/test/demo-project", "hook_event_name": "PermissionRequest",
                  "permission_mode": "default", "tool_name": "Bash",
                  "tool_input": {"command": "git push origin main", "description": "push"},
                  "transcript_path": "/nonexistent/transcript.jsonl"}, ensure_ascii=False))
EOF
  OFFICE_HOME="$R116_HOME" OFFICE_APPROVAL_WAIT=90 OFFICE_APPROVAL_POLL=0.5 \
    bash hooks/office-approval-wait.sh < "$R116_HOME/hook_in.json" > "$R116_HOME/hook_out.json" 2>/dev/null &
  R116_PIDS="$R116_PIDS $!"
  R116_ASK=""
  for i in $(seq 1 40); do
    R116_ASK=$(ls "$R116_HOME/.claude/office_approvals/" 2>/dev/null \
      | sed -nE "s/^$R116_SESS\.([0-9a-f]{32})\.json$/\1/p" | head -1)
    [ -n "$R116_ASK" ] && break
    sleep 0.25
  done
  # 掲示が中継に載るまで待つ（ASK_GRACE 3 秒＋走査 2 秒＋sync）＝スマホ役が受け取ったフレームを読んで、その人の ask で見る
  # （文字列の grep は不可: status には actions.caps.ask など別の "ask" キーがあり、掲示の前に窓を開けてしまった）
  for i in $(seq 1 80); do
    python3 - "$R116_HOME/frames.jsonl" "$R116_SESS" <<'EOF' 2>/dev/null && break
import json, sys
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        o = json.loads(json.loads(line).get("json") or "{}")
    except ValueError:
        continue
    if any(isinstance(e, dict) and e.get("session") == sys.argv[2] and isinstance(e.get("ask"), dict)
           for e in (o.get("employees") or [])):
        sys.exit(0)
sys.exit(1)
EOF
    sleep 0.25
  done
  sleep 1                                  # その sync の使用量の写しが書かれるのを待つ
  R116_ROWS0=$(python3 -c 'import json,sys;print(int(json.load(open(sys.argv[1])).get("rows") or 0))' "$R116_USAGE" 2>/dev/null || echo 0)
  R116_N0=$(wc -l < "$R116_HOME/frames.jsonl" | tr -d ' ')
  sleep "$R116_WIN"                        # 2 秒の走査をおよそ 5 周（掲示は出たまま・ほかに変わるものは無い）
  R116_ROWS1=$(python3 -c 'import json,sys;print(int(json.load(open(sys.argv[1])).get("rows") or 0))' "$R116_USAGE" 2>/dev/null || echo 0)
  R116_N1=$(wc -l < "$R116_HOME/frames.jsonl" | tr -d ' ')
  curl -s "$B/status" -H "Authorization: Bearer $TOKEN" > "$R116_HOME/status.json"
  for P in $R116_PIDS; do pkill -P "$P" 2>/dev/null; kill "$P" 2>/dev/null; wait "$P" 2>/dev/null; done
  R116_PIDS=""
  python3 - "$R116_HOME" "$R116_SESS" "$R116_ASK" "$R116_ROWS0" "$R116_ROWS1" "$R116_N0" "$R116_N1" "$R116_WIN" \
    > "$R116_HOME/check.txt" 2>&1 <<'EOF'
import json, sys
from pathlib import Path
home, sess, ask, rows0, rows1, n0, n1, win = sys.argv[1:9]
home = Path(home)
assert len(ask) == 32, f"本物の承認フックが掲示を出さない: {ask!r}"
assert "WS接続" in (home / "agent.log").read_text(encoding="utf-8", errors="replace"), "relay_agent が WS 常駐（ws_loop）に入らない"

def asks_of(js):
    try:
        o = json.loads(js) if isinstance(js, str) and js else {}
    except ValueError:
        return []
    return [(k, e["ask"]) for k in ("employees", "roster") for e in (o.get(k) or [])
            if isinstance(e, dict) and e.get("session") == sess and isinstance(e.get("ask"), dict)]

def flat(o, p=""):
    """変わったキーの道筋だけを言うための平らな表（値は出さない）。指紋と同じく generatedAt・relay は除き、経過は分で見る。"""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if p == "" and k in ("generatedAt", "relay"):
                continue
            out.update(flat(v // 60 if k == "age" and isinstance(v, int) else v, f"{p}.{k}"))
        return out
    if isinstance(o, list):
        out = {}
        for i, v in enumerate(o):
            out.update(flat(v, f"{p}[{i}]"))
        return out
    return {p: o}

lines = [x for x in (home / "frames.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
frames = [json.loads(x) for x in lines]
seen = [a for f in frames for a in asks_of(f.get("json"))]
assert seen, "掲示が中継の status（スマホ役の WS フレーム）に一度も載らない"
problems = []                                    # id8 と送信の揺れは別々に見て、両方を言う（片方の失敗で他方を隠さない）
# 本番の形の前提: 送信履歴が載っている（最後の送信からの秒が数＝走査のたびに増える値が中継の snapshot にある）
lsa = [(json.loads(f["json"]).get("today") or {}).get("lastSentAgo") for f in frames if f.get("json")]
if not lsa or not all(type(x) is int for x in lsa):
    problems.append(f"送信履歴が中継の today に載らない（本番の形の前提が崩れた）: {lsa[:3]}")
bad = [(k, a) for k, a in seen if a.get("id8") != ask[:8]]
if bad:
    problems.append(f"中継の ask に掲示の印 id8 が無い／違う（R102-11 の答え先・全文の先読みが効かない）: {bad[:2]}")
st = json.loads((home / "status.json").read_text(encoding="utf-8"))
now = asks_of(st.get("json"))
if not (now and all(a.get("id8") == ask[:8] for _, a in now)):
    problems.append(f"GET /status の ask に id8 が無い: {now}")
rows0, rows1, n0, n1 = int(rows0), int(rows1), int(n0), int(n1)
assert rows0 > 0, "relay_agent の sync の写し（office_relay_usage.json）が無い"
# 窓の間に届いたフレーム（office を載せた sync＝DO の書込 4 行：agentseen・status・attnstate・使用量）と、何が変わって送ったか
win_frames = [f for f in frames[n0:n1] if "json" in f]
changed = []
for i, f in enumerate(frames[:n1]):
    if i < n0 or "json" not in f:
        continue
    prev = next((g for g in reversed(frames[:i]) if g.get("json")), None)
    a, b = flat(json.loads(prev["json"])) if prev else {}, flat(json.loads(f["json"] or "{}"))
    changed.append(sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))[:4])
d = rows1 - rows0
empty_syncs = (d - 4 * len(win_frames)) / 2      # office を載せない sync（2 行：agentseen・使用量）の回数
if empty_syncs > 1:
    problems.append(f"掲示の後、何も変わらないのに sync を送り続けた（{win} 秒で DO の書込 {d} 行・フレーム {len(win_frames)} 枚"
                    f"＝空の sync 約 {empty_syncs:g} 回）")
if len(win_frames) > 2:
    problems.append(f"{win} 秒の間に status のフレームが {len(win_frames)} 枚（送信が揺れた・変化: {changed}）")
assert not problems, " ／ ".join(problems)
print(f"フレーム {len(seen)} 件すべて id8 あり・{win} 秒で DO の書込 {d} 行＝office の送信 {len(win_frames)} 回"
      f"（変化: {changed or 'なし'}）・空の sync {empty_syncs:g} 回")
EOF
  R116_RC=$?
  R116_OUT=$(tail -2 "$R116_HOME/check.txt" | tr '\n' ' ')
  if [ "$R116_RC" -eq 0 ]; then
    ok "R116-S0 本物の ws_loop（WS 常駐・2 秒の走査）で中継の status に掲示の印 id8 が載り、送信が揺れない（${R116_OUT}）"
  else
    ng "R116-S0 中継の id8／送信の揺れ: ${R116_OUT}（relay_agent: $(tail -3 "$R116_HOME/agent.log" 2>/dev/null | tr '\n' ' ')）"
  fi
  rm -rf "$R116_HOME"
  R116_HOME=""
else
  echo "  - node組込WebSocket無し（node<22）→ R116-S0 の id8 の E2E 省略"
fi

# ---- R79-10 遠隔実行（許可リスト）E2E: スマホ役の act-封筒 → relay_agent → daemon → 実行 ----
# 実daemonを temp HOME で起こし、許可リストに1件だけ登録した安全なレシピを遠隔実行する。
ACT_PORT=4796
ACT_HOME="$VHOME"
mkdir -p "$ACT_HOME/.claude"
ACT_MARK="$ACT_HOME/act_ran.txt"
python3 - "$ACT_HOME" "$ACT_MARK" <<'EOF'
import json, os, sys, stat
home, mark = sys.argv[1], sys.argv[2]
p = os.path.join(home, ".claude", "office_recipes.json")
json.dump({"v": 1, "recipes": [
    {"id": "r_e2e", "label": "E2E検査", "argv": ["/bin/sh", "-c", f"echo ran > {mark}; echo done /Users/x/y/report.md"],
     "cwd": "/tmp", "timeoutSec": 20, "returnOutput": "tail"}]},
    open(p, "w"), ensure_ascii=False)
os.chmod(p, 0o600)
EOF
OFFICE_HOME="$ACT_HOME" OFFICE_FAKE_NOTIFY="$ACT_HOME/notify.txt" \
  python3 server/office_server.py --port $ACT_PORT > /tmp/act_daemon.log 2>&1 &
ACT_PID=$!
ACT_UP=0
for i in $(seq 1 30); do
  curl -s -o /dev/null -H "X-Office-Local: 1" "http://127.0.0.1:$ACT_PORT/api/office" && { ACT_UP=1; break; }
  sleep 0.5
done
[ "$ACT_UP" = "1" ] && ok "R79-10 daemon起動（実行者=office_server）" || ng "R79-10 daemonが起動しない: $(tail -3 /tmp/act_daemon.log)"
# office_json に許可リストの最小ビューが載る（argv/cwdは載らない＝中継に構成情報を出さない）
python3 - "$ACT_PORT" <<'EOF' && ok "R79-10 office_json.actions（id/labelのみ・argv非公開）" || ng "R79-10 actionsビュー不正"
import json, sys, urllib.request
d = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/api/office", headers={"X-Office-Local":"1"})).read())
a = d["actions"]
assert a["caps"]["actions"] == 1, a
assert [r["id"] for r in a["recipes"]] == ["r_e2e"], a
assert set(a["recipes"][0]) == {"id", "label", "dangerous", "returnOutput"}, a["recipes"][0]
assert "argv" not in json.dumps(a) and "/tmp" not in json.dumps(a), a
EOF
# スマホ役: act-封筒（kind=run）を署名して /instruct へ → relay_agent --once が daemon へ回す
ACTN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
ACT_TEXT='{"aioffice":1,"kind":"run","recipe":"r_e2e","args":[],"reqId":"req-e2e00001"}'
ENV_ACT=$(sign_env "act-00112233445566ff" "$ACT_TEXT" "$(date +%s)" "$ACTN")
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$ENV_ACT" >/dev/null
OFFICE_HOME="$ACT_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$ACT_PORT \
  python3 server/relay_agent.py --once > /tmp/act_relay.log 2>&1
RAN=0
for i in $(seq 1 20); do
  [ -f "$ACT_MARK" ] && { RAN=1; break; }
  sleep 0.5
done
[ "$RAN" = "1" ] && ok "R79-10 スマホ→中継→relay_agent→daemon→**実コマンド実行**" \
  || ng "R79-10 実行されず: $(tail -3 /tmp/act_relay.log)"
[ ! -f "$ACT_HOME/.claude/office_inbox/act-00112233445566ff.json" ] \
  && ok "R79-10 act-はoffice_inboxに書かない（孤児根絶）" || ng "R79-10 act-の孤児inboxが生えた"
# 結果が office_json に出る（出力はscrub済み＝絶対パスが basename になっている）
python3 - "$ACT_PORT" <<'EOF' && ok "R79-10 実行結果がoffice_jsonに反映（出力はscrub済み）" || ng "R79-10 結果が反映されない"
import json, sys, time, urllib.request
for _ in range(40):
    d = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/api/office", headers={"X-Office-Local":"1"})).read())
    rs = [r for r in d["actions"]["results"] if r["reqId"] == "req-e2e00001"]
    if rs and rs[0]["state"] in ("done", "failed", "timeout"):
        r = rs[0]
        assert r["state"] == "done", r
        assert "report.md" in r["output"], r
        assert "/Users/" not in r["output"], r      # パスはbasenameへ
        break
    time.sleep(0.5)
else:
    raise AssertionError("結果が現れない")
EOF
# ---- R92: 実行結果が「スマホが見る側」まで届くか ---------------------------------
# ここが本丸。daemon の /api/office に出るのは前から動いていたが、relay_agent は
# **別プロセスで自分の office_json() を組み立てる**ので、プロセスのメモリにしか無い
# レジストリは relay 側から永久に空＝スマホの ▶実行は ⏳ のまま結果が来なかった。
OFFICE_HOME="$ACT_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$ACT_PORT \
  python3 server/relay_agent.py --once >> /tmp/act_relay.log 2>&1
curl -s "$B/status" -H "Authorization: Bearer $TOKEN" > /tmp/act_status.json
python3 - <<'EOF' && ok "R92 実行結果が中継の /status まで届く（スマホが受け取る側）" || ng "R92 実行結果が中継に載らない: $(head -c 200 /tmp/act_status.json)"
import json
d = json.load(open("/tmp/act_status.json"))
# /status は本体を "json" に文字列で包む（外側は ok/ts/agentOnline/agentSeenAgo）
office = json.loads(d["json"]) if isinstance(d.get("json"), str) else d
rs = [r for r in (office.get("actions") or {}).get("results", [])
      if r.get("reqId") == "req-e2e00001"]
assert rs, f"結果が中継に無い: {json.dumps(office.get('actions'), ensure_ascii=False)[:400]}"
assert rs[0]["state"] == "done", rs[0]
assert "report.md" in rs[0].get("output", ""), rs[0]
assert "/Users/" not in json.dumps(rs[0], ensure_ascii=False), rs[0]   # パスは出さない
EOF
# 保存ファイルにも構成情報（argv/cwd）を残さない
python3 - "$ACT_HOME" <<'EOF' && ok "R92 結果の保存ファイルは0600・argv/cwdを持たない" || ng "R92 結果ファイルが不正"
import json, os, sys
p = os.path.join(sys.argv[1], ".claude", "office_action_results.json")
assert os.path.exists(p), "結果ファイルが作られていない"
assert oct(os.stat(p).st_mode & 0o777) == "0o600", oct(os.stat(p).st_mode)
raw = open(p, encoding="utf-8").read()
assert "argv" not in raw and "/tmp" not in raw, raw[:300]
rows = json.loads(raw)["results"]
assert any(r["reqId"] == "req-e2e00001" for r in rows), rows
EOF

# ---- R100-1b スマホから許可（実 hook × 実 daemon × 中継 × パスキー役）--------------------------
# ON・許可鍵の登録（enr-）・封書 kind:"ask"・apv- の 14 段・hook の allow・rm -rf は通らない・OFF・孤児 inbox 0。詳細は tests/apv_e2e.py。
# R114: ON の間のタップ（mode:"tap"・WebAuthn 無し）＝remote/review の往復（hook が allow・監査に mode=tap）・mac は tap-mac
# （掲示の時点で mac なら決定の瞬間に下がっても）・hook の独立の tap-mac・OFF は off・鍵の無い端末は not-enrolled も同じ節。
python3 tests/apv_e2e.py "$B" "$TOKEN" "$PORT" && ok "R100-1b／R114 スマホから許可 E2E（Face ID の署名とタップ・tests/apv_e2e.py）" \
  || ng "R100-1b／R114 スマホから許可 E2E が赤（python3 tests/apv_e2e.py の ✗ 行）"

# ---- R87 封書: スマホの会話ビューア（E2EE）E2E -------------------------------------
# スマホ役が署名した dlg- 要求 → 中継 → relay_agent → daemon が封じる → 次の sync で dlg レーン →
# GET /status から取った封書を**端末秘密で本当に開ける**（PWA 本番と同じ openBlob）／
# フル Bearer だけの第三者には base64url しか見えない／別端末では開けない／限定トークンは書けない／
# 期限で空になる／dlg- の孤児 inbox は生えない。R79-10 の daemon は OFFICE_CONFIG を持たないので、
# 専用の HOME・config・daemon(:4795) を立てる。
DLG_HOME=$(python3 tests/make_home.py)
DLG_PORT=4795
printf '{"projects": {}, "dialogRelay": true}\n' > "$DLG_HOME/office_config.json"
# 端末を1台発行（正本の new_device＝実際のペアリングと同じ台帳形式）
DLG_DEV=$(OFFICE_HOME="$DLG_HOME" python3 - <<'EOF'
import importlib.util, json
from pathlib import Path
spec = importlib.util.spec_from_file_location("o", "server/office_server.py")
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
d = o.new_device("e2e-phone")
print(json.dumps({"d": d["device_id"], "s": d["secret"]}))
EOF
)
DLG_DID=$(echo "$DLG_DEV" | python3 -c 'import sys,json;print(json.load(sys.stdin)["d"])')
DLG_SEC=$(echo "$DLG_DEV" | python3 -c 'import sys,json;print(json.load(sys.stdin)["s"])')
OFFICE_HOME="$DLG_HOME" OFFICE_CONFIG="$DLG_HOME/office_config.json" OFFICE_DIALOG_TTL=8 \
  python3 server/office_server.py --port $DLG_PORT > /tmp/dlg_daemon.log 2>&1 &
DLG_PID=$!
DLG_UP=0
for i in $(seq 1 30); do
  curl -s -o /dev/null -H "X-Office-Local: 1" "http://127.0.0.1:$DLG_PORT/api/office" && { DLG_UP=1; break; }
  sleep 0.5
done
[ "$DLG_UP" = "1" ] && ok "R87 daemon起動（封じる側・dialogRelay=ON）" || ng "R87 daemonが起動しない: $(tail -3 /tmp/dlg_daemon.log)"
# caps.dialog が「封じられる Mac かつ ON」で 1
python3 - "$DLG_PORT" <<'EOF' && ok "R87 caps.dialog=1（封緘バックエンド有・ON）" || ng "R87 caps.dialog が立たない"
import json, sys, urllib.request
d = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/api/office", headers={"X-Office-Local":"1"})).read())
assert d["actions"]["caps"].get("dialog") == 1, d["actions"]["caps"]
EOF
# スマホ役: dlg-<reqId> の封筒（署名は正本 sign_envelope・この端末の秘密で）
DLG_REQ=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
DLG_TEXT="{\"aioffice\":1,\"kind\":\"dialog\",\"reqId\":\"$DLG_REQ\",\"session\":\"sess-verify0001\",\"depth\":0}"
ENV_DLG=$(python3 - "$DLG_DID" "$DLG_SEC" "dlg-$DLG_REQ" "$DLG_TEXT" "$(date +%s)" "$(python3 -c 'import secrets;print(secrets.token_hex(16))')" <<'EOF'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("o", "server/office_server.py")
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
did, sec, sess, text, ts, nonce = sys.argv[1:7]
print(json.dumps(o.sign_envelope(sec, did, sess, text, int(ts), nonce)))
EOF
)
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$ENV_DLG" >/dev/null
# --once #1: 要求を daemon へ回す（封じる）→ --once #2: 封書を dlg レーンで中継へ載せる
OFFICE_HOME="$DLG_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$DLG_PORT \
  python3 server/relay_agent.py --once > /tmp/dlg_relay.log 2>&1
[ -f "$DLG_HOME/.claude/office_dialog_bundles.json" ] \
  && [ "$(stat -f '%Lp' "$DLG_HOME/.claude/office_dialog_bundles.json")" = "600" ] \
  && ok "R87 封書ファイルが 0600 で作られた（daemon→relay_agent はファイル経由）" \
  || ng "R87 封書ファイルが無い/権限が違う: $(ls -la "$DLG_HOME/.claude/" | grep dialog)"
ls "$DLG_HOME/.claude/office_inbox/" 2>/dev/null | grep -q '^dlg-' \
  && ng "R87 dlg- の孤児 inbox が生えた" || ok "R87 dlg- は office_inbox に書かない（孤児根絶）"
OFFICE_HOME="$DLG_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$DLG_PORT \
  python3 server/relay_agent.py --once >> /tmp/dlg_relay.log 2>&1
curl -s "$B/status" -H "Authorization: Bearer $TOKEN" > /tmp/dlg_status.json
# 端末役: 中継から取った封書を端末秘密で開き、ローカル API の会話と一致することを確かめる
python3 - "$DLG_PORT" "$DLG_DID" "$DLG_SEC" "$DLG_REQ" <<'EOF' && ok "R87 実往復: 封書を端末秘密で開くとローカルの会話と一致（中継は暗号文しか持たない）" || ng "R87 実往復に失敗（/tmp/dlg_relay.log を確認）"
import json, subprocess, sys, urllib.request
port, did, sec, req = sys.argv[1:5]
d = json.load(open("/tmp/dlg_status.json"))
assert isinstance(d.get("dlg"), str) and d["dlg"], f"/status に dlg が無い: {list(d)}"
lane = json.loads(d["dlg"])
item = next(i for i in lane["items"] if i["id"] == req)
assert "err" not in item, item
raw = d["dlg"]
assert all(c.isalnum() or c in "-_" for c in item["b"]), "b が base64url でない"
local = json.loads(urllib.request.urlopen(urllib.request.Request(
    f"http://127.0.0.1:{port}/api/session/dialog?session=sess-verify0001", headers={"X-Office-Local":"1"})).read())
assert local["messages"], "ローカルの会話が空"
for m in local["messages"]:
    assert m["text"] not in raw, "平文が中継に載っている"
r = subprocess.run(["node", "tests/dialog_open_cli.mjs"], input=json.dumps(
    {"bundle": item, "secretHex": sec, "deviceId": did, "targetSession": "sess-verify0001"}),
    capture_output=True, text=True, env={"NO_COLOR": "1", "FORCE_COLOR": "0", "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"})
assert r.returncode == 0, r.stderr
page = json.loads(r.stdout)
assert page["messages"] == local["messages"], "封書の会話がローカルと違う"
# 別端末の秘密では開けない
r2 = subprocess.run(["node", "tests/dialog_open_cli.mjs"], input=json.dumps(
    {"bundle": item, "secretHex": "ab" * 32, "deviceId": did, "targetSession": "sess-verify0001"}),
    capture_output=True, text=True, env={"NO_COLOR": "1", "FORCE_COLOR": "0", "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"})
assert r2.returncode == 2, "別の秘密で開けてしまった"
EOF
# R100-1a: ターミナル画面（kind:"term"）も同じ封書のレーン＝端末の秘密で開くと「端末の行」（kind/rows/cur）が出る・平文は中継に載らない
TRM_REQ=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
TRM_TEXT="{\"aioffice\":1,\"kind\":\"term\",\"reqId\":\"$TRM_REQ\",\"session\":\"sess-verify0001\",\"after\":null}"
ENV_TRM=$(python3 - "$DLG_DID" "$DLG_SEC" "dlg-$TRM_REQ" "$TRM_TEXT" "$(date +%s)" "$(python3 -c 'import secrets;print(secrets.token_hex(16))')" <<'EOF'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("o", "server/office_server.py")
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)
did, sec, sess, text, ts, nonce = sys.argv[1:7]
print(json.dumps(o.sign_envelope(sec, did, sess, text, int(ts), nonce)))
EOF
)
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$ENV_TRM" >/dev/null
OFFICE_HOME="$DLG_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$DLG_PORT \
  python3 server/relay_agent.py --once >> /tmp/dlg_relay.log 2>&1
OFFICE_HOME="$DLG_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$DLG_PORT \
  python3 server/relay_agent.py --once >> /tmp/dlg_relay.log 2>&1
curl -s "$B/status" -H "Authorization: Bearer $TOKEN" > /tmp/trm_status.json
python3 - "$DLG_DID" "$DLG_SEC" "$TRM_REQ" <<'EOF' && ok "R100-1a ターミナル画面の封書: 端末の秘密で開くと kind:term の行とカーソル（平文は中継に無い）" || ng "R100-1a ターミナル画面の往復に失敗（/tmp/dlg_relay.log）"
import json, subprocess, sys
did, sec, req = sys.argv[1:4]
d = json.load(open("/tmp/trm_status.json"))
lane = json.loads(d["dlg"])
item = next(i for i in lane["items"] if i["id"] == req)
assert "err" not in item, item
r = subprocess.run(["node", "tests/dialog_open_cli.mjs"], input=json.dumps(
    {"bundle": item, "secretHex": sec, "deviceId": did, "targetSession": "sess-verify0001"}),
    capture_output=True, text=True, env={"NO_COLOR": "1", "FORCE_COLOR": "0", "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"})
assert r.returncode == 0, r.stderr
page = json.loads(r.stdout)
assert page["kind"] == "term" and page["reset"] is True and page["rows"], page
assert isinstance(page["cur"], dict) and page["cur"]["off"] > 0, page["cur"]
for row in page["rows"]:
    t = row.get("t") or ""
    assert len(t) < 8 or t not in d["dlg"], "端末の行の平文が中継に載っている"
EOF
# 限定トークンでは dlg を書けない（/sync は 403）・読めない（GET /status に無い）
for TK in "$POST_TOKEN" "$MACMINI_TOKEN"; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$B/sync" -H "Authorization: Bearer $TK" \
    -H "Content-Type: application/json" -d '{"dlg":"x"}')
  [ "$CODE" = "403" ] || ng "R87 限定トークンで /sync に dlg を書けた ($CODE)"
done
curl -s "$B/status" -H "Authorization: Bearer $POST_TOKEN" | grep -q '"dlg"' \
  && ng "R87 限定トークンの GET /status に dlg が載る" || ok "R87 限定トークンは封書を書けない・読めない（403／非搭載）"
# 期限（OFFICE_DIALOG_TTL=8）を過ぎると次の --once で空を1回送り、中継の行が空になる
sleep 9
OFFICE_HOME="$DLG_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$DLG_PORT \
  python3 server/relay_agent.py --once >> /tmp/dlg_relay.log 2>&1
curl -s "$B/status" -H "Authorization: Bearer $TOKEN" | python3 -c '
import sys, json
d = json.load(sys.stdin); lane = json.loads(d["dlg"]) if d.get("dlg") else None
assert lane == {"v": 1, "items": []}, lane' && ok "R87 期限後は空バンドル＝暗号文が中継から消える" || ng "R87 期限後も封書が中継に残る"
kill $DLG_PID 2>/dev/null; wait $DLG_PID 2>/dev/null
rm -rf "$DLG_HOME"

# 未登録レシピは denied（許可リストの外は実行されない＝この機能の安全性の本体）
ACTN2=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
ENV_ACT2=$(sign_env "act-00112233445566ee" '{"aioffice":1,"kind":"run","recipe":"r_notregistered","args":[],"reqId":"req-e2e00002"}' "$(date +%s)" "$ACTN2")
curl -s -X POST "$B/instruct" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$ENV_ACT2" >/dev/null
OFFICE_HOME="$ACT_HOME" RELAY_URL="$B" RELAY_TOKEN="$TOKEN" OFFICE_PORT=$ACT_PORT \
  python3 server/relay_agent.py --once >> /tmp/act_relay.log 2>&1
python3 - "$ACT_PORT" <<'EOF' && ok "R79-10 未登録レシピは denied（許可リスト外は実行しない）" || ng "R79-10 未登録レシピの拒否が確認できない"
import json, sys, time, urllib.request
for _ in range(20):
    d = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/api/office", headers={"X-Office-Local":"1"})).read())
    rs = [r for r in d["actions"]["results"] if r["reqId"] == "req-e2e00002"]
    if rs:
        assert rs[0]["state"] == "denied", rs[0]
        break
    time.sleep(0.5)
else:
    raise AssertionError("denied結果が現れない")
EOF
# レシピの編集はローカルUI専用: CSRFヘッダ無しの POST /api/recipes/set は403
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:$ACT_PORT/api/recipes/set" \
  -H "Content-Type: application/json" -d '{"recipes":[]}')
[ "$CODE" = "403" ] && ok "R79-10 レシピ編集はCSRF必須（403）" || ng "R79-10 CSRF無しで編集できた ($CODE)"
kill "$ACT_PID" 2>/dev/null; wait "$ACT_PID" 2>/dev/null

# ---- R79-9 mini側もWSドッグフーディング（openclaw_agent が wake駆動で oc-配達） ----
rm -f "$VHOME/.claude/openclaw_inbox/oc-wsdog-01.json"
OFFICE_HOME="$VHOME" python3 tools/openclaw_agent.py > /tmp/oc_ws_agent.log 2>&1 &
OC_WS_PID=$!
OC_UP=0
for i in $(seq 1 20); do
  grep -q "WS接続" /tmp/oc_ws_agent.log 2>/dev/null && { OC_UP=1; break; }
  sleep 0.5
done
[ "$OC_UP" = "1" ] && ok "R79-9 openclaw_agent がWS常時接続（site=macmini）" \
  || ng "R79-9 openclaw_agentがWS接続しない: $(tail -3 /tmp/oc_ws_agent.log 2>/dev/null)"
OCN=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
ENV_OCD=$(sign_oc "oc-wsdog-01" "WS経由のoc配達" "$(date +%s)" "$OCN")
curl -s -X POST "$B/instruct?site=macmini" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "$ENV_OCD" >/dev/null
FOC="$VHOME/.claude/openclaw_inbox/oc-wsdog-01.json"
OC_DELIVERED=0
for i in $(seq 1 20); do
  [ -f "$FOC" ] && { OC_DELIVERED=1; break; }
  sleep 0.5
done
if [ "$OC_DELIVERED" = "1" ] && grep -q "WS経由のoc配達" "$FOC"; then
  ok "R79-9 wake駆動のoc-配達（mini側・ポーリング無し）"
else
  ng "R79-9 oc WS配達失敗: $(tail -5 /tmp/oc_ws_agent.log 2>/dev/null)"
fi
kill "$OC_WS_PID" 2>/dev/null; wait "$OC_WS_PID" 2>/dev/null
R=$(curl -s "$B/pull?site=macmini" -H "Authorization: Bearer $TOKEN")
IDS=$(python3 -c 'import json,sys;d=json.loads(sys.argv[1]);print(json.dumps([i["id"] for i in d.get("items",[])]))' "$R")
[ "$IDS" != "[]" ] && curl -s -X POST "$B/ack?site=macmini" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d "{\"ids\":$IDS}" >/dev/null

echo
[ $NG -eq 0 ] && echo "✅ relay E2E 合格" || { echo "❌ relay E2E ${NG}件失敗"; exit 1; }
