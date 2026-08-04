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
import { SPRITES, SPRITE_MIME } from "./sprites_data.js";   // PWAオフィス絵用の本物キャラ立ち絵（自動生成）
import { ASSETS, MODULES } from "./modules_data.js";   // R77: PWAの3Dシーン用ESM（自動生成・/ui/... の同じパスで返す）
import { b64u, jwkToRawPub, sendWebPush } from "./webpush.js";   // P7: Web Push（暗号は全部Worker側＝Mac側stdlib不変）

// PWA歩行絵の収録状況はバンドル時に一度だけ索引化する。テーマ派生(__入り)や
// walkdown/walkupはスクリプト側で除外されるため、ここでも安全なstemだけを扱う。
const WALK_META = Object.create(null);
for (const key of Object.keys(SPRITES)) {
  const m = /^([A-Za-z0-9_]+)_walk(2)?$/.exec(key);
  if (!m || !Object.prototype.hasOwnProperty.call(SPRITES, `${m[1]}_walk`)) continue;
  WALK_META[m[1]] = Object.prototype.hasOwnProperty.call(SPRITES, `${m[1]}_walk2`) ? 2 : 1;
}
const WALK_META_JSON = JSON.stringify(WALK_META);

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
  }

  enqueue(session, text, ts) {
    this.ctx.storage.sql.exec(
      "INSERT INTO inbox(session,text,ts) VALUES (?,?,?)", session, text, ts);
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
    const app = this._seenTs("appseen");
    const appSeenAgo = app == null ? null : Math.max(0, Math.floor((now - app) / 1000));
    return { acked, items, newly, subs, appSeenAgo };
  }

  // R51: PWA向け GET /status を単一DO呼び出しに（appseen=now 更新＋agentSeenAgo 添付）。
  statusForApp() {
    const now = Date.now();
    this._touchSeen("appseen", now);
    const s = this.getStatus();
    const agent = this._seenTs("agentseen");
    return { json: s.json, ts: s.ts,
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
    if (method === "GET" && (path === "/app" || path === "/app/")) {
      return new Response(APP_HTML.replace("__WALK_STEMS__", WALK_META_JSON), {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
    }
    if (method === "GET" && path === "/app/sw.js") {
      return new Response(SW_JS, {
        headers: { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store" } });
    }
    if (method === "GET" && path === "/app/manifest.webmanifest") {
      return new Response(MANIFEST, {
        headers: { "Content-Type": "application/manifest+json; charset=utf-8", "Cache-Control": "no-store" } });
    }
    // PWAオフィス絵のキャラ立ち絵（非秘密・無認証）。名前は SPRITES マップの索引のみに使う＝
    // パストラバーサル不能。exact一致は長期immutable、未同梱テーマ/P1カスタム絵は短期キャッシュでフォールバック。
    // R77: PWAの3Dシーン用ESM（非秘密・無認証）。MODULES マップの索引のみ＝パストラバーサル不能。
    // デスクトップと同じ import 指定子("/ui/...")をそのまま解決させるため、**同じパス**で返す。
    if (method === "GET" && path.startsWith("/ui/")) {
      const IMMUTABLE = "public, max-age=31536000, immutable";
      const src = (Object.prototype.hasOwnProperty.call(MODULES, path) && MODULES[path]) || "";
      if (src) {
        return new Response(src, {
          headers: { "Content-Type": "text/javascript; charset=utf-8", "Cache-Control": IMMUTABLE },
        });
      }
      // 3Dシーンが URL で読むテクスチャ（importでは辿れないので別マップ）
      const asset = (Object.prototype.hasOwnProperty.call(ASSETS, path) && ASSETS[path]) || null;
      if (asset) {
        return new Response(spriteBytes(asset[1]), {
          headers: { "Content-Type": asset[0], "Cache-Control": IMMUTABLE },
        });
      }
      return new Response("not found", { status: 404 });
    }
    if (method === "GET" && path.startsWith("/app/sprite/")) {
      let name = "";
      try { name = decodeURIComponent(path.slice("/app/sprite/".length)).replace(/\.png$/i, ""); }
      catch (_) { name = ""; }   // 不正な%エンコード（%, %ZZ）で500にせずフォールバックへ
      const own = (k) => (Object.prototype.hasOwnProperty.call(SPRITES, k) && SPRITES[k]) || "";
      let b64 = own(name);   // __proto__等の継承値を掴まない
      const exact = !!b64;
      if (!b64) {
        // 旧キャッシュ端末が要求する退役済みスタイル名は、既定アセットへフォールバックする。
        const stripped = name.replace(/__[a-z][a-z0-9]*(_walk(?:up|down)?)?$/, "$1");
        if (stripped !== name) b64 = own(stripped);
      }
      if (!b64) b64 = own("generic_m") || own("generic_f");
      const cacheCtl = exact ? "public, max-age=31536000, immutable" : "public, max-age=300";
      if (!b64) return new Response("no sprites", { status: 404 });
      return new Response(spriteBytes(b64), {
        headers: { "Content-Type": SPRITE_MIME, "Cache-Control": cacheCtl } });
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
    const auth = request.headers.get("Authorization") || "";
    const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
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
    // /instruct・/sync・mac本体status には引き続き触れない（漏れてもmac面は無傷）
    if (isMini && !isFull &&
        !(site !== "mac" &&
          ((method === "POST" && path === "/status") ||
           (method === "GET" && path === "/pull") ||
           (method === "POST" && path === "/ack")))) {
      return jsonResp({ ok: false, error: "forbidden" }, 403);
    }

    const room = env.ROOM.getByName(site);

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
      return jsonResp({ ok: true, items: r.items, acked: r.acked, openclaw, appSeenAgo: r.appSeenAgo });
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
  background_color: "#241f18",
  theme_color: "#241f18",
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

// PWAの部屋割当だけを純関数に固定。部屋に入れるのはリーダー自身（minions数は同伴部下の人数）。
// PWA_ASSIGN_ROOMS_BEGIN
function assignRooms(employees) {
  // external(OpenClaw等)の社員は机割当の対象外（専用区画で描く・R42.1）
  const all = (Array.isArray(employees) ? employees : [])
    .filter((e) => e && typeof e === "object" && !e.external);
  const active = all.filter((e) => e.state !== "resting");
  const leaders = active.filter((e) => Number(e.minions) > 0);
  const meeting = leaders.filter((e) => Number(e.minions) >= 3)
    .slice().sort((a, b) => Number(b.minions) - Number(a.minions)).slice(0, 1);
  const oneOnOne = leaders.filter((e) => Number(e.minions) >= 1 && Number(e.minions) <= 2)
    .slice(0, 1);
  const inRoom = new Set(meeting.concat(oneOnOne));
  return {
    meeting,
    oneOnOne,
    desks: active.filter((e) => !inRoom.has(e)),
    rest: all.filter((e) => e.state === "resting"),
  };
}
// PWA_ASSIGN_ROOMS_END
const PWA_ASSIGN_ROOMS_SOURCE = assignRooms.toString();

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
'<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">' +
'<meta name=apple-mobile-web-app-capable content=yes>' +
'<meta name=apple-mobile-web-app-status-bar-style content=default>' +
'<meta name=apple-mobile-web-app-title content="AI Office">' +
'<meta name=theme-color content="#fffdf8">' +
'<link rel=manifest href="/app/manifest.webmanifest">' +
'<link rel=apple-touch-icon href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 100 100\'%3E%3Crect width=\'100\' height=\'100\' fill=\'%23241f18\'/%3E%3Ctext y=\'74\' x=\'50\' font-size=\'64\' text-anchor=\'middle\'%3E%F0%9F%8F%A2%3C/text%3E%3C/svg%3E">' +
'<title>AI Office</title>' +
'<style>' +
'*{box-sizing:border-box;margin:0;padding:0}' +
':root{--wood:#e5c49a;--ink:#241f18;--paper:#fffdf8;--line:#e2d9c6;--sage:#5f9b78;--sage-d:#3f6d4f;--sage-l:#7fd8a4;--amber:#b9791a;--alert:#c05a5a;--danger:#8e4438;--muted:#6b6252;--sh:rgba(58,53,44,.16)}' +
'@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}' +
'body{background:#f6f2e9;color:#241f18;font-family:-apple-system,"Hiragino Sans",system-ui,sans-serif;font-size:16px;padding:0 0 calc(78px + env(safe-area-inset-bottom));-webkit-tap-highlight-color:transparent}' +
'main{padding:0;max-width:640px;margin:0 auto}' +
'.card{position:relative;background:#fffdf8;border:1px solid #e2d9c6;border-radius:12px;padding:12px 14px;margin-bottom:10px;box-shadow:0 1px 2px rgba(40,32,18,.06)}' +
'.card:active{background:#f6e7cd}' +
'.card.alert{border-color:#c05a5a;background:#fbf1dd}' +
'.card.pend{border-color:#b9791a;background:#fbf6ea}' +
'.card .nm{font-weight:800;font-size:15px}' +
'.card .st{font-size:13px;color:#5c5346;margin-top:3px;display:flex;align-items:center;gap:6px}' +
'.dot{width:9px;height:9px;border-radius:50%;flex:none;background:#8a7f6d}' +
'.dot.working{background:#5f9b78}.dot.waiting{background:#c99a3e}.dot.resting{background:#b0a693}' +
'.card .q{font-size:12.5px;color:#8a5a10;margin-top:5px}' +
'.card .meta{display:flex;align-items:center;gap:9px}' +
'.card .meta img.cav{height:36px;width:auto;image-rendering:pixelated;flex:none}' +
'.card .meta .nm{flex:1;min-width:0}' +
'.card .age{font-size:11px;color:#6b6252;font-weight:700;flex:none;white-space:nowrap}' +
'.card .age.fresh{color:#2f6f68}' +
/* R51: 配達往復チップ（📨 queued → ✓ delivered）と roster セッション内訳行 */
'.dchip{flex:none;font-size:10px;font-weight:800;color:#8a5a10;background:#fbf6ea;border:1px solid #e2d0a8;border-radius:999px;padding:2px 7px;white-space:nowrap}' +
'.dchip.ok{color:#2f6f68;background:#eef5ef;border-color:#cfe3d4}' +
'body.th-dark .dchip{background:#2e2718;border-color:#6b5a40;color:#e8bd69}' +
'body.th-dark .dchip.ok{color:#9ee6bb;background:#22301f;border-color:#3f6d4f}' +
'.sessrows{display:flex;flex-direction:column;gap:4px;margin:2px 0 6px}' +
'.sessrow{display:flex;align-items:center;gap:7px;font-size:12px;color:#5c5346;font-weight:700}' +
'.sessrow .sessid{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#6b6252}' +
'body.th-dark .sessrow{color:#cfc3a6}body.th-dark .sessrow .sessid{color:#a99a7d}' +
'.feed{margin-top:7px;border-top:1px dashed #ece3d0;padding-top:6px;display:flex;flex-direction:column;gap:2px}' +
'.feedline{font-size:12px;color:#6b6252;line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
'.sheet .sec{font-size:11px;font-weight:800;color:#8a7f6d;letter-spacing:.04em;margin:13px 0 5px}' +
'.sheet .wk-work{border-top:1px dashed #ece3d0;padding:8px 0 3px}' +
'.sheet .wk-title{font-size:13px;font-weight:800;color:#8a5a10;margin-bottom:4px}' +
'.sheet .wk-row{display:flex;gap:7px;font-size:13px;line-height:1.65}' +
'.sheet .wk-label{flex:none;width:45px;color:#8a7f6d;font-weight:700}' +
'.sheet .wk-items{min-width:0;display:flex;flex-direction:column}' +
'.sheet .wk-item{word-break:break-word;color:#4a4236}' +
'.sheet .wk-now .wk-item{font-weight:800;color:#241f18}' +
'body.th-dark .sheet .wk-work{border-color:#3d352a}body.th-dark .sheet .wk-title{color:#e8bd69}' +
'body.th-dark .sheet .wk-label{color:#a3946f}body.th-dark .sheet .wk-item{color:#d9cdb2}' +
'body.th-dark .sheet .wk-now .wk-item{color:#f5e9d0}' +
/* 最近の動き=シートの主役（「何をしているか分からない」FB対応: 大きく・多く・スクロール可） */
'.sheet .feedbox{background:#faf5ea;border:1px solid #ece3d0;border-radius:9px;padding:11px 13px;display:flex;flex-direction:column;gap:7px;max-height:46vh;overflow-y:auto}' +
'.sheet .feedbox .feedline{white-space:normal;font-size:13.5px;color:#4a4236;line-height:1.7}' +
'.sheet .feedbox .feedline:first-child{color:#241f18;font-weight:800}' +
'.sheet .said{background:#faf5ea;border:1px solid #ece3d0;border-radius:9px;padding:9px 11px;font-size:12.5px;color:#4a4236;line-height:1.65;white-space:pre-wrap;max-height:34vh;overflow:auto}' +
'.sheet .saidq{background:#fdf3e2;border:1.5px solid #d9a044;font-size:14px;font-weight:700;color:#241f18}' +
'body.th-dark .sheet .saidq{background:#3a2c15;border-color:#b9791a;color:#f5e9d0}' +
'.empty{color:#8a7f6d;text-align:center;padding:40px 0}' +
'.setup{max-width:520px;margin:0 auto;padding:16px}' +
'.setup h2{font-size:17px;margin-bottom:8px}.setup p{color:#5c5346;font-size:14px;line-height:1.7;margin-bottom:10px}' +
'textarea,input{width:100%;border:1px solid #e2d9c6;border-radius:9px;padding:10px;font-size:15px;font-family:inherit;background:#fffdf8}' +
/* 色は状態を意味する（PC掟と同一）: 送信/承認=セージ・危険(停止)=赤・中立=ニュートラル */
'button{width:100%;border:0;border-radius:10px;padding:13px;font-size:15px;font-weight:800;color:#fff;background:var(--sage);margin-top:8px;font-family:inherit;transition:transform .1s,filter .1s;min-height:44px}' +
'button.g{background:var(--sage)}button.r{background:var(--danger)}button.sub{background:#efe7d6;color:var(--muted)}' +
'button:active{transform:translateY(1px);filter:brightness(.95)}button:disabled{opacity:.5;pointer-events:none}' +
'.hidden{display:none}' +
'#sheetwrap{position:fixed;inset:0;z-index:90;pointer-events:none}' +
'#sheetwrap.open{pointer-events:auto}' +
'.sheet{position:absolute;left:0;right:0;bottom:0;background:#fffdf8;border-top:2px solid #241f18;border-radius:16px 16px 0 0;padding:14px 16px calc(16px + env(safe-area-inset-bottom));box-shadow:0 -8px 30px rgba(40,32,18,.22);max-width:640px;margin:0 auto;max-height:88vh;overflow-y:auto;transform:translateY(110%);transition:transform .32s cubic-bezier(.32,.72,0,1);will-change:transform}' +
'.sheet::before{content:"";display:block;width:38px;height:4px;border-radius:2px;background:#d8cdb8;margin:0 auto 12px}' +
'#sheetwrap.open .sheet{transform:translateY(0)}' +
'.sheet h3{font-size:16px;margin-bottom:2px}.sheet .who{color:#8a5a10;font-size:13px}' +
'.shhead{display:flex;align-items:center;gap:11px;margin-bottom:10px}' +
'.shhead img{height:48px;width:auto;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.12))}' +
'#shsay{background:#f6f1e4;color:#3c352a;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:14px;line-height:1.75;margin:2px 0 10px;min-height:44px;white-space:pre-wrap}' +
'body.th-dark #shsay{background:#2b2419;border-color:#3d352a;color:#d9cdb2}' +
'.mask{position:absolute;inset:0;background:rgba(20,16,10,.45);opacity:0;transition:opacity .28s ease}' +
'#sheetwrap.open .mask{opacity:1}' +
/* 全文ログビューア（「最近の動き」タップで拡大・シートより上層） */
'#logwrap{position:fixed;inset:0;z-index:120;pointer-events:none}' +
'#logwrap.open{pointer-events:auto}' +
'#logwrap .mask{position:absolute;inset:0;background:rgba(20,16,10,.5);opacity:0;transition:opacity .28s ease}' +
'#logwrap.open .mask{opacity:1}' +
'#logwrap.open .sheet{transform:translateY(0)}' +
/* ⚙️設定シート（フッター4ボタン化） */
'#setwrap{position:fixed;inset:0;z-index:115;pointer-events:none}' +
'#setwrap.open{pointer-events:auto}' +
'#setwrap .mask{position:absolute;inset:0;background:rgba(20,16,10,.5);opacity:0;transition:opacity .28s ease}' +
'#setwrap.open .mask{opacity:1}' +
'#setwrap.open .sheet{transform:translateY(0)}' +
'.setrow{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:13px 2px;border-bottom:1px dashed #e4dbc8}' +
'.setrow .lb{font-weight:800;font-size:14px;flex:none}' +
'.setrow .hint{font-size:11.5px;color:#8a7f6d;margin-top:3px;font-weight:600}' +
'.seg{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}' +
'.seg button{width:auto;margin:0;background:#efe7d6;color:#6b5a40;border-radius:9px;padding:9px 14px;font-size:13px;min-height:42px}' +
'.seg button.on{background:var(--sage);color:#fff}' +
'.setver{text-align:center;color:#a99a7d;font-size:11px;margin-top:12px;font-weight:700}' +
/* 🌙ダークテーマ（設定で切替・シーン絵はそのまま=イラスト部は共通） */
'body.th-dark{background:#17130e;color:#ece2cc}' +
'body.th-dark .card{background:#26211a;border-color:#3d352a;box-shadow:none}' +
'body.th-dark .card:active{background:#31291f}' +
'body.th-dark .card .nm{color:#f2e8d4}body.th-dark .card .st{color:#b3a790}body.th-dark .card .q{color:#e8b86a}body.th-dark .card .age{color:#a99a7d}' +
'body.th-dark .card.alert{background:#33231d}body.th-dark .card.pend{background:#2e2718}' +
'body.th-dark .feedline{color:#a99a7d}' +
'body.th-dark .feed{border-color:#3d352a}' +
'body.th-dark .sheet{background:#221d15;border-top-color:#f5c96b;color:#ece2cc}' +
'body.th-dark .sheet h3{color:#f5e9d0}body.th-dark .sheet .sec{color:#a3946f}' +
'body.th-dark .sheet .feedbox,body.th-dark .sheet .said{background:#2b2419;border-color:#3d352a;color:#d9cdb2}' +
'body.th-dark .sheet .feedbox .feedline{color:#cfc3a6}body.th-dark .sheet .feedbox .feedline:first-child{color:#f2e8d4}' +
'body.th-dark .lgline{color:#d9cdb2;border-color:#3d352a}body.th-dark #lgbody .lgline:first-of-type{color:#f5e9d0}' +
'body.th-dark textarea,body.th-dark input{background:#2b2419;border-color:#3d352a;color:#ece2cc}' +
'body.th-dark button.sub{background:#3a3226;color:#cfc3a6}' +
'body.th-dark .empty{color:#a99a7d}body.th-dark .setup h2{color:#f5e9d0}body.th-dark .setup p{color:#b3a790}' +
'body.th-dark .setrow{border-color:#3d352a}body.th-dark .seg button{background:#3a3226;color:#cfc3a6}body.th-dark .seg button.on{background:var(--sage);color:#fff}' +
/* v5: クロームのth-dark（classicライト化に伴い、ダークテーマは従来の暗色クロームを維持） */
'body.th-dark .hdr2{background:#241f18;color:#f5c96b;border-bottom-color:#3d352a}' +
'body.th-dark .hdr2 .total{background:#3a2f22;border-color:rgba(245,201,107,.4)}' +
'body.th-dark .hdr2 .total b{color:#7fd8a4}body.th-dark .hdr2 .total small{color:#e9dcc0}' +
'body.th-dark .statbar{background:#2e2718;border-bottom-color:#3d352a}' +
'body.th-dark .stat{background:#3a3226;border-color:#3d352a;color:#cfc3a6}' +
'body.th-dark .stat b{color:#f5c96b}body.th-dark .stat.attn{background:#33231d;border-color:#5a3028}body.th-dark .stat.attn b{color:#ff9d8a}' +
'body.th-dark .deptbar{background:#241f18;border-bottom-color:#3d352a}' +
'body.th-dark .deptchip{background:#3a3226;border-color:#6b5a40;color:#d8ccb5}' +
'body.th-dark .deptchip.on{background:var(--sage);border-color:var(--sage-l);color:#fff}' +
'body.th-dark .tabbar{background:#241f18;border-top-color:#3d352a;box-shadow:0 -4px 14px rgba(30,20,8,.25)}' +
'body.th-dark .tabbar button{background:#3a2f22;color:#e9dcc0}' +
'body.th-dark .tabbar button.on{background:var(--sage);color:#fff}' +
'body.th-dark .setrow .hint{color:#a3946f}' +
/* 🔎文字大きめ（アクセシビリティ） */
'body.th-big{font-size:17.5px}' +
'body.th-big .card .nm{font-size:17px}body.th-big .card .st{font-size:15px}body.th-big .feedline{font-size:14px}' +
'body.th-big .sheet .feedbox .feedline{font-size:15.5px}body.th-big .lgline{font-size:16.5px}' +
'body.th-big #shsay{font-size:16px}' +
'body.th-big .ws .plate{font-size:12px}body.th-big .ws .act{font-size:11.5px}' +
'.logsheet{max-height:94vh}' +
'#lgbody{margin:2px 0 10px}' +
'.lgsec{font-size:11px;font-weight:800;color:#8a7f6d;letter-spacing:.04em;margin:13px 0 3px}' +
'.lgline{font-size:14px;line-height:1.85;color:#3c352a;padding:10px 2px;border-bottom:1px dashed #e4dbc8;white-space:pre-wrap;word-break:break-word}' +
'.lgline:last-child{border-bottom:0}' +
'#lgbody .lgline:first-of-type{font-weight:800;color:#241f18}' +
'#note{position:fixed;left:50%;bottom:calc(86px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(8px);z-index:130;opacity:0;transition:opacity .25s,transform .25s;font-size:13px;font-weight:800;color:#fff;background:var(--sage);padding:10px 18px;border-radius:999px;max-width:90vw;text-align:center;box-shadow:0 4px 14px rgba(40,32,18,.28);pointer-events:none}' +
'#note.show{opacity:1;transform:translateX(-50%) translateY(0)}' +
'#note.err{background:var(--danger)}' +
/* v5: ❗即答カード=コンパクト化（1枚目のみ展開・選択肢は横スクロール・2枚目以降は1行折り畳み） */
'#attncards{display:none;width:100%;padding:8px 10px 0;background:transparent}' +
'.attncard{margin:0 0 8px;padding:9px 11px 10px}' +
'.attncard .attnhead{display:flex;align-items:center;gap:8px;min-width:0}' +
'.attncard .attnface{height:30px;width:auto;max-width:36px;image-rendering:pixelated;flex:none;filter:drop-shadow(0 2px 0 rgba(0,0,0,.14))}' +
'.attncard .attnname{font-size:13px;font-weight:800;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
'.attncard .attnq{margin-top:5px;color:#8e4438;font-size:13px;font-weight:700;line-height:1.4;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:1;overflow:hidden;word-break:break-word}' +
'.attncard.attnmini{display:flex;align-items:center;gap:8px;padding:8px 11px;min-height:44px;cursor:pointer}' +
'.attncard.attnmini .attnname{flex:none;max-width:38vw}' +
'.attncard.attnmini .attnq{flex:1;min-width:0;margin:0;white-space:nowrap;display:block;text-overflow:ellipsis}' +
'.attncard.attnmini .attngo{flex:none;color:var(--muted);font-size:12px;font-weight:800}' +
'.attnoptions{display:flex;flex-direction:row;gap:6px;margin-top:7px;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;padding-bottom:2px}' +
'.attnoptions::-webkit-scrollbar{display:none}' +
'.attnoptions .qopt{flex:none;width:auto;min-width:120px;max-width:200px}' +
'.quickoptions{display:flex;flex-direction:column;gap:6px;margin-top:8px}' +
'.qopt{display:flex;flex-direction:column;align-items:flex-start;gap:1px;width:100%;min-height:44px;margin:0;padding:8px 9px;background:#f6eddc;color:#6b3d18;border:1.5px solid #c98b44;border-radius:9px;text-align:left;font-size:12px;line-height:1.25}' +
'.qopt:hover{background:#f0dfbf}' +
'.qopt .qopt-label{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:800}' +
'.qopt .qopt-desc{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8a7f6d;font-size:10.5px;font-weight:600}' +
'.quickoptions{margin:0 0 8px}' +
'.quickoptions .qopt{background:#fff5e6}' +
'.attncard .attnactions{display:flex;flex-wrap:nowrap;gap:6px;margin-top:7px}' +
'.attncard .attnactions button{flex:1 1 0;min-width:0;width:auto;min-height:44px;margin:0;padding:7px 4px;font-size:11px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
'.attncard .attnactions button.sub{color:var(--muted)}' +
'.attncard .attnsent{margin-top:7px;color:#2f6f68;font-size:12px;font-weight:800}' +
'#attncards.on{display:block}' +
'#attncards .attnmore{width:auto;min-height:44px;margin:0 0 8px;padding:8px 14px;background:#fdf4ef;color:var(--danger);border:1px solid var(--alert);border-radius:999px;font-size:12px;font-weight:800}' +
'body.th-dark #attncards{background:#17130e}body.th-dark .attncard .attnq{color:#ffb09c}body.th-dark .attncard .attnsent{color:#9ee6bb}' +
'#banner{display:none;background:var(--danger);color:#fff;font-size:12px;font-weight:800;text-align:center;padding:6px 10px}' +
'body.off #banner{display:block}' +
'body.off #room,body.off #list{filter:grayscale(.55);opacity:.62}' +
'#list{padding:10px 12px}' +
/* ===== ドット絵オフィスシーン（縦積みバルペン・案1＋接ぎ木） ===== */
'html{overflow-x:hidden}' +
'.topbar{position:sticky;top:0;z-index:20}' +
/* v5: クロームはPC版と同じ明るいデザイン言語（クリーム地+hairline+セージ=主操作）。ダークはth-darkで維持 */
'.hdr2{background:#fffdf8;color:#241f18;display:flex;align-items:center;gap:8px;padding:calc(9px + env(safe-area-inset-top)) 14px 8px;font-weight:800;border-bottom:1px solid var(--line)}' +
'.hdr2 .live{width:9px;height:9px;border-radius:50%;background:var(--sage);flex:none}' +
'.hdr2 .live.off{background:#b0a693}' +
/* 右肩=「いま動いているAIの合計」（セッション+部下エージェント・2026-07-13 FB） */
'.hdr2 .total{margin-left:auto;flex:none;display:flex;align-items:baseline;gap:4px;background:#eef5ef;border:1px solid #cfe3d4;border-radius:999px;padding:4px 12px}' +
'.hdr2 .total b{font-size:17px;color:var(--sage-d);line-height:1}' +
'.hdr2 .total small{font-size:10px;color:var(--muted);font-weight:800}' +
'.hdr2 .ttl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;font-size:15px}' +
/* 統計バー=各アイコンの意味をラベルで明示（🟢🟡❗が何か分かるように）・1行コンパクト */
'.statbar{display:flex;gap:6px;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch;padding:6px 10px;background:#fffdf8;border-bottom:1px solid var(--line);height:40px;min-height:40px;scrollbar-width:none}' +
'.statbar::-webkit-scrollbar{display:none}' +
'.stat{flex:none;display:inline-flex;align-items:center;gap:5px;background:#f6f2e9;border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:10.5px;font-weight:800;color:var(--muted)}' +
'.stat b{font-size:13.5px;color:#241f18}' +
'.stat.attn{border-color:#e6c3ba;background:#fbf1dd}' +
'.stat.attn b{color:var(--alert)}' +
'.deptbar{display:flex;gap:7px;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch;padding:6px 10px;background:#fffdf8;border-bottom:1px solid var(--line);height:47px;min-height:47px;scrollbar-width:none}' +
'.deptbar::-webkit-scrollbar{display:none}' +
'.deptbar:empty{display:none}' +
'.deptchip{width:auto;flex:none;margin:0;min-height:34px;padding:5px 13px;border:1px solid var(--line);border-radius:999px;background:#f6f2e9;color:var(--muted);font-size:12px;font-weight:800}' +
'.deptchip.on{background:var(--sage);border-color:var(--sage);color:#fff}' +
/* ===== フッターメニュー（2026-07-12 スマホ専用再構成: 操作は全部ここ・親指圏・潰れない） ===== */
'.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:60;display:flex;gap:8px;background:#fffdf8;border-top:1px solid var(--line);padding:7px 10px calc(7px + env(safe-area-inset-bottom));box-shadow:0 -2px 12px rgba(58,53,44,.10)}' +
'.tabbar button{flex:1 1 0;min-width:0;margin:0;background:#f2ede1;color:var(--muted);border-radius:11px;padding:6px 2px;min-height:54px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-size:11px;font-weight:800;line-height:1.2}' +
'.tabbar button .ic{font-size:20px;line-height:1}' +
'.tabbar button.on{background:var(--sage);color:#fff}' +
'.tabbar button:active{transform:translateY(1px);filter:brightness(.95)}' +
/* ===== スマホ=タイル床＋モダン家具のオフィスシーン 2026-07-24 ===== */
/* v5: 旧v3シーンの#room定義はM1定義(後方)へ一本化済み */
/* 歩行キャラ=フッターメニュー直上の固定通路（スクロール位置に関係なく常に「忙しく動いてる」・2026-07-13 FB） */
/* 高さ=char50+bob3+bottom3+plate17+余白5=78px（64pxでは頭が見切れる=2026-07-13 FB） */
/* v5: walkbar(フッター歩行帯)は廃止＝v4.0ミニマップの歩行遷移と情報重複し、名札プレートが重なって崩壊していた */
'#room .empty{margin:auto;text-align:center;color:#6b5a40;font-size:14px;font-weight:700;padding:48px 20px}' +
'.ws{position:relative;z-index:2;width:100%;margin:0;padding:4px 2px 0;background:none;color:inherit;border:0;display:flex;flex-direction:column;align-items:center;min-width:0}' +
'.ws:active{filter:brightness(1.07)}' +
'.ws .bubble{position:relative;z-index:5;margin-bottom:1px;max-width:96%;background:#fffaf0;border:1.5px solid #d8c7a2;border-radius:9px;padding:2px 8px;font-size:10.5px;line-height:1.35;color:#5c5346;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 2px rgba(58,53,44,.12)}' +
'.ws[data-alert] .bubble{background:#4a1f16;border-color:#c05a5a;color:#ffd9a8}' +
/* キャラは大きく・机は小さく＝上半身がしっかり見える比率（机の前景が腰下を隠す=PC版と同じ着席表現） */
'.ws .stage{position:relative;width:100%;max-width:110px;height:118px;margin:0 auto}' +
'.ws .stage::after{content:"";position:absolute;left:50%;bottom:-2px;transform:translateX(-50%);width:86%;height:12px;background:radial-gradient(ellipse at center,rgba(70,44,16,.22) 0 55%,transparent 72%);z-index:0}' +
'.ws .stage .char{position:absolute;left:50%;bottom:30px;transform:translateX(-50%);height:76px;width:auto;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.12));z-index:1}' +
'.gesture{pointer-events:none;line-height:1}' +
'.ws .gesture{position:absolute;top:24px;left:calc(50% + 25px);font-size:14px;z-index:6}' +
'.card .gesture{position:absolute;top:10px;right:12px;font-size:15px}' +
'.standing{margin:5px 12px 0;padding:8px 10px 10px;background:rgba(255,248,232,.28);border:2px dashed rgba(120,84,40,.22);border-radius:14px}' +
'.standing>.zonelabel{margin:0 0 6px 0}' +
'.standingrow{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}' +
'.standingperson{position:relative;width:auto;min-width:64px;margin:0;padding:3px 6px 4px;background:rgba(255,253,248,.74);color:#2e2a22;border:1px solid rgba(120,84,40,.22);border-radius:9px;display:flex;flex-direction:column;align-items:center;gap:2px}' +
'.standingperson .standchar{height:52px;width:auto;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.12))}' +
'.standingperson .standname{max-width:90px;font-size:10.5px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
'.standingperson .gesture{position:absolute;top:3px;right:3px;font-size:14px}' +
'.zonemsg{flex:1 0 100%;font-size:12px;color:#6b5a40;padding:14px 12px;text-align:center;width:100%}' +
/* タイル床の上でも読める空室メッセージ */
/* 会議/ラウンジの名札チップ（誰がいるか一目で・タップ=詳細シート） */
'.mplates{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin-top:9px}' +
'.mplates .mp{width:auto;min-height:30px;margin:0;display:inline-flex;align-items:center;gap:5px;background:#2e2a22;color:#f2ead8;font-size:11px;font-weight:800;border-radius:6px;padding:3px 10px;box-shadow:0 1px 2px rgba(20,14,6,.3)}' +
'.ws .stage .char.cssav{display:flex;align-items:center;justify-content:center;font-size:28px;background:#e6cfa0;border-radius:6px;height:60px;width:52px}' +
'.ws .stage .dk{position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:70%;max-width:78px;height:auto;image-rendering:pixelated;z-index:2;filter:drop-shadow(0 3px 3px rgba(58,53,44,.20))}' +
'.ws .stage .screenglow{position:absolute;left:calc(50% - 4px);bottom:50px;width:18px;height:9px;border-radius:2px;z-index:3;opacity:0}' +
'.ws[data-state=working] .screenglow{background:#7fd8a4;box-shadow:0 0 7px #7fd8a4;opacity:.8;animation:glowpulse 1.4s ease infinite}' +
'.ws[data-state=working] .char{animation:bob 1.5s steps(2) infinite}' +
'.ws[data-state=resting] .char{filter:grayscale(.4) opacity(.85)}' +
'.ws .age{font-size:10.5px;color:#4a4234;font-weight:700;margin-top:1px;text-shadow:0 1px 1px rgba(255,255,255,.6)}' +
'.ws .badge{position:absolute;top:24px;left:calc(50% - 48px);font-size:15px;z-index:6;line-height:1}' +
'.ws[data-alert] .badge{animation:blink 1s steps(2) infinite}' +
'.ws[data-alert] .stage{outline:2px solid #c05a5a;outline-offset:2px;border-radius:8px}' +
'.ws[data-pend] .stage{outline:2px dashed #b9791a;outline-offset:2px;border-radius:8px}' +
'.ws .plate{z-index:4;margin-top:2px;max-width:100%;display:flex;align-items:center;gap:5px;background:#2e2a22;color:#f2ead8;font-size:10.5px;font-weight:800;border-radius:6px;padding:1px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 2px rgba(58,53,44,.2)}' +
'.ws .plate .dot{flex:none}' +
/* 各机の下に「いま何してるか」1行（officegridでは吹き出し非表示のため=「何してるか分からない」FB対応） */
'.ws .act{max-width:100%;font-size:10px;font-weight:700;color:#6b5a40;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px;text-align:center}' +
/* ゾーンラベル=部屋パネルに密着する看板（meet/loungeのみ・officeは床置きのまま） */
'.zl-attach{margin:10px 0 -13px 18px;position:relative;z-index:3;box-shadow:0 2px 6px rgba(30,20,10,.42)}' +
/* 休憩ラウンジ=明るいタイル床＋中央ソファ・右キッチネット・端の植物 */
'.lounge{margin:2px 12px 4px;padding:10px 8px 10px;display:flex;flex-direction:column;align-items:center;background-color:#e4e5e8;background-image:url(/app/sprite/tile2_floor_white);background-repeat:repeat;background-size:24px 24px;image-rendering:pixelated;border:1px solid rgba(110,106,98,.24);border-radius:14px;box-shadow:inset 0 2px 8px rgba(90,80,60,.14)}' +
/* ソファはキャラ(88px)との比率をPC版(ソファ幅≒キャラ2人分)に合わせる。巨大化するとキャラが飲まれる */
/* ===== M1: スマホ1画面ミニフロアマップ（論理374×470） ===== */
'#room:not(.hidden){position:relative;padding:0 0 28px;display:flex;flex-direction:column;align-items:center;background:#e4e5e8;min-height:calc(100dvh - 160px);overflow:hidden}' +
'#room:not(.hidden)::before{display:none}' +
'#mapframe{position:relative;width:100%;height:470px;overflow:hidden;flex:none;image-rendering:pixelated}' +
'#map{position:absolute;left:0;top:0;width:374px;height:470px;transform-origin:top left;transform:scale(var(--map-scale,1));background-color:#5a5751;background-image:url(/app/sprite/tile2_wall_top);background-repeat:repeat;background-size:24px 24px;image-rendering:pixelated;box-shadow:inset 0 0 0 2px rgba(40,37,32,.6)}' +
/* 廊下=木目（白タイルの部屋がPC同様に「部屋」として浮き上がる） */
'#map .mapfloor{position:absolute;left:8px;top:8px;right:8px;bottom:8px;background-color:#e0bd8d;background-image:url(/app/sprite/tile2_floor_wood);background-repeat:repeat;background-size:24px 24px;z-index:0}' +
'#map .mapface{position:absolute;left:8px;right:8px;top:8px;height:14px;background-image:url(/app/sprite/tile2_wall_face);background-repeat:repeat-x;background-size:24px 24px;z-index:1;pointer-events:none}' +
'#map .mapwindow{position:absolute;top:2px;height:18px;width:auto;z-index:2;image-rendering:pixelated;pointer-events:none;opacity:.95}' +
'#map .projectroom{position:absolute;top:22px;width:114px;height:110px;overflow:hidden;background:#e4e5e8 url(/app/sprite/tile2_floor_white) repeat;background-size:24px 24px;border:1px solid rgba(100,82,58,.38);z-index:2}' +
/* 南面=メインオフィスに面したガラス壁（PCと同じtile2_wall_glass実タイル） */
'#map .projectroom::before{content:"";position:absolute;left:0;right:0;bottom:0;height:14px;background:url(/app/sprite/tile2_wall_glass) repeat-x;background-size:24px 24px;background-position:0 100%;border-top:1px solid rgba(76,132,143,.4);z-index:4;pointer-events:none}' +
'#map .projectroom.r0{left:10px}#map .projectroom.r1{left:130px}#map .projectroom.r2{left:250px}' +
'#map .projectroom,#map .mainzone,#map .loungezone,#map .meetingzone{box-shadow:0 1px 0 rgba(255,255,255,.35) inset,0 2px 4px rgba(52,44,32,.18)}' +
'#map .mainzone{position:absolute;left:8px;top:150px;width:358px;height:170px;background:#e4e5e8 url(/app/sprite/tile2_floor_white) repeat;background-size:24px 24px;border:1px solid rgba(100,82,58,.28);z-index:1}' +
'#map .loungezone{position:absolute;left:8px;top:338px;width:177px;height:124px;overflow:hidden;background:#e4e5e8 url(/app/sprite/tile2_floor_white) repeat;background-size:24px 24px;border:1px solid rgba(110,106,98,.34);z-index:2}' +
'#map .meetingzone{position:absolute;left:191px;top:338px;width:175px;height:124px;overflow:hidden;background:#e6bc81 url(/app/sprite/tile2_floor_wood) repeat;background-size:24px 24px;border:1px solid rgba(106,82,48,.34);z-index:2}' +
'#map .zonepill{position:absolute;left:5px;top:5px;z-index:9;display:inline-flex;align-items:center;max-width:104px;min-height:19px;padding:2px 6px;background:rgba(42,34,24,.9);border:1px solid rgba(255,255,255,.18);border-radius:999px;color:#f2e4c8;font-size:9px;line-height:1.1;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none;box-shadow:0 1px 3px rgba(30,20,10,.28)}' +
'#map .mainzone .zonepill{max-width:150px}#map .meetingzone .zonepill{max-width:148px}' +
'#map .roompill{left:4px;top:18px;max-width:104px;background:rgba(43,73,78,.88);border-color:rgba(214,239,241,.65);font-size:8.5px}' +
'#map .mdesk{position:absolute;z-index:3;width:34px;height:27px;padding:0;border:0;background:transparent;pointer-events:none}' +
'#map .mdesk .deskimg{display:block;width:34px;height:auto;image-rendering:pixelated;filter:drop-shadow(0 2px 1px rgba(50,42,30,.2))}' +
'#map .mdesk.is-working::after{content:"";position:absolute;left:9px;top:2px;width:16px;height:7px;border-radius:2px;background:#7fd8a4;box-shadow:0 0 7px #7fd8a4;opacity:.85;animation:glowpulse 1.4s ease infinite}' +
'#map .loungezone .sofa{position:absolute;left:45px;top:61px;width:86px;height:auto;z-index:1;image-rendering:pixelated;filter:drop-shadow(0 2px 2px rgba(30,24,16,.24))}' +
'#map .loungezone .kitchenette{position:absolute;right:6px;top:22px;width:64px;height:auto;z-index:1;image-rendering:pixelated}' +
'#map .loungezone .plant{position:absolute;left:10px;bottom:6px;width:26px;height:auto;z-index:1;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(30,24,16,.16))}' +
'#map .meetingzone .mrug{position:absolute;left:12px;top:30px;width:151px;height:auto;z-index:1;image-rendering:pixelated;opacity:.9}' +
'#map .meetingzone .mtable{position:absolute;left:40px;top:49px;width:96px;height:auto;z-index:2;image-rendering:pixelated;filter:drop-shadow(0 2px 2px rgba(54,40,23,.24))}' +
'#map .meetingzone .whiteboard{position:absolute;right:7px;top:21px;width:27px;height:auto;z-index:2;image-rendering:pixelated}' +
'#map .mapchip{position:absolute;z-index:6;display:none;max-width:88px;padding:3px 6px;background:rgba(56,37,21,.92);border:1px solid rgba(245,201,107,.58);border-radius:999px;color:#ffe4ad;font-size:8.5px;font-weight:800;white-space:nowrap;pointer-events:none}' +
'#map .mapchip.on{display:block}' +
'#map .roomchip{left:auto;right:4px;bottom:18px}' +
'#map .mainzone .mzplant{position:absolute;right:4px;bottom:4px;width:24px;height:auto;z-index:1;image-rendering:pixelated;pointer-events:none}' +
/* キャラ=1社員1ノード。left/topを論理座標で遷移させ、spriteだけ220msで差し替える。 */
'#map .mchar{position:absolute;z-index:8;width:48px;height:50px;margin:0;padding:0;border:0;background:transparent;color:#2e2a22;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;transition:left 3.4s linear,top 3.4s linear;will-change:left,top;cursor:pointer}' +
/* 着席中(atdesk)は机前景(z3)の後ろ＝PCと同じ「机が腰下を隠す」座り表現 */
'#map .mchar.atdesk:not(.walking){z-index:2}' +
'#map .mchar .mavatar{display:block;height:34px;width:auto;max-width:48px;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.16))}' +
/* 名札=PC同様のダークプレート・50px以内(アンカー54pxピッチで非重なり) */
'#map .mchar .mname{display:block;max-width:50px;margin-top:1px;padding:0 4px;border-radius:4px;background:rgba(36,31,24,.88);color:#f2ead8;font-size:9px;line-height:12px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 2px rgba(30,24,16,.3)}' +
'#map .mchar[data-alert] .mname{background:rgba(142,52,42,.95);color:#ffe2d6}' +
'#map .mchar .typing{position:absolute;left:33px;top:-7px;z-index:2;display:none;padding:1px 3px;background:#fffaf0;border:1px solid #d8c7a2;border-radius:5px;color:#5c5346;font-size:9px;line-height:11px;box-shadow:0 1px 2px rgba(58,53,44,.16);animation:typingbob .8s steps(2) infinite}' +
'#map .mchar[data-state=working] .typing{display:block}' +
'#map .mchar[data-state=waiting] .mavatar{animation:mbreathe 3.4s ease-in-out infinite}' +
'#map .mchar[data-state=resting] .mavatar{filter:grayscale(.4) opacity(.88);animation:mbreathe 3.4s ease-in-out infinite}' +
'#map .mchar.walking .mavatar{animation:none!important}' +
'#map .mchar.faceflip .mavatar{transform:scaleX(-1)}' +
'#map .mchar.faceflip[data-state=waiting] .mavatar,#map .mchar.faceflip[data-state=resting] .mavatar{animation-name:mbreatheflip}' +
'#map .mchar[data-alert] .mavatar{outline:2px solid #c05a5a;outline-offset:2px;border-radius:5px;animation:attnpulse 1.1s ease-in-out infinite}' +
'#map .mchar[data-alert].walking .mavatar{animation:none!important}' +
'#map .mchar .mstate{position:absolute;right:-1px;top:2px;z-index:3;font-size:11px;line-height:1}' +
'#map .mchar.sel,#map .mchar:active{z-index:12}' +
/* 吹き出し会話(R23.5): 白ポップ・タップ透過・同時最大2体（過密マップを壊さない）。 */
'#map .mchar .msay{position:absolute;left:50%;bottom:52px;transform:translateX(-50%);z-index:9;display:none;max-width:104px;padding:2px 6px;background:#fffdf8;border:1px solid #d8c7a2;border-radius:7px;color:#3a352c;font-size:8.5px;line-height:1.35;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 2px rgba(58,53,44,.18);pointer-events:none}' +
'#map .mchar .msay.on{display:block}' +
'#map .mchar .msay.below{bottom:auto;top:52px}' +
'#map .mchar .msay.edgeL{left:0;transform:none}' +
'#map .mchar .msay.edgeR{left:auto;right:0;transform:none}' +
'@keyframes mbreathe{0%,100%{transform:translateY(0)}50%{transform:translateY(-1.5px)}}' +
'@keyframes mbreatheflip{0%,100%{transform:scaleX(-1) translateY(0)}50%{transform:scaleX(-1) translateY(-1.5px)}}' +
'@keyframes typingbob{0%,100%{transform:translateY(0)}50%{transform:translateY(-1px)}}' +
'@keyframes attnpulse{0%,100%{box-shadow:0 0 0 0 rgba(192,90,90,.65)}50%{box-shadow:0 0 0 4px rgba(192,90,90,0)}}' +
'@media (prefers-reduced-motion:reduce){#map .mchar{transition:none!important}#map .mchar *{animation:none!important}#map .mdesk.is-working::after{animation:none}}' +
'body.nowalk #map .mchar{transition:none!important}' +
'#room .openclaw{width:calc(100% - 24px)}' +
'/* OpenClaw室=社員を置かない常設の装飾バンド */' +
'.openclaw{height:90px;margin:2px 12px 4px;padding:7px 8px 5px;display:flex;flex-direction:column;background-color:#454c52;background-image:url(/app/sprite/tile2_floor_dark);background-repeat:repeat;background-size:24px 24px;image-rendering:pixelated;border:1px solid rgba(38,43,47,.48);border-radius:12px;box-shadow:inset 0 2px 7px rgba(12,16,20,.28)}' +
'.openclawhead{display:flex;align-items:center;gap:7px;min-height:23px;z-index:2}' +
'.openclawhead .zonelabel,.openclawhead .zonepill{margin:0;display:inline-flex;align-items:center;min-height:22px;padding:3px 8px;border-radius:999px;color:#e8edf0;background:rgba(25,31,35,.9);border:1px solid rgba(183,216,232,.35);font-size:11px;font-weight:800;white-space:nowrap;box-shadow:none}' +
'.ocpill{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border-radius:999px;background:rgba(222,232,238,.16);border:1px solid rgba(222,232,238,.36);color:#e8edf0;font-size:10px;font-weight:800;white-space:nowrap}' +
'.openclawstage{position:relative;display:flex;align-items:flex-end;justify-content:space-around;gap:6px;min-height:56px;flex:1;padding:0 7px;--oc-patrol:calc(100vw - 80px)}' +
'.openclawstage .crt{height:52px;width:auto;z-index:2;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.22))}' +
'.openclawstage .server{height:38px;width:auto;z-index:2;image-rendering:pixelated;filter:drop-shadow(0 2px 4px rgba(102,210,255,.25))}' +
'.openclawstage .ocbot{position:absolute;left:20px;bottom:2px;height:34px;width:auto;z-index:3;pointer-events:none;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.24));animation:ocpatrol 18s ease-in-out infinite}' +
'.openclawstage .ocbot2{animation-duration:26s;animation-delay:-8s}' +
// R76: OpenClaw室に実メンバーを出す（旧: 常に「未接続」＋巡回ロボだけの飾りだった）
// R77: 3Dオフィスの器（スマホ）。canvasは幅いっぱい・高さは画面の55%＝❗カードと両立
'#scene3dwrap{position:relative;margin:2px 10px 6px;border-radius:14px;overflow:hidden;background:linear-gradient(180deg,#e9ecf6,#dfe3f0);box-shadow:0 2px 10px rgba(52,44,32,.18)}' +
// 高さは「幅とほぼ同じ」＝balancedフィットの実描画に合わせる（55vhだと下に余白が出る）
'#scene3d{width:100%;height:min(52vh,calc(100vw - 20px));min-height:280px;max-height:520px;display:block}' +
'#scene3d canvas{width:100%!important;height:100%!important;display:block;touch-action:manipulation}' +
'#plates{position:absolute;inset:0;pointer-events:none}' +
'#plates .plate{position:absolute;transform:translate(-50%,-100%);pointer-events:auto;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:800 11px/1.2 inherit;color:#2b2f3a;background:rgba(255,255,255,.94);border:1px solid rgba(70,60,90,.22);border-radius:999px;padding:3px 9px;box-shadow:0 2px 6px rgba(40,34,60,.20)}' +
'#plates .plate.attn{color:#7c1d1d;background:#ffe9e6;border-color:#e5a49c}' +
'#plates .plate.sel{border-color:#5f9b78;box-shadow:0 0 0 2px rgba(95,155,120,.35)}' +
'.openclawstage .ocmem{position:relative;display:flex;flex-direction:column;align-items:center;gap:2px;z-index:4;background:none;border:0;padding:0 2px;cursor:pointer;font:inherit}' +
'.openclawstage .ocmem img{height:36px;width:auto;image-rendering:pixelated;filter:drop-shadow(0 2px 0 rgba(0,0,0,.24))}' +
'.openclawstage .ocmem .ocname{max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9.5px;font-weight:800;color:#e8edf0;background:rgba(25,31,35,.92);border:1px solid rgba(183,216,232,.35);border-radius:999px;padding:1px 6px}' +
'.openclawstage .ocmem .ocst{position:absolute;top:-3px;right:0;font-size:11px;line-height:1}' +
'.openclawstage .ocmem.sel .ocname{border-color:#ffd479;background:rgba(60,48,20,.95)}' +
'@keyframes lbreath{0%,100%{transform:translateY(0)}50%{transform:translateY(-2px)}}' +
'@keyframes bob{0%,100%{transform:translateX(-50%) translateY(0)}50%{transform:translateX(-50%) translateY(-2px)}}' +
'@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}' +
'@keyframes glowpulse{0%,100%{opacity:.35}50%{opacity:.85}}' +
'@keyframes ocpatrol{0%{transform:translateX(0) translateY(0) scaleX(1)}48%{transform:translateX(var(--oc-patrol)) translateY(-1px) scaleX(1)}50%{transform:translateX(var(--oc-patrol)) translateY(0) scaleX(-1)}98%{transform:translateX(0) translateY(-1px) scaleX(-1)}100%{transform:translateX(0) translateY(0) scaleX(1)}}' +
'@media (prefers-reduced-motion:reduce){.openclawstage .ocbot{animation:none!important}.openclawstage .ocbot1{left:18px;transform:none}.openclawstage .ocbot2{left:auto;right:18px;transform:scaleX(-1)}}' +
'@media (prefers-reduced-motion:reduce){.ws .char,.ws[data-alert] .badge,.ws[data-state=working] .screenglow{animation:none}}' +
'@keyframes breath{0%,100%{transform:translateX(-50%) translateY(0)}50%{transform:translateX(-50%) translateY(-1.5px)}}' +
'.ws[data-state=waiting] .char,.ws[data-state=resting] .char{animation:breath 3.4s ease-in-out infinite}' +
'.chip{min-height:44px;display:inline-flex;align-items:center;justify-content:center}' +
'button:active,.chip:active,.card:active,.ws:active,.mseat:active,.lseat:active{transform:translateY(1px);filter:brightness(.96)}' +
'.ws.sel .stage{outline:3px solid var(--sage-l);outline-offset:2px;border-radius:8px}' +
'.card.sel{outline:2px solid var(--sage);outline-offset:-1px}' +
'.mseat.sel,.lseat.sel{box-shadow:0 0 0 3px var(--sage-l);border-radius:6px}' +
'button:focus-visible,.chip:focus-visible,.card:focus-visible,.ws:focus-visible,.tabbar button:focus-visible{outline:3px solid var(--sage-l);outline-offset:2px}' +
'</style></head><body>' +
'<div class=topbar><div class=hdr2 onclick="window.scrollTo(0,0)"><span class=live id=live></span><span class=ttl>🏢 AI Office</span><span class=total id=total></span></div>' +
'<div class=statbar id=statbar></div>' +
'<div class=deptbar id=deptbar></div>' +
'<div id=banner>⚠️ オフライン・再接続中…</div></div>' +
'<main id=app class=hidden><div id=room></div><div id=list class=hidden></div><div id=note></div></main>' +
'<nav class="tabbar hidden" id=tabbar>' +
'<button id=tb_office onclick="setView(\'office\')"><span class=ic>🏢</span><span id=tb_office_lb>オフィス</span></button>' +
'<button id=tb_list onclick="setView(\'list\')"><span class=ic>☰</span><span id=tb_list_lb>リスト</span></button>' +
'<button id=ntog onclick="togglePush()"><span class=ic id=ntogic>🔕</span><span id=ntoglb>通知OFF</span></button>' +
'<button id=tb_set onclick="openSettings()"><span class=ic>⚙️</span><span id=tb_set_lb>設定</span></button>' +
'</nav>' +
'<div id=setwrap><div class=mask onclick="closeSettings()"></div><div class="sheet">' +
'<h3 style="margin-bottom:10px" id=st_title>⚙️ 設定</h3>' +
'<div class=setrow><div><div class=lb id=st_th_lb>🎨 テーマ</div><div class=hint id=st_th_hint>配色を切り替えます</div></div>' +
'<div class=seg><button id=sg_th_c onclick="setTheme(\'classic\')">クラシック</button><button id=sg_th_d onclick="setTheme(\'dark\')">ダーク</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_fs_lb>🔎 文字サイズ</div></div>' +
'<div class=seg><button id=sg_fs_s onclick="setBig(false)">標準</button><button id=sg_fs_b onclick="setBig(true)">大きめ</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_wk_lb>🚶 歩行アニメ</div><div class=hint id=st_wk_hint>マップ内の歩行移動</div></div>' +
'<div class=seg><button id=sg_wk_on onclick="setWalk(true)">ON</button><button id=sg_wk_off onclick="setWalk(false)">OFF</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_sd_lb>🔊 効果音</div><div class=hint id=st_sd_hint>ロボット風の効果音（初期OFF）</div></div>' +
'<div class=seg><button id=sg_sd_on onclick="setSound(true)">ON</button><button id=sg_sd_off onclick="setSound(false)">OFF</button></div></div>' +
'<div class=setrow><div><div class=lb id=st_pt_lb>🔔 通知テスト</div><div class=hint id=st_pt_hint>登録済みの全端末へテスト通知。購読時のフィルタが通知対象になります（全部=すべて）</div></div>' +
'<div class=seg><button id=st_pt_btn onclick="sendTestPush()">送信</button></div></div>' +
'<div class=setrow style="border-bottom:0"><div><div class=lb id=st_rp_lb>🔗 再ペアリング</div><div class=hint id=st_rp_hint>この端末の登録をやり直します</div></div>' +
'<div class=seg><button class=r id=st_rp_btn style="background:var(--danger);color:#fff" onclick="repair()">解除</button></div></div>' +
'<button class=sub id=st_close onclick="closeSettings()">閉じる</button>' +
'<div class=setver>AI Office PWA v3.8</div>' +
'</div></div>' +
'<div id=setup class="setup hidden"><h2 id=su_title>📱 スマホをペアリング</h2>' +
'<p><span id=su_p1>Macの AIオフィス画面（左パネル「📱 スマホ連携」）でペアリングを発行し、表示された</span><b id=su_link>リンク</b><span id=su_p2>をこの端末へ送って開くと自動でペアリングされます。うまくいかない時は、Mac側の「資格情報をコピー」で得た文字列を下に貼り付けてください。</span></p>' +
'<textarea id=pastebox rows=3 placeholder="v=1&d=...&s=...&t=...&e=... または /app#... リンク全体"></textarea>' +
'<button id=su_btn onclick="pasteCred()">この端末を登録</button></div>' +
'<div id=logwrap><div class=mask onclick="closeLog()"></div>' +
'<div class="sheet logsheet"><div class=shhead><img id=lgava alt=""><div><h3 id=lgname></h3><div class=who id=lgwho></div></div></div>' +
'<div id=lgbody></div><button class=sub id=lg_close onclick="closeLog()">閉じる</button></div></div>' +
'<div id=sheetwrap><div class=mask onclick="closeSheet()"></div>' +
'<div class=sheet><div class=shhead><img id=shava alt=""><div><h3 id=shname></h3><div class=who id=shwho></div></div></div><div id=shsay></div><div id=shdetail></div><div id=quickbtns></div>' +
'<textarea id=freetext rows=2 placeholder="自由に指示（例: キリのいいところでコミットして残タスク報告）"></textarea>' +
'<button class=g id=sh_send onclick="sendFree()">✍️ この指示を送る</button>' +
'<button class=sub id=sh_close onclick="closeSheet()">閉じる</button></div></div>' +
// ★ __name ガード: wrangler(esbuild)が .toString() 前の関数体へ keep-names ヘルパー
//   __name(fn,"n") を注入することがあり、ブラウザ側には未定義＝ReferenceErrorで
//   スクリプト全体が死ぬ（R65で実測）。恒等関数を先に敷いて無害化する。
'<script>var __name=typeof __name==="function"?__name:function(f){return f};' +
PWA_ASSIGN_ROOMS_SOURCE + "\n" + PWA_GLOSS_SOURCE +
// R42.2d-2 言語: office.lang が正本（statusで追随）・localStorageは初回描画用キャッシュ
'var LANG=(function(){try{return localStorage.getItem("aioffice.lang")==="en"?"en":"ja"}catch(e){return "ja"}})();' +
'function T(ja,en){return LANG==="en"?en:ja}' +
'var WALKF=__WALK_STEMS__;' +
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
'function checkAnswered(agents){var live={};(Array.isArray(agents)?agents:[]).forEach(function(e){if(e&&needsAttn(e)){var k=attnKey(e);if(k)live[k]=1}});var done=[];Object.keys(ATTN_SENT).forEach(function(k){if(!live[k]){done.push(k);delete ATTN_SENT[k]}});if(!done.length)return;saveAttnSent();var names=[];(Array.isArray(agents)?agents:[]).forEach(function(e){if(done.indexOf(attnKey(e))>=0){var n=e.disp||e.session;if(n)names.push(n)}});note(T("✅ 回答済み: ","✅ Answered: ")+(names.join(", ")||T("対応完了","resolved")))}' +
// R51: 配達往復の可視化＝pending(📨 queued)が消えた瞬間を捉えて ✓ delivered チップを短時間出す
'var PEND_PREV={},DELIVERED={};' +
'function checkDelivery(agents){var now=Date.now(),cur={};(Array.isArray(agents)?agents:[]).forEach(function(e){if(e&&e.pending){var k=attnKey(e);if(k)cur[k]=1}});Object.keys(PEND_PREV).forEach(function(k){if(!cur[k])DELIVERED[k]=now+25000});PEND_PREV=cur;Object.keys(DELIVERED).forEach(function(k){if(DELIVERED[k]<now)delete DELIVERED[k]})}' +
// R51: 鮮度の正直表示。status.ts(DO書込時刻)かagentSeenAgo(最終/sync)が180秒超なら stale＝
// バナー＋グレースケール(body.off流用)＋送信時confirm（{ok:true}トーストだけの無言ロストを塞ぐ）
'var STALE=false;' +
'function updateStale(tsMs,agentAgo){var age=(typeof tsMs==="number"&&tsMs>0)?Math.floor((Date.now()-tsMs)/1000):null;var was=STALE;STALE=((age!=null&&age>180)||(typeof agentAgo==="number"&&agentAgo>180));var b=document.getElementById("banner");if(STALE){var worst=Math.max(age||0,typeof agentAgo==="number"?agentAgo:0);if(b)b.textContent=T("⚠️ Mac最終同期 "+fmtAge(worst)+" — 再同期まで指示は届きません","⚠️ Mac last sync "+fmtAge(worst)+" — instructions wait until it reconnects");document.body.classList.add("off")}else{document.body.classList.remove("off");if(b&&was)b.textContent=T("⚠️ オフライン・再接続中…","⚠️ Offline — reconnecting…")}}' +
'var AUDIO={enabled:false,ctx:null,master:null,played:0,lastAt:{}};' +
'try{AUDIO.enabled=localStorage.getItem("aioffice.sound")==="1"}catch(_){}' +
'var PULSE_WAVE_CACHE=new WeakMap();' +
'function pulseWave(ctx,duty){if(!ctx||!Number.isFinite(duty)||duty<=0||duty>=1)return null;var waves=PULSE_WAVE_CACHE.get(ctx);if(!waves){waves=new Map();PULSE_WAVE_CACHE.set(ctx,waves)}if(waves.has(duty))return waves.get(duty);var N=24,real=new Float32Array(N+1),imag=new Float32Array(N+1);for(var n=1;n<=N;n++)real[n]=(2/(n*Math.PI))*Math.sin(n*Math.PI*duty);var wave=ctx.createPeriodicWave(real,imag);waves.set(duty,wave);return wave}' +
'function ensureCtx(){if(!AUDIO.enabled||AUDIO.ctx)return AUDIO.ctx;var AudioCtor=window.AudioContext||window.webkitAudioContext;if(!AudioCtor)return null;try{var ctx=new AudioCtor(),master=ctx.createGain();master.gain.value=.4;master.connect(ctx.destination);AUDIO.ctx=ctx;AUDIO.master=master;return ctx}catch(_){return null}}' +
'function tone(ctx,dest,o){o=o||{};if(!ctx||!dest||!Number.isFinite(o.freq))return;var at=Number.isFinite(o.at)?o.at:ctx.currentTime,decay=Number.isFinite(o.decay)?o.decay:.2,attack=Number.isFinite(o.attack)?o.attack:.005,peak=Number.isFinite(o.peak)?o.peak:.2;if(!Number.isFinite(at)||!Number.isFinite(decay)||decay<=0)return;var osc=ctx.createOscillator(),gain=ctx.createGain(),wave=pulseWave(ctx,o.duty);if(wave)osc.setPeriodicWave(wave);else osc.type=o.type||"sine";osc.frequency.setValueAtTime(o.freq,at);if(Number.isFinite(o.toFreq)&&o.toFreq>0)osc.frequency.exponentialRampToValueAtTime(o.toFreq,at+decay);gain.gain.setValueAtTime(.0001,at);var attackTime=Math.max(.001,Math.min(decay,attack));gain.gain.linearRampToValueAtTime(Math.max(.0001,peak),at+attackTime);gain.gain.exponentialRampToValueAtTime(.0001,at+decay);osc.connect(gain);gain.connect(dest);osc.start(at);osc.stop(at+decay+.05)}' +
'function playSoundRecipe(kind,ctx,dest,at){if(kind==="select")tone(ctx,dest,{freq:987.8,duty:.25,at:at,peak:.22,attack:.002,decay:.055});else if(kind==="cursor")tone(ctx,dest,{freq:740,duty:.25,at:at,peak:.12,decay:.03});else if(kind==="send")tone(ctx,dest,{freq:880,duty:.25,at:at,peak:.20,decay:.08,toFreq:1568});else if(kind==="attn"){tone(ctx,dest,{freq:880,duty:.25,at:at,peak:.40,decay:.55});tone(ctx,dest,{freq:1174.7,duty:.25,at:at+.17,peak:.40,decay:.55})}}' +
'function playSE(kind){if(!AUDIO.enabled||!AUDIO.ctx||!AUDIO.master||document.hidden)return false;var now=performance.now(),min=kind==="attn"?10000:90;if(now-(AUDIO.lastAt[kind]||-Infinity)<min)return false;try{playSoundRecipe(kind,AUDIO.ctx,AUDIO.master,AUDIO.ctx.currentTime);AUDIO.lastAt[kind]=now;AUDIO.played++;return true}catch(_){return false}}' +
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
'if(!emps.length){list.appendChild(el("div","empty",PREF.deptFilter?T("（選択中の部署に在席社員がいません）","(No employees in the selected department)"):T("（出勤中の社員がいません）","(No employees are on duty right now)")));return}' +
'emps.slice().sort(triageSort).forEach(function(e){var card=el("div","card"+(needsAttn(e)?" alert":isPend(e)?" pend":""));card.setAttribute("data-sess",e.session||"");card.setAttribute("role","button");card.tabIndex=0;card.addEventListener("keydown",function(ev){if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();openSheet(e)}});' +
'var meta=el("div","meta");var cav=document.createElement("img");cav.className="cav";cav.decoding="async";cav.onerror=function(){cav.onerror=null;cav.src=spriteURL("generic_m")};cav.src=spriteURL(spriteBase(e));meta.appendChild(cav);' +
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
'function sayText(e){e=e||{};var action=activityGlossPWA(e,LANG);var s=(e.disp||e.session||T("キャラクター","Agent"))+T("です！"," here! ")+(action?T("いま「"+action+"」です。","Currently: "+action+"."):T("いまの状況を確認中です。","Checking the current status."));if(e.question)s+="\\n❓ "+e.question;if((e.approvalMin||0)>0)s+=T("\\n❗ 承認まちです","\\n❗ Waiting for approval");return s}' +
'function questionOptionEntries(e){if(!e||!e.question||!Array.isArray(e.questionOptions))return [];return e.questionOptions.slice(0,4).map(function(option){var raw=String((option&&option.label)||"");if(!raw.trim())return null;var recommended=raw.indexOf("(Recommended)")>=0;var clean=raw.replace(/\\s*\\(Recommended\\)\\s*/g," ").replace(/\\s{2,}/g," ").trim()||raw.trim();return {raw:raw,label:(recommended?"⭐ ":"")+clean,desc:String((option&&option.desc)||"")};}).filter(Boolean)}' +
'function appendQuestionOptions(parent,e,klass){if(!parent)return;questionOptionEntries(e).forEach(function(option){var b=el("button","qopt "+klass);b.type="button";var label=el("span","qopt-label",option.label),desc=el("span","qopt-desc",option.desc);b.appendChild(label);b.appendChild(desc);b.addEventListener("click",function(ev){ev.stopPropagation();send(T("選択肢「"+option.raw+"」でお願いします。","Please go with the option: "+option.raw),e)});parent.appendChild(b)})}' +
'function appendWorkBlock(parent,work){if(!parent||!work||typeof work!=="object")return;var block=el("div","wk-work");block.appendChild(el("div","wk-title",T("📋 いまの仕事","📋 Current work")));[["now",T("▶ いま","▶ Now")],["next",T("⏭ 次","⏭ Next")],["done",T("✅ 済み","✅ Done")]].forEach(function(pair){var values=Array.isArray(work[pair[0]])?work[pair[0]].filter(function(value){return typeof value==="string"&&value}):[];if(!values.length)return;var row=el("div","wk-row wk-"+pair[0]);row.appendChild(el("span","wk-label",pair[1]));var items=el("div","wk-items");values.forEach(function(value){items.appendChild(el("div","wk-item",value))});row.appendChild(items);block.appendChild(row)});parent.appendChild(block)}' +
'function openSheet(e){if(e)playSE("select");if(SHSAY_IV){clearInterval(SHSAY_IV);SHSAY_IV=null}var shsay=document.getElementById("shsay");shsay.textContent="";SEL=e;document.querySelectorAll(".sel").forEach(function(n){n.classList.remove("sel")});if(e&&e.session){document.querySelectorAll("[data-sess]").forEach(function(n){if(n.getAttribute("data-sess")===e.session)n.classList.add("sel")})}document.getElementById("shname").textContent=dispCrew(e);' +
'var sa=document.getElementById("shava");sa.decoding="async";sa.onerror=function(){sa.onerror=null;sa.src=spriteURL("generic_m")};sa.src=spriteURL(spriteBase(e));' +
'document.getElementById("shwho").textContent=activityGlossPWA(e,LANG)+" ・"+fmtAge(e.age);' +
'var sht=sayText(e);if(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches){shsay.textContent=sht}else{var shchars=Array.from(sht),shi=0;SHSAY_IV=setInterval(function(){shsay.appendChild(document.createTextNode(shchars[shi++]));if(shi>=shchars.length){clearInterval(SHSAY_IV);SHSAY_IV=null}},18)}' +
'var dt=document.getElementById("shdetail");dt.innerHTML="";appendWorkBlock(dt,e.work);' +
// R51: roster の sessions[] 内訳ミニ行（state/age/❗/📨のドットのみ・本文は構造的に持たない）
'if(Array.isArray(e.sessions)&&e.sessions.length>1){dt.appendChild(el("div","sec",T("👥 セッション内訳（"+e.sessions.length+"）","👥 Sessions ("+e.sessions.length+")")));var sw=el("div","sessrows");e.sessions.slice(0,8).forEach(function(s){var r=el("div","sessrow");r.appendChild(el("span","dot "+(s.state||""),""));r.appendChild(el("span","sessid",String(s.session||"").slice(0,8)));r.appendChild(el("span","sessage",fmtAge(s.age)));if(s.attention)r.appendChild(el("span",null,"❗"));if(s.pending)r.appendChild(el("span",null,"📨"));if(s.minions)r.appendChild(el("span",null,"👥"+s.minions));sw.appendChild(r)});if(e.sessions.length>8)sw.appendChild(el("div","sessrow",T("ほか"+(e.sessions.length-8)+"件","+"+(e.sessions.length-8)+" more")));dt.appendChild(sw)}' +
// 質問は最上部+強調（承認/回答がシートの主目的・埋もれさせない）
'if(e.question){dt.appendChild(el("div","sec",T("❓ 質問待ち — 下のボタンか自由指示で回答","❓ Question waiting — reply with a button below or a custom instruction")));var qd=el("div","said saidq",e.question);dt.appendChild(qd)}' +
'var fd=(e.feed||[]);if(fd.length){dt.appendChild(el("div","sec",T("📋 最近の動き（"+fd.length+"件）— タップで全文を拡大","📋 Recent activity ("+fd.length+") — tap to expand")));var fb=el("div","feedbox");fb.setAttribute("role","button");fb.tabIndex=0;fd.slice(0,16).forEach(function(l){fb.appendChild(el("div","feedline",l))});fb.addEventListener("click",openLog);dt.appendChild(fb)}' +
'if(e.lastSaid){dt.appendChild(el("div","sec",T("💬 直近の発言","💬 Latest message")));dt.appendChild(el("div","said",e.lastSaid))}' +
'var q=document.getElementById("quickbtns");q.innerHTML="";if(e.question&&questionOptionEntries(e).length){var qo=el("div","quickoptions");appendQuestionOptions(qo,e,"quickoption");q.appendChild(qo)}QUICK().forEach(function(it){var b=el("button",it.c,it.l);' +
'b.onclick=function(){send(it.t)};q.appendChild(b)});document.getElementById("freetext").value="";' +
'document.getElementById("sheetwrap").classList.add("open")}' +
'function closeSheet(){var wrap=document.getElementById("sheetwrap");if(wrap&&wrap.classList.contains("open"))playSE("cursor");if(SHSAY_IV){clearInterval(SHSAY_IV);SHSAY_IV=null}if(wrap)wrap.classList.remove("open");document.querySelectorAll(".sel").forEach(function(n){n.classList.remove("sel")})}' +
// 全文ログビューア: 「最近の動き」タップで開く（feed全件+質問+発言を大きな文字で・承認判断の材料）
'function openLog(){var e=SEL;if(!e)return;document.getElementById("lgname").textContent=(e.disp||e.session);' +
'var la=document.getElementById("lgava");la.decoding="async";la.onerror=function(){la.onerror=null;la.src=spriteURL("generic_m")};la.src=spriteURL(spriteBase(e));' +
'document.getElementById("lgwho").textContent=activityGlossPWA(e,LANG)+" ・"+fmtAge(e.age);' +
'var b=document.getElementById("lgbody");b.innerHTML="";' +
'if(e.question){b.appendChild(el("div","lgsec",T("❓ 質問まち","❓ Question waiting")));b.appendChild(el("div","lgline",e.question))}' +
'var fd=(e.feed||[]);b.appendChild(el("div","lgsec",T("📋 最近の動き 全"+fd.length+"件（新しい順）","📋 Recent activity — all "+fd.length+" (newest first)")));' +
'fd.forEach(function(l){b.appendChild(el("div","lgline",l))});' +
'if(e.lastSaid){b.appendChild(el("div","lgsec",T("💬 直近の発言","💬 Latest message")));b.appendChild(el("div","lgline",e.lastSaid))}' +
'document.getElementById("logwrap").classList.add("open")}' +
'function closeLog(){var wrap=document.getElementById("logwrap");if(wrap&&wrap.classList.contains("open"))playSE("cursor");if(wrap)wrap.classList.remove("open")}' +
// ⚙️設定: テーマ/文字サイズ/歩行アニメはlocalStorage永続・即時適用
'var PREF={theme:localStorage.getItem("aioffice.theme")||"classic",big:localStorage.getItem("aioffice.big")==="1",walk:localStorage.getItem("aioffice.walk")!=="off",deptFilter:localStorage.getItem("aioffice.deptFilter")||""};' +
'function applyPrefs(){var df=localStorage.getItem("aioffice.deptFilter");if(df!==null)PREF.deptFilter=df;document.body.classList.toggle("th-dark",PREF.theme==="dark");document.body.classList.toggle("th-big",!!PREF.big);document.body.classList.toggle("nowalk",!PREF.walk);var tc=document.querySelector("meta[name=theme-color]");if(tc)tc.setAttribute("content",PREF.theme==="dark"?"#241f18":"#fffdf8")}' +
// R42.2d-2 静的チローム再適用: 焼き込みHTMLの日本語をtextContent/placeholderで差し替え（innerHTML禁止=XSS掟）
'var NTOG_STATE=false;' +
'function applyLangChrome(){document.documentElement.lang=LANG;function st(id,ja,en){var n=document.getElementById(id);if(n)n.textContent=T(ja,en)}' +
// OpenClaw帯はmapShellで一度だけ生成される＝言語切替時にここで再適用（生成前はopenclawZone側のT()が効く）
'st("ocz_lb","🤖 OpenClaw室","🤖 OpenClaw Room");if(typeof paintOpenclaw==="function")paintOpenclaw(LAST_OFFICE);' +
'st("banner","⚠️ オフライン・再接続中…","⚠️ Offline — reconnecting…");st("tb_office_lb","オフィス","Office");st("tb_list_lb","リスト","List");st("tb_set_lb","設定","Settings");' +
'st("st_title","⚙️ 設定","⚙️ Settings");st("st_th_lb","🎨 テーマ","🎨 Theme");st("st_th_hint","配色を切り替えます","Switch the color scheme");st("sg_th_c","クラシック","Classic");st("sg_th_d","ダーク","Dark");' +
'st("st_fs_lb","🔎 文字サイズ","🔎 Text size");st("sg_fs_s","標準","Normal");st("sg_fs_b","大きめ","Large");st("st_wk_lb","🚶 歩行アニメ","🚶 Walk animation");st("st_wk_hint","マップ内の歩行移動","Characters walk around the map");' +
'st("st_sd_lb","🔊 効果音","🔊 Sound effects");st("st_sd_hint","ロボット風の効果音（初期OFF）","Robot-style sound effects (off by default)");st("st_pt_lb","🔔 通知テスト","🔔 Test notification");' +
'st("st_pt_hint","登録済みの全端末へテスト通知。購読時のフィルタが通知対象になります（全部=すべて）","Sends a test push to every registered device. Your subscription filter applies (All = everything)");st("st_pt_btn","送信","Send");' +
'st("st_rp_lb","🔗 再ペアリング","🔗 Re-pair");st("st_rp_hint","この端末の登録をやり直します","Reset the registration of this device");st("st_rp_btn","解除","Unpair");st("st_close","閉じる","Close");' +
'st("su_title","📱 スマホをペアリング","📱 Pair your phone");st("su_p1","Macの AIオフィス画面（左パネル「📱 スマホ連携」）でペアリングを発行し、表示された","Issue a pairing from the AI Office screen on your Mac (left panel: 📱 Phone), then send the ");st("su_link","リンク","link");' +
'st("su_p2","をこの端末へ送って開くと自動でペアリングされます。うまくいかない時は、Mac側の「資格情報をコピー」で得た文字列を下に貼り付けてください。"," shown there to this device and open it to pair automatically. If that fails, paste the string from Copy credentials on the Mac below.");st("su_btn","この端末を登録","Register this device");' +
'st("lg_close","閉じる","Close");st("sh_send","✍️ この指示を送る","✍️ Send this instruction");st("sh_close","閉じる","Close");' +
'var pb=document.getElementById("pastebox");if(pb)pb.placeholder=T("v=1&d=...&s=...&t=...&e=... または /app#... リンク全体","v=1&d=...&s=...&t=...&e=... or the full /app#... link");' +
'var ft=document.getElementById("freetext");if(ft)ft.placeholder=T("自由に指示（例: キリのいいところでコミットして残タスク報告）","Custom instruction (e.g. commit at a good stopping point and report remaining tasks)");' +
'setNtog(NTOG_STATE)}' +
// R42.2d-2 office.lang=正本: status受領点で差分適用→チローム再適用→LAST_SIG=""で全再描画を強制
'function applyOfficeLang(lang){var v=lang==="en"?"en":"ja";if(v===LANG)return;LANG=v;try{localStorage.setItem("aioffice.lang",v)}catch(e){}applyLangChrome();LAST_SIG=""}' +
'function empDept(e){var d=String((e&&e.dept)||(e&&e.name)||"").trim();if(d)return Array.from(d).slice(0,40).join("");var n=String((e&&e.disp)||"").trim();return Array.from((n&&n.split(/\\s+/)[0])||"").slice(0,40).join("")}' +
'function filterEmps(emps){var a=(Array.isArray(emps)?emps:[]).filter(function(e){return e&&!e.external});return PREF.deptFilter?a.filter(function(e){return empDept(e)===PREF.deptFilter}):a}' +
// R42.1 エディション機能フラグ（office.edition.features・未定義はtrue=旧server後方互換）
'function featOn(name){var f=LAST_OFFICE&&LAST_OFFICE.edition&&LAST_OFFICE.edition.features;return !f||f[name]!==false}' +
'function buildDeptbar(emps){var bar=document.getElementById("deptbar");if(!bar)return;bar.innerHTML="";var ds=[];(Array.isArray(emps)?emps:[]).forEach(function(e){var d=empDept(e);if(d&&ds.indexOf(d)<0)ds.push(d)});bar.style.display=ds.length<2?"none":"";if(ds.length<2)return;function add(d,label){var b=el("button","deptchip",label);var on=PREF.deptFilter===d;b.classList.toggle("on",on);b.setAttribute("aria-pressed",on?"true":"false");b.addEventListener("click",function(){setDeptFilter(d)});bar.appendChild(b)}add("",T("全部","All"));ds.forEach(function(d){add(d,d)})}' +
'function setDeptFilter(d){PREF.deptFilter=String(d||"");localStorage.setItem("aioffice.deptFilter",PREF.deptFilter);dispatch()}' +
'function markSeg(){function m(id,on){var b=document.getElementById(id);if(b)b.classList.toggle("on",!!on)}m("sg_th_c",PREF.theme!=="dark");m("sg_th_d",PREF.theme==="dark");m("sg_fs_s",!PREF.big);m("sg_fs_b",PREF.big);m("sg_wk_on",PREF.walk);m("sg_wk_off",!PREF.walk);m("sg_sd_on",AUDIO.enabled);m("sg_sd_off",!AUDIO.enabled)}' +
'function setTheme(t){PREF.theme=t;localStorage.setItem("aioffice.theme",t);applyPrefs();markSeg()}' +
'function setBig(v){PREF.big=v;localStorage.setItem("aioffice.big",v?"1":"0");applyPrefs();markSeg()}' +
'function setWalk(v){PREF.walk=v;localStorage.setItem("aioffice.walk",v?"on":"off");applyPrefs();markSeg()}' +
'function openSettings(){markSeg();document.getElementById("setwrap").classList.add("open")}' +
'function closeSettings(){var wrap=document.getElementById("setwrap");if(wrap&&wrap.classList.contains("open"))playSE("cursor");if(wrap)wrap.classList.remove("open")}' +
'function setSound(v){AUDIO.enabled=!!v;try{if(AUDIO.enabled)localStorage.setItem("aioffice.sound","1");else localStorage.removeItem("aioffice.sound")}catch(_){}markSeg();if(AUDIO.enabled){var ctx=ensureCtx();if(ctx){try{ctx.resume().catch(()=>{})}catch(_){}}playSE("select")}}' +
'function sendTestPush(){pushApi("/push/test",{}).then(function(d){if(d&&d.ok){note(d.sent?T("🔔 テスト通知を送信しました（"+d.sent+"台）","🔔 Test notification sent ("+d.sent+" devices)"):T("⚠ 通知登録が0台です。先にフッターの🔕をONにしてください","⚠ No devices subscribed. Turn on 🔕 in the footer first"))}else{note("⚠ "+((d&&d.error)||T("送信失敗","send failed")))}}).catch(function(){note(T("⚠ 送信できませんでした","⚠ Could not send"))})}' +
'function repair(){if(!confirm(T("この端末のペアリングを解除して、登録画面に戻ります。よろしいですか？","Unpair this device and return to the setup screen. OK?")))return;localStorage.removeItem(KEY);location.reload()}' +
'function sendFree(){var v=document.getElementById("freetext").value.trim();if(v)send(v)}' +
'function send(text,target){if(SENDING)return;target=target||SEL;var cred=getCred();if(!cred||!target)return;' +
// R51: stale中の送信は1回confirm（Macが受信するまで届かない＝{ok:true}トーストの無言ロストを塞ぐ）
'if(STALE&&!confirm(T("Macがしばらく同期していません。Macが再同期するまで指示は届きませんが送信しますか？","Your Mac has not synced recently. The instruction will not arrive until it reconnects. Send anyway?")))return;' +
'SENDING=true;var session=target.session;closeSheet();' +
'note(T("送信中…","Sending…"));sign(cred,session,text).then(function(env){' +
'return fetch("/instruct",{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+cred.t},body:JSON.stringify(env)})})' +
'.then(function(r){return r.json()}).then(function(d){if(d&&d.ok){note(T("📨 Macへ送信しました","📨 Sent to your Mac"));if(needsAttn(target)){ATTN_SENT[attnKey(target)]=1;saveAttnSent();updateAttnCards(officeAgents(LAST_OFFICE))}playSE("send");setTimeout(function(){poll()},1200)}else note("⚠ "+((d&&d.error)||T("失敗","failed")))})' +
'.catch(function(){note(T("⚠ 送信できませんでした","⚠ Could not send"))}).then(function(){SENDING=false})}' +
'function note(m){var n=document.getElementById("note");if(!n)return;n.textContent=m;var err=m.indexOf("⚠")===0;n.className="show"+(err?" err":"");if(n._t){clearTimeout(n._t);n._t=null}if(m.indexOf(T("送信中","Sending"))<0){n._t=setTimeout(function(){n.className=""},err?4200:5200)}}' +
'var VIEW=(localStorage.getItem("aioffice.view")||"office");' +
'var LAST_OFFICE={};var LAST_SIG="",LAST_VIEW="",POLL_IV=null,ATTN_SESSIONS={};' +
'function sceneSig(emps){return PREF.deptFilter+"|"+emps.map(function(e){return (e.session||"")+"|"+(e.disp||"")+"|"+empDept(e)+"|"+(e.state||"")+"|"+(e.verb||"")+"|"+(e.target||"")+"|"+((e.feed&&e.feed[0])||"")+"|"+(e.question||"")+"|"+(e.pending?1:0)+"|"+(e.minions||0)+"|"+(needsAttn(e)?1:0)+"|"+(e.stuckTool||"")+"|"+(e.approvalMin||0)+"|"+(e.sprite||"")+"|"+(e.projectId||"")+"|"+(e.crew||0)+"|"+((e.work&&e.work.now&&e.work.now[0])||"")}).join(";")+"|D:"+Object.keys(DELIVERED).join(",")}' +
'function needsAttn(e){return (e.approvalMin>0)||!!e.question}' +
'function attnSessionSet(emps){var set={};(Array.isArray(emps)?emps:[]).forEach(function(e){if(e&&needsAttn(e)){var k=attnKey(e);if(k)set[k]=1}});return set}' +
'function checkAttnEdge(emps){var next=attnSessionSet(emps),newly=Object.keys(next).filter(function(s){return !ATTN_SESSIONS[s]});ATTN_SESSIONS=next;if(newly.length)playSE("attn")}' +
'function isPend(e){return !!e.pending&&!needsAttn(e)}' +
'function spriteBase(e){var s=((e.sprite||"").split("/").pop()||"").replace(/\\.png$/i,"");return /^[A-Za-z0-9_]+$/.test(s)?s:"generic_m"}' +
'function spriteURL(base){return "/app/sprite/"+encodeURIComponent(base)}' +
'function loadOfficeCache(){try{var raw=localStorage.getItem("aioffice.lastOffice");return raw?JSON.parse(raw):null}catch(_){return null}}' +
'function saveOfficeCache(office){try{localStorage.setItem("aioffice.lastOffice",JSON.stringify(office))}catch(_){}}' +
'function preloadCriticalSprites(){var names=["tile2_floor_white","tile2_floor_wood","tile2_floor_dark","tile2_wall_top","tile2_wall_face","furn2_desk_nochair","furn2_meeting_table","furn2_rug_meeting","furn2_sofa_cream","furn2_kitchenette_modern","furn2_plant_modern","furn_window_wide","furn_noticeboard","furn_wall_clock","furn2_crt_station","furn2_server_led","agent_bot"];try{for(var i=0;i<names.length;i++){var img=new Image();img.decoding="async";img.src=spriteURL(names[i])}}catch(_){}}' +
'function sceneImg(cls,name,alt){var img=document.createElement("img");img.className=cls||"";img.decoding="async";img.src=spriteURL(name);img.alt=alt||"";return img}' +
'function gestureBadge(e,overflow){if(overflow)return "💧";if(e&&e.state==="resting")return "☕";var v=String((e&&e.verb)||"");if(v.indexOf("考え中")===0)return "💭";if(e&&e.state==="working")return "⌨";return ""}' +
'function stateSort(a,b){var x=a.session||"",y=b.session||"";return x<y?-1:x>y?1:0}' +
// トリアージ順: ❗要対応 → 📨保留 → 🟢作業中 → 🟡待機 → 💤休憩（同ランクはsession安定順）
'function rankEmp(e){return needsAttn(e)?0:isPend(e)?1:e.state==="working"?2:e.state==="waiting"?3:4}' +
'function triageSort(a,b){var r=rankEmp(a)-rankEmp(b);return r!==0?r:stateSort(a,b)}' +
'function openclawZone(){var z=el("div","openclaw");var head=el("div","openclawhead");var zl=el("span","zonepill",T("🤖 OpenClaw室","🤖 OpenClaw Room"));zl.id="ocz_lb";head.appendChild(zl);var op=el("span","ocpill",T("未接続（拡張準備中）","Not connected (expansion coming soon)"));op.id="ocz_pill";head.appendChild(op);z.appendChild(head);var st=el("div","openclawstage");["a","b","c"].forEach(function(){var c=sceneImg("crt","furn2_crt_station","");c.setAttribute("data-furn","crt");st.appendChild(c)});var sv=sceneImg("server","furn2_server_led","");sv.setAttribute("data-furn","server");st.appendChild(sv);function addBot(cls){var bot=sceneImg("ocbot "+cls,"agent_bot","");bot.setAttribute("data-decor","1");bot.onerror=function(){bot.remove()};st.appendChild(bot)}addBot("ocbot1");addBot("ocbot2");z.appendChild(st);return z}' +
/* マップの静的シェルは初回だけ生成し、以後はpill/机/キャラの差分だけを更新する。 */
'function mapScale(){var f=document.getElementById("mapframe"),m=document.getElementById("map");if(!f||!m)return;var w=f.clientWidth||window.innerWidth||374;var s=w/374;m.style.setProperty("--map-scale",String(s));m.style.transform="scale("+s+")";f.style.height=(470*s)+"px"}' +
'function mapPill(text,cls){return el("span","zonepill"+(cls?" "+cls:""),text)}' +
'function projectName(s){var a=Array.from(String(s||T("空室","Vacant")));return a.length>8?a.slice(0,7).join("")+"…":a.join("")}' +
'function mapShell(){var room=document.getElementById("room");if(!room)return null;var cards=el("section",null);cards.id="attncards";cards.setAttribute("aria-live","polite");room.appendChild(cards);var frame=el("div",null);frame.id="mapframe";var map=el("div",null);map.id="map";frame.appendChild(map);map.appendChild(el("div","mapfloor"));map.appendChild(el("div","mapface"));' +
'var win1=sceneImg("mapwindow","furn_window_wide","");win1.style.left="36px";map.appendChild(win1);var win2=sceneImg("mapwindow","furn_window_wide","");win2.style.left="230px";map.appendChild(win2);' +
'for(var ri=0;ri<3;ri++){var pr=el("section","projectroom r"+ri);pr.setAttribute("data-room",String(ri));pr.appendChild(mapPill(T("空室","Vacant"),"roompill"));var rc=el("span","mapchip roomchip");rc.id="roomOverflow"+ri;pr.appendChild(rc);map.appendChild(pr)}' +
'var main=el("section","mainzone");main.id="mainzone";main.appendChild(mapPill(T("メインオフィス 0名","Main Office 0")));main.appendChild(sceneImg("mzplant","furn2_plant_modern",""));map.appendChild(main);' +
'var lounge=el("section","loungezone");lounge.id="loungezone";lounge.appendChild(mapPill(T("ラウンジ 0名","Lounge 0")));lounge.appendChild(sceneImg("sofa","furn2_sofa_cream",""));lounge.appendChild(sceneImg("kitchenette","furn2_kitchenette_modern",""));lounge.appendChild(sceneImg("plant","furn2_plant_modern",""));map.appendChild(lounge);' +
'var meeting=el("section","meetingzone");meeting.id="meetingzone";meeting.appendChild(mapPill(T("会議コーナー 0名","Meeting Corner 0")));meeting.appendChild(sceneImg("mrug","furn2_rug_meeting",""));meeting.appendChild(sceneImg("mtable","furn2_meeting_table",""));meeting.appendChild(sceneImg("whiteboard","furn2_whiteboard_modern",""));map.appendChild(meeting);' +
'var chip=el("span","mapchip");chip.id="mainOverflow";map.appendChild(chip);var lchip=el("span","mapchip");lchip.id="loungeOverflow";map.appendChild(lchip);room.appendChild(frame);room.appendChild(openclawZone());mapScale();return map}' +
'function updateAttnCards(emps){var host=document.getElementById("attncards");if(!host)return;var sy=window.scrollY,need=(Array.isArray(emps)?emps:[]).filter(needsAttn).slice().sort(triageSort);host.innerHTML="";host.classList.toggle("on",need.length>0);' +
'need.slice(0,1).forEach(function(e){var key=attnKey(e),card=el("article","card alert attncard");card.setAttribute("data-attn-sess",key);var head=el("div","attnhead"),img=document.createElement("img");img.className="attnface";img.decoding="async";img.alt=e.disp||key;img.onerror=function(){img.onerror=null;img.src=spriteURL("generic_m")};img.src=spriteURL(spriteBase(e));head.appendChild(img);head.appendChild(el("div","attnname","❗ "+(dispCrew(e)||key)));card.appendChild(head);card.appendChild(el("div","attnq",e.question?"❓ "+e.question:T("❗ 承認が必要です","❗ Approval needed")));if(e.question&&questionOptionEntries(e).length){var options=el("div","attnoptions");appendQuestionOptions(options,e,"attnoption");card.appendChild(options)}var actions=el("div","attnactions");QUICK().forEach(function(it){var b=el("button",it.c,it.s||it.l);b.title=it.l;b.addEventListener("click",function(ev){ev.stopPropagation();send(it.t,e)});actions.appendChild(b)});var free=el("button","sub",T("✍️ 自由に","✍️ Custom"));free.addEventListener("click",function(ev){ev.stopPropagation();openSheet(e)});actions.appendChild(free);card.appendChild(actions);if(ATTN_SENT[key])card.appendChild(el("div","attnsent",T("📨 送信済み","📨 Sent")));host.appendChild(card)});' +
// 2件目以降=1行ミニカード（タップでシートへ・展開カードの縦積みでマップを追い出さない）
'need.slice(1,3).forEach(function(e){var key=attnKey(e),mini=el("article","card alert attncard attnmini");mini.setAttribute("data-attn-sess",key);mini.appendChild(el("span","attnname","❗ "+(dispCrew(e)||key)));mini.appendChild(el("span","attnq",e.question?e.question:T("承認が必要です","Approval needed")));mini.appendChild(el("span","attngo",T("回答 ›","Reply ›")));mini.addEventListener("click",function(){openSheet(e)});host.appendChild(mini)});' +
'if(need.length>3){var more=el("button","attnmore",T("ほか+"+(need.length-3)+"件","+"+(need.length-3)+" more"));more.setAttribute("aria-label",T("ほか"+(need.length-3)+"件の要対応をリストで表示","Show "+(need.length-3)+" more items that need attention in the list"));more.addEventListener("click",function(){setView("list")});host.appendChild(more)}window.scrollTo(0,sy)}' +
'function mapDesk(x,y){var d=el("div","mdesk");d.style.left=(x-17)+"px";d.style.top=y+"px";d.appendChild(sceneImg("deskimg","furn2_desk_nochair",""));return d}' +
'function mapLayout(emps){var all=Array.isArray(emps)?emps:[],rest=[],active=[],meeting=[],used={};all.forEach(function(e){if(e.state==="resting")rest.push(e);else active.push(e)});active.filter(function(e){return Number(e.minions)>0}).sort(function(a,b){return Number(b.minions)-Number(a.minions)}).slice(0,3).forEach(function(e){meeting.push(e);used[e.session||""]=1});' +
'var source=active.slice(),depts=[];all.forEach(function(e){var d=empDept(e)||T("その他","Other");if(depts.indexOf(d)<0)depts.push(d)});var projectDepts=depts.slice(0,3),rooms=[[],[],[]],desk=[];source.forEach(function(e){if(used[e.session||""]){return}var d=empDept(e)||T("その他","Other"),ix=projectDepts.indexOf(d);if(ix>=0)rooms[ix].push(e);else desk.push(e)});var roomTotals=rooms.map(function(list){return list.length});rooms.forEach(function(list){list.splice(4).forEach(function(e){desk.push(e)})});active.filter(function(e){return used[e.session||""]}).slice(3).forEach(function(e){desk.push(e)});' +
'return {all:all,rest:rest,active:active,meeting:meeting,rooms:rooms,roomTotals:roomTotals,projectDepts:projectDepts,desks:desk}}' +
/* v5: アンカー54pxピッチ=キャラ48px+名札50pxが重ならない間隔。各プロジェクト部屋は最大4名+あふれチップ */
'var PROJECT_ANCHORS=[[6,26],[60,26],[6,60],[60,60]],MAIN_DESK_ANCHORS=[[60,196],[187,196],[314,196],[60,268],[187,268],[314,268]],MAIN_STAND_ANCHORS=[[36,296],[122,296],[208,296],[294,296]],LOUNGE_ANCHORS=[[76,448],[152,448]],MEETING_ANCHORS=[[205,448],[336,448],[270,462]];' +
'function keyOf(e,i){return String((e&&e.session)||("employee-"+i))}' +
'function mcharSource(rec){var n=rec.base;if(rec.walking){if(WALKF[rec.base]===2)n=rec.base+(WALKBIT?"_walk2":"_walk");else n=WALKBIT?rec.base:rec.base+"_walk"}return spriteURL(n)}' +
'function mapChar(e,left,top,zone,index){var key=keyOf(e,index),b=el("button","mchar");b.setAttribute("data-sess",key);b.setAttribute("data-state",e.state||"");b.setAttribute("aria-label",e.disp||key);b.tabIndex=0;var img=document.createElement("img");img.className="mavatar";img.decoding="async";img.alt=e.disp||key;img.onerror=function(){img.onerror=null;img.src=spriteURL("generic_m")};var name=el("span","mname",e.disp||key),typing=el("span","typing","⌨"),state=el("span","mstate"),say=el("span","msay");b.appendChild(img);b.appendChild(name);b.appendChild(typing);b.appendChild(state);b.appendChild(say);b.addEventListener("click",function(){openSheet(e)});var rec={root:b,img:img,name:name,typing:typing,state:state,say:say,e:e,base:spriteBase(e),walking:false,key:key,zone:zone,x:left,y:top,moveToken:0,moveTimer:null};MCHARS.set(key,rec);b.style.left=left+"px";b.style.top=top+"px";img.src=spriteURL(rec.base);document.getElementById("map").appendChild(b);return rec}' +
'function moveMchar(rec,left,top){var dx=left-rec.x;rec.zone=rec.zone||"";if(Math.abs(dx)>1)rec.root.classList.toggle("faceflip",dx<0);if(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches){rec.walking=false;rec.root.classList.remove("walking");rec.x=left;rec.y=top;rec.root.style.left=left+"px";rec.root.style.top=top+"px";rec.img.src=spriteURL(rec.base);return}if(Math.abs(left-rec.x)>1||Math.abs(top-rec.y)>1){if(rec.moveTimer)clearTimeout(rec.moveTimer);rec.walking=true;rec.root.classList.add("walking");rec.img.src=mcharSource(rec);rec.x=left;rec.y=top;rec.root.style.left=left+"px";rec.root.style.top=top+"px";var tok=++rec.moveToken;rec.moveTimer=setTimeout(function(){if(MCHARS.get(rec.key)!==rec||rec.moveToken!==tok)return;rec.walking=false;rec.root.classList.remove("walking");rec.img.src=spriteURL(rec.base);rec.moveTimer=null},3460)}else{rec.x=left;rec.y=top;rec.root.style.left=left+"px";rec.root.style.top=top+"px";if(!rec.walking)rec.img.src=spriteURL(rec.base)}}' +
'function setMchar(rec,e,left,top,zone){rec.base=spriteBase(e);rec.e=e;rec.root.setAttribute("data-state",e.state||"");if(needsAttn(e))rec.root.setAttribute("data-alert","1");else rec.root.removeAttribute("data-alert");rec.root.classList.toggle("pending",isPend(e));rec.root.classList.toggle("atdesk",String(zone||"").indexOf("desk")===0);rec.name.textContent=dispCrew(e)||rec.key;rec.typing.style.display=e.state==="working"?"block":"";rec.state.textContent=needsAttn(e)?"❗":e.state==="resting"?"💤":"";rec.zone=zone;rec.root.title=(e.verb||"")+(e.target?(" "+e.target):"");moveMchar(rec,left,top)}' +
'function updateDeskState(people){var by={};people.forEach(function(e){by[e.session||""]=e});var ds=document.querySelectorAll("#map .mdesk");for(var i=0;i<ds.length;i++){var e=by[ds[i].getAttribute("data-desk-sess")||""];ds[i].classList.toggle("is-working",!!e&&e.state==="working")}}' +
'function updateMapScene(office){var map=document.getElementById("map")||mapShell();if(!map)return;var emps=filterEmps(officeAgents(office)),lay=mapLayout(emps),want={};var rooms=map.querySelectorAll(".projectroom");for(var ri=0;ri<3;ri++){var rd=lay.projectDepts[ri]||T("空室","Vacant"),tot=(lay.roomTotals&&lay.roomTotals[ri])||lay.rooms[ri].length,p=rooms[ri].querySelector(".roompill");p.textContent=projectName(rd)+" "+tot+T("名","");rooms[ri].setAttribute("data-dept",rd);var rc=document.getElementById("roomOverflow"+ri);if(rc){if(tot>4){rc.textContent=T("＋","+")+(tot-4)+T("名","");rc.classList.add("on")}else rc.classList.remove("on")}}' +
'var main=map.querySelector("#mainzone .zonepill"),lp=map.querySelector("#loungezone .zonepill"),mp=map.querySelector("#meetingzone .zonepill");main.textContent=T("メインオフィス ","Main Office ")+lay.desks.length+T("名","");lp.textContent=T("ラウンジ ","Lounge ")+lay.rest.length+T("名","");mp.textContent=T("会議コーナー ","Meeting Corner ")+lay.meeting.length+T("名","");' +
'var deskPeople=lay.desks.slice(0,6),usedDesk={};for(var di=0;di<6;di++){var d=map.querySelector(".mdesk[data-desk-index=\\\""+di+"\\\"]");if(!d){d=mapDesk(MAIN_DESK_ANCHORS[di][0],MAIN_DESK_ANCHORS[di][1]);d.setAttribute("data-desk-index",String(di));map.appendChild(d)}var de=deskPeople[di];d.setAttribute("data-desk-sess",de?keyOf(de,di):"");if(de)usedDesk[keyOf(de,di)]=1}' +
'var placements=[];lay.rooms.forEach(function(list,ri){list.slice(0,4).forEach(function(e,i){var a=PROJECT_ANCHORS[i],x=10+ri*120+a[0],y=22+a[1];placements.push({e:e,x:x,y:y,zone:"project"+ri})})});deskPeople.forEach(function(e,i){var a=MAIN_DESK_ANCHORS[i];placements.push({e:e,x:a[0]-24,y:a[1]-42,zone:"desk"+i})});var extra=lay.desks.slice(6);extra.forEach(function(e,i){var a=MAIN_STAND_ANCHORS[i%4],off=Math.floor(i/4)*10;placements.push({e:e,x:a[0]+off,y:a[1]-42,zone:"stand"+i})});lay.rest.forEach(function(e,i){var a=LOUNGE_ANCHORS[i%2],off=Math.floor(i/2)*11,dir=i%2?1:-1;placements.push({e:e,x:a[0]-24+dir*off,y:a[1]-42,zone:"lounge"})});lay.meeting.forEach(function(e,i){var a=MEETING_ANCHORS[i];placements.push({e:e,x:a[0]-24,y:a[1]-42,zone:"meeting"})});' +
'var chip=document.getElementById("mainOverflow");if(lay.desks.length>8){chip.textContent=T("＋","+")+(lay.desks.length-8)+T("名","");chip.classList.add("on");chip.style.left="292px";chip.style.top="300px"}else{chip.classList.remove("on")}var lchip=document.getElementById("loungeOverflow");if(lay.rest.length>2){lchip.textContent=T("💤＋","💤+")+(lay.rest.length-2)+T("名","");lchip.classList.add("on");lchip.style.left="74px";lchip.style.top="438px"}else{lchip.classList.remove("on")}' +
'placements.forEach(function(pos,i){var k=keyOf(pos.e,i),rec=MCHARS.get(k);want[k]=1;if(!rec){rec=mapChar(pos.e,pos.x,pos.y,pos.zone,i);if(!(window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches)){rec.y=470;rec.root.style.top="470px"}}setMchar(rec,pos.e,pos.x,pos.y,pos.zone)});' +
'MCHARS.forEach(function(rec,k){if(!want[k]){if(rec.root.parentNode)rec.root.parentNode.removeChild(rec.root);if(rec.moveTimer)clearTimeout(rec.moveTimer);MCHARS.delete(k)}});updateDeskState(deskPeople);updateAttnCards(emps);mapScale();if(!emps.length){if(!document.getElementById("mapEmpty")){var empty=el("div","empty",T("🌙 いま出勤中の社員はいません","🌙 No one is in the office right now"));empty.id="mapEmpty";document.getElementById("room").appendChild(empty)}}else{var old=document.getElementById("mapEmpty");if(old)old.remove()}}' +
// R76: OpenClaw室のデータ駆動。実メンバーが居ればロブスターbotを並べて名前・状態を出し、
// タップで通常の社員と同じシート（＝oc-宛の指示もそのまま送れる）。0名のときだけ巡回ロボの飾り。
'function paintOpenclaw(office){var z=document.querySelector(".openclaw");if(!z)return;'+
'var stage=z.querySelector(".openclawstage"),pill=document.getElementById("ocz_pill");'+
'var ex=(officeAgents(office)||[]).filter(function(e){return e&&e.external});'+
'if(pill)pill.textContent=ex.length?(ex.length+T("名 接続中"," connected")):T("未接続","Not connected");'+
'stage.querySelectorAll("[data-decor]").forEach(function(n){n.style.display=ex.length?"none":""});'+
// 帯の横幅(約366px)は有限。人が増えたら家具の側が譲る＝はみ出さない
'var sv=stage.querySelector(\'[data-furn="server"]\');if(sv)sv.style.display=ex.length>=2?"none":"";'+
'var crts=stage.querySelectorAll(\'[data-furn="crt"]\');'+
'var keep=Math.max(0,3-Math.max(0,ex.length-1));'+
'for(var ci=0;ci<crts.length;ci++)crts[ci].style.display=ci<keep?"":"none";'+
// 既存ノードは属性走査で引く（セレクタ組み立て＝エスケープ事故の温床なので使わない）
'var have={};stage.querySelectorAll(".ocmem").forEach(function(n){have[n.getAttribute("data-sess")||""]=n});'+
'var seen={};ex.slice(0,4).forEach(function(e){var key=e.session||e.disp||"";seen[key]=1;'+
'var b=have[key];'+
'if(!b){b=el("button","ocmem");b.type="button";b.setAttribute("data-sess",key);'+
'var img=document.createElement("img");img.className="ocavatar";img.decoding="async";'+
'img.onerror=function(){img.onerror=null;img.src=spriteURL("generic_m")};b.appendChild(img);'+
'b.appendChild(el("span","ocname",""));b.appendChild(el("span","ocst",""));stage.appendChild(b)}'+
'var im=b.querySelector("img"),src=spriteURL(spriteBase(e));if(im.getAttribute("src")!==src)im.src=src;'+
'im.alt=dispCrew(e)||key;b.querySelector(".ocname").textContent=dispCrew(e)||key;'+
'b.querySelector(".ocst").textContent=needsAttn(e)?"❗":e.state==="resting"?"💤":e.state==="working"?"⌨":"";'+
'b.title=(e.verb||"");b.onclick=function(){openSheet(e)}});'+
'stage.querySelectorAll(".ocmem").forEach(function(n){if(!seen[n.getAttribute("data-sess")])n.remove()})}'+
// R77: スマホも3Dオフィス（デスクトップと同じ IsoScene）。3Dが起動していない
// 端末（WebGL不可・モジュール未取得）では従来の2Dマップへ自動で退避する。
// R77: 3Dの器。#attncards（❗トリアージ）は据え置き＝スマホの主目的を落とさない。
'function sceneShell3D(){var room=document.getElementById("room");if(!room)return null;'+
'if(!document.getElementById("attncards")){var cards=el("section",null);cards.id="attncards";cards.setAttribute("aria-live","polite");room.appendChild(cards)}'+
'var wrap=el("div",null);wrap.id="scene3dwrap";var host=el("div",null);host.id="scene3d";var plates=el("div",null);plates.id="plates";wrap.appendChild(host);wrap.appendChild(plates);room.appendChild(wrap);'+
'host.addEventListener("click",function(ev){if(!window.__scene3d)return;var r=host.getBoundingClientRect();var id=window.__scene3d.pick(ev.clientX-r.left,ev.clientY-r.top);if(!id)return;var e=empOfAgent(id);if(e)openSheet(e)});'+
'var s=document.createElement("script");s.type="module";s.src="/ui/pwa/boot3d.js";s.onerror=function(){if(!document.getElementById("map"))mapShell()};document.body.appendChild(s);return host}'+
// シーンのagent(id=projectId or session) から /status の社員を引く単一の対応点
'function empOfAgent(id){if(!window.__scene3d)return null;var ags=window.__scene3d.agents()||[];var ag=null;for(var i=0;i<ags.length;i++)if(ags[i].id===id){ag=ags[i];break}var sess=ag?ag.session:id;var list=officeAgents(LAST_OFFICE)||[];for(var j=0;j<list.length;j++)if(list[j]&&list[j].session===sess)return list[j];return null}'+
// 名札は「❗のある社員」と「選択中」だけ＝390pxで9枚出すと重なって読めない
'function paintPlates(){var layer=document.getElementById("plates");if(!layer||!window.__scene3d)return;var ags=window.__scene3d.agents()||[];var seen={};for(var i=0;i<ags.length;i++){(function(a){var e=empOfAgent(a.id);var attn=e?needsAttn(e):false;var sel=!!(SEL&&e&&SEL.session===e.session);if(!attn&&!sel)return;var p=window.__scene3d.project(a.id);if(!p)return;seen[a.id]=1;var n=null,all=layer.querySelectorAll(".plate");for(var k=0;k<all.length;k++)if(all[k].getAttribute("data-plate")===a.id){n=all[k];break}if(!n){n=el("button","plate");n.type="button";n.setAttribute("data-plate",a.id);n.addEventListener("click",function(){var cur=empOfAgent(a.id);if(cur)openSheet(cur)});layer.appendChild(n)}n.className="plate"+(attn?" attn":"")+(sel?" sel":"");n.textContent=(attn?"❗ ":"")+(e?dispCrew(e):a.name||a.id);n.style.left=Math.round(p.left)+"px";n.style.top=Math.round(p.top-30)+"px"})(ags[i])}var nodes=layer.querySelectorAll(".plate");for(var q=nodes.length-1;q>=0;q--)if(!seen[nodes[q].getAttribute("data-plate")])nodes[q].remove()}'+
'window.__paintPlates=paintPlates;'+
// 3Dモジュールは非同期で載る。載った瞬間に**シーンだけ**描き直す。
// ここで dispatch()（全再描画）を呼ぶと、設定シート等の開いているDOMが差し替わり
// 直前のクリックが detach 空振りになる（R67でデスクトップが踏んだのと同じ罠）。
'document.addEventListener("scene3d-ready",function(){if(LAST_OFFICE&&VIEW==="office")renderScene(LAST_OFFICE)});'+
'function renderScene(office){var room=document.getElementById("room");if(room)room.querySelectorAll(".empty").forEach(function(n){n.remove()});if(!document.getElementById("scene3d"))sceneShell3D();if(window.__scene3d&&window.__scene3d.ready){window.__scene3d.apply(office);paintPlates();var m=document.getElementById("mapframe");if(m)m.style.display="none";var oc0=room?room.querySelector(".openclaw"):null;if(oc0)oc0.style.display="none";return}'+
'if(!document.getElementById("map"))mapShell();var oc=room?room.querySelector(".openclaw"):null;if(oc)oc.style.display=featOn("openclaw")?"":"none";updateMapScene(office);paintOpenclaw(office)}' +
'window.addEventListener("resize",function(){mapScale()});' +
// 歩行キャラ(固定通路): 稼働/待機の社員が全員(最大6)同時に歩く。速度=活動の鮮度
//   (age 0秒→8秒で横断/10分以上→24秒でのんびり)。⚡=30秒以内に動いた社員。
//   差分更新(追加/削除/速度変更のみ)＝既存walkerのアニメを無闇に再起動しない。
'var MCHARS=new Map();var WALKBIT=0;var WALK_IV=setInterval(function(){WALKBIT=WALKBIT?0:1;MCHARS.forEach(function(rec){if(!rec||!rec.walking)return;var src=mcharSource(rec);if(rec.img.src.indexOf(src)===-1)rec.img.src=src})},220);' +
// 吹き出し会話(R23.5): 素材はredaction通過後も生きる動作ログ行と定型flavorのみ（lastSaid/targetは常に空＝掟）。
// sceneSigに混ぜず独立6秒ローテ＝5秒pollの再描画ゲートや歩行アニメを乱さない。決定論(tick+hash)・同時最大2体。
'function MSAY(){return {workBusy:[T("集中モード…！","Deep focus mode…!"),T("エラーと格闘中…","Wrestling with an error…"),T("あと少しでコミットできます","Almost ready to commit"),T("今日中に片付けたい…！","Hoping to wrap this up today…!")],workCalm:[T("コツコツ進めてます","Making steady progress"),T("順調です、この調子","Going well, keeping the pace"),T("このへん整理しておきますね","Tidying this part up"),T("ドキュメントも書いておこう","Should write the docs too")],waiting:[T("次の指示待ちです","Waiting for the next instruction"),T("いつでもいけます","Ready when you are"),T("指示くださいな","Give me something to do")],resting:[T("ちょっと休憩☕","Quick break ☕"),T("ひと息つきましょう","Taking a breather"),T("このソファ落ち着く〜","This sofa is so comfy")]}}' +
'function msayLine(e,tick){var feed=(e&&e.feed)||[];var log="";for(var i=0;i<feed.length;i++){var f=String(feed[i]||"");if(f&&f.indexOf("💬")!==0){log=f;break}}' +
'var h=wHash(String((e&&e.session)||""));if(log&&(tick+h)%3!==0)return log.length>26?log.slice(0,26)+"…":log;' +
'var pool=e.state==="working"?(((e.age||0)<=90)?MSAY().workBusy:MSAY().workCalm):e.state==="waiting"?MSAY().waiting:MSAY().resting;return pool[(tick+h)%pool.length]}' +
'var SAYTICK=0;var SAY_IV=setInterval(function(){if(document.hidden)return;SAYTICK++;var n=0;MCHARS.forEach(function(rec){if(!rec||!rec.say)return;var e=rec.e;var show=!!e&&!rec.walking&&!needsAttn(e)&&((SAYTICK+wHash(rec.key))%5<1)&&n<2;if(show){var line=msayLine(e,SAYTICK);if(line){rec.say.textContent=line;rec.say.classList.add("on");rec.say.classList.toggle("below",rec.y<30);rec.say.classList.toggle("edgeL",rec.x<34);rec.say.classList.toggle("edgeR",rec.x>292);n++;return}}rec.say.classList.remove("on")})},6000);' +
'function wHash(s){var h=0;for(var i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))>>>0;return h}' +
// ヘッダー: 右肩=いま動いているAI合計(作業中セッション+部下エージェント)・統計バー=意味をラベルで明示
// R51: rosterでは sessions[] が実セッションの内訳＝稼働数/出勤数を正直に数える（旧employeesは[e]で同値）
'function buildHeader(emps){var w=0,wa=0,rs=0,al=0,mn=0,pd=0,tot=0;emps.forEach(function(e){if(needsAttn(e))al++;if(isPend(e))pd++;mn+=(e.minions||0);var ms=(Array.isArray(e.sessions)&&e.sessions.length)?e.sessions:[e];tot+=ms.length;ms.forEach(function(m){if(m.state==="working")w++;else if(m.state==="waiting")wa++;else if(m.state==="resting")rs++})});' +
'var t=document.getElementById("total");t.innerHTML="";t.appendChild(document.createTextNode("🤖 "));t.appendChild(el("b",null,String(w+mn)));t.appendChild(el("small",null,T("体 稼働中","agents active")));t.title=T("作業中セッション"+w+" + 部下エージェント"+mn,"Working sessions "+w+" + subagents "+mn);' +
'var sb=document.getElementById("statbar");sb.innerHTML="";function mk(cls,ic,lb,n){var s=el("span","stat"+(cls?" "+cls:""));s.appendChild(document.createTextNode(ic+" "+lb+" "));s.appendChild(el("b",null,String(n)));sb.appendChild(s)}' +
'mk("","🟢",T("作業中","Working"),w);mk("","🟡",T("指示待ち","Waiting"),wa);if(al)mk("attn","❗",T("要対応","Attention"),al);if(pd)mk("","📨",T("配達待ち","Pending"),pd);mk("","👥",T("部下","Subagents"),mn);if(rs)mk("","💤",T("休憩","Break"),rs);mk("","🏢",T("出勤","Present"),tot)}' +
'function dispatch(){var off=LAST_OFFICE;var emps=officeAgents(off);var sig=sceneSig(emps);if(sig===LAST_SIG&&VIEW===LAST_VIEW)return;var sy=window.scrollY;LAST_SIG=sig;LAST_VIEW=VIEW;buildHeader(emps);buildDeptbar(emps);var room=document.getElementById("room"),list=document.getElementById("list");var to=document.getElementById("tb_office"),tl=document.getElementById("tb_list");if(to)to.classList.toggle("on",VIEW==="office");if(tl)tl.classList.toggle("on",VIEW!=="office");if(VIEW==="office"){room.classList.remove("hidden");list.classList.add("hidden");renderScene(off)}else{list.classList.remove("hidden");room.classList.add("hidden");renderList(off)}window.scrollTo(0,sy)}' +
'function setView(v){if(VIEW!==v)playSE("cursor");VIEW=v;localStorage.setItem("aioffice.view",VIEW);dispatch()}' +
'function poll(){var cred=getCred();if(!cred)return;' +
'fetch("/status",{headers:{"Authorization":"Bearer "+cred.t}}).then(function(r){' +
'if(r.status===401){if(POLL_IV){clearInterval(POLL_IV);POLL_IV=null}conn(false,T("未認証・再ペアリングを","Unauthorized — pair this device again"));document.body.classList.remove("off");document.getElementById("app").classList.add("hidden");document.getElementById("tabbar").classList.add("hidden");document.getElementById("setup").classList.remove("hidden");return null}return r.json()}).then(function(d){' +
'if(!d)return;var office={};try{office=JSON.parse(d.json||"{}")}catch(_){}' +
'conn(true,T("✓ 接続","✓ Connected"));LAST_OFFICE=office;applyOfficeLang(office&&office.lang);' +
// R51: 鮮度の正直表示（ts=DO書込時刻・agentSeenAgo=relay_agent最終/syncからの秒）→ staleバナー。
// applyOfficeLang より後に呼ぶ（言語切替時の applyLangChrome が #banner を既定文言へ戻すため）
'updateStale(typeof d.ts==="number"?d.ts:0,typeof d.agentSeenAgo==="number"?d.agentSeenAgo:null);' +
'saveOfficeCache(office);var ags=officeAgents(office);checkAttnEdge(ags);checkAnswered(ags);checkDelivery(ags);dispatch();refreshAges(ags)}).catch(function(){conn(false,T("オフライン","Offline"))})}' +
'document.addEventListener("keydown",function(ev){if(ev.key!=="Escape")return;if(document.getElementById("logwrap").classList.contains("open"))closeLog();else if(document.getElementById("setwrap").classList.contains("open"))closeSettings();else closeSheet()});' +
// R51: 基本ポーリング 5秒→20秒（CF無料枠対策）。谷間は復帰時/Push受信/送信直後の即時pollで埋める。
'document.addEventListener("visibilitychange",function(){if(document.hidden){if(POLL_IV){clearInterval(POLL_IV);POLL_IV=null}}else if(!POLL_IV&&getCred()&&!document.getElementById("app").classList.contains("hidden")){poll();POLL_IV=setInterval(poll,20000)}});' +
'function pushSupported(){return "serviceWorker" in navigator&&"PushManager" in window&&"Notification" in window}' +
'function setNtog(on){NTOG_STATE=!!on;var ic=document.getElementById("ntogic"),lb=document.getElementById("ntoglb"),b=document.getElementById("ntog");if(ic)ic.textContent=on?"🔔":"🔕";if(lb)lb.textContent=on?T("通知ON","Alerts ON"):T("通知OFF","Alerts OFF");if(b){b.classList.toggle("on",!!on);b.title=on?T("タップで通知OFF","Tap to turn alerts off"):T("❗承認/質問まちをプッシュ通知","Push alerts for ❗approvals and questions")}}' +
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
'function boot(){applyPrefs();applyLangChrome();credFromHash();preloadCriticalSprites();if(!getCred()){document.getElementById("setup").classList.remove("hidden");return}' +
'document.getElementById("app").classList.remove("hidden");document.getElementById("tabbar").classList.remove("hidden");var cached=loadOfficeCache();if(cached){LAST_OFFICE=cached;applyOfficeLang(cached&&cached.lang);ATTN_SESSIONS=attnSessionSet(officeAgents(cached));dispatch();refreshAges(officeAgents(cached))}else{var r0=document.getElementById("room");if(r0)r0.appendChild(el("div","empty",T("接続中…","Connecting…")))}poll();POLL_IV=setInterval(poll,20000);' +
// R51: SWからのpush到着通知（aioffice-poll）で即時poll＝20秒間隔でも❗を待たせない
'if("serviceWorker" in navigator){navigator.serviceWorker.addEventListener("message",function(ev){if(ev&&ev.data&&ev.data.type==="aioffice-poll"&&!document.hidden)poll()});navigator.serviceWorker.register("/app/sw.js").then(function(){refreshNtog()}).catch(function(){})}}' +
'boot();' +
'</script></body></html>';
