import * as THREE from "/ui/vendor/three/three.module.min.js";
import { FACE_KEYS } from "/ui/core/expr.js";

/** Black glass and white silhouettes only; all lighting comes from the visor material. */
export function faceAtlasTexture() {
  const canvas = document.createElement("canvas"), size = 256, aspect = .67 / .65;
  canvas.width = size * 4; canvas.height = size * 2;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#262626"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (const [cell, key] of FACE_KEYS.entries()) {
    ctx.save(); ctx.translate(cell % 4 * size, Math.floor(cell / 4) * size); ctx.scale(size, size);
    ctx.fillStyle = ctx.strokeStyle = "#ffffff"; ctx.lineCap = "round"; ctx.lineWidth = .025;
    const dot = (x, y, r = .06) => {
      ctx.beginPath(); ctx.ellipse(x, y, r, r * aspect, 0, 0, Math.PI * 2); ctx.fill();
    };
    const line = (x1, y1, x2, y2) => {
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    };
    const arc = (x1, y1, cx, cy, x2, y2) => {
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(cx, cy, x2, y2); ctx.stroke();
    };
    for (const x of [.23, .77]) {
      if (key === "blink") line(x - .0475, .49, x + .0475, .49);
      else if (key === "happy") arc(x - .05, .51, x, .39, x + .05, .51);
      else if (key === "sleepy") {
        ctx.beginPath(); ctx.ellipse(x, .49, .06, .06 * aspect, 0, 0, Math.PI); ctx.closePath(); ctx.fill();
      } else if (key === "focus") {
        // Flat/slanted upper lids are part of each eye, never separate eyebrows.
        ctx.beginPath(); ctx.moveTo(x - .06, .455); ctx.lineTo(x + .06, .47);
        ctx.bezierCurveTo(x + .07, .565, x - .07, .565, x - .06, .455); ctx.fill();
      } else if (key === "think") dot(x - .018, .44);
      else if (key === "question") dot(x, x < .5 ? .45 : .51);
      else dot(x, .49);
    }
    if (key === "alert") dot(.5, .65, .032);
    else if (key === "focus") line(.445, .65, .555, .65);
    else if (key === "think") arc(.4125, .68, .5, .57, .5875, .68);
    else if (key === "question") arc(.4125, .64, .5, .71, .5875, .61);
    else arc(.4125, .63, .5, key === "happy" ? .76 : .72, .5875, .63);
    ctx.restore();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  // No mip levels can mix adjacent expressions at the small office-camera scale.
  texture.generateMipmaps = false; texture.minFilter = texture.magFilter = THREE.LinearFilter;
  return texture;
}

class FaceAtlasError extends Error {
  constructor() { super("Face atlas shader unavailable"); this.faceAtlasFailure = true; }
}

/** The only shader change: map local face UVs into one cell, with canvas's top row first. */
function compileHook(material, previous, compiled) {
  return function(shader, renderer) {
    try {
      previous.call(material, shader, renderer);
      if (!shader.vertexShader.includes("#include <common>")
        || !shader.vertexShader.includes("#include <uv_vertex>")) throw new FaceAtlasError();
      shader.vertexShader = shader.vertexShader.replace("#include <common>", `
        #include <common>
        // R90_FACE_ATLAS
        #ifdef USE_INSTANCING
          attribute float faceCell;
        #endif
      `).replace("#include <uv_vertex>", `
        #include <uv_vertex>
        #if defined(USE_INSTANCING) && defined(USE_MAP)
          vMapUv = (vMapUv + vec2(mod(faceCell, 4.0), 1.0 - floor(faceCell / 4.0))) / vec2(4.0, 2.0);
        #endif
      `);
      compiled();
    } catch { throw new FaceAtlasError(); }
  };
}

/** One instanced screen in normal mode; eight stock-shader batches on failure. */
export class FaceAtlasBatch {
  constructor(scene, mesh, capacity) {
    this.scene = scene; this.mesh = mesh; this.capacity = capacity;
    this.material = mesh.material; this.count = 0; this.fallback = false;
    this.fallbackMeshes = [];
    this.matrix = new THREE.Matrix4();
    this.cells = new THREE.InstancedBufferAttribute(new Float32Array(capacity), 1);
    this.cells.setUsage(THREE.DynamicDrawUsage);
    mesh.geometry.setAttribute("faceCell", this.cells);
    mesh.name = "robot:face:atlas";
    const m = this.material;
    this.original = { map: m.map, color: m.color.clone(), emissive: m.emissive.clone(),
      emissiveMap: m.emissiveMap, compile: m.onBeforeCompile, cacheKey: m.customProgramCacheKey };
    this.texture = faceAtlasTexture();
    m.map = this.texture; m.color.set(0xffffff); m.emissive.set(0); m.emissiveMap = null;
    if (typeof m.onBeforeCompile === "function") {
      this.compiled = false;
      this.hook = compileHook(m, m.onBeforeCompile, () => { this.compiled = true; });
      m.onBeforeCompile = this.hook;
      m.customProgramCacheKey = () => "robot-face-atlas:4x2:v1";
      m.needsUpdate = true;
    } else this.useFallback("onBeforeCompile unavailable");
  }

  setCell(index, expression) {
    const cell = FACE_KEYS.indexOf(expression);
    this.cells.setX(index, cell < 0 ? 0 : cell);
  }

  end(count) {
    this.count = count;
    this.cells.needsUpdate = true;
    if (!this.fallback && this.material.onBeforeCompile !== this.hook) this.useFallback("compile hook replaced");
    if (!this.fallback) return;
    this.mesh.count = 0;
    for (const mesh of this.fallbackMeshes) mesh.count = 0;
    for (let i = 0; i < count; i++) {
      const mesh = this.fallbackMeshes[this.cells.getX(i)];
      this.mesh.getMatrixAt(i, this.matrix); mesh.setMatrixAt(mesh.count++, this.matrix);
    }
    for (const mesh of this.fallbackMeshes) mesh.instanceMatrix.needsUpdate = true;
  }

  useFallback(reason = "shader compilation failed") {
    if (this.fallback) return;
    this.fallback = true; this.fallbackReason = reason;
    // Restore the stock shader, not a clone: all eight meshes still share the existing visor.
    this.material.onBeforeCompile = () => {};
    this.material.customProgramCacheKey = () => "robot-face-atlas:stock:v1";
    this.material.needsUpdate = true;
    for (const [cell, key] of FACE_KEYS.entries()) {
      const geometry = this.mesh.geometry.clone(); geometry.deleteAttribute("faceCell");
      const uv = geometry.getAttribute("uv");
      for (let i = 0; i < uv.count; i++) {
        uv.setXY(i, (uv.getX(i) + cell % 4) / 4, (uv.getY(i) + 1 - Math.floor(cell / 4)) / 2);
      }
      const mesh = new THREE.InstancedMesh(geometry, this.material, this.capacity);
      mesh.name = `robot:face:${key}`;
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.frustumCulled = false; mesh.count = 0;
      this.scene.add(mesh); this.fallbackMeshes.push(mesh);
    }
    this.end(this.count);
  }

  /** Retry the same frame, including frozen renders; no timer or animation history. */
  render(renderer, draw) {
    if (!this.fallback && this.material.onBeforeCompile !== this.hook) this.useFallback("compile hook replaced");
    if (this.fallback) return draw();
    if (!renderer.debug) { this.useFallback("shader diagnostics unavailable"); return draw(); }
    const previous = renderer.debug.onShaderError, check = renderer.debug.checkShaderErrors;
    renderer.debug.checkShaderErrors = true;
    renderer.debug.onShaderError = (gl, program, vertex, fragment) => {
      if ((gl.getShaderSource(vertex) || "").includes("R90_FACE_ATLAS")) throw new FaceAtlasError();
      if (previous) previous(gl, program, vertex, fragment);
      else console.error("Shader compilation failed", gl.getProgramInfoLog(program));
    };
    try {
      try {
        const result = draw();
        if (!this.compiled && this.count) {
          this.useFallback("compile hook not invoked");
          return draw();
        }
        return result;
      }
      catch (error) {
        if (!error?.faceAtlasFailure) throw error;
        this.useFallback();
        return draw();
      }
    } finally {
      renderer.debug.onShaderError = previous;
      renderer.debug.checkShaderErrors = check;
    }
  }

  dispose() {
    for (const mesh of this.fallbackMeshes) {
      mesh.removeFromParent(); mesh.dispose(); mesh.geometry.dispose();
    }
    this.texture.dispose();
    const m = this.material, old = this.original;
    m.map = old.map; m.color.copy(old.color); m.emissive.copy(old.emissive); m.emissiveMap = old.emissiveMap;
    m.onBeforeCompile = old.compile; m.customProgramCacheKey = old.cacheKey; m.needsUpdate = true;
  }
}
