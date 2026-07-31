# AI Office 中継（P2） — Cloudflare Worker + Durable Object

スマホ（外出先）と Mac の間で「指示」と「オフィス状況」を中継する小さな Worker。
ポート開放なし・Mac は完全アウトバウンドのポーリングのみ・無料枠内（月額 ¥0）。

## 仕組み

```
[スマホ/curl] --HTTPS+Bearer--> [Worker fetch()] --RPC--> [Room DO (SQLite)]
[Mac: server/relay_agent.py] --HTTPS(アウトバウンド・ポーリング)--> [Worker] --> [Room DO]
        └→ 取り出した指示を ~/.claude/office_inbox へ → 既存 Stop hook が実セッションへ配達
        └→ office_server.office_json() を /status へ push（スマホ表示用）
```

Mac 1台 = 1 Room（`getByName("mac")`）。P2 は共有 Bearer トークン認証（P3 で QR ペアリング + HMAC 署名へ強化）。

## ルート（`/` 以外は Bearer 必須）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/` | ヘルスチェック（認証不要） |
| POST | `/instruct` | スマホ役: 指示をキューに積む `{session, text}` |
| GET | `/pull` | Mac役: キューを peek（消さない・リース）→ `items[{id,...}]` |
| POST | `/ack` | Mac役: 配達済みの id を削除 `{ids:[...]}` |
| POST | `/status` | Mac役: オフィス状況 JSON を保存 `{office:{...}}`（P7: ❗遷移でWeb Push発火） |
| GET | `/status` | スマホ役: 最新のオフィス状況を取得 |
| GET | `/push/vapid` | P7: 購読用VAPID公開鍵（フルBearerのみ・VAPID_JWK未設定なら503） |
| POST | `/push/subscribe` | P7: PWAの購読を保存 `{subscription}`（上限10台） |
| POST | `/push/unsubscribe` | P7: 購読削除 `{endpoint}` |
| GET | `/push/subs` | P7: 登録台数のみ `{count}`（観測用） |
| POST | `/push/test` | P7: 全購読へテスト通知（iPhone実機E2E用） |

配達保証は **at-least-once**（`/pull` は消さず、Mac が配達に成功した id だけ `/ack` で消す）。
途中でネットワークが切れても指示が「静かに消える」ことはなく、次の pull で再取得される。
同一セッションへの複数指示は relay_agent が 1 通に結合して配達する（単一スロットの office_inbox で
最後以外が上書きされないように）。

## ローカル検証（ログイン不要・課金なし）

```bash
cd "AI Office/relay" && npm install          # 初回のみ（wrangler をローカル取得）
bash "AI Office/tests/relay_e2e.sh"          # wrangler dev → curl投函 → relay_agent配達 → status往復
# もしくはフルverify に含める:
RUN_RELAY=1 bash "AI Office/verify.sh"
```

## 本番デプロイ（ユーザー操作・Cloudflare アカウント必要）

```bash
cd "AI Office/relay"
npx wrangler login                                   # 初回のみ（ブラウザ認証）
TOKEN=$(openssl rand -hex 32)                        # 長いランダムトークンを生成
echo "$TOKEN" | npx wrangler secret put RELAY_TOKEN  # Worker 側の秘密に設定
bash deploy.sh                                       # ★公開前ゲート付きdeploy（禁止語検査→構文check→wrangler deploy）
                                                     #   → https://ai-office-relay.<subdomain>.workers.dev

# Mac 側の設定（秘密・リポジトリに置かない）
cp office_relay.example.json ~/.claude/office_relay.json
chmod 600 ~/.claude/office_relay.json
#   url を deploy で出た URL に、token を上の $TOKEN に書き換える
python3 "../server/relay_agent.py"                   # 常駐ポーリング開始
```

## P3 スマホPWA＋QRペアリング＋HMAC署名（2026-07-08）

- 追加ルート（無認証・秘密なし）: `GET /app`（PWA本体）・`GET /app/sw.js`・`GET /app/manifest.webmanifest`。スマホはこれを開いて creds を localStorage に保存し、以後 `/status`・`/instruct` を叩く。
- `POST /instruct` は **署名封筒**を受理する（`{v,device_id,session,text,ts,nonce,alg:"HS256",sig}`）。**Worker は sig を検証しない**（署名鍵を持たない・形式プレフィルタのみ）。真正性は Mac 側 `relay_agent` が per-device HMAC-SHA256 で検証する＝**Bearer が漏れても偽造不可**。無署名の `{session,text}` は 400。
- ペアリング: Mac の office UI「📱スマホ連携」で発行 → `<relay>/app#v=1&d=..&s=..&t=..&e=..` リンクをスマホへ（AirDrop/メッセージ）→ 開くと1回で登録。デバイス台帳＝`~/.claude/office_devices.json`(600)・30日TTL・office UI から revoke。
- 検証: `bash tests/relay_e2e.sh`（署名版・JS/Python KAT・改竄reject・リプレイ非再配達を含む）／`RUN_RELAY=1 bash "AI Office/verify.sh"`。
- deploy 後の追加設定は不要（PWA も Worker 内蔵）。`office_relay.json` の `url`+`token` を office UI が読んで pair_url を組み立てる。

## RELAY_POST_TOKEN（P5・OpenClaw用の限定トークン）

OpenClaw（別Mac）から Discord 経由で指示を送るための2枚目のトークン。**POST /instruct と GET /status のみ**許可（`/pull`・`/ack`・POST `/status` は 403）。

- **後方互換**: 未設定なら従来と完全同一（フル `RELAY_TOKEN` のみ）。
- **fail-closed**: `RELAY_TOKEN` と同値に設定すると 500（限定トークンがフル権限へ無言昇格するのを防ぐ）＝別の値にすること。
- **署名は不変**: Worker は署名鍵を持たず sig を検証しない（真正性は Mac 側 relay_agent の per-device HMAC）。POST_TOKEN 漏洩の被害は /instruct スパム（Mac が全棄却＋rate延期で有界）と /status 窃視のみ。

```bash
cd "AI Office/relay"
RUN_RELAY=1 bash "../verify.sh"                              # ★デプロイ前に必ず green
openssl rand -hex 32 | npx wrangler secret put RELAY_POST_TOKEN
bash deploy.sh                                               # 公開前ゲート付きdeploy
```
OpenClaw Mac 側: office UI「📱スマホ連携」で label=OpenClaw のデバイスを発行 → `office_server.py`＋`tools/office_send.py`＋`~/.claude/office_send.json`(600・`{url,post_token,device_id,secret}`)を配置 →
`python3 office_send.py <session> "<指示>"`。宛先 session は `GET /status` か Mac の office_status で確認。

## P7 iOS Web Push（2026-07-11）— ❗承認/質問まちをスマホ通知

暗号（RFC8291 aes128gcm＋RFC8292 VAPID）は全部 Worker の WebCrypto（`src/webpush.js`）＝Mac側は無変更。
KAT=`tests/webpush_kat.mjs`（RFC8291 Appendix A公式ベクタとバイト一致）。

**セットアップ（デプロイ側・済んでいれば不要）**:

```bash
cd "AI Office/relay"
node -e 'const c=require("crypto");const {privateKey}=c.generateKeyPairSync("ec",{namedCurve:"P-256"});console.log(JSON.stringify(privateKey.export({format:"jwk"})))' \
  | npx wrangler secret put VAPID_JWK
bash deploy.sh
```

**iPhone側（手動E2E・ハーネス化不能な最終区間）**:

1. PWAを**ホーム画面に追加**して開く（Safariタブでは通知不可・iOS 16.4+。追加済みならそのまま）
2. ヘッダーの **🔕 をタップ → 通知を許可** → 🔔 になれば購読完了
3. テスト: `curl -s -X POST <url>/push/test -H "Authorization: Bearer <token>"` → iPhoneに「テスト通知」
4. 以後、どのセッションかが❗（承認/質問まち）に**なった瞬間**に通知が届く（続いている間は連打しない）

VAPID鍵ローテ: 新JWK生成→`secret put`→`deploy`→**全端末で🔔を入れ直し**（applicationServerKeyが変わるため）。

## トークンのローテーション（runbook・定期=90日目安／漏洩疑い=即時）

**RELAY_TOKEN（輸送Bearer・フル権限）**

```bash
cd "AI Office/relay"
NEW=$(openssl rand -hex 32)
echo "$NEW" | npx wrangler secret put RELAY_TOKEN     # ①Worker側を更新（この瞬間から旧トークンは401）
python3 - "$NEW" <<'EOF'                              # ②Mac側 office_relay.json の token を書き換え（600維持）
import json, os, sys
p = os.path.expanduser("~/.claude/office_relay.json")
c = json.load(open(p)); c["token"] = sys.argv[1]
json.dump(c, open(p, "w")); os.chmod(p, 0o600)
EOF
launchctl kickstart -k gui/$(id -u)/com.senao.aioffice.relay   # ③relay_agentはconfig起動時読みなので再起動が必須
```

- 確認: `tail ~/Library/Application\ Support/AIOffice/logs/relay.daemon.log` に「📡 中継エージェント起動」＋
  旧トークンで `curl -H "Authorization: Bearer <旧>" <url>/status` が **401/403** になること。
- **注意: スマホPWAは全台再ペアリングが必要**（pair_url に輸送トークンが焼かれているため。
  office UI「📱スマホ連携」で再発行→旧デバイスは30日TTLで自然失効 or revoke）。

**RELAY_POST_TOKEN（OpenClaw用・限定権限）**: 同じ手順の `secret put RELAY_POST_TOKEN` 版＋
OpenClaw Mac の `~/.claude/office_send.json` の `post_token` を書き換え。`RELAY_TOKEN` と同値は500（fail-closed）。

**per-device HMAC secret（真正性の鍵）**: ローテ＝office UI で該当デバイスを **revoke → 新規発行 → 再ペアリング**
（台帳 `~/.claude/office_devices.json` はMac側でのみ発効・Workerは署名鍵を持たない）。

**RELAY_MACMINI_TOKEN（R42.4・Mac mini→中継のOpenClaw status専用）**: 許可は
`POST /status?site=<mac以外>` のみ（mac本体status/指示面には触れない）。設定＝
`wrangler secret put RELAY_MACMINI_TOKEN` → mini側 `~/.claude/office_push.json` の `token` に同値
（送信= `tools/openclaw_push.py`・契約スキーマ= docs/openclaw-status-schema.md）。
他トークンと同値は500（fail-closed）。ローテ＝secret put→mini側書き換えのみ（PWA再ペアリング不要）。

## 無料枠

常駐ポーリング（5秒間隔で pull + status = 約34,000 req/日）は Workers 無料枠（10万 req/日）・DO 無料枠の範囲内。非視聴時は間隔を延ばして更に削減可（`interval`）。スマホの `/status` ポーリング（視聴中5秒間隔）も同枠内。
