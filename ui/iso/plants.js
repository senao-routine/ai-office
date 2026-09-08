// Seeded leaf cards: all species share one atlas and one static leaf batch.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { rand } from "/ui/platform/clock.js";

export const PLANT_PRESETS = Object.freeze({
  monstera: { cell: 0, shells: [.28, .18, .08], cards: [7, 5, 3], tilt: .85, size: [.50, .65] },
  strelitzia: { cell: 1, shells: [.20, .12, .04], cards: [5, 4, 3], tilt: .42, size: [.35, .90] },
  pothos: { cell: 2, shells: [.25, .16, .06], cards: [9, 7, 4], tilt: 1.1, size: [.30, .42] },
  snake: { cell: 3, shells: [.18, .10, .03], cards: [7, 5, 3], tilt: .15, size: [.18, .85] },
});

export function leafAtlasTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 512;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#4f5a2a";
  for (let cell = 0; cell < 4; cell++) {
    const x = cell % 2 * 256 + 128, y = Math.floor(cell / 2) * 256 + 118;
    ctx.beginPath(); ctx.ellipse(x, y, cell === 3 ? 24 : 64, 100, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillRect(x - 3, y, 6, 125);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.userData.file = "leaf_atlas.webp";
  return texture;
}

export function leafCardGeometry(width, height, cell, inner = false) {
  const geometry = new THREE.PlaneGeometry(width, height, 3, 3);
  const position = geometry.getAttribute("position"), uv = geometry.getAttribute("uv");
  const colors = new Float32Array(position.count * 3);
  for (let i = 0; i < position.count; i++) {
    const t = uv.getY(i);
    // Petiole is the origin; a quadratic bend lifts the tip by 6 cm.
    position.setY(i, t * height); position.setZ(i, .06 * t * t);
    uv.setXY(i, (cell % 2 + .005 + uv.getX(i) * .99) / 2,
      (1 - Math.floor(cell / 2) + .005 + t * .99) / 2);
    const shade = (.82 + .18 * t) * (inner ? .9 : 1);
    colors.fill(shade, i * 3, i * 3 + 3);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  return geometry;
}

/** Append in stable room/species/shell/card order, synchronously after resetRand(). */
export function addPlant(pieces, { x, y = .04, z, scale = 1, species = "monstera", terra = false }) {
  const preset = PLANT_PRESETS[species];
  const at = (dx, dy, dz) => new THREE.Matrix4().makeScale(scale, scale, scale)
    .setPosition(x + dx * scale, y + dy * scale, z + dz * scale);
  const profile = [[0, 0], [.15, 0], [.18, .04], [.23, .40], [.24, .44], [.22, .46], [.20, .42], [.19, .36]];
  pieces.push({ geometry: new THREE.LatheGeometry(profile.map(([r, h]) => new THREE.Vector2(r, h)), 24),
    material: terra ? "potTerra" : "pot", matrix: at(0, 0, 0) });
  // Soil shares the dark warm material; vertex tint keeps it at the guide's #3c3229.
  const soil = new THREE.CircleGeometry(.20, 24).rotateX(-Math.PI / 2);
  const tint = new THREE.Color(0x3c3229), base = new THREE.Color(0x2e2d2c);
  tint.setRGB(tint.r / base.r, tint.g / base.g, tint.b / base.b);
  const colors = new Float32Array(soil.getAttribute("position").count * 3);
  for (let i = 0; i < colors.length; i += 3) tint.toArray(colors, i);
  soil.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  pieces.push({ geometry: soil, material: "dark", matrix: at(0, .405, 0) });
  let index = 0;
  for (let shell = 0; shell < preset.shells.length; shell++) {
    for (let card = 0; card < preset.cards[shell]; card++, index++) {
      const angle = 2.399963 * index + rand() * .3;
      const radius = preset.shells[shell], size = .9 + rand() * .2;
      const stemHeight = species === "snake" ? 0 : .18 + shell * .12;
      const dx = Math.sin(angle) * radius, dz = Math.cos(angle) * radius;
      const matrix = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(preset.tilt, angle, 0, "YXZ"))
        .scale(new THREE.Vector3(scale, scale, scale))
        .setPosition(x + dx * scale, y + (.43 + stemHeight) * scale, z + dz * scale);
      pieces.push({ geometry: leafCardGeometry(preset.size[0] * size, preset.size[1] * size,
        preset.cell, shell === 2), material: "leafCard", matrix, preserveColor: true });
      if (scale >= 1 && stemHeight > 0) pieces.push({
        geometry: new THREE.CylinderGeometry(.008, .012, stemHeight, 8), material: "felt",
        matrix: at(dx, .43 + stemHeight / 2, dz),
      });
    }
  }
  return index;
}
