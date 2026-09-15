"""合成スキン GLB（2 骨・4 頂点・1 clip）で glb_skin の読みを固定する（S2 の単体テストの原型）。"""
import json, struct, sys, os, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "tools")); import glb_skin as g

def synth():
    pos = [(0,0,0),(1,0,0),(1,1,0),(0,1,0)]; nrm = [(0,0,1)]*4; idx = [0,1,2,0,2,3]
    joints = [(0,1,0,0)]*4; weights = [(0.75,0.25,0,0)]*2 + [(0.25,0.75,0,0)]*2
    ibm = [[1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1], [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,-1,0,1]]   # bone1 は y=1 に居る
    times = [0.0, 0.5, 1.0]; rot = [(0,0,0,1),(0,0,0.7071068,0.7071068),(0,0,1,0)]     # z 軸回転 0→90→180°
    cubic = []  # CUBICSPLINE: in-tangent, value, out-tangent
    for r in rot: cubic += [(0,0,0,0), r, (0,0,0,0)]
    trans = [(0,0,0),(0,0.1,0),(0,0.2,0)]
    buf = bytearray(); views = []; accs = []
    def push(vals, fmt, n, ctype, typ, extra=None):
        off = len(buf)
        for v in vals: buf.extend(struct.pack("<"+fmt*n, *v) if n > 1 else struct.pack("<"+fmt, v))
        while len(buf) % 4: buf.append(0)
        views.append({"buffer":0,"byteOffset":off,"byteLength":len(buf)-off}); acc = {"bufferView":len(views)-1,"componentType":ctype,"count":len(vals),"type":typ}
        if extra: acc.update(extra)
        accs.append(acc); return len(accs)-1
    a_pos = push(pos,"f",3,5126,"VEC3",{"min":[0,0,0],"max":[1,1,0]}); a_nrm = push(nrm,"f",3,5126,"VEC3"); a_idx = push(idx,"H",1,5123,"SCALAR")
    a_j = push(joints,"B",4,5121,"VEC4"); a_w = push(weights,"f",4,5126,"VEC4"); a_ibm = push(ibm,"f",16,5126,"MAT4")
    a_t = push(times,"f",1,5126,"SCALAR"); a_rot = push(cubic,"f",4,5126,"VEC4"); a_tr = push(trans,"f",3,5126,"VEC3")
    gltf = {"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[0,2]}],
        "nodes":[{"name":"Root","children":[1],"translation":[0,0,0]},{"name":"Bone1","translation":[0,1,0]},{"name":"Body","mesh":0,"skin":0}],
        "meshes":[{"primitives":[{"attributes":{"POSITION":a_pos,"NORMAL":a_nrm,"JOINTS_0":a_j,"WEIGHTS_0":a_w},"indices":a_idx}]}],
        "skins":[{"joints":[0,1],"inverseBindMatrices":a_ibm,"skeleton":0}],
        "animations":[{"name":"wave","channels":[{"sampler":0,"target":{"node":1,"path":"rotation"}},{"sampler":1,"target":{"node":0,"path":"translation"}}],
                       "samplers":[{"input":a_t,"output":a_rot,"interpolation":"CUBICSPLINE"},{"input":a_t,"output":a_tr,"interpolation":"LINEAR"}]}],
        "accessors":accs,"bufferViews":views,"buffers":[{"byteLength":len(buf)}]}
    js = json.dumps(gltf).encode(); js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s",len(js),b"JSON")+js+struct.pack("<I4s",len(buf),b"BIN\0")+bytes(buf)
    return struct.pack("<4sII",b"glTF",2,12+len(body))+body

class T(unittest.TestCase):
    def test_skin_prims_clips(self):
        gltf, bin_chunk = g.read_glb(synth())
        sk = g.read_skin(gltf, bin_chunk)
        self.assertEqual(sk["joints"], [0,1]); self.assertEqual(sk["nodes"][1]["parent"], 0); self.assertEqual(sk["root"], 0)
        self.assertAlmostEqual(sk["ibm"][1][13], -1.0)
        prims = g.skinned_primitives(gltf, bin_chunk)
        self.assertEqual(len(prims), 1); p = prims[0]
        self.assertEqual(p["joints"][0], (0,1,0,0)); self.assertAlmostEqual(sum(p["weights"][0]), 1.0)
        clips = g.read_clips(gltf, bin_chunk, fps=10, joints=set(sk["joints"]), root_joint=0)
        self.assertEqual(len(clips), 1); c = clips[0]
        self.assertEqual(c["frames"], 11); self.assertAlmostEqual(c["duration"], 1.0)
        r = c["tracks"][1]["r"]; self.assertEqual(len(r), 11)
        # t=0.5 で 90°（CUBICSPLINE の値列だけを線形補間）・t=1.0 で 180°
        self.assertAlmostEqual(r[5][2], 0.7071068, places=3); self.assertAlmostEqual(abs(r[10][2]), 1.0, places=3)
        t = c["tracks"][0]["t"]; self.assertAlmostEqual(t[10][1], 0.2, places=5)
        self.assertNotIn("t", c["tracks"][1])   # root 以外の translation は落とす

if __name__ == "__main__": unittest.main()
