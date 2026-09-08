// Synchronous room radiance: two fixed PMREM maps, no asset-loading race.
import * as THREE from "/ui/vendor/three/three.module.min.js";
import { targetComplete } from "./post.js";

export function roomEnvironment(renderer) {
  const targets = [];
  const result = { day: null, evening: null,
    dispose() { for (const rt of targets) rt.dispose(); targets.length = 0; } };
  // PMREM itself requires half-float attachments, unlike the LDR post fallback.
  if (!renderer.extensions.has("EXT_color_buffer_half_float")
    && !renderer.extensions.has("EXT_color_buffer_float")) return result;
  const envScene = new THREE.Scene();
  const materials = [];
  const basic = (color, side = THREE.FrontSide) => {
    const m = new THREE.MeshBasicMaterial({ color, side, toneMapped: false });
    materials.push(m);
    return m;
  };
  const wall = basic(0xefebe4, THREE.BackSide);
  const ceiling = basic(0xf6f2ec, THREE.BackSide);
  const floor = basic(0xd9c5a5, THREE.BackSide);
  envScene.add(new THREE.Mesh(new THREE.BoxGeometry(6, 3, 6),
    [wall, wall, ceiling, floor, wall, wall]));
  const windowX = basic(new THREE.Color().setRGB(6, 6, 6));
  const windowZ = basic(new THREE.Color().setRGB(4.0, 4.4, 4.8));
  const paneX = new THREE.Mesh(new THREE.PlaneGeometry(3, 2), windowX);
  paneX.position.set(-2.99, 0.1, 0);
  paneX.rotation.y = Math.PI / 2;
  envScene.add(paneX);
  const paneZ = new THREE.Mesh(new THREE.PlaneGeometry(2, 1.6), windowZ);
  paneZ.position.set(0, 0.15, -2.99);
  envScene.add(paneZ);
  const lamp = basic(new THREE.Color().setRGB(3.0, 2.3, 1.5));
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.25, 16, 12), lamp);
  bulb.position.set(2.2, 1.1, 0);
  envScene.add(bulb);
  let pmrem;
  const previous = renderer.getRenderTarget();
  const toneMapping = renderer.toneMapping;
  const onShaderError = renderer.debug.onShaderError;
  const checkShaderErrors = renderer.debug.checkShaderErrors;
  try {
    renderer.debug.checkShaderErrors = true;
    renderer.debug.onShaderError = () => { throw new Error("Room PMREM unavailable"); };
    renderer.toneMapping = THREE.NoToneMapping;
    pmrem = new THREE.PMREMGenerator(renderer);
    for (const hour of ["day", "evening"]) {
      if (hour === "evening") {
        windowX.color.setRGB(3.5, 2.6, 2.0);
        windowZ.color.setRGB(3.5, 2.6, 2.0);
        lamp.color.setRGB(18.0, 13.8, 9.0);
      }
      const target = pmrem.fromScene(envScene, 0.04, 0.1, 100);
      targets.push(target);
      if (!targetComplete(renderer, target)) throw new Error("Room framebuffer unavailable");
      result[hour] = target.texture;
    }
  } catch {
    result.dispose();
    result.day = result.evening = null; // Hemisphere/key/fill remain usable without an env.
  } finally {
    renderer.setRenderTarget(previous);
    renderer.toneMapping = toneMapping;
    renderer.debug.onShaderError = onShaderError;
    renderer.debug.checkShaderErrors = checkShaderErrors;
    pmrem?.dispose();
    envScene.traverse((o) => o.geometry?.dispose());
    for (const m of materials) m.dispose();
  }
  return result;
}
