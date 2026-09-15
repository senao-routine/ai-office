#!/usr/bin/env python3
"""GLB（Tripo 生成・Draco 無し）→ AI Office の生成ジオメトリ JS モジュール（R96-D0）。

three の addons（GLTFLoader）を vendor しないための自前変換器。GLB のヘッダ＋JSON＋BIN を読み、
部品（mesh / node）ごとに position / normal / index を取り出し、Int16 に量子化して
`ui/iso/gen/<name>.js` へ書く。色は `--bake-color` でベースカラーテクスチャを頂点色に焼く（PIL・tools 限定）。

使い方:  python3 tools/glb_to_geom.py in.glb --name armchair --out ui/iso/gen [--bake-color] [--max-tris 8000] [--max-kb 120]
出力: export default { name, bbox, parts: [{ name, tri, pos:[q,...], nrm:[...], idx:[...], col:[...]?, scale, offset }] }
"""
import argparse, json, math, os, struct, sys, base64

def read_glb(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise SystemExit("not a GLB v2")
    off = 12; gltf = None; bin_chunk = None
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", data, off); off += 8
        chunk = data[off:off + clen]; off += clen
        if ctype == b"JSON":
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == b"BIN\x00":
            bin_chunk = chunk
    if gltf is None or bin_chunk is None:
        raise SystemExit("GLB に JSON/BIN チャンクが無い")
    for ext in gltf.get("extensionsRequired", []) + gltf.get("extensionsUsed", []):
        if "draco" in ext.lower():
            raise SystemExit("Draco 圧縮の GLB は読まない（gltf-transform は --compress を付けずに）")
    return gltf, bin_chunk

CTYPE = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

def accessor(gltf, bin_chunk, idx):
    acc = gltf["accessors"][idx]
    bv = gltf["bufferViews"][acc["bufferView"]]
    fmt, size = CTYPE[acc["componentType"]]; n = NCOMP[acc["type"]]
    stride = bv.get("byteStride") or size * n
    base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    out = []
    for i in range(acc["count"]):
        o = base + i * stride
        out.append(struct.unpack_from("<" + fmt * n, bin_chunk, o) if n > 1 else struct.unpack_from("<" + fmt, bin_chunk, o)[0])
    if acc.get("normalized"):
        mx = {"B": 255, "H": 65535, "b": 127, "h": 32767}.get(fmt)
        if mx: out = [tuple(v / mx for v in t) if n > 1 else v / mx for t in out]
    return out

def node_world(gltf, ni, parent=None):
    node = gltf["nodes"][ni]
    if "matrix" in node:
        m = node["matrix"]
    else:
        t = node.get("translation", [0, 0, 0]); r = node.get("rotation", [0, 0, 0, 1]); s = node.get("scale", [1, 1, 1])
        x, y, z, w = r
        m = [ (1-2*(y*y+z*z))*s[0], (2*(x*y+z*w))*s[0], (2*(x*z-y*w))*s[0], 0,
              (2*(x*y-z*w))*s[1], (1-2*(x*x+z*z))*s[1], (2*(y*z+x*w))*s[1], 0,
              (2*(x*z+y*w))*s[2], (2*(y*z-x*w))*s[2], (1-2*(x*x+y*y))*s[2], 0,
              t[0], t[1], t[2], 1 ]
    if parent is None: return m
    # column-major 4x4 multiply parent * m
    r = [0]*16
    for c in range(4):
        for rr in range(4):
            r[c*4+rr] = sum(parent[k*4+rr] * m[c*4+k] for k in range(4))
    return r

def apply(m, v):
    x, y, z = v
    return (m[0]*x + m[4]*y + m[8]*z + m[12], m[1]*x + m[5]*y + m[9]*z + m[13], m[2]*x + m[6]*y + m[10]*z + m[14])

def normal_matrix(m):
    """ワールド行列の線形部 3x3 の逆転置（非一様スケールでも法線が面に垂直のまま）。特異なら線形部そのまま。"""
    a, b, c = m[0], m[4], m[8]
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    det = a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)
    if abs(det) < 1e-12:
        return (a, b, c, d, e, f, g, h, i)
    # inverse = adj / det; transpose(inverse) = transpose(adj) / det
    inv = ((e*i - f*h), -(b*i - c*h), (b*f - c*e),
           -(d*i - f*g), (a*i - c*g), -(a*f - c*d),
           (d*h - e*g), -(a*h - b*g), (a*e - b*d))
    it = (inv[0], inv[3], inv[6], inv[1], inv[4], inv[7], inv[2], inv[5], inv[8])
    return tuple(v / det for v in it)

def det3(m):
    a, b, c = m[0], m[4], m[8]
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    return a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)

def apply_normal(nm, n):
    x, y, z = n
    v = (nm[0]*x + nm[1]*y + nm[2]*z, nm[3]*x + nm[4]*y + nm[5]*z, nm[6]*x + nm[7]*y + nm[8]*z)
    l = math.sqrt(sum(c*c for c in v)) or 1.0
    return tuple(c / l for c in v)

def collect(gltf, bin_chunk):
    """scene の全 node を辿り、mesh ごとに world 座標の三角形群を返す（部品名は node/mesh 名）。"""
    parts = []
    scene = gltf["scenes"][gltf.get("scene", 0)]
    def walk(ni, parent):
        node = gltf["nodes"][ni]; m = node_world(gltf, ni, parent); nm = normal_matrix(m)
        if "mesh" in node:
            mesh = gltf["meshes"][node["mesh"]]
            for pi, prim in enumerate(mesh.get("primitives", [])):
                if prim.get("mode", 4) != 4: continue
                pos = [apply(m, v) for v in accessor(gltf, bin_chunk, prim["attributes"]["POSITION"])]
                nrm = [apply_normal(nm, v) for v in accessor(gltf, bin_chunk, prim["attributes"]["NORMAL"])] if "NORMAL" in prim["attributes"] else None
                uv = accessor(gltf, bin_chunk, prim["attributes"]["TEXCOORD_0"]) if "TEXCOORD_0" in prim["attributes"] else None
                idx = accessor(gltf, bin_chunk, prim["indices"]) if "indices" in prim else list(range(len(pos)))
                if det3(m) < 0:
                    # 反転スケール（scale [-1,1,1] 等）は面の向きを裏返す＝片面描画で外側がカリングされるので頂点順も反転する
                    idx = [v for t in range(0, len(idx) - len(idx) % 3, 3) for v in (idx[t], idx[t + 2], idx[t + 1])]
                name = node.get("name") or mesh.get("name") or f"mesh{node['mesh']}"
                if len(mesh.get("primitives", [])) > 1: name += f"_{pi}"
                parts.append({"name": name, "pos": pos, "nrm": nrm, "uv": uv, "idx": idx, "material": prim.get("material")})
        for c in node.get("children", []): walk(c, m)
    for ni in scene.get("nodes", []): walk(ni, None)
    return parts

def face_normals(pos, idx):
    nrm = [[0.0, 0.0, 0.0] for _ in pos]
    for i in range(0, len(idx), 3):
        a, b, c = pos[idx[i]], pos[idx[i+1]], pos[idx[i+2]]
        u = (b[0]-a[0], b[1]-a[1], b[2]-a[2]); v = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
        n = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        for k in (idx[i], idx[i+1], idx[i+2]):
            nrm[k][0] += n[0]; nrm[k][1] += n[1]; nrm[k][2] += n[2]
    out = []
    for n in nrm:
        l = math.sqrt(n[0]*n[0]+n[1]*n[1]+n[2]*n[2]) or 1.0
        out.append((n[0]/l, n[1]/l, n[2]/l))
    return out

def weld(p, eps=1e-4):
    """同じ位置＋法線の頂点を 1 つに畳む（Tripo の出力は三角形ごとに頂点が分かれていて 1.5〜3 倍太い）。"""
    pos, nrm, idx = p["pos"], p["nrm"] or face_normals(p["pos"], p["idx"]), p["idx"]
    uv = p["uv"]
    key2new = {}; new_pos = []; new_nrm = []; new_uv = []; remap = []
    q = 1.0 / eps
    for i, v in enumerate(pos):
        n = nrm[i]
        k = (round(v[0]*q), round(v[1]*q), round(v[2]*q), round(n[0]*50), round(n[1]*50), round(n[2]*50),
             (round(uv[i][0]*512), round(uv[i][1]*512)) if uv is not None else 0)
        j = key2new.get(k)
        if j is None:
            j = len(new_pos); key2new[k] = j; new_pos.append(v); new_nrm.append(n)
            if uv is not None: new_uv.append(uv[i])
        remap.append(j)
    p["pos"], p["nrm"], p["idx"] = new_pos, new_nrm, [remap[i] for i in idx]
    if uv is not None: p["uv"] = new_uv
    return p

def top_surface(parts, bbox):
    """一番大きい上向きの面（机の天板・座面・棚板）の高さ。kit の fit:"box" が spec.h をここに合わせる。"""
    lo, hi = bbox
    h = max(1e-6, hi[1] - lo[1]); bins = [0.0] * 200
    for p in parts:
        pos, idx = p["pos"], p["idx"]
        for t in range(0, len(idx), 3):
            a, b, c = pos[idx[t]], pos[idx[t + 1]], pos[idx[t + 2]]
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            area2 = math.sqrt(nx * nx + ny * ny + nz * nz)
            if area2 <= 0 or ny / area2 < 0.9:
                continue
            y = (a[1] + b[1] + c[1]) / 3
            bins[min(199, max(0, int((y - lo[1]) / h * 200)))] += area2
    # 幅 4% の窓で面積を足し、最大の 30% 以上ある窓のうち**一番高い**ものを天板にする。
    # 単純な最大面積だとソファは「クッションの下の受け板」を拾い、座面が 30cm 高く出る（別モデルレビューの実測）。
    W = 8
    windows = [sum(bins[i:i + W]) for i in range(0, 200 - W + 1)]
    best = max(windows)
    if best <= 0:
        return round(lo[1] + h, 5)
    k = max(i for i, a in enumerate(windows) if a >= 0.3 * best)
    ys = sum(bins[j] * (j + 0.5) for j in range(k, k + W)); a = sum(bins[k:k + W])
    return round(lo[1] + (ys / a) / 200 * h, 5)


def quantize(parts):
    """全部品共通の bbox で Int16 量子化（scale/offset を 1 組持つ＝部品間の位置関係を保つ）。"""
    xs = [c for p in parts for v in p["pos"] for c in (v[0],)]; ys = [v[1] for p in parts for v in p["pos"]]; zs = [v[2] for p in parts for v in p["pos"]]
    lo = (min(xs), min(ys), min(zs)); hi = (max(xs), max(ys), max(zs))
    span = max(hi[i]-lo[i] for i in range(3)) or 1.0
    q = 32767.0 / span
    for p in parts:
        p["qpos"] = [int(round((v[i]-lo[i]) * q)) for v in p["pos"] for i in range(3)]
        nrm = p["nrm"] or face_normals(p["pos"], p["idx"])
        p["qnrm"] = [int(round(c * 127)) for v in nrm for c in v]
    return {"offset": [lo[0], lo[1], lo[2]], "scale": span / 32767.0, "bbox": [list(lo), list(hi)]}

def b64_i16(vals): return base64.b64encode(struct.pack("<%dh" % len(vals), *vals)).decode("ascii")
def b64_i8(vals): return base64.b64encode(struct.pack("<%db" % len(vals), *vals)).decode("ascii")
def b64_u16(vals): return base64.b64encode(struct.pack("<%dH" % len(vals), *vals)).decode("ascii")
def b64_u8(vals): return base64.b64encode(bytes(vals)).decode("ascii")

INDEX_HEAD = """// R96-D: Tripo 生成什器の登録簿。tools/glb_to_geom.py --register が manifest.json から書く（手で編集しない）。
// ここに無い種類は kit.js の procedural にフォールバックする。PWA では manifest.json で
// pwa:false の物を tools/gen_pwa_modules.py が `export default null` に差し替える（同じ import 図のまま軽くする）。
"""


def register(out_dir, name, meta):
    """manifest.json に name を登録し、index.js を manifest から決定論で書き直す。"""
    mpath = os.path.join(out_dir, "manifest.json")
    manifest = json.load(open(mpath, encoding="utf-8")) if os.path.exists(mpath) else {"items": {}}
    manifest.setdefault("items", {})[name] = meta
    manifest["items"] = dict(sorted(manifest["items"].items()))
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2); f.write("\n")
    lines = [INDEX_HEAD]
    for n in manifest["items"]:
        lines.append(f'import {n} from "./{n}.js";')
    lines.append("")
    lines.append("const place = (mod, place) => (mod ? { ...mod, place } : null);   // PWA のスタブ（null）はそのまま null")
    lines.append("export const GEN = Object.freeze({")
    for n, m in manifest["items"].items():
        hint = json.dumps({"yaw": m.get("yaw", 0), "fit": m.get("fit", "w"), "fitH": m.get("fitH", "top")})
        lines.append(f"  {n}: place({n}, {hint}),")
    lines.append("});")
    with open(os.path.join(out_dir, "index.js"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb"); ap.add_argument("--name", required=True); ap.add_argument("--out", default="ui/iso/gen")
    ap.add_argument("--max-tris", type=int, default=8000); ap.add_argument("--max-kb", type=int, default=120)
    ap.add_argument("--bake-color", action="store_true", help="ベースカラーテクスチャを頂点色に焼く（PIL）")
    ap.add_argument("--dry", action="store_true", help="書かずに統計だけ")
    ap.add_argument("--no-weld", action="store_true", help="頂点の溶接をしない")
    ap.add_argument("--register", action="store_true", help="ui/iso/gen/manifest.json と index.js に登録する")
    ap.add_argument("--pwa", action="store_true", help="登録時: PWA にも同梱する（manifest pwa:true）")
    ap.add_argument("--yaw", type=float, default=0.0, help="登録時: 正面を +z に向ける回転（rad）")
    ap.add_argument("--fit", default="w", choices=["w", "d", "h", "box"], help="登録時: spec のどの寸法に合わせるか")
    ap.add_argument("--fit-h", default="top", choices=["top", "full"], help="登録時: 高さは天板/座面(top)か外形(full)を spec.h に合わせるか")
    ap.add_argument("--source", default="", help="登録時: Tripo task id")
    a = ap.parse_args()
    gltf, bin_chunk = read_glb(a.glb)
    parts = collect(gltf, bin_chunk)
    if not parts: raise SystemExit("メッシュが無い")
    tris = sum(len(p["idx"]) // 3 for p in parts)
    if tris > a.max_tris: raise SystemExit(f"三角形 {tris} > 上限 {a.max_tris}（Tripo 側で face_limit を下げる）")
    if not a.no_weld:
        parts = [weld(p) for p in parts]
    meta = quantize(parts)
    out_parts = []
    for p in parts:
        n = len(p["pos"])
        rec = {"name": p["name"], "n": n, "tri": len(p["idx"]) // 3,
               "pos": b64_i16(p["qpos"]), "nrm": b64_i8(p["qnrm"]),
               "idx": b64_u16(p["idx"]) if n <= 65535 else None}
        if rec["idx"] is None: raise SystemExit(f"{p['name']}: 頂点 {n} > 65535")
        if a.bake_color and p.get("material") is not None:
            col = bake_color(gltf, bin_chunk, p, a.glb)
            if col is not None: rec["col"] = col
        out_parts.append(rec)
    mod = {"name": a.name, "offset": meta["offset"], "scale": meta["scale"], "bbox": meta["bbox"], "parts": out_parts,
           "top": top_surface(parts, meta["bbox"]), "tris": tris, "source": os.path.basename(a.glb)}
    js = "// generated by tools/glb_to_geom.py — do not edit by hand (R96-D)\nexport default " + json.dumps(mod, separators=(",", ":")) + ";\n"
    kb = len(js.encode("utf-8")) / 1024
    print(f"{a.name}: parts={len(parts)} tris={tris} verts={sum(len(p['pos']) for p in parts)} size={kb:.1f}KB", file=sys.stderr)
    if kb > a.max_kb: raise SystemExit(f"サイズ {kb:.1f}KB > 上限 {a.max_kb}KB")
    if a.dry: return
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, a.name + ".js"), "w", encoding="utf-8") as f: f.write(js)
    if a.register:
        register(a.out, a.name, {"pwa": bool(a.pwa), "yaw": a.yaw, "fit": a.fit, "fitH": a.fit_h, "tris": tris, "kb": round(kb, 1),
                                 "source": a.source or os.path.basename(os.path.dirname(os.path.abspath(a.glb)))})

def bake_color(gltf, bin_chunk, p, glb_path):
    """材質のベースカラー（factor × texture）を頂点ごとにサンプルして 0..255 の RGB へ。
    PIL はテクスチャがある材質でだけ import する（単色材質は stdlib だけで焼ける＝macOS 標準 python でもテストが通る）。"""
    import io
    mat = gltf["materials"][p["material"]]; pbr = mat.get("pbrMetallicRoughness", {})
    factor = pbr.get("baseColorFactor", [1, 1, 1, 1])
    img = None
    wrap = (10497, 10497)   # REPEAT
    if "baseColorTexture" in pbr:
        from PIL import Image
        tex = gltf["textures"][pbr["baseColorTexture"]["index"]]; image = gltf["images"][tex["source"]]
        if "sampler" in tex:
            smp = gltf.get("samplers", [])[tex["sampler"]]
            wrap = (smp.get("wrapS", 10497), smp.get("wrapT", 10497))
        if "bufferView" in image:
            bv = gltf["bufferViews"][image["bufferView"]]
            raw = bin_chunk[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        elif "uri" in image and not image["uri"].startswith("data:"):
            img = Image.open(os.path.join(os.path.dirname(glb_path), image["uri"])).convert("RGB")
    # sRGB バイト → リニア → factor（リニア）を掛ける → sRGB バイトへ戻す（kit 側はこのバイトをリニアへ戻して使う）。
    to_lin = [(c / 255) / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in range(256)]
    def to_srgb(v):
        v = min(1.0, max(0.0, v))
        v = v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return int(round(v * 255))
    cols = []
    if img is not None and p["uv"] is not None:
        w, h = img.size; px = img.load()
        def coord(t, n, mode):
            if mode == 33071:                       # CLAMP_TO_EDGE
                return min(n - 1, max(0, int(t * n)))
            if mode == 33648:                       # MIRRORED_REPEAT
                f = t % 2.0
                if f > 1.0: f = 2.0 - f
                return min(n - 1, max(0, int(f * n)))
            return min(n - 1, max(0, int(t * n) % n))   # REPEAT
        for (u, v) in p["uv"]:
            x = coord(u, w, wrap[0]); y = coord(v, h, wrap[1])
            r, g, b = px[x, y]
            cols += [to_srgb(to_lin[r] * factor[0]), to_srgb(to_lin[g] * factor[1]), to_srgb(to_lin[b] * factor[2])]
    elif img is not None:
        return None   # テクスチャがあるのに UV が無い＝焼けない（共通材質の白のまま）
    else:
        solid = [to_srgb(factor[0]), to_srgb(factor[1]), to_srgb(factor[2])]
        for _ in p["pos"]:
            cols += solid
    return b64_u8(cols)

if __name__ == "__main__":
    main()
