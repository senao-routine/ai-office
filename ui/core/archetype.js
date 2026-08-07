// R80.7: ロボットの職業アーキタイプ（純ロジック・DOM/THREE/乱数なし）。
// ユーザーFB「ロボットに特徴がない。動画編集なら動画編集者みたいなデザインが欲しい」。
// プロジェクト名/role のキーワードで職業を決め、該当なしは projectId ハッシュで
// パステル色だけ変える（無関係なプロジェクトに嘘の職業帽子を被せない）。
//
// tint = 殻のインスタンスカラー（白1,1,1からの淡い色差し＝白ロボの人格は保つ）
// acc  = アクセサリのインスタンスカラー

export const ARCHETYPE_RULES = [
  ["video", /動画|映像|ムービー|video|movie|edit|premiere|カット|字幕/i],
  ["writer", /ブログ|blog|記事|執筆|ライタ|note|メルマガ|newsletter|原稿|書/i],
  ["design", /デザイン|design|figma|イラスト|絵|banner|サムネ|lp|ランディング/i],
  ["ops", /経理|申請|運用|自動化|bot|cron|管理|ops|infra|台帳|集計/i],
  ["dev", /開発|コード|アプリ|エンジニア|dev|code|app|api|server|サーバ|基盤|ツール/i],
];

const STYLE = {
  video: { tint: [0.87, 0.92, 1.0], acc: [0.22, 0.24, 0.32] },   // ヘッドホン=ダークグレー
  writer: { tint: [0.88, 0.98, 0.90], acc: [0.98, 0.80, 0.25] }, // 鉛筆=イエロー
  design: { tint: [1.0, 0.90, 0.94], acc: [0.88, 0.36, 0.52] },  // ベレー=ローズ
  ops: { tint: [1.0, 0.96, 0.86], acc: [0.30, 0.32, 0.42] },     // 蝶ネクタイ=ネイビー
  dev: { tint: [0.90, 0.93, 1.0], acc: [0.32, 0.40, 0.85] },     // キャップ=ブルー
};

// 該当なしプロジェクトの淡色パレット（識別はモノグラム＋この色差し）
const PASTELS = [
  [1.0, 1.0, 1.0], [0.92, 0.95, 1.0], [0.93, 1.0, 0.95],
  [1.0, 0.94, 0.92], [0.97, 0.93, 1.0], [1.0, 0.98, 0.90],
];

function hashStr(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** agent（buildWorldの1件）→ {kind, tint, acc}。kind="generic" はアクセサリなし。 */
export function archetypeFor(agent) {
  const text = [agent?.name, agent?.role, agent?.dept].filter(Boolean).join(" ");
  for (const [kind, re] of ARCHETYPE_RULES) {
    if (re.test(text)) return { kind, tint: STYLE[kind].tint, acc: STYLE[kind].acc };
  }
  const h = hashStr(String(agent?.id || agent?.session || ""));
  return { kind: "generic", tint: PASTELS[h % PASTELS.length], acc: null };
}
