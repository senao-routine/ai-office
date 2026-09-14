// Capacity depends only on the observed population peak; levels unlock furnishings.
import { LAYOUT_SPECS, M } from "./layout_specs.js";

const count = (n) => Number.isSafeInteger(n) && n >= 0 ? n : 0;
const TIERS = ["S", "M", "L", "XL"];
export const maxSeenFor = ({ agents = 0, maxSeen = 0 } = {}) =>
  Math.max(count(maxSeen), count(Array.isArray(agents) ? agents.length : agents));

/** Caller carries maxSeen forward, including across reloads. officeLevel cannot buy seats. */
export function tierFor({ agents = 0, maxSeen = 0, officeLevel, cap = null } = {}) {
  const peak = maxSeenFor({ agents, maxSeen });
  const tier = peak <= 8 ? "S" : peak <= 12 ? "M" : peak <= 18 ? "L" : "XL";
  const limit = TIERS.indexOf(cap);
  return limit >= 0 && limit < TIERS.indexOf(tier) ? cap : tier;
}

/** R94（2026-09-14 本人裁定「本当に会社のようなオフィス」）: 会議室・ラウンジ・植物・コーヒーは**最初から在る**。
 * R90 の「レベルで解錠」は本人の実オフィスが Lv0 で会議室 2 つ・ラウンジ無しの「劣化版」に見えた。
 * レベルで増えるのは cafe（タッチダウン席）だけ残す。 */
export function decorationsFor(officeLevel) {
  const level = count(officeLevel);
  return { plants: true, coffee: true, meet3: true, lounge: true, meet4: true, cafe: level >= 20 };
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
