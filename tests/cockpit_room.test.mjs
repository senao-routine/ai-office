// R99: 部屋（ui/cockpit/room.js）を node で直接確かめる。前半は「席の割り当ての帳簿」、後半（第 2 波）は
// 1 フレームの配線（roomLayout → stepWalkers → roomMark → topPose → 足元の y で人と手前の什器の段を交互に → 印 → 選択・ホバー）。
//
// 前半: 帳簿。
// live と再生（ダイジェストの巻き戻し）で前回の割り当てを別々に持つこと:
//   - 再生の割り当てを live へ持ち帰らない（閉じた瞬間に全員が再生時の席へ飛ぶ）
//   - live の割り当てを再生へ持ち込まない（再生の初回は前回なし＝過去の部屋を「今の席」で描かない）
// スモーク（tests/cockpit_smoke.py の 15）は前者しか捕まえられない（後者は再生中の配置の正解を外から出せない）。
// そこで後者は、帳簿そのもの（assignBook）と、描画の呼び出し口（init().draw の { replay } が帳簿まで届くか）の
// 2 段で見る。room.js は相対パスで import する（R102-10・スマホは版つきの道 /u/<BUILD>/ で読む）。/ui/... の絶対パスで読むほかの
// 部品のために、解決先をリポジトリへ向ける。
// DOM は init の中でしか触らない＝draw の門は、canvas の口だけ持つ偽の document で回す（絵は見ない）。
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, next) {
  return next(specifier.startsWith("/ui/") ? new URL(specifier.slice(1), root).href : specifier, context);
} });
// 写しの部屋（後半の門）: `room.js?<印>` から読む 1 モジュールだけを、中身を差し替えた写しにすり替える
//（本物の生成物・core は触らない）。すり替えの字面が見つからなければ赤（元の形が変わったら、この門も直す）。
//   ?missing: cells.js から codex の idle0 / idle1 を抜く（描けないコマの門）
//   ?notile : tiles.js から markThink と footstep を抜く（描けないタイルの門＝missingTiles）
//   ?nopath : pxwalk.js の planPath を、globalThis.__cockpitNoPath が真の間だけ null（届かない）にする（unreachable の門）
const VARIANTS = {
  missing: { spec: "./cells.js", file: "cells.js", from: "export const PX_CELLS = Object.freeze({",
    to: "const REAL_CELLS = Object.freeze({",
    tail: "\nconst { idle0, idle1, ...codexRest } = REAL_CELLS.codex;"
      + "\nexport const PX_CELLS = Object.freeze({ ...REAL_CELLS, codex: Object.freeze(codexRest) });\n" },
  notile: { spec: "./tiles.js", file: "tiles.js", from: "export const PX_TILES = Object.freeze({",
    to: "const REAL_TILES = Object.freeze({",
    tail: "\nconst { markThink, footstep, ...tileRest } = REAL_TILES;"
      + "\nexport const PX_TILES = Object.freeze(tileRest);\n" },
  nopath: { spec: "../core/pxwalk.js", file: "pxwalk.js", from: "export function planPath(",
    to: "function realPlanPath(",
    tail: "\nexport function planPath(a, b) { return globalThis.__cockpitNoPath ? null : realPlanPath(a, b); }\n" },
};
registerHooks({
  resolve(specifier, context, next) {
    const out = next(specifier, context);
    const parent = String(context.parentURL || "");
    for (const [tag, v] of Object.entries(VARIANTS)) {
      if (specifier === v.spec && parent.includes(`room.js?${tag}`)) return { ...out, url: `${out.url}?${tag}` };
    }
    return out;
  },
  load(url, context, next) {
    const out = next(url, context);
    for (const [tag, v] of Object.entries(VARIANTS)) {
      if (!url.endsWith(`${v.file}?${tag}`)) continue;
      const src = String(out.source);
      if (!src.includes(v.from)) throw new Error(`${v.file} に「${v.from}」が無い（すり替えの門を直す）`);
      return { format: "module", shortCircuit: true, source: src.replace(v.from, v.to) + v.tail };
    }
    return out;
  },
});
const { assignBook, init, footprints, HOVER_LINE, PRINT_STEP, PRINTS_PER_WALKER } =
  await import("../ui/cockpit/room.js");
const { FRONT_BANDS, LIFE_SPOTS, roomLayout, tintHash, tintIndex } = await import("../ui/core/pxroom.js");
const { FOOT_CENTER, RISE_SEC, WALK_SPEED, WALK_STRIDE, planPath, posAt } = await import("../ui/core/pxwalk.js");
const { SETTLE_SEC } = await import("../ui/core/pxtop.js");
const { PX_CELLS, PX_CELL_W, PX_CELL_H } = await import("../ui/cockpit/cells.js");
const { RAMP, SESSION_COLORS } = await import("../ui/cockpit/palette.js");
// 1 フレームの drawCalls の上限の正本は tools/ui_shot.py（ここに数字を書き写さない）
const DRAW_CALL_LIMIT = Number(readFileSync(new URL("../tools/ui_shot.py", import.meta.url), "utf8")
  .match(/^DRAW_CALL_LIMIT\s*=\s*(\d+)/m)?.[1]);

/** 渡された前回を控え、world の tag をそのまま割り当てとして返す配置（帳簿の受け渡しだけを見る） */
function spyBook(keepPrev) {
  const seen = [];
  const book = assignBook(keepPrev, (world, board, prev) => { seen.push(prev); return { assign: world.tag }; });
  return { seen, frame: (tag, replay) => { book.layout({ tag }, [], replay); return seen.at(-1); } };
}
const A = "A", B = "B", C = "C", D = "D";

test("帳簿: live と再生の前回は別々・再生の初回は前回なし・live を描いたら再生の分を捨てる", () => {
  const { frame } = spyBook(true);
  assert.equal(frame(A, false), null);
  assert.equal(frame(B, true), null, "再生の初回に live の割り当てを渡している");
  assert.equal(frame(C, true), B, "再生の 2 フレーム目に再生の前回が渡っていない");
  assert.equal(frame(D, false), A, "再生の割り当てが live 側へ書かれた");
  assert.equal(frame("E", true), null, "再生を出ても再生の割り当てが残っている（次の再生が前回ありで始まる）");
  assert.equal(frame("F", false), D, "再生中も live の前回が残っていない");
});

test("帳簿: keepPrev=false（固定時刻）は常に前回なし", () => {
  const { frame } = spyBook(false);
  for (const [tag, replay] of [[A, false], [B, true], [C, false], [D, true]]) {
    assert.equal(frame(tag, replay), null, `${tag}（replay=${replay}）に前回を渡した`);
  }
});

// 実際の配置で: live の席が「台帳の順に詰めた席」とずれている（机 0 の人が帰った）ときに再生を開いて閉じる。
const person = (i, extra = {}) => ({ id: `p${i}`, session: `s${i}`, state: "working", zone: "desk", ...extra });
const worldOf = (...agents) => ({ agents });
const seats = (actors) => Object.fromEntries(actors.map((a) => [a.session, [a.zone, a.x, a.y]]));
const ALL = worldOf(person(0), person(1), person(2), person(3));
const LEFT = worldOf(person(1), person(2), person(3));                      // s0 が帰った＝机 0 が空く
const CALL = worldOf(person(1, { attention: true, state: "waiting" }), person(2), person(3));

/** 同じ筋書き（live 2 枚 → 再生 2 枚 → live 1 枚）を frame(world, replay) → actors で回す */
function scenario(frame) {
  const { s0, ...stay } = seats(frame(ALL, false));
  const live = seats(frame(LEFT, false));
  const replay0 = seats(frame(LEFT, true));
  frame(CALL, true);
  const back = seats(frame(LEFT, false));
  return { stay, live, replay0, back };
}

function assertScenario({ stay, live, replay0, back }) {
  const fresh = seats(roomLayout(LEFT, [], null).actors);
  assert.deepEqual(live, stay, "前提: live は s0 が帰っても残りの人を動かさない");
  assert.notDeepEqual(fresh, live, "前提: 前回なしの配置と live の配置が違う（同じだと門が空振り）");
  assert.deepEqual(replay0, fresh, "再生の初回が live の席を引き継いでいる（前回なしで始まっていない）");
  assert.deepEqual(back, live, "再生を閉じたら live の席が再生時の席に飛んだ");
}

test("帳簿＋roomLayout: 再生を開いて閉じても live の席は同じ・再生は前回なしの配置から始まる", () => {
  const book = assignBook(true);
  assertScenario(scenario((world, replay) => book.layout(world, [], replay).actors));
});

test("部屋の draw: { replay } の旗が帳簿まで届く（呼び出し口で旗を取り違えると赤）", () => {
  const ctx = () => ({ fillRect() {}, drawImage() {}, clearRect() {} });
  const had = Object.hasOwn(globalThis, "document");
  const saved = globalThis.document;
  globalThis.document = { createElement: () => ({ setAttribute() {}, remove() {}, getContext: ctx }) };
  try {
    const room = init({ host: { append() {} } });
    assertScenario(scenario((world, replay) => { room.draw(world, [], 0, { replay }); return room.actors(); }));
  } finally {
    if (had) globalThis.document = saved;
    else delete globalThis.document;
  }
});

// ── 後半（R99 第 2 波）: 1 フレームの配線 ─────────────────────────────────────
// 偽の document: canvas は「何を塗ったか」を控えるだけ。メインの canvas（init で最初に作る）の控えは clearRect で空にする
//＝控え＝直近の 1 フレームぶん。重なり順は room.layers()（貼る口 put と同じ場所で控える）と、貼った人の今の左上で見る。
function fakeDom() {
  const made = [];
  const createElement = () => {
    const log = [];
    const ctx = { log, fillStyle: "", globalAlpha: 1, imageSmoothingEnabled: false,
      fillRect(x, y, w, h) { log.push(["fill", x, y, w, h, this.fillStyle, this.globalAlpha]); },
      drawImage(img, x, y) { log.push(["img", img, x, y]); },
      clearRect() { log.length = 0; } };
    const c = { width: 0, height: 0, ctx, getContext: () => ctx, setAttribute() {}, remove() {},
      getBoundingClientRect: () => ({ left: 0, top: 0 }) };
    made.push(c);
    return c;
  };
  return { createElement, made };
}

/** 偽の document の上で部屋を作って fn(room, main) を回す（main＝メインの canvas の控え） */
async function withRoom(opts, fn, mod = null) {
  const had = Object.hasOwn(globalThis, "document");
  const saved = globalThis.document;
  const dom = fakeDom();
  globalThis.document = { createElement: dom.createElement };
  try {
    const room = (mod || { init }).init({ host: { append() {} }, ...opts });
    return await fn(room, dom.made[0]);
  } finally {
    if (had) globalThis.document = saved;
    else delete globalThis.document;
  }
}

const who = (i, extra = {}) => ({ id: `p${i}`, session: `s${i}`, state: "working", zone: "desk", vendor: "claude", ...extra });
const room1 = (room, key) => room.marks().find((m) => m.key === key);

/**
 * 重なり順（C3）の門: 段は FRONT_BANDS の順（奥 → 手前）。貼った人は足元の中心の y（左上 y + FOOT_CENTER[1]）が段の depth
 * より小さければ段より先・そうでなければ段より後。印は全部の人と全部の段より後、選択とホバーは印より後、足跡は人と段より先。
 */
function assertC3(room, label) {
  const L = room.layers();
  const at = (name) => L.indexOf(name);
  const bands = FRONT_BANDS.map((b) => [b, at(`band:${b.key}`)]);
  for (const [b, i] of bands) assert.ok(i >= 0, `${label}: 段 ${b.key} を貼っていない`);
  assert.deepEqual(bands.map(([, i]) => i), [...bands.map(([, i]) => i)].sort((x, y) => x - y), `${label}: 段が奥→手前の順でない`);
  const persons = room.actors();
  for (const a of persons) {
    const i = at(`person:${a.key}`);
    assert.ok(i >= 0, `${label}: ${a.key} は actors() に居るのに貼った記録が無い`);
    const footY = a.y + FOOT_CENTER[1];
    for (const [b, bi] of bands) {
      assert.equal(i < bi, footY < b.depth,
        `${label}: ${a.key}（足元 y ${footY}）と段 ${b.key}（depth ${b.depth}）の前後が逆（人 ${i}・段 ${bi}）`);
    }
  }
  const idx = (pre) => L.map((l, i) => (l.startsWith(pre) ? i : -1)).filter((i) => i >= 0);
  const solid = [...bands.map(([, i]) => i), ...idx("person:")];
  const marks = idx("mark:");
  for (const i of marks) assert.ok(i > Math.max(...solid), `${label}: 印 ${L[i]} が人か手前の什器より先に貼られた（机に隠れる）`);
  for (const i of [...idx("select:"), ...idx("hover:")]) {
    assert.ok(i > Math.max(...solid, ...marks), `${label}: ${L[i]} が印・人・段より先`);
  }
  for (const i of idx("print:")) assert.ok(i < Math.min(...solid), `${label}: 足跡 ${L[i]} が人か段より後（床の上に描いていない）`);
}

/**
 * 貼った絵の門: actors() の 1 人ずつ、その左上に貼った焼き置きのドットが PX_CELLS[vendor][cell]（mirror なら左右反転）と
 * 1 ドットも違わない（焼き置きの鍵からベンダーや左右が落ちると、別の絵を黙って貼る）。
 */
function assertSprites(room, main, label) {
  const S = room.stats().scale;
  const imgs = main.ctx.log.filter((e) => e[0] === "img");
  for (const a of room.actors()) {
    // 挨拶の跳ね・歩き出す前の立ち上がり（a.lift）は描く y だけを上げる（R110-S4）
    const hit = imgs.filter((e) => e[2] === a.x * S && e[3] === (a.y - (a.lift || 0)) * S && e[1].width === 12 * S && e[1].height === 16 * S);
    assert.equal(hit.length, 1, `${label}: ${a.key} の左上に貼ったロボが ${hit.length} 枚`);
    const got = new Set(hit[0][1].ctx.log.filter((e) => e[0] === "fill").map((e) => `${e[1] / S},${e[2] / S}`));
    const rows = PX_CELLS[a.vendor][a.cell];
    const want = new Set();
    rows.forEach((row, y) => [...row].forEach((ch, x) => {
      if (ch !== ".") want.add(`${a.flip ? row.length - 1 - x : x},${y}`);
    }));
    assert.deepEqual([...got].sort(), [...want].sort(), `${label}: ${a.key} に ${a.vendor}.${a.cell}${a.flip ? "（鏡）" : ""} と違う絵を貼った`);
  }
}

/**
 * 直近のフレームで貼ったスプライトの鍵（焼き置きの門の材料）: ロボは (vendor, cell, tint, flip)、頭上の印は (タイル, 印)、
 * 足跡はタイルの名前 1 つ。貼った結果（actors・marks・layers）から作る（room.js の焼き置きの鍵の字面は読まない）。
 * 印は room.js の鍵（タイル＋❗の色かどうか）より細かく分ける＝上限が緩む側にしか外れない。
 */
function pastedKeys(room) {
  const actors = room.actors();
  const keys = new Set(actors.map((a) => `robot|${a.vendor}|${a.cell}|${a.tint}|${a.flip ? 1 : 0}`));
  const pasted = new Set(actors.map((a) => a.key));
  // 印は実際に貼ったタイル（R110-S5: 考え中の雲は live で点の付いた写し markThink1/2 に替わる）
  for (const m of room.marks()) if (m.markAt && pasted.has(m.key)) keys.add(`mark|${m.markTile}|${m.mark}`);
  // 職業の小道具（R110-S2）: タイルと左右（W の鏡は別の焼き置き）
  for (const m of room.marks()) if (m.propAt && pasted.has(m.key)) keys.add(`prop|${m.propAt.tile}|${m.propAt.flip ? 1 : 0}`);
  if (room.layers().some((l) => l.startsWith("print:"))) keys.add("tile|footstep");
  // 机の画面（R99-P4-L3）: "screen:<key>:<タイル>" の貼った記録から
  for (const l of room.layers()) if (l.startsWith("screen:")) keys.add(`tile|${l.split(":").pop()}`);
  return keys;
}

test("部屋の draw（C3）: 扉から机まで歩く人は、足元の y で手前の什器の段と交互に描かれる・印は最後", async () => {
  await withRoom({}, (room, main) => {
    const base = [who(0), who(1, { kind: "think" }), who(3, { attention: true, state: "waiting" })];
    room.draw({ agents: base }, [], 0);                 // 最初の世界（baseline＝誰も歩かない）
    assertC3(room, "t=0");
    assert.equal(room.stats().walking, 0);
    assert.equal(room1(room, "s3").zone, "queue", "前提: 確認待ちは受付（カウンターの向こう）");
    assert.equal(room1(room, "s3").mark, "call");
    assert.equal(room1(room, "s1").mark, "think");
    // ❗ は確認待ちの色（RAMP.signal＝台帳の ❗ と同じ --cp-alert）・💭 はタイルの凡例のまま（signal を使わない）
    const S = room.stats().scale;
    const markImg = (key) => {
      const at = room1(room, key).markAt;
      return main.ctx.log.find((e) => e[0] === "img" && e[2] === at.x * S && e[3] === at.y * S)[1];
    };
    const colors = (img) => new Set(img.ctx.log.filter((e) => e[0] === "fill").map((e) => e[5]));
    assert.ok(colors(markImg("s3")).has(RAMP.signal[3]) && colors(markImg("s3")).has(RAMP.signal[2]), "❗ が確認待ちの色でない");
    assert.ok(![...colors(markImg("s1"))].some((c) => RAMP.signal.includes(c)), "💭 に確認待ちの色");
    const seen = new Set();
    let prev = null;
    let arrivedAt = null;
    // 手前の段は在席の集合が変わったときだけ焼き直す（C3 の裁定）: s2 が加わった刻み（k=0＝机が 1 つ埋まる）で 1 回だけ。
    // 歩いている間（12fps）に毎フレーム焼き直す退行は drawCalls（メインの canvas）にも layers にも写らない＝ここで数える
    const fgStart = room.stats().fgBakes;
    let fgWalk = null;
    let walkedFrames = 0;
    // スプライト（ロボ・印・足跡）の焼き置きも同じ: 焼くのは**初めて貼る鍵**だけ。上限は焼いた回数ではなく、貼った鍵の
    // 集合の大きさで決める（焼いた回数を上限にすると自分を基準にする＝歩いている間だけ焼き置きが効かない退行が写らない）。
    // 鍵の出どころは貼った結果（actors の vendor・cell・tint・flip／印のタイルと印／足跡を貼ったか）。baseline で貼った鍵は
    // 引く（その分はもう焼いてある）。足跡のタイルは描画のたびに引く実装なので、先に焼いてあれば増分に出ない＝上限が 1 緩むだけ
    const spriteStart = room.stats().spriteBakes;
    const before = pastedKeys(room);
    const walkKeys = new Set();
    let settleTicks = 0;
    // R110-S4: 24px/秒＝扉から机まで最長 11 秒＋着いた後の一拍（SETTLE_SEC）を見る分
    for (let k = 0; k <= 12 * 14; k += 1) {
      const t = 1 + k / 12;
      room.draw({ agents: [...base, who(2, { vendor: "codex" })] }, [], t);
      assertC3(room, `t=${t.toFixed(3)}`);
      assertSprites(room, main, `t=${t.toFixed(3)}`);
      for (const key of pastedKeys(room)) walkKeys.add(key);
      const s2 = room1(room, "s2");
      const st = room.stats();
      assert.equal(st.unreachable, 0);
      if (k === 0) {
        assert.equal(st.fgBakes, fgStart + 1, "前提: s2 が机を 1 つ埋めた刻みで手前の段を焼き直す");
        fgWalk = st.fgBakes;
      } else {
        assert.equal(st.fgBakes, fgWalk, `t=${t.toFixed(3)}: 在席の集合は変わっていないのに手前の段を焼き直した（歩いている間）`);
      }
      if (s2.walk) walkedFrames += 1;
      if (s2.walk) {
        assert.equal(st.fps, 12, "歩いている人が居る間は 12fps");
        assert.equal(s2.kind, "walk");
        assert.equal(s2.mirror, s2.dir === "W", "左へ歩くときだけ E のコマを鏡に");
        // 12fps の描画 1 回で歩行コマがちょうど 1 つ進む（room が walk を topPose へ渡していないと止まる）
        const n = Number(s2.cell.slice(-1));
        if (prev !== null) assert.equal(n, (prev + 1) % 4, `t=${t.toFixed(3)}: 歩行コマが ${prev}→${n}`);
        prev = n;
        seen.add(s2.y + FOOT_CENTER[1]);
      } else if (arrivedAt === null && k > 0) {
        arrivedAt = t;
        assert.equal(st.fps, 6, "着いたら 6fps に戻る");
        // R110-S4: 着いた瞬間は正面の待機コマで一拍（SETTLE_SEC）置いてから打鍵
        assert.deepEqual([s2.kind, s2.cell], ["settle", "idle0"], "着いた瞬間に一拍置かない（すぐ打鍵した）");
      } else if (arrivedAt !== null) {
        if (s2.kind === "settle") settleTicks += 1;
        else assert.equal(s2.cell.startsWith("type"), true, `t=${t.toFixed(3)}: 一拍の後に打鍵しない（${s2.kind}）`);
        if (t - arrivedAt > SETTLE_SEC + 1e-6) assert.notEqual(s2.kind, "settle", "一拍が SETTLE_SEC を過ぎても続いた");
      }
      if (k === 0) {
        assert.equal(st.entering, 1, "baseline の後に来た人は扉から入る");
        const solid = room.layers().filter((l) => l.startsWith("person:") || l.startsWith("band:"));
        assert.equal(solid.at(-1), "person:s2", "扉の上の人（足元 y 156）は全部の段より手前");
      }
    }
    assert.ok(arrivedAt !== null, "14 秒で机に着かない");
    assert.equal(settleTicks, Math.round(SETTLE_SEC * 12) - 1, "一拍が 12fps の格子で SETTLE_SEC ぶん続かない");
    assert.ok(walkedFrames >= 12, `前提: 歩いている刻みが少ない（${walkedFrames}）＝焼き直しの門が空振り`);
    const fresh = [...walkKeys].filter((key) => !before.has(key));
    const baked = room.stats().spriteBakes - spriteStart;
    assert.ok(fresh.some((key) => key.includes("|walk")), `前提: 歩行のコマを貼っていない（${fresh}）＝焼き置きの門が空振り`);
    assert.ok(baked <= fresh.length,
      `歩いている ${walkedFrames} 刻みでスプライトを ${baked} 回焼いた＝初めて貼った鍵 ${fresh.length} 個より多い`
      + `（同じ鍵を焼き直している＝焼き置きが効いていない）: ${fresh.join(" ")}`);
    // 歩いた足元の y が、段の depth の両側にまたがる（全部の段の手前と奥を通った＝門が空振りしていない）
    for (const b of FRONT_BANDS.filter((x) => x.key.startsWith("desk"))) {
      assert.ok([...seen].some((y) => y < b.depth) && [...seen].some((y) => y >= b.depth), `段 ${b.key} の両側を歩いていない`);
    }
    assert.ok(room.layers().filter((l) => l.startsWith("print:")).length === 0, "着いた人は足跡を残さない");
  });
});

test("部屋の draw: 歩いている人の足跡は床の上に数個だけ（タイルで貼る）", async () => {
  await withRoom({}, (room) => {
    room.draw({ agents: [who(0)] }, [], 0);
    const counts = [];
    for (let k = 0; k <= 12 * 3; k += 1) {
      room.draw({ agents: [who(0), who(2)] }, [], 1 + k / 12);
      const prints = room.layers().filter((l) => l === "print:s2").length;
      assert.ok(prints <= PRINTS_PER_WALKER, "足跡が多すぎる");
      counts.push(prints);
    }
    assert.equal(counts[0], 0, "歩き出した瞬間に足跡は無い");
    assert.ok(counts.includes(PRINTS_PER_WALKER), `足跡が ${PRINTS_PER_WALKER} 個まで増えない（${counts}）`);
  });
});

test("footprints（純関数）: 足元より 1 歩以上後ろ・床の上で滑らない・左右交互・巻き戻しや壊れた経路は空", () => {
  const path = planPath([153, 141], [101, 45]);
  assert.ok(path && path.length > 2, "前提: 扉から机 2 への経路");
  assert.deepEqual(footprints(path, 10, 10), []);
  assert.deepEqual(footprints(path, 10, 9), [], "巻き戻し（t < t0）");
  assert.deepEqual(footprints(null, 0, 5), []);
  assert.deepEqual(footprints([[1, 1]], 0, 5), [], "1 点だけの経路");
  assert.deepEqual(footprints(path, NaN, 5), []);
  let before = null;
  for (let k = 1; k < 60; k += 1) {
    const t = 10 + k / 12;
    const prints = footprints(path, 10, t);
    assert.ok(prints.length <= PRINTS_PER_WALKER);
    const dist = (t - 10) * WALK_SPEED;
    assert.equal(prints.length, Math.min(PRINTS_PER_WALKER, Math.max(0, Math.floor(Math.round(dist * 1e6) / 1e6 / PRINT_STEP))),
      `t=${t}: 足跡の数`);
    const here = posAt(path, 10, t);
    for (const [x, y] of prints) {
      assert.ok(Number.isInteger(x) && Number.isInteger(y), "整数");
      // 足跡のタイル（2×2）は今の足元の中心から 1 歩（PRINT_STEP）以上離れている（足の下に隠れない）
      const d = Math.abs(x + 1 - (here.x + FOOT_CENTER[0])) + Math.abs(y + 1 - (here.y + FOOT_CENTER[1]));
      assert.ok(d >= PRINT_STEP - 3, `t=${t}: 足跡 ${[x, y]} が足元に近すぎる（${d}）`);
    }
    // 床の上で滑らない: 前の刻みの足跡のうち新しい方から 2 つは、今の刻みにも同じ座標で残る（新しいのが先頭に足されるだけ）
    if (before && before.length === PRINTS_PER_WALKER && prints.length === PRINTS_PER_WALKER) {
      const keep = new Set(prints.map(String));
      assert.ok(before.slice(0, 2).every((p) => keep.has(String(p))) || String(before) === String(prints),
        `t=${t}: 足跡が床の上で滑った（${before} → ${prints}）`);
    }
    before = prints;
  }
  // 左右交互（縦に歩く区間: 扉から上へ）
  const up = footprints(path, 0, 1);                    // 24px 進んだ＝全部 x 153 の縦の区間
  assert.equal(up.length, PRINTS_PER_WALKER);
  assert.notEqual(up[0][0], up[1][0], "縦に歩く足跡が左右に振れていない");
  assert.equal(up[0][0], up[2][0]);
});

test("部屋の draw: 同じ t の描き直しは冪等（前の t の帳簿から）＝同じ t に届いた報告も ✓ になり、描き直しで消えない", async () => {
  await withRoom({}, (room) => {
    const working = { agents: [who(0)] };
    const reported = { agents: [who(0, { state: "waiting", kind: "said", age: 30 })] };
    room.draw(working, [], 10);
    room.draw(working, [], 10.5);
    room.draw(reported, [], 10.5);                      // 同じ t に届いた報告（データの取得は刻みの途中で届く）
    assert.equal(room1(room, "s0").mark, "done", "同じ t に届いた報告の ✓ が失われた");
    assert.equal(room1(room, "s0").cell, "done0");
    for (let i = 0; i < 3; i += 1) {
      room.draw(reported, [], 10.5);                    // ホバー・選択の描き直し
      assert.equal(room1(room, "s0").mark, "done", "同じ t の描き直しで ✓ が消えた");
    }
    assert.equal(room.stats().marks.done, 1);
    for (const t of [11, 13, 15, 17, 18.4]) {
      room.draw(reported, [], t);
      assert.equal(room1(room, "s0").mark, "done", `t=${t}: ✓ の窓（8 秒）の中`);
    }
    room.draw(reported, [], 18.6);
    assert.equal(room1(room, "s0").mark, "wait", "8 秒で ✓ から 💬 に替わる");
  });
});

test("部屋の draw: t が進まない間（frozen＝reduced-motion・停止・?t=）は歩かせず ✓ を出さない・戻っても遅れて演じない", async () => {
  const working = { agents: [who(0)] };
  const reported = { agents: [who(0, { state: "waiting", kind: "said", age: 30 })] };
  const joined = { agents: [...reported.agents, who(5)] };
  // 対照: 止めなければ、live で working を記録した後に届いた報告は ✓ になる（下の筋書きの材料が ✓ を生む＝空振りしない）
  await withRoom({}, (room) => {
    room.draw(working, [], 20);
    room.draw(working, [], 20.5);
    room.draw(reported, [], 21);
    assert.equal(room1(room, "s0").mark, "done", "前提: 止めなければ、working の記録の後の報告は ✓");
  });
  let still = false;
  await withRoom({ frozen: () => still }, (room) => {
    // **live で working を記録させてから**止める。開いた瞬間から止まっている・報告が止める前に届く、のどちらでも、
    // 再開した時の前回の記録は working でない＝古さの門（MARK_STALE_SEC）を壊しても遅れた ✓ が出ようがない（門が空振り）
    room.draw(working, [], 20);
    room.draw(working, [], 20.5);
    assert.equal(room1(room, "s0").mark, null, "前提: 打鍵中は印なし");
    assert.equal(room.stats().marks.done, 0);
    still = true;
    const cells = new Set();
    for (let k = 0; k < 12; k += 1) {
      room.draw(reported, [], 21 + k / 6);             // 止めている間に報告が届く（その後も描き続ける）
      assert.equal(room1(room, "s0").mark, "wait", "止めている間に ✓ を出した");
      assert.notEqual(room1(room, "s0").cell, "done0");
      cells.add(room1(room, "s0").cell);
    }
    assert.equal(cells.size, 1, `止めている間にコマが送られた（${[...cells]}）＝動きの時計が固定されていない`);
    // 止めている間に来た人は歩かせない（目的地に居る）
    room.draw(joined, [], 23);
    assert.equal(room.stats().walking, 0, "止めている間に歩いた");
    assert.equal(room.stats().fps, 6);
    const s5 = room1(room, "s5");
    const want = roomLayout(joined, [], null).actors.find((a) => a.key === "s5");
    assert.deepEqual([s5.x, s5.y], [want.x, want.y], "止めている間に来た人が目的地に居ない");
    // 動きを戻す: 最後に live で描いた 20.5 から 4.5 秒後。止めている間の遷移・入場を遅れて演じない。
    // 間の長さは実装の MARK_STALE_SEC を読まずに書く（読むと、古さの門を壊す変異が期待値ごと動いて緑のまま）
    still = false;
    room.draw(joined, [], 25);
    assert.equal(room.stats().walking, 0, "止めている間に来た人が、戻った瞬間に扉から歩き出した");
    assert.equal(room1(room, "s0").mark, "wait", "止めている間の報告を、戻った瞬間に遅れて祝った");
    assert.notEqual(room1(room, "s0").cell, "done0", "止めている間の報告に、戻った瞬間に遅れて万歳した");
    room.draw(joined, [], 25 + 1 / 6);
    assert.equal(room1(room, "s0").mark, "wait", "戻った次の刻みで遅れて祝った");
  });
});

/**
 * 選択の角かっこの正本＝design/cockpit.html の見本（選んだ人に塗る fillRect の並び）。room.js の定数（SELECT_MARK）を
 * 期待値に写すと「実装が実装と一致する」しか見ない＝見本から独立に読む。見本の式を人の左上 (0, 0)・倍率 1 で評価し、
 * 人の左上からの矩形 [dx, dy, w, h] にする。見本の書き方が変わって読めなければ赤（この門を直す）。
 * design/ は公開物に無い（tools/_publish_excludes.sh）＝design/ も CLAUDE.md も無い木（公開物）でだけ突き合わせを skip。
 * 開発ツリー（tools/publish_snapshot.sh が在る＝公開物・配布物から除外している。CLAUDE.md は受け取った人が /init で作りうるので印にしない）で design/ が消えたら、readFileSync が赤にする（skip に倒さない）。
 */
const DESIGN_SKIP = !existsSync(new URL("../design/", import.meta.url)) && !existsSync(new URL("../tools/publish_snapshot.sh", import.meta.url))
  ? "design/ は公開物に含めない＝選択の角かっこを見本と突き合わせない" : false;
function designSelectRects() {
  const html = readFileSync(new URL("../design/cockpit.html", import.meta.url), "utf8");
  const block = html.match(/a\.s\.id !== selected\) continue;([\s\S]*?)\n\s*\}/)?.[1];
  assert.ok(block, "design/cockpit.html に選択の角かっこ（a.s.id !== selected の後の塗り）が見つからない");
  const args = [...block.matchAll(/ctx\.fillRect\(([^;]+)\);/g)].map((m) => m[1]);
  assert.ok(args.length >= 2, `design/cockpit.html の角かっこの fillRect が ${args.length} 本しか読めない`);
  // eslint-disable-next-line no-new-func
  return args.map((a) => new Function("a", "SCALE", "PX_CELL_W", "PX_CELL_H", `return [${a}];`)(
    { x: 0, y: 0 }, 1, PX_CELL_W, PX_CELL_H));
}
const rectKey = (r) => r.join(",");
/** 選んだ人（key）に塗った角かっこ＝最後に貼った画像（印）の後の塗り。人の左上からの論理矩形 [dx, dy, w, h] と色 */
function selectFills(room, main, key) {
  const a = room.actors().find((x) => x.key === key);
  const S = room.stats().scale;
  const log = main.ctx.log;
  return log.slice(log.findLastIndex((e) => e[0] === "img") + 1).filter((e) => e[0] === "fill")
    .map((e) => ({ rect: [e[1] / S - a.x, e[2] / S - a.y, e[3] / S, e[4] / S], color: e[5] }));
}
const selectWorld = () => ({ agents: [who(0), who(1, { kind: "think" })] });

test("部屋の draw: 選択の角かっこ＝design/cockpit.html の見本の形（人の左上から）", { skip: DESIGN_SKIP }, async () => {
  const want = designSelectRects();
  await withRoom({}, (room, main) => {
    room.setSelected("s1");
    room.draw(selectWorld(), [], 1);
    assert.equal(room.layers().at(-1), "select:s1");
    assert.deepEqual(selectFills(room, main, "s1").map((f) => rectKey(f.rect)).sort(),
      want.map(rectKey).sort(), "選択の角かっこが design/cockpit.html の見本の形と違う");
  });
});

test("部屋の draw: 選択は頭上の角かっこ・ホバーは足元の線（描き分け・印より後）", async () => {
  await withRoom({}, (room, main) => {
    const world = selectWorld();
    room.setSelected("s1");
    room.draw(world, [], 1);
    assertC3(room, "選択");
    assert.equal(room.stats().selected, 1);
    assert.equal(room.layers().at(-1), "select:s1");
    // 角かっこの形は上の門（見本との突き合わせ）。ここでは色: 最後に貼った画像（印）の後の塗りが角かっこ
    const s1 = room.actors().find((a) => a.key === "s1");
    const S = room.stats().scale;
    assert.deepEqual(new Set(selectFills(room, main, "s1").map((f) => f.color)), new Set([s1.tint]), "角かっこはその人の個体色");
    room.setHover({ session: "s0" });
    room.draw(world, [], 1);
    assert.equal(room.stats().hovered, 1);
    assert.equal(room.layers().at(-1), "hover:s0");
    const s0 = room.actors().find((a) => a.key === "s0");
    const [dx, dy, w, h] = HOVER_LINE;
    const line = main.ctx.log.filter((e) => e[0] === "fill").at(-1);
    assert.deepEqual(line.slice(1, 5), [(s0.x + dx) * S, (s0.y + dy) * S, w * S, h * S]);
    assert.equal(line[5], s0.tint, "ホバーの線はその人の個体色");
    room.setHover("p1");                                 // プロジェクトの行（key）
    room.draw(world, [], 1);
    assert.deepEqual(room.layers().filter((l) => l.startsWith("hover:")), ["hover:s1"]);
    room.setSelected("nobody");
    room.setHover(null);
    room.draw(world, [], 1);
    assert.equal(room.stats().selected, 0);
    assert.equal(room.stats().hovered, 0);
  });
});

test("部屋の draw: 退場中の人は歩くだけ（印・挙手・選択なし・クリックで当たらない）", async () => {
  await withRoom({}, (room) => {
    const caller = who(2, { attention: true, state: "waiting" });
    room.draw({ agents: [who(0), caller] }, [], 40);
    room.draw({ agents: [who(0), caller] }, [], 40.5);
    assert.equal(room1(room, "s2").mark, "call", "前提: 受付で ❗");
    assert.ok(room.point("s2") && room.point("p2"), "前提: 居る間は照準（point）に入る");
    room.setSelected("s2");
    room.setHover({ session: "s2" });
    let t = 40.5;
    let leftOnce = false;
    // R110-S4: 24px/秒＋立ち上がり＝受付から扉まで最長でも 12 秒に収まる
    for (let k = 1; k < 12 * 12; k += 1) {
      t = 40.5 + k / 12;
      room.draw({ agents: [who(0)] }, [], t);
      const s2 = room1(room, "s2");
      if (!s2) break;
      leftOnce = true;
      assert.equal(s2.leaving, true);
      assert.equal(s2.mark, null, "退場中に印を出した");
      // R110-S4: 受付に立っていた人は立ち上がって（rise・最初の 2 刻み）から歩く。どちらも挙手ではない
      assert.ok(s2.kind === "walk" || (s2.kind === "rise" && k <= 2), `退場中に挙手した（k=${k}: ${s2.kind}）`);
      assert.equal(room.stats().leaving, 1);
      assert.equal(room.stats().fps, 12);
      assert.ok(!room.layers().includes("select:s2"), "台帳に居ない退場中の人に選択の印");
      assert.ok(!room.layers().includes("hover:s2"), "台帳に居ない退場中の人にホバーの線");
      assert.equal(room.stats().marks.call, 0);
      assert.ok(room.actors().some((a) => a.key === "s2" && a.leaving), "退場中の人を貼っていない");
      const S = room.stats().scale;
      assert.notEqual(room.pick((s2.x + 6) * S, (s2.y + 8) * S)?.session, "s2", "退場中の人がクリックで当たった");
      assert.equal(room.point("s2"), null, "退場中の人を照準（point・スモークの狙い）に入れた");
      assert.equal(room.point("p2"), null, "退場中の人を id で照準に入れた");
    }
    assert.ok(leftOnce, "退場を 1 フレームも描いていない（門が空振り）");
    assert.equal(room1(room, "s2"), undefined, "12 秒で扉に着いて消えない");
    assert.equal(room.stats().walking, 0);
  });
});

test("部屋の draw: 無いコマは描かずに数える（別のコマで黙って代用しない）・知らないベンダーは claude の形", async () => {
  const variant = await import("../ui/cockpit/room.js?missing");
  assert.ok(PX_CELLS.codex?.idle0, "前提: 本物の codex には idle0 が在る（すり替えは門の中だけ）");
  await withRoom({}, (room) => {
    // s9 は報告して待っている codex（💬 を持つ・止まっているコマは idle＝無い）＝貼れなかった人の印の扱いを見る
    const world = { agents: [who(0), who(7, { vendor: "codex", state: "idle" }), who(8, { vendor: "codex" }),
      who(9, { vendor: "codex", state: "waiting", kind: "said", age: 30 }), who(10, { state: "waiting", kind: "said", age: 30 })] };
    room.draw(world, [], 1);
    const st = room.stats();
    assert.equal(st.missingCells, 2, "codex の idle0 が無いのに数えていない");
    assert.equal(room1(room, "s7").missing, true);
    assert.ok(!room.actors().some((a) => a.key === "s7"), "無いコマを別のコマで貼った");
    assert.ok(!room.layers().some((l) => l.startsWith("person:s7")));
    assert.equal(room1(room, "s8").missing, false, "codex の打鍵は在る");
    assert.equal(room.actors().find((a) => a.key === "s8").vendor, "codex");
    // 貼れなかった人の印は描かず・数えない（貼れた claude の s10 の 💬 だけが数と層に出る）
    assert.equal(room1(room, "s9").missing, true);
    assert.equal(room1(room, "s9").mark, "wait", "前提: s9 は 💬 を持つ");
    assert.equal(room1(room, "s10").mark, "wait", "前提: s10 は 💬 を持つ");
    assert.equal(st.marks.wait, 1, "貼れなかった人の印を数えた");
    assert.ok(!room.layers().some((l) => l.startsWith("mark:s9")), "貼れなかった人の印を描いた（宙に浮いた印）");
    assert.ok(room.layers().includes("mark:s10:wait"), "前提: 貼れた人の 💬 は描く");
    room.draw(world, [], 2);
    assert.equal(room.stats().missingCells, 4, "累計で数える");
  }, variant);
  await withRoom({}, (room, main) => {
    // s1（codex）と s2（claude）は同じプロジェクト＝同じ個体色・同じコマ（焼き置きの鍵からベンダーが落ちると同じ絵になる）
    room.draw({ agents: [who(0, { vendor: "other" }), who(1, { vendor: "codex", state: "idle" }),
      who(2, { id: "p1", vendor: "claude", state: "idle" })] }, [], 1);
    assertSprites(room, main, "ベンダー");
    assert.equal(room.stats().missingCells, 0, "本物のシートで描けないコマがある");
    assert.equal(room.actors().find((a) => a.key === "s0").vendor, "claude", "知らないベンダーは claude の形（topVendor）");
    assert.equal(room.actors().find((a) => a.key === "s1").vendor, "codex");
  });
});

test("部屋の draw: 読書のコマ・drawCalls は実際に呼んだ数・入れ替わりの山でも上限以内", async () => {
  assert.ok(DRAW_CALL_LIMIT > 0, "tools/ui_shot.py の DRAW_CALL_LIMIT を読めない");
  await withRoom({}, (room, main) => {
    room.draw({ agents: [who(0, { verb: "読込中" })] }, [], 1);
    assert.equal(room.stats().reads, 1);
    assert.ok(room1(room, "s0").cell.startsWith("read"));
    const counted = () => main.ctx.log.filter((e) => e[0] === "fill" || e[0] === "img").length;
    assert.equal(room.stats().drawCalls, counted());
    // 26 人（部屋の全部の枠）が全員帰り、別の 26 人が同じ刻みに来る＝退場 26 ＋入場 26 が同時に歩く
    const crowd = (from) => Array.from({ length: 26 }, (_, i) => who(from + i, {
      zone: i < 9 ? "desk" : i < 18 ? "queue" : i < 20 ? "lounge" : i < 23 ? "meeting" : "external",
      attention: i >= 9 && i < 18, state: i >= 9 && i < 18 ? "waiting" : i >= 18 && i < 20 ? "resting" : "working",
      minions: i >= 20 && i < 23 ? 1 : 0 }));
    room.draw({ agents: crowd(100) }, [], 10);
    room.draw({ agents: crowd(100) }, [], 10.5);
    let peak = 0;
    for (let k = 1; k <= 12 * 4; k += 1) {
      room.draw({ agents: crowd(200) }, [], 10.5 + k / 12);
      assert.equal(room.stats().drawCalls, counted(), "drawCalls が実際に呼んだ数と違う");
      peak = Math.max(peak, room.stats().drawCalls);
      if (k === 6) assert.ok(room.stats().leaving > 0 && room.stats().entering > 0, "前提: 入退場が同時に歩いている");
    }
    assert.ok(peak <= DRAW_CALL_LIMIT, `入れ替わりの山で drawCalls ${peak} > ${DRAW_CALL_LIMIT}`);
    assert.equal(room.stats().unreachable, 0);
  });
});

test("部屋の draw: 同じ個体色・同じコマでも右向き（E）と左向き（W の鏡）を別の焼き置きで貼る", async () => {
  await withRoom({}, (room, main) => {
    // 同じプロジェクト（同じ個体色）の 2 人: b はソファへ扉から入る（左へ歩く＝鏡）、その後 a がソファから扉へ帰る（右へ歩く）
    const a = who(0, { id: "p9", session: "a", zone: "lounge", state: "resting" });
    const b = who(1, { id: "p9", session: "b", zone: "lounge", state: "resting" });
    room.draw({ agents: [a] }, [], 0);
    const dirs = new Set();
    let t = 0;
    for (let k = 1; k <= 12 * 20; k += 1) {
      t = k / 12;
      room.draw({ agents: k < 12 * 10 ? [a, b] : [b] }, [], t);
      assertSprites(room, main, `t=${t.toFixed(3)}`);
      for (const m of room.marks()) if (m.walk) dirs.add(`${m.key}:${m.dir}`);
    }
    assert.ok(dirs.has("b:W") && dirs.has("a:E"), `前提: 同じ個体色で左へも右へも歩いた（${[...dirs]}）`);
  });
});

// ── 第 2 波の統合の修正（room） ─────────────────────────────────────────────
test("部屋の draw: 届かない（unreachable）は t ごとに 1 回＝同じ t に届いたデータも数え、描き直しで増やさない", async () => {
  const variant = await import("../ui/cockpit/room.js?nopath");
  try {
    // 前提: 差し替えた写しでも、旗を立てるまでは本物と同じく扉から歩いて入る
    await withRoom({}, (room) => {
      room.draw({ agents: [who(0)] }, [], 1);
      room.draw({ agents: [who(0), who(2)] }, [], 1 + 1 / 6);
      assert.equal(room.stats().entering, 1, "前提: 旗の無い写しで入場が歩いていない");
      assert.equal(room.stats().unreachable, 0);
    }, variant);
    await withRoom({}, (room) => {
      room.draw({ agents: [who(0)] }, [], 1);
      room.draw({ agents: [who(0)] }, [], 1 + 1 / 6);    // この格子はもう描いた（onFrame が格子の最初の rAF で描いた後）
      globalThis.__cockpitNoPath = true;
      // 本番の経路: データは格子の途中で届き、apply → redraw が**同じ t** で描き直す
      const two = { agents: [who(0), who(2)] };
      room.draw(two, [], 1 + 1 / 6);
      assert.equal(room.stats().entering, 0, "前提: 経路が引けない人は歩かない");
      assert.equal(room.stats().unreachable, 1, "同じ t に届いた人の「届かない」を数えていない（累計が 0 のまま＝門が空振り）");
      for (let i = 0; i < 3; i += 1) {
        room.draw(two, [], 1 + 1 / 6);                   // ホバー・選択の描き直し
        assert.equal(room.stats().unreachable, 1, "同じ t の描き直しで二重に数えた");
      }
      for (const t of [1 + 2 / 6, 1 + 3 / 6, 2]) {
        room.draw(two, [], t);
        assert.equal(room.stats().unreachable, 1, `t=${t}: 帳簿に立っている人をまた数えた`);
      }
      // 退場の「届かない」も同じ t に届いたデータで数える（扉への経路が引けない＝その場で消える）
      room.draw({ agents: [who(0)] }, [], 2);
      assert.equal(room.stats().leaving, 0, "前提: 扉へ引けない人は歩かない");
      assert.equal(room.stats().unreachable, 2, "同じ t に届いた退場の「届かない」を数えていない");
      room.draw({ agents: [who(0)] }, [], 2);
      assert.equal(room.stats().unreachable, 2, "同じ t の描き直しで二重に数えた（退場）");
      room.draw({ agents: [who(0)] }, [], 2 + 1 / 6);
      assert.equal(room.stats().unreachable, 2, "次の t に同じ退場を数え直した");
      // 再生（ダイジェスト）は帳簿に触らない＝累計も動かさない
      room.draw({ agents: [who(0), who(4)] }, [], 5, { replay: true });
      assert.equal(room.stats().unreachable, 2, "再生の描画で累計が動いた");
    }, variant);
  } finally {
    delete globalThis.__cockpitNoPath;
  }
});

test("部屋の draw: 退場中のロボは直近の個体色のまま（衝突でずらされた色が、台帳から落ちてハッシュの色に化けない）", async () => {
  // ハッシュの枠が同じ 2 プロジェクト＝片方は tintIndex に別の色へずらされる
  const n = SESSION_COLORS.length;
  const slot = (k) => tintHash(k) % n;
  let pair = null;
  for (let i = 0; i < 400 && !pair; i += 1) {
    for (let j = i + 1; j < 400 && !pair; j += 1) if (slot(`pj${i}`) === slot(`pj${j}`)) pair = [`pj${i}`, `pj${j}`];
  }
  assert.ok(pair, "前提: ハッシュの枠が同じ 2 キーが見つからない");
  let tints = tintIndex(pair, n, null);
  // index.js の tintFor と同じ形（台帳の集合に無いキーはハッシュの剰余）
  const colorOf = (key) => SESSION_COLORS[tints.get(String(key)) ?? tintHash(key) % n];
  const moved = pair.find((k) => tints.get(k) !== slot(k));
  const other = pair.find((k) => k !== moved);
  assert.ok(moved && other, "前提: 片方が衝突でずらされていない");
  const M = who(0, { id: moved, session: "sM" });
  const O = who(1, { id: other, session: "sO" });
  await withRoom({ colorOf }, (room, main) => {
    room.draw({ agents: [M, O] }, [], 1);
    room.draw({ agents: [M, O] }, [], 1.5);
    const tintM = room.actors().find((a) => a.key === "sM").tint;
    const tintO = room.actors().find((a) => a.key === "sO").tint;
    assert.equal(tintM, SESSION_COLORS[tints.get(moved)], "前提: 居る間はずらした色");
    assert.notEqual(tintM, tintO, "前提: 2 人は別の色");
    assert.equal(colorOf(moved) === tintO, false);
    // sM のセッションが消える: index.js の apply が台帳の集合で tints を作り直し（moved が落ちる）、同じ t で描き直す
    tints = tintIndex([other], n, tints);
    assert.equal(colorOf(moved), tintO, "前提: 集合から落ちたキーはハッシュの色＝もう 1 人と同じ色");
    const S = room.stats().scale;
    let leftOnce = false;
    for (let k = 0; k < 12 * 8; k += 1) {
      const t = 1.5 + k / 12;
      room.draw({ agents: [O] }, [], t);
      const m = room.actors().find((a) => a.key === "sM");
      if (!m) break;
      leftOnce = true;
      assert.equal(m.leaving, true);
      assert.equal(m.tint, tintM, `t=${t.toFixed(3)}: 退場中の個体色が ${m.tint}（居たときは ${tintM}・もう 1 人は ${tintO}）`);
      // 貼った絵そのものも居たときの色（控えの数字だけでなく塗り）
      const img = main.ctx.log.find((e) => e[0] === "img" && e[2] === m.x * S && e[3] === (m.y - (m.lift || 0)) * S
        && e[1].width === PX_CELL_W * S)?.[1];
      const painted = new Set(img.ctx.log.filter((e) => e[0] === "fill").map((e) => e[5]));
      assert.ok(painted.has(tintM) && !painted.has(tintO), `t=${t.toFixed(3)}: 退場中のロボを別の色で塗った`);
      room.draw({ agents: [O] }, [], t);                  // 同じ t の描き直しでも同じ色（冪等）
      assert.equal(room.actors().find((a) => a.key === "sM").tint, tintM, "同じ t の描き直しで色が変わった");
    }
    assert.ok(leftOnce, "退場を 1 フレームも描いていない（門が空振り）");
    assert.equal(room.actors().find((a) => a.key === "sO").tint, tintO, "残った人の色が変わった");
  });
  // 冪等: 退場の色は「前の t の終わり」の控えから決まる＝同じ t の途中の描き直し（その t に居た間の色）を挟んでも同じ
  const leaveTint = (detour) => {
    const cmap = new Map([[moved, "#111111"], [other, "#222222"]]);
    return withRoom({ colorOf: (k) => cmap.get(k) }, (room) => {
      room.draw({ agents: [M, O] }, [], 1);
      room.draw({ agents: [M, O] }, [], 1.5);
      if (detour) { cmap.set(moved, "#333333"); room.draw({ agents: [M, O] }, [], 2); }
      cmap.set(moved, "#444444");
      room.draw({ agents: [O] }, [], 2);
      return room.actors().find((a) => a.key === "sM")?.tint;
    });
  };
  assert.equal(await leaveTint(false), "#111111", "前提: 退場の色は前の t の終わりの色");
  assert.equal(await leaveTint(true), "#111111", "同じ t の描き直しを挟むと退場の色が変わった（冪等でない）");
});

test("部屋の draw: 非表示のタブ（document.hidden）の間は描かず帳簿も進めない・戻っても入場や ✓ を遅れて演じない", async () => {
  await withRoom({}, (room, main) => {
    const working = { agents: [who(0)] };
    room.draw(working, [], 50);
    room.draw(working, [], 50 + 1 / 6);
    const before = room.stats();
    const painted = main.ctx.log.length;
    globalThis.document.hidden = true;
    // 隠れている間にデータが届く（報告と入場）。apply → redraw は来るが、部屋は描かない
    const later = { agents: [who(0, { state: "waiting", kind: "said", age: 30 }), who(5)] };
    for (let k = 1; k <= 6 * 6; k += 1) room.draw(later, [], 50 + 1 / 6 + k / 6);
    const hid = room.stats();
    assert.equal(hid.frames, before.frames, "隠れている間に描いた");
    assert.equal(hid.t, before.t, "隠れている間に時刻を進めた");
    assert.equal(main.ctx.log.length, painted, "隠れている間にメインの canvas へ塗った");
    assert.deepEqual(room.walkers().map((w) => w.key), ["s0"], "隠れている間に歩行の帳簿を進めた");
    // 戻る（visibilitychange の後、最初の rAF）
    globalThis.document.hidden = false;
    const back = 50 + 1 / 6 + 37 / 6;
    room.draw(later, [], back);
    assert.equal(room.stats().entering, 0, "隠れている間に来た人が、戻った瞬間に扉から歩き出した");
    assert.equal(room.stats().walking, 0);
    assert.notEqual(room1(room, "s0").mark, "done", "隠れている間の報告を、戻った瞬間に遅れて祝った");
    const want = roomLayout(later, [], null).actors.find((a) => a.key === "s5");
    assert.deepEqual([room1(room, "s5").x, room1(room, "s5").y], [want.x, want.y], "隠れている間に来た人が目的地に居ない");
    // 戻った後は live のまま（次の刻みも描く・止まっていない）
    room.draw(later, [], back + 1 / 6);
    assert.equal(room.stats().frames, before.frames + 2);
  });
});

test("部屋の draw: 無いタイル（印・足跡）は貼らずに数える（missingTiles）＝別の絵で代用しない", async () => {
  const variant = await import("../ui/cockpit/room.js?notile");
  const { PX_TILES } = await import("../ui/cockpit/tiles.js");
  assert.ok(PX_TILES.markThink && PX_TILES.footstep, "前提: 本物の生成物には 💭 と足跡が在る（抜くのは門の中だけ）");
  // 同じ筋書き: s1 は考え中（💭）、s2 は baseline の後に扉から机まで歩く（足跡）
  const script = (room) => {
    const rows = [];
    room.draw({ agents: [who(0), who(1, { kind: "think" })] }, [], 1);
    rows.push({ miss: room.stats().missingTiles, layers: room.layers(), think: room1(room, "s1").mark });
    for (let k = 1; k <= 12 * 3; k += 1) {
      room.draw({ agents: [who(0), who(1, { kind: "think" }), who(2)] }, [], 1 + k / 12);
      rows.push({ miss: room.stats().missingTiles, layers: room.layers(), think: room1(room, "s1").mark });
    }
    return rows;
  };
  const real = await withRoom({}, script);
  assert.ok(real.every((r) => r.miss === 0), "本物のタイルで描けないものがある");
  const printsAt = real.map((r) => r.layers.filter((l) => l.startsWith("print:")).length);
  assert.ok(printsAt.some((c) => c > 0), "前提: 本物では足跡を貼った（門が空振りしない）");
  assert.ok(real.every((r) => r.think === "think" && r.layers.includes("mark:s1:think")), "前提: 本物では 💭 を貼った");
  const gone = await withRoom({}, script, variant);
  let prev = 0;
  gone.forEach((r, k) => {
    assert.equal(r.think, "think", "前提: 印は決まっている（タイルが無いだけ）");
    assert.ok(!r.layers.some((l) => l.startsWith("mark:s1")), `${k}: 無い 💭 のタイルを貼った`);
    assert.ok(!r.layers.some((l) => l.startsWith("print:")), `${k}: 無い足跡のタイルを貼った`);
    // 1 フレームに 💭 で 1・足跡は（貼るはずの足跡があれば）1 人 1 回だけ
    const want = 1 + (printsAt[k] > 0 ? 1 : 0);
    assert.equal(r.miss - prev, want, `${k}: missingTiles の増え方が ${r.miss - prev}（期待 ${want}）`);
    prev = r.miss;
  });
});

// ── C3 の画素（マスク検査）: 扉 ↔ 全部の枠の道のりで ────────────────────────────
// 上の C3 の門は「layers() の順が段の depth の規則どおりか」＝実装の数字（FRONT_BANDS）どうしの整合しか見ない。
// ここは**画素**で見る: 偽の canvas に残った塗りを貼った順に重ね、人の不透明なドットのうち、手前の什器の天板
// （手前の層で足元を持つ行＝机・受付のカウンター・ソファの座面・会議卓）のドットと重なるものが、人より後に貼った段で
// 塗られているかを数える。前後は**家具の足元の下端**（FURNITURE の foot）と人の足元の y だけで決める（段の分け方・depth・
// 貼る順の実装は読まない＝段の組み違い・depth の取り違え・段を人の後にまとめて貼る、のどれでも赤）:
//   人の足元の y ≥ 天板の足元の下端（前を通る）→ 天板のドットが人の上に来ない
//   人の足元の y < 天板の足元の下端（奥に居る）→ 重なるドットは天板が隠す（腰から下が机に隠れる）
// 道のり: 26 枠を 1 人ずつ扉から入って埋め、1 人ずつ扉へ帰す（12fps で 1 刻みずつ・歩いている人は毎刻み・止まっている人は
// 段を焼き直すたびに見る）。
test("部屋の draw（C3 の画素）: 扉 ↔ 全部の枠で、手前の天板は前を通る人に被らず、奥に居る人の腰から下を隠す", async () => {
  const { PX_TILES } = await import("../ui/cockpit/tiles.js");
  const { TILE_PALETTE } = await import("../ui/cockpit/palette.js");
  const { FURNITURE, ROBOT_FOOT, SLOTS, footRects } = await import("../ui/core/pxroom.js");
  const tops = FURNITURE.filter((f) => f.layer === "front" && footRects(f).length).map((f) => {
    const mask = new Set();
    PX_TILES[f.tile].forEach((row, y) => [...String(row)].forEach((ch, x) => {
      if (Object.hasOwn(TILE_PALETTE, ch)) mask.add(`${f.x + x},${f.y + y}`);
    }));
    return { id: f.id, bottom: Math.max(...footRects(f).map(([, y, , h]) => y + h)), mask };
  });
  assert.ok(tops.filter((f) => f.id.startsWith("desk")).length === 9 && tops.some((f) => f.id === "counter"),
    `前提: 天板（机 9・カウンター…）を配置表から拾えていない（${tops.map((f) => f.id)}）`);
  // 足元の y＝人の足（ROBOT_FOOT・左上からの矩形）の縦の中心
  const footDY = ROBOT_FOOT[1] + ROBOT_FOOT[3] / 2;
  const slots = Object.values(SLOTS).reduce((n, list) => n + list.length, 0);
  // 26 人＝全部の枠。区分は roomLayout が world の zone・attention から決める（who の既定は机）
  const kinds = [
    ...Array.from({ length: SLOTS.desk.length }, () => ({ zone: "desk" })),
    ...Array.from({ length: SLOTS.queue.length }, () => ({ zone: "queue", attention: true, state: "waiting" })),
    ...Array.from({ length: SLOTS.lounge.length }, () => ({ zone: "lounge", state: "resting" })),
    ...Array.from({ length: SLOTS.meeting.length }, () => ({ zone: "meeting", minions: 1 })),
    ...Array.from({ length: SLOTS.external.length }, () => ({ zone: "external" })),
  ];
  assert.equal(kinds.length, slots, "前提: 全部の枠を 1 人ずつ埋める");
  const people = kinds.map((k, i) => who(i, { ...k, vendor: ["claude", "codex", "openclaw"][i % 3] }));

  await withRoom({}, (room, main) => {
    const S = room.stats().scale;
    const cache = new Map();                   // img → {bakes, dots}（段は焼き直すたびに読み直す）
    const dotsOf = (img, band) => {
      const bakes = band ? room.stats().fgBakes : 0;
      const hit = cache.get(img);
      if (hit && hit.bakes === bakes) return hit.dots;
      const dots = new Set();
      for (const e of img.ctx.log) {
        if (e[0] !== "fill") continue;
        for (let i = 0; i < e[3] / S; i += 1) for (let j = 0; j < e[4] / S; j += 1) dots.add(`${e[1] / S + i},${e[2] / S + j}`);
      }
      cache.set(img, { bakes, dots });
      return dots;
    };
    const seen = new Map();                    // 人 → 最後に確かめた（位置・コマ・焼き直し）＝止まっている人を毎刻み数え直さない
    const bad = [];
    const hits = { front: 0, behind: 0 };
    const visited = new Set();
    /** 直近のフレームの全員を確かめる */
    function inspect(label) {
      const L = room.layers();
      const imgs = main.ctx.log.filter((e) => e[0] === "img");
      assert.equal(imgs.length, L.length, `${label}: 貼った画像と layers() の数が違う（控えと塗りが別物）`);
      const fg = room.stats().fgBakes;
      for (const a of room.actors()) {
        const sig = `${a.x},${a.y},${a.vendor},${a.cell},${a.flip},${fg}`;
        if (seen.get(a.key) === sig) continue;
        seen.set(a.key, sig);
        const wi = L.indexOf(`person:${a.key}`);
        assert.ok(wi >= 0, `${label}: ${a.key} を貼った記録が無い`);
        const [, wimg, wx, wy] = imgs[wi];
        const mine = [...dotsOf(wimg, false)].map((k) => k.split(",").map(Number)).map(([x, y]) => [x + wx / S, y + wy / S]);
        const later = [];
        for (let j = wi + 1; j < L.length; j += 1) {
          if (L[j].startsWith("band:")) later.push([dotsOf(imgs[j][1], true), imgs[j][2] / S, imgs[j][3] / S]);
        }
        const covered = ([x, y]) => later.some(([d, bx, by]) => d.has(`${x - bx},${y - by}`));
        const footY = a.y + footDY;
        for (const top of tops) {
          const over = mine.filter(([x, y]) => top.mask.has(`${x},${y}`));
          if (!over.length) continue;
          const hidden = over.filter(covered).length;
          if (footY >= top.bottom) {
            hits.front += 1;
            if (hidden) bad.push(`${label}: ${a.key}（足元 y ${footY}）は ${top.id}（足元の下端 ${top.bottom}）の前なのに天板が ${hidden} ドット被った`);
          } else {
            hits.behind += 1;
            if (hidden < over.length) bad.push(`${label}: ${a.key}（足元 y ${footY}）は ${top.id}（足元の下端 ${top.bottom}）の奥なのに ${over.length - hidden} ドット机の上に乗った`);
          }
        }
      }
    }
    /** 誰も歩かなくなるまで 12fps で進める（着いた枠を控える） */
    let t = 0;
    function settle(world, label) {
      for (let k = 0; k < 12 * 20; k += 1) {
        t += 1 / 12;
        room.draw(world, [], t);
        inspect(`${label} t=${t.toFixed(3)}`);
        if (room.stats().walking === 0) return;
      }
      assert.fail(`${label}: 20 秒で誰も歩かなくならない`);
    }
    room.draw({ agents: people.slice(0, 1) }, [], t);          // 最初の世界（誰も歩かない）
    inspect("最初");
    for (let n = 2; n <= people.length; n += 1) {
      const world = { agents: people.slice(0, n) };
      settle(world, `入場 ${people[n - 1].session}`);
      const me = room.marks().find((m) => m.key === people[n - 1].session);
      visited.add(`${me.zone}@${me.x},${me.y}`);
    }
    assert.equal(visited.size, slots - 1, `扉から入って着いた枠が ${visited.size}（全部の枠を歩いていない）`);
    for (let n = people.length - 1; n >= 0; n -= 1) settle({ agents: people.slice(0, n) }, `退場 ${people[n].session}`);
    assert.equal(room.stats().unreachable, 0);
    assert.equal(room.stats().missingCells, 0);
    assert.deepEqual(bad.slice(0, 6), [], `天板と人の前後が画素で逆（全 ${bad.length} 件）`);
    // 空振りしていない: 天板の前を通った・奥に居た場面が両方ある
    assert.ok(hits.front >= 50 && hits.behind >= 50, `前後を確かめた場面が少ない（前 ${hits.front}・奥 ${hits.behind}）`);
  });
});

// ── R99 Phase 4（P4-2a）: 倍率を形で選ぶ（init の scale・setScale）と、ロボの当たりは最寄り ─────────────
// 倍率の正本は ui/core/pxroom.js の roomScale（単体テストは pxroom.test.js）。ここが見るのは「受け取った整数倍で塗る」配線:
//   - どの倍率でも**同じ論理の絵**（塗った矩形を倍率で割ると ×3 と 1 ドットも違わない）＝倍率で絵が変わらない
//   - setScale は焼き置き（背景・手前の段・ロボと印）を作り直すが、帳簿（歩行・印・個体色）は持ち越す
//     ＝倍率を替えても歩いている人が瞬間移動しない・×3 へ戻せば元と同じ絵（golden の部屋が変わらない）
//   - 整数でない・範囲外の倍率では描かない（割合の縮小・非整数のズームをしない掟を、呼び手の値に任せない）
//   - 当たりは描いた矩形の縁から PICK_RADIUS_PX（CSS px）を倍率で割った論理 px まで＝どの倍率でも同じ CSS の距離
const { ROOM: GRID_P4, PICK_RADIUS_PX, ROOM_SCALE_FLOOR, ROOM_SCALE_MIN, ROOM_SCALE_MAX } = await import("../ui/core/pxroom.js");

/** 焼き置きの中身（塗った矩形の控え）を、倍率で割った論理の絵にする。img は幅・丈も論理へ */
const logical = (log, S) => log.map((e) => (e[0] === "fill"
  ? ["fill", e[1] / S, e[2] / S, e[3] / S, e[4] / S, e[5], e[6]]
  : ["img", e[1].width / S, e[1].height / S, JSON.stringify(logical(e[1].ctx.log, S)), e[2] / S, e[3] / S]));
/** 塗った矩形が全部 S の整数倍の格子に乗っているか（1 つでも外れたらドットが割れる） */
const onGrid = (log, S) => log.every((e) => (e[0] === "fill"
  ? [e[1], e[2], e[3], e[4]].every((v) => Number.isInteger(v / S))
  : Number.isInteger(e[2] / S) && Number.isInteger(e[3] / S) && onGrid(e[1].ctx.log, S)));
/** 最初に貼った背景（"bg"）が今の倍率の寸法で、床・壁・家具を塗ってある（空の背景・古い倍率の背景を貼らない） */
function assertBg(main, k, label) {
  const bg = main.ctx.log.find((e) => e[0] === "img");
  assert.ok(bg, `${label}: 背景を貼っていない`);
  assert.deepEqual([bg[1].width, bg[1].height, bg[2], bg[3]], [GRID_P4.w * k, GRID_P4.h * k, 0, 0], `${label}: 背景の寸法・位置が ×${k} でない`);
  const fills = bg[1].ctx.log.filter((e) => e[0] === "fill");
  assert.ok(fills.length > 500, `${label}: 背景に塗った矩形が ${fills.length}（空の背景）`);
  assert.ok(fills.some((e) => e[1] === 0 && e[2] === 0 && e[3] === GRID_P4.w * k && e[4] === GRID_P4.h * k),
    `${label}: 背景の床（全面）が ×${k} の寸法で塗られていない`);
}
const P4_WORLD = { agents: [who(0), who(1, { kind: "think", vendor: "codex" }), who(3, { attention: true, state: "waiting" }),
  who(4, { zone: "lounge", state: "resting", vendor: "openclaw" })] };

test("倍率（P4-2a）: init の scale（×2〜×4）で canvas・焼き置き・貼る座標が k 倍・論理の絵は ×3 と同じ・既定は ROOM.scale", async () => {
  assert.equal(GRID_P4.scale, 3, "前提: 既定の倍率は ×3（3 カラム・golden cockpit.png）");
  const pictures = {};
  // R102-1: ×1（ROOM_SCALE_FLOOR）はスマホの PWA の狭い窓だけが使う＝同じ絵を倍率で割った形であることもここで見る
  for (const k of [ROOM_SCALE_FLOOR, ROOM_SCALE_MIN, 3, ROOM_SCALE_MAX]) {
    await withRoom({ scale: k }, (room, main) => {
      assert.equal(room.stats().scale, k, `×${k}: stats().scale`);
      assert.deepEqual([main.width, main.height], [GRID_P4.w * k, GRID_P4.h * k], `×${k}: canvas の画素`);
      room.draw({ agents: P4_WORLD.agents.slice(0, 3) }, [], 0);
      room.setSelected("s1");
      room.draw(P4_WORLD, [], 1);                       // s4 は扉から歩いて入る
      room.draw(P4_WORLD, [], 2);                       // 歩いている途中（足跡・歩行コマも貼る）
      assertSprites(room, main, `×${k}`);
      assertBg(main, k, `×${k}`);
      assert.ok(onGrid(main.ctx.log, k), `×${k}: 倍率の格子に乗らない矩形がある（ドットが割れる）`);
      assert.ok(room.layers().some((l) => l.startsWith("print:")) && room.layers().includes("select:s1"),
        `×${k}: 前提: 足跡と選択も貼った（門が空振りしない）`);
      pictures[k] = { log: logical(main.ctx.log, k), layers: room.layers(), actors: room.actors() };
    });
  }
  for (const k of [ROOM_SCALE_FLOOR, ROOM_SCALE_MIN, ROOM_SCALE_MAX]) {
    assert.deepEqual(pictures[k].layers, pictures[3].layers, `×${k}: 貼った順が ×3 と違う`);
    assert.deepEqual(pictures[k].actors, pictures[3].actors, `×${k}: 人の論理の位置・コマが ×3 と違う`);
    assert.ok(pictures[k].log.length > 20, "前提: 塗った控えがある");
    assert.deepEqual(pictures[k].log, pictures[3].log, `×${k}: 倍率で割った絵が ×3 と違う（倍率で絵が変わった）`);
  }
  await withRoom({}, (room, main) => {
    assert.equal(room.stats().scale, GRID_P4.scale, "scale を渡さなければ ROOM.scale");
    assert.equal(main.width, GRID_P4.w * GRID_P4.scale);
  });
});

test("倍率（P4-2a）: 整数でない・範囲外の倍率では描かない（init は既定の ROOM.scale・setScale は拒んで今の倍率のまま）", async () => {
  // ×1 は R102-1 から受ける（PWA の床）・×5〜×6 は R110-S6 の眺めるモード（ROOM_SCALE_WATCH_MAX）から受ける
  for (const bad of [7, 2.5, 0, -3, NaN, Infinity, "3", null, 0.5]) {
    await withRoom({ scale: bad }, (room, main) => {
      assert.equal(room.stats().scale, GRID_P4.scale, `init({ scale: ${String(bad)} }) が ×${room.stats().scale}`);
      assert.equal(main.width, GRID_P4.w * GRID_P4.scale);
      const bakes = room.stats().bgBakes;
      assert.equal(room.setScale(bad), false, `setScale(${String(bad)}) を受けた`);
      assert.equal(room.stats().scale, GRID_P4.scale);
      assert.equal(room.stats().bgBakes, bakes, `setScale(${String(bad)}) で背景を焼き直した`);
    });
  }
});

test("倍率（R110-S6）: 眺めるモードの ×5・×6 は受ける（canvas が k 倍・論理の絵は ×3 と同じ）・上限の外（×7）は受けない", async () => {
  const { ROOM_SCALE_WATCH_MAX } = await import("../ui/core/pxroom.js");
  assert.equal(ROOM_SCALE_WATCH_MAX, 6, "前提: 眺めるモードの上限は ×6");
  const pictures = {};
  for (const k of [3, 5, ROOM_SCALE_WATCH_MAX]) {
    await withRoom({}, (room, main) => {
      assert.equal(room.setScale(k), k !== GRID_P4.scale, `setScale(${k})`);
      assert.equal(room.stats().scale, k);
      assert.deepEqual([main.width, main.height], [GRID_P4.w * k, GRID_P4.h * k], `×${k}: canvas の画素`);
      room.draw(P4_WORLD, [], 0);
      assertBg(main, k, `×${k}`);
      assert.ok(onGrid(main.ctx.log, k), `×${k}: 倍率の格子に乗らない矩形がある`);
      pictures[k] = { log: logical(main.ctx.log, k), actors: room.actors() };
      assert.equal(room.setScale(ROOM_SCALE_WATCH_MAX + 1), false, "×7 を受けた");
      assert.equal(room.stats().scale, k);
    });
  }
  for (const k of [5, ROOM_SCALE_WATCH_MAX]) {
    assert.deepEqual(pictures[k].actors, pictures[3].actors, `×${k}: 人の論理の位置・コマが ×3 と違う`);
    assert.deepEqual(pictures[k].log, pictures[3].log, `×${k}: 倍率で割った絵が ×3 と違う`);
  }
});

test("倍率（P4-2a）: setScale は焼き置きを作り直し帳簿は持ち越す・同じ倍率は何もしない・×3 へ戻すと元と同じ絵", async () => {
  let exact3 = null;
  const walkerWorld = { agents: [...P4_WORLD.agents] };
  await withRoom({}, (room, main) => {
    room.draw({ agents: P4_WORLD.agents.slice(0, 3) }, [], 0);
    room.setSelected("s3");
    room.draw(walkerWorld, [], 1.5);                    // s4 が扉から歩いている途中
    const before = { log: logical(main.ctx.log, 3), exact: JSON.stringify(logical(main.ctx.log, 1)),
      layers: room.layers(), actors: room.actors(), walkers: room.walkers(), marks: room.marks() };
    assert.ok(room.stats().walking >= 1, "前提: 歩いている人が居る（帳簿の持ち越しの門が空振りしない）");
    const st0 = room.stats();
    assert.equal(room.setScale(3), false, "同じ倍率で作り直した");
    assert.equal(room.stats().bgBakes, st0.bgBakes);

    assert.equal(room.setScale(2), true);
    const st2 = room.stats();
    assert.equal(st2.scale, 2);
    assert.deepEqual([main.width, main.height], [GRID_P4.w * 2, GRID_P4.h * 2], "×2: canvas の画素");
    room.draw(walkerWorld, [], 1.5);                    // 同じ t の描き直し（倍率を替えた直後の 1 枚）
    // R104 検収: 背景は最初の draw で焼く（init の時点では光の段が分からない）＝倍率を替えた直後の 1 枚で 1 回だけ焼き直す
    assert.equal(room.stats().bgBakes, st0.bgBakes + 1, "×2: 背景を焼き直していない（×3 の背景を貼ると絵が 1.5 倍に割れる）");
    assert.equal(room.stats().fgBakes, st0.fgBakes + 1, "×2: 手前の段を焼き直していない");
    assertSprites(room, main, "×2 に替えた直後");
    assertBg(main, 2, "×2 に替えた直後");
    assert.ok(onGrid(main.ctx.log, 2), "×2 に替えた直後: 格子に乗らない矩形（古い倍率の焼き置きを貼った）");
    assert.deepEqual(room.actors(), before.actors, "倍率を替えたら人が動いた（帳簿を捨てた＝瞬間移動・入場のやり直し）");
    assert.deepEqual(room.walkers(), before.walkers, "倍率を替えたら歩行の帳簿が変わった");
    assert.deepEqual(room.marks(), before.marks, "倍率を替えたら印・コマが変わった");
    assert.deepEqual(logical(main.ctx.log, 2), before.log, "×2 の絵を倍率で割ると ×3 の絵と違う");
    room.draw(walkerWorld, [], 1.5 + 1 / 12);            // 次の刻みも歩き続ける（×2 のまま）
    assert.ok(room.stats().walking >= 1 && onGrid(main.ctx.log, 2), "×2: 次の刻みで歩行が止まった／格子から外れた");
    exact3 = before.exact;
  });
  // ×4 を経て ×3 へ戻し、同じ t（1.5）の絵を描き直す: 画素（倍率で割らない控え）まで最初から ×3 の絵と同じ
  await withRoom({}, (again, main2) => {
    again.draw({ agents: P4_WORLD.agents.slice(0, 3) }, [], 0);
    again.setSelected("s3");
    again.draw(walkerWorld, [], 1.5);
    assert.equal(again.setScale(4), true);
    again.draw(walkerWorld, [], 1.5);
    assert.equal(again.setScale(3), true);
    again.draw(walkerWorld, [], 1.5);
    assert.equal(JSON.stringify(logical(main2.ctx.log, 1)), exact3,
      "×4 を経て ×3 へ戻した絵が、最初から ×3 の絵と画素で違う（golden の部屋が変わる）");
    assert.equal(again.stats().bgBakes, 3, "背景は倍率を替えたときだけ焼く（1 + 2 回）");
  });
});

test("当たり（P4-2a）: 最寄りのロボ・半径は PICK_RADIUS_PX（CSS px）を倍率で割る・退場中は当てない・point も今の倍率", async () => {
  assert.ok(PICK_RADIUS_PX >= 22, "前提: 当たりの半径は 22 CSS px 以上（統括の裁定）");
  for (const k of [ROOM_SCALE_MIN, 3, ROOM_SCALE_MAX]) {
    await withRoom({ scale: k }, (room) => {
      room.draw({ agents: [who(0)] }, [], 0);
      const a = room.actors().find((x) => x.key === "s0");
      const L = a.x * k, T0 = a.y * k, R = (a.x + PX_CELL_W) * k, B = (a.y + PX_CELL_H) * k;
      const cy = (T0 + B) / 2, cx = (L + R) / 2;
      const hit = (x, y) => room.pick(x, y)?.session ?? null;
      assert.equal(hit(cx, cy), "s0", `×${k}: 真ん中を押して当たらない`);
      // 縁から半径（CSS px）の内側は当たり・1 px 先は外れ（倍率で割った論理 px で比べている。ちょうどの境目は
      // 22/3 の浮動小数の誤差で揺れる＝0.5px 内側で見る。半径を 21 にすると内側の点が外れて赤）
      const inR = PICK_RADIUS_PX - 0.5;
      assert.equal(hit(L - inR, cy), "s0", `×${k}: 左の縁から ${inR}px で当たらない`);
      assert.equal(hit(L - PICK_RADIUS_PX - 1, cy), null, `×${k}: 左の縁から ${PICK_RADIUS_PX + 1}px でも当たった`);
      assert.equal(hit(cx, B + inR), "s0", `×${k}: 下の縁から ${inR}px で当たらない`);
      assert.equal(hit(cx, B + PICK_RADIUS_PX + 1), null, `×${k}: 下の縁から ${PICK_RADIUS_PX + 1}px でも当たった`);
      // ひとりの当たりは縦横とも 44 CSS px 以上（×2 の矩形 24×32 は届かない＝矩形だけの当たりに戻すと赤）
      assert.ok(R - L + 2 * PICK_RADIUS_PX >= 44 && B - T0 + 2 * PICK_RADIUS_PX >= 44);
      assert.equal(hit(L - 8, cy), "s0", `×${k}: 矩形の 8px 外（矩形だけの当たりなら外れる）で当たらない`);
      const p = room.point("s0");
      assert.deepEqual([p.left, p.top], [(a.x + PX_CELL_W / 2) * k, (a.y + 4) * k], `×${k}: point が今の倍率でない`);
    });
  }
  // 倍率を替えた後も、当たりと照準は新しい倍率で読む（古い S を握ったままだと 1.5 倍ずれる）
  await withRoom({}, (room) => {
    room.draw({ agents: [who(0)] }, [], 0);
    room.setScale(2);
    room.draw({ agents: [who(0)] }, [], 0);
    const a = room.actors()[0];
    assert.equal(room.pick((a.x + 6) * 2, (a.y + 8) * 2)?.session, "s0", "setScale(2) の後の当たりが ×2 でない");
    assert.equal(room.pick((a.x + 6) * 3 + 40, (a.y + 8) * 3 + 40)?.session ?? null, null, "setScale(2) の後も ×3 の座標で当たる");
    assert.deepEqual(room.point("s0"), { left: (a.x + 6) * 2, top: (a.y + 4) * 2 });
  });
  // 近い人が勝つ（隣り合う机の 2 人の間を押す）・退場中の人は半径の中でも当てない
  await withRoom({}, (room) => {
    room.draw({ agents: [who(0), who(1)] }, [], 0);
    const [s0, s1] = ["s0", "s1"].map((key) => room.actors().find((x) => x.key === key));
    assert.equal(s0.y, s1.y, "前提: 同じ段の机に並んでいる");
    const [l, r] = s0.x < s1.x ? [s0, s1] : [s1, s0];
    const gapL = (l.x + PX_CELL_W) * 3, gapR = r.x * 3, y = (l.y + 8) * 3;
    assert.ok(gapR - gapL > 4, "前提: 2 人の間に隙間がある");
    assert.equal(room.pick(gapL + 1, y)?.session, l.session, "左の人の縁の近くで右の人が当たった");
    assert.equal(room.pick(gapR - 1, y)?.session, r.session, "右の人の縁の近くで左の人が当たった");
    for (let t = 1; t < 3; t += 1 / 12) room.draw({ agents: [who(0)] }, [], t);   // s1 が帰る
    const leaving = room.actors().find((x) => x.key === "s1");
    if (leaving) {
      assert.equal(leaving.leaving, true);
      assert.notEqual(room.pick((leaving.x + 6) * 3, (leaving.y + 8) * 3)?.session, "s1", "退場中の人が当たった");
    }
  });
});

// ── R99-P4-L1: 個体ごとの位相・挨拶（本人「しっかりアバターが動くように」2026-09-28）──────────────
// 約束: 同じ world・同じ光の段の止め絵（frozen＝固定時刻・停止・視差効果を減らす）は t に依らず同じ。
// live だけが位相で散り、挨拶は入力起点で 0.9 秒だけ出る。
const mixed = () => ({ agents: [
  ...[0, 1, 2, 3, 4, 5].map((i) => who(i)),                                      // 打鍵 6 人
  who(6, { state: "waiting", kind: "said", age: 30 }),
  who(7, { state: "resting" }),
  who(8, { attention: true, question: "q" }),
] });

test("部屋の draw: 止め絵（frozen）は同じ光の段なら t に依らず同じ絵・位相も挨拶も出ない", async () => {
  await withRoom({ frozen: () => true, keepPrev: false }, async (room) => {
    const snap = (t) => {
      room.draw(mixed(), [], t);
      // 座標まで比べる（印の置き場所・人の x,y）＝止め絵で印が 2px 上がる・人が散歩先に居る、を名前だけの比較で見逃さない
      return { layers: room.layers().join("|"), cells: room.marks().map((m) => `${m.key}:${m.cell}`).join(","),
        at: room.marks().map((m) => `${m.key}:${m.x},${m.y}:${m.markAt ? `${m.markAt.x},${m.markAt.y}` : "-"}`).join(","),
        calls: room.stats().drawCalls };
    };
    const a = snap(0);
    for (const t of [3.2, 600, 1 / 6, 7.75]) assert.deepEqual(snap(t), a, `t=${t} で止め絵が変わった`);
    assert.equal(room.setGreet("s0"), false, "止めている間に挨拶を受けた");
    assert.deepEqual(snap(10), a, "止めている間の挨拶の頼みが絵を変えた");
    assert.ok(room.marks().filter((m) => m.kind === "type").every((m) => m.cell === "type0"), "止め絵の打鍵が type0 でない");
    // 止め絵の印は定位置（ぴょこっ無しの topMarkAt）＝止め絵同士を比べるだけでは「全部同じだけずれた」を見逃す
    const { topMarkAt } = await import("../ui/core/pxtop.js");
    for (const m of room.marks().filter((x) => x.mark)) {
      assert.deepEqual(m.markAt, topMarkAt({ x: m.x, y: m.y }, m.mark), `止め絵の ${m.key} の印が定位置でない（ぴょこっが止め絵に出た）`);
    }
  });
});

test("部屋の draw: live では同じ種別の人のコマが散る（全員同時に切り替わらない）・同じ t の描き直しは冪等", async () => {
  await withRoom({}, (room) => {
    let mixedSeen = false;
    for (let k = 0; k < 24; k += 1) {
      const t = 30 + k / 6;
      room.draw(mixed(), [], t);
      const typing = room.marks().filter((m) => m.kind === "type").map((m) => m.cell);
      assert.equal(typing.length, 6);
      if (new Set(typing).size > 1) mixedSeen = true;
      const once = room.marks().map((m) => m.cell).join(",");
      room.draw(mixed(), [], t);
      assert.equal(room.marks().map((m) => m.cell).join(","), once, `t=${t} の描き直しでコマが変わった`);
    }
    assert.ok(mixedSeen, "打鍵 6 人が 4 秒のあいだ一度も違うコマにならなかった＝位相が効いていない");
  });
});

test("部屋の draw: 挨拶は受けた次の描画から 0.9 秒・万歳と待機・0.3〜0.6 秒だけ 1px 上に貼る・当たりと座標は動かない", async () => {
  await withRoom({}, (room, main) => {
    const w = { agents: [who(0), who(1)] };
    room.draw(w, [], 40);
    const before = room.actors().find((a) => a.key === "s0");
    assert.equal(room.setGreet("nobody"), false, "居ない人の挨拶を受けた");
    assert.equal(room.setGreet("s0"), true);
    const S = room.stats().scale;
    const spriteY = () => main.ctx.log.filter((e) => e[0] === "img" && e[2] === before.x * S && e[1].height === 16 * S)
      .map((e) => e[3] / S);
    room.draw(w, [], 41);
    assert.equal(room1(room, "s0").kind, "greet");
    assert.equal(room1(room, "s0").cell, "done0");
    assert.equal(room1(room, "s1").kind, "type", "頼んでいない人まで挨拶した");
    assert.equal(room.stats().greeting, 1);
    room.draw(w, [], 41 + 2 / 6);                        // 0.33 秒: 待機のコマ・1px 跳ねる
    assert.equal(room1(room, "s0").cell, "idle0");
    assert.deepEqual(spriteY(), [before.y - 1], "跳ねの間に 1px 上へ貼っていない");
    const now = room.actors().find((a) => a.key === "s0");
    assert.deepEqual([now.x, now.y], [before.x, before.y], "挨拶で drawn の座標が動いた（当たり・名札がずれる）");
    room.draw(w, [], 41 + 2 / 6);                        // 同じ t の描き直しは冪等
    assert.equal(room1(room, "s0").cell, "idle0");
    room.draw(w, [], 41 + 4 / 6);
    assert.equal(room1(room, "s0").cell, "done0");
    assert.deepEqual(spriteY(), [before.y], "跳ねの窓の外で上に貼った");
    room.draw(w, [], 41.95);                             // 0.95 秒: 終わっている
    assert.equal(room1(room, "s0").kind, "type", "0.9 秒を過ぎても挨拶が続いた");
    assert.equal(room.stats().greeting, 0);
  });
});


// ── R99-P4-L2: 暮らし（散歩）＝返事を 1 分以上待っている机の人が散歩先へ行き、同じ机へ戻る ──────────────
test("部屋の draw: 散歩は待っている人だけ・散歩先に着いて同じ机へ戻る・働いている人は動かない・届かない 0・止め絵と life:false は動かない", async () => {
  const w = { agents: [
    who(0, { state: "waiting", kind: "said", age: 120 }), who(1, { state: "waiting", kind: "said", age: 300 }),
    who(2), who(3),
  ] };
  const spot = new Set(LIFE_SPOTS.map((s) => `${s.x},${s.y}`));
  const run = async (opts, fn) => withRoom(opts, (room) => {
    const home = new Map();
    const seen = new Map();                        // key → { arrived, back, moved }
    let maxAway = 0;
    for (let k = 0; k <= 360 * 6; k += 1) {
      const t = k / 6;
      room.draw(w, [], t);
      maxAway = Math.max(maxAway, room.stats().away);
      for (const a of room.actors()) {
        if (k === 0) home.set(a.key, [a.x, a.y]);
        const rec = seen.get(a.key) || { arrived: false, back: false, moved: false };
        const [hx, hy] = home.get(a.key);
        if (a.x !== hx || a.y !== hy) rec.moved = true;
        if (spot.has(`${a.x},${a.y}`)) rec.arrived = true;
        if (rec.arrived && a.x === hx && a.y === hy) rec.back = true;
        seen.set(a.key, rec);
      }
    }
    return fn({ seen, maxAway, stats: room.stats() });
  });
  await run({}, ({ seen, maxAway, stats }) => {
    for (const key of ["s0", "s1"]) {
      assert.ok(seen.get(key).arrived, `${key}（返事待ち）が 6 分のうちに散歩先に着かなかった`);
      assert.ok(seen.get(key).back, `${key} が散歩先から同じ机へ戻らなかった`);
    }
    for (const key of ["s2", "s3"]) assert.equal(seen.get(key).moved, false, `${key}（打鍵中）が動いた`);
    assert.ok(maxAway >= 1);
    assert.equal(stats.unreachable, 0, "散歩の経路が届かず瞬間移動した");
    assert.equal(stats.life, true);
  });
  await run({ life: false }, ({ seen, maxAway, stats }) => {
    for (const [key, r] of seen) assert.equal(r.moved, false, `life:false なのに ${key} が動いた`);
    assert.equal(maxAway, 0);
    assert.equal(stats.life, false);
  });
  await run({ frozen: () => true }, ({ seen, maxAway }) => {
    for (const [key, r] of seen) assert.equal(r.moved, false, `止め絵なのに ${key} が動いた`);
    assert.equal(maxAway, 0);
  });
});


test("部屋の draw: live の履歴（挨拶・✓・印のぴょこっ・散歩）の後で止めた絵は、素の止め絵と 1 か所も違わない", async () => {
  const w = { agents: [who(0, { state: "waiting", kind: "said", age: 120 }), who(1), who(2, { kind: "think" }),
    who(3, { attention: true, question: "q" })] };
  const cleared = { agents: [w.agents[0], w.agents[1], w.agents[2], who(3)] };
  const plain = await withRoom({ frozen: () => true, keepPrev: false }, (room, main) => {
    room.draw(w, [], 0);
    return { marks: JSON.stringify(room.marks()), log: JSON.stringify(main.ctx.log.map((e) => (e[0] === "img" ? ["img", e[2], e[3]] : e))) };
  });
  let still = false;
  const after = await withRoom({ frozen: () => still, keepPrev: false }, (room, main) => {
    for (let k = 0; k <= 60 * 6; k += 1) {                 // 60 秒 live で回す（散歩の窓・印・✓・挨拶の途中で止める）
      room.draw(k > 200 ? cleared : w, [], k / 6);
      if (k === 300) room.setGreet("s1");
    }
    still = true;
    room.draw(w, [], 61);
    return { marks: JSON.stringify(room.marks()), log: JSON.stringify(main.ctx.log.map((e) => (e[0] === "img" ? ["img", e[2], e[3]] : e))) };
  });
  assert.equal(after.marks, plain.marks, "live の履歴の後で止めた絵の人・コマ・印の置き場所が素の止め絵と違う");
  assert.equal(after.log, plain.log, "live の履歴の後で止めた絵の塗り（座標）が素の止め絵と違う");
});

test("部屋の draw: 停止から戻った最初のフレームは散歩させない（散歩先へ瞬間移動しない）・次のフレームから机を出て歩く", async () => {
  const w = { agents: [who(0, { state: "waiting", kind: "said", age: 120 }), who(1)] };
  // s0 の散歩の窓が開いている t を探す（止めている間に窓が開く）
  const { strollWindow } = await import("../ui/core/life.js");
  let tw = 60;
  while (!strollWindow("s0", tw).open || !strollWindow("s0", tw + 3).open) tw += 1;
  let still = false;
  await withRoom({ frozen: () => still }, (room) => {
    room.draw(w, [], 30);
    room.draw(w, [], 30 + 1 / 6);
    const home = room.actors().find((a) => a.key === "s0");
    still = true;
    room.draw(w, [], tw);
    still = false;
    room.draw(w, [], tw + 1 / 6);                          // 戻った最初のフレーム
    const back = room.actors().find((a) => a.key === "s0");
    assert.deepEqual([back.x, back.y], [home.x, home.y], "停止から戻った瞬間に散歩先へ瞬間移動した");
    assert.equal(room.stats().away, 0);
    room.draw(w, [], tw + 2 / 6);
    assert.equal(room.stats().away, 1, "戻った次のフレームで散歩を始めない");
    assert.ok(room.actors().find((a) => a.key === "s0").walking, "散歩先へ歩いて向かっていない（机から歩き出していない）");
  });
});

// ── R99-P4-L3: 机の画面が語る ──────────────────────────────────────────────────
test("部屋の draw: 席に座って打鍵・考え中の人の画面は live で送られ（行・点）、止め絵・散歩中・待っている人には貼らない", async () => {
  const { DESKS } = await import("../ui/core/pxroom.js");
  const w = { agents: [who(0), who(1, { kind: "think" }), who(2, { state: "waiting", kind: "said", age: 30 })] };
  await withRoom({}, (room, main) => {
    const seen = new Map();                                // key → 貼ったタイルの集合
    for (let k = 0; k <= 36; k += 1) {
      room.draw(w, [], 10 + k / 6);
      for (const l of room.layers().filter((x) => x.startsWith("screen:"))) {
        const [, key, tile] = l.split(":");
        if (!seen.has(key)) seen.set(key, new Set());
        seen.get(key).add(tile);
      }
    }
    assert.deepEqual([...(seen.get("s0") || [])].sort(), ["screenType0", "screenType1"], "打鍵の人の画面が 2 枚の行で送られない");
    assert.deepEqual([...(seen.get("s1") || [])].sort(), ["screenThink0", "screenThink1"], "考え中の人の画面に点が出ない");
    assert.equal(seen.has("s2"), false, "待っている人の画面を送った");
    // 置き場所はその人の机のモニタの画面の内側（モニタ desk+18,−9 の 1px 内側）
    const S = room.stats().scale;
    const a0 = room.actors().find((a) => a.key === "s0");
    const desk = DESKS[a0.seat];
    const imgs = main.ctx.log.filter((e) => e[0] === "img" && e[1].width === 10 * S && e[1].height === 4 * S);
    assert.ok(imgs.every((e) => DESKS.some((d) => e[2] === (d.x + 19) * S && e[3] === (d.y - 8) * S)), "画面のタイルがモニタの画面の外に貼られた");
    assert.ok(desk);
  });
  await withRoom({ frozen: () => true }, (room) => {
    for (const t of [0, 0.5, 1, 3.2]) {
      room.draw(w, [], t);
      assert.equal(room.stats().screens, 0, `止め絵（t=${t}）で画面を貼った`);
    }
  });
});

// ── R103-6: 時刻の光・仕事の小物・助手・止まった理由の赤い紙 ────────────────
const LIGHT_STAGES = ["morning", "day", "evening", "night"];

/** 背景以外の塗り（手前の家具・ロボ・状態の印・机の光）。光の段でこれらの色や形を変えない。 */
function withoutBackground(main, S) {
  const bg = main.ctx.log.find((e) => e[0] === "img");
  return logical(main.ctx.log.filter((e) => e !== bg), S);
}

/** 奥の家具の不透明なドットに最後に塗った色。窓以外のガラス・紙・机の材質が時刻で染まらない門。 */
async function backFurnitureColors(main, S) {
  const { FURNITURE } = await import("../ui/core/pxroom.js");
  const { PX_TILES } = await import("../ui/cockpit/tiles.js");
  const { TILE_PALETTE } = await import("../ui/cockpit/palette.js");
  const img = main.ctx.log.find((e) => e[0] === "img")[1];
  const fills = img.ctx.log.filter((e) => e[0] === "fill");
  const points = new Set();
  for (const f of FURNITURE.filter((f) => f.layer === "back" && f.tile !== "window")) {
    PX_TILES[f.tile].forEach((row, y) => [...row].forEach((ch, x) => {
      if (Object.hasOwn(TILE_PALETTE, ch)) points.add(`${f.x + x},${f.y + y}`);
    }));
  }
  return [...points].sort().map((key) => {
    const [x, y] = key.split(",").map(Number);
    const last = fills.findLast((e) => e[1] <= x * S && e[2] <= y * S && e[1] + e[3] > x * S && e[2] + e[4] > y * S);
    return [key, last?.[5], last?.[6]];
  });
}

test("部屋の draw（R103-6）: 4 段の光は背景だけを変え、家具・ロボ・状態の色と焼き置きは保つ", async () => {
  await withRoom({ frozen: () => true, keepPrev: false }, async (room, main) => {
    const S = room.stats().scale;
    const w = P4_WORLD;
    room.draw(w, [], 20, { lightStage: "day" });
    const day = logical(main.ctx.log, S);
    const objects = withoutBackground(main, S);
    const furniture = await backFurnitureColors(main, S);
    assert.ok(furniture.length > 500, "前提: 窓以外の奥の家具のドットを拾えていない");
    const initial = room.stats();
    const backs = new Set();
    for (const lightStage of LIGHT_STAGES) {
      room.draw(w, [], 20, { lightStage });
      assert.deepEqual(withoutBackground(main, S), objects, `${lightStage}: 光で手前の家具・ロボ・状態の色や形が変わった`);
      assert.deepEqual(await backFurnitureColors(main, S), furniture, `${lightStage}: 窓以外の奥の家具の色が変わった`);
      const picture = logical(main.ctx.log, S);
      backs.add(JSON.stringify(picture[0]));
      const baked = room.stats();
      room.draw(w, [], 20, { lightStage });
      assert.deepEqual(logical(main.ctx.log, S), picture, `${lightStage}: 同じ world・t・段の描き直しで絵が変わった`);
      room.draw(w, [], 700, { lightStage });
      assert.deepEqual(logical(main.ctx.log, S), picture, `${lightStage}: 止め絵が t で変わった`);
      assert.equal(room.stats().bgBakes, baked.bgBakes, `${lightStage}: 同じ段で背景を焼き直した`);
      assert.equal(room.stats().fgBakes, initial.fgBakes, `${lightStage}: 光の段だけで手前の家具を焼き直した`);
      assert.equal(room.stats().spriteBakes, initial.spriteBakes, `${lightStage}: 光の段だけでロボや印を焼き直した`);
    }
    assert.equal(backs.size, 4, "朝・昼・夕・夜の背景が 4 種になっていない（段を渡しても古い背景を貼っている）");
    room.draw(w, [], 20, { lightStage: "day" });
    assert.deepEqual(logical(main.ctx.log, S), day, "昼へ戻っても元の背景へ戻らない（焼き置きが別の段のまま）");
    room.draw(w, [], 20);
    assert.deepEqual(logical(main.ctx.log, S), day, "段の省略は昼の絵を受け止めない");
  });
});

/** タイルの不透明なドット [x,y,色]（生成物が正本。描画側の位置や cache の鍵は読まない）。 */
async function tileDots(name, colors = null) {
  const { PX_TILES } = await import("../ui/cockpit/tiles.js");
  const { TILE_PALETTE } = await import("../ui/cockpit/palette.js");
  assert.ok(PX_TILES[name], `前提: 生成物に ${name} が無い`);
  const paint = colors || TILE_PALETTE;
  const dots = [];
  PX_TILES[name].forEach((row, y) => [...row].forEach((ch, x) => {
    if (paint[ch]) dots.push([x, y, paint[ch]]);
  }));
  return dots;
}

/** 段に焼いた実際のドットから、タイルと一致する塗りを探す。返す座標は部屋の論理 px。 */
async function bandTiles(room, main, name, colors = null) {
  const S = room.stats().scale;
  const expected = await tileDots(name, colors);
  const imgs = main.ctx.log.filter((e) => e[0] === "img");
  const copies = [];
  room.layers().forEach((layer, n) => {
    if (!layer.startsWith("band:")) return;
    const [, img, bx, by] = imgs[n];
    const fills = img.ctx.log.filter((e) => e[0] === "fill");
    for (let i = 0; i <= fills.length - expected.length; i += 1) {
      const dx = fills[i][1] / S - expected[0][0], dy = fills[i][2] / S - expected[0][1];
      if (expected.every(([x, y, color], k) => {
        const e = fills[i + k];
        return e[1] === (x + dx) * S && e[2] === (y + dy) * S && e[3] === S && e[4] === S && e[5] === color && e[6] === 1;
      })) copies.push({ x: bx / S + dx, y: by / S + dy, layer });
    }
  });
  return copies;
}

const WORK_PROPS = [
  { verb: "編集中", target: "room.js", tile: "deskScreen" },
  { verb: "調査中", tile: "deskDocuments" },
  { verb: "段取り整理中", tile: "deskBoard" },
];
const workProps = (i) => { const { tile, ...work } = WORK_PROPS[i]; return work; };

test("部屋の draw（R103-6）: 仕事の 3 種の小物と赤い紙を実際の机へ焼き、種類・err が変われば古い絵を捨てる", async () => {
  const { DESKS } = await import("../ui/core/pxroom.js");
  const { TILE_PALETTE, RAMP: COLORS } = await import("../ui/cockpit/palette.js");
  const red = { ...TILE_PALETTE, r: COLORS.red[1], s: COLORS.red[2], t: COLORS.red[3] };
  await withRoom({ frozen: () => true, keepPrev: false }, async (room, main) => {
    const S = room.stats().scale;
    const plain = { agents: [who(0)] };
    room.draw(plain, [], 0);
    const original = withoutBackground(main, S);
    let fg = room.stats().fgBakes;
    const desk = DESKS[room.actors()[0].seat];
    for (const { tile, ...work } of WORK_PROPS) {
      const world = { agents: [who(0, work)] };
      room.draw(world, [], 0);
      const copies = await bandTiles(room, main, tile);
      assert.equal(copies.length, 1, `${tile}: 仕事のタイルを机に 1 枚焼いていない`);
      assert.ok(copies[0].x >= desk.x && copies[0].x < desk.x + 30 && copies[0].y >= desk.y - 9 && copies[0].y <= desk.y,
        `${tile}: 小物がその人の机の上にない（${JSON.stringify(copies[0])}）`);
      assert.equal(room.stats().deskItems, 1);
      assert.equal(room.stats().errorNotes, 0);
      assert.equal(room.stats().fgBakes, fg + 1, `${tile}: 同じ在席のまま仕事だけ変わったのに段を焼き直さない`);
      fg = room.stats().fgBakes;
      const picture = logical(main.ctx.log, S);
      room.draw(world, [], 600);
      assert.deepEqual(logical(main.ctx.log, S), picture, `${tile}: 止め絵の小物が t で変わった`);
      assert.equal(room.stats().fgBakes, fg, `${tile}: 同じ種類で段を焼き直した`);
    }
    room.draw(plain, [], 0);
    assert.equal(room.stats().deskItems, 0);
    assert.deepEqual(withoutBackground(main, S), original, "種類のない仕事へ戻っても元の机の小物へ戻らない");
    fg = room.stats().fgBakes;
    const stopped = { agents: [who(0, { state: "waiting", err: { k: "api", ago: 30 } })] };
    room.draw(stopped, [], 0);
    assert.equal(room.stats().errorNotes, 1);
    assert.equal(room.stats().fgBakes, fg + 1, "同じ席で err が届いても段を焼き直さない");
    const papers = await bandTiles(room, main, "errorNote", red);
    assert.equal(papers.length, 1, "赤い紙の生成タイルを 1 枚焼いていない");
    assert.ok(papers[0].x >= desk.x && papers[0].x < desk.x + 30 && papers[0].y >= desk.y - 3 && papers[0].y < desk.y + 4,
      "赤い紙がその人の机にない");
    const errorPicture = logical(main.ctx.log, S);
    fg = room.stats().fgBakes;
    for (const t of [1 / 6, 3.2, 700]) {
      room.draw(stopped, [], t);
      assert.deepEqual(logical(main.ctx.log, S), errorPicture, "赤い紙が点滅した／止め絵を変えた");
      assert.equal(room.stats().fgBakes, fg, "err が変わらないのに段を焼き直した");
    }
    room.draw({ agents: [who(0, { err: stopped.agents[0].err })] }, [], 0);
    assert.equal(room.stats().errorNotes, 0, "再開しても古い err の紙を貼っている");
    assert.deepEqual(withoutBackground(main, S), original, "赤い紙を消した後で元の机へ戻らない");
  });
});

test("部屋の draw（R103-6）: 会議室の本人に助手を 0/1/3/3 体添え、人数・名札・当たりに足さない", async () => {
  const { TILE_PALETTE, RAMP: COLORS } = await import("../ui/cockpit/palette.js");
  const colors = { ...TILE_PALETTE, e: COLORS.bot[0], f: COLORS.bot[1], g: COLORS.bot[2], h: COLORS.bot[3] };
  const dots = await tileDots("assistant", colors);
  await withRoom({ frozen: () => true, keepPrev: false }, (room, main) => {
    const S = room.stats().scale;
    const world = (minionsLive) => ({ agents: [who(0, { zone: "meeting", minionsLive })] });
    room.draw(world(0), [], 0);
    const persons = room.actors();
    assert.equal(persons[0].zone, "meeting", "前提: 本人が会議室にいない");
    const samplePicks = () => {
      const got = [];
      for (let y = 0; y < 160; y += 5) for (let x = 0; x < 184; x += 5) got.push(room.pick(x * S, y * S)?.session || null);
      return got;
    };
    const picks = samplePicks();
    const point = room.point("s0");
    for (const [count, want] of [[0, 0], [1, 1], [3, 3], [5, 3]]) {
      room.draw(world(count), [], 0);
      const layers = room.layers();
      const imgs = main.ctx.log.filter((e) => e[0] === "img");
      const assistantLayers = layers.filter((l) => l.startsWith("assistant:"));
      assert.equal(assistantLayers.length, want, `minionsLive=${count}: 助手を ${want} 体貼っていない`);
      assert.equal(room.stats().assistants, want);
      assert.deepEqual(room.actors(), persons, "助手を actors に加えた／本人の位置やセルを変えた");
      assert.deepEqual(samplePicks(), picks, "助手を当たりの対象に加えた");
      assert.deepEqual(room.point("s0"), point, "助手で本人の照準を変えた");
      assert.equal(room.marks().length, 1, "助手を名札・状態の印の人数に加えた");
      for (const name of assistantLayers) {
        const [, img, x, y] = imgs[layers.indexOf(name)];
        assert.deepEqual(img.ctx.log.filter((e) => e[0] === "fill").map((e) => [e[1] / S, e[2] / S, e[5]]), dots,
          `${name}: 生成タイルの助手と違うドットを貼った`);
        assert.ok(Number.isInteger(x / S) && Number.isInteger(y / S), `${name}: 整数の位置でない`);
        const a = persons[0];
        assert.ok(Math.abs(x / S - a.x) <= PX_CELL_W + 5 && Math.abs(y / S - a.y) <= PX_CELL_H,
          `${name}: 会議室の本人の脇にいない`);
        // 12×16 のセルの端は透明。矩形の交差ではなく本人の不透明なドットへの重なりを見る。
        const body = new Set();
        PX_CELLS[a.vendor][a.cell].forEach((row, ry) => [...row].forEach((ch, rx) => {
          if (ch !== ".") body.add(`${a.x + rx},${a.y + ry}`);
        }));
        assert.ok(dots.every(([dx, dy]) => !body.has(`${x / S + dx},${y / S + dy}`)), `${name}: 本人の胴のドットへ重ねた`);
        assert.equal(room.point(name), null, "助手そのものを照準の対象にした");
      }
      const picture = logical(main.ctx.log, S);
      room.draw(world(count), [], 700);
      assert.deepEqual(logical(main.ctx.log, S), picture, "止め絵の助手が t で変わった");
    }
  });
});

test("部屋の draw（R103-6）: 席を予約して会議室へ行った本人の机に仕事の小物と赤い紙を残す", async () => {
  const { DESKS } = await import("../ui/core/pxroom.js");
  const { TILE_PALETTE, RAMP: COLORS } = await import("../ui/cockpit/palette.js");
  const red = { ...TILE_PALETTE, r: COLORS.red[1], s: COLORS.red[2], t: COLORS.red[3] };
  await withRoom({ frozen: () => true }, async (room, main) => {
    room.draw({ agents: [who(0, workProps(0))] }, [], 0);
    const desk = DESKS[room.actors()[0].seat];
    room.draw({ agents: [who(0, { ...workProps(0), zone: "meeting", minionsLive: 1 })] }, [], 1);
    assert.equal(room.actors()[0].zone, "meeting");
    assert.equal(room.stats().deskItems, 1);
    assert.equal(room.stats().assistants, 1);
    assert.ok((await bandTiles(room, main, "deskScreen")).some((p) => p.x >= desk.x && p.x < desk.x + 30),
      "会議室へ行くと予約した机の仕事の小物が消える");
    room.draw({ agents: [who(0, { ...workProps(0), zone: "meeting", minionsLive: 1,
      state: "waiting", err: { k: "api", ago: 3 } })] }, [], 2);
    assert.equal(room.stats().errorNotes, 1);
    assert.ok((await bandTiles(room, main, "errorNote", red)).some((p) => p.x >= desk.x && p.x < desk.x + 30),
      "会議室にいる本人の予約机に赤い紙を焼いていない");
  });
});

test("部屋の draw（R103-6）: 再生は同じ world・光の段なら t に依らず小物・助手・赤い紙も同じ止め絵", async () => {
  const world = { agents: [
    who(0, workProps(0)),
    who(1, { ...workProps(1), state: "waiting", err: { k: "api", ago: 3 } }),
    who(2, { ...workProps(2), kind: "think" }),
    who(3, { zone: "meeting", minionsLive: 3 }),
  ] };
  await withRoom({}, (room, main) => {
    const S = room.stats().scale;
    const options = { replay: true, lightStage: "night" };
    room.draw(world, [], 0, options);
    assert.equal(room.stats().assistants, 3, "前提: 再生の助手が出ていない");
    assert.equal(room.stats().deskItems, 3, "前提: 再生の小物が出ていない");
    assert.equal(room.stats().errorNotes, 1, "前提: 再生の赤い紙が出ていない");
    const picture = logical(main.ctx.log, S);
    const persons = room.actors();
    const bakes = room.stats();
    for (const t of [3.2, 600, 1 / 6, 7.75]) {
      room.draw(world, [], t, options);
      assert.deepEqual(logical(main.ctx.log, S), picture, `再生 t=${t}: 止め絵が変わった`);
      assert.deepEqual(room.actors(), persons, `再生 t=${t}: 人の位置・コマが変わった`);
      assert.equal(room.stats().walking, 0, "再生で歩いた");
      assert.equal(room.stats().bgBakes, bakes.bgBakes, "同じ段の再生で背景を焼き直した");
      assert.equal(room.stats().fgBakes, bakes.fgBakes, "同じ world の再生で机を焼き直した");
      assert.equal(room.stats().spriteBakes, bakes.spriteBakes, "同じ止め絵の再生でスプライトを焼き直した");
    }
  });
});

test("部屋の draw（R103-6）: 小物・赤い紙・助手を足した入退場の山でも drawCalls は実数で上限以内", async () => {
  await withRoom({}, (room, main) => {
    const crowd = (from) => ({ agents: Array.from({ length: 26 }, (_, i) => who(from + i, {
      ...workProps(i % WORK_PROPS.length),
      zone: i < 9 ? "desk" : i < 18 ? "queue" : i < 20 ? "lounge" : i < 23 ? "meeting" : "external",
      attention: i >= 9 && i < 18,
      state: i < 18 ? "waiting" : i < 20 ? "resting" : "working",
      ...(i < 9 ? { err: { k: "api", ago: 3 } } : {}), minionsLive: 5,
    })) });
    room.draw(crowd(100), [], 10);
    assert.equal(room.stats().assistants, 78, "前提: 全員に 3 体ずつ助手を貼っていない");
    assert.equal(room.stats().deskItems, 9);
    assert.equal(room.stats().errorNotes, 9);
    room.draw(crowd(100), [], 10.5);
    let peak = 0;
    for (let k = 1; k <= 12 * 4; k += 1) {
      room.draw(crowd(200), [], 10.5 + k / 12, { lightStage: "evening" });
      const calls = main.ctx.log.filter((e) => e[0] === "fill" || e[0] === "img").length;
      assert.equal(room.stats().drawCalls, calls, "drawCalls が実際に呼んだ数と違う");
      peak = Math.max(peak, calls);
      if (k === 6) assert.ok(room.stats().leaving > 0 && room.stats().entering > 0, "前提: 入退場が同時でない");
    }
    assert.ok(peak <= DRAW_CALL_LIMIT, `助手を添えた入退場の山で drawCalls ${peak} > ${DRAW_CALL_LIMIT}`);
    assert.equal(room.stats().missingTiles, 0);
  });
});

// ── R110-S4: 歩行のテンポ（24px/秒・立ち上がり・到着の一拍・足跡の間隔）───────────────────────────
test("足跡（R110-S4）: 間隔は歩行の 1 周（PRINT_STEP＝WALK_STRIDE＝8px）・経路の上の 8px ごとの点に置く", () => {
  assert.equal(WALK_STRIDE, 8);
  assert.equal(PRINT_STEP, WALK_STRIDE, "歩幅を縮めたのに足跡の間隔を半周のままにした（足跡が詰まって帯になる）");
  const path = planPath([153, 141], [101, 45]);
  // 十分に歩いた後の 3 つの足跡は、経路の上で PRINT_STEP ずつ離れた点の脇（足元の中心から 1〜3px）にある
  const t = 10 + 100 / WALK_SPEED;
  const prints = footprints(path, 10, t);
  assert.equal(prints.length, PRINTS_PER_WALKER);
  const kTop = Math.floor(100 / PRINT_STEP) - 1;
  prints.forEach(([x, y], i) => {
    const p = posAt(path, 0, (kTop - i) * PRINT_STEP, 1);
    const cx = p.x + FOOT_CENTER[0], cy = p.y + FOOT_CENTER[1];
    assert.ok(Math.abs(x + 1 - cx) + Math.abs(y + 1 - cy) <= 3, `足跡 ${i} (${x},${y}) が経路の ${(kTop - i) * PRINT_STEP}px の点の脇にない`);
  });
});

test("部屋の draw（R110-S4）: 席の人は立ち上がり（2 刻み・1px 上に貼る・影と当たりは床のまま・12fps・挨拶を受けない）→ 歩いて着いて一拍 → 打鍵", async () => {
  await withRoom({}, (room, main) => {
    const S = room.stats().scale;
    const desk = { agents: [who(0), who(1)] };
    room.draw(desk, [], 20);
    room.draw(desk, [], 20 + 1 / 6);
    const home = room.actors().find((a) => a.key === "s1");
    // s1 が ❗ になる＝受付へ。最初の 2 刻み（RISE_SEC）は席で立ち上がる
    const call = { agents: [who(0), who(1, { attention: true, state: "waiting", question: "q" })] };
    const t0 = 20 + 2 / 6;
    const rise = Math.round(RISE_SEC * 12);
    assert.equal(rise, 2);
    for (let k = 0; k < rise; k += 1) {
      const t = t0 + k / 12;
      room.draw(call, [], t);
      const m = room1(room, "s1");
      const a = room.actors().find((x) => x.key === "s1");
      assert.deepEqual([m.kind, m.cell, m.lift, m.walk], ["rise", "idle0", 1, null], `k=${k}: 立ち上がりのコマ`);
      assert.deepEqual([a.x, a.y], [home.x, home.y], `k=${k}: 立ち上がりの間に drawn の座標が動いた（当たり・名札がずれる）`);
      assert.ok(a.walking && a.rising, `k=${k}: 立ち上がりの人を歩く人の旗に数えない`);
      const st = room.stats();
      assert.equal(st.walking, 1, `k=${k}: 立ち上がりを stats().walking に数えない`);
      assert.equal(st.fps, 12, `k=${k}: 立ち上がりの間が 12fps でない`);
      // ロボは 1px 上・影は床（足元の行）のまま
      const sprite = main.ctx.log.filter((e) => e[0] === "img" && e[2] === home.x * S && e[1].height === 16 * S).map((e) => e[3] / S);
      assert.deepEqual(sprite, [home.y - 1], `k=${k}: 立ち上がりで 1px 上に貼っていない`);
      assert.ok(main.ctx.log.some((e) => e[0] === "fill" && e[1] === (home.x + 3) * S && e[2] === (home.y + 16) * S && e[6] === 0.55),
        `k=${k}: 影が床（足元の行）に無い`);
      assert.equal(room.setGreet("s1"), false, `k=${k}: 立ち上がっている人の挨拶を受けた（手を振らないのに受けた）`);
      assert.ok(!room.layers().some((l) => l.startsWith("print:s1")), "立ち上がりの間に足跡");
      assert.ok(!room.layers().some((l) => l.startsWith("screen:s1")), "立ち上がりの間に画面を送った");
    }
    // 歩き出す（歩行コマ 0 から・席の位置から）
    room.draw(call, [], t0 + rise / 12);
    const go = room1(room, "s1");
    assert.ok(go.walk && go.kind === "walk" && go.cell.endsWith("0") && go.lift === 0, `立ち上がりの後に歩き出さない（${go.kind} ${go.cell}）`);
    // 着くまで歩かせる → 一拍（正面の待機）→ 挙手
    let t = t0 + rise / 12;
    let arrived = null;
    const after = [];
    for (let k = 1; k < 12 * 14 && after.length < 8; k += 1) {
      t = t0 + (rise + k) / 12;
      room.draw(call, [], t);
      const m = room1(room, "s1");
      if (m.walk) continue;
      if (arrived === null) arrived = t;
      after.push(m.kind);
    }
    assert.ok(arrived !== null, "14 秒で受付に着かない");
    assert.equal(room1(room, "s1").zone, "queue");
    assert.deepEqual(after.slice(0, 5), ["settle", "settle", "settle", "settle", "raise"], `着いた後のコマ ${after}`);
  });
});

test("部屋の draw（R110-S4）: 立ち上がり・一拍の途中で止めた絵は、素の止め絵と 1 か所も違わない（frozen では arrivedAt も立ち上がりも出ない）", async () => {
  const a = { agents: [who(0), who(1), who(3, { attention: true, question: "q" })] };
  // s1 が ❗ になり（席で立ち上がる）、s3 の ❗ が片付く（受付で立ち上がって机へ）・s5 が扉から入る
  const b = { agents: [who(0), who(1, { attention: true, question: "q" }), who(3), who(5)] };
  const plain = await withRoom({ frozen: () => true, keepPrev: false }, (room, main) => {
    room.draw(b, [], 0);
    return { marks: JSON.stringify(room.marks()), log: JSON.stringify(main.ctx.log.map((e) => (e[0] === "img" ? ["img", e[2], e[3]] : e))) };
  });
  // 止める瞬間: 立ち上がりの途中（変わった次の刻み）と、着いた一拍の途中（入場の人が着いた次の刻み）の 2 通り
  for (const stopAfter of ["rise", "settle"]) {
    let still = false;
    const after = await withRoom({ frozen: () => still, keepPrev: false }, (room, main) => {
      room.draw(a, [], 10);
      room.draw(a, [], 10 + 1 / 6);
      let t = 10 + 2 / 6;
      let seen = false;
      for (let k = 0; k < 12 * 20 && !seen; k += 1) {
        t = 10 + 2 / 6 + k / 12;
        room.draw(b, [], t);
        const ms = room.marks();
        if (stopAfter === "rise") seen = ms.some((m) => m.kind === "rise");
        else seen = ms.some((m) => m.kind === "settle");
      }
      assert.ok(seen, `前提: live で ${stopAfter} のコマが出ない（門が空振り）`);
      still = true;
      room.draw(b, [], t + 1 / 12);
      return { marks: JSON.stringify(room.marks()), log: JSON.stringify(main.ctx.log.map((e) => (e[0] === "img" ? ["img", e[2], e[3]] : e))) };
    });
    assert.equal(after.marks, plain.marks, `${stopAfter} の途中で止めた絵の人・コマ・lift・settled が素の止め絵と違う`);
    assert.equal(after.log, plain.log, `${stopAfter} の途中で止めた絵の塗り（座標）が素の止め絵と違う`);
  }
});

// ── R110-S5・S2: 用事（道具に連動して席を離れて戻る）・証拠の紙・画面の模様・打鍵の速さ・考え中の点・職業の小道具 ──────────
const { AWAY_MAX, EVIDENCE_PAPER_SEC, trayPapers } = await import("../ui/cockpit/room.js");
const { DESKS: DESKS_R, ERRAND_SPOTS, EVIDENCE_TRAY } = await import("../ui/core/pxroom.js");
const { ERRAND, EVIDENCE_ERRAND } = await import("../ui/core/errand.js");
const { strollWindow } = await import("../ui/core/life.js");
const { TOP_ARCH_TILES, TOP_DESK_TILES, topPropAt } = await import("../ui/core/pxtop.js");
const SPOT_AT = new Map(ERRAND_SPOTS.map((s) => [s.name, [s.x, s.y]]));
/** 工具を使って働いている机の人（R110: tool＝最後の工具・verb＝その訳語） */
const VERB_OF = { Edit: "編集中", Write: "執筆中", Bash: "実行中", Read: "読込中", Grep: "調査中", WebFetch: "リサーチ中",
  WebSearch: "リサーチ中", TodoWrite: "段取り整理中" };
const tooling = (i, tool, extra = {}) => who(i, { kind: "tool", tool, verb: VERB_OF[tool] || "", ...extra });
/** t0 から t1 の手前まで fps で描く（each(t) は描いた後に呼ぶ） */
function drive(room, worldAt, t0, t1, fps, each = () => {}) {
  for (let k = 0; ; k += 1) {
    const t = t0 + k / fps;
    if (t >= t1) break;
    room.draw(worldAt(t), [], t);
    each(t);
  }
}
/** 止め絵の控え（人・コマ・印・小道具の置き場所・貼った順・塗りの全部＝焼き置きの中身まで） */
const snapStill = (room, main) => ({ marks: JSON.stringify(room.marks()), layers: room.layers().join("|"),
  log: JSON.stringify(main.ctx.log.map((e) => (e[0] === "img" ? ["img", e[2], e[3], JSON.stringify(e[1].ctx.log)] : e))) });

test("用事（R110-S5）: 読む道具に変わった人だけ本棚へ歩き、窓（span）の後に同じ机へ戻る・席・人数・在席は全フレームで同じ・1 刻み 4px 以下", async () => {
  await withRoom({}, (room) => {
    const switchAt = 40;
    const w = (t) => ({ agents: [tooling(0, t < switchAt ? "Edit" : "Read"), tooling(1, "Edit"), tooling(2, "Bash")] });
    const home = new Map();
    const prevXY = new Map();
    let placed = null;
    let fgAfter = null;
    let atShelf = 0;
    let wentAt = null;
    let backAt = null;
    drive(room, w, 31, 31 + 60, 12, (t) => {
      const st = room.stats();
      if (placed === null) placed = JSON.stringify(st.placed);
      assert.equal(JSON.stringify(st.placed), placed, `t=${t.toFixed(2)}: 区分ごとの人数が変わった`);
      assert.ok(st.errands <= 1);
      assert.equal(st.unreachable, 0);
      assert.equal(st.missingTiles, 0);
      // 在席の集合（点いた画面）は変わらない＝データの切り替わり（verb で机の小物が替わる）の後は段を焼き直さない
      if (t >= switchAt + 1) {
        if (fgAfter === null) fgAfter = st.fgBakes;
        else assert.equal(st.fgBakes, fgAfter, `t=${t.toFixed(2)}: 用事の間に手前の段を焼き直した（在席が変わった）`);
      }
      for (const a of room.actors()) {
        if (!home.has(a.key)) home.set(a.key, [a.x, a.y, a.seat]);
        const [hx, hy, seat] = home.get(a.key);
        assert.equal(a.seat, seat, `${a.key} の席番号が変わった`);
        const p = prevXY.get(a.key);
        if (p) {
          const step = Math.abs(a.x - p[0]) + Math.abs(a.y - p[1]);
          assert.ok(step <= 4, `t=${t.toFixed(2)}: ${a.key} が 1 刻みで ${step}px 動いた（瞬間移動）`);
        }
        prevXY.set(a.key, [a.x, a.y]);
        if (a.key !== "s0") assert.deepEqual([a.x, a.y], [hx, hy], `${a.key}（Edit・Bash は机に残る）が動いた`);
      }
      const s0 = room.actors().find((a) => a.key === "s0");
      if (s0.errand && wentAt === null) wentAt = t;
      const [sx, sy] = SPOT_AT.get("shelf");
      // 窓が閉じた刻みは本棚の前で立ち上がる（errand はもう無い）＝本棚に居る刻みは用事の窓の中だけ数える
      if (s0.x === sx && s0.y === sy && s0.errand === "shelf") atShelf += 1;
      if (s0.x === sx && s0.y === sy && !s0.errand) assert.ok(s0.walking, `t=${t.toFixed(2)}: 用事が無いのに本棚の前に立ち止まっている`);
      if (atShelf && backAt === null && s0.x === home.get("s0")[0] && s0.y === home.get("s0")[1]) backAt = t;
    });
    assert.ok(wentAt !== null && wentAt - switchAt < 0.1, `読む道具に変わった刻みで出発しない（${wentAt}）`);
    assert.ok(atShelf >= 12 * 5, `本棚の前に ${atShelf} 刻みしか立たない（span ${ERRAND.span} 秒の窓）`);
    assert.ok(backAt !== null, "窓の後に同じ机へ戻らない");
    assert.ok(backAt >= switchAt + ERRAND.span, `窓（${ERRAND.span} 秒）が閉じる前に戻った（${backAt}）`);
  });
});

test("用事（R110-S5）: 道具で行き先が決まる（読む→本棚・Web→掲示板・段取り→ボード・証拠→カウンター）・Bash と Edit は机に残る", async () => {
  const cases = [["Read", "shelf"], ["Grep", "shelf"], ["WebFetch", "bulletin"], ["WebSearch", "bulletin"], ["TodoWrite", "board"],
    ["Bash", null], ["Write", null], ["evidence", "counter"]];
  for (const [tool, want] of cases) {
    await withRoom({}, (room) => {
      const w = (t) => {
        const ev = tool === "evidence" && t >= 40;
        return { generatedAt: ev ? 1000 : 900,
          agents: [tooling(0, t < 40 || tool === "evidence" ? "Edit" : tool, ev ? { evidence: { kind: "tested", ago: 1 } } : {})] };
      };
      let home = null;
      let reached = false;
      const seen = new Set();
      drive(room, w, 31, 40 + 12, 12, () => {
        const a = room.actors()[0];
        home ||= [a.x, a.y];
        if (a.errand) seen.add(a.errand);
        if (want && a.x === SPOT_AT.get(want)[0] && a.y === SPOT_AT.get(want)[1]) reached = true;
        if (!want) assert.deepEqual([a.x, a.y], home, `${tool}: 机を離れた`);
      });
      assert.deepEqual([...seen], want ? [want] : [], `${tool}: 行き先が ${[...seen]}`);
      if (want) assert.ok(reached, `${tool}: 12 秒で ${want} に着かない`);
    });
  }
});

test("用事（R110-S5）: 同時に出るのは 2 人まで・散歩と合わせて 3 人まで・同じ行き先に 2 人立たない・働いていない人・❗・standby・会議・休憩は出ない", async () => {
  // 机で返事を待つ 3 人（散歩する）は同じ散歩の枠の key＝3 人とも同時に散歩先へ出る。その窓の途中で道具を一斉に替える
  //（用事が 2 人＝上限・散歩は残りの 1 人へ減る。散歩に残りの上限を渡さないと 2＋3＝5 人が机を離れる）
  const strollIdx = [];
  for (let i = 6; strollIdx.length < 3 && i < 2000; i += 1) {
    if (strollWindow(`s${i}`, 1000).slot === strollWindow("s6", 1000).slot) strollIdx.push(i);
  }
  assert.equal(strollIdx.length, 3, "前提: 同じ散歩の枠の key が 3 つ見つからない");
  const strollers = strollIdx.map((i) => `s${i}`);
  let T = 60;
  while (!strollers.every((k) => strollWindow(k, T - 8).open && strollWindow(k, T + 15).open)) T += 1;
  const w = (t) => {
    const at = t >= T;
    return { agents: [
      tooling(0, at ? "Read" : "Edit"), tooling(1, at ? "Read" : "Edit"), tooling(2, at ? "WebFetch" : "Edit"),
      tooling(3, at ? "WebFetch" : "Edit"), tooling(4, at ? "TodoWrite" : "Edit"), tooling(5, at ? "TodoWrite" : "Edit"),
      ...strollIdx.map((i) => who(i, { state: "waiting", kind: "said", age: 300 })),
      tooling(909, at ? "Read" : "Edit"),                                                        // 10 人目＝standby
      tooling(910, at ? "Read" : "Edit", { attention: true, state: "waiting", question: "q" }),  // ❗＝受付
      tooling(911, at ? "Read" : "Edit", { zone: "meeting", minions: 1 }),                       // 会議室
      tooling(912, at ? "Read" : "Edit", { state: "resting" }),                                  // 休憩
      who(913, { state: "waiting", kind: "said", age: 30 }),                                     // 返事待ち（散歩の前）
    ] };
  };
  await withRoom({}, (room) => {
    let maxErr = 0, maxAll = 0;
    const never = new Set([...strollers, "s909", "s910", "s911", "s912", "s913"]);
    let strolledBefore = 0;
    drive(room, w, 31, T + 25, 6, (t) => {
      const st = room.stats();
      assert.ok(st.errands <= ERRAND.maxAway, `t=${t.toFixed(2)}: 用事 ${st.errands} 人`);
      assert.ok(st.errands + st.away <= AWAY_MAX, `t=${t.toFixed(2)}: 用事 ${st.errands}＋散歩 ${st.away} 人`);
      maxErr = Math.max(maxErr, st.errands);
      maxAll = Math.max(maxAll, st.errands + st.away);
      if (t < T) strolledBefore = Math.max(strolledBefore, st.away);
      const marks = room.marks();
      const names = marks.filter((m) => m.errand).map((m) => m.errand);
      assert.equal(new Set(names).size, names.length, `t=${t.toFixed(2)}: 同じ行き先に 2 人（${names}）`);
      for (const m of marks) if (never.has(m.key)) assert.equal(m.errand, null, `t=${t.toFixed(2)}: ${m.key}（${m.zone}）が用事に出た`);
      assert.ok(marks.some((m) => m.key === "s909" && m.zone === "standby"), "前提: 10 人目が standby");
    });
    assert.equal(strolledBefore, 3, "前提: 道具を替える前に 3 人が散歩に出ていない（散歩の上限の門が空振り）");
    assert.equal(maxErr, ERRAND.maxAway, "前提: 用事が上限まで出ない（門が空振り）");
    assert.equal(maxAll, AWAY_MAX, "前提: 用事と散歩が同時に上限まで出ない（門が空振り）");
  });
});

test("用事（R110-S5）: 非表示・再生から戻った最初のフレームは出ない・隠れている間の道具の切り替えを遅れて演じない・止め絵と life:false は出ない", async () => {
  await withRoom({}, (room) => {
    const w = (tool) => ({ agents: [tooling(0, tool), tooling(1, "Edit")] });
    drive(room, () => w("Edit"), 40, 42, 12);
    const home = room.actors().find((a) => a.key === "s0");
    globalThis.document.hidden = true;
    for (let k = 1; k <= 30; k += 1) room.draw(w("Read"), [], 42 + k / 6);       // 隠れている間に Read へ
    globalThis.document.hidden = false;
    drive(room, () => w("Read"), 48, 52, 12, (t) => {
      assert.equal(room.stats().errands, 0, `t=${t.toFixed(2)}: 隠れている間の切り替えで遅れて本棚へ出た`);
      const a = room.actors().find((x) => x.key === "s0");
      assert.deepEqual([a.x, a.y], [home.x, home.y]);
    });
    // 戻った後の新しい切り替えでは出る（門が死んでいない）
    drive(room, () => w("WebFetch"), 52, 53, 12);
    assert.equal(room.marks().find((m) => m.key === "s0").errand, "bulletin", "戻った後の切り替えで出ない");
    // 再生は出ない
    room.draw(w("TodoWrite"), [], 53.1, { replay: true });
    assert.equal(room.stats().errands, 0, "再生で用事に出た");
  });
  for (const opts of [{ frozen: () => true }, { life: false }]) {
    await withRoom(opts, (room) => {
      drive(room, (t) => ({ agents: [tooling(0, t < 40 ? "Edit" : "Read")] }), 31, 60, 6, () => {
        assert.equal(room.stats().errands, 0, `${Object.keys(opts)}: 用事に出た`);
      });
    });
  }
});

test("用事（R110-S5）: 停止から戻った最初のフレームは出ない（短い停止でも）・次のフレームから机を出て歩く・長い停止の間の切り替えは演じない", async () => {
  // 短い停止（記録が古くならない 2 秒）の間に道具が替わる＝用事の記録の古さの門では止まらない。止める門は散歩と同じ
  //「戻った最初のフレーム」（lifeLive && !resumed）
  let still = false;
  await withRoom({ frozen: () => still }, (room) => {
    drive(room, () => ({ agents: [tooling(0, "Edit")] }), 40, 41, 12);
    const home = room.actors()[0];
    still = true;
    room.draw({ agents: [tooling(0, "Read")] }, [], 42);
    still = false;
    room.draw({ agents: [tooling(0, "Read")] }, [], 42 + 1 / 12);
    assert.equal(room.stats().errands, 0, "停止から戻った瞬間に用事に出た");
    assert.deepEqual([room.actors()[0].x, room.actors()[0].y], [home.x, home.y]);
    room.draw({ agents: [tooling(0, "Read")] }, [], 42 + 2 / 12);
    assert.equal(room.stats().errands, 1, "戻った次のフレームで本棚へ出ない");
    assert.ok(room.actors()[0].walking, "本棚へ机から歩き出していない（瞬間移動）");
  });
  // 長い停止（記録が古くなる 9 秒）の間の切り替えは、戻った後も遅れて演じない
  await withRoom({ frozen: () => still }, (room) => {
    drive(room, () => ({ agents: [tooling(0, "Edit")] }), 40, 41, 12);
    still = true;
    room.draw({ agents: [tooling(0, "Read")] }, [], 50);
    still = false;
    drive(room, () => ({ agents: [tooling(0, "Read")] }), 50 + 1 / 12, 53, 12, (t) => {
      assert.equal(room.stats().errands, 0, `t=${t.toFixed(2)}: 長い停止の間の切り替えで遅れて本棚へ出た`);
    });
  });
});

test("証拠の紙（R110-S5）: カウンターに着いた人がトレイに 1 枚置く・最大 3 枚で古い紙から入れ替わる・600 秒で消える・止め絵には出ない", async () => {
  await withRoom({}, (room) => {
    // s0 に 4 回、新しい成功が届く（40・70・100・130 秒）
    const arrivals = [40, 70, 100, 130];
    const w = (t) => {
      const n = arrivals.filter((a) => t >= a).length;
      return { generatedAt: 10000 + n * 1000,
        agents: [tooling(0, "Edit", n ? { evidence: { kind: n % 2 ? "tested" : "committed", ago: 1 } } : {})] };
    };
    const placed = [];
    let before = new Set();
    drive(room, w, 31, 160, 12, (t) => {
      const st = room.stats();
      assert.ok(st.papers <= EVIDENCE_TRAY.length);
      const L = room.layers();
      const now = new Set(L.filter((l) => l.startsWith("paper:")));
      for (const id of now) {
        if (before.has(id)) continue;
        // 置いた瞬間はカウンターに着いて立っている刻み（歩いている途中・立ち上がりの間ではない）
        const a = room.actors()[0];
        assert.deepEqual([a.x, a.y], SPOT_AT.get(EVIDENCE_ERRAND.spot), `t=${t.toFixed(2)}: カウンターに着く前に紙を置いた`);
        assert.ok(!a.walking, `t=${t.toFixed(2)}: 歩いている途中に紙を置いた`);
        placed.push([t, id]);
      }
      before = now;
      // 紙はカウンターの段の直後（天板の上＝段より後）
      const ci = L.indexOf("band:counter");
      L.forEach((l, i) => { if (l.startsWith("paper:")) assert.ok(i > ci, "紙がカウンターの段より先に貼られた（天板に隠れる）"); });
    });
    assert.equal(placed.length, arrivals.length, `紙を ${placed.length} 回しか置いていない（${placed.map(([t]) => t.toFixed(1))}）`);
    const ids = room.layers().filter((l) => l.startsWith("paper:"));
    assert.equal(ids.length, EVIDENCE_TRAY.length, "4 回目の証拠の後に 3 枚でない");
    assert.ok(!ids.includes(placed[0][1]), "いちばん古い紙が残った（4 枚目で入れ替わらない）");
    assert.equal(room.stats().papers, EVIDENCE_TRAY.length);
  });
  // 帳簿は純関数: 着いた人だけ・用事 1 回に 1 枚・同じ t の描き直しで増えない・600 秒で消える・巻き戻った紙は消す
  const spot = SPOT_AT.get(EVIDENCE_ERRAND.spot);
  const d = { key: "s0", x: spot[0], y: spot[1], errand: EVIDENCE_ERRAND.spot, walk: null, lift: 0 };
  const book = new Map([["s0", { lastGo: 12 }]]);
  const one = trayPapers([], [d], book, 20);
  assert.deepEqual(one, [{ id: "s0@12", t: 20, slot: 0 }]);
  assert.deepEqual(trayPapers(one, [d], book, 20), one, "同じ t の描き直しで 2 枚目を置いた");
  assert.deepEqual(trayPapers(one, [d], book, 25), one, "同じ用事（同じ出発）で 2 枚目を置いた");
  assert.equal(trayPapers(one, [], book, 20 + EVIDENCE_PAPER_SEC - 0.01).length, 1);
  assert.equal(trayPapers(one, [], book, 20 + EVIDENCE_PAPER_SEC).length, 0, `${EVIDENCE_PAPER_SEC} 秒で消えない`);
  assert.equal(trayPapers(one, [], book, 19).length, 0, "巻き戻った紙が残った");
  for (const bad of [{ ...d, walk: { dir: "S", phase: 0 } }, { ...d, lift: 1 }, { ...d, leaving: true }, { ...d, x: d.x + 1 },
    { ...d, errand: "shelf" }]) {
    assert.deepEqual(trayPapers([], [bad], book, 20), [], `着いていない人が紙を置いた: ${JSON.stringify(bad)}`);
  }
  // トレイが埋まっていれば、いちばん古い紙の場所へ（ほかの紙は動かない）
  let tray = [];
  for (const [i, t] of [[1, 10], [2, 20], [3, 30], [4, 40]]) tray = trayPapers(tray, [d], new Map([["s0", { lastGo: i }]]), t);
  assert.deepEqual(tray.map((p) => [p.id, p.slot]).sort(), [["s0@2", 1], ["s0@3", 2], ["s0@4", 0]]);
  // 止め絵には出さない
  await withRoom({ frozen: () => true, keepPrev: false }, (room) => {
    room.draw({ generatedAt: 999, agents: [tooling(0, "Edit", { evidence: { kind: "tested", ago: 1 } })] }, [], 3.2);
    assert.equal(room.stats().papers, 0);
  });
});

test("画面の模様と打鍵の速さ（R110-S5）: Bash＝墨と緑・Edit＝差分・Read＝紙・Web＝帯・段取りと工具なしは打鍵の行／Edit は速く、工具なしはゆっくり", async () => {
  const w = { agents: [tooling(0, "Bash"), tooling(1, "Edit"), tooling(2, "Read"), tooling(3, "WebFetch"), tooling(4, "TodoWrite"),
    who(5, { kind: "think" }), who(6, { kind: "order", verb: "指示を受領" })] };
  const want = { s0: ["screenBash0", "screenBash1"], s1: ["screenDiff0", "screenDiff1"], s2: ["screenPage0", "screenPage1"],
    s3: ["screenWeb0", "screenWeb1"], s4: ["screenType0", "screenType1"], s5: ["screenThink0", "screenThink1"],
    s6: ["screenType0", "screenType1"] };
  await withRoom({}, (room) => {
    const seen = new Map();
    const cells = new Map();
    drive(room, () => w, 20, 26, 6, () => {
      for (const l of room.layers().filter((x) => x.startsWith("screen:"))) {
        const [, key, tile] = l.split(":");
        if (!seen.has(key)) seen.set(key, new Set());
        seen.get(key).add(tile);
      }
      for (const m of room.marks()) {
        if (!cells.has(m.key)) cells.set(m.key, []);
        cells.get(m.key).push(m.cell);
      }
    });
    for (const [key, tiles] of Object.entries(want)) assert.deepEqual([...(seen.get(key) || [])].sort(), tiles, `${key} の画面の模様`);
    // 打鍵のコマの切り替わり（6 秒・6fps）: Edit（burst 3fps）＞ Bash（normal 2fps）＞ 工具なし（calm 1fps）
    const flips = (key) => cells.get(key).slice(1).filter((c, i) => c !== cells.get(key)[i]).length;
    assert.ok(flips("s1") > flips("s0") && flips("s0") > flips("s6"),
      `打鍵の速さが道具で違わない（Edit ${flips("s1")}・Bash ${flips("s0")}・工具なし ${flips("s6")}）`);
    assert.ok(Math.abs(flips("s1") - 3 * 6) <= 1 && Math.abs(flips("s0") - 2 * 6) <= 1 && Math.abs(flips("s6") - 6) <= 1,
      `打鍵の速さ（Edit ${flips("s1")}・Bash ${flips("s0")}・工具なし ${flips("s6")}）が TYPE_FPS × 6 秒でない`);
  });
  await withRoom({ frozen: () => true, keepPrev: false }, (room) => {
    for (const t of [0, 0.5, 3.2]) {
      room.draw(w, [], t);
      assert.equal(room.stats().screens, 0, "止め絵で画面の模様を貼った");
      assert.ok(room.marks().filter((m) => m.kind === "type").every((m) => m.cell === "type0"), "止め絵の打鍵が type0 でない");
    }
  });
});

test("考え中の点（R110-S5）: live では雲の点が 0 → 2 → 3 個と増えて繰り返す（考え始めは点なしから）・止め絵は markThink のまま", async () => {
  await withRoom({}, (room) => {
    drive(room, () => ({ agents: [who(0), who(1, { kind: "think" })] }), 10, 11, 6);
    const seq = [];
    drive(room, () => ({ agents: [who(0, { kind: "think" }), who(1, { kind: "think" })] }), 11, 14, 6, () => {
      seq.push(room.marks().find((m) => m.key === "s0").markTile);
    });
    assert.deepEqual(seq.slice(0, 9), ["markThink", "markThink", "markThink", "markThink1", "markThink1", "markThink1",
      "markThink2", "markThink2", "markThink2"], `考え始めの点の送り: ${seq}`);
    assert.equal(seq[9], "markThink", "3 段の後に点なしへ戻らない");
    assert.ok(room.layers().includes("mark:s0:think"), "点の付いた雲も 💭 の印として貼る（mark:<key>:think）");
  });
  await withRoom({ frozen: () => true, keepPrev: false }, (room) => {
    for (const t of [0, 0.5, 1, 7.75]) {
      room.draw({ agents: [who(0, { kind: "think" })] }, [], t);
      assert.equal(room.marks()[0].markTile, "markThink", `止め絵（t=${t}）で点を送った`);
    }
  });
});

test("職業の小道具（R110-S2）: 頭（か胸）に別タイルで重ねる・人のすぐ後に貼る・生成タイルのドット・止め絵にも出す・dev と該当なしは付けない", async () => {
  const { PX_TILES } = await import("../ui/cockpit/tiles.js");
  const { TILE_PALETTE } = await import("../ui/cockpit/palette.js");
  const names = { video: "動画編集部", design: "デザイン室", audio: "音声書き起こし", support: "広報", writer: "ブログ",
    research: "リサーチ室", infra: "インフラ構築", finance: "経理", ops: "運用ボット", dev: "開発", generic: "こんにちは" };
  const world = { agents: Object.values(names).map((name, i) => who(i, { name })) };
  await withRoom({ frozen: () => true, keepPrev: false }, (room, main) => {
    room.draw(world, [], 0);
    const S = room.stats().scale;
    const L = room.layers();
    const imgs = main.ctx.log.filter((e) => e[0] === "img");
    const byKey = new Map(room.marks().map((m) => [m.key, m]));
    let props = 0;
    Object.keys(names).forEach((kind, i) => {
      const m = byKey.get(`s${i}`);
      assert.equal(m.prop, kind, `s${i}（${names[kind]}）の職業`);
      const at = m.propAt;
      if (!Object.hasOwn(TOP_ARCH_TILES, kind)) { assert.equal(at, null, `${kind} に小道具を付けた（嘘の帽子）`); return; }
      assert.ok(at, `${kind} に小道具が無い`);
      const li = L.indexOf(`prop:s${i}`);
      assert.equal(li, L.indexOf(`person:s${i}`) + 1, `${kind}: 小道具が人のすぐ後に貼られていない`);
      const [, img, x, y] = imgs[li];
      assert.deepEqual([x / S, y / S], [at.x, at.y]);
      assert.deepEqual(at, topPropAt({ x: m.x, y: m.y }, kind, PX_CELLS[m.vendor][m.cell], { dir: m.dir }));
      // 貼ったドット＝生成タイル（TILE_PALETTE）そのもの
      const got = img.ctx.log.filter((e) => e[0] === "fill").map((e) => `${e[1] / S},${e[2] / S},${e[5]}`).sort();
      const want = [];
      PX_TILES[at.tile].forEach((row, ry) => [...row].forEach((ch, rx) => { if (TILE_PALETTE[ch]) want.push(`${rx},${ry},${TILE_PALETTE[ch]}`); }));
      assert.deepEqual(got, want.sort(), `${kind}: 生成タイル ${at.tile} と違うドット`);
      props += 1;
    });
    assert.equal(room.stats().props, props);
    assert.equal(props, Object.keys(TOP_ARCH_TILES).length, `前提: 小道具を ${props} 人にしか貼っていない`);
    const plain = logical(main.ctx.log, S);
    for (const t of [3.2, 600]) {
      room.draw(world, [], t);
      assert.deepEqual(logical(main.ctx.log, S), plain, `止め絵（t=${t}）の小道具が変わった`);
    }
  });
  // W（左へ歩く）は鏡・挨拶の跳ねで一緒に 1px 上がる（live）
  await withRoom({}, (room) => {
    const a = who(0, { id: "p9", session: "a", zone: "lounge", state: "resting", name: "動画編集部" });
    const other = who(5);
    room.draw({ agents: [other] }, [], 0);              // 最初の世界（baseline）に人が居る＝後から来た a は扉から歩いて入る
    room.draw({ agents: [other] }, [], 0.5);
    let mirrored = 0;
    drive(room, () => ({ agents: [other, a] }), 1, 14, 12, () => {
      const m = room.marks().find((x) => x.key === "a");
      if (m.mirror) {
        mirrored += 1;
        assert.equal(m.propAt.flip, true, "左へ歩くロボの小道具が鏡でない");
        assert.deepEqual(m.propAt, topPropAt({ x: m.x, y: m.y }, "video", PX_CELLS[m.vendor][m.cell], { mirror: true }));
        assert.ok(room.layers().includes("prop:a"));
      }
    });
    assert.ok(mirrored > 0, "前提: 左へ歩く刻みが無い");
    const w = { agents: [who(1, { name: "動画編集部" })] };
    drive(room, () => w, 20, 21, 6);
    assert.equal(room.setGreet("s1"), true);
    room.draw(w, [], 21 + 2 / 6);
    room.draw(w, [], 21 + 2 / 6 + 2 / 6);
    const m = room.marks().find((x) => x.key === "s1");
    assert.equal(m.lift, 1, "前提: 挨拶の跳ねの間");
    assert.equal(m.propAt.y, topPropAt({ x: m.x, y: m.y }, "video", PX_CELLS[m.vendor][m.cell]).y - 1, "挨拶の跳ねで小道具が上がらない");
  });
});

test("職業の机の小物（R110-S2）: 動画＝タイムライン・書く人と文字起こし＝紙の束・デザイン＝パレットを天板の右に焼く・dev と該当なしは無し", async () => {
  for (const [name, tile, h] of [["動画編集部", "deskTimeline", 6], ["ブログ", "deskStack", 6], ["音声書き起こし", "deskStack", 6],
    ["デザイン室", "deskBrush", 3]]) {
    await withRoom({ frozen: () => true, keepPrev: false }, async (room, main) => {
      room.draw({ agents: [who(0, { name })] }, [], 0);
      const desk = DESKS_R[room.actors()[0].seat];
      const copies = await bandTiles(room, main, tile);
      assert.equal(copies.length, 1, `${name}: ${tile} を机に 1 枚焼いていない`);
      // 天板の右・ロボの右隣（机の x+17）・下端は天板の行 3
      assert.deepEqual([copies[0].x, copies[0].y + h - 1], [desk.x + 17, desk.y + 3], `${name}: ${tile} の置き場所（机 ${desk.x},${desk.y}）`);
      assert.equal(TOP_DESK_TILES[room.marks()[0].prop], tile);
    });
  }
  await withRoom({ frozen: () => true, keepPrev: false }, async (room, main) => {
    room.draw({ agents: [who(0, { name: "開発" }), who(1, { name: "こんにちは" })] }, [], 0);
    for (const tile of ["deskTimeline", "deskStack", "deskBrush"]) {
      assert.equal((await bandTiles(room, main, tile)).length, 0, `dev・該当なしの机に ${tile}`);
    }
  });
});

test("止め絵（R110）: 小道具・本棚・机の小物を含めて t に依らず同じ絵・用事も紙も画面の模様も点も出ない", async () => {
  const world = { generatedAt: 5000, agents: [
    tooling(0, "Read", { name: "リサーチ室" }), tooling(1, "Edit", { name: "動画編集部" }), tooling(2, "WebFetch", { name: "ブログ" }),
    tooling(3, "TodoWrite", { name: "運用ボット", evidence: { kind: "committed", ago: 1 } }), who(4, { kind: "think", name: "デザイン室" }),
    who(5, { state: "waiting", kind: "said", age: 120, name: "広報" }), who(6, { attention: true, question: "q", name: "経理" }),
  ] };
  await withRoom({ frozen: () => true, keepPrev: false }, (room, main) => {
    room.draw(world, [], 0);
    const a = snapStill(room, main);
    const st = room.stats();
    assert.ok(st.props >= 5, `前提: 止め絵に小道具が ${st.props} 個`);
    assert.deepEqual([st.errands, st.papers, st.screens, st.away], [0, 0, 0, 0]);
    assert.ok(room.marks().every((m) => !m.markAt || m.markTile === m.markAt.tile), "止め絵で考え中の点を送った");
    // 本棚（壁掛け）は背景に焼いてある
    assert.ok(a.log.length > 0);
    for (const t of [3.2, 600, 1 / 6, 7.75]) {
      room.draw(world, [], t);
      assert.deepEqual(snapStill(room, main), a, `t=${t} で止め絵が変わった`);
    }
  });
});

test("止め絵（R110）: 用事・証拠の紙・打鍵の速さ・考え中の点の live の履歴の後で止めた絵は、素の止め絵と 1 か所も違わない", async () => {
  const final = { generatedAt: 9000, agents: [
    tooling(0, "Read", { name: "リサーチ室" }), tooling(1, "Edit", { name: "動画編集部", evidence: { kind: "tested", ago: 1 } }),
    tooling(2, "TodoWrite"), who(3, { kind: "think" }), tooling(4, "WebFetch", { name: "ブログ" }),
  ] };
  const early = { generatedAt: 8000, agents: [tooling(0, "Edit", { name: "リサーチ室" }), tooling(1, "Edit", { name: "動画編集部" }),
    tooling(2, "Edit"), who(3, { kind: "think" }), tooling(4, "Edit", { name: "ブログ" })] };
  const noEvidence = { ...final, generatedAt: 8000, agents: final.agents.map((a) => (a.session === "s1" ? { ...a, evidence: null } : a)) };
  const plain = await withRoom({ frozen: () => true, keepPrev: false }, (room, main) => { room.draw(final, [], 0); return snapStill(room, main); });
  for (const stopAt of [50, 80, 118]) {                    // 行き先へ歩いている途中・カウンターに居る間・紙を置いて戻った後
    let still = false;
    const after = await withRoom({ frozen: () => still, keepPrev: false }, (room, main) => {
      let sawErrand = false;
      let sawPaper = false;
      // 45 秒で道具が替わる（本棚・ボードへ 2 人＝上限）。証拠は窓が閉じて戻った後の 70 秒に届く（上限に掛かった
      // きっかけは使い切り＝窓の途中に届くとカウンターへは行かない）
      drive(room, (t) => (t < 45 ? early : t < 70 ? noEvidence : final), 31, stopAt, 12, () => {
        if (room.stats().errands) sawErrand = true;
        if (room.stats().papers) sawPaper = true;
      });
      assert.ok(sawErrand, `前提: ${stopAt} までに用事に出ていない（門が空振り）`);
      if (stopAt > 100) assert.ok(sawPaper, "前提: 紙を置いていない（門が空振り）");
      still = true;
      room.draw(final, [], stopAt + 1);
      return snapStill(room, main);
    });
    assert.equal(after.marks, plain.marks, `${stopAt}: 履歴の後で止めた絵の人・コマ・小道具・印が素の止め絵と違う`);
    assert.equal(after.layers, plain.layers, `${stopAt}: 履歴の後で止めた絵の貼った順が素の止め絵と違う`);
    assert.equal(after.log, plain.log, `${stopAt}: 履歴の後で止めた絵の塗りが素の止め絵と違う`);
  }
});

test("drawCalls（R110）: 26 人全員に小道具・助手・机の画面・証拠の紙 3 枚を足した入れ替わりの山でも上限以内・実際に呼んだ数", async () => {
  await withRoom({}, (room, main) => {
    const kinds = Object.keys(TOP_ARCH_TILES);
    const NAMES = { video: "動画", design: "デザイン", audio: "音声", support: "広報", writer: "ブログ", research: "リサーチ",
      infra: "インフラ構築", finance: "経理", ops: "運用ボット" };
    const TOOLS = ["Edit", "Bash", "TodoWrite"];
    const crowd = (from, ev = 0) => ({ generatedAt: 20000 + ev * 100, agents: Array.from({ length: 26 }, (_, i) => who(from + i, {
      name: NAMES[kinds[i % kinds.length]],
      ...(i < 9 ? { kind: "tool", tool: TOOLS[i % 3], verb: VERB_OF[TOOLS[i % 3]] } : {}),
      ...(i === 0 && ev ? { evidence: { kind: "tested", ago: 1 } } : {}),
      zone: i < 9 ? "desk" : i < 18 ? "queue" : i < 20 ? "lounge" : i < 23 ? "meeting" : "external",
      attention: i >= 9 && i < 18, state: i >= 9 && i < 18 ? "waiting" : i >= 18 && i < 20 ? "resting" : "working",
      minionsLive: 5,
    })) });
    // 証拠を 3 回届けて紙を 3 枚置く（s100 がカウンターへ 3 往復）
    drive(room, (t) => crowd(100, t >= 100 ? 3 : t >= 70 ? 2 : t >= 40 ? 1 : 0), 31, 125, 6);
    assert.equal(room.stats().papers, EVIDENCE_TRAY.length, "前提: 証拠の紙が 3 枚になっていない");
    assert.equal(room.stats().props, 26, `前提: 小道具 ${room.stats().props} 個（26 人全員）`);
    const counted = () => main.ctx.log.filter((e) => e[0] === "fill" || e[0] === "img").length;
    let peak = 0;
    for (let k = 1; k <= 12 * 4; k += 1) {
      room.draw(crowd(200), [], 125 + k / 12, { lightStage: "evening" });
      assert.equal(room.stats().drawCalls, counted(), "drawCalls が実際に呼んだ数と違う");
      peak = Math.max(peak, room.stats().drawCalls);
      if (k === 6) assert.ok(room.stats().leaving > 0 && room.stats().entering > 0, "前提: 入退場が同時に歩いていない");
    }
    assert.ok(peak <= DRAW_CALL_LIMIT, `小道具・助手・画面・紙を足した入れ替わりの山で drawCalls ${peak} > ${DRAW_CALL_LIMIT}`);
    assert.equal(room.stats().missingTiles, 0);
    assert.equal(room.stats().unreachable, 0);
    console.log(`# R110 drawCalls の山 = ${peak}（上限 ${DRAW_CALL_LIMIT}）`);
  });
});
