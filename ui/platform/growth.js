// Only the population peak is client-owned. Server levels never enter this store.
import { maxSeenFor } from "/ui/core/tier.js";
import { frozen } from "./clock.js";

const KEY = "aioffice.growth";
export function createGrowth({ isolated = frozen, storage = () => globalThis.localStorage } = {}) {
  let maxSeen = 0;
  const read = () => {
    if (isolated) return;
    try { maxSeen = maxSeenFor({ maxSeen, agents: JSON.parse(storage()?.getItem(KEY))?.maxSeen }); }
    catch { /* unavailable storage or malformed legacy data: keep the in-memory peak */ }
  };
  read();
  return {
    observe(agents) {
      read(); // Include a larger peak published by another tab.
      const before = maxSeen;
      maxSeen = maxSeenFor({ agents, maxSeen });
      if (!isolated && maxSeen > before) {
        try { storage()?.setItem(KEY, JSON.stringify({ maxSeen })); }
        catch { /* quota/private mode: the office still grows for this session */ }
      }
      return maxSeen;
    },
  };
}
