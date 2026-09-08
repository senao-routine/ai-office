// Core-only, spatial post processing. No history buffers or time uniforms.
import * as THREE from "/ui/vendor/three/three.module.min.js";

const vertexShader = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0);
  }
`;
const gaussian = `
  vec4 blur9(sampler2D tex, vec2 uv, vec2 stepUv) {
    vec4 c = texture2D(tex, uv) * 0.2270270270;
    c += (texture2D(tex, uv + stepUv) + texture2D(tex, uv - stepUv)) * 0.1945945946;
    c += (texture2D(tex, uv + stepUv * 2.0) + texture2D(tex, uv - stepUv * 2.0)) * 0.1216216216;
    c += (texture2D(tex, uv + stepUv * 3.0) + texture2D(tex, uv - stepUv * 3.0)) * 0.0540540541;
    c += (texture2D(tex, uv + stepUv * 4.0) + texture2D(tex, uv - stepUv * 4.0)) * 0.0162162162;
    return c;
  }
`;
const material = (fragmentShader, uniforms, toneMapped = false) => new THREE.ShaderMaterial({
  vertexShader, fragmentShader, uniforms, toneMapped,
  depthTest: false, depthWrite: false, blending: THREE.NoBlending,
});

/** Check actual framebuffer allocation: WebGL errors do not necessarily throw. */
export function targetComplete(renderer, target) {
  const previous = renderer.getRenderTarget();
  try {
    renderer.setRenderTarget(target);
    const gl = renderer.getContext();
    return gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  } finally {
    renderer.setRenderTarget(previous);
  }
}

export class PostProcess {
  constructor(renderer, { quality = "high", tilt = false } = {}) {
    this.renderer = renderer;
    this.quality = ["high", "mobile", "off"].includes(quality) ? quality : "high";
    this.tilt = tilt;
    this.targets = [];
    this.materials = [];
    renderer.info.autoReset = false;
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = 1.0;
    // Require the advertised half-float attachment extension; otherwise use LDR.
    this.type = renderer.extensions.has("EXT_color_buffer_half_float")
      ? THREE.HalfFloatType : THREE.UnsignedByteType;
    if (this.quality !== "high") return; // No RTs, quads or post materials on mobile/off.
    this.scene = new THREE.Scene();
    this.camera = new THREE.Camera();
    this.geometry = new THREE.PlaneGeometry(2, 2);
    this.quad = new THREE.Mesh(this.geometry, null);
    this.quad.frustumCulled = false;
    this.scene.add(this.quad);
    this.threshold = material(`
      uniform sampler2D source;
      uniform float threshold;
      varying vec2 vUv;
      void main() {
        vec4 c = texture2D(source, vUv);
        float luma = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
        gl_FragColor = vec4(c.rgb * max(luma - threshold, 0.0) / max(luma, 0.00001), 0.0);
      }
    `, { source: { value: null }, threshold: { value: 1.0 } });
    this.blur = material(`
      uniform sampler2D source;
      uniform vec2 stepUv;
      varying vec2 vUv;
      ${gaussian}
      void main() { gl_FragColor = blur9(source, vUv, stepUv); }
    `, { source: { value: null }, stepUv: { value: new THREE.Vector2() } });
    this.composite = material(`
      uniform sampler2D source;
      uniform sampler2D bloom;
      uniform sampler2D sharp;
      varying vec2 vUv;
      uint hashPixel(uvec2 p) {
        uint h = p.x * 374761393u + p.y * 668265263u;
        h = (h ^ (h >> 13u)) * 1274126177u;
        return h ^ (h >> 16u);
      }
      void main() {
        float alpha = texture2D(sharp, vUv).a;
        gl_FragColor = vec4(texture2D(source, vUv).rgb + texture2D(bloom, vUv).rgb * 0.10, alpha);
        #include <tonemapping_fragment>
        gl_FragColor.rgb *= 1.0 - 0.08 * smoothstep(0.5, 1.0, length(vUv - 0.5) * 1.25);
        float grain = float(hashPixel(uvec2(gl_FragCoord.xy)) & 65535u) / 65535.0;
        gl_FragColor.rgb *= 1.0 + (grain * 2.0 - 1.0) * 0.01;
        #include <colorspace_fragment>
        gl_FragColor.a = alpha;
        if (alpha == 0.0) gl_FragColor.rgb = vec3(0.0);
      }
    `, { source: { value: null }, bloom: { value: null }, sharp: { value: null } }, true);
    this.materials.push(this.threshold, this.blur, this.composite);
    if (tilt) {
      this.tiltMaterial = material(`
        uniform sampler2D source;
        uniform vec2 stepUv;
        varying vec2 vUv;
        ${gaussian}
        void main() {
          float edge = 1.0 - smoothstep(0.0, 0.08, min(vUv.y, 1.0 - vUv.y));
          gl_FragColor = mix(texture2D(source, vUv), blur9(source, vUv, stepUv), edge);
        }
      `, { source: { value: null }, stepUv: { value: new THREE.Vector2() } });
      this.materials.push(this.tiltMaterial);
    }
  }

  resize() {
    if (this.quality !== "high") return;
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    if (this.width === size.x && this.height === size.y && this.targets.length) return;
    this.width = size.x; this.height = size.y;
    this._releaseTargets();
    try {
      this._allocate();
    } catch {
      this._releaseTargets();
      // Some software drivers advertise HDR but reject multisampled HDR storage.
      if (this.type === THREE.HalfFloatType) {
        this.type = THREE.UnsignedByteType;
        try { this._allocate(); return; } catch { this._releaseTargets(); }
      }
      this._direct();
    }
  }

  _allocate() {
    if (this.renderer.capabilities.maxSamples < 4) throw new Error("MSAA4 unavailable");
    const add = (w, h, samples = 0, depthBuffer = false) => {
      const rt = new THREE.WebGLRenderTarget(w, h, {
        type: this.type, samples, depthBuffer, stencilBuffer: false,
        minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter,
        generateMipmaps: false,
      });
      this.targets.push(rt);
      if (!targetComplete(this.renderer, rt)) throw new Error("Post framebuffer unavailable");
      return rt;
    };
    this.rt0 = add(this.width, this.height, 4, true);
    const w = Math.max(1, Math.floor(this.width / 4));
    const h = Math.max(1, Math.floor(this.height / 4));
    this.rt1 = add(w, h);
    this.rt2 = add(w, h);
    if (this.tilt) {
      this.tiltX = add(this.width, this.height);
      this.tiltY = add(this.width, this.height);
    }
    this.threshold.uniforms.threshold.value = this.type === THREE.HalfFloatType ? 1.0 : 0.9;
  }

  _pass(mat, target) {
    this.quad.material = mat;
    this.renderer.setRenderTarget(target);
    this.renderer.render(this.scene, this.camera);
  }

  render(scene, camera) {
    const r = this.renderer;
    // Count the scene, shadows, transmission and every post draw in one frame.
    r.info.reset();
    this.resize();
    if (this.quality === "high") {
      const onShaderError = r.debug.onShaderError;
      const checkShaderErrors = r.debug.checkShaderErrors;
      try {
        r.debug.checkShaderErrors = true;
        r.debug.onShaderError = (...args) => {
          // Let the face-atlas guard identify its shader before handling post failures.
          onShaderError?.(...args);
          throw new Error("Post shader unavailable");
        };
        r.toneMapping = THREE.NoToneMapping;
        r.setRenderTarget(this.rt0);
        r.render(scene, camera);
        this.threshold.uniforms.source.value = this.rt0.texture;
        this._pass(this.threshold, this.rt1);
        this.blur.uniforms.source.value = this.rt1.texture;
        this.blur.uniforms.stepUv.value.set(1 / this.rt1.width, 0);
        this._pass(this.blur, this.rt2);
        this.blur.uniforms.source.value = this.rt2.texture;
        this.blur.uniforms.stepUv.value.set(0, 1 / this.rt1.height);
        this._pass(this.blur, this.rt1);
        let source = this.rt0.texture;
        if (this.tilt) {
          // Maximum kernel extent = 2 CSS pixels, confined to the outer 8%.
          const px = 0.5 * r.getPixelRatio();
          this.tiltMaterial.uniforms.source.value = source;
          this.tiltMaterial.uniforms.stepUv.value.set(px / this.width, 0);
          this._pass(this.tiltMaterial, this.tiltX);
          this.tiltMaterial.uniforms.source.value = this.tiltX.texture;
          this.tiltMaterial.uniforms.stepUv.value.set(0, px / this.height);
          this._pass(this.tiltMaterial, this.tiltY);
          source = this.tiltY.texture;
        }
        r.toneMapping = THREE.NeutralToneMapping;
        this.composite.uniforms.source.value = source;
        this.composite.uniforms.sharp.value = this.rt0.texture;
        this.composite.uniforms.bloom.value = this.rt1.texture;
        this._pass(this.composite, null);
        return;
      } catch (error) {
        if (error?.faceAtlasFailure) throw error; // Face batches retry without disabling post.
        this._direct();
      } finally {
        r.debug.onShaderError = onShaderError;
        r.debug.checkShaderErrors = checkShaderErrors;
        r.setRenderTarget(null);
        r.toneMapping = THREE.NeutralToneMapping;
      }
    }
    r.setRenderTarget(null);
    r.toneMapping = THREE.NeutralToneMapping;
    r.render(scene, camera);
  }

  _releaseTargets() {
    for (const rt of this.targets) rt.dispose();
    this.targets.length = 0;
    this.rt0 = this.rt1 = this.rt2 = this.tiltX = this.tiltY = null;
  }

  _direct() {
    this.renderer.setRenderTarget(null);
    this.quality = "off";
    this.dispose();
    this.renderer.toneMapping = THREE.NeutralToneMapping;
  }

  dispose() {
    this._releaseTargets();
    for (const m of this.materials) m.dispose();
    this.materials.length = 0;
    this.geometry?.dispose();
  }
}
