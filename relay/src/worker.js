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
        disp: [...String(e.disp || e.session || "")].slice(0, 40).join(""),
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
    // 同時に複数部署で❗が発生しても、購読対象外の表示名を本文へ混ぜない。
    const targets = new Map();
    for (const key of newly) {
      for (const row of pushTargets(subs, nowMap[key].dept)) {
        let target = targets.get(row.k);
        if (!target) {
          target = { row, names: [] };
          targets.set(row.k, target);
        }
        target.names.push(nowMap[key].disp);
      }
    }
    for (const { row, names } of targets.values()) {
      try {
        const head = names.slice(0, 3).join("・") + (names.length > 3 ? ` ほか${names.length - 3}件` : "");
        const payload = { title: "🏢 AI Office", body: `❗ ${head} が承認/質問まち`, tag: "aioffice-attn" };
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

const APP_HTML = "<!doctype html><html lang=ja><head>" +
'<meta charset=utf-8>' +
'<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1,user-scalable=no">' +
'<meta name=apple-mobile-web-app-capable content=yes>' +
'<meta name=apple-mobile-web-app-status-bar-style content=default>' +
'<meta name=apple-mobile-web-app-title content="AI Office">' +
'<meta name=theme-color content="rgba(255,255,255,.86)">' +
'<link rel=manifest href="/app/manifest.webmanifest">' +
'<link rel=apple-touch-icon href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'%3E%3Crect width=\'100\' height=\'100\' fill=\'%23241f18\'/%3E%3Ctext y=\'74\' x=\'50\' font-size=\'64\' text-anchor=\'middle\'%3E%F0%9F%8F%A2%3C/text%3E%3C/svg%3E">' +
'<title>AI Office</title>' +
'<style>' +
'*{box-sizing:border-box;margin:0;padding:0}' +
// R78: スマホのクロームをデスクトップのガラスHUDと同じ言語へ（3Dシーンにベージュのチップが乗る不一致の解消）。
// 変数名は据え置き＝既存ルールが自動追随。意味色（承認=緑/保留=琥珀/危険=赤）は保持し、
// 中立面と選択状態だけを紫青の寒色系へ寄せる（ui/iso/style.css の :root と同値）。
':root{--wood:#e7e8f8;--ink:#23213a;--paper:rgba(255,255,255,.82);--paper-solid:#ffffff;--line:rgba(96,82,170,.14);--accent:#7c5cff;--accent-2:#4f8dff;--sage:#22c55e;--sage-d:#15803d;--sage-l:#86efac;--amber:#f5a524;--alert:#e0538a;--danger:#b4436b;--muted:#6c6890;--sh:rgba(64,52,140,.14)}' +
'@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}' +
'body{background:radial-gradient(90% 60% at 18% 4%,rgba(124,92,255,.14) 0%,rgba(124,92,255,0) 55%),radial-gradient(80% 50% at 88% 92%,rgba(79,141,255,.12) 0%,rgba(79,141,255,0) 58%),linear-gradient(165deg,#fbfaff 0%,#f1f0fc 52%,#e7e8f8 100%);color:var(--ink);font-family:-apple-system,"Hiragino Sans",system-ui,sans-serif;font-size:16px;padding:0 0 calc(78px + env(safe-area-inset-bottom));-webkit-tap-highlight-color:transparent}' +
'main{padding:0;max-width:640px;margin:0 auto}' +
'.card{position:relative;background:rgba(255,255,255,.86);border:1px solid rgba(96,82,170,.14);border-radius:12px;padding:12px 14px;margin-bottom:10px;box-shadow:0 1px 2px rgba(40,32,18,.06)}' +
'.card:active{background:#efedfb}' +
'.card.alert{border-color:rgba(224,83,138,.55);background:#fdf2f7}' +
'.card.pend{border-color:#b9791a;background:#fbf6ea}' +
'.card .nm{font-weight:800;font-size:15px}' +
'.card .st{font-size:13px;color:#4a4670;margin-top:3px;display:flex;align-items:center;gap:6px}' +
'.dot{width:9px;height:9px;border-radius:50%;flex:none;background:#6c6890}' +
'.dot.working{background:#5f9b78}.dot.waiting{background:#c99a3e}.dot.resting{background:#b0a693}' +
'.card .q{font-size:12.5px;color:var(--danger);margin-top:5px}' +
'.card .meta{display:flex;align-items:center;gap:9px}' +
'.mono{--asz:36px;width:var(--asz);height:var(--asz);border-radius:999px;flex:none;display:inline-flex;align-items:center;justify-content:center;font-weight:800;font-size:calc(var(--asz)*.42);line-height:1;color:var(--ink);background:rgba(124,92,255,.10);border:2px solid #b9b5d6;box-sizing:border-box}' +
'.mono.st-working{border-color:var(--sage)}' +
'.mono.st-waiting{border-color:var(--amber)}' +
'.mono.ext{border-color:#5aa2ff}' +
'.mono.attn{border-color:var(--alert)}' +
'body.th-dark .mono{background:rgba(124,92,255,.22);color:#e8e6f6}' +
'.card .meta .nm{flex:1;min-width:0}' +
'.card .age{font-size:11px;color:#6b6252;font-weight:700;flex:none;white-space:nowrap}' +
'.card .age.fresh{color:#2f6f68}' +
/* R51: 配達往復チップ（📨 queued → ✓ delivered）と roster セッション内訳行。
   ★.dchip の琥珀は **保留を意味する色**（規約: 承認=緑/保留=琥珀/危険=赤）なので
   ガラス化の対象外。中立面と混同して掃除しないこと（R80） */
'.dchip{flex:none;font-size:10px;font-weight:800;color:#8a5a10;background:#fbf6ea;border:1px solid #e2d0a8;border-radius:999px;padding:2px 7px;white-space:nowrap}' +
'.dchip.ok{color:#2f6f68;background:#eef5ef;border-color:#cfe3d4}' +
'body.th-dark .dchip{background:#2e2a1c;border-color:#6a5a2e;color:#e8bd69}' +
'body.th-dark .dchip.ok{color:#9ee6bb;background:#22301f;border-color:#3f6d4f}' +
'.sessrows{display:flex;flex-direction:column;gap:4px;margin:2px 0 6px}' +
'.sessrow{display:flex;align-items:center;gap:7px;font-size:12px;color:#4a4670;font-weight:700}' +
'.sessrow .sessid{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#6b6252}' +
'body.th-dark .sessrow{color:#c9c5e6}body.th-dark .sessrow .sessid{color:#8f8ab5}' +
'.feed{margin-top:7px;border-top:1px dashed rgba(96,82,170,.10);padding-top:6px;display:flex;flex-direction:column;gap:2px}' +
'.feedline{font-size:12px;color:#6b6252;line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
'.sheet .sec{font-size:11px;font-weight:800;color:#6c6890;letter-spacing:.04em;margin:13px 0 5px}' +
'.sheet .wk-work{border-top:1px dashed rgba(96,82,170,.10);padding:8px 0 3px}' +
'.sheet .wk-title{font-size:13px;font-weight:800;color:var(--ink);margin-bottom:4px}' +
'.sheet .wk-row{display:flex;gap:7px;font-size:13px;line-height:1.65}' +
'.sheet .wk-label{flex:none;width:45px;color:#6c6890;font-weight:700}' +
'.sheet .wk-items{min-width:0;display:flex;flex-direction:column}' +
'.sheet .wk-item{word-break:break-word;color:#4a4236}' +
'.sheet .wk-now .wk-item{font-weight:800;color:#23213a}' +
'body.th-dark .sheet .wk-work{border-color:#2c2a48}body.th-dark .sheet .wk-title{color:#e8bd69}' +
'body.th-dark .sheet .wk-label{color:#8f8ab5}body.th-dark .sheet .wk-item{color:#c9c5e6}' +
'body.th-dark .sheet .wk-now .wk-item{color:#f2f0ff}' +
/* 最近の動き=シートの主役（「何をしているか分からない」FB対応: 大きく・多く・スクロール可） */
'.sheet .feedbox{background:rgba(255,255,255,.60);border:1px solid rgba(96,82,170,.10);border-radius:9px;padding:11px 13px;display:flex;flex-direction:column;gap:7px;max-height:46vh;overflow-y:auto}' +
'.sheet .feedbox .feedline{white-space:normal;font-size:13.5px;color:#4a4670;line-height:1.7}' +
'.sheet .feedbox .feedline:first-child{color:#23213a;font-weight:800}' +
'.sheet .said{background:rgba(255,255,255,.60);border:1px solid rgba(96,82,170,.10);border-radius:9px;padding:9px 11px;font-size:12.5px;color:#4a4670;line-height:1.65;white-space:pre-wrap;max-height:34vh;overflow:auto}' +
'.sheet .saidq{background:#fdf3e2;border:1.5px solid #d9a044;font-size:14px;font-weight:700;color:#23213a}' +
'body.th-dark .sheet .saidq{background:#3a2c15;border-color:#b9791a;color:#ffe3b0}' +
'.empty{color:#6c6890;text-align:center;padding:40px 0}' +
'.setup{max-width:520px;margin:0 auto;padding:16px}' +
'.setup h2{font-size:17px;margin-bottom:8px}.setup p{color:#4a4670;font-size:14px;line-height:1.7;margin-bottom:10px}' +
'textarea,input{width:100%;border:1px solid rgba(96,82,170,.14);border-radius:9px;padding:10px;font-size:15px;font-family:inherit;background:rgba(255,255,255,.86)}' +
/* 色は状態を意味する（PC掟と同一）: 送信/承認=セージ・危険(停止)=赤・中立=ニュートラル */
'button{width:100%;border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:800;color:#fff;background:var(--sage);margin-top:8px;font-family:inherit;transition:transform .1s,filter .1s;min-height:44px}' +
'button.g{background:var(--sage)}button.r{background:var(--danger)}button.sub{background:#eeecf9;color:#514d78;border:1px solid var(--line)}' +
'button:active{transform:translateY(1px);filter:brightness(.95)}button:disabled{opacity:.5;pointer-events:none}' +
'.hidden{display:none}' +
'#sheetwrap{position:fixed;inset:0;z-index:90;pointer-events:none}' +
'#sheetwrap.open{pointer-events:auto}' +
'.sheet{position:absolute;left:0;right:0;bottom:0;background:rgba(255,255,255,.86);border-top:2px solid #23213a;border-radius:16px 16px 0 0;padding:14px 16px calc(16px + env(safe-area-inset-bottom));box-shadow:0 -8px 30px rgba(40,32,18,.22);max-width:640px;margin:0 auto;max-height:88vh;overflow-y:auto;transform:translateY(110%);transition:transform .32s cubic-bezier(.32,.72,0,1);will-change:transform}' +
'.sheet::before{content:"";display:block;width:38px;height:4px;border-radius:2px;background:rgba(96,82,170,.28);margin:0 auto 12px}' +
'#sheetwrap.open .sheet{transform:translateY(0)}' +
'.sheet h3{font-size:16px;margin-bottom:2px}.sheet .who{color:var(--muted);font-size:13px}' +
'.shhead{display:flex;align-items:center;gap:11px;margin-bottom:10px}' +

'#shsay{background:rgba(255,255,255,.66);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px;line-height:1.75;margin:2px 0 10px;min-height:44px;white-space:pre-wrap}' +
'body.th-dark #shsay{background:#232045;border-color:#2c2a48;color:#d9d6ee}' +
'.mask{position:absolute;inset:0;background:rgba(20,16,10,.45);opacity:0;transition:opacity .28s ease}' +
'#sheetwrap.open .mask{opacity:1}' +
/* 全文ログビューア（「最近の動き」タップで拡大・シートより上層） */
'#logwrap{position:fixed;inset:0;z-index:120;pointer-events:none}' +
'#logwrap.open{pointer-events:auto}' +
'#logwrap .mask{position:absolute;inset:0;background:rgba(20,16,10,.5);opacity:0;transition:opacity .28s ease}' +
'#logwrap.open .mask{opacity:1}' +
'#logwrap.open .sheet{transform:translateY(0)}' +
/* ⚙️設定シート（フッター4ボタン化） */
'#scene3dwrap,#scene3d{touch-action:none}' +
'#runwrap,#lnwrap,#reswrap{position:fixed;inset:0;z-index:112;pointer-events:none}' +
'#runwrap.open,#lnwrap.open,#reswrap.open{pointer-events:auto}' +
'#runwrap .mask,#lnwrap .mask,#reswrap .mask{position:absolute;inset:0;background:rgba(20,16,10,.5);opacity:0;transition:opacity .28s ease}' +
'#runwrap.open .mask,#lnwrap.open .mask,#reswrap.open .mask{opacity:1}' +
'#runwrap.open .sheet,#lnwrap.open .sheet,#reswrap.open .sheet{transform:translateY(0)}' +
'#rn_list .qopt{margin-bottom:8px}' +
'#setwrap{position:fixed;inset:0;z-index:115;pointer-events:none}' +
'#setwrap.open{pointer-events:auto}' +
'#setwrap .mask{position:absolute;inset:0;background:rgba(20,16,10,.5);opacity:0;transition:opacity .28s ease}' +
'#setwrap.open .mask{opacity:1}' +
'#setwrap.open .sheet{transform:translateY(0)}' +
'.setrow{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:13px 2px;border-bottom:1px dashed var(--line)}' +
'.setrow .lb{font-weight:800;font-size:14px;flex:none}' +
'.setrow .hint{font-size:11.5px;color:#6c6890;margin-top:3px;font-weight:600}' +
'.seg{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}' +
'.seg button{width:auto;margin:0;background:#eeecf9;color:#514d78;border-radius:9px;padding:9px 14px;font-size:13px;min-height:42px}' +
'.seg button.on{background:var(--sage);color:#fff}' +
'.setver{text-align:center;color:#8f8ab5;font-size:11px;margin-top:12px;font-weight:700}' +
/* 🌙ダークテーマ（設定で切替・シーン絵はそのまま=イラスト部は共通） */
// R78: ダークもライトと同じ言語（深いインディゴ＋紫アクセント）。琥珀/緑の暖色は撤去。
'body.th-dark{background:radial-gradient(90% 60% at 18% 4%,rgba(124,92,255,.18) 0%,rgba(124,92,255,0) 55%),linear-gradient(165deg,#191733 0%,#15132b 60%,#111027 100%);color:#e8e6f6}' +
'body.th-dark .card{background:#221f3e;border-color:#2c2a48;box-shadow:none}' +
'body.th-dark .card:active{background:#2a2650}' +
'body.th-dark .card .nm{color:#e8e6f6}body.th-dark .card .st{color:#b9b5d6}body.th-dark .card .q{color:#e8bd69}body.th-dark .card .age{color:#8f8ab5}' +
'body.th-dark .card.alert{background:#341e2e}body.th-dark .card.pend{background:#2e2a1c}' +
'body.th-dark .feedline{color:#8f8ab5}' +
'body.th-dark .feed{border-color:#2c2a48}' +
'body.th-dark .sheet{background:#1b1933;border-top-color:var(--accent);color:#e8e6f6}' +
'body.th-dark .sheet h3{color:#f2f0ff}body.th-dark .sheet .sec{color:#8f8ab5}' +
'body.th-dark .sheet .feedbox,body.th-dark .sheet .said{background:#232045;border-color:#2c2a48;color:#c9c5e6}' +
'body.th-dark .sheet .feedbox .feedline{color:#b9b5d6}body.th-dark .sheet .feedbox .feedline:first-child{color:#f2f0ff}' +
'body.th-dark .lgline{color:#d9d6ee;border-color:#2c2a48}body.th-dark #lgbody .lgline:first-of-type{color:#f2f0ff}' +
'body.th-dark textarea,body.th-dark input{background:#232045;border-color:#2c2a48;color:#e8e6f6}' +
'body.th-dark button.sub{background:#2c2a48;color:#c9c5e6}' +
'body.th-dark .empty{color:#8f8ab5}body.th-dark .setup h2{color:#f2f0ff}body.th-dark .setup p{color:#b9b5d6}' +
'body.th-dark .setrow{border-color:#2c2a48}body.th-dark .seg button{background:#2c2a48;color:#c9c5e6}body.th-dark .seg button.on{background:var(--sage);color:#fff}' +
/* v5: クロームのth-dark（classicライト化に伴い、ダークテーマは従来の暗色クロームを維持） */
'body.th-dark .hdr2{background:rgba(30,28,58,.72);color:#e8e6f6;border-bottom-color:rgba(160,140,255,.20)}' +
'body.th-dark #hstats .hstat{background:rgba(58,54,102,.55);border-color:rgba(160,140,255,.20);color:#c9c5e6}' +
'body.th-dark #hstats .hstat b{color:#f2f0ff}' +
'body.th-dark #hstats .hstat.attn{background:rgba(224,83,138,.20);border-color:rgba(224,83,138,.48)}body.th-dark #hstats .hstat.attn b{color:#ff9db8}' +
'body.th-dark .hdr2 #ntog{color:#e8e6f6}' +
'body.th-dark .deptbar{background:#23213a;border-bottom-color:#2c2a48}' +
'body.th-dark .deptchip{background:rgba(58,54,102,.45);border-color:rgba(160,140,255,.22);color:#c9c5e6}' +
'body.th-dark .deptchip.on{background:rgba(124,92,255,.30);border-color:rgba(160,140,255,.55);color:#efeaff}' +
'body.th-dark .tabbar{background:rgba(25,23,51,.72);border-top-color:rgba(160,140,255,.20);box-shadow:0 -8px 24px rgba(8,6,24,.45)}' +
'body.th-dark .tabbar button{background:transparent;color:#b9b5d6;border-color:transparent}' +
'body.th-dark .tabbar button.on{background:rgba(124,92,255,.24);color:#d9ceff;border-color:rgba(160,140,255,.45)}' +
'body.th-dark #roster .rchip{background:rgba(58,54,102,.55);border-color:rgba(160,140,255,.20);color:#e8e6f6}' +
'body.th-dark .setrow .hint{color:#8f8ab5}' +
/* 🔎文字大きめ（アクセシビリティ） */
'body.th-big{font-size:17.5px}' +
'body.th-big .card .nm{font-size:17px}body.th-big .card .st{font-size:15px}body.th-big .feedline{font-size:14px}' +
'body.th-big .sheet .feedbox .feedline{font-size:15.5px}body.th-big .lgline{font-size:16.5px}' +
'body.th-big #shsay{font-size:16px}' +
'.logsheet{max-height:94vh}' +
'#lgbody{margin:2px 0 10px}' +
'.lgsec{font-size:11px;font-weight:800;color:#6c6890;letter-spacing:.04em;margin:13px 0 3px}' +
'.lgline{font-size:14px;line-height:1.85;color:#3c352a;padding:10px 2px;border-bottom:1px dashed var(--line);white-space:pre-wrap;word-break:break-word}' +
'.lgline:last-child{border-bottom:0}' +
'#lgbody .lgline:first-of-type{font-weight:800;color:#23213a}' +
'#note{position:fixed;left:50%;bottom:calc(86px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(8px);z-index:130;opacity:0;transition:opacity .25s,transform .25s;font-size:13px;font-weight:800;color:#fff;background:var(--sage);padding:10px 18px;border-radius:999px;max-width:90vw;text-align:center;box-shadow:0 4px 14px rgba(40,32,18,.28);pointer-events:none}' +
'#note.show{opacity:1;transform:translateX(-50%) translateY(0)}' +
'#note.err{background:var(--danger)}' +
'button:active,.chip:active,.card:active{transform:translateY(1px);filter:brightness(.96)}' +
'button:focus-visible,.chip:focus-visible,.card:focus-visible,.tabbar button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}' +
/* v5: ❗即答カード=コンパクト化（1枚目のみ展開・選択肢は横スクロール・2枚目以降は1行折り畳み） */
'#attncards{display:none;width:100%;background:transparent}' +
'.attncard{margin:0 0 8px;padding:9px 11px 10px}' +
'#attncards .attncard:last-child,#attncards .attnmore{margin-bottom:0}' +
'.attncard .attnhead{display:flex;align-items:center;gap:8px;min-width:0}' +
'.attncard .attnname{font-size:13px;font-weight:800;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
'.attncard .attnq{margin-top:5px;color:var(--danger);font-size:13px;font-weight:700;line-height:1.4;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden;word-break:break-word}' +
'.attncard.attnmini{display:flex;align-items:center;gap:8px;padding:8px 11px;min-height:44px;cursor:pointer}' +
'.attncard.attnmini .attnname{flex:none;max-width:38vw}' +
'.attncard.attnmini .attnq{flex:1;min-width:0;margin:0;white-space:nowrap;display:block;text-overflow:ellipsis}' +
'.attncard.attnmini .attngo{flex:none;color:var(--muted);font-size:12px;font-weight:800}' +
'.attnoptions{display:flex;flex-direction:row;gap:6px;margin-top:7px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding-bottom:2px}' +
'.attnoptions::-webkit-scrollbar{display:none}' +
'.attnoptions .qopt{flex:none;width:auto;min-width:120px;max-width:200px}' +
'.quickoptions{display:flex;flex-direction:column;gap:6px;margin-top:8px}' +
'.qopt{display:flex;flex-direction:column;align-items:flex-start;gap:1px;width:100%;min-height:44px;margin:0;padding:8px 9px;background:rgba(255,255,255,.86);color:var(--ink);border:1.5px solid rgba(124,92,255,.30);border-radius:9px;text-align:left;font-size:12px;line-height:1.25}' +
'.qopt:hover{background:rgba(124,92,255,.10)}' +
'.qopt .qopt-label{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:800}' +
'.qopt .qopt-desc{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#6c6890;font-size:10.5px;font-weight:600}' +
'.quickoptions{margin:0 0 8px}' +
'.quickoptions .qopt{background:rgba(255,255,255,.92)}' +
'.attncard .attnactions{display:flex;flex-wrap:nowrap;gap:6px;margin-top:7px}' +
'.attncard .attnactions button{flex:1 1 0;min-width:0;width:auto;min-height:44px;margin:0;padding:7px 4px;font-size:11px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
'.attncard .attnactions button.sub{color:var(--muted)}' +
'.attncard .attnsent{margin-top:7px;color:#2f6f68;font-size:12px;font-weight:800}' +
'#attncards.on{display:block}' +
'#attncards .attnmore{width:auto;min-height:44px;margin:0;padding:8px 14px;background:#fdf4ef;color:var(--danger);border:1px solid var(--alert);border-radius:999px;font-size:12px;font-weight:800}' +
'body.th-dark #attncards{background:transparent}body.th-dark .attncard .attnq{color:#ffb09c}body.th-dark .attncard .attnsent{color:#9ee6bb}' +
// R78: 警告は「面ごと赤く塗る」から「ガラスの上の赤いチップ」へ（3Dシーンの上でも浮かない）
'#banner{display:none;margin:8px 10px 0;border-radius:12px;background:rgba(180,67,107,.10);border:1px solid rgba(180,67,107,.30);color:var(--danger);font-size:12px;font-weight:800;text-align:center;padding:7px 10px}' +
'body.off #banner{display:block}' +
'body.off #room,body.off #list{filter:grayscale(.55);opacity:.62}' +
'#list{padding:10px 12px}' +
/* ===== ドット絵オフィスシーン（縦積みバルペン・案1＋接ぎ木） ===== */
'html{overflow-x:hidden}' +
'.topbar{position:sticky;top:0;z-index:20}' +
/* v5: クロームはPC版と同じ明るいデザイン言語（クリーム地+hairline+セージ=主操作）。ダークはth-darkで維持 */
'.hdr2{background:rgba(255,255,255,.66);-webkit-backdrop-filter:blur(18px) saturate(150%);backdrop-filter:blur(18px) saturate(150%);color:var(--ink);display:flex;align-items:center;gap:8px;padding:calc(9px + env(safe-area-inset-top)) 14px 9px;font-weight:800;border-bottom:1px solid var(--line);letter-spacing:.01em}' +
'.hdr2 .live{width:9px;height:9px;border-radius:50%;background:var(--sage);flex:none}' +
'.hdr2 .live.off{background:#a7a2c4}' +
/* R79-5: ヘッダー統合＝統計は右肩に3つだけ（❗/稼働/待機）。statbar/deptbar の常設2段は廃止して
   3Dシーンへ高さを返す。数字の意味はラベル（稼働/待機）と title で明示（アイコンだけの羅列はNGの掟を維持） */
'#hstats{margin-left:auto;flex:none;display:flex;gap:5px}' +
'#hstats .hstat{display:inline-flex;align-items:center;gap:4px;background:rgba(124,92,255,.10);border:1px solid rgba(124,92,255,.22);border-radius:999px;padding:3px 9px;font-size:10.5px;font-weight:800;color:var(--muted);white-space:nowrap}' +
'#hstats .hstat b{font-size:13.5px;line-height:1;color:var(--ink);font-variant-numeric:tabular-nums}' +
'#hstats .hstat.attn{background:rgba(224,83,138,.12);border-color:rgba(224,83,138,.38)}#hstats .hstat.attn b{color:var(--alert)}' +
/* 🔔はヘッダー右端（タブは3つに集約）。汎用button{}の後勝ちに負けないようid込みで上書き */
'.hdr2 #ntog{width:auto;flex:none;margin:0;padding:0;min-height:40px;min-width:44px;display:inline-flex;align-items:center;justify-content:center;background:transparent;border-radius:11px;color:var(--ink);font-size:18px}' +
'.hdr2 #ntog.on{background:rgba(124,92,255,.12)}' +
'.hdr2 .ttl{min-width:0;flex:1;display:flex;flex-direction:column;gap:1px;line-height:1.25}' +
'.hdr2 .ttl .tname{font-size:13.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
'.hdr2 .ttl .tsub{font-size:10px;font-weight:700;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
'.deptbar{display:flex;gap:7px;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch;padding:6px 10px;background:rgba(255,255,255,.55);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-bottom:1px solid var(--line);height:47px;min-height:47px;scrollbar-width:none}' +
'.deptbar::-webkit-scrollbar{display:none}' +
'.deptbar:empty{display:none}' +
'.deptchip{width:auto;flex:none;margin:0;min-height:34px;padding:5px 13px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.70);color:var(--muted);font-size:12px;font-weight:800}' +
'.deptchip.on{background:rgba(124,92,255,.12);border-color:rgba(124,92,255,.30);color:var(--accent)}' +
/* ===== フッターメニュー（2026-07-12 スマホ専用再構成: 操作は全部ここ・親指圏・潰れない） ===== */
'.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;gap:8px;background:rgba(255,255,255,.72);-webkit-backdrop-filter:blur(18px) saturate(150%);backdrop-filter:blur(18px) saturate(150%);border-top:1px solid var(--line);padding:7px 10px calc(7px + env(safe-area-inset-bottom));box-shadow:0 -8px 24px rgba(64,52,140,.10)}' +
'.tabbar button{flex:1 1 0;min-width:0;margin:0;background:transparent;color:var(--muted);border:1px solid transparent;border-radius:13px;padding:6px 2px;min-height:54px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;font-size:11px;font-weight:800;line-height:1.2;transition:background .14s,color .14s,border-color .14s}' +
'.tabbar button .ic{font-size:20px;line-height:1}' +
'.tabbar button.on{background:rgba(124,92,255,.12);color:var(--accent);border-color:rgba(124,92,255,.28)}' +
'.tabbar button.on .ic{filter:none}' +
'.tabbar button:active{transform:translateY(1px);filter:brightness(.95)}' +
/* ===== スマホ=タイル床＋モダン家具のオフィスシーン 2026-07-24 ===== */
/* v5: 旧v3シーンの#room定義はM1定義(後方)へ一本化済み */
/* 歩行キャラ=フッターメニュー直上の固定通路（スクロール位置に関係なく常に「忙しく動いてる」・2026-07-13 FB） */
/* 高さ=char50+bob3+bottom3+plate17+余白5=78px（64pxでは頭が見切れる=2026-07-13 FB） */
/* v5: walkbar(フッター歩行帯)は廃止＝v4.0ミニマップの歩行遷移と情報重複し、名札プレートが重なって崩壊していた */
'#room .empty{margin:auto;text-align:center;color:#514d78;font-size:14px;font-weight:700;padding:48px 20px}' +
'.ws[data-alert] .bubble{background:#4a1f16;border-color:#c05a5a;color:#ffd9a8}' +
/* キャラは大きく・机は小さく＝上半身がしっかり見える比率（机の前景が腰下を隠す=PC版と同じ着席表現） */
'.gesture{pointer-events:none;line-height:1}' +
'.card .gesture{position:absolute;top:10px;right:12px;font-size:15px}' +
/* タイル床の上でも読める空室メッセージ */
/* 会議/ラウンジの名札チップ（誰がいるか一目で・タップ=詳細シート） */
'.ws[data-state=working] .screenglow{background:#7fd8a4;box-shadow:0 0 7px #7fd8a4;opacity:.8;animation:glowpulse 1.4s ease infinite}' +
'.ws[data-state=working] .char{animation:bob 1.5s steps(2) infinite}' +
'.ws[data-state=resting] .char{filter:grayscale(.4) opacity(.85)}' +
'.ws[data-alert] .badge{animation:blink 1s steps(2) infinite}' +
'.ws[data-alert] .stage{outline:2px solid #c05a5a;outline-offset:2px;border-radius:8px}' +
'.ws[data-pend] .stage{outline:2px dashed #b9791a;outline-offset:2px;border-radius:8px}' +
/* 各机の下に「いま何してるか」1行（officegridでは吹き出し非表示のため=「何してるか分からない」FB対応） */
/* ゾーンラベル=部屋パネルに密着する看板（meet/loungeのみ・officeは床置きのまま） */
/* 休憩ラウンジ=明るいタイル床＋中央ソファ・右キッチネット・端の植物 */
/* ソファはキャラ(88px)との比率をPC版(ソファ幅≒キャラ2人分)に合わせる。巨大化するとキャラが飲まれる */
/* ===== M1: スマホ1画面ミニフロアマップ（論理374×470） ===== */
// R79-5: officeタブは固定フルスクリーン＝一切スクロールしない（100dvh非依存＝iOSのアドレスバー
// 伸縮に強い）。3Dは inset:0 のフルブリード背景、❗ドック/ロスターは親指圏の下部ドックに浮く。
// ガラスのヘッダー/タブバーの背後にもcanvasが透ける（器の角丸・枠・余白は撤去）。
'#room:not(.hidden){position:fixed;inset:0;z-index:1;display:flex;flex-direction:column;background:transparent;overflow:hidden;padding:0}' +
/* R79-6: ビネット＝深度は影でなく段（::after 1枚・drawCall 0）。ドックのガラスを立たせる */
'#room:not(.hidden)::after{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;background:radial-gradient(130% 96% at 50% 40%,transparent 62%,rgba(35,33,58,.12) 100%)}' +
'body.th-dark #room:not(.hidden)::after{background:radial-gradient(130% 96% at 50% 40%,transparent 58%,rgba(8,6,24,.38) 100%)}' +
'#dock .card.alert{background:rgba(255,247,250,.90);-webkit-backdrop-filter:blur(18px) saturate(150%);backdrop-filter:blur(18px) saturate(150%);border-color:rgba(224,83,138,.42);box-shadow:0 14px 34px rgba(64,52,140,.20)}' +
'body.th-dark #dock .card.alert{background:rgba(42,26,44,.88);border-color:rgba(224,83,138,.50)}' +
/* 廊下=木目（白タイルの部屋がPC同様に「部屋」として浮き上がる） */
/* 南面=メインオフィスに面したガラス壁（PCと同じtile2_wall_glass実タイル） */
/* キャラ=1社員1ノード。left/topを論理座標で遷移させ、spriteだけ220msで差し替える。 */
/* 着席中(atdesk)は机前景(z3)の後ろ＝PCと同じ「机が腰下を隠す」座り表現 */
/* 名札=PC同様のダークプレート・50px以内(アンカー54pxピッチで非重なり) */
/* 吹き出し会話(R23.5): 白ポップ・タップ透過・同時最大2体（過密マップを壊さない）。 */
'@keyframes typingbob{0%,100%{transform:translateY(0)}50%{transform:translateY(-1px)}}' +
'@keyframes attnpulse{0%,100%{box-shadow:0 0 0 0 rgba(192,90,90,.65)}50%{box-shadow:0 0 0 4px rgba(192,90,90,0)}}' +
'.ocpill{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border-radius:999px;background:rgba(222,232,238,.16);border:1px solid rgba(222,232,238,.36);color:#e8edf0;font-size:10px;font-weight:800;white-space:nowrap}' +
// R76: OpenClaw室に実メンバーを出す（旧: 常に「未接続」＋巡回ロボだけの飾りだった）
// R79-5: 3Dはフルブリード背景（旧: 角丸ガラスの器・高さ54vh）。カメラは boot3d の
// balanced フィット＋botPad が効くので3D側の変更は不要。
'#scene3dwrap{position:absolute;inset:0;overflow:hidden;background:transparent}' +
'#scene3d{width:100%;height:100%;display:block}' +
'#scene3d canvas{width:100%!important;height:100%!important;display:block;touch-action:manipulation}' +
'#plates{position:absolute;inset:0;pointer-events:none}' +
// R78: 3Dでは名札を全員に出せない（390pxで9枚は重なる）。誰が何をしているかは帯で補い、
// タップでその社員へカメラが寄る＝一覧と3Dが同じ対象を指す。汎用buttonより高い詳細度で上書き。
/* R79-5: 下部ドック＝❗トリアージとロスターの置き場（親指圏）。タブバーの直上に浮くガラス。
   ❗があるときはロスターを隠す＝一等地を二重に使わない */
'#emptyhint{position:absolute;left:50%;top:44%;transform:translate(-50%,-50%);z-index:4;width:min(300px,80vw);padding:16px 18px;text-align:center;border-radius:16px;background:rgba(255,255,255,.80);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border:1px solid var(--line);box-shadow:0 12px 34px rgba(64,52,140,.14)}' +
'#emptyhint .eh-t{font-size:14px;font-weight:800;color:var(--ink);margin-bottom:6px}' +
'#emptyhint .eh-s{font-size:12px;line-height:1.7;color:var(--muted)}' +
'body.th-dark #emptyhint{background:rgba(30,28,58,.86);border-color:rgba(160,140,255,.22)}' +
'#dock{position:absolute;left:8px;right:8px;bottom:calc(78px + env(safe-area-inset-bottom));z-index:6;display:flex;flex-direction:column;gap:6px}' +
// R80.7: ロスターは**上部の常設帯**へ（ユーザーFB「下のスライドできる項目は上に」）。
// ❗カードは下（親指圏）のまま＝縦の取り合いが消えたので圧縮モードは廃止。
'#topdock{position:absolute;left:8px;right:8px;top:calc(58px + env(safe-area-inset-top));z-index:6}' +

// R81-5: 下部は #infodock 1枚のガラスパネル（バラバラの小ピルが「素人感」の正体・ユーザーFB）
'#infodock{background:rgba(255,255,255,.82);-webkit-backdrop-filter:blur(22px) saturate(160%);backdrop-filter:blur(22px) saturate(160%);border:1px solid var(--line);border-radius:18px;box-shadow:0 12px 32px rgba(64,52,140,.16);overflow:hidden}' +
'body.th-dark #infodock{background:rgba(28,26,54,.86);border-color:rgba(160,140,255,.22)}' +
'#gaugebar{color:var(--ink);min-height:0;margin:0;width:100%;text-align:left;font-weight:800;background:transparent;border:0;border-radius:0;box-shadow:none;padding:10px 14px 13px;border-top:1px solid rgba(124,92,255,.12);display:flex;gap:12px;align-items:center}' +
'#gaugebar .gg b{color:var(--ink)}' +
'body.th-dark #gaugebar{color:#e8e6f6;border-top-color:rgba(160,140,255,.16)}' +
'body.th-dark #gaugebar .gg b{color:#e8e6f6}' +
'#gaugebar:empty{display:none}' +
'#ticker{display:flex;align-items:center;gap:10px;padding:12px 14px 10px;margin:0;width:100%;text-align:left;background:transparent;border:0;border-radius:0;box-shadow:none;color:var(--ink);font-weight:700;transition:opacity .18s;min-height:0}' +
'#ticker:empty{display:none}' +
'#ticker.fade{opacity:0}' +
'#ticker .mono{--asz:26px;flex:none}' +
'#ticker .tk-nm{flex:none;font-size:13.5px;max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;letter-spacing:.01em}' +
'#ticker .tk-act{flex:1;min-width:0;font-size:12.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700}' +
'#ticker .tk-n{flex:none;font-size:10px;color:var(--muted);font-weight:800;background:rgba(124,92,255,.10);border-radius:99px;padding:2px 7px}' +
'body.th-dark #ticker{color:#e8e6f6}' +
'#gaugebar .gg{flex:1;display:grid;grid-template-columns:1fr auto;grid-template-areas:"lb pct" "bar bar";column-gap:7px;row-gap:4px;min-width:0;align-items:center}' +
'#gaugebar .gg .lb{grid-area:lb;font-size:10.5px;font-weight:800;color:var(--muted);overflow:hidden;white-space:nowrap;letter-spacing:.01em}' +
'#gaugebar .gg .tr{grid-area:bar;height:10px;border-radius:99px;background:rgba(124,92,255,.12);overflow:hidden;box-shadow:inset 0 1px 2px rgba(64,52,140,.10)}' +
'#gaugebar .gg .tr i{display:block;height:100%;border-radius:99px;background:var(--sage)}' +
'#gaugebar .gg.warn .tr i{background:var(--amber)}' +
'#gaugebar .gg.hot .tr i{background:var(--alert)}' +
'#gaugebar .gg b{grid-area:pct;font-size:13.5px;font-weight:800;letter-spacing:.01em}' +
'#gaugebar .gg-t,#gaugebar .gg-p{display:flex;flex:none;align-items:center;background:rgba(124,92,255,.10);border-radius:12px;padding:7px 11px}' +
'#gaugebar .gg-p .lb{font-size:12px;color:var(--ink);max-width:96px}' +
'body.th-dark #gaugebar .gg-p .lb{color:#e8e6f6}' +
'#gaugebar .gg-t .lb{font-size:12.5px;color:var(--ink)}' +
'body.th-dark #gaugebar .gg-t .lb{color:#e8e6f6}' +
'#reswrap .sheet{background:rgba(255,255,255,.97)}' +
'body.th-dark #reswrap .sheet{background:rgba(26,24,50,.97)}' +
'#rs_body .rs-sec{font-weight:800;font-size:13px;margin:14px 0 6px}' +
'#rs_body .rs-sec:first-child{margin-top:2px}' +
'#rs_body .rs-bar{display:flex;align-items:center;gap:9px;margin:7px 0}' +
'#rs_body .rs-bar .lb{flex:none;width:86px;font-size:12px;font-weight:700}' +
'#rs_body .rs-bar .tr{flex:1;height:9px;border-radius:99px;background:rgba(124,92,255,.12);overflow:hidden}' +
'#rs_body .rs-bar .tr i{display:block;height:100%;border-radius:99px;background:var(--sage)}' +
'#rs_body .rs-bar.warn .tr i{background:var(--amber)}' +
'#rs_body .rs-bar.hot .tr i{background:var(--alert)}' +
'#rs_body .rs-bar b{flex:none;font-size:12px;font-weight:800}' +
'#rs_body .rs-note{font-size:11.5px;color:#6c6890;font-weight:600;margin:2px 0 4px;line-height:1.6}' +
'#rs_body .rs-chips{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0 10px}' +
'#rs_body .rs-chip{width:auto;margin:0;background:#eeecf9;color:#514d78;border:1px solid transparent;border-radius:999px;padding:7px 14px;font-size:12.5px;font-weight:800;min-height:36px}' +
'#rs_body .rs-chip.on{background:var(--sage);color:#fff}' +
'body.th-dark #rs_body .rs-chip{background:rgba(124,92,255,.16);color:#d9ceff}' +
'body.th-dark #rs_body .rs-chip.on{background:var(--sage);color:#fff}' +
'body.th-dark #rs_body .rs-note{color:#8f8ab5}' +
'body.th-dark #gaugebar{background:rgba(30,28,58,.80);border-color:rgba(160,140,255,.20)}' +
'#roster{flex:none;display:flex;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch;padding:2px;scrollbar-width:none}' +
'#roster::-webkit-scrollbar{display:none}' +
'#roster:empty{display:none}' +
/* R79-6: チップは縦型アバターグリッド（モノグラム上・名前下・幅62px＝390pxで5枚超が一目）。
   モノグラムは3Dの足元ピンと同じ文字＋状態リング＝「チップのE＝あのロボ」の対応が学習できる。
   述語はテキスト名札とシートが担う（チップに詰め込まない） */
'#roster .rchip{flex:none;margin:0;display:flex;align-items:center;text-align:left;gap:7px;min-width:118px;max-width:178px;background:rgba(255,255,255,.78);-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px);border:1px solid var(--line);border-radius:16px;padding:6px 11px 6px 8px;min-height:48px;font-weight:700;color:var(--ink);box-shadow:0 6px 18px rgba(64,52,140,.12);transition:background .14s,border-color .14s}' +
'#roster .rchip .mono{--asz:24px;flex:none}' +
'#roster .rchip .rtxt{display:flex;flex-direction:column;gap:2px;min-width:0}' +
'#roster .rchip.attn{border-color:rgba(224,83,138,.38);background:rgba(224,83,138,.10)}' +
'#roster .rchip.sel{border-color:rgba(124,92,255,.42);background:rgba(124,92,255,.12)}' +
'#roster .rchip .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:132px;font-size:11px;line-height:1.2}' +
'#roster .rchip .gl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:132px;font-size:9px;line-height:1.2;font-weight:600;color:var(--muted)}' +
/* R79-6: 名札は二段マーカー（ユーザーFB「どのロボットがどのエージェントか分からない」）。
   ①全員=足元モノグラムピン（1文字＋状態リング＝リスト/ロスターの.monoと同じ記号体系）
   ②選択中/❗先頭=テキスト名札（デスクトップ.lblと同じガラスピル・足元アンカー・高さ18px） */
'#plates .pin{position:absolute;transform:translate(-50%,0);pointer-events:none;display:flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:999px;font-weight:800;font-size:10px;line-height:1;color:var(--ink);background:rgba(255,255,255,.85);-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px);border:2px solid #b9b5d6;box-shadow:0 2px 8px rgba(40,34,60,.22);box-sizing:border-box}' +
'#plates .pin.st-working{border-color:var(--sage)}' +
'#plates .pin.st-waiting{border-color:var(--amber)}' +
'#plates .pin.ext{border-color:#5aa2ff}' +
'#plates .pin.attn{border-color:var(--alert);background:#fff0f5;color:#a3274e;animation:attnpulse 1.6s ease infinite}' +
/* 汎用button{}のmin-height:44/width:100%/marginに負けないよう明示上書き（R78教訓） */
'#plates .plate{position:absolute;transform:translate(-50%,0);pointer-events:auto;display:flex;align-items:center;gap:5px;width:auto;max-width:150px;height:18px;min-height:18px;margin:0;font-weight:700;font-size:11px;line-height:1.2;color:#33305a;background:rgba(255,255,255,.88);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);border:1px solid rgba(255,255,255,.95);border-radius:999px;padding:2px 9px;white-space:nowrap;box-shadow:0 4px 14px rgba(64,52,140,.18)}' +
'#plates .plate .dot{width:6px;height:6px;border-radius:999px;background:#c9c6dd;flex:none}' +
'#plates .plate .nm{overflow:hidden;text-overflow:ellipsis}' +
'#plates .plate.st-working .dot{background:var(--sage)}' +
'#plates .plate.st-waiting .dot{background:var(--amber)}' +
'#plates .plate.attn{border-color:rgba(224,83,138,.55);background:rgba(255,244,246,.95);color:#a3274e}' +
'#plates .plate.attn .dot{background:var(--alert)}' +
'#plates .plate.sel{border-color:rgba(124,92,255,.55);box-shadow:0 0 0 2px rgba(124,92,255,.30),0 4px 14px rgba(64,52,140,.18)}' +
'body.th-dark #plates .pin{background:rgba(30,28,58,.88);color:#e8e6f6}' +
'body.th-dark #plates .plate{background:rgba(30,28,58,.92);border-color:rgba(160,140,255,.30);color:#e8e6f6}' +
'body.th-dark #plates .plate.attn{background:rgba(58,26,40,.94);color:#ff9db8}' +
'@keyframes bob{0%,100%{transform:translateX(-50%) translateY(0)}50%{transform:translateX(-50%) translateY(-2px)}}' +
'@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}' +
'@keyframes glowpulse{0%,100%{opacity:.35}50%{opacity:.85}}' +
'@keyframes breath{0%,100%{transform:translateX(-50%) translateY(0)}50%{transform:translateX(-50%) translateY(-1.5px)}}' +
'.ws[data-state=waiting] .char,.ws[data-state=resting] .char{animation:breath 3.4s ease-in-out infinite}' +
'.chip{min-height:44px;display:inline-flex;align-items:center;justify-content:center}' +
'.card.sel{outline:2px solid var(--sage);outline-offset:-1px}' +
'</style></head><body>' +
// R79-5: ヘッダー1段（live・タイトル・統計3つ・🔔）＝statbar/deptbarの常設2段を廃止。
// 部署フィルタ(deptbar)はリストタブ専用として温存（buildDeptbarがVIEWでゲート）。
'<div class=topbar><div class=hdr2 onclick="window.scrollTo(0,0)"><span class=live id=live></span><span class=ttl>🏢 AI Office</span><span id=hstats></span><button id=ntog onclick="event.stopPropagation();togglePush()"><span id=ntogic>🔕</span></button></div>' +
'<div class=deptbar id=deptbar></div>' +
'<div id=banner>⚠️ オフライン・再接続中…</div></div>' +
'<main id=app class=hidden><div id=room></div><div id=list class=hidden></div><div id=note></div></main>' +
'<nav class="tabbar hidden" id=tabbar>' +
'<button id=tb_office onclick="setView(\'office\')"><span class=ic>🏢</span><span id=tb_office_lb>オフィス</span></button>' +
'<button id=tb_list onclick="setView(\'list\')"><span class=ic>☰</span><span id=tb_list_lb>リスト</span></button>' +
'<button id=tb_run onclick="openRun()"><span class=ic>▶</span><span id=tb_run_lb>実行</span></button>' +
'<button id=tb_set onclick="openSettings()"><span class=ic>⚙️</span><span id=tb_set_lb>設定</span></button>' +
'</nav>' +
// R79-10: ▶実行シート（許可リスト方式）。ここに出るのは**Macの前で登録したレシピ**だけ＝
// このUIから新しいコマンドを作ることはできない（作れる経路を持たないのが安全性の本体）。
'<div id=runwrap><div class=mask onclick="closeRun()"></div><div class="sheet">' +
'<h3 id=rn_title>▶ 遠隔実行</h3><div class=who id=rn_hint>Macに登録した操作だけを実行できます</div>' +
'<div id=rn_list></div><div id=rn_results></div>' +
'<button class=sub id=rn_close onclick="closeRun()">閉じる</button></div></div>' +
'<div id=lnwrap><div class=mask onclick="closeLaunch()"></div><div class="sheet">' +
'<h3 style="margin-bottom:6px" id=ln_title>▶ プロジェクトを起動</h3>' +
'<p class=hint id=ln_hint style="font-size:11.5px;color:#6c6890;font-weight:600;margin:0 0 10px">過去にMacで開いたことのあるプロジェクトだけが対象です（スマホから新しいフォルダは登録できません）</p>' +
'<div id=ln_list></div>' +
'<button class=sub id=ln_close onclick="closeLaunch()">閉じる</button></div></div>' +
'<div id=reswrap><div class=mask onclick="closeRes()"></div><div class="sheet">' +
'<h3 style="margin-bottom:6px" id=rs_title>⚡ リソースとライセンス</h3>' +
'<div id=rs_body></div>' +
'<button class=sub id=rs_close onclick="closeRes()">閉じる</button></div></div>' +
'<div id=setwrap><div class=mask onclick="closeSettings()"></div><div class="sheet">' +
'<h3 style="margin-bottom:10px" id=st_title>⚙️ 設定</h3>' +
'<div class=setrow><div><div class=lb id=st_th_lb>🎨 テーマ</div><div class=hint id=st_th_hint>配色を切り替えます</div></div>' +
'<div class=seg><button id=sg_th_c onclick="setTheme(\'classic\')">クラシック</button><button id=sg_th_d onclick="setTheme(\'dark\')">ダーク</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_fs_lb>🔎 文字サイズ</div></div>' +
'<div class=seg><button id=sg_fs_s onclick="setBig(false)">標準</button><button id=sg_fs_b onclick="setBig(true)">大きめ</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_sd_lb>🔊 効果音</div><div class=hint id=st_sd_hint>ロボット風の効果音（初期OFF）</div></div>' +
'<div class=seg><button id=sg_sd_on onclick="setSound(true)">ON</button><button id=sg_sd_off onclick="setSound(false)">OFF</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_pt_lb>🔔 通知テスト</div><div class=hint id=st_pt_hint>登録済みの全端末へテスト通知。購読時のフィルタが通知対象になります（全部=すべて）</div></div>' +
'<div class=seg><button id=st_pt_btn onclick="sendTestPush()">送信</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_ln_lb>▶ プロジェクトを起動</div><div class=hint id=st_ln_hint>休眠中のプロジェクトのセッションをMacで開きます</div></div>' +
'<div class=seg><button id=st_ln_btn onclick="closeSettings();openLaunch()">開く</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_res_lb>⚡ リソース</div><div class=hint id=st_res_hint>読み込み中…</div></div><div class=seg><button id=st_res_btn onclick="closeSettings();openRes()">開く</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_lic_lb>🧾 ライセンス</div><div class=hint id=st_lic_hint>読み込み中…</div></div></div>' +
'<div class=setrow style="border-bottom:0"><div><div class=lb id=st_rp_lb>🔗 再ペアリング</div><div class=hint id=st_rp_hint>この端末の登録をやり直します</div></div>' +
'<div class=seg><button class=r id=st_rp_btn style="background:var(--danger);color:#fff" onclick="repair()">解除</button></div></div>' +
'<button class=sub id=st_close onclick="closeSettings()">閉じる</button>' +
'<div class=setver>AI Office PWA v4.0</div>' +
'</div></div>' +
'<div id=setup class="setup hidden"><h2 id=su_title>📱 スマホをペアリング</h2>' +
'<p><span id=su_p1>Macの AIオフィス画面（左パネル「📱 スマホ連携」）でペアリングを発行し、表示された</span><b id=su_link>リンク</b><span id=su_p2>をこの端末へ送って開くと自動でペアリングされます。うまくいかない時は、Mac側の「ペアリングリンクをコピー」で得たリンクを下に貼り付けてください。</span></p>' +
'<textarea id=pastebox rows=3 placeholder="Macで表示されたペアリングリンクを貼り付け"></textarea>' +
'<button id=su_btn onclick="pasteCred()">この端末を登録</button></div>' +
'<div id=logwrap><div class=mask onclick="closeLog()"></div>' +
'<div class="sheet logsheet"><div class=shhead><span id=lgava class=mono></span><div><h3 id=lgname></h3><div class=who id=lgwho></div></div></div>' +
'<div id=lgbody></div><button class=sub id=lg_close onclick="closeLog()">閉じる</button></div></div>' +
'<div id=sheetwrap><div class=mask onclick="closeSheet()"></div>' +
'<div class=sheet><div class=shhead><span id=shava class=mono></span><div><h3 id=shname></h3><div class=who id=shwho></div></div></div><div id=shsay></div><div id=shdetail></div><div id=quickbtns></div>' +
'<textarea id=freetext rows=2 placeholder="自由に指示（例: キリのいいところでコミットして残タスク報告）"></textarea>' +
'<button class=g id=sh_send onclick="sendFree()">✍️ この指示を送る</button>' +
'<button class=sub id=sh_close onclick="closeSheet()">閉じる</button></div></div>' +
// ★ __name ガード: wrangler(esbuild)が .toString() 前の関数体へ keep-names ヘルパー
//   __name(fn,"n") を注入することがあり、ブラウザ側には未定義＝ReferenceErrorで
//   スクリプト全体が死ぬ（R65で実測）。恒等関数を先に敷いて無害化する。
'<script>var __name=typeof __name==="function"?__name:function(f){return f};' +
PWA_GLOSS_SOURCE +
// R42.2d-2 言語: office.lang が正本（statusで追随）・localStorageは初回描画用キャッシュ
'var LANG=(function(){try{return localStorage.getItem("aioffice.lang")==="en"?"en":"ja"}catch(e){return "ja"}})();' +
'function T(ja,en){return LANG==="en"?en:ja}' +
'var OFFICE_DESK_CAPACITY=6;' +
'var KEY="aioffice.cred";' +
'function getCred(){try{return JSON.parse(localStorage.getItem(KEY)||"null")}catch(_){return null}}' +
'function saveCred(d,s,t,e){localStorage.setItem(KEY,JSON.stringify({d:d,s:s,t:t,e:e?parseInt(e,10):0}))}' +
'function credFromHash(){var h=location.hash.replace(/^#/,"");if(!h)return false;var p=new URLSearchParams(h);' +
'var d=p.get("d"),s=p.get("s"),t=p.get("t"),e=p.get("e");if(d&&s&&t){saveCred(d,s,t,e);history.replaceState(null,"",location.pathname);return true}return false}' +
'function pasteCred(){var v=document.getElementById("pastebox").value.trim();if(v.indexOf("#")>=0)v=v.split("#").slice(1).join("#");' +
'var p=new URLSearchParams(v),d=p.get("d"),s=p.get("s"),t=p.get("t"),e=p.get("e");' +
'if(d&&s&&t){saveCred(d,s,t,e);location.hash="";location.reload()}else{alert(T("資格情報コードが読めませんでした","Could not read the credential code"))}}' +
'function hexToBytes(h){var a=new Uint8Array(h.length/2);for(var i=0;i<a.length;i++)a[i]=parseInt(h.substr(i*2,2),16);return a}' +
'function toHex(buf){var b=new Uint8Array(buf),o="";for(var i=0;i<b.length;i++)o+=b[i].toString(16).padStart(2,"0");return o}' +
'function sha256hex(str){return crypto.subtle.digest("SHA-256",new TextEncoder().encode(str)).then(toHex)}' +
'function sign(cred,session,text){var ts=Math.floor(Date.now()/1000);var nb=new Uint8Array(16);crypto.getRandomValues(nb);var nonce=toHex(nb.buffer);' +
'return sha256hex(text).then(function(th){' +
'var canonical=["aioffice-instruct","v1",cred.d,session,String(ts),nonce,th].join("\\n");' +
'return crypto.subtle.importKey("raw",hexToBytes(cred.s),{name:"HMAC",hash:"SHA-256"},false,["sign"]).then(function(k){' +
'return crypto.subtle.sign("HMAC",k,new TextEncoder().encode(canonical))}).then(function(sb){' +
'return {v:1,device_id:cred.d,session:session,text:text,ts:ts,nonce:nonce,alg:"HS256",sig:toHex(sb)}})})}' +
'function el(tag,cls,txt){var x=document.createElement(tag);if(cls)x.className=cls;if(txt!=null)x.textContent=txt;return x}' +
'var SENDING=false;' +
// R51: 1アバター=1プロジェクト。データ源は roster 優先・無ければ employees（旧server後方互換）。
'function officeAgents(office){var r=office&&office.roster;return (Array.isArray(r)&&r.length)?r:((office&&office.employees)||[])}' +
// ❗カード/回答済みフラグのキーは projectId（rosterで代表sessionが入れ替わっても同一プロジェクト=1件）
'function attnKey(e){return String((e&&(e.projectId||e.session))||"")}' +
'function crewOf(e){var n=Number(e&&e.crew);return n>1?n:0}' +
'function dispCrew(e){var c=crewOf(e);return (e&&(e.disp||e.session)||"")+(c?" ×"+c:"")}' +
// R51: ❗回答済みフラグは localStorage 永続（再訪しても「送信済み」が残り、消えたら✅ answeredトースト）
'var ATTN_SENT=(function(){try{return JSON.parse(localStorage.getItem("aioffice.attnSent")||"{}")||{}}catch(_){return {}}})();' +
'function saveAttnSent(){try{localStorage.setItem("aioffice.attnSent",JSON.stringify(ATTN_SENT))}catch(_){}}' +
// R82: 今日のまとめ用の軽量カウンタ（端末ローカル・日付キー・7日分だけ保持）
'function todayKey(){var d=new Date();return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0")}'+
'function bumpSentLog(){try{var log=JSON.parse(localStorage.getItem("aioffice.sentlog")||"{}")||{};var k=todayKey();log[k]=(log[k]||0)+1;var keys=Object.keys(log).sort();while(keys.length>7){delete log[keys.shift()]}localStorage.setItem("aioffice.sentlog",JSON.stringify(log))}catch(_){}}'+
'function sentToday(){try{return (JSON.parse(localStorage.getItem("aioffice.sentlog")||"{}")||{})[todayKey()]||0}catch(_){return 0}}'+
'function bumpAnsLog(){try{var log=JSON.parse(localStorage.getItem("aioffice.anslog")||"{}")||{};var k=todayKey();log[k]=(log[k]||0)+1;var keys=Object.keys(log).sort();while(keys.length>7){delete log[keys.shift()]}localStorage.setItem("aioffice.anslog",JSON.stringify(log))}catch(_){}}'+
'function answeredToday(){var n=0;try{n=(JSON.parse(localStorage.getItem("aioffice.anslog")||"{}")||{})[todayKey()]||0}catch(_){n=0}var t0=new Date();t0.setHours(0,0,0,0);Object.keys(ATTN_SENT).forEach(function(k){var v=ATTN_SENT[k];if(typeof v==="number"&&v>=t0.getTime())n++});return n}'+
'function checkAnswered(agents){var live={};(Array.isArray(agents)?agents:[]).forEach(function(e){if(e&&needsAttn(e)){var k=attnKey(e);if(k)live[k]=1}});var done=[];Object.keys(ATTN_SENT).forEach(function(k){if(!live[k]){done.push(k);if(typeof ATTN_SENT[k]==="number")bumpAnsLog();delete ATTN_SENT[k]}});if(!done.length)return;saveAttnSent();var names=[];(Array.isArray(agents)?agents:[]).forEach(function(e){if(done.indexOf(attnKey(e))>=0){var n=e.disp||e.session;if(n)names.push(n)}});note(T("✅ 回答済み: ","✅ Answered: ")+(names.join(", ")||T("対応完了","resolved")))}' +
// R51: 配達往復の可視化＝pending(📨 queued)が消えた瞬間を捉えて ✓ delivered チップを短時間出す
'var PEND_PREV={},DELIVERED={};' +
'function checkDelivery(agents){var now=Date.now(),cur={};(Array.isArray(agents)?agents:[]).forEach(function(e){if(e&&e.pending){var k=attnKey(e);if(k)cur[k]=1}});Object.keys(PEND_PREV).forEach(function(k){if(!cur[k])DELIVERED[k]=now+25000});PEND_PREV=cur;Object.keys(DELIVERED).forEach(function(k){if(DELIVERED[k]<now)delete DELIVERED[k]})}' +
// R51: 鮮度の正直表示。status.ts(DO書込時刻)かagentSeenAgo(最終/sync)が180秒超なら stale＝
// バナー＋グレースケール(body.off流用)＋送信時confirm（{ok:true}トーストだけの無言ロストを塞ぐ）
'var STALE=false;' +
// R79-8.1: agentOn=MacのWSソケットが今繋がっている＝生存の正直な信号。WS化でsyncは
// 「変化時+240s heartbeat」だけになり、agentSeenAgoだけ見ると静穏時に偽staleが出る
'function updateStale(tsMs,agentAgo,agentOn){var age=(typeof tsMs==="number"&&tsMs>0)?Math.floor((Date.now()-tsMs)/1000):null;var was=STALE;STALE=!agentOn&&((age!=null&&age>180)||(typeof agentAgo==="number"&&agentAgo>180));var b=document.getElementById("banner");if(STALE){var worst=Math.max(age||0,typeof agentAgo==="number"?agentAgo:0);if(b)b.textContent=T("⚠️ Mac最終同期 "+fmtAge(worst)+" — 再同期まで指示は届きません","⚠️ Mac last sync "+fmtAge(worst)+" — instructions wait until it reconnects");document.body.classList.add("off")}else{document.body.classList.remove("off");if(b&&was)b.textContent=T("⚠️ オフライン・再接続中…","⚠️ Offline — reconnecting…")}}' +
'var AUDIO={enabled:false,ctx:null,master:null,played:0,lastAt:{}};' +
'try{AUDIO.enabled=localStorage.getItem("aioffice.sound")==="1"}catch(_){}' +
'var PULSE_WAVE_CACHE=new WeakMap();' +
'function pulseWave(ctx,duty){if(!ctx||!Number.isFinite(duty)||duty<=0||duty>=1)return null;var waves=PULSE_WAVE_CACHE.get(ctx);if(!waves){waves=new Map();PULSE_WAVE_CACHE.set(ctx,waves)}if(waves.has(duty))return waves.get(duty);var N=24,real=new Float32Array(N+1),imag=new Float32Array(N+1);for(var n=1;n<=N;n++)real[n]=(2/(n*Math.PI))*Math.sin(n*Math.PI*duty);var wave=ctx.createPeriodicWave(real,imag);waves.set(duty,wave);return wave}' +
'function ensureCtx(){if(!AUDIO.enabled||AUDIO.ctx)return AUDIO.ctx;var AudioCtor=window.AudioContext||window.webkitAudioContext;if(!AudioCtor)return null;try{var ctx=new AudioCtor(),master=ctx.createGain();master.gain.value=.4;master.connect(ctx.destination);AUDIO.ctx=ctx;AUDIO.master=master;return ctx}catch(_){return null}}' +
'function tone(ctx,dest,o){o=o||{};if(!ctx||!dest||!Number.isFinite(o.freq))return;var at=Number.isFinite(o.at)?o.at:ctx.currentTime,decay=Number.isFinite(o.decay)?o.decay:.2,attack=Number.isFinite(o.attack)?o.attack:.005,peak=Number.isFinite(o.peak)?o.peak:.2;if(!Number.isFinite(at)||!Number.isFinite(decay)||decay<=0)return;var osc=ctx.createOscillator(),gain=ctx.createGain(),wave=pulseWave(ctx,o.duty);if(wave)osc.setPeriodicWave(wave);else osc.type=o.type||"sine";osc.frequency.setValueAtTime(o.freq,at);if(Number.isFinite(o.toFreq)&&o.toFreq>0)osc.frequency.exponentialRampToValueAtTime(o.toFreq,at+decay);gain.gain.setValueAtTime(.0001,at);var attackTime=Math.max(.001,Math.min(decay,attack));gain.gain.linearRampToValueAtTime(Math.max(.0001,peak),at+attackTime);gain.gain.exponentialRampToValueAtTime(.0001,at+decay);osc.connect(gain);gain.connect(dest);osc.start(at);osc.stop(at+decay+.05)}' +
'function playSoundRecipe(kind,ctx,dest,at){if(kind==="select")tone(ctx,dest,{freq:987.8,duty:.25,at:at,peak:.22,attack:.002,decay:.055});else if(kind==="cursor")tone(ctx,dest,{freq:740,duty:.25,at:at,peak:.12,decay:.03});else if(kind==="send")tone(ctx,dest,{freq:880,duty:.25,at:at,peak:.20,decay:.08,toFreq:1568});else if(kind==="talk"){tone(ctx,dest,{freq:980+((performance.now()/16|0)%5)*110,duty:.25,at:at,peak:.06,decay:.028})}else if(kind==="attn"){tone(ctx,dest,{freq:880,duty:.25,at:at,peak:.40,decay:.55});tone(ctx,dest,{freq:1174.7,duty:.25,at:at+.17,peak:.40,decay:.55})}}' +
'function playSE(kind){if(!AUDIO.enabled||!AUDIO.ctx||!AUDIO.master||document.hidden)return false;var now=performance.now(),min=kind==="attn"?10000:(kind==="talk"?45:90);if(now-(AUDIO.lastAt[kind]||-Infinity)<min)return false;try{playSoundRecipe(kind,AUDIO.ctx,AUDIO.master,AUDIO.ctx.currentTime);AUDIO.lastAt[kind]=now;AUDIO.played++;return true}catch(_){return false}}' +
'function unlockAudio(){if(!AUDIO.enabled)return;var ctx=ensureCtx();if(ctx){try{ctx.resume().catch(()=>{})}catch(_){}}}' +
'document.addEventListener("pointerdown",unlockAudio,{capture:true});' +
'document.addEventListener("visibilitychange",function(){if(!AUDIO.ctx)return;if(document.hidden){try{AUDIO.ctx.suspend().catch(()=>{})}catch(_){}}else if(AUDIO.enabled){try{AUDIO.ctx.resume().catch(()=>{})}catch(_){}}});' +
'window.__pwaAudio={get enabled(){return AUDIO.enabled},get ctx(){return AUDIO.ctx},get played(){return AUDIO.played},play:function(kind){return playSE(kind||"select")},pulseWave:function(duty){return pulseWave(AUDIO.ctx,duty)}};' +
'function conn(ok,msg){document.body.classList.toggle("off",!ok);var l=document.getElementById("live");if(l){l.className="live"+(ok?"":" off");if(msg!=null)l.title=msg}}' +
'var SEL=null;' +
'var SHSAY_IV=null;' +
'function fmtAge(s){s=Math.max(0,Math.floor(s||0));if(LANG==="en")return s<60?s+"s ago":s<3600?Math.floor(s/60)+"m ago":s<86400?Math.floor(s/3600)+"h ago":Math.floor(s/86400)+"d ago";if(s<60)return s+"秒前";if(s<3600)return Math.floor(s/60)+"分前";if(s<86400)return Math.floor(s/3600)+"時間前";return Math.floor(s/86400)+"日前"}' +
'function refreshAges(emps){var m={};for(var j=0;j<emps.length;j++)m[emps[j].session||""]=emps[j];var ns=document.querySelectorAll("[data-agesess]");for(var i=0;i<ns.length;i++){var e=m[ns[i].getAttribute("data-agesess")];if(!e)continue;ns[i].textContent=fmtAge(e.age);if(e.age!=null&&e.age<30)ns[i].classList.add("fresh");else ns[i].classList.remove("fresh")}}' +
'function renderList(office){var list=document.getElementById("list");list.innerHTML="";var emps=filterEmps(officeAgents(office));' +
'if(!emps.length){list.appendChild(el("div","empty",PREF.deptFilter?T("（選択中の絞り込みに該当するプロジェクトがありません）","(No projects match the current filter)"):T("（出勤中のプロジェクトがありません）","(No projects are on duty right now)")));return}' +
// R82: 今日のまとめ（サーバー無改修＝既存office_json+端末ローカルの実数だけで正直に）
'var sm=el("div","card daysum");var smHead=el("div","nm",T("📅 今日のまとめ","📅 Today"));sm.appendChild(smHead);'+
'var tk0=office.tasks||{};var tot=(tk0.pending||0)+(tk0.inProgress||0)+(tk0.completed||0);'+
'var lines=[];if(tot)lines.push(T("📋 タスク: 完了"+(tk0.completed||0)+" ・ 進行中"+(tk0.inProgress||0)+" ・ 未着手"+(tk0.pending||0),"📋 Tasks: done "+(tk0.completed||0)+" · doing "+(tk0.inProgress||0)+" · todo "+(tk0.pending||0)));'+
'var st0=sentToday();if(st0)lines.push(T("📨 今日送った指示: "+st0+"件","📨 Instructions sent today: "+st0));'+
'var an0=answeredToday();if(an0)lines.push(T("✅ 今日answerした❗: "+an0+"件","✅ Alerts answered today: "+an0));'+
'var doneRows=[];emps.forEach(function(e){var dn=(e.work&&Array.isArray(e.work.done))?e.work.done.filter(function(s){return s&&s.trim()}):[];if(dn.length)doneRows.push({name:dispCrew(e),items:dn.slice(0,3)})});'+
'if(!lines.length&&!doneRows.length){sm.appendChild(el("div","st",T("まだ今日の動きがありません","No activity yet today")))}'+
'else{lines.forEach(function(l){sm.appendChild(el("div","st",l))});'+
'doneRows.slice(0,4).forEach(function(r){var d=el("div","st");d.appendChild(el("b","",r.name+": "));d.appendChild(document.createTextNode("✅ "+r.items.join(" / ")));sm.appendChild(d)})}'+
'list.appendChild(sm);'+
'emps.slice().sort(triageSort).forEach(function(e){var card=el("div","card"+(needsAttn(e)?" alert":isPend(e)?" pend":""));card.setAttribute("data-sess",e.session||"");card.setAttribute("role","button");card.tabIndex=0;card.addEventListener("keydown",function(ev){if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();openSheet(e)}});' +
'var meta=el("div","meta");meta.appendChild(avatarNode(e,36));' +
'meta.appendChild(el("div","nm",(needsAttn(e)?"❗ ":isPend(e)?"📨 ":"")+dispCrew(e)));' +
'var dk=attnKey(e);if(isPend(e))meta.appendChild(el("span","dchip",T("📨 配達待ち","📨 queued")));else if(DELIVERED[dk])meta.appendChild(el("span","dchip ok",T("✓ 配達済み","✓ delivered")));' +
'var ageEl=el("div","age"+((e.age!=null&&e.age<30)?" fresh":""),fmtAge(e.age));ageEl.setAttribute("data-agesess",e.session||"");meta.appendChild(ageEl);card.appendChild(meta);' +
'var gb=gestureBadge(e,false);if(gb)card.appendChild(el("span","gesture",gb));' +
'var st=el("div","st");st.appendChild(el("span","dot "+(e.state||""),""));' +
'st.appendChild(document.createTextNode(" "+activityGlossPWA(e,LANG)));card.appendChild(st);' +
'if(e.question)card.appendChild(el("div","q","❓ "+e.question));' +
'var fd=(e.feed||[]).slice(0,3);if(fd.length){var fb=el("div","feed");fd.forEach(function(l){fb.appendChild(el("div","feedline",l))});card.appendChild(fb)}' +
'card.addEventListener("click",function(){openSheet(e)});list.appendChild(card)})}' +
'function QUICK(){return [{l:T("✅ 承認して進める","✅ Approve & continue"),s:T("✅ 承認","✅ Approve"),t:T("はい、そのまま進めてください。","Yes, please go ahead as planned."),c:"g"},{l:T("🛑 いったん停止して報告","🛑 Stop and report"),s:T("🛑 停止","🛑 Stop"),t:T("いったん作業を止めて、いまの状況を報告してください。","Please pause the work and report the current status."),c:"r"},{l:T("📝 進捗を1行で報告","📝 One-line progress report"),s:T("📝 報告","📝 Report"),t:T("いまの進捗を1〜2行で報告してください。","Please report your current progress in one or two lines."),c:"sub"}]}' +
'function sayText(e){e=e||{};var action=activityGlossPWA(e,LANG);var s=(e.disp||e.session||T("このプロジェクト","This project"))+T("です！"," here! ")+(action?T("いま「"+action+"」です。","Currently: "+action+"."):T("いまの状況を確認中です。","Checking the current status."));if(e.question)s+="\\n❓ "+e.question;if((e.approvalMin||0)>0)s+=T("\\n❗ 承認まちです","\\n❗ Waiting for approval");return s}' +
'function questionOptionEntries(e){if(!e||!e.question||!Array.isArray(e.questionOptions))return [];return e.questionOptions.slice(0,4).map(function(option){var raw=String((option&&option.label)||"");if(!raw.trim())return null;var recommended=raw.indexOf("(Recommended)")>=0;var clean=raw.replace(/\\s*\\(Recommended\\)\\s*/g," ").replace(/\\s{2,}/g," ").trim()||raw.trim();return {raw:raw,label:(recommended?"⭐ ":"")+clean,desc:String((option&&option.desc)||"")};}).filter(Boolean)}' +
'function appendQuestionOptions(parent,e,klass){if(!parent)return;questionOptionEntries(e).forEach(function(option){var b=el("button","qopt "+klass);b.type="button";var label=el("span","qopt-label",option.label),desc=el("span","qopt-desc",option.desc);b.appendChild(label);b.appendChild(desc);b.addEventListener("click",function(ev){ev.stopPropagation();send(T("選択肢「"+option.raw+"」でお願いします。","Please go with the option: "+option.raw),e)});parent.appendChild(b)})}' +
'function appendWorkBlock(parent,work){if(!parent||!work||typeof work!=="object")return;var block=el("div","wk-work");block.appendChild(el("div","wk-title",T("📋 いまの仕事","📋 Current work")));[["now",T("▶ いま","▶ Now")],["next",T("⏭ 次","⏭ Next")],["done",T("✅ 済み","✅ Done")]].forEach(function(pair){var values=Array.isArray(work[pair[0]])?work[pair[0]].filter(function(value){return typeof value==="string"&&value}):[];if(!values.length)return;var row=el("div","wk-row wk-"+pair[0]);row.appendChild(el("span","wk-label",pair[1]));var items=el("div","wk-items");values.forEach(function(value){items.appendChild(el("div","wk-item",value))});row.appendChild(items);block.appendChild(row)});parent.appendChild(block)}' +
'function openSheet(e){if(e)playSE("select");if(SHSAY_IV){clearInterval(SHSAY_IV);SHSAY_IV=null}var shsay=document.getElementById("shsay");shsay.textContent="";SEL=e;document.querySelectorAll(".sel").forEach(function(n){n.classList.remove("sel")});if(e&&e.session){document.querySelectorAll("[data-sess]").forEach(function(n){if(n.getAttribute("data-sess")===e.session)n.classList.add("sel")})}document.getElementById("shname").textContent=dispCrew(e);' +
'var sa=document.getElementById("shava");sa.style.setProperty("--asz","48px");setMono(sa,e);' +
'document.getElementById("shwho").textContent=activityGlossPWA(e,LANG)+" ・"+fmtAge(e.age);' +
'var sht=sayText(e);if(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches){shsay.textContent=sht}else{var shchars=Array.from(sht),shi=0;SHSAY_IV=setInterval(function(){shsay.appendChild(document.createTextNode(shchars[shi++]));if(shi%3===0)playSE("talk");if(shi>=shchars.length){clearInterval(SHSAY_IV);SHSAY_IV=null}},18)}' +
'var dt=document.getElementById("shdetail");dt.innerHTML="";appendWorkBlock(dt,e.work);' +
// R51: roster の sessions[] 内訳ミニ行（state/age/❗/📨のドットのみ・本文は構造的に持たない）
'if(Array.isArray(e.sessions)&&e.sessions.length>1){dt.appendChild(el("div","sec",T("👥 セッション内訳（"+e.sessions.length+"）","👥 Sessions ("+e.sessions.length+")")));var sw=el("div","sessrows");e.sessions.slice(0,8).forEach(function(s){var r=el("div","sessrow");r.appendChild(el("span","dot "+(s.state||""),""));r.appendChild(el("span","sessid",String(s.session||"").slice(0,8)));r.appendChild(el("span","sessage",fmtAge(s.age)));if(s.attention)r.appendChild(el("span",null,"❗"));if(s.pending)r.appendChild(el("span",null,"📨"));if(s.minions)r.appendChild(el("span",null,"👥"+s.minions));sw.appendChild(r)});if(e.sessions.length>8)sw.appendChild(el("div","sessrow",T("ほか"+(e.sessions.length-8)+"件","+"+(e.sessions.length-8)+" more")));dt.appendChild(sw)}' +
// 質問は最上部+強調（承認/回答がシートの主目的・埋もれさせない）
'if(e.question){dt.appendChild(el("div","sec",T("❓ 質問待ち — 下のボタンか自由指示で回答","❓ Question waiting — reply with a button below or a custom instruction")));var qd=el("div","said saidq",e.question);dt.appendChild(qd)}' +
'var fd=(e.feed||[]);if(fd.length){dt.appendChild(el("div","sec",T("📋 最近の動き（"+fd.length+"件）— タップで全文を拡大","📋 Recent activity ("+fd.length+") — tap to expand")));var fb=el("div","feedbox");fb.setAttribute("role","button");fb.tabIndex=0;fd.slice(0,16).forEach(function(l){fb.appendChild(el("div","feedline",l))});fb.addEventListener("click",openLog);dt.appendChild(fb)}' +
'if(e.lastSaid){dt.appendChild(el("div","sec",T("💬 直近の発言","💬 Latest message")));dt.appendChild(el("div","said",e.lastSaid))}' +
'var q=document.getElementById("quickbtns");q.innerHTML="";if(canAct()&&e&&e.projectId&&(e.state==="resting"||!e.state)){var lb=el("button","sub",T("▶ このプロジェクトを起動","▶ Launch this project"));lb.title=T("Macでこのプロジェクトのセッションを開きます","Opens a session for this project on your Mac");lb.addEventListener("click",function(){launchProject(e)});q.appendChild(lb)}if(e.question&&questionOptionEntries(e).length){var qo=el("div","quickoptions");appendQuestionOptions(qo,e,"quickoption");q.appendChild(qo)}QUICK().forEach(function(it){var b=el("button",it.c,it.l);' +
'b.onclick=function(){send(it.t)};q.appendChild(b)});'+
// R82: ユーザー定義の定型文（Macの定型ボードで作成→office_json.templatesで同期）
'((LAST_OFFICE&&LAST_OFFICE.templates)||[]).slice(0,8).forEach(function(tp){'+
'if(!tp||!tp.label||!tp.text)return;var b=el("button","sub","✳ "+tp.label);b.title=tp.text;'+
'b.onclick=function(){send(tp.text)};q.appendChild(b)});document.getElementById("freetext").value="";' +
'document.getElementById("sheetwrap").classList.add("open")}' +
'function closeSheet(){var wrap=document.getElementById("sheetwrap");if(wrap&&wrap.classList.contains("open"))playSE("cursor");if(SHSAY_IV){clearInterval(SHSAY_IV);SHSAY_IV=null}if(wrap)wrap.classList.remove("open");document.querySelectorAll(".sel").forEach(function(n){n.classList.remove("sel")})}' +
// 全文ログビューア: 「最近の動き」タップで開く（feed全件+質問+発言を大きな文字で・承認判断の材料）
'function openLog(){var e=SEL;if(!e)return;document.getElementById("lgname").textContent=(e.disp||e.session);' +
'var la=document.getElementById("lgava");la.style.setProperty("--asz","48px");setMono(la,e);' +
'document.getElementById("lgwho").textContent=activityGlossPWA(e,LANG)+" ・"+fmtAge(e.age);' +
'var b=document.getElementById("lgbody");b.innerHTML="";' +
'if(e.question){b.appendChild(el("div","lgsec",T("❓ 質問まち","❓ Question waiting")));b.appendChild(el("div","lgline",e.question))}' +
'var fd=(e.feed||[]);b.appendChild(el("div","lgsec",T("📋 最近の動き 全"+fd.length+"件（新しい順）","📋 Recent activity — all "+fd.length+" (newest first)")));' +
'fd.forEach(function(l){b.appendChild(el("div","lgline",l))});' +
'if(e.lastSaid){b.appendChild(el("div","lgsec",T("💬 直近の発言","💬 Latest message")));b.appendChild(el("div","lgline",e.lastSaid))}' +
'document.getElementById("logwrap").classList.add("open")}' +
'function closeLog(){var wrap=document.getElementById("logwrap");if(wrap&&wrap.classList.contains("open"))playSE("cursor");if(wrap)wrap.classList.remove("open")}' +
// ⚙️設定: テーマ/文字サイズ/歩行アニメはlocalStorage永続・即時適用
'var PREF={theme:localStorage.getItem("aioffice.theme")||"classic",big:localStorage.getItem("aioffice.big")==="1",deptFilter:localStorage.getItem("aioffice.deptFilter")||""};' +
'function applyPrefs(){var df=localStorage.getItem("aioffice.deptFilter");if(df!==null)PREF.deptFilter=df;document.body.classList.toggle("th-dark",PREF.theme==="dark");document.body.classList.toggle("th-big",!!PREF.big);var tc=document.querySelector("meta[name=theme-color]");if(tc)tc.setAttribute("content",PREF.theme==="dark"?"#23213a":"rgba(255,255,255,.86)")}' +
// R42.2d-2 静的チローム再適用: 焼き込みHTMLの日本語をtextContent/placeholderで差し替え（innerHTML禁止=XSS掟）
'var NTOG_STATE=false;' +
'function applyLangChrome(){document.documentElement.lang=LANG;function st(id,ja,en){var n=document.getElementById(id);if(n)n.textContent=T(ja,en)}' +
'st("banner","⚠️ オフライン・再接続中…","⚠️ Offline — reconnecting…");st("tb_office_lb","オフィス","Office");st("tb_list_lb","リスト","List");st("tb_run_lb","実行","Run");st("tb_set_lb","設定","Settings");' +
'st("st_title","⚙️ 設定","⚙️ Settings");st("st_th_lb","🎨 テーマ","🎨 Theme");st("st_th_hint","配色を切り替えます","Switch the color scheme");st("sg_th_c","クラシック","Classic");st("sg_th_d","ダーク","Dark");' +
'st("st_fs_lb","🔎 文字サイズ","🔎 Text size");st("sg_fs_s","標準","Normal");st("sg_fs_b","大きめ","Large");' +
'st("st_sd_lb","🔊 効果音","🔊 Sound effects");st("st_sd_hint","ロボット風の効果音（初期OFF）","Robot-style sound effects (off by default)");st("st_pt_lb","🔔 通知テスト","🔔 Test notification");' +
'st("st_pt_hint","登録済みの全端末へテスト通知。購読時のフィルタが通知対象になります（全部=すべて）","Sends a test push to every registered device. Your subscription filter applies (All = everything)");st("st_pt_btn","送信","Send");' +
'st("st_rp_lb","🔗 再ペアリング","🔗 Re-pair");st("st_rp_hint","この端末の登録をやり直します","Reset the registration of this device");st("st_rp_btn","解除","Unpair");st("st_close","閉じる","Close");' +
'st("su_title","📱 スマホをペアリング","📱 Pair your phone");st("su_p1","Macの AIオフィス画面（左パネル「📱 スマホ連携」）でペアリングを発行し、表示された","Issue a pairing from the AI Office screen on your Mac (left panel: 📱 Phone), then send the ");st("su_link","リンク","link");' +
'st("su_p2","をこの端末へ送って開くと自動でペアリングされます。うまくいかない時は、Mac側の「ペアリングリンクをコピー」で得たリンクを下に貼り付けてください。"," shown there to this device and open it to pair automatically. If that fails, paste the string from Copy credentials on the Mac below.");st("su_btn","この端末を登録","Register this device");' +
'st("lg_close","閉じる","Close");st("sh_send","✍️ この指示を送る","✍️ Send this instruction");st("sh_close","閉じる","Close");' +
'st("st_rn_lb","▶ 遠隔実行","▶ Remote actions");st("st_rn_hint","Macに登録した操作を実行します（登録はMacの前でのみ）","Run actions you registered on the Mac (they can only be created there)");st("st_rn_btn","開く","Open");st("rn_title","▶ 遠隔実行","▶ Remote actions");st("rn_hint","Macに登録した操作だけを実行できます","Only actions registered on your Mac can run");st("rn_close","閉じる","Close");' +
'var pb=document.getElementById("pastebox");if(pb)pb.placeholder=T("Macで表示されたペアリングリンクを貼り付け","Paste the pairing link shown on your Mac");' +
'var ft=document.getElementById("freetext");if(ft)ft.placeholder=T("自由に指示（例: キリのいいところでコミットして残タスク報告）","Custom instruction (e.g. commit at a good stopping point and report remaining tasks)");' +
'setNtog(NTOG_STATE)}' +
// R42.2d-2 office.lang=正本: status受領点で差分適用→チローム再適用→LAST_SIG=""で全再描画を強制
'function applyOfficeLang(lang){var v=lang==="en"?"en":"ja";if(v===LANG)return;LANG=v;try{localStorage.setItem("aioffice.lang",v)}catch(e){}applyLangChrome();LAST_SIG=""}' +
'function empDept(e){var d=String((e&&e.dept)||(e&&e.name)||"").trim();if(d)return Array.from(d).slice(0,40).join("");var n=String((e&&e.disp)||"").trim();return Array.from((n&&n.split(/\\s+/)[0])||"").slice(0,40).join("")}' +
'function filterEmps(emps){var a=(Array.isArray(emps)?emps:[]).filter(function(e){return !!e});return PREF.deptFilter?a.filter(function(e){return empDept(e)===PREF.deptFilter}):a}' +
// R42.1 エディション機能フラグ（office.edition.features・未定義はtrue=旧server後方互換）
'function featOn(name){var f=LAST_OFFICE&&LAST_OFFICE.edition&&LAST_OFFICE.edition.features;return !f||f[name]!==false}' +
// R79-5: 部署フィルタはリストタブ専用（3D/❗ドックは常に全員＝「フィルタ中も全員立っている」を仕様に昇格）
'function buildDeptbar(emps){var bar=document.getElementById("deptbar");if(!bar)return;bar.innerHTML="";var ds=[];(Array.isArray(emps)?emps:[]).forEach(function(e){var d=empDept(e);if(d&&ds.indexOf(d)<0)ds.push(d)});var show=ds.length>=2&&VIEW==="list";bar.style.display=show?"":"none";if(!show)return;function add(d,label){var b=el("button","deptchip",label);var on=PREF.deptFilter===d;b.classList.toggle("on",on);b.setAttribute("aria-pressed",on?"true":"false");b.addEventListener("click",function(){setDeptFilter(d)});bar.appendChild(b)}add("",T("全部","All"));ds.forEach(function(d){add(d,d)})}' +
'function setDeptFilter(d){PREF.deptFilter=String(d||"");localStorage.setItem("aioffice.deptFilter",PREF.deptFilter);dispatch()}' +
'function markSeg(){function m(id,on){var b=document.getElementById(id);if(b)b.classList.toggle("on",!!on)}m("sg_th_c",PREF.theme!=="dark");m("sg_th_d",PREF.theme==="dark");m("sg_fs_s",!PREF.big);m("sg_fs_b",PREF.big);m("sg_sd_on",AUDIO.enabled);m("sg_sd_off",!AUDIO.enabled)}' +
'function setTheme(t){PREF.theme=t;localStorage.setItem("aioffice.theme",t);applyPrefs();markSeg()}' +
'function setBig(v){PREF.big=v;localStorage.setItem("aioffice.big",v?"1":"0");applyPrefs();markSeg()}' +
// R80.6: 設定を「状態が読める場所」へ（ユーザーFB「PC版にあるリソースやライセンスが無い」）。
// ライセンス=edition.features から導出／リソース=office_json.res(Claude枠%)+relay(中継使用量)。
// どちらも新しい秘密は運ばない（%とレベルの整数だけ＝redaction設計の内側）。
// R80.7: 下部ゲージ帯（ユーザーFB「下にゲージ・消費クレジットとか」）。Claude枠%と中継%。
// R82: res v2（providers[]）対応。旧サーバー（fiveHour/sevenDayのみ）はclaude行を合成=版ズレ両対応
'function resProviders(){var res=(LAST_OFFICE||{}).res||{};if(Array.isArray(res.providers)&&res.providers.length)return res.providers;'+
'var bars=[];if(typeof res.fiveHour==="number")bars.push({k:"5h",pct:Math.round(res.fiveHour)});'+
'if(typeof res.sevenDay==="number")bars.push({k:T("7日","7d"),pct:Math.round(res.sevenDay)});'+
'return bars.length?[{id:"claude",label:"Claude",bars:bars,staleSec:res.staleSec}]:[]}'+
'function pinnedProvider(){var ps=resProviders();if(!ps.length)return null;var want=localStorage.getItem("aioffice.gaugePin")||"claude";'+
'for(var i=0;i<ps.length;i++)if(ps[i].id===want&&ps[i].bars.length)return ps[i];'+
'for(var j=0;j<ps.length;j++)if(ps[j].bars.length)return ps[j];return ps[0]}'+
// R82-sec(S1): サーバー由来の pv.label/b0.k を innerHTML 連結していた＝XSS掟の唯一の破れ。
// paintRes と同じ el()＝textContent 構築へ統一（label/kは中継経由で攻撃者制御可能なため）。
'function paintGauges(){var gb=document.getElementById("gaugebar");if(!gb)return;var o=LAST_OFFICE||{};var rl=o.relay||{};'+
'function bar(lb,pct){var p=Math.max(0,Math.min(100,Math.round(pct)));var d=el("div","gg"+(p>=90?" hot":p>=70?" warn":""));d.appendChild(el("span","lb",lb));var tr=el("span","tr");var i=el("i");i.style.width=Math.max(2,p)+"%";tr.appendChild(i);d.appendChild(tr);d.appendChild(el("b","",p+"%"));gb.appendChild(d)}'+
'gb.innerHTML="";var pv=pinnedProvider();'+
'if(pv){var pc=el("div","gg gg-p");pc.appendChild(el("span","lb","⚡"+pv.label));gb.appendChild(pc);'+
'pv.bars.slice(0,2).forEach(function(b0){bar(b0.k,b0.pct)})}'+
'if(typeof rl.pct==="number")bar(T("📡中継","📡Relay"),rl.pct);'+
'paintTicker(false)}'+
// R81-4: ライブ活動ティッカー（殺風景対策の本体＝「誰が今なにを」が常に流れる）。
// 4.5秒ローテ・フェード・タップでそのプロジェクトのシート。reduced-motionは即時切替。
'var TICKER_I=0,TICKER_CUR=null;'+
'function paintTicker(adv){var tk=document.getElementById("ticker");if(!tk)return;var emps=officeAgents(LAST_OFFICE)||[];if(!emps.length){tk.innerHTML="";TICKER_CUR=null;return}'+
'if(adv)TICKER_I=(TICKER_I+1)%emps.length;if(TICKER_I>=emps.length)TICKER_I=0;'+
'var e=emps[TICKER_I];if(!e)return;var sess=e.session||"";if(!adv&&TICKER_CUR===sess){var ga=tk.querySelector(".tk-act");if(ga){var g2=activityGlossPWA(e,LANG)||"";if(ga.textContent!==g2)ga.textContent=g2}return}'+
'TICKER_CUR=sess;'+
'function swap(){tk.innerHTML="";var m=el("span","mono");setMono(m,e);tk.appendChild(m);'+
'tk.appendChild(el("span","tk-nm",dispCrew(e)));'+
'tk.appendChild(el("span","tk-act",activityGlossPWA(e,LANG)||""));'+
'var tk1=(LAST_OFFICE||{}).tasks||{};var tot1=(tk1.pending||0)+(tk1.inProgress||0)+(tk1.completed||0);'+
'if(tot1)tk.appendChild(el("span","tk-n","📋"+(tk1.completed||0)+"/"+tot1));'+
'tk.appendChild(el("span","tk-n",(TICKER_I+1)+"/"+emps.length));'+
'tk.classList.remove("fade")}'+
'if(adv&&!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches)){tk.classList.add("fade");setTimeout(swap,180)}else swap()}'+
'setInterval(function(){if(document.hidden||VIEW!=="office")return;paintTicker(true)},6000);'+
// R81-4: ⚡リソースシート＝ゲージの意味を全部ここで説明する（「謎の中継ゲージ」FB対策）
'function paintRes(){var b=document.getElementById("rs_body");if(!b)return;b.innerHTML="";var o=LAST_OFFICE||{};var res=o.res||{};var rl=o.relay||{};var tk0=o.tasks||{};'+
'function sec(s){b.appendChild(el("div","rs-sec",s))}'+
'function note(s){b.appendChild(el("div","rs-note",s))}'+
'function bar(lb,pct){var d=el("div","rs-bar"+(pct>=90?" hot":pct>=70?" warn":""));d.appendChild(el("span","lb",lb));var tr=el("span","tr");var i=el("i");i.style.width=Math.max(2,Math.min(100,Math.round(pct)))+"%";tr.appendChild(i);d.appendChild(tr);d.appendChild(el("b","",Math.round(pct)+"%"));b.appendChild(d)}'+
// R82: 多プロバイダ＝チップでピン切替（下のバーに表示）＋全プロバイダのセクション
'var ps=resProviders();'+
'if(ps.length){'+
'sec(T("⚡ AIの利用枠 — バーに表示する枠を選べます","⚡ AI quotas — pick one for the bar"));'+
'var pinId=(pinnedProvider()||{}).id;var chips=el("div","rs-chips");'+
'ps.forEach(function(pv){var c=el("button","rs-chip"+(pv.id===pinId?" on":""),pv.label);c.type="button";'+
'c.addEventListener("click",function(){localStorage.setItem("aioffice.gaugePin",pv.id);paintGauges();paintRes();note(T("⚡ バーに「"+pv.label+"」を表示します","⚡ Bar now shows "+pv.label))});'+
'chips.appendChild(c)});b.appendChild(chips);'+
'ps.forEach(function(pv){'+
'if(pv.bars.length){pv.bars.forEach(function(b0){bar(pv.label+" "+b0.k,b0.pct)})}'+
'else if(pv.note==="cap-none")note(pv.label+T(": 上限が設定されていません（実額はMacの⚡リソースで確認）",": no cap set — see the Mac dashboard for spend"));'+
'if(typeof pv.staleSec==="number"&&pv.staleSec>120)note(pv.label+T("は"+Math.round(pv.staleSec/60)+"分前の実測です",": measured "+Math.round(pv.staleSec/60)+" min ago"))});'+
'note(T("100%に達すると、その枠がリセットされるまで応答しません。","At 100% the provider stops responding until the window resets."))}'+
'else note(T("MacのAI Officeを最新版に更新すると表示されます。","Update AI Office on your Mac to see this."));'+
'sec(T("📡 スマホ中継の使用量","📡 Relay usage"));'+
'if(typeof rl.pct==="number"){bar(T("今日","Today"),rl.pct);'+
'note(T("このスマホ連携が使うあなたのCloudflare無料枠の消費量（毎日リセット）。80%を超えると自動で通信を減らし、枠切れを防ぎます。","How much of your Cloudflare free tier this phone link used today. Above 80% it slows itself down."))}'+
'else note(T("接続が確立すると表示されます。","Shown once the relay is connected."));'+
'var tot=(tk0.pending||0)+(tk0.inProgress||0)+(tk0.completed||0);'+
'if(tot){sec(T("📋 タスクの進み","📋 Task progress"));'+
'note(T("完了 "+(tk0.completed||0)+" ・ 進行中 "+(tk0.inProgress||0)+" ・ 未着手 "+(tk0.pending||0),"Done "+(tk0.completed||0)+" · In progress "+(tk0.inProgress||0)+" · Pending "+(tk0.pending||0)))}'+
'sec(T("🧾 ライセンス","🧾 License"));'+
'var ed=(o.edition&&o.edition.edition)||"";'+
'note(featOn("relayPwa")?T("Pro 有効 — スマホ連携・プッシュ通知・遠隔実行・コスト表示が使えます"+(ed?"（"+ed+"）":""),"Pro active — phone link, push, remote actions, cost view"+(ed?" ("+ed+")":"")):T("無料版 — スマホ連携はProライセンスで解錠します","Free — phone link unlocks with a Pro license"))}'+
'function openRes(){paintRes();playSE("select");document.getElementById("reswrap").classList.add("open")}'+
'function closeRes(){var w=document.getElementById("reswrap");if(w&&w.classList.contains("open"))playSE("cursor");if(w)w.classList.remove("open")}'+
'function paintSettingsInfo(){var o=LAST_OFFICE||{};'+
'var lic=document.getElementById("st_lic_hint");if(lic){var ed=(o.edition&&o.edition.edition)||"";'+
'lic.textContent=featOn("relayPwa")?T("Pro 有効（スマホ連携・通知・遠隔実行）","Pro active (phone link, push, remote actions)")+(ed?" · "+ed:""):T("無料版（スマホ連携はProで解錠）","Free (phone link unlocks with Pro)")}'+
'var rs=document.getElementById("st_res_hint");if(rs){var parts=[];var res=o.res||{};'+
'if(typeof res.fiveHour==="number")parts.push(T("Claude 5時間枠 ","Claude 5h ")+Math.round(res.fiveHour)+"%");'+
'if(typeof res.sevenDay==="number")parts.push(T("7日枠 ","7d ")+Math.round(res.sevenDay)+"%");'+
'var rl=o.relay||{};if(typeof rl.pct==="number")parts.push(T("中継使用量 ","Relay ")+Math.round(rl.pct)+"%"+(rl.level>=2?T("（自動減速中）"," (throttled)"):rl.level>=1?T("（減速中）"," (slowed)"):""));'+
'rs.textContent=parts.length?parts.join(" · "):T("MacのAI Officeを更新すると表示されます","Update AI Office on your Mac to see gauges")}}'+
'function openSettings(){markSeg();paintSettingsInfo();document.getElementById("setwrap").classList.add("open")}' +
// R80.6: 休眠プロジェクトの起動（引き当てはMac側launchと同じ「過去に開いた実在プロジェクト」のみ）
'function paintLaunch(){var host=document.getElementById("ln_list");if(!host)return;host.innerHTML="";'+
'var o=LAST_OFFICE||{};var known=Array.isArray(o.launchable)?o.launchable:[];'+
'if(!canAct()){host.appendChild(el("div","empty",T("このMacは遠隔起動に未対応です（AI Officeを更新してください）","This Mac does not support remote launch yet (update AI Office)")));return}'+
'var act={};officeAgents(o).forEach(function(e){if(e&&e.projectId&&e.state!=="resting")act[e.projectId]=1});'+
'var rows=known.filter(function(pj){return pj&&pj.projectId&&!act[pj.projectId]});'+
'if(!rows.length){host.appendChild(el("div","empty",T("起動できる休眠プロジェクトがありません（全部出勤中です）","No dormant projects to launch (everything is on duty)")));return}'+
'rows.slice(0,12).forEach(function(pj){var b=el("button","qopt");b.type="button";'+
'b.appendChild(el("span","qopt-label","\u25b6 "+(pj.name||pj.projectId)));'+
'b.appendChild(el("span","qopt-desc",T("最終稼働: ","Last active: ")+fmtAge(pj.ageSec)));'+
'b.addEventListener("click",function(){launchProject({projectId:pj.projectId,disp:pj.name||pj.projectId});closeLaunch()});'+
'host.appendChild(b)})}'+
'function openLaunch(){paintLaunch();document.getElementById("lnwrap").classList.add("open")}'+
'function closeLaunch(){var w=document.getElementById("lnwrap");if(w&&w.classList.contains("open"))playSE("cursor");if(w)w.classList.remove("open")}'+
// ── R79-10 遠隔実行（許可リスト） ────────────────────────────────────────
// 送信は既存の sign() をそのまま使う（canonical/KATは1バイトも触らない）。
// session を "act-<16hex>" にするだけで、Mac側 relay_agent が act-分岐で daemon へ回す。
'function actionsView(){var a=LAST_OFFICE&&LAST_OFFICE.actions;return (a&&typeof a==="object")?a:{recipes:[],results:[],caps:{}}}' +
'function canAct(){var c=actionsView().caps||{};return c.actions===1}' +
'function actSession(){var b=new Uint8Array(8);crypto.getRandomValues(b);return "act-"+toHex(b.buffer)}' +
'function newReqId(){var b=new Uint8Array(8);crypto.getRandomValues(b);return "r"+toHex(b.buffer)}' +
'function sendAction(payload,label){if(SENDING)return;var cred=getCred();if(!cred)return;' +
'if(STALE&&!confirm(T("Macがしばらく同期していません。届くまで実行されませんが送信しますか？","Your Mac has not synced recently. Send anyway?")))return;' +
'SENDING=true;note(T("▶ 実行を依頼中…","▶ Requesting…"));' +
'sign(cred,actSession(),JSON.stringify(payload)).then(function(env){' +
'return fetch("/instruct",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+cred.t},body:JSON.stringify(env)})})' +
'.then(function(r){return r.json()}).then(function(d){' +
'if(d&&d.ok){note(T("▶ 依頼しました: ","▶ Requested: ")+label);playSE("send");setTimeout(function(){poll()},1500)}' +
'else note("⚠ "+((d&&d.error)||T("失敗","failed")))})' +
'.catch(function(){note(T("⚠ 送信できませんでした","⚠ Could not send"))}).then(function(){SENDING=false})}' +
'function runRecipe(r){if(r.dangerous&&!confirm(T("「"+r.label+"」は危険操作です。Macの前でも確認を求められます。実行を依頼しますか？","\\""+r.label+"\\" is marked dangerous. You will also be asked to confirm on the Mac. Request it?")))return;' +
'sendAction({aioffice:1,kind:"run",recipe:r.id,args:[],reqId:newReqId()},r.label)}' +
'function launchProject(e){if(!e||!e.projectId)return;sendAction({aioffice:1,kind:"launch",project:e.projectId,reqId:newReqId()},dispCrew(e))}' +
'function stateChip(s){return s==="running"?"⏳":s==="done"?"✅":s==="failed"?"⚠️":s==="timeout"?"⌛":s==="busy"?"🔁":s==="denied"?"⛔":"•"}' +
'function paintRun(){var host=document.getElementById("rn_list"),res=document.getElementById("rn_results");if(!host||!res)return;var a=actionsView();host.innerHTML="";res.innerHTML="";' +
'if(!canAct()){host.appendChild(el("div","empty",T("このMacは遠隔実行に未対応です（AI Officeを更新してください）","This Mac does not support remote actions yet (update AI Office)")));return}' +
'var rs=a.recipes||[];if(!rs.length){host.appendChild(el("div","empty",T("実行できる操作がまだありません。Macのオフィス画面で登録してください（安全のため、スマホからは追加できません）","No actions registered yet. Add them on your Mac (for safety they cannot be created from the phone)")))}' +
'rs.forEach(function(r){var b=el("button","qopt");b.type="button";b.appendChild(el("span","qopt-label",(r.dangerous?"⚠️ ":"▶ ")+r.label));b.appendChild(el("span","qopt-desc",r.returnOutput==="none"?T("結果のみ受け取る","status only"):T("出力の一部を受け取る","returns output")));b.addEventListener("click",function(){runRecipe(r)});host.appendChild(b)});' +
'var results=a.results||[];if(!results.length)return;res.appendChild(el("div","sec",T("🧾 最近の実行","🧾 Recent runs")));' +
'results.slice(0,6).forEach(function(x){var row=el("div","sessrow");row.appendChild(el("span",null,stateChip(x.state)));row.appendChild(el("span","nm",x.label||x.recipe||x.kind));' +
'var meta=(x.state==="running")?T("実行中","running"):((x.exitCode!=null?"exit "+x.exitCode+" ":"")+Math.round((x.durationMs||0)/100)/10+"s");row.appendChild(el("span","sessage",meta));res.appendChild(row);' +
'if(x.output){var o=el("div","said");o.textContent=x.output;res.appendChild(o)}})}' +
'function openRun(){paintRun();document.getElementById("runwrap").classList.add("open")}' +
'function closeRun(){var w=document.getElementById("runwrap");if(w&&w.classList.contains("open"))playSE("cursor");if(w)w.classList.remove("open")}' +
'function closeSettings(){var wrap=document.getElementById("setwrap");if(wrap&&wrap.classList.contains("open"))playSE("cursor");if(wrap)wrap.classList.remove("open")}' +
'function setSound(v){AUDIO.enabled=!!v;try{if(AUDIO.enabled)localStorage.setItem("aioffice.sound","1");else localStorage.removeItem("aioffice.sound")}catch(_){}markSeg();if(AUDIO.enabled){var ctx=ensureCtx();if(ctx){try{ctx.resume().catch(()=>{})}catch(_){}}playSE("select")}}' +
'function sendTestPush(){pushApi("/push/test",{}).then(function(d){if(d&&d.ok){note(d.sent?T("🔔 テスト通知を送信しました（"+d.sent+"台）","🔔 Test notification sent ("+d.sent+" devices)"):T("⚠ 通知登録が0台です。先にヘッダーの🔕をONにしてください","⚠ No devices subscribed. Turn on 🔕 in the header first"))}else{note("⚠ "+((d&&d.error)||T("送信失敗","send failed")))}}).catch(function(){note(T("⚠ 送信できませんでした","⚠ Could not send"))})}' +
'function repair(){if(!confirm(T("この端末のペアリングを解除して、登録画面に戻ります。よろしいですか？","Unpair this device and return to the setup screen. OK?")))return;localStorage.removeItem(KEY);location.reload()}' +
'function sendFree(){var v=document.getElementById("freetext").value.trim();if(v)send(v)}' +
'function send(text,target){if(SENDING)return;target=target||SEL;var cred=getCred();if(!cred||!target)return;' +
// R51: stale中の送信は1回confirm（Macが受信するまで届かない＝{ok:true}トーストの無言ロストを塞ぐ）
'if(STALE&&!confirm(T("Macがしばらく同期していません。Macが再同期するまで指示は届きませんが送信しますか？","Your Mac has not synced recently. The instruction will not arrive until it reconnects. Send anyway?")))return;' +
'SENDING=true;var session=target.session;closeSheet();' +
'note(T("送信中…","Sending…"));sign(cred,session,text).then(function(env){' +
'return fetch("/instruct",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+cred.t},body:JSON.stringify(env)})})' +
'.then(function(r){return r.json()}).then(function(d){if(d&&d.ok){note(T("📨 Macへ送信しました","📨 Sent to your Mac"));bumpSentLog();if(needsAttn(target)){ATTN_SENT[attnKey(target)]=Date.now();saveAttnSent();updateAttnCards(officeAgents(LAST_OFFICE))}playSE("send");setTimeout(function(){poll()},1200)}else note("⚠ "+((d&&d.error)||T("失敗","failed")))})' +
'.catch(function(){note(T("⚠ 送信できませんでした","⚠ Could not send"))}).then(function(){SENDING=false})}' +
'function note(m){var n=document.getElementById("note");if(!n)return;n.textContent=m;var err=m.indexOf("⚠")===0;n.className="show"+(err?" err":"");if(n._t){clearTimeout(n._t);n._t=null}if(m.indexOf(T("送信中","Sending"))<0){n._t=setTimeout(function(){n.className=""},err?4200:5200)}}' +
'var VIEW=(localStorage.getItem("aioffice.view")||"office");' +
'var LAST_OFFICE={};var LAST_SIG="",LAST_VIEW="",POLL_IV=null,ATTN_SESSIONS={};'+
'var SCENE3D="pending";' +
'function sceneSig(emps){return PREF.deptFilter+"|"+emps.map(function(e){return (e.session||"")+"|"+(e.disp||"")+"|"+empDept(e)+"|"+(e.state||"")+"|"+(e.verb||"")+"|"+(e.target||"")+"|"+((e.feed&&e.feed[0])||"")+"|"+(e.question||"")+"|"+(e.pending?1:0)+"|"+(e.minions||0)+"|"+(needsAttn(e)?1:0)+"|"+(e.stuckTool||"")+"|"+(e.approvalMin||0)+"|"+(e.sprite||"")+"|"+(e.projectId||"")+"|"+(e.crew||0)+"|"+((e.work&&e.work.now&&e.work.now[0])||"")}).join(";")+"|D:"+Object.keys(DELIVERED).join(",")}' +
'function needsAttn(e){return (e.approvalMin>0)||!!e.question}' +
'function attnSessionSet(emps){var set={};(Array.isArray(emps)?emps:[]).forEach(function(e){if(e&&needsAttn(e)){var k=attnKey(e);if(k)set[k]=1}});return set}' +
'function checkAttnEdge(emps){var next=attnSessionSet(emps),newly=Object.keys(next).filter(function(s){return !ATTN_SESSIONS[s]});ATTN_SESSIONS=next;if(newly.length)playSE("attn")}' +
'function isPend(e){return !!e.pending&&!needsAttn(e)}' +
// R79: アバター＝モノグラム（名前1文字＋状態リング）。ドット絵の立ち絵は2D時代の遺物で、
// デスクトップ3Dは一切使っていなかった（スプライト同梱2.4MBの存在理由がここだけだった）。
// 状態リングの色は意味色の規約どおり: 緑=作業中/琥珀=待機/赤=❗/青=外部(3DのACCENTS.externalと同系)
'function monoChar(e){var a=Array.from(String(dispCrew(e)||"?"));return (a[0]||"?").toUpperCase()}'+
'function setMono(n,e){n.textContent=monoChar(e);n.className="mono st-"+((e&&e.state)||"idle")+((e&&e.external)?" ext":"")+(needsAttn(e)?" attn":"")}'+
'function avatarNode(e,px){var n=el("span","mono");if(px)n.style.setProperty("--asz",px+"px");setMono(n,e);return n}' +
'function loadOfficeCache(){try{var raw=localStorage.getItem("aioffice.lastOffice");return raw?JSON.parse(raw):null}catch(_){return null}}' +
'function saveOfficeCache(office){try{localStorage.setItem("aioffice.lastOffice",JSON.stringify(office))}catch(_){}}' +
'function gestureBadge(e,overflow){if(overflow)return "💧";if(e&&e.state==="resting")return "☕";var v=String((e&&e.verb)||"");if(v.indexOf("考え中")===0)return "💭";if(e&&e.state==="working")return "⌨";return ""}' +
'function stateSort(a,b){var x=a.session||"",y=b.session||"";return x<y?-1:x>y?1:0}' +
// トリアージ順: ❗要対応 → 📨保留 → 🟢作業中 → 🟡待機 → 💤休憩（同ランクはsession安定順）
'function rankEmp(e){return needsAttn(e)?0:isPend(e)?1:e.state==="working"?2:e.state==="waiting"?3:4}' +
// PWA_TRIAGE_BEGIN
// R80-A11: ❗同士の並び順の正本は **ui/core/world.js の attentionQueue**（デスクトップが使う）。
// 従来スマホは session の辞書順で並べていたため、**同じ❗キューなのにMacとスマホで
// 「最優先の1件」が別人**になっていた（Macで「Aさんに答えて」と言われスマホを開くとBさん）。
// 同じ規則をここへ移植する: ①15分超の承認まちを質問より先へ昇格 ②質問 ③その他の承認まち、
// 同ランクは待たせた分数の降順。ロジックを変えるときは world.js と両方直すこと。
'var STARVE_MIN=15;' +
'function attnRank(e){if(!e.question&&(Number(e.approvalMin)||0)>=STARVE_MIN)return 0;return e.question?1:2}' +
'function triageSort(a,b){var r=rankEmp(a)-rankEmp(b);if(r!==0)return r;' +
'if(needsAttn(a)&&needsAttn(b)){var ra=attnRank(a),rb=attnRank(b);if(ra!==rb)return ra-rb;' +
'var d=(Number(b.approvalMin)||0)-(Number(a.approvalMin)||0);if(d!==0)return d}' +
'return stateSort(a,b)}' +
// PWA_TRIAGE_END
// R79-5: officeタブは固定画面＝scrollY退避は不要。ドックは3Dを覆わない高さに収める
'function updateAttnCards(emps){var host=document.getElementById("attncards");if(!host)return;var need=(Array.isArray(emps)?emps:[]).filter(needsAttn).slice().sort(triageSort);host.innerHTML="";host.classList.toggle("on",need.length>0);' +
'need.slice(0,1).forEach(function(e){var key=attnKey(e),card=el("article","card alert attncard");card.setAttribute("data-attn-sess",key);var head=el("div","attnhead"),img=avatarNode(e,30);head.appendChild(img);head.appendChild(el("div","attnname","❗ "+(dispCrew(e)||key)));card.appendChild(head);card.appendChild(el("div","attnq",e.question?"❓ "+e.question:T("❗ 承認が必要です","❗ Approval needed")));if(e.question&&questionOptionEntries(e).length){var options=el("div","attnoptions");appendQuestionOptions(options,e,"attnoption");card.appendChild(options)}var actions=el("div","attnactions");QUICK().forEach(function(it){var b=el("button",it.c,it.s||it.l);b.title=it.l;b.addEventListener("click",function(ev){ev.stopPropagation();send(it.t,e)});actions.appendChild(b)});var free=el("button","sub",T("✍️ 自由に","✍️ Custom"));free.addEventListener("click",function(ev){ev.stopPropagation();openSheet(e)});actions.appendChild(free);card.appendChild(actions);if(ATTN_SENT[key])card.appendChild(el("div","attnsent",T("📨 送信済み","📨 Sent")));host.appendChild(card)});' +
// 2件目=1行ミニカード（タップでシートへ）。3件目以降はリストへ誘導＝ドックが3Dを覆い尽くさない
'need.slice(1,2).forEach(function(e){var key=attnKey(e),mini=el("article","card alert attncard attnmini");mini.setAttribute("data-attn-sess",key);mini.appendChild(el("span","attnname","❗ "+(dispCrew(e)||key)));mini.appendChild(el("span","attnq",e.question?e.question:T("承認が必要です","Approval needed")));mini.appendChild(el("span","attngo",T("回答 ›","Reply ›")));mini.addEventListener("click",function(){openSheet(e)});host.appendChild(mini)});' +
'if(need.length>2){var more=el("button","attnmore",T("ほか+"+(need.length-2)+"件","+"+(need.length-2)+" more"));more.setAttribute("aria-label",T("ほか"+(need.length-2)+"件の要対応をリストで表示","Show "+(need.length-2)+" more items that need attention in the list"));more.addEventListener("click",function(){setView("list")});host.appendChild(more)}}' +
// R77: スマホも3Dオフィス（デスクトップと同じ IsoScene）。3Dが起動しない端末
// （WebGL不可・モジュール未取得）はリスト表示へ自動退避する（2DマップはR79-2で全撤去）。
// #attncards（❗トリアージ）は据え置き＝スマホの主目的を落とさない。
'function sceneShell3D(){var room=document.getElementById("room");if(!room)return null;'+
'var wrap=el("div",null);wrap.id="scene3dwrap";var host=el("div",null);host.id="scene3d";var plates=el("div",null);plates.id="plates";wrap.appendChild(host);wrap.appendChild(plates);room.appendChild(wrap);'+
// R79-5: ❗トリアージとロスターは下部ドック（親指圏）。既存ノードは移設＝id重複(B7)を再発させない
'var top=el("div",null);top.id="topdock";var rs=document.getElementById("roster");if(!rs){rs=el("div",null);rs.id="roster"}top.appendChild(rs);room.appendChild(top);'+
'var dock=el("div",null);dock.id="dock";var cards=document.getElementById("attncards");if(!cards){cards=el("section",null);cards.id="attncards";cards.setAttribute("aria-live","polite")}dock.appendChild(cards);'+
// ticker/gaugebar/infodock も attncards/roster と同じ**再利用ガード**（B7: sceneShell3Dは
// 再描画のたび走る＝素で作るとid重複し、描画対象と表示個体が別物になって空白が出る。再発2敗目）
'var info=document.getElementById("infodock");if(!info){info=el("div",null);info.id="infodock";'+
'var tk=el("button",null);tk.id="ticker";tk.type="button";tk.addEventListener("click",function(){var e=TICKER_CUR&&officeAgents(LAST_OFFICE).filter(function(x){return x&&x.session===TICKER_CUR})[0];if(e)openSheet(e)});info.appendChild(tk);'+
'var gb=el("button",null);gb.id="gaugebar";gb.type="button";gb.title=T("タップで詳細（AI利用枠・中継・ライセンス）","Tap for details (AI quota, relay, license)");gb.addEventListener("click",openRes);info.appendChild(gb)}'+
'dock.appendChild(info);room.appendChild(dock);'+
// R80.6: タップは2段（ユーザーFB「いきなりウィンドウでなく、まずフォーカスして
// どういう作業のアバターか浮かび上がってほしい」）。1度目=カメラが寄る+選択名札+
// 「誰が・何を」トースト。同じロボの2度目=シート。空きタップ=選択解除+カメラ戻し。
// 素早い連続タップはOSが2発目を dblclick にすることがある＝同じハンドラを両方へ
// 2度目の空振り救済: 1度目でカメラが寄る＝ロボが画面上を移動するので、同じ場所への
// 2度目タップはpickを外しやすい（実機で「タップが効かない」と見えた本体）。
// 選択中ロボの現在位置から180px以内なら「2度目=詳細」とみなす。遠い空タップ=選択解除。
'function hostTap(ev){if(!window.__scene3d)return;if(window.__scene3d.gestureMoved&&window.__scene3d.gestureMoved())return;var r=host.getBoundingClientRect();var tx=ev.clientX-r.left,ty=ev.clientY-r.top;var id=window.__scene3d.pick(tx,ty);if(!id){if(SEL){var ags=window.__scene3d.agents()||[],cur=null;for(var i=0;i<ags.length;i++)if(ags[i].session===SEL.session){cur=ags[i];break}var pp=cur&&window.__scene3d.project(cur.id);if((Date.now()-SEL_AT<4000)||(pp&&Math.hypot(pp.left-tx,pp.top-ty)<180)){var fresh=cur&&empOfAgent(cur.id);openSheet(fresh||SEL);return}SEL=null;document.querySelectorAll(".sel").forEach(function(n){n.classList.remove("sel")});window.__scene3d.focus(null)}return}tapAgent(id)}'+
'host.addEventListener("click",hostTap);host.addEventListener("dblclick",hostTap);'+
'var s=document.createElement("script");s.type="module";s.src="/ui/pwa/boot3d.js";s.onerror=scene3dFailed;document.body.appendChild(s);setTimeout(function(){if(SCENE3D==="pending")scene3dFailed()},4000);return host}'+
// シーンのagent(id=projectId or session) から /status の社員を引く単一の対応点
'function empOfAgent(id){if(!window.__scene3d)return null;var ags=window.__scene3d.agents()||[];var ag=null;for(var i=0;i<ags.length;i++)if(ags[i].id===id){ag=ags[i];break}var sess=ag?ag.session:id;var list=officeAgents(LAST_OFFICE)||[];for(var j=0;j<list.length;j++)if(list[j]&&list[j].session===sess)return list[j];return null}'+
// R79-6→R80.5: 名札（ユーザーFB「どのロボットがどのエージェントか分からない」×2回）。
// 6体以下=全員テキスト名札（識別が最優先・重なりはdrop()が下へ逃がす）。7体以上=二段marker
// （①全員=足元モノグラムピン ②選択中/❗先頭=テキスト名札）＝390pxで名札が潰れる密度への退避。
// ノードはid keyedで再利用＝毎フレームの全DOM走査をやめる（B12）。重なりは下へ逃がして解消（デスクトップpaintLabelsと同型）
// R80.6: 2段タップの単一実装（canvas・名札の両方から呼ぶ）。1度目=フォーカス+選択+
// 「誰が・何を」トースト（まず注目させる）。同じ対象の2度目=詳細シート。
'var SEL_AT=0;'+
'function tapAgent(id){var e=empOfAgent(id);if(!e)return;if(SEL&&SEL.session===e.session){openSheet(e);return}SEL=e;SEL_AT=Date.now();playSE("select");document.querySelectorAll(".sel").forEach(function(n){n.classList.remove("sel")});document.querySelectorAll("[data-sess]").forEach(function(n){if(n.getAttribute("data-sess")===e.session)n.classList.add("sel")});if(window.__scene3d&&window.__scene3d.focus)window.__scene3d.focus(id);if(window.__scene3d&&window.__scene3d.greet)window.__scene3d.greet(id);note(dispCrew(e)+" \u2014 "+activityGlossPWA(e,LANG)+T("（もう一度タップで詳細）"," (tap again for details)"))}'+
'var PLATE_NODES={};'+
'function paintPlates(){var layer=document.getElementById("plates");var s3=window.__scene3d;if(!layer||!s3)return;var ags=s3.agents()||[];var W=layer.clientWidth||0;'+
'var emps=officeAgents(LAST_OFFICE)||[],bySess={};emps.forEach(function(x){if(x&&x.session)bySess[x.session]=x});'+
'var attnFirst=emps.filter(needsAttn).slice().sort(triageSort)[0]||null;'+
'var seen={},placed=[];'+
'function drop(l,t,w,h){for(var g=0;g<6;g++){var hit=null;for(var i=0;i<placed.length;i++){var q=placed[i];if(l-w/2<q.r+3&&l+w/2>q.l-3&&t<q.b+2&&t+h>q.t-2){hit=q;break}}if(!hit)break;t=hit.b+2}placed.push({l:l-w/2,r:l+w/2,t:t,b:t+h});return t}'+
'var showAll=ags.length<=6;'+
'ags.forEach(function(a){var e=bySess[a.session]||null;var at=(s3.anchor&&s3.anchor(a.id))||s3.project(a.id);if(!at||!isFinite(at.left)||!isFinite(at.top))return;'+
'var attn=e?needsAttn(e):!!a.attention;var sel=!!(SEL&&e&&SEL.session===e.session);var text=showAll||sel||!!(attn&&attnFirst&&e&&e.session===attnFirst.session);'+
'var kind=text?"plate":"pin";var n=PLATE_NODES[a.id];'+
'if(n&&n.getAttribute("data-kind")!==kind){n.remove();n=null}'+
'if(!n){if(text){n=el("button","plate");n.type="button";n.appendChild(el("i","dot"));n.appendChild(el("span","nm",""));(function(id){n.addEventListener("click",function(){tapAgent(id)})})(a.id)}else{n=el("span","pin")}n.setAttribute("data-kind",kind);PLATE_NODES[a.id]=n;layer.appendChild(n)}'+
'seen[a.id]=1;'+
'var cls=kind+(e&&e.state?" st-"+e.state:"")+((e&&e.external)?" ext":"")+(attn?" attn":"")+(sel?" sel":"");'+
'if(n.className!==cls)n.className=cls;'+
'if(text){var base=e?dispCrew(e):String(a.name||a.id);var arr=Array.from(base);var short=(attn?"❗ ":"")+arr.slice(0,8).join("")+(arr.length>8?"…":"");var nm=n.querySelector(".nm");if(nm.textContent!==short){nm.textContent=short;n.title=base}}'+
'else{var ch=monoChar(e||{disp:a.name});if(n.textContent!==ch)n.textContent=ch}'+
'var w=text?110:20,h=text?18:20;'+
'var half=w/2,maxL=W-half-2,left=maxL>half+2?Math.min(Math.max(at.left,half+2),maxL):at.left;'+
'var top=drop(left,at.top+4,w,h);'+
'var lp=Math.round(left)+"px",tp=Math.round(top)+"px";if(n.style.left!==lp)n.style.left=lp;if(n.style.top!==tp)n.style.top=tp});'+
'Object.keys(PLATE_NODES).forEach(function(id){if(!seen[id]){PLATE_NODES[id].remove();delete PLATE_NODES[id]}})}'+
// R78→R80.5: 帯は「名前＋今なにをしているか」の情報カード（豆粒チップでは何も分からない実FB）。
// タップでそのプロジェクトへカメラが寄る＝帯と3Dが同じ対象を指す。
// R79-6: チップ先頭にモノグラム（3Dの足元ピンと同じ文字＋状態リング＝対応が学習できる）。
// R80.5: 述語(activityGloss)を常設の下段に＝タップしなくても「誰が・何を」が読める。×N=セッション内訳
'function paintEmptyHint(n){var room=document.getElementById("room");if(!room)return;var el0=document.getElementById("emptyhint");if(n>0){if(el0)el0.remove();return}if(el0)return;var box=el("div",null);box.id="emptyhint";box.appendChild(el("div","eh-t",T("まだ誰も出勤していません","Nobody is on duty yet")));box.appendChild(el("div","eh-s",T("Macのターミナルで claude を起動すると、そのプロジェクトがここに出勤します。","Start claude in a terminal on your Mac and the project will show up here.")));room.appendChild(box)}'+
'function paintRoster(office){var bar=document.getElementById("roster");if(!bar)return;var emps=officeAgents(office).slice().sort(triageSort);var seen={};emps.forEach(function(e){var key=e.session||"";seen[key]=1;var n=null,all=bar.querySelectorAll(".rchip");for(var i=0;i<all.length;i++)if(all[i].getAttribute("data-sess")===key){n=all[i];break}if(!n){n=el("button","rchip");n.type="button";n.setAttribute("data-sess",key);n.appendChild(el("span","mono"));var tx=el("span","rtxt");tx.appendChild(el("span","nm",""));tx.appendChild(el("span","gl",""));n.appendChild(tx);n.addEventListener("click",function(){var cur=(officeAgents(LAST_OFFICE)||[]).filter(function(x){return x&&x.session===key})[0];if(!cur)return;openSheet(cur);if(window.__scene3d&&window.__scene3d.focus){var ags=window.__scene3d.agents()||[];for(var j=0;j<ags.length;j++)if(ags[j].session===key){window.__scene3d.focus(ags[j].id);break}}});bar.appendChild(n)}n.setAttribute("data-state",e.state||"");n.className="rchip"+(needsAttn(e)?" attn":"")+(SEL&&SEL.session===key?" sel":"");setMono(n.querySelector(".mono"),e);var rnm=dispCrew(e);var rn=n.querySelector(".nm");if(rn.textContent!==rnm)rn.textContent=rnm;var gl0=activityGlossPWA(e,LANG)||"";var gn=n.querySelector(".gl");if(gn&&gn.textContent!==gl0)gn.textContent=gl0;var tt=rnm+" \u2014 "+gl0;if(n.title!==tt)n.title=tt});var nodes=bar.querySelectorAll(".rchip");for(var q=nodes.length-1;q>=0;q--)if(!seen[nodes[q].getAttribute("data-sess")])nodes[q].remove()}'+
'window.__paintPlates=paintPlates;'+
// 3Dモジュールは非同期で載る。載った瞬間に**シーンだけ**描き直す。
// ここで dispatch()（全再描画）を呼ぶと、設定シート等の開いているDOMが差し替わり
// 直前のクリックが detach 空振りになる（R67でデスクトップが踏んだのと同じ罠）。
// R79-9: 4秒watchdogのリスト退避は「一時的」＝遅れて3Dが起動したら自動でofficeへ復帰する。
// 遅い回線/端末（実機3G・CIの高負荷でモジュール取得+シェーダコンパイルが4秒を超える）で
// 退避が恒久化し、動くはずの3Dに二度と戻らない穴を塞ぐ
'var SCENE3D_AUTORETREAT=false;'+
'document.addEventListener("scene3d-ready",function(){SCENE3D="ready";if(SCENE3D_AUTORETREAT){SCENE3D_AUTORETREAT=false;note(T("3D表示が使えるようになりました","3D view is ready"));setView("office")}else if(LAST_OFFICE&&VIEW==="office")renderScene(LAST_OFFICE)});'+
'function scene3dFailed(){if(SCENE3D!=="pending")return;SCENE3D="failed";note(T("3D表示を利用できないため、リスト表示に切り替えました","3D unavailable — switched to the list view"));if(VIEW==="office"){SCENE3D_AUTORETREAT=true;setView("list")}else if(LAST_OFFICE)dispatch()}'+
'document.addEventListener("scene3d-failed",scene3dFailed);'+
'function renderScene(office){var room=document.getElementById("room");'+
'if(room)room.querySelectorAll(".empty:not(#no3d)").forEach(function(n){n.remove()});'+
'if(!document.getElementById("scene3d"))sceneShell3D();'+
// ❗カードはシーンの状態(pending/ready/failed)と無関係＝分岐の前で必ず塗る。
// pending中に塗らないと「3Dの起動待ちの数秒間、主目的の❗が出ない」穴になる（実測で発見）
'updateAttnCards(officeAgents(office));'+
// R80-A1: 0人のとき、officeタブは**文言ゼロの無言画面**だった（誰もいない3Dだけ）。
// リストタブには案内があるのに、既定タブであるofficeには無いという穴。
// フルブリード化で置き場所が消えていたので、シーンの上に浮かせる。
'paintEmptyHint(officeAgents(office).length);'+
'if(window.__scene3d&&window.__scene3d.ready){var n3=document.getElementById("no3d");if(n3)n3.remove();'+
'window.__scene3d.apply(office);paintPlates();paintRoster(office);paintGauges();return}'+
'if(SCENE3D==="pending")return;'+
'if(!document.getElementById("no3d")){var nt=el("div","empty");nt.id="no3d";nt.textContent=T("この端末では3D表示を利用できません。操作はすべて☰リストからできます。","3D view is unavailable on this device. Everything works from the List tab.");if(room)room.appendChild(nt)}}'+
// R79-5: ヘッダー統合＝統計は右肩に3つだけ（❗/稼働/待機）。横スクロールしないと見えない数値は
// 「一目」ではない。内訳（📨/👥/💤/出勤）はリストタブとシートが担う。
// R51: rosterでは sessions[] が実セッションの内訳＝稼働数を正直に数える（旧employeesは[e]で同値）
'function buildHeader(emps){var w=0,wa=0,al=0,mn=0;emps.forEach(function(e){if(needsAttn(e))al++;mn+=(e.minions||0);var ms=(Array.isArray(e.sessions)&&e.sessions.length)?e.sessions:[e];ms.forEach(function(m){if(m.state==="working")w++;else if(m.state==="waiting")wa++})});' +
'var hs=document.getElementById("hstats");if(!hs)return;hs.innerHTML="";function mk(cls,lb,n,tip){var s=el("span","hstat"+(cls?" "+cls:""));s.appendChild(document.createTextNode(lb));s.appendChild(el("b",null,String(n)));s.title=tip;s.addEventListener("click",function(){note(tip)});hs.appendChild(s)}' +
// R80-A10: タッチ端末に title は出ないので、タップで内訳（作業中N + サブエージェントM）を言う。
// 「稼働12」が実は3+9だった、という一目の誤解を潰す。
'var tl=document.querySelector(".hdr2 .ttl");if(tl){var on0=(LAST_OFFICE&&LAST_OFFICE.officeName)||"AI Office";var sub0=emps.length?(emps.length+T("プロジェクト"," projects")):"";var sig0=on0+"|"+sub0;if(tl.getAttribute("data-sig")!==sig0){tl.setAttribute("data-sig",sig0);tl.innerHTML="";var nm0=el("span","tname","🏢 "+on0);tl.appendChild(nm0);if(sub0)tl.appendChild(el("span","tsub",sub0))}}if(al)mk("attn","❗",al,T("要対応（承認/質問まち）","Needs attention (approvals/questions)"));mk("",T("稼働","Active"),w+mn,T("作業中セッション"+w+" + サブエージェント"+mn,"Working sessions "+w+" + subagents "+mn));mk("",T("待機","Idle"),wa,T("指示待ちセッション","Sessions waiting for instructions"))}' +
'function dispatch(){var off=LAST_OFFICE;var emps=officeAgents(off);var sig=sceneSig(emps);if(sig===LAST_SIG&&VIEW===LAST_VIEW)return;var sy=window.scrollY;LAST_SIG=sig;LAST_VIEW=VIEW;buildHeader(emps);buildDeptbar(emps);var room=document.getElementById("room"),list=document.getElementById("list");var to=document.getElementById("tb_office"),tl=document.getElementById("tb_list");if(to)to.classList.toggle("on",VIEW==="office");if(tl)tl.classList.toggle("on",VIEW!=="office");if(VIEW==="office"){room.classList.remove("hidden");list.classList.add("hidden");renderScene(off)}else{list.classList.remove("hidden");room.classList.add("hidden");renderList(off)}window.scrollTo(0,sy)}' +
'function setView(v){if(VIEW!==v)playSE("cursor");VIEW=v;localStorage.setItem("aioffice.view",VIEW);dispatch()}' +
// R79-7: status適用の単一路。HTTPポーリング応答もWS pushフレームも必ずここを通る
'function applyStatus(d){if(!d)return;var office={};try{office=JSON.parse(d.json||"{}")}catch(_){}' +
'conn(true,WS_ON?T("⚡ ライブ接続","⚡ Live"):T("✓ 接続","✓ Connected"));LAST_OFFICE=office;applyOfficeLang(office&&office.lang);' +
// R51: 鮮度の正直表示（ts=DO書込時刻・agentSeenAgo=relay_agent最終/syncからの秒）→ staleバナー。
// applyOfficeLang より後に呼ぶ（言語切替時の applyLangChrome が #banner を既定文言へ戻すため）
'updateStale(typeof d.ts==="number"?d.ts:0,typeof d.agentSeenAgo==="number"?d.agentSeenAgo:null,d.agentOnline===true);' +
'saveOfficeCache(office);var ags=officeAgents(office);checkAttnEdge(ags);checkAnswered(ags);checkDelivery(ags);dispatch();refreshAges(ags);if(document.getElementById("runwrap").classList.contains("open"))paintRun()}' +
'function onUnauthorized(){stopPolling();wsStop();conn(false,T("未認証・再ペアリングを","Unauthorized — pair this device again"));document.body.classList.remove("off");document.getElementById("app").classList.add("hidden");document.getElementById("tabbar").classList.add("hidden");document.getElementById("setup").classList.remove("hidden")}' +
'function poll(){var cred=getCred();if(!cred)return;' +
'fetch("/status",{headers:{"Authorization":"Bearer "+cred.t}}).then(function(r){' +
'if(r.status===401){onUnauthorized();return null}return r.json()}).then(applyStatus).catch(function(){conn(false,T("オフライン","Offline"))})}' +
'function startPolling(){if(!POLL_IV&&getCred()&&!document.getElementById("app").classList.contains("hidden")){POLL_IV=setInterval(poll,20000)}}' +
'function stopPolling(){if(POLL_IV){clearInterval(POLL_IV);POLL_IV=null}}' +
// R79-7: WebSocket常時接続。認証はサブプロトコル bearer.<b64url>（ヘッダ不可のブラウザ制約）。
// keepalive "p"→"P" はDO側auto-response（課金ゼロ）・75秒毎の{"t":"status"}で鮮度を刷新。
// 切断中はHTTPポーリングが自動で肩代わり＝WSが死んでも製品は退化しない（退避経路の掟）。
// R80-C1: **バックオフが一度も進まない欠陥の修正**。旧実装は onopen で無条件に WS_TRY=0 に
// 戻していたため、「接続は成立するが直後に切れる」環境（企業プロキシ・キャリアのWS間引き・
// iOS Low Data Mode）で **1秒固定リトライが永続**し、スマホ1台で 86,400 req/日
// （＋DO rows 172,800＝無料枠の173%）を発生させ得た。Mac側 relay_agent は同じ罠に対して
// 「60秒生きた接続だけを成功とみなす」ガードを持っていたので、それをここへ移植する。
// さらに**1分あたりの接続試行に上限**を設ける（どんな異常環境でも枠を割らない最後の壁）。
'var WS=null,WS_ON=false,WS_TRY=0,WS_KA=null,WS_ST=null,WS_OPENED_AT=0,WS_STAMPS=[];' +
'var WS_MIN_ALIVE=60000,WS_MAX_PER_MIN=6;' +
'function b64uTok(s){return btoa(s).replace(/\\+/g,"-").replace(/\\//g,"_").replace(/=+$/,"")}' +
'function wsStop(){if(WS){try{WS.onclose=null;WS.onmessage=null;WS.close()}catch(_){}WS=null}WS_ON=false;if(WS_KA){clearInterval(WS_KA);WS_KA=null}if(WS_ST){clearInterval(WS_ST);WS_ST=null}}' +
// 直近60秒の接続試行が上限に達していたら、その分だけ待たせる（レート超過時は必ず遅延が伸びる）
'function wsBudgetDelay(){var now=Date.now();WS_STAMPS=WS_STAMPS.filter(function(t){return now-t<60000});' +
'if(WS_STAMPS.length<WS_MAX_PER_MIN)return 0;return 60000-(now-WS_STAMPS[0])+250}' +
'function wsRetry(){var delay=[1000,2000,5000,10000,30000][Math.min(WS_TRY,4)];WS_TRY++;' +
'delay=Math.max(delay,wsBudgetDelay());' +
'startPolling();setTimeout(function(){if(!WS&&!document.hidden&&getCred())wsConnect()},delay)}' +
'function wsConnect(){var cred=getCred();if(!cred||WS||document.hidden)return;' +
'var wait=wsBudgetDelay();if(wait>0){setTimeout(function(){if(!WS&&!document.hidden&&getCred())wsConnect()},wait);return}' +
'var u=(location.protocol==="https:"?"wss://":"ws://")+location.host+"/ws?role=app";' +
'WS_STAMPS.push(Date.now());' +
'try{WS=new WebSocket(u,["aioffice.v1","bearer."+b64uTok(cred.t)])}catch(_){WS=null;wsRetry();return}' +
'WS.onopen=function(){WS_ON=true;WS_OPENED_AT=Date.now();stopPolling();' +
'WS_KA=setInterval(function(){try{if(WS)WS.send("p")}catch(_){}},25000);' +
'WS_ST=setInterval(function(){try{if(WS)WS.send(\'{"t":"status"}\')}catch(_){}},75000)};' +
'WS.onmessage=function(ev){if(ev.data==="P")return;var d=null;try{d=JSON.parse(ev.data)}catch(_){return}if(d&&d.t==="status")applyStatus(d)};' +
// ★ここが修正の核: リトライ回数を戻すのは「十分に生きた接続」のときだけ。
//   短命な接続はバックオフを進める＝繋がっては切れる環境で指数的に間隔が伸びる。
'WS.onclose=function(){var lived=WS_OPENED_AT?Date.now()-WS_OPENED_AT:0;WS_OPENED_AT=0;' +
'if(lived>=WS_MIN_ALIVE)WS_TRY=0;wsStop();if(!document.hidden)wsRetry()}}' +
'window.__office_ws={get on(){return WS_ON},get tries(){return WS_TRY},' +
'get attempts(){return WS_STAMPS.length},budgetDelay:wsBudgetDelay};' +
'document.addEventListener("keydown",function(ev){if(ev.key!=="Escape")return;if(document.getElementById("logwrap").classList.contains("open"))closeLog();else if(document.getElementById("runwrap").classList.contains("open"))closeRun();else if(document.getElementById("lnwrap").classList.contains("open"))closeLaunch();else if(document.getElementById("reswrap").classList.contains("open"))closeRes();else if(document.getElementById("setwrap").classList.contains("open"))closeSettings();else closeSheet()});' +
// R51: 基本ポーリング 5秒→20秒（CF無料枠対策）。R79-7: 表示中はWSが主役＝復帰時は即poll1発
// →WS再接続（openでポーリング停止）。非表示はWSも切る（iOSはどのみち凍結する＝電池）。
'document.addEventListener("visibilitychange",function(){if(document.hidden){stopPolling();wsStop()}else if(getCred()&&!document.getElementById("app").classList.contains("hidden")){poll();startPolling();wsConnect()}});' +
'function pushSupported(){return "serviceWorker" in navigator&&"PushManager" in window&&"Notification" in window}' +
// R79-5: 🔔はヘッダーのアイコンボタン（タブ3つ化でラベル無し）＝意味はtitle/aria-labelで伝える
'function setNtog(on){NTOG_STATE=!!on;var ic=document.getElementById("ntogic"),b=document.getElementById("ntog");if(ic)ic.textContent=on?"🔔":"🔕";if(b){b.classList.toggle("on",!!on);var tip=on?T("通知ON — タップでOFF","Alerts ON — tap to turn off"):T("通知OFF — ❗承認/質問まちをプッシュ通知","Alerts OFF — push alerts for ❗approvals and questions");b.title=tip;b.setAttribute("aria-label",tip)}}' +
'function refreshNtog(){if(!pushSupported())return;navigator.serviceWorker.ready.then(function(r){return r.pushManager.getSubscription()}).then(function(s){setNtog(!!s)}).catch(function(_){})}' +
'function b64uToU8(s){s=s.replace(/-/g,"+").replace(/_/g,"/");while(s.length%4)s+="=";var b=atob(s),u=new Uint8Array(b.length);for(var i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u}' +
'function pushApi(p,body){var cred=getCred();var o={headers:{"Authorization":"Bearer "+cred.t}};if(body){o.method="POST";o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}return fetch(p,o).then(function(r){return r.json()})}' +
// P7 通知トグル: iOSは「ホーム画面に追加」した standalone PWA でのみ Push 可（Safariタブでは非対応）
'function togglePush(){var cred=getCred();if(!cred)return;' +
'if(!pushSupported()){note(T("⚠ この端末は通知非対応です（ホーム画面に追加したAI Officeから開いてください）","⚠ Notifications are not supported here (open AI Office added to your Home Screen)"));return}' +
'navigator.serviceWorker.ready.then(function(reg){return reg.pushManager.getSubscription().then(function(sub){' +
'if(sub){return pushApi("/push/unsubscribe",{endpoint:sub.endpoint}).then(function(){return sub.unsubscribe()}).then(function(){setNtog(false);note(T("通知をOFFにしました","Notifications turned off"))})}' +
'return Notification.requestPermission().then(function(p){if(p!=="granted"){note(T("⚠ 通知が許可されていません（iOS設定→通知→AI Office）","⚠ Notifications are not permitted (iOS Settings → Notifications → AI Office)"));return}' +
'return pushApi("/push/vapid").then(function(v){if(!v||!v.ok){note(T("⚠ サーバ側のVAPID鍵が未設定です","⚠ Server VAPID key is not configured"));return}' +
'return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64uToU8(v.key)}).then(function(ns){' +
'return pushApi("/push/subscribe",{subscription:ns.toJSON(),depts:PREF.deptFilter?[PREF.deptFilter]:[]}).then(function(d){' +
'if(d&&d.ok){setNtog(true);note(T("🔔 通知ON＝❗承認/質問まちで届きます","🔔 Alerts ON — you will be notified for ❗approvals/questions"))}else{note(T("⚠ 登録失敗: ","⚠ Subscribe failed: ")+((d&&d.error)||""));ns.unsubscribe()}})})})})})}).catch(function(e){note(T("⚠ 通知設定エラー: ","⚠ Notification setup error: ")+e)})}' +
'function boot(){applyPrefs();applyLangChrome();credFromHash();if(!getCred()){document.getElementById("setup").classList.remove("hidden");return}' +
'document.getElementById("app").classList.remove("hidden");document.getElementById("tabbar").classList.remove("hidden");var cached=loadOfficeCache();if(cached){LAST_OFFICE=cached;applyOfficeLang(cached&&cached.lang);ATTN_SESSIONS=attnSessionSet(officeAgents(cached));dispatch();refreshAges(officeAgents(cached))}else{var r0=document.getElementById("room");if(r0)r0.appendChild(el("div","empty",T("接続中…","Connecting…")))}poll();startPolling();wsConnect();' +
// R51: SWからのpush到着通知（aioffice-poll）で即時poll＝20秒間隔でも❗を待たせない
'if("serviceWorker" in navigator){navigator.serviceWorker.addEventListener("message",function(ev){if(ev&&ev.data&&ev.data.type==="aioffice-poll"&&!document.hidden)poll()});navigator.serviceWorker.register("/app/sw.js").then(function(){refreshNtog()}).catch(function(){})}}' +
'boot();' +
'</script></body></html>';

// R79: PWAシェルの版ID（起動時に1回だけ計算）。APP_HTML はこのファイル末尾で定義される
// ので、定義後のここで算出する（fetch ハンドラはモジュール評価の後に走るため参照できる）。
// モジュール束の UI_BUILD とは別物: アプリだけ直したときにも必ず版が変わる必要がある。
const APP_BUILD = _fnv1a(APP_HTML) + "-" + _fnv1a(SW_JS + MANIFEST);
