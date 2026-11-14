// 作業ビューのページと取得の状態。描画とスクロール位置は各様式が持つ。
// cockpit の loadActivity / refreshDialog と同じカーソル・再取得の規則を app に共有する。
import { isBot } from "../core/kind.js";   // 相対（node --test が素の node で読む・ブラウザでは /ui/core/kind.js と同じ実体）

export const activityMotionSignature = (member) => JSON.stringify([
  member?.feed?.[0] || "", member?.verb || "", member?.target || "", member?.state || "",
  member?.question || "", member?.ask?.askId || "", member?.work?.now?.[0] || "",
  // R128: Bot の記録は人の指示でも 1 行増える（Bot の状態は変わらない）＝未読の印（pending）の替わり目と、Bot が最後に動いた時刻（mtime＝出勤・報告・相談・取り下げ）でも取り直す。セッションは今までどおり
  ...(isBot(member) ? [Boolean(member?.pending), Number(member?.mtime) || 0] : []),
]);

/** 中央の記録を描き直すかの印（2026-11-14）。データが届くたびに丸ごと作り直すと、中央の文を選んでも 1 秒で外れ（本番で確かめた）、
 * 押したボタンも外れる。行（取り直しのたびに新しい配列＝呼び手が参照で比べる）とこの印が前回と同じなら描き直さない。
 * 経過秒は各様式の毎秒のループが [data-since] をその場で書き換える。印に入れるのは描き手（activity.js の render・各様式の decorate・
 * tray.js の answerActs）が読む行の項目だけ＝age・mtime など毎回替わる数は入れない。待ち時間のように替わる文は extra に文のまま渡す。 */
export const activityPaintKey = (member, extra = []) => JSON.stringify([
  member?.session || "", member?.state || "", Boolean(member?.attention), member?.question || "",
  member?.questionOptions || null, member?.ask || null, ...extra,
]);

/** 前回描いた節がそのまま残っているか（ほかの描き手が host の中身を替えていたら描き直す）。 */
export const samePaint = (painted, host, rows, key) => Boolean(painted) && painted.host === host && painted.rows === rows
  && painted.key === key && host.firstElementChild === painted.first && host.lastElementChild === painted.last;

/** R128: Bot の記録は、行（状態・未読の印・時刻）が何も変わらなくても増える（未読が立ったままの 2 通目の指示・別の画面からの答え）。
 * 選んでいる間はこの秒数ごとに差分を取りに行く（カーソルつき＝増えていなければ 0 行）。セッションは今までどおり変化があったときだけ。 */
export const BOT_RECORD_POLL_SEC = 5;
export const botRecordDue = (member, mark, nowSec) => isBot(member) && Boolean(mark) && mark.session === member?.session
  && nowSec - mark.at >= BOT_RECORD_POLL_SEC;

export const createActivityPage = () => ({
  rows: [], cur: null, beg: null, top: false, gap: false, loaded: false,
});

/** 純粋なページ結合。先頭遡りでは最新側のカーソルを動かさない。 */
export function mergeActivityPage(page, data, { before = false } = {}) {
  if (!data?.ok) return page;
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const reset = Boolean(data.reset);
  const first = !page.loaded;
  return {
    ...page,
    rows: before && !reset ? rows.concat(page.rows) : reset || first ? rows : page.rows.concat(rows),
    cur: !before || reset ? data.cur || page.cur : page.cur,
    beg: before || first || reset ? data.beg || null : page.beg,
    top: before || first || reset ? Boolean(data.top) : page.top,
    gap: reset ? Boolean(data.gap) : page.gap || Boolean(data.gap),
    loaded: true,
  };
}

/** 時刻は秒の単調時計を注入する。部屋の固定時刻・デモではネットワークを呼ばない。 */
export function createActivityFeed({ fetchActivity, now = () => 0, frozen = false, demo = false, onChange = () => {} }) {
  const pages = new Map();
  let mark = null, pending = null, disposed = false;
  const page = (session) => {
    if (!pages.has(session)) pages.set(session, createActivityPage());
    return pages.get(session);
  };
  const load = async (member, { before = false } = {}) => {
    if (!member?.session || frozen || demo || disposed || pending) return false;
    const current = page(member.session);
    if (before && (!current.beg || current.top)) return false;
    mark = { session: member.session, sig: activityMotionSignature(member), age: Number(member.age) || 0, at: now() };
    // Promise.resolve().then は同期例外も捕える。pending を確定してから通信を始める。
    const request = Promise.resolve().then(() => fetchActivity(member.session,
      before ? { before: current.beg } : { after: current.cur }));
    pending = request;
    let changed = false;
    try {
      const data = await request;
      if (disposed || !data?.ok) return false;
      Object.assign(current, mergeActivityPage(current, data, { before }));
      changed = true;
      onChange(member.session, current, { before });
      return true;
    } catch {
      // 受信済み本文は残し、次の変化で再試行する。
      return false;
    } finally {
      if (pending === request) pending = null;
      if (!disposed) onChange(member.session, current, { before, settled: true, changed });
    }
  };
  const refresh = (member) => {
    if (!member?.session || frozen || demo || disposed || pending) return Promise.resolve(false);
    const current = page(member.session), sig = activityMotionSignature(member), age = Number(member.age) || 0;
    const moved = !current.loaded || !mark || mark.session !== member.session || mark.sig !== sig || age + 5 < mark.age
      || botRecordDue(member, mark, now());
    if (!moved) { if (mark) mark.age = age; return Promise.resolve(false); }
    if (mark?.session === member.session && now() - mark.at < 3) return Promise.resolve(false);
    return load(member);
  };
  return { page, load, refresh, get busy() { return Boolean(pending); }, dispose() { disposed = true; } };
}
