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
PORT=8789
TOKEN="e2e-relay-token"
DID="d_0123456789ab"
SECRET="00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

# JS↔Python 署名パリティ＋APP_HTML canonical 検査（node のみ・node_modules不要＝クローン直後でも走る）
if command -v node >/dev/null 2>&1; then
  node tests/js_sign_kat.mjs && ok "JS署名KAT一致 (canonical相互運用)" || ng "JS署名KAT不一致"
  # R65: PWAへ移植した gloss が正本 ui/core/world.js と同一出力（片方だけ直すと落ちる）
  node tests/gloss_parity.mjs >/dev/null 2>&1 && ok "R65 gloss parity (PWA↔core 同一出力)" || ng "R65 gloss parity 不一致 (node tests/gloss_parity.mjs で詳細)"
  # P7: Web Push暗号KAT（RFC8291 Appendix A公式ベクタ＋VAPID自己検証・wrangler不要）
  node tests/webpush_kat.mjs >/dev/null 2>&1 && ok "Web Push KAT (RFC8291ベクタ+VAPID)" || ng "Web Push KAT失敗 (node tests/webpush_kat.mjs で詳細)"
  # R5: Cloudflare依存を読み込まず、worker.js から純関数だけを抽出して購読フィルタを固定。
  node -e 'const fs=require("fs"),a=require("assert");const s=fs.readFileSync("relay/src/worker.js","utf8"),i=s.indexOf("function pushTargets("),j=s.indexOf("// R5_PUSH_TARGETS_END",i);if(i<0||j<0)throw Error("pushTargets not found");eval(s.slice(i,j));const row=(d)=>({v:JSON.stringify(d)});a.strictEqual(pushTargets([row({depts:[]})],"開発").length,1);a.strictEqual(pushTargets([row({depts:["開発"]})],"開発").length,1);a.strictEqual(pushTargets([row({depts:["営業"]})],"開発").length,0);a.strictEqual(pushTargets([row({endpoint:"https://legacy"})],"開発").length,1)' \
    && ok "R5 pushTargets 空/一致/不一致/レガシー" || ng "R5 pushTargets フィルタ判定失敗"
else
  echo "  - node 無し → JS署名KAT省略"
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
# R42.2: relay_agent main() はライセンスゲート配下（relayPwa）。fixture HOMEにテスト鍵の
# hybridライセンスを敷いて従来のE2E挙動を維持する（無ライセンス停止は verify ▶5/単体で検査）。
OFFICE_LICENSE_SIGNING=tests/fixtures/license_test_key.json \
  python3 tools/license_sign.py issue --edition hybrid --email relay-e2e@fixture \
  --out "$VHOME/office_license.json" >/dev/null 2>&1
export OFFICE_LICENSE="$VHOME/office_license.json"
export OFFICE_LICENSE_PUBKEY_N=$(python3 -c 'import json;print(json.load(open("tests/fixtures/license_test_key.json"))["n"][2:])')
# wrangler dev は子に workerd を spawn する。親subshellをkillしても workerd が :PORT に残り
# 次回起動が古いコードの残骸を叩く（＝嘘green/嘘fail）。ポート専有プロセスも明示的に落とす。
cleanup(){ kill ${WPID:-} 2>/dev/null; wait ${WPID:-} 2>/dev/null;
           # wrangler dev の子孫(npm/node/workerd 2階層)は別プロセス群で残るのでポート名で確実に落とす
           pkill -f "wrangler dev --port $PORT" 2>/dev/null;
           pkill -f "workerd.*:$PORT" 2>/dev/null;
           lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null;  # LISTEN限定=接続中の無関係プロセスを巻き込まない
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
curl -s "$B/app" | grep -q "AI Office" && ok "PWA /app 無認証配信 (200)" || ng "PWA /app 配信異常"
curl -s "$B/app/manifest.webmanifest" | grep -q "standalone" && ok "PWA manifest 配信" || ng "PWA manifest 異常"

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
[ "$TS1" = "$TS2" ] && ok "R51 office=null は status.ts を進めない（変化時のみpush）" || ng "R51 nullでもts更新: $TS1→$TS2"
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
ok, msg = o.post_instruction("oc-lobster-1", "R42.5 双方向テスト指示")
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
R=$(curl -s -X POST "$B/status" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"office":{"employees":[{"session":"e2e-attn-0001","disp":"E2E検証部","state":"working","verb":"検証中","question":"どの案で進めますか?","questionOptions":[{"label":"案A (Recommended)","desc":"推奨案"},{"label":"案B","desc":"別案"},{"label":"案C","desc":"保留案"}]},{"session":"oc-e2e","disp":"OpenClaw","dept":"OpenClaw","state":"working","verb":"replying","external":"openclaw","sprite":"/assets/agent_bot.png"}]}}')
echo "$R" | grep -q '"ok":true' && ok "P7 ❗入りstatus push 生存 (通知は非同期)" || ng "P7 ❗status pushで死んだ: $R"

# status push済みの社員がスマホPWAに描画され、社員タップ→シートまで通ることを確認。
PWA_PY="${VENV_PY:-}"
if [ -n "$PWA_PY" ] && [ -x "$PWA_PY" ] && "$PWA_PY" -c 'import playwright' >/dev/null 2>&1; then
  mkdir -p tests/artifacts
  # R77: 既定は3D経路（pwa3d_smoke）・2Dへの退避経路は pwa_smoke が担当（/ui/**を落として検査）
  "$PWA_PY" tests/pwa3d_smoke.py "$B" "$DID" "$SECRET" "$TOKEN" tests/artifacts/pwa3d_smoke.png \
    || ng "PWA 3Dスモーク失敗"
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

echo
[ $NG -eq 0 ] && echo "✅ relay E2E 合格" || { echo "❌ relay E2E ${NG}件失敗"; exit 1; }
