// 共有の絵文字除去に、cockpit の端末用の記号を足す。
// ▶ を消す前に > に変え、人数の帯で「作業中」の数を判別できるようにする。
// iso / pixel は従来の T を保つ。スモークもここから同じ関数を引く。
import { plainText as hudPlainText } from "/ui/hud/plain.js";

export function plainText(value) {
  return hudPlainText(typeof value === "string" ? value.replace(/▶/gu, ">") : value);
}

const COCKPIT_KEYS = {
  off_noconn: "cp_off_noconn", off_stale: "cp_off_stale",
  kind_perm: "cp_kind_perm", kind_question: "cp_kind_question", kind_plan: "cp_kind_plan", kind_stuck: "cp_kind_stuck",
};
export const plainT = (T) => Object.assign((key, ...args) => plainText(T(COCKPIT_KEYS[key] || key, ...args)), { plain: true });
