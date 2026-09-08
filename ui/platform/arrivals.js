import { epochNow, frozen } from "./clock.js";

const KEY = "aioffice.arrivals.v1";
const TTL = 600;

/** Presentation metadata only. Never persist prompts, names, or working paths. */
export function createArrivals({ isolated = frozen, demo = false, clock = epochNow,
  storage = () => globalThis.localStorage } = {}) {
  if (isolated) return { update() {}, hired() {}, label: () => null };
  if (demo) {
    let rows = new Map();
    return { hired() {}, update(office) {
      rows = new Map((office?.roster || []).map((row) => [row.session, row]));
    }, label: (session) => rows.get(session)?.isNew ? { isNew: true, slug: "" } : null };
  }
  let seen = null;
  let rows = new Map();
  const stamps = new Map();
  try {
    const saved = JSON.parse(storage().getItem(KEY) || "[]");
    if (Array.isArray(saved)) for (const row of saved.slice(-256)) {
      if (Array.isArray(row) && typeof row[0] === "string" && Number.isFinite(row[1])) {
        stamps.set(row[0], row[1]);
      }
    }
  } catch { /* Storage may be unavailable. */ }
  const fresh = (at, t) => Number.isFinite(at) && t >= at && t - at < TTL;
  const save = (t) => {
    for (const [id, at] of stamps) if (!fresh(at, t)) stamps.delete(id);
    try { storage().setItem(KEY, JSON.stringify([...stamps].slice(-256))); } catch { /* In memory works. */ }
  };
  return {
    hired(bgId) {
      if (!bgId) return;
      const t = clock();
      stamps.set(`bg:${bgId}`, t);
      save(t);
    },
    update(office) {
      const t = clock();
      rows = new Map((office?.employees || []).map((e) => [e.session, e]));
      for (const [id, row] of rows) {
        if (!id || row.external) continue;
        const jobAt = stamps.get(`bg:${row.bg?.id}`);
        if (fresh(jobAt, t)) stamps.set(`session:${id}`, jobAt);
        else if (seen && !seen.has(id) && !stamps.has(`session:${id}`)) stamps.set(`session:${id}`, t);
      }
      // Retain the baseline across temporary disappearance; returning sessions are not new.
      seen = new Set([...(seen || []), ...rows.keys()]);
      save(t);
    },
    label(session) {
      const row = rows.get(session);
      if (!row || row.external) return null;
      const isNew = fresh(stamps.get(`session:${session}`), clock());
      // Only label actual worktrees, using their observed slug, never a guessed branch.
      const slug = String(row.cwd || "").match(/\/worktrees\/([^/]+)(?:\/|$)/)?.[1] || "";
      return isNew || slug ? { isNew, slug } : null;
    },
  };
}
