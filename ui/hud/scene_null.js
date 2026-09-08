// WebGL が使えないときも HUD を利用できる null object。
import { frozen } from "/ui/platform/clock.js";

/** ctx: shell, T, openList */
export function init({ shell, T, openList }) {
  const nil = () => null;
  const scene = {
    ready: () => true, update: () => {}, resize: () => {}, dispose: () => {},
    pickAgent: nil, projectAgent: nil, project: nil, projectBoss: nil,
    labelAnchorFor: nil, focusOn: () => {}, focusOff: () => {},
    stats: () => ({ drawCalls: 0, robots: 0 }),
  };
  const vp = shell.querySelector("#viewport");
  if (vp) {
    const note = document.createElement("div");
    note.id = "no3d";
    note.className = "no3d";
    note.textContent = T("no3d");
    if (!frozen) {
      const panel = document.createElement("div"); panel.className = "no3d no3d-panel";
      note.className = "";
      const list = document.createElement("button"); list.type = "button";
      list.id = "no3d-list"; list.className = "abtn"; list.textContent = T("no3d_list");
      list.addEventListener("click", () => openList?.());
      panel.append(note, list); vp.append(panel);
    } else vp.append(note);
  }
  return scene;
}
