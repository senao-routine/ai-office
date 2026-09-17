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
    observe(agents, serverPeak = 0) {
      read(); // Include a larger peak published by another tab.
      const before = maxSeen;
      // R97: Mac 側が覚えている peak も畳み込む。これが無いと、別のブラウザで開いた・保存領域が
      // 消えた、というだけで机が消えて「壊れた」ように見える（本人の報告）。
      maxSeen = maxSeenFor({ agents, maxSeen: maxSeenFor({ maxSeen, agents: serverPeak }) });
      if (!isolated && maxSeen > before) {
        try { storage()?.setItem(KEY, JSON.stringify({ maxSeen })); }
        catch { /* quota/private mode: the office still grows for this session */ }
      }
      return maxSeen;
    },
  };
}
