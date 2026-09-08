// R90-V3: 既存 import 向けの互換窓口。レイアウトの正本は spec と buildLayout。
// 公開座標を維持し、経路探索は生成グラフ上で行う。
import { buildLayout } from "./layout.js";
import { DEFAULT_SPEC } from "./layout_specs.js";

const layout = buildLayout(DEFAULT_SPEC);
export const {
  LAYOUT, WALL, PODS, cleanerRoute: CLEANER_ROUTE, bossWalk: BOSS_WALK,
  idleSpots: IDLE_SPOTS, restSpots: REST_SPOTS,
} = layout;

/** 従来どおり呼び出し側が変更可能な新しい配列を返す。 */
export function obstacleRects() {
  return layout.obstacleRects.map((rect) => ({ ...rect }));
}

export function walkGraph() {
  return {
    nodes: layout.walkGraph.nodes.map((p) => [...p]),
    edges: layout.walkGraph.edges.map((edge) => [...edge]),
    obstacles: obstacleRects(),
  };
}

/** 線分(a→b)と矩形の交差（端点が矩形内の場合も交差扱い・辺上の掠めは非交差）。 */
export function segIntersectsRect(ax, az, bx, bz, r) {
  // 境界（辺上）を交差に数えない＝矩形をεだけ内側へ縮めて厳密内部で判定する
  const EPS = 1e-6;
  const x1 = r.x1 + EPS;
  const x2 = r.x2 - EPS;
  const z1 = r.z1 + EPS;
  const z2 = r.z2 - EPS;
  const inside = (x, z) => x > x1 && x < x2 && z > z1 && z < z2;
  if (inside(ax, az) || inside(bx, bz)) return true;
  // 各辺との交差判定（軸平行なのでパラメトリックで十分）
  const dx = bx - ax;
  const dz = bz - az;
  let t0 = 0;
  let t1 = 1;
  // Liang–Barsky クリッピング: 交差区間が残れば矩形を通過している
  for (const [p, q] of [
    [-dx, ax - x1], [dx, x2 - ax],
    [-dz, az - z1], [dz, z2 - az],
  ]) {
    if (p === 0) {
      if (q < 0) return false;          // 平行で外側
    } else {
      const t = q / p;
      if (p < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
      if (t0 > t1) return false;
    }
  }
  return t1 - t0 > 1e-9;                // 掠め（接触のみ）は交差扱いしない
}

function connection(point, graph) {
  const [x, z] = point;
  // Seats may be inside their own room; all other solids still block the
  // approach. A point on a corridor gets no such exception.
  const obstacles = (graph.obstacles || []).filter((r) =>
    !(x >= r.x1 && x <= r.x2 && z >= r.z1 && z <= r.z2));
  let best = null;
  let distance = Infinity;
  for (const [i, j] of graph.edges) {
    const a = graph.nodes[i];
    const b = graph.nodes[j];
    const dx = b[0] - a[0];
    const dz = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (z - a[1]) * dz) / (dx * dx + dz * dz)));
    const p = [a[0] + t * dx, a[1] + t * dz];
    const d = (p[0] - x) ** 2 + (p[1] - z) ** 2;
    if (d >= distance || obstacles.some((r) => segIntersectsRect(x, z, ...p, r))) continue;
    best = { point: p, edge: [i, j] };
    distance = d;
  }
  return best;
}

// Binary min-heap: each relaxation costs O(log V), instead of scanning every
// node on every step. The Euclidean heuristic is admissible for weighted edges.
function push(heap, item) {
  let i = heap.length;
  heap.push(item);
  while (i > 0) {
    const parent = (i - 1) >> 1;
    if (heap[parent][0] <= item[0]) break;
    heap[i] = heap[parent];
    i = parent;
  }
  heap[i] = item;
}

function pop(heap) {
  const first = heap[0];
  const last = heap.pop();
  if (heap.length) {
    let i = 0;
    while (2 * i + 1 < heap.length) {
      let child = 2 * i + 1;
      if (child + 1 < heap.length && heap[child + 1][0] < heap[child][0]) child++;
      if (last[0] <= heap[child][0]) break;
      heap[i] = heap[child];
      i = child;
    }
    heap[i] = last;
  }
  return first;
}

/** Join visible projections on corridor edges, then find the shortest A* path. */
export function routePath(from, to, graph = layout.walkGraph) {
  // Accept the historical object-shaped callers as well as coordinate pairs.
  from = Array.isArray(from) ? from : [from.x, from.z];
  to = Array.isArray(to) ? to : [to.x, to.z];
  const start = connection(from, graph);
  const goal = connection(to, graph);
  // An unreachable target must never turn into a straight line through walls.
  if (!start || !goal) return [[...from], [...from]];
  const nodes = [...graph.nodes, start.point, goal.point];
  const s = nodes.length - 2;
  const g = nodes.length - 1;
  const adj = nodes.map(() => []);
  function link(i, j) {
    const w = Math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1]);
    adj[i].push([j, w]);
    adj[j].push([i, w]);
  }
  for (const [i, j] of graph.edges) link(i, j);
  for (const i of start.edge) link(s, i);
  for (const i of goal.edge) link(g, i);
  if (start.edge.every((i) => goal.edge.includes(i))) link(s, g);
  const dist = nodes.map(() => Infinity);
  const prev = nodes.map(() => -1);
  const heuristic = (i) => Math.hypot(nodes[i][0] - nodes[g][0], nodes[i][1] - nodes[g][1]);
  const heap = [];
  dist[s] = 0;
  push(heap, [heuristic(s), 0, s]);
  while (heap.length) {
    const [, distance, u] = pop(heap);
    if (distance !== dist[u]) continue;
    if (u === g) break;
    for (const [v, w] of adj[u]) {
      const next = distance + w;
      if (next < dist[v]) {
        dist[v] = next;
        prev[v] = u;
        push(heap, [next + heuristic(v), next, v]);
      }
    }
  }
  if (!Number.isFinite(dist[g])) return [[...from], [...from]];
  const chain = [];
  for (let n = g; n >= 0; n = prev[n]) {
    chain.push(nodes[n]);
    if (n === s) break;
  }
  const pts = [[...from], ...chain.reverse(), [...to]];
  const out = [pts[0]];
  for (let i = 1; i < pts.length; i++) {
    const last = out[out.length - 1];
    // Only exact duplicates may be removed: dropping a short corner could
    // introduce an unchecked diagonal through a solid during a re-route.
    if (Math.hypot(pts[i][0] - last[0], pts[i][1] - last[1]) > 1e-9) out.push([...pts[i]]);
  }
  if (out.length === 1) out.push([to[0], to[1]]);
  return out;
}
