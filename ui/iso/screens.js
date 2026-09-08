// R90-V6: data-driven ink bars. No glyphs, random sequence or animation time.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { DESK_SLOTS, stableIndex } from "/ui/core/world.js";

const W = 512, H = 320;
const COLORS = { working: "#7a9469", waiting: "#c28a3a", attention: "#c0483a", resting: "#b4aca0" };
const count = (value) => Number.isFinite(Number(value)) ? Math.max(0, Math.floor(Number(value))) : 0;
// office-json's work.counts uses these three keys; done/total is derived from them.
const countsOf = (agent) => ["completed", "in_progress", "pending"].map((key) => count(agent?.work?.counts?.[key]));

export function screenTexture(height = H) {
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = height;
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function clear(texture, color = "#f1ede7", y = 0) {
  const ctx = texture.image.getContext("2d");
  ctx.save(); ctx.translate(0, y);
  ctx.fillStyle = color; ctx.fillRect(0, 0, W, H);
  return ctx;
}

function finish(texture, ctx) {
  ctx.restore(); texture.needsUpdate = true;
}

/** Board/daily share the upper/lower halves of one canvas (one GPU texture). */
export function reportGeometry(width, height, daily = false) {
  const geometry = new THREE.PlaneGeometry(width, height), uv = geometry.getAttribute("uv");
  for (let i = 0; i < uv.count; i++) uv.setY(i, uv.getY(i) / 2 + (daily ? 0 : .5));
  return geometry;
}

function paintMonitor(texture, agent, activity, state, counts) {
  const ctx = clear(texture);
  // 状態帯は 512x320 の 32px = 10% では俯瞰（モニタが約70px幅）で色が読めない。
  // 実測で 64px（20%）にすると office のズームでも状態が分かる（R90-V6・Claude 実測）。
  ctx.fillStyle = COLORS[state]; ctx.fillRect(0, 0, W, 64);
  if (agent) {
    // The first row encodes the same activity string as the list, without baking text.
    // Hashing the whole string also distinguishes equally long Japanese activities.
    if (activity) {
      let x = 28;
      ctx.fillStyle = "#2e2d2c";
      for (let i = 0; i < 4; i++) {
        const width = 38 + stableIndex(`${activity}:${i}`, 57);
        ctx.fillRect(x, 54, width, 13); x += width + 12;
      }
      for (let row = 0; row < 8; row++) {
        let x = 28 + stableIndex(`${activity}:indent:${row}`, 3) * 24;
        ctx.fillStyle = row % 3 ? "#5e5a55" : "#2e2d2c";
        for (let col = 0; col < 3; col++) {
          const width = 24 + stableIndex(`${activity}:${row}:${col}`, 73);
          ctx.fillRect(x, 91 + row * 22, width, 8); x += width + 12;
        }
      }
    }
    const total = counts.reduce((a, b) => a + b, 0);
    if (total) {
      ctx.fillStyle = "#e4dcd2"; ctx.fillRect(28, 290, 456, 10);
      ctx.fillStyle = COLORS.working; ctx.fillRect(28, 290, Math.round(456 * counts[0] / total), 10);
    }
  }
  finish(texture, ctx);
}

function paintBoard(texture, counts) {
  const ctx = clear(texture, "#f7f4ea");
  const total = Math.max(1, counts.reduce((a, b) => a + b, 0));
  ctx.fillStyle = "#e4dcd2"; ctx.fillRect(48, 274, 416, 3);
  counts.forEach((n, i) => {
    const x = 76 + i * 140, h = Math.round(210 * n / total);
    ctx.fillStyle = [COLORS.working, COLORS.waiting, COLORS.resting][i];
    ctx.fillRect(x, 274 - h, 80, h);
    ctx.fillRect(x, 292, 80, 8);
  });
  finish(texture, ctx);
}

export function boardTexture() {
  const texture = screenTexture(H * 2);
  paintBoard(texture, [0, 0, 0]);
  return texture;
}

function paintDaily(texture, sent, history) {
  const ctx = clear(texture, "#f1ede7", H);
  ctx.fillStyle = "#e4dcd2"; ctx.fillRect(28, 34, 456, 44);
  // Monotone scale with no invented maximum; zero/missing counts have no ink.
  ctx.fillStyle = COLORS.working;
  ctx.fillRect(28, 34, Math.round(456 * sent / (sent + 10)), 44);
  history.forEach((present, i) => {
    const y = 119 + i * 59;
    ctx.fillStyle = present ? COLORS.working : COLORS.resting; ctx.fillRect(28, y, 14, 14);
    ctx.fillStyle = present ? "#2e2d2c" : "#e4dcd2"; ctx.fillRect(60, y, 360, 14);
  });
  finish(texture, ctx);
}

/** Persistent seat canvases/maps and one shared MeshBasicMaterial across all tiers.
 * The daily screen shares that material too; boards retain their existing material.
 */
export class ActivityScreens {
  constructor(board, seats = DESK_SLOTS) {
    this.maps = Array.from({ length: seats }, () => screenTexture());
    this.daily = board;
    this.board = board;
    this.material = new THREE.MeshBasicMaterial({ map: this.maps[0], toneMapped: false, side: THREE.DoubleSide });
    this.keys = [];
    this.update({ agents: [], seats: new Map() });
  }

  bind(mesh, texture) {
    mesh.material = this.material;
    mesh.onBeforeRender = () => {
      if (this.material.map === texture) return;
      this.material.map = texture;
      // WebGLRenderer caches uniforms by material id. Invalidate the material version
      // so consecutive meshes upload their own map; the shader program is reused.
      this.material.needsUpdate = true;
    };
    return mesh;
  }

  resize(seats) {
    while (this.maps.length < seats) this.maps.push(screenTexture());
    while (this.maps.length > seats) this.maps.pop().dispose();
    this.keys.length = Math.min(this.keys.length, seats);
  }

  update(world) {
    const agents = world.agents || [], seated = new Map();
    for (const agent of agents) {
      const slot = world.seats?.get(agent.id);
      if (Number.isInteger(slot) && slot >= 0 && slot < this.maps.length) seated.set(slot, agent);
    }
    this.maps.forEach((texture, slot) => {
      const agent = seated.get(slot), activity = agent?.activity || agent?.verb || "";
      const state = agent?.attention || agent?.state === "attention" ? "attention"
        : agent?.state === "working" ? "working" : agent?.state === "waiting" ? "waiting" : "resting";
      const counts = countsOf(agent);
      const key = JSON.stringify([agent?.id ?? null, activity, state, ...counts]);
      if (key === this.keys[slot]) return;
      paintMonitor(texture, agent, activity, state, counts);
      this.keys[slot] = key;
    });
    // Shared office totals, just like the HUD's work-count aggregation.
    const totals = agents.reduce((sum, agent) => countsOf(agent).map((n, i) => sum[i] + n), [0, 0, 0]);
    const boardKey = totals.join(":");
    if (boardKey !== this.boardKey) {
      paintBoard(this.board, totals); this.boardKey = boardKey;
    }
    const sent = count(world.today?.sent), history = [0, 1, 2].map((i) => Boolean(world.history?.[i]));
    const dailyKey = JSON.stringify([sent, ...history]);
    if (dailyKey !== this.dailyKey) {
      paintDaily(this.daily, sent, history); this.dailyKey = dailyKey;
    }
  }

  dispose() {
    // The shared report canvas is owned by the existing board material.
    for (const texture of this.maps) texture.dispose();
    this.material.dispose();
  }
}
