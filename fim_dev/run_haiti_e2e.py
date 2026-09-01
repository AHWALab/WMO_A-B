"""End to end check of both Haiti sites with pluvial, fluvial and combined.

Builds a synthetic cycle with a KNOWN areal rain total over each site's area
of concern plus CREST discharge series carrying the EF5 warm up nans, runs
the shipped pipeline against the shipped store and the shipped site YAML, and
checks the match, the products and the combination rule.
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
RKEY = "haiti_90m"
SITES = [("Haiti_Gris", ["ts.cuenca_griss.crest.%s.csv" % CYCLE]),
         ("Haiti_LaQuinte", ["ts.cuenca_laquinta_1.crest.%s.csv" % CYCLE,
                             "ts.cuenca_laquinta_2.crest.%s.csv" % CYCLE])]

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


def series(path, peak):
    """CREST style series, with the EF5 warm up nans in front."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Time", "Discharge(m^3 s^-1)", "Observed(m^3 s^-1)", "Precip(mm h^-1)"])
        vals = ["nan", "nan"] + [round(peak * f, 2) for f in (0.2, 0.6, 1.0, 0.8, 0.3)]
        for i, v in enumerate(vals):
            w.writerow([f"2023-06-21 {i:02d}:00", v, "nan", 0.0])


def main():
    from tito_utils.fim_utils.pipeline_pf import load_pf_config, run_pf_cycle
    import zarr
    os.environ["TITO_FIM_ROOT"] = ROOT
    shutil.rmtree("outputs", ignore_errors=True)

    for region, gauges in SITES:
        yml = f"fim_config/{region}.yaml"
        cfg0 = load_pf_config(yml, root=ROOT)
        z = zarr.open_group(cfg0["store"], mode="r")
        mm = np.asarray(z["magnitude_mm"][:])
        fq = np.asarray(z["fluvial_q"][:])
        sid = [s.decode() if isinstance(s, bytes) else str(s) for s in z["storm_id"][:]]
        # aim at a PLACEHOLDER scenario when the store has them, so the test
        # proves the stand in maps flow through the pipeline too
        at = dict(z.attrs)
        ph = at.get("placeholder_scenarios", [])
        if ph:
            k = sid.index(sorted(ph)[len(ph) // 2])
            target = float(mm[k]) * 1.001
        else:
            target = float(np.median(mm))
        qprobe = [float(np.median(fq[:, i])) for i in range(fq.shape[1])]
        print(f"\n== {region}: {len(sid)} scenarios, rain {mm.min():.2f} to {mm.max():.1f} mm, "
              f"probing {target:.1f} mm and Q {[round(q) for q in qprobe]}")

        b = aoc_bounds(cfg0["aoc_geojson"])
        base = os.path.join("outputs", CYCLE, RKEY)
        shutil.rmtree(base, ignore_errors=True)
        grid(os.path.join(base, "imerg", f"qpeaccum.{CYCLE}.tif"), b, target * 0.4)
        grid(os.path.join(base, "gfs", f"qpeaccum.{CYCLE}.tif"), b, target * 0.6)
        grid(os.path.join(base, "gfs", f"maxunitq.{CYCLE}.tif"), b, 7.0)
        for g, q in zip(gauges, qprobe):
            series(os.path.join(base, "gfs", g), q)

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
        ck(f"{region}: both hazards enabled",
           s["hazards_enabled"] == {"pluvial": True, "fluvial": True},
           str(s.get("hazards_enabled")))
        ck(f"{region}: P, F and PF routines all ran",
           set(s.get("routines", {})) >= {"P", "F", "PF"}, str(sorted(s.get("routines", {}))))

        r = list(csv.DictReader(open(f"outputs/_test/{region}/member_decisions.csv")))[0]
        tot = float(r["total_mm"])
        ck(f"{region}: areal rain read back as the probe total",
           abs(tot - target) / target < 0.02, f"{tot:.1f} vs {target:.1f} mm")
        ck(f"{region}: pluvial matched", r.get("p_storm", "") != "",
           f"{r.get('p_storm')} rule {r.get('p_rule')}")
        if ph:
            ck(f"{region}: the matched scenario IS a placeholder (stand in map works)",
               r.get("p_storm") in ph, str(r.get("p_storm")))
        if r.get("p_storm"):
            mag = mm[sid.index(r["p_storm"])]
            ck(f"{region}: pluvial match inside the band",
               0.9 * tot <= mag <= 1.2 * tot, f"{mag:.1f} mm for {tot:.1f} mm")
        ck(f"{region}: fluvial matched on the warm up series (nan safe)",
           r.get("f_storm", "") not in ("", None) and "missing_discharge" not in str(r),
           f"{r.get('f_storm')} rule {r.get('f_rule')}")

        for mode in ("pluvial", "fluvial", "combined"):
            d = os.path.join(f"outputs/_test/{region}", mode)
            prods = sorted(os.listdir(d)) if os.path.isdir(d) else []
            ck(f"{region}: {mode} has four probability products",
               all(any(f"prob_depth_ge_{t}." in p for p in prods)
                   for t in ("10cm", "30cm", "70cm", "100cm")), f"{len(prods)} files")

        pp = f"outputs/_test/{region}/pluvial/prob_depth_ge_30cm.{CYCLE}.tif"
        fp = f"outputs/_test/{region}/fluvial/prob_depth_ge_30cm.{CYCLE}.tif"
        cp = f"outputs/_test/{region}/combined/prob_depth_ge_30cm.{CYCLE}.tif"
        with rasterio.open(pp) as a, rasterio.open(fp) as bb, rasterio.open(cp) as c:
            P, F, C = a.read(1), bb.read(1), c.read(1)
            ck(f"{region}: products carry the model grid and crs",
               str(c.crs) == z.attrs["crs"] and list(c.shape) == list(z.attrs["grid_shape"]),
               f"{c.crs} {c.shape}")
        ck(f"{region}: combined is the per pixel maximum of P and F",
           np.allclose(C, np.maximum(P, F), atol=1e-6),
           f"maxdiff {float(np.abs(C - np.maximum(P, F)).max()):.4f}")
        ck(f"{region}: pluvial product is not empty", float(P.max()) > 0, f"max {float(P.max()):.2f}")
        ck(f"{region}: fluvial product is not empty", float(F.max()) > 0, f"max {float(F.max()):.2f}")
        ck(f"{region}: combined wet area at least the pluvial wet area",
           int((C > 0).sum()) >= int((P > 0).sum()),
           f"P {(P > 0).sum():,} F {(F > 0).sum():,} C {(C > 0).sum():,} px")
        ck(f"{region}: overbank products written",
           os.path.isdir(f"outputs/_test/{region}/combined_overbank"))

    print()
    bad = [n for n, ok in CHECKS if not ok]
    print(f"== {len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed ==")
    for n in bad:
        print("   FAILED:", n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
