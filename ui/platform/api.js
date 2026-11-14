// サーバーとの唯一の通信口。
// 掟: すべての fetch に X-Office-Local: 1 を付ける（GET も）。
// これが無いと CSRF ガード（server/office_server.py の _csrf_ok）に 403 で弾かれる。
const HEADERS = { "X-Office-Local": "1" };

export class ApiError extends Error {
  constructor(message, status, path) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export async function api(path, { method = "GET", body = null, signal = null, keepalive = false } = {}) {
  const opts = { method, headers: { ...HEADERS }, signal, keepalive };
  if (body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      throw new ApiError(`JSONを解釈できません (${res.status})`, res.status, path);
    }
  }
  if (!res.ok) {
    throw new ApiError((data && data.error) || `HTTP ${res.status}`, res.status, path);
  }
  return data;
}

export const getOffice = (signal) => api("/api/office", { signal });
export const getDigest = (signal, day = null, since = null) => {
  const query = new URLSearchParams();
  if (day !== null) query.set("day", day);
  if (since !== null) query.set("since", since);
  return api(`/api/digest${query.size ? `?${query}` : ""}`, { signal });
};
export const getTimeline = (since, signal) =>
  api(`/api/timeline?since=${encodeURIComponent(since)}&limit=500`, { signal });
export const postSeen = () => api("/api/seen", { method: "POST", body: {}, keepalive: true });
// getProjects は R85-2 で撤去（import元ゼロのデッドエクスポートだった。PCの起動導線は office_json.launchable を使う）。
export const getStatusBoard = (signal) => api("/api/status_board", { signal });

/** 指示を投函する。宛先は代表セッションID（core/project.js が決める）。 */
export const postInstruction = (session, text) =>
  api("/api/instruct", { method: "POST", body: { session, text } });

/**
 * R86-H: いま人間に聞いていて止まっているセッションへ**その場で**答える。
 * 指示ポスト(/api/instruct)はターンが終わるまで届かないので、承認まちには構造的に届かない。
 * behavior="allow" が通るのはこの経路（loopback+CSRF＝Macの前の人間）だけ。
 */
// R100-0: askId で「どの 1 件への回答か」を必ず言う（同じセッションで 2 件並ぶと取り違えた）。
export const approvalReply = (session, behavior, message = "", askId = "") =>
  api("/api/approval/reply", { method: "POST", body: { session, behavior, message, askId } });

/** R53: そのセッションが動いている実ターミナル（ホストアプリ）を前面へ。 */
export const focusTerminal = (session) =>
  api("/api/terminal/focus", { method: "POST", body: { session } });

/** R54: アカウント連携（🔑）。status はローカルUI専用（masked以外の秘密値は来ない）。 */
export const getKeysStatus = () => api("/api/keys/status");
export const setOfficeKey = (name, value) =>
  api("/api/keys/set", { method: "POST", body: { name, value } });
export const testProvider = (id, value) =>
  api("/api/providers/test", { method: "POST", body: value === undefined ? { id } : { id, value } });

/** ➕新プロジェクト（P1）: フォルダ選択（ネイティブダイアログ・最大300秒）→登録。 */
export const pickProjectFolder = () =>
  api("/api/project/pick", { method: "POST", body: {} });
/** Same NFC cwd hash as project_id_for; the server still checks projects_index. */
export async function projectIdForPath(path) {
  const bytes = new TextEncoder().encode(path.normalize("NFC"));
  const hash = await crypto.subtle.digest("SHA-1", bytes);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 12);
}
export const setProjectArch = (projectId, arch) =>
  api("/api/project/arch", { method: "POST", body: { projectId, arch } });
/** R91: アバター1体の見た目。scope="session"=この個体だけ／"project"=このフォルダ全員。 */
export const setAvatarStyle = (avatarId, scope, style) =>
  api("/api/avatar/style", { method: "POST", body: { avatarId, scope, ...style } });
export const hireSession = (projectId, prompt, worktree = false) =>
  api("/api/hire", { method: "POST", body: { projectId, prompt, worktree } });
export const newProject = (path, name, { launch = true } = {}) =>
  api("/api/project/new", { method: "POST", body: { path, name, launch } });

/** 💳実支出台帳（R50提案3で新UIへ移植）: upsert/delete。形は status_board.spend_apply が正本。 */
export const spendApply = (body) =>
  api("/api/status_board/spend", { method: "POST", body });

/** R63: APIプロバイダの手動予算（上限が取れないプロバイダ用・amount=0で解除）。 */
export const budgetApply = (provider, amount, currency = "USD") =>
  api("/api/status_board/budget", { method: "POST", body: { provider, amount, currency } });

// 🧾ライセンスAPI（licenseStatus/licenseSet）は R84 全機能無料化で撤去（R85-2）。

/** 📱スマホ連携（P3）: デバイス発行/一覧/失効。pair/new はPro機能（403あり）。 */
export const pairNew = (label) =>
  api("/api/pair/new", { method: "POST", body: { label } });
export const pairList = () => api("/api/pair/list");
export const pairRevoke = (deviceId) =>
  api("/api/pair/revoke", { method: "POST", body: { device_id: deviceId } });
/** R116-S1b: つながりの延長（Mac の物理確認・通知）。窓を開くだけ＝スマホがアプリを開くと新しい秘密を封書で受け取って完了する。 */
export const pairExtend = (deviceId) =>
  api("/api/pair/extend", { method: "POST", body: { device_id: deviceId } });
/** R100-1b: スマホから許可。ON（1/4/12 時間・Mac の物理確認）・許可鍵の登録チケット・失効・状態。画面の操作だけ（Origin 必須）。 */
export const approveStatus = () => api("/api/approve/status");
// R118（本人裁定 2026-10-13「常に ON」）: ON は期限なし＝時間は送らない
export const setRemoteApprove = (on) =>
  api("/api/config/remote_approve", { method: "POST", body: { on: on === true } });
export const approveTicket = (device) =>
  api("/api/approve/enroll_ticket", { method: "POST", body: { device, projects: "*" } });
export const approveRevoke = (device) =>
  api("/api/approve/revoke", { method: "POST", body: { device } });
/** R87: スマホに会話（封書）を見せる許可。loopback+CSRF 配下だけ＝遠隔からは変えられない。 */
export const setDialogRelay = (on) =>
  api("/api/config/dialog_relay", { method: "POST", body: { on } });

/**
 * R128 #4: ⚙ の Bot パネル（出勤の口の Bot）。一覧は GET（loopback＋CSRF）・登録／直し／削除／消音は Origin 必須の POST
 *（Mac の前の人だけ＝中継・Bot の道具には道が無い）。name・role を省く（null）とサーバーの既定（名前＝id・役＝Bot）。
 */
export const doorBots = () => api("/api/door/bots");
export const doorBotAdd = (bot, name = null, role = null) =>
  api("/api/door/bot/add", { method: "POST", body: { bot, name, role } });
export const doorBotRemove = (bot) => api("/api/door/bot/remove", { method: "POST", body: { bot } });
export const doorBotMute = (bot, on) =>
  api("/api/door/bot/mute", { method: "POST", body: { bot, on: on === true } });

/** R82 クイック定型文（作成はローカルUIのみ・スマホへは office_json.templates で同期）。 */
export const getTemplates = () => api("/api/templates");
export const setTemplates = (templates) =>
  api("/api/templates/set", { method: "POST", body: { templates } });

/** R79-10 遠隔実行の許可リスト（ローカルUI専用＝ここでしか作れない・スマホからは参照のみ）。 */
/** R86-B: シート会話ビューア（会話本文はこのオンデマンドAPIのみ＝office_json非搭載）。 */
export const getDialog = (session, depth = 0) =>
  api(`/api/session/dialog?session=${encodeURIComponent(session)}&depth=${encodeURIComponent(depth)}`);

/**
 * R121: 作業ビューの行（server/office_term.py の行・伏字済み）。after＝{off, h}（差分）・before＝{off, h, i}（遡り）。
 * 返り値は {ok, rows, cur, reset, gap, beg, top}（スマホの封書 term と同じ形）。
 */
export const getActivity = (session, { after = null, before = null } = {}) => {
  const q = new URLSearchParams({ session });
  if (after) q.set("after", `${after.off}:${after.h}`);
  else if (before) q.set("before", `${before.off}:${before.h}:${before.i || 0}`);
  return api(`/api/session/activity?${q}`);
};

/** R85-3: PC機能パリティ（休眠プロジェクト起動・言語切替・為替レート編集）。 */
export const launchProject = (projectId) =>
  api("/api/projects/launch", { method: "POST", body: { projectId } });
export const setServerLang = (lang) =>
  api("/api/lang", { method: "POST", body: { lang } });
export const fxApply = (jpyPerUsd) =>
  api("/api/status_board/fx", { method: "POST", body: { jpyPerUsd } });

export const getRecipes = () => api("/api/recipes");
export const setRecipes = (recipes) =>
  api("/api/recipes/set", { method: "POST", body: { recipes } });

/** ヘッダ必須の SSE を fetch で購読。hello で接続確定、切断時は3秒後に再接続。 */
export function events(onPoke, onDrop, onOpen) {
  let stopped = false;
  let timer = 0;
  let since = null;
  let ac = null;

  const connect = async () => {
    ac = new AbortController();
    let reader;
    try {
      const path = since === null ? "/api/events" : `/api/events?since=${since}`;
      const res = await fetch(path, { headers: { ...HEADERS }, signal: ac.signal });
      if (!res.ok || !res.body || !res.headers.get("Content-Type")?.startsWith("text/event-stream")) {
        throw new ApiError("イベントに接続できません", res.status, path);
      }
      reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let kind = "";
      let lines = [];
      const dispatch = () => {
        if (!lines.length) return;
        let data;
        try { data = JSON.parse(lines.join("\n")); } catch { return; }
        if (!Number.isSafeInteger(data?.seq) || data.seq < 0) return;
        if (kind === "hello" && data.v === 2) {
          if (since === null || since > data.seq) since = data.seq;
          onOpen?.(data);
        } else if (kind === "poke") {
          since = data.seq;
          onPoke(data);
        }
      };
      while (!stopped) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let end;
        while ((end = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, end).replace(/\r$/, "");
          buffer = buffer.slice(end + 1);
          if (!line) {
            dispatch();
            kind = "";
            lines = [];
          } else if (line.startsWith("event:")) {
            kind = line.slice(6).replace(/^ /, "");
          } else if (line.startsWith("data:")) {
            lines.push(line.slice(5).replace(/^ /, ""));
          }
        }
      }
    } catch {
      // 接続失敗・切断中も通常のポーリングが状態を取得する。
    } finally {
      reader?.releaseLock();
      ac.abort();
      if (!stopped) {
        onDrop?.();
        timer = setTimeout(connect, 3000);
      }
    }
  };
  connect();
  return () => { stopped = true; clearTimeout(timer); ac?.abort(); };
}

/**
 * 一定間隔でポーリングし、コールバックへ渡す。
 * 既定（3秒間隔）は2回連続で失敗したらオフライン扱い（現行UIと同じ判定）。
 * R106-14: SSE接続後は間隔が15秒へ伸びる（ui/hud/session.js）＝そのまま「2回連続」を待つと、接続が切れてから
 * 最大約30秒、無警告で古いデータを出し続ける窓ができていた（copy-consistency-04の実測）。間隔が長いときほど
 * 最初の1回の失敗を重く見る＝間隔が SLOW_POLL_MS 以上のときは1回でオフライン扱いにする。
 */
const SLOW_POLL_MS = 10000;
export function poll(fetcher, onData, onOffline, intervalMs = 3000) {
  let fails = 0;
  let timer = 0;
  let stopped = false;
  let running = false;
  let pending = false;
  const ac = new AbortController();

  const run = async () => {
    if (stopped) return;
    if (running) { pending = true; return; }
    clearTimeout(timer);
    running = true;
    // R98: コールバック（onData / onOffline）の例外は「通信の失敗」ではない。同じ catch に落とすと
    // 描画のバグが「サーバーに繋がらない」に化ける（台帳の証拠列で実際に踏んだ）。
    // かつ**例外でポーリングを止めない**＝次回の予約は必ず finally で行う（別モデルレビュー）。
    const safely = (label, fn) => {
      try { fn(); } catch (err) { console.error(`[poll] ${label} failed`, err); }
    };
    try {
      const data = await fetcher(ac.signal);
      if (stopped) return;
      fails = 0;
      safely("onOffline", () => onOffline?.(false));
      safely("onData", () => onData(data));
    } catch (err) {
      if (stopped || err?.name === "AbortError") return;
      if (++fails >= (intervalMs >= SLOW_POLL_MS ? 1 : 2)) safely("onOffline", () => onOffline?.(true, err));
    } finally {
      running = false;
      if (!stopped) {
        timer = setTimeout(run, pending ? 0 : intervalMs);
        pending = false;
      }
    }
  };
  // 呼び出し可能な解除関数は従来どおり。即時更新は多重 fetch を作らず1回に束ねる。
  const stop = () => { stopped = true; clearTimeout(timer); ac.abort(); };
  stop.refresh = run;
  stop.setInterval = (ms) => {
    intervalMs = ms;
    if (!stopped && !running) {
      clearTimeout(timer);
      timer = setTimeout(run, intervalMs);
    }
  };
  Object.defineProperty(stop, "intervalMs", { get: () => intervalMs });
  run();
  return stop;
}

// ── R99 Phase 2: コックピットの端末（本物の pty）───────────────────────────────
// 出力は stream でしか届かない（サーバーはどこにも保存しない）。入力は**順番を守って**送る
// ＝打った順に 1 本ずつ POST する（並べて投げると到着順が入れ替わり、打鍵が化ける）。

export const termList = () => api("/api/term/list");
export const termOpen = (kind, projectId, cols, rows) =>
  api("/api/term/open", { method: "POST", body: { kind, projectId: projectId || null, cols, rows } });
export const termResize = (id, cols, rows) =>
  api("/api/term/resize", { method: "POST", body: { id, cols, rows } });
export const termClose = (id) => api("/api/term/close", { method: "POST", body: { id } });

/** 1 回の POST に載せる上限（生バイト）。サーバーは 64 KiB まで受けるが、base64 と JSON で
 *  膨らむので余裕を取る。長い貼り付けは**この大きさに割って順番に**送る（1 回で送ると上限を超えて
 *  拒否され、しかも黙って消えた＝別モデルレビューが 70 KiB で実測）。 */
export const TERM_INPUT_CHUNK = 16 * 1024;

/** 入力の送り口を 1 本作る。send(bytes) は打った順に届く（前の POST が返るまで次を投げない）。
 *  届かなかったら onError を呼ぶ（黙って捨てない）。途中で失敗したら残りは送らない＝順番が崩れた
 *  入力をシェルに流すより、止めて知らせる方が安全。 */
export function termInput(id, { onError } = {}) {
  let chain = Promise.resolve();
  let pending = [];
  const post = (bytes) => {
    let bin = "";
    for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
    return api("/api/term/input", { method: "POST", body: { id, data: btoa(bin) } });
  };
  const flush = async () => {
    // ★空振りで `chain` を返してはいけない。flush は `chain.then(flush)` の中で呼ばれるので、
    // chain＝いま解決中の自分自身＝Promise が循環して reject し、以降の .then(flush) が**全部飛ばされる**
    // （最初の打鍵の塊だけ届き、あとは永久に消える端末になった。E2E で実測）。
    if (!pending.length) return undefined;
    // 前の POST を待つ間に溜まった打鍵は 1 つにまとめる（速打ちで POST の山を作らない）
    const size = pending.reduce((n, b) => n + b.length, 0);
    const all = new Uint8Array(size);
    let at = 0;
    for (const b of pending) { all.set(b, at); at += b.length; }
    pending = [];
    for (let i = 0; i < all.length; i += TERM_INPUT_CHUNK) {
      try {
        const res = await post(all.subarray(i, i + TERM_INPUT_CHUNK));
        if (res && res.ok === false) throw new ApiError("端末が入力を受け取れませんでした", 200, "/api/term/input");
      } catch (err) {
        onError?.(err, all.length - i);
        return undefined;
      }
    }
    return undefined;
  };
  return (bytes) => {
    pending.push(bytes);
    // 1 回の失敗（瞬断・daemon の再起動）で鎖ごと死なないよう、必ず catch で繋ぎ直す
    chain = chain.then(flush).catch(() => {});
    return chain;
  };
}

/**
 * R111-B2: 端末（最大 4 本）の出力を **1 本の接続で** 購読する（GET /api/term/streams＝サーバーの多重化）。
 * なぜ 1 本か: サーバーは HTTP/1.0＝短い要求（/api/office・一覧・打鍵の POST・resize）も毎回 1 接続を食う。同一オリジンは 6 本まで
 * ＝ペインごとに 1 本ずつ繋ぐと /api/events と合わせて 5 本が塞がり、打鍵の POST が後ろで待つ（別モデルレビューが突いた形）。
 *
 * 契約（server/office_server.py の _term_streams）: 端末ごとに hello（繋ぐたびに来る・id を含む）→ out（`<id> <base64>`＝JSON でない）
 * → exit（{id, code}）。from(id)＝その画面が読み終えた通し番号（無ければ null）。サーバーはリングの中なら**続きだけ**返し
 *（hello.reset=false）、無理なら頭から（hello.reset=true・hello.modes＝画面モードの立て直し）。**reset=true のときだけ画面を消す**こと。
 * 一覧に無い id は 200 のままその id だけ exit {code:null}。全 id が exit したらサーバーが閉じる＝ここも終わる。
 * exit の前に切れたら 1 秒後に繋ぎ直す（**from は繋ぎ直すたびに id ごとに問い直す**＝受け取ったところから続ける）。
 * 400（ids・from の形違い）と 404（多重化を知らない古い daemon）は繋ぎ直しても直らない＝止める（毎秒叩かない）。
 * 戻り値は止める関数（`.active`＝まだ購読中か）。
 */
export function termStreams(ids, { from = () => null, onHello, onOut, onExit, onDrop } = {}) {
  const want = [...new Set(ids)];
  const live = new Set(want);                  // まだ exit を受けていない id
  let stopped = false;
  let dead = !want.length;                     // 全 id の exit を受けた・直らない拒否で止めた
  let timer = 0;
  let ac = null;
  const decodeB64 = (s) => {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
    return out;
  };
  const exit = (id, code) => {
    if (!live.delete(id)) return;
    onExit?.(id, code);
    if (!live.size) dead = true;
  };
  const connect = async () => {
    if (stopped || dead) return;
    ac = new AbortController();
    let reader;
    try {
      const ask = [...live];
      const at = ask.map((id) => [id, from(id)]).filter(([, n]) => Number.isSafeInteger(n) && n >= 0);
      const path = `/api/term/streams?ids=${ask.join(",")}${at.length ? `&from=${at.map(([id, n]) => `${id}:${n}`).join(",")}` : ""}`;
      const res = await fetch(path, { headers: { ...HEADERS }, signal: ac.signal });
      if (res.status === 400 || res.status === 404) { dead = true; return; }
      if (!res.ok || !res.body) throw new ApiError("端末に接続できません", res.status, path);
      reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let kind = "";
      let data = "";
      while (!stopped && !dead) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let end;
        while ((end = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, end).replace(/\r$/, "");
          buffer = buffer.slice(end + 1);
          if (line.startsWith("event:")) kind = line.slice(6).trim();
          else if (line.startsWith("data:")) data = line.slice(5).trim();
          else if (!line && kind) {
            if (kind === "hello") {
              const h = JSON.parse(data);
              if (h.modes) h.modes = decodeB64(h.modes);
              if (live.has(h.id)) onHello?.(h.id, h);
            } else if (kind === "out") {
              const cut = data.indexOf(" ");
              const id = cut > 0 ? data.slice(0, cut) : "";
              if (live.has(id)) onOut?.(id, decodeB64(data.slice(cut + 1)));
            } else if (kind === "exit") {
              const x = JSON.parse(data);
              exit(x.id, x.code ?? null);
            }
            kind = ""; data = "";
          }
        }
      }
    } catch {
      // 切断・一時的な失敗。下で繋ぎ直す
    } finally {
      reader?.releaseLock();
      ac.abort();
      if (!stopped && !dead) {
        onDrop?.();
        timer = setTimeout(connect, 1000);
      }
    }
  };
  connect();
  const stop = () => { stopped = true; clearTimeout(timer); ac?.abort(); };
  Object.defineProperty(stop, "active", { get: () => !stopped && !dead });
  return stop;
}
