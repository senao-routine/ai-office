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
export const approvalReply = (session, behavior, message = "") =>
  api("/api/approval/reply", { method: "POST", body: { session, behavior, message } });

/** R53: そのセッションが動いている実ターミナル（ホストアプリ）を前面へ。 */
export const focusTerminal = (session) =>
  api("/api/terminal/focus", { method: "POST", body: { session } });

/** R54: アカウント連携（🔑）。status はローカルUI専用（masked以外の秘密値は来ない）。 */
export const getKeysStatus = () => api("/api/keys/status");
export const setOfficeKey = (name, value) =>
  api("/api/keys/set", { method: "POST", body: { name, value } });

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

/** R82 クイック定型文（作成はローカルUIのみ・スマホへは office_json.templates で同期）。 */
export const getTemplates = () => api("/api/templates");
export const setTemplates = (templates) =>
  api("/api/templates/set", { method: "POST", body: { templates } });

/** R79-10 遠隔実行の許可リスト（ローカルUI専用＝ここでしか作れない・スマホからは参照のみ）。 */
/** R86-B: シート会話ビューア（会話本文はこのオンデマンドAPIのみ＝office_json非搭載）。 */
export const getDialog = (session, depth = 0) =>
  api(`/api/session/dialog?session=${encodeURIComponent(session)}&depth=${encodeURIComponent(depth)}`);

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
 * 2回連続で失敗したらオフライン扱いにする（現行UIと同じ判定）。
 */
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
    try {
      const data = await fetcher(ac.signal);
      if (stopped) return;
      fails = 0;
      onOffline?.(false);
      onData(data);
    } catch (err) {
      if (stopped || err.name === "AbortError") return;
      if (++fails >= 2) onOffline?.(true, err);
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
