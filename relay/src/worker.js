// AI Office 中継 Worker（P2） — スマホ⇄Mac の指示/状態を1つの Room Durable Object で中継。
//
// ルート（/ 以外は Bearer トークン必須）:
//   GET  /                → ヘルスチェック（認証不要）
//   POST /instruct        → スマホ役: 指示をキューに積む {session, text}
//   GET  /pull            → Mac役(relay_agent): キューを peek（消さない・リース）→ items[{id,...}]
//   POST /ack             → Mac役: 配達済み id を削除 {ids:[...]}（配達成功して初めて消す）
//   POST /status          → Mac役: オフィス状況JSONを保存（スマホ表示用）{office:{...}}
//   GET  /status          → スマホ役: 最新のオフィス状況を取得
//                           （R51: フルBearer・site無しクエリ時のみ kv appseen=now を更新し、
//                            agentSeenAgo=relay_agent最終/syncからの秒 を応答へ添える）
//   POST /sync            → Mac役(relay_agent) R51: 1周1リクエスト統合（フルBearerのみ・限定トークンは403）
//                           req  {office:<redacted snapshot>|null, ackIds:[int...], wantOpenclaw:bool}
//                           resp {ok, items:[peek結果], openclaw:<macmini status|null>, appSeenAgo:int|null}
//                           Room DOへは単一fetch(sync): ack→peek→(office在れば)putStatus＋❗エッジ検出→
//                           agentseen=now→appseen読出し。office=null は「変化なし」＝status非更新
//                           （agentseenだけは必ず更新）。/pull・/ack・/status は無改変＝旧relay_agent後方互換。
//   P7 Web Push（フルBearerのみ・VAPID_JWK未設定なら503で無効）:
//   GET  /push/vapid      → 購読用のVAPID公開鍵（applicationServerKey）
//   POST /push/subscribe  → {subscription} を保存（❗発生時にここへ通知が飛ぶ）
//   POST /push/unsubscribe→ {endpoint} を削除
//   GET  /push/subs       → 登録台数＋部署フィルタ（観測用・endpointは返さない）
//   POST /push/test       → 全購読へテスト通知（iPhone実機E2E用）
//
// 配達保証: /pull は消さず /ack で削除する2フェーズ = at-least-once。
//   relay_agent が配達に成功した時だけ ack するので、途中でネットワークが切れても
//   （ack が飛ばなければ）次の pull で再取得できる。指示が「静かに消える」ことがない。
// Mac1台=1 Room（getByName("mac")）。P2は共有トークン認証。P3でQRペアリング＋HMAC署名へ。
import { DurableObject } from "cloudflare:workers";
import { ASSETS, BUILD as UI_BUILD, MODULES } from "./modules_data.js";
import { APP_HTML } from "./app_html.js";

// R79: PWAシェル(APP_HTML/SW_JS/MANIFEST)の版ID。**シェル自身の内容**から作る。
// ここでモジュール束の UI_BUILD を流用すると、アプリだけ直したときに版が変わらず
// 古いシェルが配られる（ETagの意味が消える）。起動時に1回だけ計算する。
function _fnv1a(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h.toString(36);
}   // R77: PWAの3Dシーン用ESM（自動生成・/ui/... の同じパスで返す）
import { b64u, jwkToRawPub, sendWebPush } from "./webpush.js";   // P7: Web Push（暗号は全部Worker側＝Mac側stdlib不変）

// PWA歩行絵の収録状況はバンドル時に一度だけ索引化する。テーマ派生(__入り)や
// walkdown/walkupはスクリプト側で除外されるため、ここでも安全なstemだけを扱う。

// base64→bytes（Workersは atob 提供・exact sprite応答は immutable キャッシュされ実行は初回のみ）
function spriteBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const PEEK_LIMIT = 100;   // 1回の pull で返す最大件数（DOストレージ肥大の読み側ガード）
const ACK_LIMIT = 500;

export class Room extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      this.ctx.storage.sql.exec(
        `CREATE TABLE IF NOT EXISTS inbox(
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           session TEXT NOT NULL, text TEXT NOT NULL, ts INTEGER NOT NULL)`);
      this.ctx.storage.sql.exec(
        `CREATE TABLE IF NOT EXISTS kv(
           k TEXT PRIMARY KEY, v TEXT NOT NULL, ts INTEGER NOT NULL)`);
    });
    // R79-7: keepalive "p"→"P" は auto-response＝課金されず・hibernation中のDOを起こさない
    // （この1行がWS常時接続を「タダで維持できる」根拠。素のonmessage応答にすると20:1課金＋毎回起床）
    this.ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("p", "P"));
  }

  // ---- R79-7 WebSocket（hibernation式） ----------------------------------
  // 掟: Room はインメモリ状態を1つも持たない。配信先は必ず ctx.getWebSockets(tag) から
  // 取る（hibernationを跨いで生存する唯一の手段）。ここにMapやSetを足したら負け。
  // 認証は Worker 側ゲートが済ませてから fetch を転送してくる（RPCメソッドと同じ信頼境界）。
  async fetch(request) {
    if ((request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
      return new Response("expected websocket", { status: 426 });
    }
    const url = new URL(request.url);
    const role = url.searchParams.get("role") === "agent" ? "agent" : "app";
    const pair = new WebSocketPair();
    const client = pair[0], server = pair[1];
    this.ctx.acceptWebSocket(server, [role]);
    // R79-8: Web Push の VAPID sub に使う自Origin。hibernation越しに要るが
    // インメモリ禁止の掟があるので kv に置く（サーバー導出値＝改竄面ではない）。
    // R79-9: site も同様に記憶（Room名=site。webSocketMessage の sync が
    // 「通知はsite=macのみ」の既存規則をWS経路でも守るための材料）
    // R80-C3: 同じ値の書き直しは rows written の純粋な無駄（upgrade毎に2行）。
    // DO SQLite rows written は無料枠 100,000/日＝**このプロダクトの真のボトルネック**なので、
    // 変化したときだけ書く。
    this._kvPutIfChanged("wsorigin", url.origin);
    this._kvPutIfChanged("wssite", url.searchParams.get("site") || "mac");
    // 接続直後に現在のstatusを1発（再接続時のフル再同期＝全部冪等・last-write-wins）
    if (role === "app") {
      try { server.send(this._statusFrame()); } catch (_) { /* 未確立でも次のframeで届く */ }
    }
    // ブラウザは要求したサブプロトコルのどれかが返らないと接続を落とす（RFC6455）。
    // bearer.<...> は選ばず常に aioffice.v1 をecho（Macはヘッダ認証＝無要求なら無echo）。
    const offered = (request.headers.get("Sec-WebSocket-Protocol") || "")
      .split(",").map((s) => s.trim());
    const headers = offered.includes("aioffice.v1")
      ? { "Sec-WebSocket-Protocol": "aioffice.v1" } : {};
    return new Response(null, { status: 101, webSocket: client, headers });
  }

  _statusFrame() {
    const s = this.getStatus();
    const agent = this._seenTs("agentseen");
    const now = Date.now();
    // R79-8.1: WS化でsyncは「変化時+240s heartbeat」だけ＝agentSeenAgoが180s閾値を跨ぎ
    // 偽stale（Mac生存中に「指示は届きません」）が出る。正直な生存信号は接続の有無。
    return JSON.stringify({ t: "status", json: s.json, ts: s.ts,
      agentOnline: this.ctx.getWebSockets("agent").length > 0,
      agentSeenAgo: agent == null ? null : Math.max(0, Math.floor((now - agent) / 1000)) });
  }

  // 扇形配信＝送信は無料。切れかけソケットへのsend失敗は握る（closeで自然に掃除される）
  _fan(tag, msg) {
    for (const ws of this.ctx.getWebSockets(tag)) {
      try { ws.send(msg); } catch (_) { /* 個別失敗は無視 */ }
    }
  }

  _kvPut(k, v) {
    this.ctx.storage.sql.exec(
      "INSERT INTO kv(k,v,ts) VALUES (?,?,?) " +
      "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts", k, v, Date.now());
  }

  _kvGet(k) {
    const r = this.ctx.storage.sql.exec("SELECT v FROM kv WHERE k=?", k).toArray();
    return r.length ? r[0].v : "";
  }

  // ── R80: 使用量の自己防衛 ────────────────────────────────────────────
  // Cloudflare 無料枠のうち、このプロダクトが最初に割るのは **DO SQLite rows written
  // （100,000行/日）**（実測監査 R80）。誰も見ていないまま枠を割ると中継が止まり、
  // ユーザーには「スマホが繋がらない」としか見えない。そこで
  //   ①1日分の書込行数を数える ②UIに出す ③閾値を越えたらMac側が自動で間引く
  // の3点で「気づけて、勝手に減速する」形にする。カウンタ自体の書込は1日1行に畳む。
  _today() {
    return Math.floor(Date.now() / 86400000);   // UTC日。境界の厳密さより安さを優先
  }

  /** 書込行数を加算（メモリを持たない掟のため kv に置く。加算自体も1行なので +1 して数える） */
  _bump(rows) {
    const day = this._today();
    const cur = this._kvGet("usage");
    let d = null;
    try { d = cur ? JSON.parse(cur) : null; } catch (_) { d = null; }
    if (!d || d.day !== day) d = { day, rows: 0 };
    d.rows += (rows | 0) + 1;                   // +1 = この書込自体
    this._kvPut("usage", JSON.stringify(d));
    return d;
  }

  /** 今日の使用量ビュー（無料枠に対する比率と、間引き段階）。読みだけ＝安い。 */
  usage() {
    const day = this._today();
    let d = null;
    try { d = JSON.parse(this._kvGet("usage") || "null"); } catch (_) { d = null; }
    if (!d || d.day !== day) d = { day, rows: 0 };
    const pct = Math.min(999, Math.round((d.rows / 100000) * 100));
    // 段階: 0=通常 / 1=50%超（控えめ）/ 2=80%超（最小限）。Mac側がこれを見て間隔を伸ばす。
    const level = pct >= 80 ? 2 : pct >= 50 ? 1 : 0;
    return { rows: d.rows, limit: 100000, pct, level };
  }

  _kvPutIfChanged(k, v) {
    if (this._kvGet(k) === v) return false;    // 読み(安い)で書き(高い)を節約する
    this._kvPut(k, v);
    return true;
  }

  async webSocketMessage(ws, message) {
    // "p" は auto-response が処理済み＝ここへ来ない。JSON以外は無視（プロトコル外は黙殺）
    if (typeof message !== "string") return;
    let d = null;
    try { d = JSON.parse(message); } catch (_) { return; }
    if (!d || typeof d !== "object") return;
    if (d.t === "status") {
      // WSのstatus要求は appseen を書かない（在席=接続数のメモリ判定へ移行・DB書込を増やさない）
      try { ws.send(this._statusFrame()); } catch (_) { /* 失敗はcloseに任せる */ }
      return;
    }
    // R79-8: Mac(agent)の1周を WS メッセージで（受信20:1課金＝HTTP /sync の1/20）。
    // 既存RPC sync() を呼んで返すだけ＝ack掟/❗エッジ/putStatus扇形配信がそのまま効く。
    // 役割はタグで固定: appソケットからのsyncは黙殺（スマホにMac権限を与えない）
    if (d.t === "sync") {
      if (!this.ctx.getTags(ws).includes("agent")) return;
      const office = (d.office && typeof d.office === "object") ? d.office : null;
      // R79-9: 通知はsite=macのみ（HTTP /sync と同じ規則）。miniの生pushで❗通知を焼くと
      // メインMacのマージ済みpushと二重通知になる
      const attnNow = (office && this.env.VAPID_JWK
        && (this._kvGet("wssite") || "mac") === "mac") ? computeAttnNow(office) : null;
      const r = this.sync({
        ackIds: Array.isArray(d.ackIds) ? d.ackIds : [],
        officeJson: office ? JSON.stringify(office) : null,
        attnNow,
      });
      let openclaw = null;
      if (d.wantOpenclaw === true) {
        try {
          const s = await this.env.ROOM.getByName("macmini").getStatus();
          if (s && s.json) openclaw = { json: s.json, ts: s.ts };
        } catch (_) { /* openclawはベストエフォート */ }
      }
      // 応答を先に返してから通知（Push送出でMacのtickを待たせない）
      try {
        ws.send(JSON.stringify({ t: "sync", ok: true, items: r.items, acked: r.acked,
          openclaw, appSeenAgo: r.appSeenAgo, appOnline: !!r.appOnline,
          usage: r.usage }));
      } catch (_) { /* 失敗はcloseに任せる */ }
      // R80-C8: Push送出を **await しない**。HTTP経路は ctx.waitUntil で Worker 側へ逃がして
      // いるのに、WS経路だけ DO 内で待っていた＝❗1件ごとに DO を数百ms active に固定していた
      // （hibernation設計の趣旨に反する非対称）。失敗はベストエフォートの掟どおり握る。
      if (attnNow && r.newly && r.newly.length && r.subs && r.subs.length) {
        const origin = this._kvGet("wsorigin");
        if (origin) {
          sendAttnPushes(this, this.env, r.newly, attnNow, r.subs, origin)
            .catch(() => { /* 通知はベストエフォート */ });
        }
      }
    }
  }

  async webSocketClose(ws, code) {
    try { ws.close(code, "bye"); } catch (_) { /* 既閉は無視 */ }
  }

  async webSocketError() { /* closeが後続する＝状態を持たないので何もしない */ }

  enqueue(session, text, ts) {
    this.ctx.storage.sql.exec(
      "INSERT INTO inbox(session,text,ts) VALUES (?,?,?)", session, text, ts);
    // R79-7 扇形配信フックその1: 指示が積まれたらMac(agent)を起こす（R79-8で受信側を実装。
    // sync()にはフックしない＝二重pushを構造的に防ぐ）
    this._fan("agent", '{"t":"wake"}');
    return this.ctx.storage.sql.exec("SELECT COUNT(*) AS n FROM inbox").one().n;
  }

  // peek: 消さずに先頭から最大PEEK_LIMIT件返す（ack されるまでキューに残る）
  peek() {
    return this.ctx.storage.sql.exec(
      "SELECT id,session,text,ts FROM inbox ORDER BY id LIMIT ?", PEEK_LIMIT).toArray();
  }

  // ack: 配達済みの id を削除（数値のみ・最大ACK_LIMIT件）
  ack(ids) {
    const clean = (Array.isArray(ids) ? ids : [])
      .filter((x) => Number.isInteger(x)).slice(0, ACK_LIMIT);
    if (!clean.length) return 0;
    const ph = clean.map(() => "?").join(",");
    this.ctx.storage.sql.exec(`DELETE FROM inbox WHERE id IN (${ph})`, ...clean);
    return clean.length;
  }

  putStatus(json, ts) {
    this.ctx.storage.sql.exec(
      "INSERT INTO kv(k,v,ts) VALUES ('status',?,?) " +
      "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts", json, ts);
    // R79-7 扇形配信フックその2: statusが変わった瞬間にスマホ(app)へpush（送信は無料）。
    // ここはMac側経路(/sync・POST /status)からしか呼ばれない＝直前にMacが生きている＝agentSeenAgo:0
    this._fan("app", JSON.stringify({ t: "status", json, ts, agentSeenAgo: 0,
      agentOnline: this.ctx.getWebSockets("agent").length > 0 }));
  }

  getStatus() {
    const r = this.ctx.storage.sql.exec("SELECT v,ts FROM kv WHERE k='status'").toArray();
    return r.length ? { json: r[0].v, ts: r[0].ts } : { json: "", ts: 0 };
  }

  // ---- P7 Web Push 購読台帳（既存kvテーブルに 'push:<id>' 行で同居・スキーマ無改変） ----
  putSub(id, json, ts) {
    const n = this.ctx.storage.sql.exec(
      "SELECT COUNT(*) AS n FROM kv WHERE k LIKE 'push:%'").one().n;
    // 上限10台（DO肥大ガード）。既存idの更新は常に許可
    const exists = this.ctx.storage.sql.exec(
      "SELECT 1 FROM kv WHERE k=?", "push:" + id).toArray().length;
    if (!exists && n >= 10) return false;
    this.ctx.storage.sql.exec(
      "INSERT INTO kv(k,v,ts) VALUES (?,?,?) " +
      "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts", "push:" + id, json, ts);
    return true;
  }

  delSub(id) {
    this.ctx.storage.sql.exec("DELETE FROM kv WHERE k=?", "push:" + id);
  }

  listSubs() {
    return this.ctx.storage.sql.exec(
      "SELECT k,v FROM kv WHERE k LIKE 'push:%'").toArray();
  }

  // ❗状態のエッジ検出用スナップショット（前回の attn セッション集合）
  getAttnState() {
    const r = this.ctx.storage.sql.exec("SELECT v FROM kv WHERE k='attnstate'").toArray();
    try { return r.length ? JSON.parse(r[0].v) : {}; } catch { return {}; }
  }

  putAttnState(obj, ts) {
    this.ctx.storage.sql.exec(
      "INSERT INTO kv(k,v,ts) VALUES ('attnstate',?,?) " +
      "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts", JSON.stringify(obj), ts);
  }

  // ---- R51 在席ハートビート（kv 'agentseen'/'appseen'・attnstateと同じ流儀＝スキーマ無改変） ----
  _touchSeen(k, ts) {
    this.ctx.storage.sql.exec(
      "INSERT INTO kv(k,v,ts) VALUES (?,?,?) " +
      "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts", k, "1", ts);
  }

  _seenTs(k) {
    const r = this.ctx.storage.sql.exec("SELECT ts FROM kv WHERE k=?", k).toArray();
    return r.length ? r[0].ts : null;
  }

  // R51: relay_agent の1周分を単一DO呼び出しに畳む。
  //   ackIds削除 → peek → (office在れば) putStatus＋❗エッジ検出（attnstate差分。Push送出は
  //   Worker側が戻り値 newly/subs で行う）→ agentseen=now（office=nullでも必ず）→ appseen読出し。
  // /pull peek→/ack削除の durability は ack()/peek() をそのまま呼ぶ＝方式無改変。
  sync(p) {
    p = (p && typeof p === "object") ? p : {};
    const now = Date.now();
    const acked = this.ack(p.ackIds);
    const items = this.peek();
    let newly = [], subs = [];
    if (typeof p.officeJson === "string") {
      this.putStatus(p.officeJson, now);
      if (p.attnNow && typeof p.attnNow === "object") {
        const prev = this.getAttnState();
        this.putAttnState(p.attnNow, now);
        newly = Object.keys(p.attnNow).filter((k) => !(k in prev));
        if (newly.length) subs = this.listSubs();
      }
    }
    this._touchSeen("agentseen", now);   // office=null（変化なし）でも「Mac側は生きている」を刻む
    // R79-7: WS接続中のスマホは「いま在席」＝リクエスト0円のメモリ判定（20秒毎のappseen書込を置換）。
    // HTTPポーリングへ退避中の端末は従来どおり appseen で見る（両経路の和が在席）
    const appOnline = this.ctx.getWebSockets("app").length > 0;
    const app = this._seenTs("appseen");
    const appSeenAgo = appOnline ? 0
      : (app == null ? null : Math.max(0, Math.floor((now - app) / 1000)));
    // R80: この周で書いた行数（status + attnstate + agentseen）を計上して返す。
    // Mac側は usage.level を見て自分のscan間隔を伸ばす＝**枠を割る前に自動で減速する**。
    const wrote = 1 + (typeof p.officeJson === "string" ? 1 : 0)
      + (p.attnNow && typeof p.attnNow === "object" ? 1 : 0);
    this._bump(wrote);
    return { acked, items, newly, subs, appSeenAgo, appOnline, usage: this.usage() };
  }

  // R51: PWA向け GET /status を単一DO呼び出しに（appseen=now 更新＋agentSeenAgo 添付）。
  // R79-8.1: HTTPポーリング退避中の端末にも agentOnline（Mac WS在席）を添える
  statusForApp() {
    const now = Date.now();
    this._touchSeen("appseen", now);
    const s = this.getStatus();
    const agent = this._seenTs("agentseen");
    return { json: s.json, ts: s.ts,
      agentOnline: this.ctx.getWebSockets("agent").length > 0,
      agentSeenAgo: agent == null ? null : Math.max(0, Math.floor((now - agent) / 1000)) };
  }
}

const jsonResp = (obj, code = 200) =>
  new Response(JSON.stringify(obj), {
    status: code,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });

async function readJson(request) {
  try { return await request.json(); } catch { return {}; }
}

// タイミング安全な文字列比較（トークン照合の side-channel を避ける）
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// R79-7: トークンの取り出し口を1つに。ブラウザのWebSocketはヘッダを付けられないので
// サブプロトコル ["aioffice.v1","bearer.<b64url(token)>"] を併用する（クエリ ?t= はログに
// 残るので不採用）。取り出した後は既存の isFull/isPost/isMini 判定へそのまま流す
// ＝403マトリクスは一行も変わらない。
function extractToken(request) {
  const auth = request.headers.get("Authorization") || "";
  if (auth.startsWith("Bearer ")) return auth.slice(7);
  for (const p of (request.headers.get("Sec-WebSocket-Protocol") || "").split(",")) {
    const v = p.trim();
    if (v.startsWith("bearer.")) {
      let b = v.slice(7).replace(/-/g, "+").replace(/_/g, "/");
      while (b.length % 4) b += "=";
      try { return atob(b); } catch (_) { return ""; }
    }
  }
  return "";
}

// 購読ID＝endpointのSHA-256先頭12バイト（endpoint自体をキーに使わない＝kv行キーの肥大回避）
async function subId(endpoint) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(d)].slice(0, 12).map((b) => b.toString(16).padStart(2, "0")).join("");
}

// R5: 購読部署は最大10件・各40文字。形式外は購読自体を拒まず「全て」に戻す。
function normalizeDepts(value) {
  if (!Array.isArray(value) || value.length > 10 ||
      !value.every((d) => typeof d === "string" && [...d].length <= 40)) return [];
  return value.slice();
}

// R5_PUSH_TARGETS_BEGIN: relay_e2e.sh がこの純関数だけを node で単体検証する。
function pushTargets(subs, dept) {
  return (Array.isArray(subs) ? subs : []).filter((row) => {
    let sub = row;
    try {
      if (row && typeof row.v === "string") sub = JSON.parse(row.v);
    } catch (_) { return false; }
    if (!sub || typeof sub !== "object") return false;
    const depts = Array.isArray(sub.depts) ? sub.depts : [];
    return depts.length === 0 || depts.includes(dept);
  });
}
// R5_PUSH_TARGETS_END

function attnDept(e) {
  // roster行は dept を持たず name（=プロジェクト/部署名）を持つ（R51）。
  const dept = String((e && e.dept) || (e && e.name) || "").trim();
  if (dept) return [...dept].slice(0, 40).join("");
  const disp = String((e && e.disp) || "").trim();
  return [...((disp && disp.split(/\s+/)[0]) || "")].slice(0, 40).join("");
}

// R51: ❗集合の材料は roster（1アバター=1プロジェクト）優先・無ければ employees（旧server後方互換）。
// キーは projectId 優先＝同一プロジェクトの複数セッションが同時❗でも1通知にデデュープされる。
function computeAttnNow(office) {
  const roster = office && office.roster;
  const list = (Array.isArray(roster) && roster.length ? roster
    : (office && office.employees)) || [];
  const now = {};
  for (const e of list) {
    if (e && (e.projectId || e.session) && ((e.approvalMin || 0) > 0 || e.question)) {
      now[String(e.projectId || e.session)] = {
        // R85-1: /rename のセッション名を優先（Push本文にも同じ名前を出す＝ユーザー裁定）
        disp: [...String(e.title || e.disp || e.session || "")].slice(0, 40).join(""),
        dept: attnDept(e),
      };
    }
  }
  return now;
}

// 新規❗遷移分（newly）を購読フィルタに掛けてWeb Push送出。通知本文は表示名のみ（掟）。
// 全経路 try/catch のベストエフォート＝本流(/status保存・/sync応答)を絶対に壊さない。
async function sendAttnPushes(room, env, newly, nowMap, subs, subContact) {
  try {
    let jwk;
    try { jwk = JSON.parse(env.VAPID_JWK); } catch { return; }
    // The edge map keeps only names/departments. Recover allowlisted ask
    // metadata from the status already saved by every caller, once per batch.
    const details = new Map();
    try {
      const status = await room.getStatus();
      const office = JSON.parse(status.json);
      const list = (Array.isArray(office.roster) && office.roster.length
        ? office.roster : office.employees) || [];
      for (const e of list) {
        if (e) details.set(String(e.projectId || e.session), e);
      }
    } catch (_) { /* Old status/rooms still get a generic approval notification. */ }
    // 同時に複数部署で❗が発生しても、購読対象外の表示名を本文へ混ぜない。
    const targets = new Map();
    for (const key of newly) {
      for (const row of pushTargets(subs, nowMap[key].dept)) {
        let target = targets.get(row.k);
        if (!target) {
          target = { row, items: [] };
          targets.set(row.k, target);
        }
        target.items.push({ ...details.get(key), ...nowMap[key] });
      }
    }
    for (const { row, items } of targets.values()) {
      try {
        const names = items.map((e) => e.disp);
        const head = names.slice(0, 3).join("・") + (names.length > 3 ? ` ほか${names.length - 3}件` : "");
        const first = items[0];
        const ask = first.ask || {};
        const tool = /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(ask.tool || "") ? ask.tool : "";
        const question = ask.kind === "question" || (!ask.kind && first.question);
        const kind = ask.kind === "permission" ? (tool ? `${tool} の許可` : "許可")
          : question ? "質問" : "承認";
        // Redaction strips ask.ts; the representative's numeric age survives
        // for both hooked questions and the legacy AskUserQuestion fallback.
        const lead = first.sessions?.find((s) => s.session === first.session) || first;
        const value = Number(question && lead.age != null ? lead.age / 60 : first.approvalMin);
        const minutes = Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
        const payload = { title: "🏢 AI Office",
          body: `❗ ${head} — ${kind}を待っています（${minutes}分）`, tag: "aioffice-attn" };
        const st = await sendWebPush(JSON.parse(row.v), payload, jwk, subContact);   // TTL=既定3600
        if (st === 404 || st === 410) await room.delSub(row.k.slice(5));   // 購読失効は台帳から掃除
      } catch (_) { /* 個別の送信失敗は握る（次の❗遷移で再送機会がある） */ }
    }
  } catch (_) { /* 通知はベストエフォート */ }
}

// P7: /status push のたびに ❗（承認/質問まち）への遷移を検出し、新規遷移分だけ Web Push を送る。
// - エッジ検出（前回スナップショットとの差分）＝❗が続く限り連打しない
// - 通知本文は表示名のみ（question本文は載せない＝ロック画面への露出を最小化）
// - 旧 POST /status 経路用。/sync は Room.sync 内でエッジ検出し sendAttnPushes だけ使う。
async function notifyAttn(room, env, office, subContact) {
  try {
    const now = computeAttnNow(office);
    const prev = await room.getAttnState();
    await room.putAttnState(now, Date.now());
    const newly = Object.keys(now).filter((s) => !(s in prev));
    if (!newly.length) return;
    const subs = await room.listSubs();
    if (!subs.length) return;
    await sendAttnPushes(room, env, newly, now, subs, subContact);
  } catch (_) { /* 通知はベストエフォート */ }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    if (path === "/" || path === "") {
      return new Response("ai-office-relay ok\n", {
        headers: { "Cache-Control": "no-store" } });
    }

    // PWAアプリシェル（P3・無認証で配信）。シェルに秘密は無く、資格情報は QR/リンクの
    // fragment→localStorage 由来。データ面(/instruct,/status)は下の Bearer ゲートで守る。
    // R79: no-store をやめる。旧実装はアプリを開くたびシェル3本（約110KB）を必ず再取得し、
    // ゾーンキャッシュも無い（workers_dev）ため全部がWorker実行だった。
    // ETag + must-revalidate なら「変わっていなければ304（数百バイト）」で済む。
    // 秘密は含まない（資格情報はlocalStorage・APP_HTMLはシェルのみ）ので公開キャッシュ可。
    const APP_ETAG = 'W/"app-' + APP_BUILD + '"';
    // R82-sec(F7): 承認/停止/▶実行ボタンを持つ画面なのでクリックジャッキングを塞ぐ。
    // PWAはインライン onclick を多用するため script-src は縛らない（S1でXSSの発生源は
    // 既に除去済み）。ここは機能に影響しない frame/object/base の封じ込めと nosniff だけ。
    const SEC_HEADERS = {
      "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
      "X-Frame-Options": "DENY",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    };
    const shell = (body, type) => {
      if (request.headers.get("If-None-Match") === APP_ETAG) {
        return new Response(null, {
          status: 304,
          headers: { ETag: APP_ETAG, "Cache-Control": "public, max-age=0, must-revalidate",
                     ...SEC_HEADERS },
        });
      }
      return new Response(body, {
        headers: { "Content-Type": type, "Cache-Control": "public, max-age=0, must-revalidate",
                   ETag: APP_ETAG, ...SEC_HEADERS },
      });
    };
    if (method === "GET" && (path === "/app" || path === "/app/")) {
      return shell(APP_HTML, "text/html; charset=utf-8");
    }
    if (method === "GET" && path === "/app/sw.js") {
      return shell(SW_JS, "text/javascript; charset=utf-8");
    }
    if (method === "GET" && path === "/app/manifest.webmanifest") {
      return shell(MANIFEST, "application/manifest+json; charset=utf-8");
    }
    // R79: /app/sprite/ は廃止（アバターはモノグラム＝画像ゼロ）。
    // 旧キャッシュのSW/HTMLが要求してきても 404 を短期キャッシュで返して静かに枯らす。
    if (method === "GET" && path.startsWith("/app/sprite/")) {
      return new Response("gone", { status: 404, headers: { "Cache-Control": "public, max-age=3600" } });
    }
    if (method === "GET" && path.startsWith("/ui/")) {
      // R79: immutable(1年) をやめる。3Dシーンのコードは**修正が入る生きたコード**であり、
      // 1年キャッシュだと boot3d.js の不具合修正が既存端末へ最大1年届かない
      // （R77→R78 の focus() 追加が実際に届かない状態だった）。
      // no-cache + 内容ハッシュETag ＝ 毎回検証させるが、変わっていなければ 304（数百バイト）。
      const REVALIDATE = "public, max-age=0, must-revalidate";
      const etag = 'W/"ui-' + UI_BUILD + '"';
      if (request.headers.get("If-None-Match") === etag) {
        return new Response(null, { status: 304, headers: { ETag: etag, "Cache-Control": REVALIDATE } });
      }
      const src = (Object.prototype.hasOwnProperty.call(MODULES, path) && MODULES[path]) || "";
      if (src) {
        return new Response(src, {
          headers: { "Content-Type": "text/javascript; charset=utf-8",
                     "Cache-Control": REVALIDATE, ETag: etag },
        });
      }
      // 3Dシーンが URL で読むテクスチャ（importでは辿れないので別マップ）
      const asset = (Object.prototype.hasOwnProperty.call(ASSETS, path) && ASSETS[path]) || null;
      if (asset) {
        return new Response(spriteBytes(asset[1]), {
          headers: { "Content-Type": asset[0], "Cache-Control": REVALIDATE, ETag: etag },
        });
      }
      return new Response("not found", { status: 404 });
    }

    // 認証（/ ・/app* 以外は全て Bearer 必須）。RELAY_POST_TOKEN=OpenClaw用の限定トークンで
    // POST /instruct と GET /status のみ許可（未設定なら isPost 恒偽＝従来と完全同一挙動＝後方互換）。
    // safeEqual は非string側で false を返すので env 未定義でも安全。
    // 誤設定で限定トークンがフル権限へ無言昇格するのを防ぐ（fail-closed）
    if (env.RELAY_TOKEN && env.RELAY_POST_TOKEN && safeEqual(env.RELAY_TOKEN, env.RELAY_POST_TOKEN)) {
      return jsonResp({ ok: false, error: "misconfig: RELAY_TOKEN == RELAY_POST_TOKEN" }, 500);
    }
    // R42.4: mini用限定トークンも同じfail-closed（重複＝無言昇格を500で止める）
    if (env.RELAY_MACMINI_TOKEN && ((env.RELAY_TOKEN && safeEqual(env.RELAY_TOKEN, env.RELAY_MACMINI_TOKEN)) ||
        (env.RELAY_POST_TOKEN && safeEqual(env.RELAY_POST_TOKEN, env.RELAY_MACMINI_TOKEN)))) {
      return jsonResp({ ok: false, error: "misconfig: RELAY_MACMINI_TOKEN duplicates another token" }, 500);
    }
    // R42.4 site分割: site毎に別DO（attnstate/statusが自動分離）。既定=mac（後方互換）。
    const siteParam = url.searchParams.get("site");
    const site = siteParam == null ? "mac"
      : (/^[a-z0-9-]{1,16}$/.test(siteParam) ? siteParam : null);
    if (!site) {
      return jsonResp({ ok: false, error: "bad site" }, 400);
    }
    const token = extractToken(request);   // R79-7: ヘッダ/WSサブプロトコルを同じ判定へ
    const isFull = !!env.RELAY_TOKEN && safeEqual(token, env.RELAY_TOKEN);
    const isPost = !!env.RELAY_POST_TOKEN && safeEqual(token, env.RELAY_POST_TOKEN);
    const isMini = !!env.RELAY_MACMINI_TOKEN && safeEqual(token, env.RELAY_MACMINI_TOKEN);
    if (!isFull && !isPost && !isMini) {
      return jsonResp({ ok: false, error: "unauthorized" }, 401);
    }
    if (isPost && !isFull &&
        !((method === "POST" && path === "/instruct" && site === "mac") ||
          (method === "GET" && path === "/status" && site === "mac"))) {
      return jsonResp({ ok: false, error: "forbidden" }, 403);   // /pull /ack POST/status は限定トークン不可
    }
    // miniトークンは「mac以外のsite」限定: POST /status（R42.4片方向）に加え
    // R42.5で GET /pull・POST /ack を追加＝oc-宛指示の受け取り（peek/ack）。
    // R79-9で GET /ws を追加（mini 2本のWS化＝pull/pushポーリングの置換）。
    // /instruct・/sync・mac本体status には引き続き触れない（漏れてもmac面は無傷）
    if (isMini && !isFull &&
        !(site !== "mac" &&
          ((method === "POST" && path === "/status") ||
           (method === "GET" && path === "/pull") ||
           (method === "POST" && path === "/ack") ||
           (method === "GET" && path === "/ws")))) {
      return jsonResp({ ok: false, error: "forbidden" }, 403);
    }

    const room = env.ROOM.getByName(site);

    // R79-7: WebSocket常時接続（フルBearerのみ＝限定トークンは上の許可リスト外で既に403）。
    // アップグレードはRoom DOのfetchへ転送＝hibernation式でDOに直結する。
    if (path === "/ws") {
      if ((request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
        return jsonResp({ ok: false, error: "expected websocket" }, 426);
      }
      return room.fetch(request);
    }

    if (method === "POST" && path === "/instruct") {
      // P3: 署名封筒を受理。Worker は署名鍵を持たない＝sig は検証せず、形式プレフィルタのみ。
      // 真正性(HMAC)の検証は Mac 側 relay_agent が行う（Bearer が漏れても偽造不可）。
      const b = await readJson(request);
      // 署名対象(session/text)は verbatim で扱う。ここで .trim() すると署名時のバイト列と
      // ズレて Mac 側 verify_envelope が bad-sig で無言ドロップする（署名済みフィールドを改変しない）。
      const session = String(b.session || "");
      const text = String(b.text || "");
      const deviceId = String(b.device_id || "");
      const nonce = String(b.nonce || "");
      const sig = String(b.sig || "");
      const alg = String(b.alg || "");
      const ts = b.ts;
      if (!/^[a-zA-Z0-9-]{8,64}$/.test(session)) return jsonResp({ ok: false, error: "bad session" }, 400);
      // 長さは Python 側 len(text)（コードポイント）に合わせる。text.length は UTF-16 単位で
      // 絵文字等の astral 文字を2カウントしてしまい、正当な指示を誤って 400 にする。
      if (!text || [...text].length > 4000) return jsonResp({ ok: false, error: "bad text" }, 400);
      if (!/^d_[0-9a-f]{12}$/.test(deviceId)) return jsonResp({ ok: false, error: "bad device_id" }, 400);
      if (!/^[0-9a-f]{32}$/.test(nonce)) return jsonResp({ ok: false, error: "bad nonce" }, 400);
      if (!/^[0-9a-f]{64}$/.test(sig)) return jsonResp({ ok: false, error: "bad sig" }, 400);
      if (alg !== "HS256") return jsonResp({ ok: false, error: "bad alg" }, 400);
      if (!Number.isInteger(ts)) return jsonResp({ ok: false, error: "bad ts" }, 400);
      // 署名封筒JSONを text カラムに内包（DOスキーマは無改変）。余分フィールドは落として正規化
      const envelope = JSON.stringify({ v: 1, device_id: deviceId, session, text, ts, nonce, alg: "HS256", sig });
      const queued = await room.enqueue(session, envelope, Date.now());
      return jsonResp({ ok: true, queued });
    }

    if (method === "GET" && path === "/pull") {
      const items = await room.peek();
      return jsonResp({ ok: true, items });
    }

    if (method === "POST" && path === "/ack") {
      const b = await readJson(request);
      const acked = await room.ack(b.ids);
      return jsonResp({ ok: true, acked });
    }

    // R51: relay_agent の1周（pull+ack+status）を1リクエスト・単一DO呼び出しに統合。
    // 認証はフルBearerのみ（限定トークンは上のゲートで既に403＝POST /instruct・GET /status限定の原則を崩さない）。
    if (method === "POST" && path === "/sync") {
      const b = await readJson(request);
      const office = (b.office && typeof b.office === "object") ? b.office : null;
      const ackIds = Array.isArray(b.ackIds) ? b.ackIds : [];
      // ❗エッジ検出の材料はWorker側で純関数計算し、prev差分はDO内（sync）で行う。
      // R42.4: 通知はsite=macのみ（miniの生pushとメインのマージ済みpushで二重通知しない）
      const attnNow = (office && env.VAPID_JWK && site === "mac") ? computeAttnNow(office) : null;
      const r = await room.sync({
        ackIds,
        officeJson: office ? JSON.stringify(office) : null,
        attnNow,
      });
      if (attnNow && r.newly && r.newly.length && r.subs && r.subs.length) {
        // Push送出はレスポンスをブロックしない（relay_agentのtickを遅らせない）
        ctx.waitUntil(sendAttnPushes(room, env, r.newly, attnNow, r.subs, url.origin));
      }
      let openclaw = null;
      if (b.wantOpenclaw === true) {
        // 既存の GET /status?site=macmini と同じ内容（該当kvが無ければ null）
        try {
          const s = await env.ROOM.getByName("macmini").getStatus();
          if (s && s.json) openclaw = { json: s.json, ts: s.ts };
        } catch (_) { /* openclawはベストエフォート＝本流を壊さない */ }
      }
      return jsonResp({ ok: true, items: r.items, acked: r.acked, openclaw,
        appSeenAgo: r.appSeenAgo, appOnline: !!r.appOnline, usage: r.usage });
    }

    if (method === "POST" && path === "/status") {
      const b = await readJson(request);
      const office = b.office ?? b;
      await room.putStatus(JSON.stringify(office), Date.now());
      // P7: ❗遷移のWeb Pushはレスポンスをブロックしない（relay_agentの5秒tickを遅らせない）。
      // R42.4: 通知はsite=macのみ（miniの生pushとメインのマージ済みpushで二重通知しない）
      if (env.VAPID_JWK && site === "mac") ctx.waitUntil(notifyAttn(room, env, office, url.origin));
      return jsonResp({ ok: true });
    }

    if (method === "GET" && path === "/status") {
      // R51: PWA在席検知。フルBearer・site無しクエリ時のみ appseen=now を刻み agentSeenAgo を添える。
      // site付き読取（OpenClawアグリゲータ等）や限定トークンは在席と誤認しない＝従来応答のまま。
      if (isFull && siteParam === null) {
        const s = await room.statusForApp();
        return jsonResp({ ok: true, ...s });
      }
      const s = await room.getStatus();
      return jsonResp({ ok: true, ...s });
    }

    // ---- P7 Web Push（フルBearerのみ・限定POST_TOKENは上のゲートで既に403） ----
    if (path.startsWith("/push/")) {
      let jwk = null;
      try { jwk = env.VAPID_JWK ? JSON.parse(env.VAPID_JWK) : null; } catch { jwk = null; }
      if (!jwk || !jwk.d || !jwk.x || !jwk.y) {
        return jsonResp({ ok: false, error: "VAPID未設定（openssl等で鍵生成→ wrangler secret put VAPID_JWK）" }, 503);
      }
      if (method === "GET" && path === "/push/vapid") {
        return jsonResp({ ok: true, key: b64u(jwkToRawPub(jwk)) });
      }
      if (method === "POST" && path === "/push/subscribe") {
        const b = await readJson(request);
        const s = b.subscription || b;
        const depts = normalizeDepts(b.depts);
        const endpoint = String((s && s.endpoint) || "");
        const p256dh = String((s && s.keys && s.keys.p256dh) || "");
        const authKey = String((s && s.keys && s.keys.auth) || "");
        if (!/^https:\/\//.test(endpoint) || endpoint.length > 1024) return jsonResp({ ok: false, error: "bad endpoint" }, 400);
        // p256dh=65バイト(b64u 87字)・auth=16バイト(b64u 22字)。緩めの幅で形式だけ固定
        if (!/^[A-Za-z0-9_-]{80,90}$/.test(p256dh)) return jsonResp({ ok: false, error: "bad p256dh" }, 400);
        if (!/^[A-Za-z0-9_-]{16,32}$/.test(authKey)) return jsonResp({ ok: false, error: "bad auth" }, 400);
        const stored = await room.putSub(await subId(endpoint),
          JSON.stringify({ endpoint, keys: { p256dh, auth: authKey }, depts }), Date.now());
        return stored ? jsonResp({ ok: true }) : jsonResp({ ok: false, error: "購読上限(10台)・不要な端末をunsubscribeしてから" }, 400);
      }
      if (method === "POST" && path === "/push/unsubscribe") {
        const b = await readJson(request);
        await room.delSub(await subId(String(b.endpoint || "")));
        return jsonResp({ ok: true });
      }
      if (method === "GET" && path === "/push/subs") {
        const subs = await room.listSubs();
        const safe = subs.map((row) => {
          try { return { depts: normalizeDepts(JSON.parse(row.v).depts) }; }
          catch (_) { return { depts: [] }; }
        });
        return jsonResp({ ok: true, count: subs.length, subs: safe });   // endpointは秘匿
      }
      if (method === "POST" && path === "/push/test") {
        const subs = await room.listSubs();
        let sent = 0, gone = 0;
        for (const row of subs) {
          try {
            const st = await sendWebPush(JSON.parse(row.v),
              { title: "🏢 AI Office", body: "テスト通知（Web Push配線OK）", tag: "aioffice-test" }, jwk, url.origin, 60);
            if (st === 404 || st === 410) { await room.delSub(row.k.slice(5)); gone++; }
            else sent++;
          } catch (_) { /* 個別失敗は継続 */ }
        }
        return jsonResp({ ok: true, sent, gone, total: subs.length });
      }
      return jsonResp({ ok: false, error: "not found" }, 404);
    }

    return jsonResp({ ok: false, error: "not found" }, 404);
  },
};

// ===== P3 スマホPWA（簡易ビュー先行） =====================================
// 自己完結の素HTML1枚。creds は QR/リンクの #fragment→localStorage 由来（サーバに秘密は無い）。
// 署名は WebCrypto。下の sign() の canonical は server/office_server.py の _canonical と厳密一致
// させること。ズレ検知は tests/js_sign_kat.mjs が本ファイルの canonical リテラルを直接読んで
// 期待値とバイト一致を検査する（relay_e2e / RUN_RELAY で実行）＝変えたら KAT が破れる。
const MANIFEST = JSON.stringify({
  name: "せなお AI Office",
  short_name: "AI Office",
  start_url: "/app",
  display: "standalone",
  background_color: "#23213a",
  theme_color: "#23213a",
  icons: [{
    src: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Crect width='100' height='100' fill='%23241f18'/%3E%3Ctext y='74' x='50' font-size='64' text-anchor='middle'%3E%F0%9F%8F%A2%3C/text%3E%3C/svg%3E",
    sizes: "any", type: "image/svg+xml",
  }],
});

const SW_JS = [
  '// AI Office PWA 最小SW v2: シェルは素通し、/status と /instruct は必ずネットワーク（network-only）。',
  '// P7: push受信で通知表示・タップで /app へ（iOSはホーム画面追加のPWAのみ通知可）。',
  'self.addEventListener("install", function(e){ self.skipWaiting(); });',
  'self.addEventListener("activate", function(e){ e.waitUntil(self.clients.claim()); });',
  'self.addEventListener("fetch", function(e){ /* データはキャッシュしない＝常に最新 */ });',
  'self.addEventListener("push", function(e){',
  '  var d = {}; try { d = e.data ? e.data.json() : {}; } catch (_) {}',
  '  // R51: 開いているPWAへ即時pollを促す（20秒間隔の谷間を❗が待たない）',
  '  e.waitUntil(Promise.all([',
  '    self.registration.showNotification(d.title || "🏢 AI Office",',
  '      { body: d.body || "", tag: d.tag || "aioffice", data: d }),',
  '    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function(cs){',
  '      for (var i = 0; i < cs.length; i++) { try { cs[i].postMessage({ type: "aioffice-poll" }); } catch (_) {} }',
  '    })]));',
  '});',
  'self.addEventListener("notificationclick", function(e){',
  '  e.notification.close();',
  '  e.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(function(cs){',
  '    for (var i = 0; i < cs.length; i++) { if (cs[i].url.indexOf("/app") >= 0) return cs[i].focus(); }',
  '    return clients.openWindow("/app");',
  '  }));',
  '});',
].join("\n");


// R65: 「今何してます?」一言要約（R60のPWA追随）。**正本は ui/core/world.js の
// tidyActivity/activityGloss**＝ロジックを変えるときは両方直す。同期ズレは
// tests/gloss_parity.mjs（relay_e2e ▶node節）が同一入力→同一出力で機械検知する。
// PWA_GLOSS_BEGIN
function tidyActivityPWA(s, max) {
  max = max || 60;
  let t = String(s == null ? "" : s).replace(/[`*]+/g, "");
  t = t.replace(/(^|\s)[#>]{1,3}\s+/g, "$1");
  t = t.replace(/(^|[\s（(「\[])((?:[^\s／/（）()「」\[\]]+\/){1,}[^\s（）()「」\[\]]+)/g,
    (m, pre, path) => path.includes("://") ? m : pre + path.split("/").pop());
  for (const [o, c] of [["（", "）"], ["(", ")"], ["「", "」"], ["[", "]"]]) {
    let depth = 0;
    let firstOpen = -1;
    for (let i = 0; i < t.length; i++) {
      if (t[i] === o) {
        if (depth === 0) firstOpen = i;
        depth += 1;
      } else if (t[i] === c) {
        depth = Math.max(0, depth - 1);
        if (depth === 0) firstOpen = -1;
      }
    }
    if (depth > 0 && firstOpen >= 0) t = t.slice(0, firstOpen);
  }
  t = t.replace(/\s+/g, " ").trim();
  if ([...t].length > max) t = [...t].slice(0, max - 1).join("").trimEnd() + "…";
  return t;
}

function activityGlossPWA(a, lang) {
  if (!a) return "";
  const GLOSS = {
    test: { ja: "🧪 テストを実行中", en: "🧪 Running tests" },
    ship: { ja: "📦 変更をコミット/反映中", en: "📦 Shipping changes" },
    build: { ja: "🔧 ビルド/セットアップ中", en: "🔧 Building & setup" },
    code: { ja: "✍️ コードを編集中", en: "✍️ Writing code" },
    docs: { ja: "📝 ドキュメントを執筆中", en: "📝 Writing docs" },
    write: { ja: "📝 文章を執筆中", en: "📝 Writing" },
    research: { ja: "🔎 調査・読み込み中", en: "🔎 Researching" },
    think: { ja: "🤔 次の一手を考え中", en: "🤔 Thinking it through" },
    report: { ja: "✅ 結果を報告中", en: "✅ Reporting results" },
    run: { ja: "⚙️ 処理を実行中", en: "⚙️ Running a task" },
    waiting: { ja: "⏳ 次の指示を待っています", en: "⏳ Waiting for input" },
    resting: { ja: "☕ ひと休み中", en: "☕ Taking a break" },
  };
  const CODE_EXT = /\.(py|js|mjs|ts|tsx|jsx|css|html|sh|json|yml|yaml|toml|swift|rs|go|c|h|cpp)\b/i;
  const L = (key) => (GLOSS[key] ? GLOSS[key][lang === "en" ? "en" : "ja"] : "");
  const now = Array.isArray(a.work && a.work.now)
    ? a.work.now.find((s) => s && s.trim()) : "";
  if (now) return "📋 " + tidyActivityPWA(now, 42);
  if (a.state === "resting") return L("resting");
  const verb = String(a.verb || "").trim();
  const raw = (verb + " " + (a.target || "")).trim();
  if (a.kind === "think" || /考え中|Thinking/i.test(verb)) return L("think");
  if (/指示待ち|Waiting/i.test(verb)) return L("waiting");
  if (/報告中|Reporting|Replying|応答中/i.test(verb)) return L("report");
  if (/調査中|Reading|Searching|検索中/i.test(verb)) return L("research");
  const target = String(a.target || "");
  if (/実行中|Running/i.test(verb)) {
    if (/verify|pytest|unittest|node --test|\btest\b|spec|smoke/i.test(target)) return L("test");
    if (/git |commit|push|merge|rebase|deploy/i.test(target)) return L("ship");
    if (/npm|pip|install|build|make|brew/i.test(target)) return L("build");
    return L("run");
  }
  if (/編集中|Editing/i.test(verb)) {
    if (/\.md\b|readme|docs?\//i.test(target)) return L("docs");
    if (CODE_EXT.test(target)) return L("code");
    return L("code");
  }
  if (/執筆中|Writing/i.test(verb)) {
    return /\.md\b|readme/i.test(target) ? L("docs") : L("write");
  }
  const tidied = tidyActivityPWA(raw, 42);
  return tidied || (a.state === "working" ? L("run") : L("waiting"));
}
// PWA_GLOSS_END
const PWA_GLOSS_SOURCE = tidyActivityPWA.toString() + "\n" + activityGlossPWA.toString();

// R79: PWAシェルの版ID（起動時に1回だけ計算）。APP_HTML は生成モジュールから import。
// SW_JS / MANIFEST の定義後に算出する。
// モジュール束の UI_BUILD とは別物: アプリだけ直したときにも必ず版が変わる必要がある。
const APP_BUILD = _fnv1a(APP_HTML) + "-" + _fnv1a(SW_JS + MANIFEST);
