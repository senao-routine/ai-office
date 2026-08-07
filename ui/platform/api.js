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

export async function api(path, { method = "GET", body = null, signal = null } = {}) {
  const opts = { method, headers: { ...HEADERS }, signal };
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
export const getProjects = (signal) => api("/api/projects", { signal });
export const getStatusBoard = (signal) => api("/api/status_board", { signal });

/** 指示を投函する。宛先は代表セッションID（core/project.js が決める）。 */
export const postInstruction = (session, text) =>
  api("/api/instruct", { method: "POST", body: { session, text } });

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
export const newProject = (path, name, { launch = true } = {}) =>
  api("/api/project/new", { method: "POST", body: { path, name, launch } });

/** 💳実支出台帳（R50提案3で新UIへ移植）: upsert/delete。形は status_board.spend_apply が正本。 */
export const spendApply = (body) =>
  api("/api/status_board/spend", { method: "POST", body });

/** R63: APIプロバイダの手動予算（上限が取れないプロバイダ用・amount=0で解除）。 */
export const budgetApply = (provider, amount, currency = "USD") =>
  api("/api/status_board/budget", { method: "POST", body: { provider, amount, currency } });

/** 🧾ライセンス（R42.2）: 状態と登録（文字列はJSONとして受理・nullで解除）。 */
export const licenseStatus = () => api("/api/license/status");
export const licenseSet = (license) =>
  api("/api/license/set", { method: "POST", body: { license } });

/** 📱スマホ連携（P3）: デバイス発行/一覧/失効。pair/new はPro機能（403あり）。 */
export const pairNew = (label) =>
  api("/api/pair/new", { method: "POST", body: { label } });
export const pairList = () => api("/api/pair/list");
export const pairRevoke = (deviceId) =>
  api("/api/pair/revoke", { method: "POST", body: { device_id: deviceId } });

/** R79-10 遠隔実行の許可リスト（ローカルUI専用＝ここでしか作れない・スマホからは参照のみ）。 */
export const getRecipes = () => api("/api/recipes");
export const setRecipes = (recipes) =>
  api("/api/recipes/set", { method: "POST", body: { recipes } });

/**
 * 一定間隔でポーリングし、コールバックへ渡す。
 * 2回連続で失敗したらオフライン扱いにする（現行UIと同じ判定）。
 */
export function poll(fetcher, onData, onOffline, intervalMs = 3000) {
  let fails = 0;
  let timer = 0;
  let stopped = false;
  const ac = new AbortController();

  const run = async () => {
    if (stopped) return;
    try {
      const data = await fetcher(ac.signal);
      fails = 0;
      onOffline?.(false);
      onData(data);
    } catch (err) {
      if (err.name === "AbortError") return;
      if (++fails >= 2) onOffline?.(true, err);
    }
    if (!stopped) timer = setTimeout(run, intervalMs);
  };
  run();
  return () => { stopped = true; clearTimeout(timer); ac.abort(); };
}
