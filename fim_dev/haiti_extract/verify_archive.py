"""Decode every archive GeoTIFF and compare md5 against the extraction state."""
import hashlib, json, os, struct, sys, time, zlib
import numpy as np
sys.path.insert(0, os.path.expanduser("~/mnt/FIM_version/Data/_haiti_build"))
import tif

B = os.path.expanduser("~/mnt/FIM_version/Data/_haiti_build")
OUT = os.path.expanduser("~/mnt/FIM_version/Data/Haitii/max_depth_only")
BUDGET = float(os.environ.get("BUDGET", "36"))
SITE = sys.argv[1]
st = json.load(open(os.path.join(B, "state_%s.json" % SITE.lower())))["done"]
ck_path = os.path.join(B, "verify_archive_%s.json" % SITE.lower())
done = json.load(open(ck_path)) if os.path.exists(ck_path) else {}
t0 = time.time()
bad = []
for smp in sorted(st):
    if smp in done:
        continue
    if time.time() - t0 > BUDGET:
        print("budget reached (%d verified so far), rerun" % len(done), flush=True)
        json.dump(done, open(ck_path, "w")); sys.exit(0)
    p = os.path.join(OUT, SITE, "%s_MaximumDepth.tif" % smp)
    buf = open(p, "rb").read()
    bo, big, t = tif.tags(buf)
    H, W = t[257][0], t[256][0]
    rps = t[278][0]
    rows = []
    for i, (o, c) in enumerate(zip(t[273], t[279])):
        raw = zlib.decompress(buf[o:o + c])
        nr = min(rps, H - i * rps)
        d = np.frombuffer(raw, dtype="<u2").reshape(nr, W)
        a = np.cumsum(d.astype("<u8"), axis=1, dtype="uint64").astype("<u2")  # undo predictor
        rows.append(a)
    a = np.vstack(rows)
    md5 = hashlib.md5(a.astype("<u2").tobytes()).hexdigest()
    ok = (md5 == st[smp]["md5"] and (H, W) == (st[smp]["H"], st[smp]["W"]))
    done[smp] = bool(ok)
    if not ok:
        bad.append(smp)
json.dump(done, open(ck_path, "w"))
n_ok = sum(1 for v in done.values() if v)
print("DONE %s: %d of %d verified pixel exact, %d BAD %s" % (
    SITE, n_ok, len(st), len(st) - n_ok,
    [k for k, v in done.items() if not v] or ""), flush=True)
