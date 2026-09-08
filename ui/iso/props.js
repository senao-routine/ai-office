// Four single-material hand props. Geometry is baked relative to the right mitten.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { mergeGeometries } from "./merge.js";

export const PROP_MATERIALS = Object.freeze({ tablet: "dark", mug: "white", wrench: "wood", book: "paper" });

export function propGeometries() {
  const merge = (parts) => {
    const geometry = mergeGeometries(parts);
    parts.forEach(part => part.dispose());
    return geometry;
  };
  // Inset face and raised rim are geometry, with no screen texture/material.
  const tablet = merge([
    new THREE.BoxGeometry(.23, .035, .31),
    new THREE.BoxGeometry(.19, .012, .25).translate(0, -.024, 0),
  ]).translate(-.07, -.04, .03);
  // Lathed cross-section closes the bottom and leaves an actual hollow cup.
  const mug = merge([
    new THREE.LatheGeometry([[0, 0], [.065, 0], [.075, .14], [.061, .14], [.052, .025], [0, .025]]
      .map(([x, y]) => new THREE.Vector2(x, y)), 20),
    new THREE.TorusGeometry(.045, .014, 8, 16).translate(.085, .075, 0),
  ]).rotateX(Math.PI).translate(-.09, .05, 0);
  const jaw = new THREE.Shape();
  jaw.moveTo(-.045, -.08); jaw.lineTo(-.075, -.025); jaw.lineTo(-.062, .06);
  jaw.lineTo(-.03, .075); jaw.lineTo(-.028, .008); jaw.lineTo(.028, .008);
  jaw.lineTo(.03, .075); jaw.lineTo(.062, .06); jaw.lineTo(.075, -.025);
  jaw.lineTo(.045, -.08); jaw.closePath();
  const wrench = merge([
    new THREE.BoxGeometry(.045, .24, .032).translate(0, -.17, .016),
    new THREE.ExtrudeGeometry(jaw, { depth: .032, bevelEnabled: false }),
  ]).rotateX(Math.PI / 2).translate(0, -.045, -.04);
  // Open book: two splayed page blocks and a spine, all using paper.
  const book = merge([
    new THREE.BoxGeometry(.16, .025, .24).rotateZ(.18).translate(-.079, 0, 0),
    new THREE.BoxGeometry(.16, .025, .24).rotateZ(-.18).translate(.079, 0, 0),
    new THREE.BoxGeometry(.022, .04, .25),
  ]).translate(-.10, -.045, .025);
  return { tablet, mug, wrench, book };
}
