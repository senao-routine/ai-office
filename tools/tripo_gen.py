#!/usr/bin/env python3
"""Tripo API v3 で低ポリの什器を生成して GLB を取る（R96-D0・stdlib のみ・`tools/` 限定）。

  python3 tools/tripo_gen.py --name desk --prompt "..." [--model P1-20260311] [--faces 4000] [--seed 11]
      [--no-texture] [--parts] [--quad] [--smart-low-poly] [--out tools/tripo_out] [--ledger docs/tripo-ledger.md]

- 既定は texture あり（P1・40cr）＝ glb_to_geom.py --bake-color がベースカラーを頂点色へ焼く。
  単色で塗る小物だけ --no-texture（20cr）。P1 は smart_low_poly / generate_parts を受け付けない（v3.1 のみ）。
- 出力 URL は 5 分で失効するので成功したら即ダウンロードする。
- 鍵は env `TRIPO_API_KEY`（無ければ ~/Downloads/works/.env の 1 行を読む・値は表示しない）。
"""
import argparse, json, os, sys, time, urllib.request, urllib.error, re, datetime

BASE = "https://openapi.tripo3d.ai/v3"

def api_key():
    k = os.environ.get("TRIPO_API_KEY")
    if k: return k
    env = os.path.expanduser("~/Downloads/works/.env")
    try:
        for line in open(env, encoding="utf-8"):
            if line.startswith("TRIPO_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise SystemExit("TRIPO_API_KEY が無い（env か ~/Downloads/works/.env）")

def call(method, path, body=None, key=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "aioffice-tripo-gen/1")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data.decode("utf-8"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True); ap.add_argument("--prompt", required=True)
    ap.add_argument("--negative", default="text, logo, watermark, people, floor, background, base plate")
    ap.add_argument("--model", default="P1-20260311", help="P1（品質・smart_low_poly 非対応）か v3.1-20260211"); ap.add_argument("--faces", type=int, default=4000)
    ap.add_argument("--parts", action="store_true"); ap.add_argument("--quad", action="store_true")
    ap.add_argument("--smart-low-poly", action="store_true"); ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--no-texture", action="store_true", help="テクスチャ無し（-20cr・色を焼けない＝単色の材質で塗る物だけ）")
    ap.add_argument("--out", default="tools/tripo_out"); ap.add_argument("--ledger", default="docs/tripo-ledger.md")
    ap.add_argument("--poll", type=float, default=3.0); ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args()
    if not re.match(r"^[a-z][a-z0-9_]{1,40}$", a.name): raise SystemExit("--name は小文字の識別子")
    key = api_key()
    body = {"prompt": a.prompt, "negative_prompt": a.negative, "model_version": a.model, "face_limit": a.faces,
            "texture": not a.no_texture, "pbr": not a.no_texture, "model_seed": a.seed, "quad": bool(a.quad),
            "auto_size": True, "export_uv": True}
    if a.parts: body["generate_parts"] = True
    if a.smart_low_poly: body["smart_low_poly"] = True
    t0 = time.time()
    try:
        res = call("POST", "/generation/text-to-model", body, key)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"POST failed {e.code}: {e.read()[:300]!r}")
    task_id = (res.get("data") or res).get("task_id") or (res.get("data") or {}).get("id")
    if not task_id: raise SystemExit(f"task_id が無い: {json.dumps(res)[:300]}")
    print(f"task {task_id} submitted ({a.model} faces={a.faces} parts={a.parts} texture={not a.no_texture})", file=sys.stderr)
    status = None; task = None
    while time.time() - t0 < a.timeout:
        time.sleep(a.poll)
        task = call("GET", f"/tasks/{task_id}", None, key)
        d = task.get("data") or task
        status = d.get("status")
        if status in ("success", "failed", "cancelled", "banned", "expired", "unknown"):
            break
    if status != "success":
        raise SystemExit(f"task {task_id} status={status}: {json.dumps(task)[:400]}")
    d = task.get("data") or task
    out = d.get("output") or {}
    url = out.get("model") or out.get("model_url") or out.get("pbr_model") or out.get("base_model")
    if not url: raise SystemExit(f"model url が無い: {json.dumps(out)[:400]}")
    odir = os.path.join(a.out, a.name); os.makedirs(odir, exist_ok=True)
    glb = os.path.join(odir, "model.glb")
    req = urllib.request.Request(url, headers={"User-Agent": "aioffice-tripo-gen/1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(glb, "wb") as f:
        f.write(r.read())
    json.dump({"task_id": task_id, "request": body, "task": d, "elapsed_sec": round(time.time() - t0, 1),
               "downloaded": datetime.datetime.now().isoformat(timespec="seconds")},
              open(os.path.join(odir, "task.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k in ("rendered_image", "generated_image"):
        u = out.get(k) or out.get(k + "_url")
        if u:
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "aioffice-tripo-gen/1"}), timeout=60) as r:
                    open(os.path.join(odir, k + os.path.splitext(u.split("?")[0])[1]), "wb").write(r.read())
            except Exception:
                pass
    credits = d.get("credits_consumed") or d.get("credits")
    size = os.path.getsize(glb)
    print(f"ok {a.name}: {size//1024}KB credits={credits} {round(time.time()-t0)}s → {glb}", file=sys.stderr)
    try:
        with open(a.ledger, "a", encoding="utf-8") as f:
            f.write(f"| {datetime.date.today()} | {a.name} | {a.model} | faces {a.faces}{' parts' if a.parts else ''}{' quad' if a.quad else ''}{' lowpoly' if a.smart_low_poly else ''} | seed {a.seed} | {credits} | {size//1024}KB | {task_id} | {a.prompt[:80]} |\n")
    except OSError:
        pass

if __name__ == "__main__":
    main()
