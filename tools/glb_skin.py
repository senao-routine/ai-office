#!/usr/bin/env python3
"""GLB のスキン（JOINTS_0/WEIGHTS_0・skins・ノード階層）とアニメーション（channels/samplers）を stdlib だけで読む（D2 S2 の土台）。
tools/glb_to_geom.py へ統合する前の独立モジュール。出力は kit/rigbot が読む単純な dict:
  skin = {"joints":[node index…], "ibm":[16 floats…]*J | None, "nodes":[{"name","parent","t":[3],"r":[4],"s":[3]}…], "root": node index}
  clips = [{"name", "duration", "fps", "tracks": {joint_i: {"r": [[t, qx,qy,qz,qw]…], "t": [[t, x,y,z]…]}}}]
CUBICSPLINE/STEP は LINEAR へ再サンプル（既定 15fps）。"""
import json, math, struct, sys

CT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

def read_glb(data):
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF" and version == 2, "not GLB v2"
    off = 12; gltf = None; bin_chunk = b""
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", data, off); off += 8
        chunk = data[off:off + clen]; off += clen
        if ctype == b"JSON": gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == b"BIN\x00": bin_chunk = chunk
    return gltf, bin_chunk

def accessor(gltf, bin_chunk, idx):
    acc = gltf["accessors"][idx]; bv = gltf["bufferViews"][acc["bufferView"]]
    fmt, size = CT[acc["componentType"]]; n = NCOMP[acc["type"]]
    base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0); stride = bv.get("byteStride", size * n)
    out = []
    for i in range(acc["count"]):
        vals = struct.unpack_from("<" + fmt * n, bin_chunk, base + i * stride)
        if acc.get("normalized"):
            mx = {"b": 127.0, "B": 255.0, "h": 32767.0, "H": 65535.0}[fmt]
            vals = tuple(max(-1.0, v / mx) for v in vals)
        out.append(vals if n > 1 else vals[0])
    return out

def read_skin(gltf, bin_chunk, skin_index=0):
    """skins[i] と、その joints が属するノード階層（root からの TRS）。"""
    skin = gltf["skins"][skin_index]; joints = list(skin["joints"])
    ibm = [list(m) for m in accessor(gltf, bin_chunk, skin["inverseBindMatrices"])] if "inverseBindMatrices" in skin else None
    nodes = []
    parent = {}
    for i, n in enumerate(gltf["nodes"]):
        for c in n.get("children", []): parent[c] = i
    for i, n in enumerate(gltf["nodes"]):
        if "matrix" in n:
            m = n["matrix"]; t = [m[12], m[13], m[14]]
            # 回転・スケールは列ベクトルから（一様スケール前提の近似・Tripo/Mixamo は TRS で出す）
            sx = math.sqrt(m[0]**2 + m[1]**2 + m[2]**2) or 1; sy = math.sqrt(m[4]**2 + m[5]**2 + m[6]**2) or 1; sz = math.sqrt(m[8]**2 + m[9]**2 + m[10]**2) or 1
            r = mat3_to_quat([m[0]/sx, m[1]/sx, m[2]/sx, m[4]/sy, m[5]/sy, m[6]/sy, m[8]/sz, m[9]/sz, m[10]/sz]); s = [sx, sy, sz]
        else:
            t = list(n.get("translation", [0, 0, 0])); r = list(n.get("rotation", [0, 0, 0, 1])); s = list(n.get("scale", [1, 1, 1]))
        nodes.append({"name": n.get("name", f"node{i}"), "parent": parent.get(i, -1), "t": t, "r": r, "s": s, "mesh": n.get("mesh")})
    root = skin.get("skeleton")
    if root is None:
        root = joints[0]
        while nodes[root]["parent"] >= 0 and nodes[root]["parent"] in joints: root = nodes[root]["parent"]
    return {"joints": joints, "ibm": ibm, "nodes": nodes, "root": root}

def mat3_to_quat(m):
    # column-major 3x3 (m[0..2] = col0)
    m00, m10, m20, m01, m11, m21, m02, m12, m22 = m
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2; return [(m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s]
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2; return [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s]
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2; return [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s]
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2; return [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s]

def skinned_primitives(gltf, bin_chunk):
    """スキン付き primitive を列挙: pos/nrm/uv/idx に加え joints(u8×4)/weights(float×4・和 1 に正規化)・skin index。ノード変換は焼かない。"""
    out = []
    for ni, node in enumerate(gltf["nodes"]):
        if "mesh" not in node or "skin" not in node: continue
        mesh = gltf["meshes"][node["mesh"]]
        for pi, prim in enumerate(mesh.get("primitives", [])):
            a = prim["attributes"]
            if "JOINTS_0" not in a or "WEIGHTS_0" not in a: continue
            pos = accessor(gltf, bin_chunk, a["POSITION"]); nrm = accessor(gltf, bin_chunk, a["NORMAL"]) if "NORMAL" in a else None
            uv = accessor(gltf, bin_chunk, a["TEXCOORD_0"]) if "TEXCOORD_0" in a else None
            joints = [tuple(int(v) for v in j) for j in accessor(gltf, bin_chunk, a["JOINTS_0"])]
            weights = []
            for w in accessor(gltf, bin_chunk, a["WEIGHTS_0"]):
                s = sum(w) or 1.0; weights.append(tuple(v / s for v in w))
            idx = accessor(gltf, bin_chunk, prim["indices"]) if "indices" in prim else list(range(len(pos)))
            out.append({"name": node.get("name") or mesh.get("name") or f"mesh{node['mesh']}", "pos": pos, "nrm": nrm, "uv": uv, "idx": idx,
                        "joints": joints, "weights": weights, "skin": node["skin"], "material": prim.get("material")})
    return out

def _slerp(a, b, t):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0: b = [-x for x in b]; dot = -dot
    if dot > 0.9995:
        r = [x + (y - x) * t for x, y in zip(a, b)]
    else:
        th = math.acos(max(-1.0, min(1.0, dot))); s = math.sin(th)
        r = [(math.sin((1 - t) * th) * x + math.sin(t * th) * y) / s for x, y in zip(a, b)]
    n = math.sqrt(sum(x * x for x in r)) or 1.0
    return [x / n for x in r]

def _sample(times, values, t, kind, interp):
    """時刻 t の値。interp: LINEAR/STEP/CUBICSPLINE（CUBICSPLINE は値の並びが in/val/out なので val だけを線形で使う＝再サンプルの近似）。"""
    if not times: return None
    if t <= times[0]: i0 = i1 = 0
    elif t >= times[-1]: i0 = i1 = len(times) - 1
    else:
        lo, hi = 0, len(times) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if times[mid] <= t: lo = mid
            else: hi = mid
        i0, i1 = lo, hi
    def val(i):
        return list(values[i * 3 + 1]) if interp == "CUBICSPLINE" else list(values[i])
    v0, v1 = val(i0), val(i1)
    if i0 == i1 or interp == "STEP": return v0
    f = (t - times[i0]) / (times[i1] - times[i0])
    return _slerp(v0, v1, f) if kind == "rotation" else [x + (y - x) * f for x, y in zip(v0, v1)]

def read_clips(gltf, bin_chunk, fps=15, joints=None, root_joint=None):
    """animations[] を LINEAR の等間隔サンプルへ。rotation は全対象骨、translation は動く骨だけ、scale は落とす。
    定数トラックは落とすが、**rest と違う定数**（in_place の retarget は Hip の高さを Root の定数 translation へ移す・実測）は r0/t0 として 1 個だけ残す。"""
    clips = []
    rest = {i: (list(n.get("translation", [0, 0, 0])), list(n.get("rotation", [0, 0, 0, 1]))) for i, n in enumerate(gltf["nodes"])}
    for anim in gltf.get("animations", []):
        samplers = anim["samplers"]; chans = anim["channels"]
        tracks = {}; duration = 0.0
        for ch in chans:
            node = ch["target"]["node"]; path = ch["target"]["path"]
            if joints is not None and node not in joints: continue
            if path == "scale" or path == "weights": continue
            # translation は動く骨（Root/Hip）だけ残す＝定数なら後段で落ちる。root_joint は互換のため受けるが絞らない
            smp = samplers[ch["sampler"]]
            times = accessor(gltf, bin_chunk, smp["input"]); values = accessor(gltf, bin_chunk, smp["output"])
            interp = smp.get("interpolation", "LINEAR")
            duration = max(duration, times[-1] if times else 0.0)
            tracks.setdefault(node, {})[path] = (times, values, interp)
        n = max(1, int(round(duration * fps))) + 1
        out_tracks = {}
        for node, paths in tracks.items():
            rec = {}
            for path, (times, values, interp) in paths.items():
                kind = "rotation" if path == "rotation" else "translation"
                samples = [_sample(times, values, k / fps, kind, interp) for k in range(n)]
                # 定数トラックは落とす（全フレーム同値）。rest と違う定数だけ r0/t0 として残す
                if all(all(abs(a - b) < 1e-5 for a, b in zip(samples[0], s)) for s in samples[1:]):
                    rest_v = rest[node][1] if path == "rotation" else rest[node][0]
                    if any(abs(a - b) > 1e-4 for a, b in zip(samples[0], rest_v)):
                        rec["r0" if path == "rotation" else "t0"] = samples[0]
                    continue
                rec["r" if path == "rotation" else "t"] = samples
            if rec: out_tracks[node] = rec
        clips.append({"name": anim.get("name", f"clip{len(clips)}"), "duration": round(duration, 4), "fps": fps, "frames": n, "tracks": out_tracks})
    return clips

if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read(); gltf, bin_chunk = read_glb(data)
    print("skins", len(gltf.get("skins", [])), "animations", [(a.get("name"), len(a["channels"])) for a in gltf.get("animations", [])])
    if gltf.get("skins"):
        sk = read_skin(gltf, bin_chunk)
        print("joints", len(sk["joints"]), "ibm", bool(sk["ibm"]), "root", sk["nodes"][sk["root"]]["name"])
        names = [sk["nodes"][j]["name"] for j in sk["joints"]]; print("joint names", names[:80])
        prims = skinned_primitives(gltf, bin_chunk); print("skinned prims", [(p["name"], len(p["pos"]), len(p["idx"]) // 3) for p in prims])
        clips = read_clips(gltf, bin_chunk, fps=15, joints=set(sk["joints"]), root_joint=sk["root"])
        for c in clips: print("clip", c["name"], "dur", c["duration"], "frames", c["frames"], "tracks", len(c["tracks"]), "with t:", sum('t' in v for v in c["tracks"].values()))
