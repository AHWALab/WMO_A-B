"""Pull ONLY MaximumDepth.tif out of every sample, quantize to cm, deflate,
append to blobs. Resumable: rerun until it prints DONE."""
import hashlib, json, os, sys, time, zlib
import numpy as np
import zr, tif

B = os.path.expanduser("~/mnt/FIM_version/Data/_haiti_build")
BUDGET = float(os.environ.get("BUDGET", "36"))
BLOB_MAX = 340 * 1024 * 1024

site = sys.argv[1]
key = site.lower()
STATE = os.path.join(B, "state_%s.json" % key)
LOCK = os.path.join(B, "lock_%s" % key)

# the mount forbids deletes, so the lock is released by truncating it
now = time.time()
if os.path.exists(LOCK) and os.path.getsize(LOCK) > 0 and now - os.path.getmtime(LOCK) < 90:
    print("LOCKED, another writer is running"); sys.exit(0)
open(LOCK, "w").write("%d %d" % (os.getpid(), int(now)))

try:
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"done": {}, "blob": 0, "pos": 0}
    _, cd = zr.outer(site)
    samples = sorted(e["n"].split("/")[1] for e in cd if e["n"].endswith("MaxVeloc-dept.zip"))
    todo = [s for s in samples if s not in st["done"]]
    print("%s: %d of %d remaining" % (site, len(todo), len(samples)), flush=True)
    t0 = time.time()
    for smp in todo:
        if time.time() - t0 > BUDGET:
            print("budget reached, rerun to continue", flush=True); break
        s, z = zr.nested(site, smp)
        try:
            buf = z.read("MaximumDepth.tif")
        finally:
            s.close()
        a, d = tif.read_float(buf)
        del buf
        nod = d.get("nodata")
        nod = float(nod[0]) if isinstance(nod, (list, tuple)) else float(nod)
        bad = ~np.isfinite(a) | (a < nod / 2)
        v = np.where(bad, 0.0, a).astype("float32")
        np.clip(v, 0, None, out=v)
        del a
        q = np.round(v * 100.0)
        np.clip(q, 0, 65535, out=q)
        q = q.astype("uint16")
        wet = q >= 5
        rows = np.nonzero(wet.any(1))[0]
        cols = np.nonzero(wet.any(0))[0]
        bbox = [int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())] if len(rows) else None
        raw = q.tobytes()
        comp = zlib.compress(raw, 6)
        blob = os.path.join(B, "b_%s_%02d.blob" % (key, st["blob"]))
        if st["pos"] + len(comp) > BLOB_MAX and st["pos"] > 0:
            st["blob"] += 1; st["pos"] = 0
            blob = os.path.join(B, "b_%s_%02d.blob" % (key, st["blob"]))
        with open(blob, "ab") as fh:
            fh.seek(0, 2)
            start = fh.tell()
            if start != st["pos"]:
                # a killed run left a partial record; the mount forbids deletes
                # so that tail is simply abandoned and we append past it
                print("  note: %d dead bytes from an interrupted run" % (start - st["pos"]), flush=True)
            fh.write(comp)
        st["done"][smp] = {"blob": os.path.basename(blob), "start": start, "csize": len(comp),
                           "H": int(q.shape[0]), "W": int(q.shape[1]),
                           "md5": hashlib.md5(raw).hexdigest(),
                           "max_cm": int(q.max()), "wet_px": int(wet.sum()),
                           "nodata_pct": round(float(bad.mean()) * 100, 3), "bbox": bbox,
                           "pixel_scale": d.get("pixel_scale"), "tiepoint": d.get("tiepoint"),
                           "geo_ascii": d.get("geo_ascii", "")}
        st["pos"] = start + len(comp)
        json.dump(st, open(STATE + ".tmp", "w")); os.replace(STATE + ".tmp", STATE)
        print("  %s %dx%d wet %d max %.2fm -> %.2f MB" % (
            smp, q.shape[1], q.shape[0], int(wet.sum()), q.max() / 100.0, len(comp) / 1e6), flush=True)
        del q, v, wet, raw, comp
    left = [s for s in samples if s not in st["done"]]
    print("DONE" if not left else "REMAINING %d" % len(left), flush=True)
finally:
    open(LOCK, "w").write("")
