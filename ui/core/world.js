// ──────────────────────────────────────────────────────────────
// 純ロジック層。DOM も window も fetch も乱数も時刻も触らない。
//
//   ui/core/**     = ここ。node --test でそのままテストできる（ブラウザ不要）
//   ui/platform/** = ブラウザに触る層（fetch・localStorage・rAF・window）
//   ui/iso, ui/pixel = 見た目（presentation・互いに独立）
//
// 掟: この層に document / window / fetch / Math.random / Date.now を書かない。
//     tools/js_layer_lint.py が機械で落とす。
// ──────────────────────────────────────────────────────────────

/** 席の数。使われないスロットは机ごと描かない＝空席を作らないための上限。 */
export const DESK_SLOTS = 12;
export const QUEUE_SLOTS = 4;
export const MEETING_SLOTS = 5;
export const LOUNGE_SLOTS = 3;

/**
 * サーバーの office_json を UI が使う world に落とす。
 * roster[] があればそれを使い、無ければ employees[] から素朴に作る（後方互換）。
 */
export function buildWorld(office) {
  if (!office || typeof office !== "object") return emptyWorld();
  const raw = Array.isArray(office.roster) && office.roster.length
    ? office.roster
    : (Array.isArray(office.employees) ? office.employees : []).map(fromEmployee);

  const agents = triageSort(raw).map((p) => ({
    id: p.projectId || p.session || "",
    session: p.session || "",
    name: p.disp || p.name || p.dept || "",
    crew: Number(p.crew) || 1,
    state: p.state || "idle",
    kind: p.kind || "idle",
    zone: zoneOf(p),
    activity: activityText(p),
    attention: needsAttention(p),
    approvalMin: Number(p.approvalMin) || 0,
    question: p.question || "",
    questionOptions: Array.isArray(p.questionOptions) ? p.questionOptions : [],
    pending: Boolean(p.pending),
    minions: Number(p.minions) || 0,
    age: Number(p.age) || 0,
    sprite: p.sprite || "",
    external: p.external || null,
    sessions: Array.isArray(p.sessions) ? p.sessions : [],
    feed: Array.isArray(p.feed) ? p.feed : [],
    work: p.work || null,
  }));

  return {
    officeName: office.officeName || "",
    lang: office.lang || "ja",
    edition: office.edition?.id || null,
    features: office.edition?.features || {},
    generatedAt: Number(office.generatedAt) || 0,
    setup: (office.setup && typeof office.setup === "object") ? office.setup : null,
    agents,
    seats: assignSeats(agents),
    counts: countByZone(agents),
    tasks: office.tasks || { pending: 0, inProgress: 0, completed: 0 },
    history: Array.isArray(office.history) ? office.history : [],
  };
}

function emptyWorld() {
  return {
    officeName: "", lang: "ja", edition: null, features: {}, generatedAt: 0,
    setup: null,
    agents: [], seats: new Map(), history: [],
    counts: { desk: 0, meeting: 0, queue: 0, lounge: 0, external: 0, attention: 0 },
    tasks: { pending: 0, inProgress: 0, completed: 0 },
  };
}

/** projects[] が無い古いサーバー向けの後方互換（1セッション＝1件のまま扱う）。 */
function fromEmployee(e) {
  return { ...e, projectId: e.session, name: e.dept, crew: 1, sessions: [] };
}

/** 名札の下に出す1行。「何をしているか」が常に見えていること（吹き出しは演出、これは事実）。 */
export function activityText(p) {
  if (!p) return "";
  return [p.verb, p.target].filter(Boolean).join(" ").trim();
}

/** ❗＝承認まち or 未回答の質問。UIの最優先表示の判定はここ1箇所に集約する。 */
export function needsAttention(employee) {
  if (!employee) return false;
  if (typeof employee.attention === "boolean") return employee.attention;
  return Number(employee.approvalMin) > 0 || Boolean(employee.question);
}

/**
 * どのゾーンに居るべきか。場所＝状態という設計の中心。
 * external(OpenClaw等) は別Macの稼働体なので専用区画から動かさない。
 */
export function zoneOf(employee) {
  if (!employee) return "desk";
  if (employee.external) return "external";
  if (needsAttention(employee)) return "queue";       // あなたの席の前に並ぶ
  if (employee.state === "resting") return "lounge";
  if (Number(employee.minions) > 0 && employee.state !== "resting") return "meeting";
  return "desk";
}

/** 表示順＝トリアージ順。❗→稼働→待機→休憩→external。 */
const ZONE_ORDER = { queue: 0, desk: 1, meeting: 1, lounge: 3, external: 4 };
export function triageSort(employees) {
  const list = Array.isArray(employees) ? [...employees] : [];
  return list.sort((a, b) => {
    const za = ZONE_ORDER[zoneOf(a)] ?? 9;
    const zb = ZONE_ORDER[zoneOf(b)] ?? 9;
    if (za !== zb) return za - zb;
    const sa = a.state === "working" ? 0 : a.state === "waiting" ? 1 : 2;
    const sb = b.state === "working" ? 0 : b.state === "waiting" ? 1 : 2;
    if (sa !== sb) return sa - sb;
    return (Number(a.age) || 0) - (Number(b.age) || 0);
  });
}

/**
 * 席の割当。同じプロジェクトは毎回同じ席に座る（筋肉記憶）ので、
 * id のハッシュを希望席にして衝突時だけ線形に空きを探す。
 * 乱数も時刻も使わない＝同じ入力なら必ず同じ配置（決定論）。
 */
export function assignSeats(agents, slots = DESK_SLOTS) {
  const seats = new Map();
  const taken = new Set();
  const deskAgents = (Array.isArray(agents) ? agents : []).filter((a) => zoneOf(a) === "desk");
  for (const a of deskAgents) {
    const want = stableIndex(a.id || a.session || "", slots);
    let idx = -1;
    for (let i = 0; i < slots; i++) {
      const cand = (want + i) % slots;
      if (!taken.has(cand)) { idx = cand; break; }
    }
    if (idx < 0) continue;              // 席が尽きたらフリーアドレス（描画側が立ち位置を決める）
    taken.add(idx);
    seats.set(a.id, idx);
  }
  return seats;
}

/** 文字列 → [0,n) の安定した整数（FNV-1a）。乱数を使わないので毎回同じ。 */
export function stableIndex(str, n) {
  if (!n) return 0;
  let h = 2166136261 >>> 0;
  const s = String(str ?? "");
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h % n;
}

export function countByZone(agents) {
  const out = { desk: 0, meeting: 0, queue: 0, lounge: 0, external: 0, attention: 0 };
  for (const a of Array.isArray(agents) ? agents : []) {
    const z = zoneOf(a);
    if (z in out) out[z] += 1;
    if (needsAttention(a)) out.attention += 1;
  }
  return out;
}

/** 承認まちがこの分数を超えたら質問より先頭へ昇格（飢餓防止）。 */
export const STARVE_MIN = 15;

/**
 * ❗の一覧（トリアージ順）。質問優先→承認まちは待たせた時間が長い順。
 * ただし STARVE_MIN 分を超えた承認まちは質問より先頭へ昇格する
 * （質問が続く限り承認まちが永遠にトレイへ出ない「飢餓」を防ぐ・R50提案3）。
 */
export function attentionQueue(agents) {
  const rank = (a) => {
    if (!a.question && (Number(a.approvalMin) || 0) >= STARVE_MIN) return 0;  // 昇格
    return a.question ? 1 : 2;
  };
  return (Array.isArray(agents) ? agents : []).filter(needsAttention).sort((a, b) => {
    const ra = rank(a);
    const rb = rank(b);
    if (ra !== rb) return ra - rb;
    return (Number(b.approvalMin) || 0) - (Number(a.approvalMin) || 0);
  });
}

/** ❗トレイに出す最優先の1件（attentionQueue の先頭）。 */
export function topAttention(agents) {
  return attentionQueue(agents)[0] || null;
}

/** 経過秒 → 短い相対表示。DOMにも時刻にも触らない純関数（呼び側が generatedAt 基準で秒を渡す）。 */
export function agoStr(seconds, lang = "ja") {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 90) return lang === "en" ? "now" : "たった今";
  const m = Math.round(s / 60);
  if (m < 60) return lang === "en" ? `${m}m ago` : `${m}分前`;
  const h = Math.round(m / 60);
  if (h < 48) return lang === "en" ? `${h}h ago` : `${h}時間前`;
  const d = Math.round(h / 24);
  return lang === "en" ? `${d}d ago` : `${d}日前`;
}

/** office_json → 「意味」のダンプ。両スタイルがこの1関数を使うので dumpWorld() は必ず一致する。 */
export function summarizeWorld(office) {
  if (!office || typeof office !== "object") return null;
  const w = buildWorld(office);
  return {
    officeName: w.officeName,
    lang: w.lang,
    edition: w.edition,
    counts: office.counts ?? null,
    zones: w.counts,
    agents: w.agents.map((a) => ({
      id: a.id,
      session: a.session,
      disp: a.name,
      crew: a.crew,
      state: a.state,
      zone: a.zone,
      seat: w.seats.has(a.id) ? w.seats.get(a.id) : null,
      attention: a.attention,
      pending: a.pending,
      minions: a.minions,
      external: a.external,
    })),
  };
}
