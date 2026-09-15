// R96-D: Tripo 生成什器の登録簿。tools/glb_to_geom.py --register が manifest.json から書く（手で編集しない）。
// ここに無い種類は kit.js の procedural にフォールバックする。PWA では manifest.json で
// pwa:false の物を tools/gen_pwa_modules.py が `export default null` に差し替える（同じ import 図のまま軽くする）。

import cafe_counter from "./cafe_counter.js";
import chair from "./chair.js";
import counter from "./counter.js";
import desk from "./desk.js";
import desk_long from "./desk_long.js";
import floor_lamp from "./floor_lamp.js";
import meeting_table from "./meeting_table.js";
import pouf from "./pouf.js";
import shelf from "./shelf.js";
import sofa from "./sofa.js";
import stool from "./stool.js";
import water_station from "./water_station.js";

const place = (mod, place) => (mod ? { ...mod, place } : null);   // PWA のスタブ（null）はそのまま null
export const GEN = Object.freeze({
  cafe_counter: place(cafe_counter, {"yaw": -1.5708, "fit": "box", "fitH": "top"}),
  chair: place(chair, {"yaw": -1.5708, "fit": "box", "fitH": "top"}),
  counter: place(counter, {"yaw": 1.5708, "fit": "box", "fitH": "top"}),
  desk: place(desk, {"yaw": 0.0, "fit": "box", "fitH": "top"}),
  desk_long: place(desk_long, {"yaw": 1.5708, "fit": "box", "fitH": "top"}),
  floor_lamp: place(floor_lamp, {"yaw": 0.0, "fit": "h", "fitH": "full"}),
  meeting_table: place(meeting_table, {"yaw": 1.5708, "fit": "box", "fitH": "top"}),
  pouf: place(pouf, {"yaw": 0.0, "fit": "box", "fitH": "top"}),
  shelf: place(shelf, {"yaw": 1.5708, "fit": "box", "fitH": "full"}),
  sofa: place(sofa, {"yaw": -1.5708, "fit": "box", "fitH": "top"}),
  stool: place(stool, {"yaw": 0.0, "fit": "box", "fitH": "top"}),
  water_station: place(water_station, {"yaw": 0.0, "fit": "h", "fitH": "full"}),
});
