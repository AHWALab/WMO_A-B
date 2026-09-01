"""End to end check of three Comoros sites, one per island.

Builds a synthetic cycle with a KNOWN areal rain total over each unit's
area of concern, runs the shipped pipeline against the shipped store and
the shipped site YAML, and checks that the matched scenario, the products
and the overbank mask all behave.
"""
import csv
import json
import os
import shutil
import sys

import numpy as np
import rasterio
from rasterio.transform import from_origin

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

CYCLE = "20230621.070000"
RKEY = "comoros_90m"
SITES = [("Comoros_Fomboni", "KM321", "Moheli"),
         ("Comoros_Moroni", "KM274", "Grande Comore"),
         ("Comoros_Mutsamudu", "KM134", "Anjouan")]

CHECKS = []


def ck(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))


def aoc_bounds(p):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1]); return
        for e in c:
            walk(e)
    for f in json.load(open(p))["features"]:
        walk(f["geometry"]["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def grid(path, b, value, res=0.002, pad=0.05):
    minx, miny, maxx, maxy = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
    w = max(20, int((maxx - minx) / res)); h = max(20, int((maxy - miny) / res))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(minx, maxy, res, res), nodata=-9999.0) as d:
        d.write(np.full((h, w), value, dtype="float32"), 1)


def main():
    from tito_utils.fim_utils.pipeline_pf import load_pf_config, run_pf_cycle
    os.environ["TITO_FIM_ROOT"] = ROOT
    shutil.rmtree("outputs", ignore_errors=True)

    for region, pcode, island in SITES:
        yml = f"fim_config/{region}.yaml"
        cfg0 = load_pf_config(yml, root=ROOT)
        store_dir = cfg0["store"]
        import zarr
        z = zarr.open_group(store_dir, mode="r")
        mm = np.asarray(z["magnitude_mm"][:])
        sid = [s.decode() if isinstance(s, bytes) else str(s) for s in z["storm_id"][:]]
        # aim at the middle of the library so the match must land in the band
        target = float(np.median(mm))
        print(f"\n== {region} ({pcode}, {island}): "
              f"library {mm.min():.1f} to {mm.max():.1f} mm, probing {target:.1f} mm")

        b = aoc_bounds(cfg0["aoc_geojson"])
        base = os.path.join("outputs", CYCLE, RKEY)
        shutil.rmtree(base, ignore_errors=True)
        grid(os.path.join(base, "imerg", f"qpeaccum.{CYCLE}.tif"), b, target * 0.4)
        grid(os.path.join(base, "gfs", f"qpeaccum.{CYCLE}.tif"), b, target * 0.6)
        # the trigger reads EF5 unit discharge; without an EF5 domain for
        # Comoros the site stays quiet, which is the correct behaviour
        grid(os.path.join(base, "gfs", f"maxunitq.{CYCLE}.tif"), b, 7.0)

        cfg = load_pf_config(yml, root=ROOT)
        cfg["products_root"] = f"outputs/_test/{region}"
        cfg["append_cycle"] = False
        cfg["member"]["template"] = "{cycle}/" + RKEY + "/gfs"
        cfg["rain_components"] = [
            {"name": "imerg", "template": "{cycle}/" + RKEY + "/imerg",
             "grid": "qpe_accum", "required": True},
            {"name": "gfs", "template": "{cycle}/" + RKEY + "/gfs",
             "grid": "qpe_accum", "required": True}]
        cfg["trigger"]["sources"] = [{"template": "{cycle}/" + RKEY + "/gfs"}]
        s = run_pf_cycle(cfg, cycle=CYCLE, verbose=False)

        ck(f"{region}: triggered", s.get("status") == "triggered", str(s.get("status")))
        ck(f"{region}: pluvial only", s["hazards_enabled"] == {"pluvial": True, "fluvial": False},
           str(s.get("hazards_enabled")))
        rows = list(csv.DictReader(open(f"outputs/_test/{region}/member_decisions.csv")))
        r = rows[0]
        tot = float(r["total_mm"])
        ck(f"{region}: areal rain read back as the probe total",
           abs(tot - target) / target < 0.02, f"{tot:.1f} vs {target:.1f} mm")
        st = r.get("p_storm", "")
        ck(f"{region}: a scenario was matched", st != "", f"{st} rule {r.get('p_rule')}")
        if st:
            mag = mm[sid.index(st)]
            ck(f"{region}: match inside the 0.9 to 1.2 band",
               0.9 * tot <= mag <= 1.2 * tot, f"{st} at {mag:.1f} mm for {tot:.1f} mm")
        d = "outputs/_test/%s/pluvial" % region
        prods = sorted(os.listdir(d)) if os.path.isdir(d) else []
        ck(f"{region}: four probability products",
           all(any(f"prob_depth_ge_{t}." in p for p in prods)
               for t in ("10cm", "30cm", "70cm", "100cm")), f"{len(prods)} files")
        ck(f"{region}: overbank products written",
           os.path.isdir("outputs/_test/%s/pluvial_overbank" % region))

        with rasterio.open(os.path.join(d, f"prob_depth_ge_10cm.{CYCLE}.tif")) as ds:
            P = ds.read(1)
            ck(f"{region}: products carry the island model grid and crs",
               str(ds.crs) == z.attrs["crs"] and list(ds.shape) == list(z.attrs["grid_shape"]),
               f"{ds.crs} {ds.shape}")
        ck(f"{region}: the flood map is not empty", float(P.max()) > 0,
           f"max prob {float(P.max()):.2f}, wet px {(P > 0).sum():,}")
        ob = os.path.join("outputs/_test/%s/pluvial_overbank" % region,
                          f"prob_depth_ge_10cm_overbank.{CYCLE}.tif")
        with rasterio.open(ob) as ds:
            O = ds.read(1)
        ck(f"{region}: overbank mask removes at most the baseline water",
           int((P > 0).sum()) - int((O > 0).sum()) >= 0
           and (int((P > 0).sum()) - int((O > 0).sum())) <= 0.10 * max(1, int((P > 0).sum())),
           f"{(P > 0).sum() - (O > 0).sum():,} px removed of {(P > 0).sum():,}")

    print()
    bad = [n for n, ok in CHECKS if not ok]
    print(f"== {len(CHECKS)-len(bad)}/{len(CHECKS)} checks passed ==")
    for n in bad:
        print("   FAILED:", n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
