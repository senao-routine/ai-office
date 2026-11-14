// R99 Phase 3: 俯瞰のコマ表（ui/core/pxtop.js）とドット（ui/cockpit/cells.js・tiles.js）の**完全性の門**。
// core は cockpit を import できない（tools/js_layer_lint.py）ので、両方を知ってよい tests/ に置く。
//
// 見ること:
//   1. PX_CELLS は vendor → cell の入れ子で、TOP_VENDORS × TOP_CELLS の全部が**別名なしで**在る
//      （描画側の「無ければ既定のコマ」逃げで、描いていないコマが黙って別の絵になるのを許さない）
//   2. 各コマ 16 行 × 12 文字・凡例は PALETTE のキー＋ a / c ＋透明 . だけ
//   3. topPose が返しうるコマの全集合が、どのベンダーの PX_CELLS にも在る
//   4. 頭上の印のタイルが契約の寸法（**正本は ui/core/pxtop.js の TOP_MARK_SIZE**・ここに数字を書き写さない）で在る
//   5. 読書の語彙（pxtop の TOP_READ_VERBS）が server/office_server.py の TOOL_VERB / TOOL_VERB_EN と食い違わない
//   6. 固定ワールド（tests/fixtures/world/cockpit.json・avatarMode=session と project の両方）を本番と同じ経路
//      （world → 台帳 departmentBoard → roomLayout → stepWalkers → roomMark / topPose / topMarkAt）で通すと、
//      読書・⚠・💬・💭・❗ が 1 件以上出て、描けないコマ（missingCells）が 0 件。次のフレームで報告した人に ✓ が出る
//      （actors が材料＝verb・stuckTool・age・key を落とすと、本番で印が 1 件も出ないのに赤にならない＝その門）
// 歩行の隣のコマの差・ベンダー同士の差（ドットの数の契約）は tools/px_lint.py --cockpit の ③ が正本（全ペア・折り返し
// まで見る）＝ここでは数えない（数字を 2 か所に持たない）。絵の良し悪しは見ない（コンタクトシートで本人が裁定する）。
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import * as CELLS from "../ui/cockpit/cells.js";
import { PX_TILES } from "../ui/cockpit/tiles.js";
import { PALETTE, TILE_PALETTE } from "../ui/cockpit/palette.js";
import {
  TOP_ARCH_SIZE, TOP_ARCH_TILES, TOP_CELLS, TOP_DESK_SIZE, TOP_DESK_TILES, TOP_DIRS, TOP_MARKS, TOP_MARK_SIZE, TOP_MARK_TILES,
  TOP_READ_TOOLS, TOP_READ_VERBS, TOP_VENDORS, roomMark, topMarkAt, topPose, topPropAt, topVendor,
} from "../ui/core/pxtop.js";
import { ERRAND_SPOTS, roomLayout } from "../ui/core/pxroom.js";
import { ERRAND, ERRAND_SPOT_OF, EVIDENCE_ERRAND, errandTargets, toolClassOf } from "../ui/core/errand.js";
import { stepWalkers } from "../ui/core/pxwalk.js";
import { buildWorld } from "../ui/core/world.js";

const root = new URL("../", import.meta.url);
// ui/hud は「/ui/...」の絶対指定子で import する（ブラウザと同じ）＝node ではリポジトリ根へ読み替える
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
const { departmentBoard } = await import("../ui/hud/board.js");

const { PX_CELLS } = CELLS;
const FLAT_HINT = "PX_CELLS が平らな形（ベンダーの入れ子でない）＝ design/gen_cells.py の表化（3 ベンダー × 24 コマ）待ち";

test("cockpit cells: 寸法の定数とベンダーの並び（契約）", () => {
  assert.equal(CELLS.PX_CELL_W, 12);
  assert.equal(CELLS.PX_CELL_H, 16);
  assert.ok(Array.isArray(CELLS.PX_VENDORS), `PX_VENDORS が export されていない（${FLAT_HINT}）`);
  assert.deepEqual([...CELLS.PX_VENDORS], [...TOP_VENDORS]);
});

test("cockpit cells: TOP_VENDORS × TOP_CELLS の全部が PX_CELLS[vendor][cell] に別名なしで在る", () => {
  assert.ok(PX_CELLS && typeof PX_CELLS === "object", "PX_CELLS が無い");
  assert.ok(!Object.hasOwn(PX_CELLS, "idle0"), FLAT_HINT);
  const missing = [];
  for (const v of TOP_VENDORS) {
    const book = Object.hasOwn(PX_CELLS, v) ? PX_CELLS[v] : null;
    if (!book || typeof book !== "object") { missing.push(`${v}（ベンダーごと無い）`); continue; }
    for (const c of TOP_CELLS) if (!Object.hasOwn(book, c)) missing.push(`${v}.${c}`);
  }
  assert.deepEqual(missing, [], `描いていないコマ: ${missing.join(" ")}`);
  for (const v of TOP_VENDORS) {
    // 名前と順は契約どおり（余分なコマも無い）。値は配列そのもの（名前の文字列＝別名・他のコマの参照ではない）
    assert.deepEqual(Object.keys(PX_CELLS[v]), [...TOP_CELLS], `${v}: コマの名前と順が契約（TOP_CELLS）と違う`);
    const refs = new Set();
    for (const c of TOP_CELLS) {
      const g = PX_CELLS[v][c];
      assert.ok(Array.isArray(g), `${v}.${c}: 文字グリッドの配列でない（別名は許さない）`);
      assert.ok(!refs.has(g), `${v}.${c}: 他のコマと同じ配列を共有している（別名）`);
      refs.add(g);
    }
  }
  assert.deepEqual(Object.keys(PX_CELLS), [...TOP_VENDORS], "PX_CELLS のベンダーが TOP_VENDORS と違う");
  // ベンダー同士で同じ表を共有していない（codex が claude の絵をそのまま使う、を許さない）
  assert.equal(new Set(TOP_VENDORS.map((v) => PX_CELLS[v])).size, TOP_VENDORS.length);
});

test("cockpit cells: 各コマ 16 行 × 12 文字・凡例は PALETTE のキー＋ a / c ＋透明だけ", () => {
  assert.ok(!Object.hasOwn(PX_CELLS, "idle0"), FLAT_HINT);
  const legend = new Set([".", ...Object.keys(PALETTE), "a", "c"]);
  const bad = [];
  for (const v of TOP_VENDORS) {
    for (const c of TOP_CELLS) {
      const g = PX_CELLS[v]?.[c];
      if (!Array.isArray(g)) { bad.push(`${v}.${c}: 無い`); continue; }
      if (g.length !== CELLS.PX_CELL_H) bad.push(`${v}.${c}: ${g.length} 行`);
      g.forEach((row, y) => {
        if (typeof row !== "string" || row.length !== CELLS.PX_CELL_W) bad.push(`${v}.${c}[${y}]: 幅 ${row?.length}`);
        else for (const ch of row) if (!legend.has(ch)) bad.push(`${v}.${c}[${y}]: 凡例に無い「${ch}」`);
      });
    }
  }
  assert.deepEqual(bad, []);
});

test("cockpit cells: topPose が返しうるコマの全集合が、どのベンダー（未知は claude）の PX_CELLS にも在る", () => {
  assert.ok(!Object.hasOwn(PX_CELLS, "idle0"), FLAT_HINT);
  const reach = new Set();
  const states = ["idle", "working", "waiting", "resting"];
  const zones = ["desk", "lounge", "meeting", "queue", "standby", "external"];
  const kinds = ["idle", "tool", "think", "said", "order"];
  const verbs = ["", ...TOP_READ_VERBS.ja, ...TOP_READ_VERBS.en, "実行中"];
  const times = Array.from({ length: 17 }, (_, i) => i * 0.25);
  for (const state of states) for (const zone of zones) for (const kind of kinds) for (const verb of verbs) {
    for (const attention of [false, true]) for (const done of [false, true]) for (const t of times) {
      reach.add(topPose({ state, zone, kind, verb, attention }, t, { done }).cell);
    }
  }
  for (const dir of TOP_DIRS) for (let i = 0; i < 8; i += 1) reach.add(topPose({}, 0, { walk: { phase: i / 8, dir } }).cell);
  assert.deepEqual([...reach].sort(), [...TOP_CELLS].sort(), "topPose が全コマに届かない／表に無いコマを返す");
  const missing = [];
  for (const v of [...TOP_VENDORS, "other", undefined, "gemini"]) {
    const book = PX_CELLS[topVendor(v)];
    for (const c of reach) if (!book || !Object.hasOwn(book, c)) missing.push(`${String(v)}→${topVendor(v)}.${c}`);
  }
  assert.deepEqual(missing, []);
});

test("cockpit tiles: 頭上の印のタイルが契約の寸法（pxtop の TOP_MARK_SIZE）で在る（凡例は TILE_PALETTE ＋透明だけ）", () => {
  assert.deepEqual(Object.values(TOP_MARK_TILES).sort(), Object.keys(TOP_MARK_SIZE).sort());
  const legend = new Set([".", ...Object.keys(TILE_PALETTE)]);
  const bad = [];
  for (const [name, [w, h]] of Object.entries(TOP_MARK_SIZE)) {
    const g = Object.hasOwn(PX_TILES, name) ? PX_TILES[name] : null;
    if (!Array.isArray(g)) { bad.push(`${name}: 無い（design/gen_tiles.py の印のタイル待ち）`); continue; }
    if (g.length !== h) bad.push(`${name}: 高さ ${g.length}（契約 ${h}）`);
    g.forEach((row, y) => {
      if (typeof row !== "string" || row.length !== w) bad.push(`${name}[${y}]: 幅 ${row?.length}（契約 ${w}）`);
      else for (const ch of row) if (!legend.has(ch)) bad.push(`${name}[${y}]: 凡例に無い「${ch}」`);
    });
    if (g.every((row) => /^\.*$/.test(row))) bad.push(`${name}: 全部透明`);
  }
  assert.deepEqual(bad, []);
});

test("cockpit tiles（R110-S2）: 職業の小道具と机の小物のタイルが契約の寸法（pxtop の TOP_ARCH_SIZE・TOP_DESK_SIZE）で在る", () => {
  const legend = new Set([".", ...Object.keys(TILE_PALETTE)]);
  const bad = [];
  for (const [name, [w, h]] of [...Object.entries(TOP_ARCH_SIZE), ...Object.entries(TOP_DESK_SIZE)]) {
    const g = Object.hasOwn(PX_TILES, name) ? PX_TILES[name] : null;
    if (!Array.isArray(g)) { bad.push(`${name}: 無い（design/gen_tiles.py の R110 S2 の作画待ち）`); continue; }
    if (g.length !== h) bad.push(`${name}: 高さ ${g.length}（契約 ${h}）`);
    g.forEach((row, y) => {
      if (typeof row !== "string" || row.length !== w) bad.push(`${name}[${y}]: 幅 ${row?.length}（契約 ${w}）`);
      else for (const ch of row) if (!legend.has(ch)) bad.push(`${name}[${y}]: 凡例に無い「${ch}」`);
    });
    if (g.every((row) => /^\.*$/.test(row))) bad.push(`${name}: 全部透明`);
  }
  assert.deepEqual(bad, []);
  // 全ベンダー・全コマで、頭の物は頭の上（セルの外へ 2 行まで）・胸の物はセルの中に置く（どこにも置けない人を作らない）
  for (const v of TOP_VENDORS) for (const c of TOP_CELLS) {
    for (const prop of Object.keys(TOP_ARCH_TILES)) {
      const at = topPropAt([0, 0], prop, PX_CELLS[v][c]);
      assert.ok(at, `${v}.${c}: ${prop} を置けない`);
      assert.ok(at.y >= -2 && at.y + at.h <= 16 && at.x >= 0 && at.x + at.w <= 12, `${v}.${c}: ${prop} がセルから外れた（${JSON.stringify(at)}）`);
    }
  }
});

// ── サーバーの動詞の表との突き合わせ ─────────────────────────────────────
const SERVER = readFileSync(new URL("../server/office_server.py", import.meta.url), "utf8");

/** server/office_server.py の `NAME = { "Tool": "動詞", ... }` を文字列で読む（Python は起動しない） */
function serverTable(name) {
  const m = SERVER.match(new RegExp(`^${name}\\s*=\\s*\\{([\\s\\S]*?)^\\}`, "m"));
  assert.ok(m, `server/office_server.py に ${name} の表が見つからない`);
  const table = {};
  for (const [, k, v] of m[1].matchAll(/"([^"]+)"\s*:\s*"([^"]*)"/g)) table[k] = v;
  assert.ok(Object.keys(table).length >= 10, `${name} の表を読めていない（${Object.keys(table).length} 件）`);
  return table;
}

test("読書の語彙: pxtop の TOP_READ_VERBS がサーバーの TOOL_VERB / TOOL_VERB_EN と一致する", () => {
  for (const [name, lang] of [["TOOL_VERB", "ja"], ["TOOL_VERB_EN", "en"]]) {
    const table = serverTable(name);
    const want = new Set(TOP_READ_VERBS[lang]);
    // 読むツールの動詞の集合＝ pxtop の語彙（サーバーが「読込中」を別の字面に変えたら、ここで落ちる）
    const got = new Set();
    for (const tool of TOP_READ_TOOLS) {
      assert.ok(Object.hasOwn(table, tool), `${name} に ${tool} が無い（読むツールの表を直す）`);
      got.add(table[tool]);
    }
    assert.deepEqual([...got].sort(), [...want].sort(), `${name}: 読むツールの動詞が pxtop の語彙と違う`);
    // 読まないツールが読む動詞を名乗っていない（書く・走らせるのに読書のコマになる、を許さない）
    const leak = Object.entries(table).filter(([tool, verb]) => !TOP_READ_TOOLS.includes(tool) && want.has(verb));
    assert.deepEqual(leak, [], `${name}: 読まないツールが読む動詞を使っている`);
  }
});

// ── 固定ワールドを本番の経路で通す（台帳 → roomLayout → stepWalkers → roomMark / topPose / topMarkAt）──────────
const FIXTURE = JSON.parse(readFileSync(new URL("fixtures/world/cockpit.json", import.meta.url), "utf8"));
const T = (key, ...args) => `${key}${args.length ? ":" + args.join(",") : ""}`;

/** サーバーの group_by_project（mode=project＝cwd 単位に集約）で roster を作り直す（Python は 1 回だけ起動） */
let projectRoster = null;
function rosterProjectMode(employees) {
  if (projectRoster) return projectRoster;
  const script = `
import importlib.util, json, os, shutil, sys, tempfile
home = tempfile.mkdtemp(prefix="cp_cells_")
os.environ["OFFICE_HOME"] = home
try:
    spec = importlib.util.spec_from_file_location("office_server_cells", sys.argv[1])
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    emps = json.load(sys.stdin)
    for e in emps:
        e.pop("evidence", None)   # scan_office は roster を作った後に employees へだけ付ける
    print(json.dumps(srv.group_by_project(emps, "ja", mode="project"), ensure_ascii=False))
finally:
    shutil.rmtree(home, ignore_errors=True)
`;
  projectRoster = JSON.parse(execFileSync("python3", ["-c", script, fileURLToPath(new URL("server/office_server.py", root))],
    { input: JSON.stringify(employees), encoding: "utf8" }));
  return projectRoster;
}

/** avatarMode ごとの office_json（session＝fixture そのまま／project＝サーバーに roster を集約させたもの） */
function officeOf(mode, employees = FIXTURE.employees) {
  if (mode === "session") return { ...FIXTURE, employees };
  return { ...FIXTURE, employees, avatarMode: "project", roster: rosterProjectMode(FIXTURE.employees) };
}

/**
 * 1 フレームぶん、本番（ui/cockpit/room.js）と同じ順に呼ぶ。印とコマの数・描けないコマ・印のタイルの食い違いを返す。
 * frame = {book, memo}（前回の帳簿と印の記録）を持ち回す。
 */
function runFrame(office, t, frame, live) {
  const world = buildWorld(office);
  const board = departmentBoard(office, world, T);
  const lay = roomLayout(world, board, frame.assign || null);
  const walk = stepWalkers(frame.book || null, lay.actors, t, { live });
  let memo = frame.memo;
  const marks = {};
  const kinds = {};
  const missing = [];
  const badTile = [];
  const who = {};
  for (const d of walk.drawn) {
    const m = roomMark(d, t, memo, { live });
    memo = m.memo;
    const pose = topPose(d, t, { walk: d.walk, done: m.mark === "done" });
    kinds[pose.kind] = (kinds[pose.kind] || 0) + 1;
    const book = PX_CELLS[topVendor(d.vendor)];
    if (!book || !Object.hasOwn(book, pose.cell)) missing.push(`${d.key}: ${topVendor(d.vendor)}.${pose.cell}`);
    if (m.mark) {
      marks[m.mark] = (marks[m.mark] || 0) + 1;
      const at = topMarkAt(d, m.mark);
      const g = Object.hasOwn(PX_TILES, at.tile) ? PX_TILES[at.tile] : null;
      if (!g || g.length !== at.h || g[0].length !== at.w) badTile.push(`${d.key}: ${at.tile} ${at.w}×${at.h}`);
    }
    who[d.session] = { mark: m.mark, kind: pose.kind };
  }
  return { frame: { book: walk.walkers, memo, assign: lay.assign }, lay, marks, kinds, missing, badTile, who };
}

for (const mode of ["session", "project"]) {
  test(`固定ワールド（avatarMode=${mode}）: 読書・⚠・💬・💭・❗ が 1 件以上出て、描けないコマが 0 件`, () => {
    const office = officeOf(mode);
    const r = runFrame(office, 100, {}, false);
    assert.equal(r.lay.overflow, 0, "前提: fixture の全員が部屋に入る");
    assert.ok(r.lay.actors.length >= 14, `前提: 部屋に ${r.lay.actors.length} 人`);
    // 印（✓ は遷移が要る＝次のテスト）
    for (const m of ["call", "warn", "wait", "think"]) {
      assert.ok((r.marks[m] || 0) > 0, `印 ${m} が 0 件（材料が roomLayout → pxtop へ届いていない）: ${JSON.stringify(r.marks)}`);
    }
    // コマ（読書・挙手・休憩・打鍵・止まり）
    for (const k of ["read", "raise", "rest", "type", "idle"]) {
      assert.ok((r.kinds[k] || 0) > 0, `コマ ${k} が 0 件: ${JSON.stringify(r.kinds)}`);
    }
    assert.deepEqual(r.missing, [], "描けないコマ（missingCells）がある");
    assert.deepEqual(r.badTile, [], "印のタイルが TOP_MARK_SIZE の寸法で PX_TILES に無い");
    // 印は台帳の行の様子どおり（⚠＝ツールが長く止まっている人・💬＝報告して 600 秒未満・❗＝確認待ち）
    assert.equal(r.who["sess-cp-08"].mark, "warn", "ツールで 960 秒止まっている人に ⚠ が出ない");
    assert.equal(r.who["sess-cp-07"].mark, "call", "承認まち（stuckTool あり）は ❗ が勝つ");
    assert.equal(r.who["sess-cp-05"].mark, "wait");
    assert.equal(r.who["sess-cp-06"].mark, "think");
    assert.equal(r.who["sess-cp-04"].kind, "read");
    // actors の key は部屋の中で一意（歩行の帳簿と印の記録はこれで人を追う）
    const keys = r.lay.actors.map((a) => a.key);
    assert.equal(new Set(keys).size, keys.length, `key が重複: ${keys}`);
  });

  test(`固定ワールド（avatarMode=${mode}）: 次のフレームで working → 報告して待機 に変わった人だけに ✓（live のときだけ）`, () => {
    const t0 = 100;
    const first = runFrame(officeOf(mode), t0, {}, true);
    assert.equal(first.who["sess-cp-01"].mark, null, "前提: 働いている人に印");
    const said = FIXTURE.employees.map((e) => (e.session === "sess-cp-01"
      ? { ...e, state: "waiting", kind: "said", verb: "指示待ち", target: "", age: 30 } : e));
    const next = runFrame(officeOf(mode, said), t0 + 0.5, first.frame, true);
    assert.equal(next.who["sess-cp-01"].mark, "done", "報告した人に ✓ が出ない（key か材料が途中で落ちた）");
    assert.equal(next.who["sess-cp-01"].kind, "done");
    assert.equal(next.marks.done, 1, `✓ が ${next.marks.done} 件（報告した 1 人だけのはず）`);
    // 開いた瞬間（初回）は誰にも ✓ を出さない・live でない（再生・固定時刻）ときも出さない
    assert.ok(!first.marks.done);
    const replay = runFrame(officeOf(mode, said), t0 + 0.5, first.frame, false);
    assert.ok(!replay.marks.done, "live でないのに ✓");
    // 8 秒で消える（💬 の窓の中なので 💬 に替わる）
    let f = next.frame;
    let last = next;
    for (let t = t0 + 1; t <= t0 + 9; t += 0.5) {
      last = runFrame(officeOf(mode, said), t, f, true);
      f = last.frame;
    }
    assert.equal(last.who["sess-cp-01"].mark, "wait");
  });
}


// ── R110-S5: 固定ワールドで用事が出る（台帳 → roomLayout → errandTargets）・R110-S2: 小道具の材料 ──────────────
/** 固定ワールドを本番の経路で並べた actors（用事の材料＝tool・evidenceAt・prop を運んでいるか） */
function roomActors(office) {
  const world = buildWorld(office);
  return roomLayout(world, departmentBoard(office, world, T), null);
}

for (const mode of ["session", "project"]) {
  test(`固定ワールド（avatarMode=${mode}・R110-S5）: 道具の 3 種（読む→本棚・Web→掲示板・段取り→ボード）が各 1 人以上・用事が出る`, () => {
    const lay = roomActors(officeOf(mode));
    const classes = new Map();
    for (const a of lay.actors) {
      const cls = a.kind === "tool" && a.zone === "desk" && a.state === "working" ? toolClassOf(a) : null;
      if (cls) classes.set(cls, (classes.get(cls) || 0) + 1);
    }
    for (const cls of Object.keys(ERRAND_SPOT_OF)) assert.ok(classes.get(cls) > 0, `用事の種類 ${cls} の人が居ない: ${JSON.stringify([...classes])}`);
    assert.ok(lay.actors.every((a) => typeof a.tool === "string"), "tool を運んでいない");
    // tool はサーバーの最後の工具の名前そのもの（台帳の行 → withKindFields → roomLayout）。動詞からの逆引きで済ませていない
    const toolOf = new Map(lay.actors.map((a) => [a.session, a.tool]));
    assert.deepEqual(["sess-cp-04", "sess-cp-11", "sess-cp-14", "sess-cp-12"].map((k) => toolOf.get(k)),
      ["Read", "WebFetch", "TodoWrite", "Bash"], "固定ワールドの tool が部屋の actors まで届いていない");
    // 同じ道具が続く人は repeat 秒以降の格子で 1 回ずつ出る（決定的ハッシュ）。上限 2・同じ立ち位置に 2 人立たない
    const spot = new Map(ERRAND_SPOTS.map((p) => [p.name, `${p.x},${p.y}`]));
    let book = null;
    const went = new Map();
    for (let t = 40; t < 40 + ERRAND.repeat * 3; t += 1) {
      const r = errandTargets(lay.actors, t, { live: true, prev: book, spots: ERRAND_SPOTS });
      book = r.book;
      assert.ok(r.away <= ERRAND.maxAway, `t=${t}: ${r.away} 人が同時に出た`);
      const at = new Set();
      for (const a of r.actors) {
        if (!a.errand) continue;
        assert.equal(`${a.x},${a.y}`, spot.get(a.errand), `${a.key} の目的地が行き先 ${a.errand} でない`);
        assert.ok(!at.has(a.errand), `t=${t}: ${a.errand} に 2 人`);
        at.add(a.errand);
        if (!went.has(a.errand)) went.set(a.errand, new Set());
        went.get(a.errand).add(a.key);
      }
    }
    for (const name of Object.values(ERRAND_SPOT_OF)) assert.ok(went.get(name)?.size > 0, `${name} へ誰も行かなかった: ${JSON.stringify([...went.keys()])}`);
  });

  test(`固定ワールド（avatarMode=${mode}・R110-S5）: 新しい証拠（テストの成功）が届いた人がカウンターへ 1 回行く`, () => {
    const office = officeOf(mode);
    let book = null;
    for (let t = 40; t <= 42; t += 1) book = errandTargets(roomActors(office).actors, t, { live: true, prev: book, spots: ERRAND_SPOTS }).book;
    // 次のスナップショット（30 秒後）で sess-cp-01 に新しい成功（ago 1）＝出来事の時刻が前の証拠より新しい
    const employees = office.employees.map((e) => (e.session === "sess-cp-01" ? { ...e, evidence: { kind: "tested", ago: 1 } } : e));
    const fresh = { ...officeOf(mode, employees), generatedAt: office.generatedAt + 30 };
    const r = errandTargets(roomActors(fresh).actors, 43, { live: true, prev: book, spots: ERRAND_SPOTS });
    const went = r.actors.filter((a) => a.errand === EVIDENCE_ERRAND.spot);
    assert.deepEqual(went.map((a) => a.session), ["sess-cp-01"],
      `カウンターへ行ったのが証拠の人だけでない: ${JSON.stringify(r.actors.filter((a) => a.errand).map((a) => [a.key, a.errand]))}`);
    // 古い証拠（sess-cp-01 の 180 秒前の成功・sess-cp-05 の 900 秒前の commit）では初めの 3 秒に誰も行かない
    assert.equal(errandTargets(roomActors(office).actors, 43, { live: true, prev: book, spots: ERRAND_SPOTS }).away, 0);
  });

  test(`固定ワールド（avatarMode=${mode}・R110-S2）: 職業の小道具が名前・役割・cwd の末尾から付く（dev・該当なしは付けない）`, () => {
    const lay = roomActors(officeOf(mode));
    const props = lay.actors.map((a) => a.prop);
    assert.ok(props.every((p) => typeof p === "string"), "prop を運んでいない");
    const shown = new Set(props.filter((p) => Object.hasOwn(TOP_ARCH_TILES, p)));
    assert.ok(shown.size >= 3, `小道具の職業が ${shown.size} 種しかない: ${[...shown]}`);
    assert.ok(props.some((p) => Object.hasOwn(TOP_DESK_TILES, p)), "机の小物の職業が居ない");
    const by = new Map(lay.actors.map((a) => [a.session, a.prop]));
    assert.equal(by.get("sess-cp-06"), "video", "動画編集部が監督のベレーでない");
    assert.equal(by.get("sess-cp-01"), "dev", "atlas-api（開発）に小道具の職業が付いた");
  });
}
