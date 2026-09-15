#!/usr/bin/env python3
"""リグ付き GLB（Tripo retarget 出力＝スキン＋メッシュ＋アニメ）→ AI Office の JS モジュール 2 本（R96-D2 S2・stdlib）。

  python3 tools/glb_rig.py body  anim_idle.glb --name robot_body --out ui/iso/gen [--fit-height 1.484] [--bake-color] [--max-kb 160]
  python3 tools/glb_rig.py clips idle=anim_idle.glb walk=anim_walk.glb … --name robot_clips --out ui/iso/gen [--fps 15] [--fit-height 1.484] [--max-kb 160]

body:  {name, kind:"rig", scale, offset, bbox, height, parts:[{n,tri,pos,nrm,idx,col?,joints,weights}], skeleton:{joints:[node…], nodes:[{name,parent,t,r,s}], ibm, root, hip}}
clips: {kind:"clips", fps, joints:[node…], clips:{idle:{duration,frames,tracks:{jointIndex:{r:<i16×4 b64>, t?:<i16×3 b64・rest からの差分>, tScale}}}}}
GLB の座標はメッシュに焼かず（スキン付きはバインド姿勢のまま）、--fit-height は geometry・IBM 平行移動・骨 TRS・clip の平行移動を同じ係数で縮める。"""
import argparse, base64, json, math, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glb_skin as G

def b64(fmt, flat):
    return base64.b64encode(struct.pack("<%d%s" % (len(flat), fmt), *flat)).decode("ascii")

def scale_skin(sk, k):
    for n in sk["nodes"]: n["t"] = [v * k for v in n["t"]]
    if sk["ibm"]:
        for m in sk["ibm"]: m[12] *= k; m[13] *= k; m[14] *= k
    return sk

def body(a):
    data = open(a.glb, "rb").read(); gltf, bin_chunk = G.read_glb(data)
    prims = G.skinned_primitives(gltf, bin_chunk)
    if not prims: sys.exit("スキン付き primitive が無い")
    sk = G.read_skin(gltf, bin_chunk, prims[0]["skin"])
    xs = [v[0] for p in prims for v in p["pos"]]; ys = [v[1] for p in prims for v in p["pos"]]; zs = [v[2] for p in prims for v in p["pos"]]
    height = max(ys) - min(ys); k = (a.fit_height / height) if a.fit_height else 1.0
    lo = (min(xs) * k, min(ys) * k, min(zs) * k); hi = (max(xs) * k, max(ys) * k, max(zs) * k)
    span = max(hi[i] - lo[i] for i in range(3)) or 1.0; q = 32767.0 / span
    scale_skin(sk, k)
    parts = []; tris = 0
    for p in prims:
        pos = [(v[0] * k, v[1] * k, v[2] * k) for v in p["pos"]]
        if a.cut_above is not None:
            # 首より上（頭）を落として InstancedMesh の頭・顔アトラスを Head 骨のソケットへ載せる（ハイブリッド C）
            keep = []
            for t in range(0, len(p["idx"]) - len(p["idx"]) % 3, 3):
                tri = p["idx"][t:t + 3]
                if not all(pos[i][1] >= a.cut_above for i in tri): keep += tri
            used = sorted(set(keep)); remap = {old_i: new_i for new_i, old_i in enumerate(used)}
            p = dict(p, idx=[remap[i] for i in keep], pos=[p["pos"][i] for i in used], nrm=[p["nrm"][i] for i in used] if p["nrm"] else None,
                     uv=[p["uv"][i] for i in used] if p["uv"] else None, joints=[p["joints"][i] for i in used], weights=[p["weights"][i] for i in used])
            pos = [pos[i] for i in used]
        qpos = [int(round((v[i] - lo[i]) * q)) for v in pos for i in range(3)]
        nrm = p["nrm"] or [(0, 1, 0)] * len(pos)
        qnrm = [max(-127, min(127, int(round(c * 127)))) for n in nrm for c in n]
        joints = [c for j in p["joints"] for c in j]
        weights = []
        for w in p["weights"]:
            ww = [int(round(c * 255)) for c in w]; d = 255 - sum(ww); ww[ww.index(max(ww))] += d   # 和を 255 に
            weights += ww
        rec = {"name": p["name"], "n": len(pos), "tri": len(p["idx"]) // 3, "pos": b64("h", qpos), "nrm": b64("b", qnrm),
               "idx": b64("H", p["idx"]), "joints": b64("B", joints), "weights": b64("B", weights)}
        if a.bake_color and p.get("material") is not None and p["uv"] is not None:
            rec["col"] = bake(gltf, bin_chunk, p)
        parts.append(rec); tris += rec["tri"]
    hip = next((i for i, j in enumerate(sk["joints"]) if sk["nodes"][j]["name"].lower() in ("hip", "hips", "mixamorig:hips", "pelvis")), None)
    mod = {"name": a.name, "kind": "rig", "offset": list(lo), "scale": span / 32767.0, "bbox": [list(lo), list(hi)], "height": round(height * k, 5), "fit": k,
           "parts": parts, "tris": tris,
           "skeleton": {"joints": sk["joints"], "root": sk["root"], "hip": hip,
                        "nodes": [{"name": n["name"], "parent": n["parent"], "t": [round(v, 6) for v in n["t"]], "r": [round(v, 6) for v in n["r"]], "s": [round(v, 6) for v in n["s"]]} for n in sk["nodes"]],
                        "ibm": b64("f", [round(v, 7) for m in sk["ibm"] for v in m]) if sk["ibm"] else None},
           "source": os.path.basename(a.glb)}
    return mod

def bake(gltf, bin_chunk, p):
    from PIL import Image
    import io
    mat = gltf["materials"][p["material"]]; pbr = mat.get("pbrMetallicRoughness", {})
    factor = pbr.get("baseColorFactor", [1, 1, 1, 1]); img = None
    if "baseColorTexture" in pbr:
        tex = gltf["textures"][pbr["baseColorTexture"]["index"]]; image = gltf["images"][tex["source"]]
        bv = gltf["bufferViews"][image["bufferView"]]; raw = bin_chunk[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    to_lin = [(c / 255) / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in range(256)]
    def to_srgb(v):
        v = min(1.0, max(0.0, v)); v = v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return int(round(v * 255))
    cols = []
    if img is None:
        solid = [to_srgb(factor[0]), to_srgb(factor[1]), to_srgb(factor[2])]
        for _ in p["pos"]: cols += solid
    else:
        w, h = img.size; px = img.load()
        for (u, v) in p["uv"]:
            x = min(w - 1, max(0, int(u * w) % w)); y = min(h - 1, max(0, int(v * h) % h)); r, g, b = px[x, y]
            cols += [to_srgb(to_lin[r] * factor[0]), to_srgb(to_lin[g] * factor[1]), to_srgb(to_lin[b] * factor[2])]
    return base64.b64encode(bytes(cols)).decode("ascii")

def clips(a):
    out = {"kind": "clips", "fps": a.fps, "clips": {}}
    joints_ref = None
    for spec in a.clip:
        name, path = spec.split("=", 1)
        gltf, bin_chunk = G.read_glb(open(path, "rb").read())
        sk = G.read_skin(gltf, bin_chunk); k = 1.0
        if a.fit_height:
            prims = G.skinned_primitives(gltf, bin_chunk)
            ys = [v[1] for p in prims for v in p["pos"]]; k = a.fit_height / (max(ys) - min(ys))
        names_here = [sk["nodes"][j]["name"] for j in sk["joints"]]
        if joints_ref is None: joints_ref = list(names_here); out["joints"] = list(names_here)
        elif names_here != out["joints"]: sys.exit(f"{name}: 骨の集合が body と違う")
        # この GLB のノード索引 → 骨番号は joints[] の位置で（名前列が body と同じ並びであることは上で検査済み・同名の骨があっても壊れない）
        node_to_joint = {j: pos for pos, j in enumerate(sk["joints"])}
        cl = G.read_clips(gltf, bin_chunk, fps=a.fps, joints=set(sk["joints"]), root_joint=sk["root"])
        # 1 本なら name、複数（≤5 まとめの retarget 出力）なら animation 名の末尾（preset:biped:walk → walk）
        for c in cl:
            cname = name if len(cl) == 1 and name != "*" else c["name"].split(":")[-1]
            if a.max_seconds and c["duration"] > a.max_seconds:
                cut = int(round(a.max_seconds * a.fps)) + 1
                blend = max(1, int(round(0.5 * a.fps)))   # 末尾 0.5 秒を先頭フレームへ寄せてループの継ぎ目を消す
                for rec in c["tracks"].values():
                    for key in ("r", "t"):
                        if key not in rec: continue
                        seq = rec[key][:cut]
                        for i in range(1, blend + 1):
                            idx = cut - 1 - blend + i
                            if idx <= 0 or idx >= cut: continue
                            w = i / blend
                            seq[idx] = G._slerp(seq[idx], seq[0], w) if key == "r" else [x + (y - x) * w for x, y in zip(seq[idx], seq[0])]
                        rec[key] = seq
                c["frames"] = cut; c["duration"] = round((cut - 1) / a.fps, 4)
            tracks = {}
            for node, rec in c["tracks"].items():
                ji = node_to_joint.get(node)
                if ji is None: continue
                t = {}
                if "r" in rec:
                    flat = [max(-32767, min(32767, int(round(v * 32767)))) for q4 in rec["r"] for v in q4]
                    t["r"] = b64("h", flat)
                if "t0" in rec:
                    rest_t = sk["nodes"][node]["t"]; t["t0"] = [round((v - rest_t[i]) * k, 6) for i, v in enumerate(rec["t0"])]
                if "r0" in rec:
                    t["r0"] = [round(v, 6) for v in rec["r0"]]
                if "t" in rec:
                    # 平行移動は「その clip の先頭フレームからの差分」で持つ（rest に足して使う）。
                    # in_place の retarget は Hip の絶対値が 0 付近（rest は 0.137）＝絶対値で置くと体が腰の高さぶん沈む（実測）。上下の揺れ（差分）だけを使う。
                    base = rec["t"][0]
                    vals = [(v - base[i]) * k for v3 in rec["t"] for i, v in enumerate(v3)]; m = max(abs(v) for v in vals) or 1.0
                    t["t"] = b64("h", [int(round(v / m * 32767)) for v in vals]); t["tScale"] = m / 32767.0
                if t: tracks[str(ji)] = t
            out["clips"][cname] = {"duration": c["duration"], "frames": c["frames"], "tracks": tracks}
    return out

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("body"); b.add_argument("glb"); b.add_argument("--name", default="robot_body"); b.add_argument("--out", default="ui/iso/gen")
    b.add_argument("--fit-height", type=float, default=None); b.add_argument("--bake-color", action="store_true"); b.add_argument("--cut-above", type=float, default=None, help="この高さ（fit 後の単位）以上の三角形を落とす＝頭を外す（ハイブリッド C）"); b.add_argument("--max-kb", type=float, default=160); b.add_argument("--dry", action="store_true")
    c = sub.add_parser("clips"); c.add_argument("clip", nargs="+", help="name=path.glb"); c.add_argument("--name", default="robot_clips"); c.add_argument("--out", default="ui/iso/gen")
    c.add_argument("--fps", type=int, default=15); c.add_argument("--max-seconds", type=float, default=0, help="長い clip（idle 15s 等）を先頭で切る"); c.add_argument("--fit-height", type=float, default=None); c.add_argument("--max-kb", type=float, default=160); c.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    mod = body(a) if a.cmd == "body" else clips(a)
    js = "// generated by tools/glb_to_geom.py — do not edit by hand (R96-D2 rig)\nexport default " + json.dumps(mod, separators=(",", ":")) + ";\n"
    kb = len(js.encode("utf-8")) / 1024
    if a.cmd == "body":
        print(f"{a.name}: tris={mod['tris']} verts={sum(p['n'] for p in mod['parts'])} joints={len(mod['skeleton']['joints'])} height={mod['height']} size={kb:.1f}KB", file=sys.stderr)
    else:
        for n, c in mod["clips"].items(): print(f"  clip {n}: {c['duration']}s frames={c['frames']} tracks={len(c['tracks'])}", file=sys.stderr)
        print(f"{a.name}: clips={len(mod['clips'])} size={kb:.1f}KB", file=sys.stderr)
    if kb > a.max_kb: sys.exit(f"サイズ {kb:.1f}KB > {a.max_kb}KB")
    if a.dry: return
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, a.name + ".js"), "w", encoding="utf-8") as f: f.write(js)

if __name__ == "__main__":
    main()
