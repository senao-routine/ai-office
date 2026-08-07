// R79-7: WebSocket経路のE2E（relay_e2e.sh から wrangler dev に対して実行・node組込WebSocket使用）。
//   検査: 認証マトリクス（フル○/限定POST×/偽×）・接続直後status・"p"→"P"（auto-response配線）・
//   {"t":"status"}要求応答・putStatus扇形配信・enqueue→agent wake・/sync の appOnline。
// 退避経路（HTTP /sync /pull /ack /status）のアサーションは relay_e2e.sh 本体が持ち続ける＝
// このファイルはWSの「追加」だけを検査し、既存HTTP検査を1つも置き換えない。
// 使い方: node ws_e2e.mjs <base_url> <full_token> <post_token> [macmini_token]
const [base, TOKEN, POST_TOKEN, MINI_TOKEN] = process.argv.slice(2);
if (!base || !TOKEN) {
  console.error("使い方: node ws_e2e.mjs <base_url> <full_token> <post_token> [macmini_token]");
  process.exit(2);
}
const WS_BASE = base.replace(/^http/, "ws");
let ng = 0;
const ok = (m) => console.log(`  ✓ ${m}`);
const bad = (m) => { console.log(`  ✗ ${m}`); ng += 1; };

const b64u = (s) => Buffer.from(s, "utf-8").toString("base64url");
const subproto = (token) => ["aioffice.v1", "bearer." + b64u(token)];

function connect(token, role, timeoutMs = 5000, extra = "") {
  return new Promise((resolve) => {
    const ws = new WebSocket(`${WS_BASE}/ws?role=${role}${extra}`, subproto(token));
    const frames = [];
    const waiters = [];
    const t = setTimeout(() => resolve({ ok: false, error: "open timeout" }), timeoutMs);
    ws.onmessage = (ev) => {
      const w = waiters.shift();
      if (w) { clearTimeout(w.t); w.resolve(String(ev.data)); }
      else frames.push(String(ev.data));
    };
    ws.onopen = () => { clearTimeout(t); resolve({ ok: true, ws, frames, waiters }); };
    ws.onerror = () => { clearTimeout(t); resolve({ ok: false, error: "refused" }); };
  });
}

// putStatus の扇形配信で積まれた未読フレームを捨てる。
// これをせずに「要求→応答」を検査すると、**先に届いていた push フレームを応答と誤認する**
// （R80で実際に踏んだ: 使用量テストの /sync が積んだ古い status を agentOnline 検査が拾った）。
function drain(conn) {
  const n = conn.frames.length;
  conn.frames.length = 0;
  return n;
}

// 次の1フレームを待つ（先着はconnectのframesに積まれている）
function nextFrame(conn, timeoutMs = 5000) {
  if (conn.frames.length) return Promise.resolve(conn.frames.shift());
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve(null), timeoutMs);
    conn.waiters.push({ resolve, t });
  });
}

async function http(method, path, body, token = TOKEN) {
  const r = await fetch(base + path, {
    method,
    headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}

// ---- 認証マトリクス（既存403マトリクスのWS面） ----
{
  const c = await connect("wrong-token", "app");
  c.ok ? bad("WS 偽トークンが接続できてしまった") : ok("WS 偽トークン拒否");
  if (c.ok) c.ws.close();
}
if (POST_TOKEN) {
  const c = await connect(POST_TOKEN, "app");
  c.ok ? bad("WS 限定POSTトークンが接続できてしまった") : ok("WS 限定POSTトークン拒否 (403)");
  if (c.ok) c.ws.close();
}

// ---- フルトークン: 接続→接続直後status→p/P→status要求→扇形配信 ----
const app = await connect(TOKEN, "app");
if (!app.ok) {
  bad(`WS フルトークン接続失敗: ${app.error}`);
} else {
  ok("WS フルトークン接続 (サブプロトコル認証)");
  if (app.ws.protocol !== "aioffice.v1") bad(`サブプロトコルechoが不正: "${app.ws.protocol}"`);
  else ok("サブプロトコル echo=aioffice.v1");

  const hello = await nextFrame(app);
  let helloOk = false;
  try { helloOk = JSON.parse(hello).t === "status"; } catch { /* fallthrough */ }
  helloOk ? ok("接続直後に status スナップショット受信（再同期）")
          : bad(`接続直後のstatusが来ない: ${hello}`);

  app.ws.send("p");
  const pong = await nextFrame(app);
  pong === "P" ? ok('keepalive "p"→"P"（auto-response配線=課金ゼロ経路）')
               : bad(`"p"の応答が"P"でない: ${pong}`);

  drain(app);
  app.ws.send('{"t":"status"}');
  const st = await nextFrame(app);
  let stOk = false;
  try { const d = JSON.parse(st); stOk = d.t === "status" && "agentSeenAgo" in d; } catch { /* fallthrough */ }
  stOk ? ok('{"t":"status"} 要求→status応答') : bad(`status要求の応答が不正: ${st}`);

  // 扇形配信: Mac役が /sync で office を push → 開いているappソケットへ即時フレーム
  const marker = "ws-fan-" + Date.now();
  const sync1 = await http("POST", "/sync", {
    office: { employees: [{ session: "e2e-ws-0001", disp: marker, state: "working", verb: "WS検証中" }] },
  });
  if (sync1.status !== 200) bad(`/sync が ${sync1.status}`);
  const fan = await nextFrame(app);
  let fanOk = false;
  try { const d = JSON.parse(fan); fanOk = d.t === "status" && String(d.json).includes(marker) && d.agentSeenAgo === 0; } catch { /* fallthrough */ }
  fanOk ? ok("putStatus 扇形配信（/sync→WS push・agentSeenAgo=0）")
        : bad(`扇形配信フレームが不正: ${String(fan).slice(0, 120)}`);

  // R80: 使用量カウンタ（無料枠に対する今日の書込行数）が sync 応答に載り、増えていく。
  // これが Mac 側の自動減速（scan間隔を伸ばす）の入力になる。
  const u1 = await http("POST", "/sync", { office: { employees: [] } });
  const u2 = await http("POST", "/sync", { office: { employees: [{ session: "u-2" }] } });
  const g1 = u1.body.usage, g2 = u2.body.usage;
  if (g1 && g2 && g2.rows > g1.rows && g2.limit === 100000 && typeof g2.level === "number") {
    ok(`使用量カウンタが増える（rows ${g1.rows}→${g2.rows} / level ${g2.level}）`);
  } else {
    bad(`usage が返らない/増えない: ${JSON.stringify(g1)} → ${JSON.stringify(g2)}`);
  }

  // /sync の appOnline: appソケットが開いている間は true / appSeenAgo=0（リクエスト0円の在席）
  const sync2 = await http("POST", "/sync", {});
  if (sync2.body.appOnline === true && sync2.body.appSeenAgo === 0) {
    ok("/sync appOnline=true・appSeenAgo=0（WS在席のメモリ判定）");
  } else {
    bad(`/sync 在席判定が不正: ${JSON.stringify(sync2.body)}`);
  }

  // enqueue→agent wake: agent役ソケットに {"t":"wake"} が届く（R79-8のMac側の受信口）
  const agent = await connect(TOKEN, "agent");
  if (!agent.ok) {
    bad("WS agent役の接続失敗");
  } else {
    // 署名付き封筒はHTTP /instructへ（封筒検証はMac側の掟のまま）。形式だけ通ればenqueueされる
    const env0 = {
      v: 1, device_id: "d_0123456789ab", session: "e2e-ws-0001", text: "wake試験",
      ts: Math.floor(Date.now() / 1000), nonce: "00112233445566778899aabbccddeeff",
      alg: "HS256", sig: "0".repeat(64),
    };
    const inst = await http("POST", "/instruct", env0);
    if (inst.status !== 200) bad(`/instruct が ${inst.status}: ${JSON.stringify(inst.body)}`);
    const wake = await nextFrame(agent);
    let wakeOk = false;
    try { wakeOk = JSON.parse(wake).t === "wake"; } catch { /* fallthrough */ }
    wakeOk ? ok("enqueue→agentソケットへ wake（Mac即時起床の配線）")
           : bad(`wakeフレームが不正: ${wake}`);
    // R79-8.1: Mac(agent)がWS在席の間、statusフレームは agentOnline:true を運ぶ
    // ＝静穏時（sync間隔240s）でもPWAが偽staleバナーを出さない根拠
    drain(app);                       // ↑の/instruct・/syncが積んだpushを捨ててから要求する
    app.ws.send('{"t":"status"}');
    const st2 = await nextFrame(app);
    let onlineOk = false;
    try { onlineOk = JSON.parse(st2).agentOnline === true; } catch { /* fallthrough */ }
    onlineOk ? ok("status に agentOnline=true（Mac WS在席＝偽stale防止）")
             : bad(`agentOnline が立たない: ${String(st2).slice(0, 100)}`);
    // 後始末: 積んだ試験封筒を ack で掃除（後続のHTTP検査を汚さない）
    const pulled = await http("GET", "/pull");
    const ids = (pulled.body.items || [])
      .filter((it) => it.session === "e2e-ws-0001").map((it) => it.id);
    if (ids.length) await http("POST", "/ack", { ids });
    agent.ws.close();
  }
  app.ws.close();

  // 切断後: appOnline が false へ戻る（在席が接続数に正しく追随）
  await new Promise((r) => setTimeout(r, 300));
  const sync3 = await http("POST", "/sync", {});
  sync3.body.appOnline === false
    ? ok("切断後 /sync appOnline=false（在席が接続数へ追随）")
    : bad(`切断後も appOnline=true のまま: ${JSON.stringify(sync3.body)}`);
}

// ---- R79-9: miniトークンのWS（site=macmini のみ・sync push＝openclaw_push のWS経路） ----
if (MINI_TOKEN) {
  const deny = await connect(MINI_TOKEN, "agent");   // site無し=mac → 403
  deny.ok ? bad("miniトークンが site=mac の /ws に接続できてしまった")
          : ok("miniトークン /ws (site=mac) 拒否");
  if (deny.ok) deny.ws.close();
  const mini = await connect(MINI_TOKEN, "agent", 5000, "&site=macmini");
  if (!mini.ok) {
    bad(`miniトークン /ws?site=macmini 接続失敗: ${mini.error}`);
  } else {
    ok("miniトークン /ws?site=macmini 接続");
    // openclaw_push のWS経路: 契約v1を {"t":"sync"} で送る → HTTP GET /status?site=macmini に反映
    const marker = "ws-mini-" + Date.now();
    mini.ws.send(JSON.stringify({ t: "sync", ackIds: [], wantOpenclaw: false,
      office: { v: 1, site: "macmini", generatedAt: 0,
                agents: [{ id: marker, name: "WS Push検査", state: "working" }] } }));
    const rep = await nextFrame(mini);
    let repOk = false;
    try { repOk = JSON.parse(rep).ok === true; } catch { /* fallthrough */ }
    repOk ? ok("mini WS sync 応答ok") : bad(`mini WS syncの応答が不正: ${rep}`);
    // 読み返しはフルトークン（miniトークンは GET /status 不可＝既存403マトリクスの仕様）
    const st = await fetch(base + "/status?site=macmini", {
      headers: { Authorization: "Bearer " + TOKEN } }).then((r) => r.json());
    String(st.json || "").includes(marker)
      ? ok("mini WS sync → /status?site=macmini 反映（openclaw_pushのWS経路）")
      : bad(`mini WS syncが反映されない: ${String(st.json).slice(0, 120)}`);
    mini.ws.close();
  }
}

if (ng) { console.log(`✗ WS E2E ${ng}件失敗`); process.exit(1); }
console.log("✓ WS E2E 合格");
