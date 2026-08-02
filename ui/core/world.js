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
export const QUEUE_SLOTS = 12;  // R70: 机12台ぶん＝❗が大量でも2列×6で整列（ユーザーFB）
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
    stuckTool: p.stuckTool || "",
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

/**
 * ターミナルの生ログ断片を「人間が読む1文」へ整える純関数（R53提案#1）。
 * - マークダウン残骸（バッククォート・強調）を除去
 * - パスらしきトークンは basename だけに（「やること/…_20260731.md」→ファイル名）
 * - 閉じられていない開き括弧以降（サーバー側の文字数切りで途切れた断片）をカット
 * - max 文字で省略（既定60）
 */
export function tidyActivity(s, max = 60) {
  let t = String(s ?? "").replace(/[`*]+/g, "");
  // 見出し/引用のマークダウン記号（実データで「指示待ち # 🎬 …」が出た＝R54自己レビュー）
  t = t.replace(/(^|\s)[#>]{1,3}\s+/g, "$1");
  // パス→basename（スラッシュ区切り2要素以上・URLは触らない）
  t = t.replace(/(^|[\s（(「\[])((?:[^\s／/（）()「」\[\]]+\/){1,}[^\s（）()「」\[\]]+)/g,
    (m, pre, path) => path.includes("://") ? m : pre + path.split("/").pop());
  // 対応の取れない開き括弧＝途切れ断片。最初の「余り開き括弧」の位置で切る
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

/** 名札の下に出す1行。「何をしているか」が常に見えていること（吹き出しは演出、これは事実）。 */
export function activityText(p) {
  if (!p) return "";
  return tidyActivity([p.verb, p.target].filter(Boolean).join(" ").trim());
}

// ── R60: 「今何してます?」の一言要約（一覧=要約・シート=詳細 の二層化） ──
// ターミナル語をそのまま貼らず、人間が読むカテゴリ一言に畳む。判定は決定論ルール
// （LLM呼び出しなし＝プライバシー/速度/決定論の掟）。core層なので文言はlang引数分岐
//（strings.jsはiso層＝coreからimportできない。agoStrと同じ流儀）。
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

/**
 * エージェントの「今何してます?」一言。優先順:
 * ①work.now（タスク管理由来＝既に人間語の作業名）を最優先
 * ②verb×対象のパターンでカテゴリ判定（ja/enどちらのverbでも判定できる）
 * ③どれにも当たらなければ tidy 済みの生1行（従来表示）へフォールバック
 * 生のコマンド/パスはシート（詳細面）だけに出す、が使い分けの掟。
 */
export function activityGloss(a, lang = "ja") {
  if (!a) return "";
  const L = (key) => (GLOSS[key] ? GLOSS[key][lang === "en" ? "en" : "ja"] : "");
  const now = Array.isArray(a.work?.now) ? a.work.now.find((s) => s && s.trim()) : "";
  if (now) return "📋 " + tidyActivity(now, 42);
  if (a.state === "resting") return L("resting");
  const verb = String(a.verb || "").trim();
  const raw = `${verb} ${a.target || ""}`.trim();
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
  const tidied = tidyActivity(raw, 42);
  return tidied || (a.state === "working" ? L("run") : L("waiting"));
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

/**
 * R59: 休憩スポットの分散割当。「全員が同じラウンジに溜まる」のを避けるため、
 * id のハッシュで希望スポットを決め、衝突時だけ線形に空きを探す（assignSeats と同じ流儀・
 * 乱数も時刻も使わない決定論）。spots は nav.js の REST_SPOTS（area/容量は配列が表す）。
 * 戻り値: Map(agentId → spots のindex)。あふれたら希望位置に重なって座る（従来より悪化しない）。
 */
/**
 * R70: 会議の3室分散。複数プロジェクトが同時に会議中のとき meet/meet2/meet3 へ
 * 「いちばん空いている部屋」を選んで散らす（assignRestSpots と同じ流儀・決定論）。
 * rooms = {meet: 席数, meet2: 席数, ...}（office.js の meetingAnchorsByRoom から導出）。
 * 戻り値: Map(agentId → {room, seat})。seat は部屋内の席index（あふれは最終席に重なる）。
 */
export function assignMeetingRooms(agents, rooms) {
  const out = new Map();
  const keys = Object.keys(rooms || {});
  if (!keys.length) return out;
  const used = keys.map(() => 0);
  const meeting = (Array.isArray(agents) ? agents : []).filter((a) => zoneOf(a) === "meeting");
  meeting.forEach((a, i) => {
    const id = a.id || a.session || "";
    const start = (stableIndex(id, keys.length) + i) % keys.length;
    let pick = -1;
    for (let step = 0; step < keys.length; step++) {
      const cand = (start + step) % keys.length;
      if (used[cand] >= rooms[keys[cand]]) continue;  // 満席
      if (pick < 0 || used[cand] < used[pick]) pick = cand;  // 最空き（タイ=start起点順）
    }
    if (pick < 0) pick = start;                       // 全室満席→希望部屋に重なる（悪化させない）
    const seat = Math.min(used[pick], rooms[keys[pick]] - 1);
    used[pick] += 1;
    out.set(a.id, { room: keys[pick], seat });
  });
  return out;
}

export function assignRestSpots(agents, spots) {
  const out = new Map();
  if (!Array.isArray(spots) || !spots.length) return out;
  // エリア（=席のグループ）を初出順に組む
  const areas = [];
  const areaOf = new Map();
  spots.forEach((s, i) => {
    const key = (s && s.area) || "";
    if (!areaOf.has(key)) { areaOf.set(key, areas.length); areas.push([]); }
    areas[areaOf.get(key)].push(i);
  });
  const taken = new Set();
  const used = areas.map(() => 0);
  const resting = (Array.isArray(agents) ? agents : []).filter((a) => zoneOf(a) === "lounge");
  resting.forEach((a, i) => {
    const id = a.id || a.session || "";
    // R62: 「いちばん空いているエリア」を選ぶ＝1箇所に溜まらない（ハッシュ希望席だけだと
    // 偏りがそのまま固まる＝ユーザーFB「手前のソファに常駐しまくり」の原因）。
    // 同数タイは (ハッシュ + 並び順) を起点に走査してずらす＝決定論のまま散る。
    const start = (stableIndex(id, areas.length) + i) % areas.length;
    let pick = -1;
    for (let k = 0; k < areas.length; k++) {
      const ai = (start + k) % areas.length;
      if (used[ai] >= areas[ai].length) continue;          // 満席のエリアは飛ばす
      if (pick < 0 || used[ai] < used[pick]) pick = ai;
    }
    if (pick < 0) {                                        // 全席満席＝希望位置に重なる
      out.set(a.id, stableIndex(id, spots.length));
      return;
    }
    const seats = areas[pick];
    const want = stableIndex(id, seats.length);
    let idx = -1;
    for (let k = 0; k < seats.length; k++) {
      const cand = seats[(want + k) % seats.length];
      if (!taken.has(cand)) { idx = cand; break; }
    }
    if (idx < 0) idx = seats[want];
    taken.add(idx);
    used[pick] += 1;
    out.set(a.id, idx);
  });
  return out;
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

/**
 * 前回worldとの差分から「配達の手応え」を検出する純関数（R53.2）。
 * woke     = 📨投函済みが消えた（hookが配達しエージェントが受け取った）
 * answered = ❗が解消した（回答が実セッションに反映された）
 * 新規出現エージェントは対象外（初回データで誤発火しない）。
 */
export function deliveryTransitions(prevAgents, agents) {
  const prev = new Map((Array.isArray(prevAgents) ? prevAgents : [])
    .map((a) => [a.session, a]));
  const out = [];
  for (const a of (Array.isArray(agents) ? agents : [])) {
    const p = prev.get(a.session);
    if (!p) continue;
    if (p.pending && !a.pending) {
      out.push({ session: a.session, name: a.name, kind: "woke" });
    } else if (p.attention && !a.attention) {
      out.push({ session: a.session, name: a.name, kind: "answered" });
    }
  }
  return out;
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
