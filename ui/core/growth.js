// Server-owned XP: validate and present the supplied breakdown, never award XP here.
const valid = (n) => typeof n === "number" && Number.isFinite(n) && n >= 0;
const rounded = (n) => Number(n.toFixed(9));

export function growthView(entry) {
  if (!entry || !valid(entry.xp) || !Number.isInteger(entry.level) || entry.level < 0) return null;
  const xp = rounded(entry.xp);
  // Relay intentionally omits the local-only breakdown. Do not invent one.
  if (!entry.breakdown) return { xp, level: entry.level, parts: null };
  const parts = Object.entries(entry.breakdown).map(([key, part]) => ({
    key, count: part?.count, xp: part?.xp,
  }));
  if (!parts.length || parts.some((part) => !valid(part.count) || !valid(part.xp))) return null;
  if (rounded(parts.reduce((sum, part) => sum + part.xp, 0)) !== xp) return null;
  return { xp, level: entry.level, parts };
}

export function projectGrowth(growth, id) {
  return growthView(growth?.byProject?.[id]);
}

/** One notice per newly observed project level; the first snapshot is a baseline. */
export function growthChanges(previous, growth) {
  const levels = new Map(previous || []);
  const changes = [];
  for (const [id, entry] of Object.entries(growth?.byProject || {})) {
    const view = growthView(entry);
    if (!view) continue;
    const before = levels.get(id);
    if (before !== undefined && view.level > before) changes.push({ id, level: view.level });
    levels.set(id, Math.max(before ?? view.level, view.level));
  }
  return { levels, changes };
}
