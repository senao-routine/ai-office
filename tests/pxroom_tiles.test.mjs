// R99-B2: 家具の配置表（ui/core/pxroom.js の FURNITURE）とドット（ui/cockpit/tiles.js の PX_TILES）の突き合わせ。
// core は cockpit を import できない（tools/js_layer_lint.py）ので、両方を知ってよい tests/ に置く。
// 表の形・足元の重なり・人の枠のめり込みは ui/core/pxroom.test.js が見る。ここは「表とドットが食い違っていない」だけ。
import test from "node:test";
import assert from "node:assert/strict";
import { DOOR, FURNITURE, GLASS_OPENING, ROOM, SLOTS, footRects } from "../ui/core/pxroom.js";
import { PX_TILES } from "../ui/cockpit/tiles.js";
import { RAMP } from "../ui/cockpit/palette.js";

/** 表が名指しするタイル（無ければ名指しで赤＝design/gen_tiles.py にまだ無い） */
function rowsOf(name) {
  assert.ok(Object.hasOwn(PX_TILES, name), `タイル ${name} が PX_TILES に無い（design/gen_tiles.py に足す）`);
  return PX_TILES[name];
}
const dims = (name) => ({ w: rowsOf(name)[0].length, h: rowsOf(name).length });

/** タイルの体（落ち影 u と透明 . だけの行・列を除いた外接矩形）。足元の規則の正本はこれ。 */
function body(name) {
  const rows = rowsOf(name);
  let x0 = Infinity, y0 = Infinity, x1 = -1, y1 = -1;
  rows.forEach((row, y) => {
    [...row].forEach((c, x) => {
      if (c === "." || c === "u") return;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    });
  });
  return [x0, y0, x1 - x0 + 1, y1 - y0 + 1];
}

/** 体の外接矩形と違う足元（footRects＝foot＋solid）を持つ家具（理由つき）。ここに無いものは「体＝足元」 */
const FOOT_EXCEPTIONS = {
  // ガラスの会議室は囲いの絵＝内側は会議室の床。足元は囲いの框だけ: 手前の框（最下の 2 行）を開口
  // （GLASS_OPENING）の左右に切り分けた 2 本と、左右の柱（最左・最右の列を框の上まで）
  glassWall: (f) => {
    const { w } = dims(f.tile);
    const b = body(f.tile);
    const top = f.y + b[1];
    const fy = f.y + b[1] + b[3] - 2;
    const [ox, , ow] = GLASS_OPENING;
    return [
      [f.x, fy, ox - f.x, 2],
      [ox + ow, fy, f.x + w - ox - ow, 2],
      [f.x, top, 1, fy - top],
      [f.x + w - 1, top, 1, fy - top],
    ];
  },
};

test("pxroom tiles: 表に出てくるタイル名が PX_TILES に全部在る", () => {
  for (const f of FURNITURE) {
    assert.ok(Object.hasOwn(PX_TILES, f.tile), `${f.id}: タイル ${f.tile} が PX_TILES に無い`);
    if (f.lit) {
      assert.ok(Object.hasOwn(PX_TILES, f.lit), `${f.id}: 在席のタイル ${f.lit} が PX_TILES に無い`);
      assert.deepEqual(dims(f.lit), dims(f.tile), `${f.id}: ${f.tile} と ${f.lit} の寸法が違う（点灯で絵がずれる）`);
    }
  }
});

test("pxroom tiles: タイルは部屋の中に収まる（壁の手前から外へはみ出さない）", () => {
  for (const f of FURNITURE) {
    const { w, h } = dims(f.tile);
    assert.ok(f.x >= 0 && f.y >= 0 && f.x + w <= ROOM.w && f.y + h <= ROOM.h,
      `${f.id}: (${f.x},${f.y}) ${w}×${h} が部屋の外へ出る`);
  }
});

test("pxroom tiles: 足元はタイルの体（落ち影を除く）と一致する", () => {
  for (const f of FURNITURE) {
    if (!f.foot) continue;
    const { w, h } = dims(f.tile);
    for (const [fx, fy, fw, fh] of footRects(f)) {
      assert.ok(fx >= f.x && fy >= f.y && fx + fw <= f.x + w && fy + fh <= f.y + h,
        `${f.id}: 足元 ${[fx, fy, fw, fh]} がタイル (${f.x},${f.y}) ${w}×${h} の外にある`);
    }
    const b = body(f.tile);
    const want = FOOT_EXCEPTIONS[f.id] ? FOOT_EXCEPTIONS[f.id](f) : [[f.x + b[0], f.y + b[1], b[2], b[3]]];
    const got = footRects(f).map((r) => [...r]);
    assert.deepEqual(got, want, `${f.id}: 足元 ${JSON.stringify(got)} がドットの体 ${JSON.stringify(want)} と食い違う（絵を直したら表も直す）`);
  }
});

// 開口のドット（GLASS_OPENING の行＝框の行・開口の列が通れる・左右 1 列に框）の門は tools/px_lint.py --cockpit ⑥ の
// _glass_opening_problems **1 本**（統括の裁定 2026-09-24: 規則を 2 か所に持たない。'.' と 'u'＝落ち影は通れる床）。
// 回帰は tests/test_px_lint_cockpit.py。ここは開口の門が見ない「原本 glassWall との差し替え」と「足元の框」だけを見る。
test("pxroom tiles: 会議室のガラス（glassWallDoor）は glassWall と同じ寸法で、開口とその左右の枠の外は glassWall と同じ・足元の框は描いてある", () => {
  const f = FURNITURE.find((x) => x.id === "glassWall");
  assert.equal(f.tile, "glassWallDoor");
  assert.deepEqual(dims(f.tile), dims("glassWall"), "開口つきのガラスの寸法が glassWall と違う（差し替えで囲いがずれる）");
  const door = rowsOf(f.tile);
  const wall = rowsOf("glassWall");
  const { w, h } = dims(f.tile);
  const [ox, , ow] = GLASS_OPENING;
  const c0 = ox - f.x;
  const c1 = c0 + ow - 1;
  // 開口とその左右の枠（扉口の縦の枠）の列の外は glassWall と同じ（囲いの絵を変えていない）
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      if (x >= c0 - 1 && x <= c1 + 1) continue;
      assert.equal(door[y][x], wall[y][x], `開口から離れた (${x},${y}) が glassWall と違う`);
    }
  }
  // 足元（框・柱）の下は全部ドットが在る＝通れない所には框が描いてある
  for (const [rx, ry, rw, rh] of footRects(f)) {
    for (let y = ry; y < ry + rh; y += 1) {
      for (let x = rx; x < rx + rw; x += 1) {
        const c = door[y - f.y][x - f.x];
        assert.ok(c !== "." && c !== "u", `足元 (${x},${y}) にガラスの框が描かれていない（'${c}'）`);
      }
    }
  }
});

test("pxroom tiles: 扉のタイルは DOOR の寸法（手前の壁の帯＋床のマット）", () => {
  const f = FURNITURE.find((x) => x.tile === "door");
  assert.ok(f, "扉が表に無い");
  assert.deepEqual(dims("door"), { w: DOOR.w, h: DOOR.h }, "扉のタイルの寸法が DOOR と違う（経路の扉の位置とずれる）");
  assert.deepEqual([f.x, f.y], [DOOR.x, DOOR.y]);
});

test("pxroom tiles: 人の枠が重なってよい家具（at）は床に足のある家具", () => {
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  for (const [zone, list] of Object.entries(SLOTS)) {
    list.forEach((s, i) => {
      for (const id of s.at) {
        assert.ok(byId.get(id)?.foot, `${zone}#${i}: at の ${id} に足元が無い（例外にする意味が無い）`);
      }
    });
  }
});

test("pxroom tiles: 天板の上の小物（on）は、ドットの下端が載せ台の足元に乗り、横幅が載せ台からはみ出さない", () => {
  const byId = new Map(FURNITURE.map((f) => [f.id, f]));
  let n = 0;
  for (const f of FURNITURE) {
    if (f.on === undefined) continue;
    const [bx, by, bw, bh] = byId.get(f.on).foot;
    const b = body(f.tile);
    const left = f.x + b[0], right = f.x + b[0] + b[2], bottom = f.y + b[1] + b[3] - 1;
    assert.ok(bottom >= by && bottom < by + bh, `${f.id}: 下端 y=${bottom} が載せ台 ${f.on} の足元 ${by}..${by + bh - 1} に乗っていない（浮く／沈む）`);
    assert.ok(left >= bx && right <= bx + bw, `${f.id}: 横 ${left}..${right - 1} が載せ台 ${f.on} ${bx}..${bx + bw - 1} からはみ出す`);
    n += 1;
  }
  assert.ok(n >= 9, `天板の上の小物が ${n} 件＝検査になっていない`);
});

test("pxroom tiles: 光（glow）の段は RAMP.amber に在る（無い段は描画側で黙って透明になる）", () => {
  let n = 0;
  for (const f of FURNITURE) {
    for (const g of f.glow || []) {
      assert.ok(g[5] < RAMP.amber.length, `${f.id}: glow の段 ${g[5]} が RAMP.amber（${RAMP.amber.length} 段）に無い`);
      n += 1;
    }
  }
  assert.ok(n >= 3, `glow が ${n} 件＝検査になっていない`);
});
