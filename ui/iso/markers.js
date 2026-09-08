import * as THREE from "/ui/vendor/three/three.module.min.js";

// One semantic ink plus white edging/details. No text, glyphs or font lookup.
const INK = Object.freeze({ attention: "#c0483a", think: "#2e2d2c", chat: "#2e2d2c", done: "#5f7d59" });
export function markerTexture(kind) {
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 128;
  const g = canvas.getContext("2d"), ink = INK[kind];
  g.lineCap = g.lineJoin = "round";
  const fill = () => {
    g.fillStyle = ink; g.fill();
    g.lineWidth = 6; g.strokeStyle = "#ffffff"; g.stroke();
  };
  const circle = (x, y, r) => { g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); };
  if (kind === "attention") {
    // Exclamation stem and dot are closed paths, not a font-rendered "!".
    g.beginPath(); g.moveTo(50, 18); g.lineTo(78, 18);
    g.lineTo(74, 77); g.lineTo(54, 77); g.closePath(); fill();
    circle(64, 101, 12); fill();
  } else if (kind === "think") {
    // Cloud lobes and two detached trail dots distinguish thought from speech.
    circle(19, 108, 5); fill(); circle(32, 91, 9); fill();
    g.beginPath(); g.moveTo(27, 72);
    g.bezierCurveTo(3, 62, 9, 31, 30, 30);
    g.bezierCurveTo(33, 10, 60, 7, 72, 22);
    g.bezierCurveTo(93, 7, 118, 27, 109, 46);
    g.bezierCurveTo(128, 68, 105, 90, 85, 79);
    g.bezierCurveTo(62, 92, 44, 85, 27, 72); g.closePath(); fill();
    g.fillStyle = "#ffffff";
    for (const x of [42, 64, 86]) { circle(x, 53, 6); g.fill(); }
  } else if (kind === "chat") {
    // One continuous outline, including the tail (no seam across its root).
    g.beginPath(); g.moveTo(35, 81);
    g.bezierCurveTo(3, 68, 8, 19, 48, 17);
    g.bezierCurveTo(85, 9, 118, 24, 114, 55);
    g.bezierCurveTo(113, 79, 89, 89, 61, 83);
    g.lineTo(29, 107); g.lineTo(35, 81); g.closePath(); fill();
    g.fillStyle = "#ffffff";
    for (const x of [42, 64, 86]) { circle(x, 51, 6); g.fill(); }
  } else if (kind === "done") {
    // White under-stroke is the border of the sage check, with no circular badge.
    g.beginPath(); g.moveTo(25, 65); g.lineTo(51, 91); g.lineTo(104, 34);
    g.strokeStyle = "#ffffff"; g.lineWidth = 23; g.stroke();
    g.strokeStyle = ink; g.lineWidth = 13; g.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
