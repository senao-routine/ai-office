// R110 S5: 部屋の「用事」＝実際の道具に連動して机を離れ、本棚・掲示板・ボードの手前・カウンターへ行って同じ机へ戻る。
// 本人「もっと AI が働いている感が欲しい」「作業内容に応じて部屋を移動する工夫を」（2026-10-06）。
// 部屋は事実だけを伝える（design/README.md「場所の意味」・R110 計画「嘘の動きは入れない」）:
//   本棚＝いま読んでいる（Read・Grep・Glob・Explore）／掲示板＝Web を調べている（WebFetch・WebSearch）／
//   ボードの手前＝段取りを整理している（TodoWrite）／カウンター＝commit かテストの成功がいま届いた（証拠）。
//   Bash・Edit は机に残る（画面の模様と打鍵の速さで見せるのは描画側）。Agent・Task は既存の会議室と助手。
//
// 純関数（DOM・通信・時刻・乱数に触らない＝時刻は引数・個体差はキーの決定的ハッシュ）。ui/core/life.js の lifeTargets と
// 同じ型: roomLayout の actors を受けて、用事に出ている人の目的地（x, y）だけを差し替えた写しを返す。席の割り当て（assign）・
// 人数（counts）・在席（occupied）・区分（zone）・席（seat）は変えない＝机は予約のまま・画面は点いたまま・SLOTS は崩れない。
// 歩く・戻るは pxwalk.stepWalkers（目的地が変われば今いる場所から経路を引き直す既存の掟）に任せる。
//
// 行き先（spots）は引数で注入する（間取りの正本は ui/core/pxroom.js の ERRAND_SPOTS＝置く側が持つ）。形は LIFE_SPOTS と同じ
// [{x, y, name}]（name が行き先の種類＝ERRAND_SPOT_OF の値か EVIDENCE_ERRAND.spot。同じ name を 2 つ置けば 2 人まで立てる）か
// {name: {x, y} | [{x, y}, …]}。spots に無い行き先へは誰も出ない（spots を渡さなければ誰も動かない）。
//
// 配線（ui/cockpit/room.js・1 フレーム・live の門は散歩と同じ `lifeLive && !resumed`＝新しい旗は作らない）:
//   const errands = errandTargets(actors, tm, { live: lifeLive && !resumed, prev: errandBook, spots: ERRAND_SPOTS });
//   if (lifeLive) errandBook = errands.book;
//   const lives = lifeTargets(errands.actors, tm, { live: …, prev: lifeBook, max: 3 - errands.away });
// 散歩（返事を待っている waiting の事実）と用事（働いている working の事実）は資格が排他＝同じ人が両方には出ない。
// 部屋全体で机を離れている人数の上限は呼び手が max で配る（用事が先・散歩は残り）。ここは自分の max だけを守る。
import { tintHash } from "./pxroom.js";

// ── 道具の種類 ────────────────────────────────────────────────────────
/**
 * 道具の種類 → 工具名（Claude Code の工具名＝server/office_server.py の TOOL_VERB のキー）。どれにも入らない工具
 *（Agent・Task・Workflow・Skill・AskUserQuestion・Artifact・mcp__… など）は種類なし（null）。Task 系は plan に入れない
 *（サブエージェントへの依頼は会議室と助手が受け持つ）。
 */
export const TOOL_CLASSES = Object.freeze({
  bash: Object.freeze(["Bash"]),
  edit: Object.freeze(["Edit", "Write", "NotebookEdit", "MultiEdit"]),
  read: Object.freeze(["Read", "Grep", "Glob", "Explore"]),
  web: Object.freeze(["WebFetch", "WebSearch"]),
  plan: Object.freeze(["TodoWrite"]),
});

/**
 * 道具の種類 → 動詞（server/office_server.py の TOOL_VERB / TOOL_VERB_EN の**値そのもの**）。サーバーは verb に表の値を
 * 載せる（describe_tool）ので字面で突き合わせる。サーバーの表と食い違ったら ui/core/errand.test.js が落ちる（表を文字列で読む）。
 * EN の "researching" は read（Grep・Glob・Explore）と web（WebFetch・WebSearch）の両方に在る＝動詞だけでは決めない
 *（toolClassOf は null を返す＝嘘の行き先を作らない）。サーバーの `tool` が在れば起きない（動詞は最後の手段）。
 */
export const TOP_TOOL_VERBS = Object.freeze({
  ja: Object.freeze({
    bash: Object.freeze(["実行中"]),
    edit: Object.freeze(["編集中", "執筆中"]),
    read: Object.freeze(["読込中", "調査中"]),
    web: Object.freeze(["リサーチ中"]),
    plan: Object.freeze(["段取り整理中"]),
  }),
  en: Object.freeze({
    bash: Object.freeze(["running"]),
    edit: Object.freeze(["editing", "writing"]),
    read: Object.freeze(["reading", "researching"]),
    web: Object.freeze(["researching"]),
    plan: Object.freeze(["planning"]),
  }),
});

const CLASS_OF_TOOL = new Map(Object.entries(TOOL_CLASSES).flatMap(([cls, tools]) => tools.map((t) => [t, cls])));
/** 動詞 → 種類（2 つ以上の種類にまたがる動詞は載せない＝決めない）。 */
const CLASS_OF_VERB = (() => {
  const seen = new Map();
  for (const table of Object.values(TOP_TOOL_VERBS)) {
    for (const [cls, verbs] of Object.entries(table)) {
      for (const v of verbs) {
        if (!seen.has(v)) seen.set(v, new Set());
        seen.get(v).add(cls);
      }
    }
  }
  return new Map([...seen].filter(([, s]) => s.size === 1).map(([v, s]) => [v, [...s][0]]));
})();

/**
 * 1 人がいま使っている道具の種類。優先: `tool`（サーバーが R110 で足した最後の工具名・言語に依らない）→ `ask.tool`
 *（いま確認を求めている工具）→ `stuckTool`（止まっている工具）→ `kind === "tool"` のときだけ `verb`（日英の訳語）。
 * 最初に在った名前が決める（その工具が種類を持たなければ null＝後ろの段へは落とさない）。kind が think・said なら null
 *（考え中・報告中は道具を使っていない）。
 * @param {object} a roomLayout の actor（か world の agent）
 * @returns {"bash"|"edit"|"read"|"web"|"plan"|null}
 */
export function toolClassOf(a) {
  if (!a || typeof a !== "object" || a.kind === "think" || a.kind === "said") return null;
  const ask = a.ask && typeof a.ask === "object" ? a.ask.tool : undefined;
  for (const name of [a.tool, ask, a.stuckTool]) {
    const s = typeof name === "string" ? name.trim() : "";
    if (s) return CLASS_OF_TOOL.get(s) ?? null;
  }
  if (a.kind !== "tool") return null;
  return CLASS_OF_VERB.get(String(a.verb ?? "").trim()) ?? null;
}

// ── 時間割と行き先 ───────────────────────────────────────────────────
/**
 * 用事の時間割（秒）。start 秒より前（ページを開いた直後）は出ない。窓は出発から span 秒（往路 ≤8・滞在 ≈10・復路）。
 * 同じ種類が続く人は前の出発（か見始め）から repeat 秒以上たった格子で 1 回だけ出る。同時に机を離れるのは maxAway 人まで。
 */
export const ERRAND = Object.freeze({ start: 30, span: 20, repeat: 240, maxAway: 2 });

/** 道具の種類 → 行き先の名前（spots の name）。bash・edit は机に残る＝ここに無い。 */
export const ERRAND_SPOT_OF = Object.freeze({ read: "shelf", web: "bulletin", plan: "board" });

/**
 * 証拠（commit・テストの成功）→ カウンターへ 1 回。kinds＝行く証拠の種類（evidence.kind）。jitter＝同じ出来事の揺れの許容
 *（出来事の時刻は generatedAt − ago＝ago は整数秒なので 1 秒未満揺れる・ui/pixel/strip.js の NEW_RESULT_SEC と同じ 2 秒）。
 * fresh＝ago がこれより古い証拠は新着と読まない（最初の取得で欠けた 24 時間前の成功が後から届いても行かない＝strip.js の
 * 「見始める前の出来事を新着にしない」と同じ掟）。
 */
export const EVIDENCE_ERRAND = Object.freeze({
  spot: "counter", span: 14, kinds: Object.freeze(["committed", "tested"]), jitter: 2, fresh: 120,
});

/**
 * 同じ種類が続く人の出発の格子。出発は `((t + hash(key) % scatter) % repeat) < tick` の間（人ごとに 0〜scatter 秒ずらす＝
 * 全員が同じ瞬間に立たない）。tick は待機の描画（6fps）が必ず 1 回は踏む幅。1 回だけ＝出発の t を lastGo に刻むので、
 * 同じ格子の残りのフレームは `t − lastGo < repeat` で出ない。
 */
export const ERRAND_GRID = Object.freeze({ scatter: 60, tick: 1 });

/**
 * 記録がこの秒数より古い（または未来の）ときは「初めて見た」と同じに扱う＝記録だけして出ない。非表示のタブ・停止・再生から
 * live へ戻ったあと、その間に起きた道具の切り替えや証拠を遅れて演じない（pxwalk の WALK_STALE_SEC・pxtop の MARK_STALE_SEC と
 * 同じ考え・同じ値＝errand.test.js が一致を見る）。
 */
export const ERRAND_STALE_SEC = 4;

/** 有限の数だけを数として読む（null・"" を 0 にしない）。 */
function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** 行き先の表（spots を正規化したもの）。list の添字が「立ち位置」・byName は名前 → 添字の並び。 */
class Spots {
  constructor(list) {
    this.list = Object.freeze(list);
    const byName = new Map();
    list.forEach((s, i) => {
      if (!byName.has(s.name)) byName.set(s.name, []);
      byName.get(s.name).push(i);
    });
    this.byName = byName;
    Object.freeze(this);
  }

  has(name) {
    return typeof name === "string" && this.byName.has(name);
  }
}

function spotTable(spots) {
  if (spots instanceof Spots) return spots;
  const list = [];
  const add = (name, s) => {
    if (typeof name === "string" && name && s && typeof s === "object" && Number.isFinite(s.x) && Number.isFinite(s.y)) {
      list.push(Object.freeze({ name, x: s.x, y: s.y }));
    }
  };
  if (Array.isArray(spots)) spots.forEach((s) => add(s?.name, s));
  else if (spots && typeof spots === "object") {
    for (const [name, v] of Object.entries(spots)) (Array.isArray(v) ? v : [v]).forEach((s) => add(name, s));
  }
  return new Spots(list);
}

/** 行き先の窓の長さ（カウンターは短い）。 */
function spanOf(name) {
  return name === EVIDENCE_ERRAND.spot ? EVIDENCE_ERRAND.span : ERRAND.span;
}

/** 机に座って働いている（呼んでいない・退場中でない・⚠ で止まっていない）。用事に出る・窓を続ける前提。 */
function atWork(a) {
  return Boolean(a) && typeof a === "object" && a.zone === "desk" && a.state === "working" && !a.attention
    && a.leaving !== true && !String(a.stuckTool ?? "").trim();
}

/**
 * 道具の用事に出てよい人か（机で働いている・kind が tool・道具の種類に行き先がある）。証拠（カウンター）は kind を問わない。
 * @param {object} a roomLayout の actor
 */
export function canErrand(a) {
  if (!atWork(a) || a.kind !== "tool") return false;
  const cls = toolClassOf(a);
  return cls !== null && Object.hasOwn(ERRAND_SPOT_OF, cls);
}

/**
 * 証拠の出来事の時刻（呼び手が actor.evidenceAt に入れる値）。`generatedAt − evidence.ago`（スナップショットの時刻から引く＝
 * 描画の時刻で引かない・ui/pixel/strip.js の掟）。行く種類（committed・tested）でなければ null。
 * @param {{kind?: string, ago?: number}|null} evidence office_json の employee.evidence
 * @param {number} generatedAt office_json の generatedAt（秒）
 * @returns {number|null}
 */
export function evidenceTime(evidence, generatedAt) {
  if (!evidence || typeof evidence !== "object" || !EVIDENCE_ERRAND.kinds.includes(evidence.kind)) return null;
  const at = num(generatedAt);
  const ago = num(evidence.ago);
  return at !== null && at > 0 && ago !== null && ago >= 0 ? at - ago : null;
}

/** 記録が使えるか（形があり、t が記録の時刻から ERRAND_STALE_SEC 以内で巻き戻っていない）。使えなければ null。 */
function liveRecord(rec, time) {
  if (!rec || typeof rec !== "object") return null;
  const seen = num(rec.seen);
  return seen !== null && time >= seen && time - seen <= ERRAND_STALE_SEC ? rec : null;
}

/**
 * 新しい証拠が届いたか（カウンターへ行くきっかけ）。actor.evidenceAt（出来事の時刻）が記録の ev（見た中でいちばん新しい
 * 出来事の時刻）より jitter 秒より新しいとき true。記録が無い・古い（初見）なら false＝開いた直後に昔の commit で行かない。
 * evidence が付いていれば kind が committed・tested で、ago が fresh 秒以内のものだけ。
 * @param {object} actor roomLayout の actor（evidenceAt・evidence を読む）
 * @param {object|undefined} prev この人の記録（errandTargets の book の値）
 * @param {number} t 秒（動きの時計）
 */
export function evidenceErrand(actor, prev, t) {
  const time = Number(t);
  if (!Number.isFinite(time) || !actor || typeof actor !== "object") return false;
  const rec = liveRecord(prev, time);
  const at = num(actor.evidenceAt);
  if (!rec || at === null) return false;
  const ev = actor.evidence;
  if (ev && typeof ev === "object") {
    if (!EVIDENCE_ERRAND.kinds.includes(ev.kind)) return false;
    const ago = num(ev.ago);
    if (ago !== null && ago > EVIDENCE_ERRAND.fresh) return false;
  }
  const best = num(rec.ev);
  return best === null || at > best + EVIDENCE_ERRAND.jitter;
}

/** 同じ種類が続く人の出発の格子が開いているか（キーの決定的ハッシュで人ごとにずらす）。 */
function gridOpen(key, time, hash) {
  const h = Number(hash(String(key ?? "")));
  const off = Number.isFinite(h) ? Math.floor(Math.abs(h)) % ERRAND_GRID.scatter : 0;
  const u = (((time + off) % ERRAND.repeat) + ERRAND.repeat) % ERRAND.repeat;
  return u < ERRAND_GRID.tick;
}

const stay = (rec) => ({ go: false, spot: null, until: null, held: false, rec });
const leave = (rec, held) => ({ go: true, spot: rec.spot, until: rec.lastGo + spanOf(rec.spot), held, rec });

/**
 * 1 人の用事を決める（立ち位置の取り合いと人数の上限は errandTargets）。
 *
 * 記録（prev・この人の分）: `{tool, since, lastGo, spot, slot, ev, seen}`＝tool 前に見た道具の種類・since その種類を見始めた t・
 * lastGo 最後に出発した t（無ければ null）・spot 開いている窓の行き先（閉じていれば null）・slot その立ち位置（spots の添字）・
 * ev 見た中でいちばん新しい証拠の時刻・seen 記録した t。
 *
 * 出発（どれも start 秒以降・机で働いている人だけ）:
 *   1. 新しい証拠（evidenceErrand）→ カウンター（EVIDENCE_ERRAND.span 秒）。道具の用事より先。
 *   2. kind が tool で、道具の種類が前の記録から**変わった瞬間** → その種類の行き先（ERRAND.span 秒）。初見（記録が無い・古い）は
 *      記録だけ＝開いた直後に全員が出ない。
 *   3. 同じ種類が続き、前の出発（か見始め）から ERRAND.repeat 秒以上たち、格子（ERRAND_GRID）が開いている → 1 回だけ。
 * 窓の途中で種類が変わっても閉じない（行き先も変えない）。机で働いている資格を失ったら閉じる（机へ戻る）。
 * 窓の間に届いた証拠は窓の後へ回す（カウンターに居る間に届いた分は同じ紙として使い切る）。
 * 出なかったきっかけ（資格なし・start 前・行き先が spots に無い）は使い切る＝あとから遅れて出ない。
 * 同じ t・同じ記録なら同じ結果（冪等）。戻り値の rec を次の記録にして同じ t で呼び直しても同じ結果。
 *
 * @param {object} actor roomLayout の actor（key・zone・state・kind・attention・leaving・stuckTool・tool・ask・verb・
 *   evidenceAt・evidence を読む）
 * @param {object|undefined} prev この人の記録
 * @param {number} t 秒（動きの時計）
 * @param {{spots?: object, hash?: (key: string) => number}} [o] spots＝行き先・hash＝決定的ハッシュ（既定 pxroom の tintHash）
 * @returns {{go: boolean, spot: string|null, until: number|null, held: boolean, rec: object|null}}
 *   go＝いま用事に出ている・spot＝行き先の名前・until＝窓が閉じる t・held＝前の記録から続いている窓・rec＝次の記録
 */
export function planErrand(actor, prev, t, o = {}) {
  const opts = o && typeof o === "object" ? o : {};
  const time = Number(t);
  if (!Number.isFinite(time)) return stay(prev && typeof prev === "object" ? prev : null);
  const cls = toolClassOf(actor);
  const evAt = actor && typeof actor === "object" ? num(actor.evidenceAt) : null;
  const rec = liveRecord(prev, time);
  if (!rec) {
    return stay(Object.freeze({ tool: cls, since: time, lastGo: null, spot: null, slot: -1, ev: evAt, seen: time }));
  }
  const table = spotTable(opts.spots);
  const hash = typeof opts.hash === "function" ? opts.hash : tintHash;
  const lastGo = num(rec.lastGo);
  const since = cls === rec.tool && num(rec.since) !== null ? rec.since : time;
  const best = num(rec.ev);
  const ev = evAt !== null && (best === null || evAt > best) ? evAt : best;
  const next = { tool: cls, since, lastGo, spot: null, slot: -1, ev, seen: time };
  const here = atWork(actor);

  // 開いている窓は続ける（種類が変わっても閉じない・机で働いている資格を失ったら閉じる）
  const open = table.has(rec.spot) && lastGo !== null && time >= lastGo && time < lastGo + spanOf(rec.spot);
  if (open && here) {
    const keepEv = rec.spot === EVIDENCE_ERRAND.spot ? ev : best;
    const slot = Number.isInteger(rec.slot) ? rec.slot : -1;
    return leave(Object.freeze({ ...next, ev: keepEv, spot: rec.spot, slot }), true);
  }
  if (time < ERRAND.start || !here) return stay(Object.freeze(next));

  // 新しい証拠 → カウンター（道具の用事より先）
  if (table.has(EVIDENCE_ERRAND.spot) && evidenceErrand(actor, rec, time)) {
    return leave(Object.freeze({ ...next, lastGo: time, spot: EVIDENCE_ERRAND.spot }), false);
  }

  // 道具の用事
  if (actor.kind !== "tool" || cls === null || !Object.hasOwn(ERRAND_SPOT_OF, cls)) return stay(Object.freeze(next));
  const dest = ERRAND_SPOT_OF[cls];
  if (!table.has(dest)) return stay(Object.freeze(next));
  if (cls !== rec.tool) return leave(Object.freeze({ ...next, lastGo: time, spot: dest }), false);
  const from = lastGo !== null && lastGo >= since ? lastGo : since;
  if (time - from >= ERRAND.repeat && gridOpen(actor.key, time, hash)) {
    return leave(Object.freeze({ ...next, lastGo: time, spot: dest }), false);
  }
  return stay(Object.freeze(next));
}

/** 立ち位置を 1 つ選ぶ（続きの窓は前の立ち位置を先に・新しい人は同じ名前の立ち位置をキーのハッシュの順に）。無ければ -1。 */
function choose(w, table, taken, hash) {
  const r = w.p.rec;
  if (w.p.held && Number.isInteger(r.slot) && r.slot >= 0 && r.slot < table.list.length
    && table.list[r.slot].name === w.p.spot && !taken.has(r.slot)) return r.slot;
  const cands = table.byName.get(w.p.spot) || [];
  if (!cands.length) return -1;
  const h = Number(hash(w.key));
  const off = Number.isFinite(h) ? Math.floor(Math.abs(h)) % cands.length : 0;
  for (let d = 0; d < cands.length; d += 1) {
    const j = cands[(off + d) % cands.length];
    if (!taken.has(j)) return j;
  }
  return -1;
}

/**
 * 用事に出ている人の目的地を行き先へ差し替える。同じ立ち位置に 2 人を立たせない・同時に出るのは max 人まで
 *（前のフレームから続いている窓が先・次に新しく出る人・それぞれキーの昇順）。立てなかった人は机に残る: 続きの窓なら閉じ
 *（机へ戻る）、新しい出発なら取り消す（きっかけは使い切り＝次の機会を待つ。窓の途中から遅れて出ない）。入力は書き換えない。
 *
 * @param {object[]} actors roomLayout().actors
 * @param {number} t 秒（動きの時計）
 * @param {{live?: boolean, prev?: Map<string, object>|null, spots?: object, max?: number, hash?: (key: string) => number}} [opts]
 *   live は t が実時間で進んでいるときだけ true（固定時刻・停止・再生・非表示から戻った最初のフレームは false）。
 *   prev は前回の book。max の既定は ERRAND.maxAway（散歩と合わせた部屋全体の上限は呼び手が配る）。
 * @returns {{actors: object[], book: Map<string, object>, away: number}}
 *   actors＝目的地だけ差し替えた写し（`{...a, x, y, errand: 行き先の名前}`・用事の無い人は入力と同じ物）・
 *   book＝次回の prev（いま居る人の分だけ・居なくなった key は捨てる）・away＝用事に出ている人数。
 *   live でなければ actors は入力そのまま・book は prev そのまま（記録を進めない）・away 0。
 */
export function errandTargets(actors, t, opts = {}) {
  const o = opts && typeof opts === "object" ? opts : {};
  const list = Array.isArray(actors) ? actors : [];
  const prev = o.prev instanceof Map ? o.prev : null;
  const time = Number(t);
  if (o.live !== true || !Number.isFinite(time)) return { actors: list, book: prev ?? new Map(), away: 0 };
  const table = spotTable(o.spots);
  const hash = typeof o.hash === "function" ? o.hash : tintHash;
  const max = typeof o.max === "number" && Number.isFinite(o.max) ? Math.max(0, Math.floor(o.max)) : ERRAND.maxAway;
  const book = new Map();
  const wants = [];
  list.forEach((a, i) => {
    if (!a || typeof a !== "object" || typeof a.key !== "string" || !a.key || book.has(a.key)) return;
    const before = prev ? prev.get(a.key) : undefined;
    const p = planErrand(a, before, time, { spots: table, hash });
    book.set(a.key, p.rec);
    if (p.go) wants.push({ i, key: a.key, p, before });
  });
  if (!wants.length) return { actors: list, book, away: 0 };
  wants.sort((p, q) => (p.p.held === q.p.held ? (p.key < q.key ? -1 : p.key > q.key ? 1 : 0) : (p.p.held ? -1 : 1)));
  const taken = new Set();
  const out = list.slice();
  let away = 0;
  for (const w of wants) {
    const pick = away < max ? choose(w, table, taken, hash) : -1;
    if (pick < 0) {
      const lastGo = w.p.held ? w.p.rec.lastGo : num(w.before?.lastGo);
      book.set(w.key, Object.freeze({ ...w.p.rec, lastGo, spot: null, slot: -1 }));
      continue;
    }
    taken.add(pick);
    away += 1;
    book.set(w.key, Object.freeze({ ...w.p.rec, slot: pick }));
    const s = table.list[pick];
    out[w.i] = Object.freeze({ ...list[w.i], x: s.x, y: s.y, errand: s.name });
  }
  return { actors: away ? out : list, book, away };
}
