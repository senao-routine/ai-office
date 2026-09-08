// Capacity depends only on the observed population peak; levels unlock furnishings.
import { LAYOUT_SPECS, M } from "./layout_specs.js";

const count = (n) => Number.isSafeInteger(n) && n >= 0 ? n : 0;
export const maxSeenFor = ({ agents = 0, maxSeen = 0 } = {}) =>
  Math.max(count(maxSeen), count(Array.isArray(agents) ? agents.length : agents));

/** Caller carries maxSeen forward, including across reloads. officeLevel cannot buy seats. */
export function tierFor({ agents = 0, maxSeen = 0, officeLevel } = {}) {
  const peak = maxSeenFor({ agents, maxSeen });
  return peak <= 8 ? "S" : peak <= 12 ? "M" : peak <= 18 ? "L" : "XL";
}

export function decorationsFor(officeLevel) {
  const level = count(officeLevel);
  return { plants: level >= 3, coffee: level >= 5, meet3: level >= 8,
    lounge: level >= 12, meet4: level >= 15, cafe: level >= 20 };
}

/** No level in older snapshots (including golden): retain the original furnishing set.
 * Explicit server levels, including zero, always take precedence over this compatibility path.
 */
export function specFor(tier, officeLevel) {
  const base = LAYOUT_SPECS[tier];
  if (!base) throw new RangeError("unknown office tier");
  if (officeLevel == null) return base;
  const decor = decorationsFor(officeLevel);
  const layout = { ...base.layout };
  const rooms = M.rooms.filter((room) => !["meet3", "meet4"].includes(room.id) || decor[room.id]);
  for (const room of M.rooms) {
    if (rooms.includes(room)) layout[room.zone] = base.layout[room.zone] || M.layout[room.zone];
    else delete layout[room.zone];
  }
  // The east edge is unchanged, so the 0.9 m aisle to meeting room 3 survives expansion.
  if (!decor.lounge) layout.loungeZone = { ...layout.loungeZone, x: 9.2, w: 3.6 };
  const furnishings = base.furnishings.filter((f) =>
    (f.id !== "coffee" || decor.coffee) && (f.id !== "touchdown" || decor.cafe)
    && (f.id !== "loungeChair" || decor.lounge)).map((f) =>
    f.id === "loungeSofa" && !decor.lounge ? { ...f, dx: -.3, w: 2.2, seats: 2 } : f);
  const rest = base.rest.filter((s) => decor.lounge || s.role !== "tablet").map((s) =>
    s.area === "lounge" && !decor.lounge ? { ...s, dx: s.dx + .6 } : s);
  return { ...base, layout, rooms, furnishings, rest, decor,
    fixtures: base.fixtures.filter((f) => f.id !== "touchdown" || decor.cafe),
    idle: base.idle.filter((s) => s.why !== "coffee" || decor.coffee) };
}
