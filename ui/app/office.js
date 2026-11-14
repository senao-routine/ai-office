// R122: the office is a 300px window in the app's right column. Both renderers
// consume the same world; their drawing code stays owned by the original view.
import { ROOM, roomScale, tintHash, tintIndex } from "/ui/core/pxroom.js";
import { assignOverflow, assignSeats } from "/ui/core/world.js";
import { createRoomLighting, frozen, loop, now } from "/ui/platform/clock.js";
import { IsoScene } from "/ui/iso/scene3d.js";
import { init as initRoom } from "/ui/cockpit/room.js";
import { SESSION_COLORS, UI_COLORS } from "/ui/cockpit/palette.js";

const ROOM_KEY = "aioffice.app.room";
const PIP_KEY = "aioffice.app.pip";
const validMode = (mode) => mode === "pixel" ? "pixel" : "3d";

export function createOffice({ host, T, onSelect, demo = false }) {
  const reduced = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  const still = () => frozen || Boolean(reduced?.matches);
  const lightStage = createRoomLighting();
  let mode = "3d";
  let pipOpen = true;
  if (!frozen && !demo) {
    try { mode = validMode(localStorage.getItem(ROOM_KEY)); } catch { /* Storage is optional. */ }
    try { pipOpen = localStorage.getItem(PIP_KEY) !== "0"; } catch { /* Storage is optional. */ }
  }
  let world = null;
  let roomWorld = null;
  let selected = null;
  let hasSelection = false;
  let disposed = false;
  let iso = null;
  let pixel = null;
  let tints = new Map();
  const lastDraw = { "3d": -1, pixel: -1 };
  let dirty = true;
  let pixelLeft = 0;
  let pixelTop = 0;
  let scale = 2;
  const layers = {};

  host.classList.add("app-office-viewport");
  host.style.position = "relative";
  host.style.overflow = "hidden";
  for (const kind of ["3d", "pixel"]) {
    const layer = document.createElement("div");
    layer.className = `app-room-layer app-room-${kind}`;
    layer.dataset.room = kind;
    Object.assign(layer.style, { position: "absolute", inset: "0", overflow: "hidden" });
    layers[kind] = layer;
    host.append(layer);
  }
  layers.pixel.style.background = UI_COLORS.stage;
  const labels = document.createElement("div");
  labels.className = "app-room-labels";
  labels.setAttribute("aria-hidden", "true");
  const labelNodes = new Map();
  const pip = document.createElement("div");
  pip.className = "app-room-pip";
  const pipView = document.createElement("div");
  pipView.className = "app-room-pip-view";
  pipView.setAttribute("aria-hidden", "true");
  const button = (className, key, visible = true) => {
    const node = document.createElement("button");
    node.type = "button"; node.className = className;
    node.dataset[visible ? "appText" : "appLabel"] = key;
    if (visible) node.textContent = T(key);
    else node.setAttribute("aria-label", T(key));
    return node;
  };
  const pipSwap = button("app-room-pip-swap", "app_room_pip_swap", false);
  const pipHide = button("app-room-pip-hide", "app_room_pip_hide");
  const pipShow = button("app-room-pip-show", "app_room_pip_show");
  pip.append(pipView, pipSwap, pipHide);
  host.append(labels, pip, pipShow);
  const hasPip = () => pipOpen && host.dataset.roomFallback !== "pixel";
  const visible = (kind) => kind === mode || hasPip();

  const agentFor = (value) => {
    if (!value || !world) return null;
    const session = typeof value === "string" ? value : value.session;
    const id = typeof value === "string" ? value : value.id;
    return world.agents.find((a) => session && a.session === session)
      || world.agents.find((a) => id && a.id === id) || null;
  };
  const selectedAgent = () => agentFor(selected);
  const renderId = (agent) => agent?.session || agent?.id || null;
  const colorOf = (key) => SESSION_COLORS[tints.get(String(key)) ?? tintHash(String(key)) % SESSION_COLORS.length];

  const ensureRenderer = () => {
    if (host.dataset.roomFallback === "pixel") mode = "pixel";
    // Move each renderer between the main view and the inset. A collapsed
    // pixel-only office never allocates a WebGL canvas.
    for (const kind of ["3d", "pixel"]) {
      const parent = kind === mode ? host : pipView;
      if (layers[kind].parentNode !== parent) parent.append(layers[kind]);
      layers[kind].hidden = !visible(kind);
    }
    pip.hidden = !hasPip();
    pipShow.hidden = pipOpen || host.dataset.roomFallback === "pixel";
    if (visible("3d") && !iso && host.dataset.roomFallback !== "pixel") {
      try {
        iso = new IsoScene(layers["3d"]);
        const canvas = iso.renderer.domElement;
        canvas.dataset.room = "3d";
        canvas.setAttribute("aria-hidden", "true");
        Object.assign(canvas.style, { display: "block", width: "100%", height: "100%" });
      } catch {
        // A machine without WebGL still has a working office and conversation.
        layers["3d"].replaceChildren();
        layers["3d"].dataset.unavailable = "true";
        host.dataset.roomFallback = "pixel";
        mode = "pixel";
        ensureRenderer();
        return;
      }
    }
    if (visible("pixel") && !pixel) {
      pixel = initRoom({ host: layers.pixel, frozen: still, keepPrev: !frozen, colorOf,
        scale, life: new URLSearchParams(location.search).get("life") !== "0" });
      const canvas = layers.pixel.querySelector("canvas");
      canvas.dataset.room = "pixel";
      Object.assign(canvas.style, { position: "absolute", imageRendering: "pixelated", maxWidth: "none" });
    }
    host.dataset.roomMode = mode;
    host.dataset.roomPip = hasPip() ? "1" : "0";
    pip.dataset.room = mode === "3d" ? "pixel" : "3d";
    pipView.dataset.room = pip.dataset.room;
    if (host.dataset.roomFallback) host.setAttribute("aria-label", T("app_room_unavailable"));
  };

  const placePixel = () => {
    const view = layers.pixel;
    const width = view.clientWidth, height = view.clientHeight;
    if (!pixel || width <= 0 || height <= 0) return;
    // Keep the renderer's integer minimum. Only the outer wall is clipped when
    // the 320px room is placed in the app's 300px window.
    pixelLeft = Math.round((width - ROOM.w * scale) / 2);
    pixelTop = Math.round((height - ROOM.h * scale) / 2);
    const actor = pixel.actors().find((a) => a.session === renderId(selectedAgent()));
    if (mode === "3d" && actor) {
      pixelLeft = Math.round(width / 2 - (actor.x + ROOM.robot.w / 2) * scale);
      pixelTop = Math.round(height / 2 - (actor.y + ROOM.robot.h / 2) * scale);
    } else if (actor) {
      const x = pixelLeft + actor.x * scale, y = pixelTop + actor.y * scale;
      if (x < 0) pixelLeft -= x;
      else if (x + ROOM.robot.w * scale > width) pixelLeft -= x + ROOM.robot.w * scale - width;
      if (y < 0) pixelTop -= y;
      else if (y + ROOM.robot.h * scale > height) pixelTop -= y + ROOM.robot.h * scale - height;
    }
    Object.assign(layers.pixel.querySelector("canvas").style, {
      left: `${pixelLeft}px`, top: `${pixelTop}px`,
      width: `${ROOM.w * scale}px`, height: `${ROOM.h * scale}px`,
    });
  };

  const point = (id) => {
    if (mode === "3d") return iso?.projectAgent(id) || null;
    const p = pixel?.point(id);
    if (!p) return null;
    const r = host.getBoundingClientRect();
    return { left: p.left - r.left, top: p.top - r.top };
  };
  const paintLabels = () => {
    const chosen = selectedAgent();
    const agents = [...(chosen ? [chosen] : []), ...(world?.agents || []).filter((a) => a.attention)];
    const keep = new Set(), placed = [];
    const clamp = (value, max) => Math.max(4, Math.min(Math.max(4, max), value));
    const overlaps = (a, b) => a.left < b.left + b.width && b.left < a.left + a.width && a.top < b.top + b.height && b.top < a.top + a.height;
    for (const agent of agents) {
      const id = renderId(agent);
      if (!id || keep.has(id)) continue;
      if (keep.size === 4) break;
      keep.add(id);
      let label = labelNodes.get(id);
      if (!label) {
        label = document.createElement("span"); label.dataset.agent = id;
        labelNodes.set(id, label); labels.append(label);
      }
      const isChosen = id === renderId(chosen);
      label.className = `app-room-label${isChosen ? " is-selected" : ""}${agent.attention ? " is-attention" : ""}`;
      const name = Array.from(String(agent.name || ""));
      label.textContent = name.length > 12 ? `${name.slice(0, 12).join("")}…` : name.join("");
      const p = point(id);
      label.hidden = !p || !Number.isFinite(p.left) || !Number.isFinite(p.top);
      if (label.hidden) continue;
      const width = label.offsetWidth, height = label.offsetHeight;
      const box = { width, height,
        left: clamp(p.left - width / 2, host.clientWidth - width - 4),
        top: clamp(p.top - height - (mode === "3d" ? 12 : 4 * scale + 4), host.clientHeight - height - 4) };
      // 隣の机の人どうしは名札が重なる（本番の実データで 6px 差）＝先に置いた札（選んだ人が先頭）を避けて上へ、
      // 上が尽きたら下へずらす。選んだ人の札は重なり順でも一番上。
      for (let step = 0; step < 8 && placed.some((q) => overlaps(box, q)); step++) {
        const up = box.top - (height + 3);
        box.top = up >= 4 ? up : clamp(Math.max(...placed.map((q) => q.top + q.height)) + 3, host.clientHeight - height - 4);
      }
      placed.push(box);
      label.style.zIndex = isChosen ? "2" : "1";
      label.style.left = `${Math.round(box.left)}px`;
      label.style.top = `${Math.round(box.top)}px`;
    }
    for (const [id, label] of labelNodes) {
      if (!keep.has(id)) { label.remove(); labelNodes.delete(id); }
    }
    // 選んだ人が右下の小窓の裏に入ったら、小窓を左下へ逃がす（入っていなければ右下へ戻す）
    const target = chosen && point(renderId(chosen));
    if (target && hasPip()) {
      const w = pip.offsetWidth, h = pip.offsetHeight, W = host.clientWidth, H = host.clientHeight;
      const under = (left) => target.left >= left && target.left <= left + w && target.top >= H - 8 - h && target.top <= H - 8;
      const flip = pip.classList.contains("is-left") ? under(8) : under(W - 8 - w);
      if (flip) pip.classList.toggle("is-left");
    }
  };

  const paint = (t = now(), force = false) => {
    if (disposed || !world || host.offsetParent === null || !host.clientWidth) return;
    let painted = false;
    for (const kind of ["3d", "pixel"]) {
      if (!visible(kind)) continue;
      const fps = kind === "pixel" ? pixel?.fps() || 6 : mode === "3d" ? 24 : 8;
      const clock = still() ? (frozen ? t : 0) : Math.floor(t * fps) / fps;
      if (!dirty && clock <= lastDraw[kind] && !force) continue;
      lastDraw[kind] = clock;
      if (kind === "3d") iso?.update(roomWorld, clock);
      else if (pixel) {
        scale = mode === "pixel" ? roomScale(host.clientWidth, host.clientHeight) : 2;
        pixel.setScale(scale);
        pixel.setSelected(renderId(selectedAgent()));
        pixel.draw(roomWorld, [], clock, { lightStage: lightStage(still()) });
        placePixel();
      }
      painted = true;
    }
    dirty = false;
    if (painted) paintLabels();
  };

  const focus = (agent) => {
    if (!agent) iso?.focusOff();
    else {
      // 3D keeps the whole floor visible in either slot. The selected name
      // and greeting identify the person; the pixel inset supplies the close-up.
      if (visible("3d")) iso?.greet(renderId(agent));
      pixel?.setSelected(renderId(agent)); pixel?.setGreet(renderId(agent));
    }
    dirty = true;
  };

  const resize = () => {
    if (disposed) return;
    if (host.offsetParent !== null && visible("3d")) iso?.resize();
    dirty = true;
    paint(now(), true);
  };
  const setMode = (next) => {
    if (disposed) return;
    mode = validMode(next);
    ensureRenderer();
    if (mode !== "3d") iso?.focusOff();
    if (!frozen && !demo) {
      try { localStorage.setItem(ROOM_KEY, mode); } catch { /* Storage is optional. */ }
    }
    lastDraw["3d"] = lastDraw.pixel = -1;
    resize();
    host.dispatchEvent(new CustomEvent("app-room-change", { bubbles: true, detail: mode }));
  };

  const setPip = (open) => {
    if (disposed) return;
    pipOpen = open;
    ensureRenderer();
    if (!frozen && !demo) {
      try { localStorage.setItem(PIP_KEY, open ? "1" : "0"); } catch { /* Storage is optional. */ }
    }
    resize();
  };
  pip.addEventListener("click", (event) => event.stopPropagation());
  pipSwap.addEventListener("click", (event) => {
    event.stopPropagation();
    // Selecting a person in the inset and swapping views is one action. The
    // event never falls through to the newly exposed main renderer.
    let agent = null;
    if (event.detail) {
      if (mode === "3d") agent = agentFor(pixel?.pick(event.clientX, event.clientY));
      else {
        const rect = pipView.getBoundingClientRect();
        agent = agentFor(iso?.pickAgent(event.clientX - rect.left, event.clientY - rect.top));
      }
    }
    if (agent) { selected = agent; focus(agent); onSelect?.(agent); }
    setMode(mode === "3d" ? "pixel" : "3d");
  });
  pipHide.addEventListener("click", (event) => { event.stopPropagation(); setPip(false); pipShow.focus(); });
  pipShow.addEventListener("click", (event) => { event.stopPropagation(); setPip(true); pipHide.focus(); });

  const clicked = (event) => {
    let agent;
    if (mode === "3d") {
      const rect = host.getBoundingClientRect();
      agent = agentFor(iso?.pickAgent(event.clientX - rect.left, event.clientY - rect.top));
    } else agent = agentFor(pixel?.pick(event.clientX, event.clientY));
    if (!agent) return;
    selected = agent;
    focus(agent);
    paint(now(), true);
    onSelect?.(agent);
  };
  host.addEventListener("click", clicked);
  const motionChanged = () => { dirty = true; paint(now(), true); };
  reduced?.addEventListener?.("change", motionChanged);
  ensureRenderer();
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(resize) : null;
  observer?.observe(host);
  const stop = loop((t) => paint(t));

  // A stable scene object also serves the shared HUD when the renderer changes.
  const scene = {
    ready: () => !visible("3d") || Boolean(iso?.ready()),
    update: () => paint(now(), true), resize,
    focusOn: (id) => { selected = agentFor(id); focus(selected); paint(now(), true); },
    focusOff: () => { iso?.focusOff(); dirty = true; paint(now(), true); },
    pickAgent: (x, y) => {
      if (mode === "3d") return iso?.pickAgent(x, y) || null;
      const rect = host.getBoundingClientRect();
      return pixel?.pick(rect.left + x, rect.top + y)?.id || null;
    },
    projectAgent: (id) => {
      return point(renderId(agentFor(id)));
    },
    project: (...args) => mode === "3d" ? iso?.project(...args) || null : null,
    projectBoss: () => mode === "3d" ? iso?.projectBoss() || null : null,
    labelAnchorFor: (...args) => mode === "3d" ? iso?.labelAnchorFor(...args) || null : null,
    stats: () => mode === "3d" ? iso?.stats() || {} : { ...pixel?.stats(), robots: pixel?.stats().actors || 0 },
  };

  const api = {
    scene, setMode, mode: () => mode, resize,
    update(nextWorld, nextSelected = null) {
      const previous = selectedAgent();
      world = nextWorld;
      selected = nextSelected;
      // A project can contain several sessions with the same source id. Give
      // each person one internal id without changing the source members that
      // clicks return to the app, and rebuild the room's seat maps accordingly.
      const agents = (world?.agents || []).map((agent) => ({ ...agent,
        id: renderId(agent), session: renderId(agent), sessions: [], crew: 1 }));
      const seats = assignSeats(agents);
      roomWorld = world ? { ...world, agents, seats, overflow: assignOverflow(agents, seats) } : null;
      tints = tintIndex(agents.map((a) => a.id), SESSION_COLORS.length, frozen ? null : tints);
      dirty = true;
      paint(now(), true); // Fixed-clock pages have no subsequent animation tick.
      const current = selectedAgent();
      if (hasSelection && current?.session !== previous?.session) {
        focus(current);
        paint(now(), true);
      }
      hasSelection = Boolean(current);
    },
    stats: scene.stats,
    /**
     * R129 今日の 1 枚: いまの部屋をドット絵で 1 枚に写す（3D か ドット絵かを問わず同じ絵＝整数倍で拡大してもくっきり）。
     * 一時の部屋を作って同じ世界を 1 回だけ描き、写しを返して捨てる（表示中の部屋と帳簿を触らない）。
     * 名前は入らない: 名札は DOM で、部屋の canvas は文字を描かない（cockpit の fillText の門）。部屋が無ければ null。
     */
    snapshot(k = ROOM.scale) {
      if (!roomWorld) return null;
      const holder = document.createElement("div");
      const shot = initRoom({ host: holder, frozen: () => true, keepPrev: false, colorOf, scale: k, life: false });
      try {
        shot.draw(roomWorld, [], now(), { lightStage: lightStage(still()) });
        const src = holder.querySelector("canvas");
        const copy = document.createElement("canvas");
        copy.width = src.width; copy.height = src.height;
        copy.getContext("2d").drawImage(src, 0, 0);
        return copy;
      } finally {
        shot.dispose();
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      stop(); observer?.disconnect();
      reduced?.removeEventListener?.("change", motionChanged);
      host.removeEventListener("click", clicked);
      iso?.dispose(); pixel?.dispose();
      layers["3d"].remove(); layers.pixel.remove();
      labels.remove(); pip.remove(); pipShow.remove();
    },
  };
  return api;
}
