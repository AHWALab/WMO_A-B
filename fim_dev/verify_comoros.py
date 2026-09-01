"""Independent verification of the 55 Comoros municipality stores.

Nothing here reuses the builder's own objects: every check goes back to the
sources (the depth blob, the reference grids, the shapefile, the magnitude
json) and compares against what is inside the shipped zips.
"""
# NOTE ON PATHS: the constants below are the paths of the machine this was
# built on (the delivered Comoros rasters, the com_admin3 shapefile and the
# magnitude table). Point them at your own copies before rerunning. Needs
# numpy, rasterio, geopandas and zarr.
import csv
import io
import json
import os
import random
import zipfile
import zlib

import numpy as np
import rasterio
from rasterio.windows import Window
import geopandas as gpd
import zarr

BLOB_DIR = "/mnt/user-data/uploads/FIM_version/Data/_comoros_build/blobs"
INDEX = "/mnt/user-data/uploads/FIM_version/Data/_comoros_build/index_final.json"
REF = "/mnt/user-data/uploads/FIM_version/Data/Comoros/_ref"
SHP = "/mnt/user-data/uploads/FIM_version/Data/Comoros/com_admin_boundaries.shp/com_admin3.shp"
MAGS = "/home/claude/com/magnitudes_Comoros_from_rain.json"
OUT = "/home/claude/com/out"
BUF = 10
EXTENT_M = 0.05
ISLAND_OF = {"KM2": "grande", "KM1": "anjouan", "KM3": "moheli"}

FAIL = []
COVER = {}
OBWET = {}
# Mlédjélé reaches the Nioumachoua islets, which are outside the Mohéli
# hydrodynamic model domain; the mainland part of the commune is fully covered.
KNOWN_PARTIAL = {"KM331"}
random.seed(20260823)


def ck(name, ok, detail=""):
    if not ok:
        FAIL.append(name)
    if not ok or os.environ.get("V"):
        print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))


def blob_scene(entry):
    with open(os.path.join(BLOB_DIR, entry["blob"]), "rb") as f:
        f.seek(entry["start"])
        raw = zlib.decompress(f.read(entry["csize"]))
    return np.frombuffer(raw, dtype="<u2").reshape(entry["H"], entry["W"])


def main():
    index = json.load(open(INDEX))
    by_isl = {}
    for e in index:
        by_isl.setdefault(e["island"], {})[e["sample"]] = e
    mags_all = json.load(open(MAGS))["magnitudes"]
    adm = gpd.read_file(SHP)
    rows = list(csv.DictReader(open(f"{OUT}/manifest_Comoros.csv")))
    per_unit = json.load(open(f"{OUT}/magnitudes_Comoros_per_unit.json"))
    print(f"verifying {len(rows)} stores\n")

    grids = {}
    for isl in ISLAND_OF.values():
        with rasterio.open(f"{REF}/ref_{isl}.tif") as src:
            grids[isl] = (src.crs, src.transform, src.width, src.height)

    deep = set(random.sample([r["pcode"] for r in rows], 9))
    n_pixel_checks = 0

    for r in rows:
        pcode, isl = r["pcode"], r["island_model"]
        tag = f"{pcode} {r['name']}"
        zp = f"{OUT}/fim_store/Comoros/{r['store']}.zip"
        crs, T, GW, GH = grids[isl]

        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            root = zarr.open_group(zarr.storage.ZipStore(zp, mode="r"), mode="r")
            depth = root["depth"]
            extent = root["extent"]
            mm = np.asarray(root["magnitude_mm"][:], dtype="float64")
            sid = [s.decode() if isinstance(s, bytes) else str(s)
                   for s in root["storm_id"][:]]
            at = dict(root.attrs)
            icsv = list(csv.DictReader(io.StringIO(z.read("index.csv").decode())))
            meta = json.loads(z.read("meta.json").decode())

        ck(f"{tag}: 200 scenarios", depth.shape[0] == 200 == len(sid) == len(mm),
           f"{depth.shape[0]}/{len(sid)}/{len(mm)}")
        ck(f"{tag}: storm ids unique", len(set(sid)) == len(sid))
        ck(f"{tag}: magnitudes ascending", bool(np.all(np.diff(mm) >= -1e-9)))
        ck(f"{tag}: dtypes", str(depth.dtype) == "float32" and str(extent.dtype) == "uint8",
           f"{depth.dtype}/{extent.dtype}")
        ck(f"{tag}: extent shape equals depth shape", extent.shape == depth.shape)
        ck(f"{tag}: grid_shape attr", list(at["grid_shape"]) == list(depth.shape[1:]))
        ck(f"{tag}: manifest grid", r["grid_wxh"] == f"{depth.shape[2]}x{depth.shape[1]}")
        ck(f"{tag}: crs is the island model crs", at["crs"] == str(crs), at["crs"])
        ck(f"{tag}: extent threshold recorded", at["extent_threshold_m"] == EXTENT_M)
        ck(f"{tag}: admin_unit attr", at["admin_unit"]["pcode"] == pcode
           and at["admin_unit"]["name"] == r["name"])
        ck(f"{tag}: fluvial index absent (pluvial only country)", "fluvial_q" not in names
           and not any(n.startswith("fluvial_q") for n in names))

        # index.csv and meta.json are the human readable companions
        ck(f"{tag}: index.csv rows match the arrays",
           [x["storm_id"] for x in icsv] == sid
           and np.allclose([float(x["magnitude_mm"]) for x in icsv], mm, atol=0.005))
        ck(f"{tag}: meta.json matches attrs", meta["grid_shape"] == list(at["grid_shape"])
           and meta["n_storms"] == at["n_storms"])

        # magnitudes must be the ones computed from the rain geotiffs
        src_m = mags_all[pcode]
        ck(f"{tag}: magnitudes come from the rain table",
           np.allclose(mm, [src_m[s] for s in sid], rtol=0, atol=1e-9))
        ck(f"{tag}: order is the magnitude sort of that table",
           sid == sorted(src_m, key=lambda s: src_m[s]))
        ck(f"{tag}: manifest magnitude range", abs(float(r["magnitude_min_mm"]) - mm.min()) < 0.05
           and abs(float(r["magnitude_max_mm"]) - mm.max()) < 0.05)
        ck(f"{tag}: per unit json agrees", per_unit[pcode]["magnitudes_mm"] == src_m)

        # window geometry recomputed from the shapefile, independently
        g = adm[adm.adm3_pcode == pcode].to_crs(crs).geometry.iloc[0]
        minx, miny, maxx, maxy = g.bounds
        c0 = max(0, int(np.floor((minx - T.c) / T.a)) - BUF)
        c1 = min(GW, int(np.ceil((maxx - T.c) / T.a)) + BUF)
        r0 = max(0, int(np.floor((maxy - T.f) / T.e)) - BUF)
        r1 = min(GH, int(np.ceil((miny - T.f) / T.e)) + BUF)
        wt = rasterio.windows.transform(Window(c0, r0, c1 - c0, r1 - r0), T)
        ck(f"{tag}: stored transform equals the recomputed window transform",
           np.allclose(at["transform"], list(np.asarray(wt)[:6]), atol=1e-9))
        ck(f"{tag}: window size equals the array size",
           (r1 - r0, c1 - c0) == tuple(depth.shape[1:]))
        # how much of the municipality the island model grid actually covers.
        # The window can only ever be clipped by the model domain itself, so
        # this is the honest coverage number, reported per unit.
        from shapely.geometry import box as _box
        wb = _box(at["transform"][2],
                  at["transform"][5] + depth.shape[1] * T.e,
                  at["transform"][2] + depth.shape[2] * T.a,
                  at["transform"][5])
        cov = g.intersection(wb).area / g.area * 100.0
        COVER[pcode] = (r["name"], round(cov, 2))
        ck(f"{tag}: municipality covered by the island model grid",
           cov >= 99.99 or pcode in KNOWN_PARTIAL, f"{cov:.2f} percent")

        # depth against the blob, on a sample of scenarios; deep units get more
        picks = random.sample(range(200), 12 if pcode in deep else 3)
        for k in picks:
            ent = by_isl[isl][sid[k]]
            full = blob_scene(ent)
            want = full[r0:r1, c0:c1].astype("float32") / 100.0
            got = np.asarray(depth[k])
            n_pixel_checks += got.size
            if not np.array_equal(got, want):
                ck(f"{tag}: depth[{k}] equals the source window", False,
                   f"maxdiff {float(np.abs(got-want).max()):.4f}")
                break
            if not np.array_equal(np.asarray(extent[k]), (want >= EXTENT_M).astype("uint8")):
                ck(f"{tag}: extent[{k}] is depth >= 0.05 m", False)
                break
        else:
            ck(f"{tag}: depth and extent equal the source ({len(picks)} scenarios)", True)

        # the site config
        sl = r["store"][len(f"fim_store_{pcode}_"):-len("_v1.zarr")]
        y = open(f"{OUT}/fim_config/Comoros_{sl}.yaml").read()
        ck(f"{tag}: yaml region key", f"region: Comoros_{sl}" in y)
        ck(f"{tag}: yaml outputs_root is the orchestrator root",
           "outputs_root: outputs\n" in y)
        ck(f"{tag}: yaml store points at this store", f"store: fim_store/Comoros/{r['store']}" in y)
        ck(f"{tag}: yaml thresholds are the standard four",
           "thresholds_m: [0.10, 0.30, 0.70, 1.00]" in y)
        ck(f"{tag}: yaml fluvial disabled", "fluvial:\n    enabled: false" in y)
        ov = [ln.split(":")[1].strip() for ln in y.splitlines()
              if ln.strip().startswith("reference_scenario")][0]
        ck(f"{tag}: overbank reference is the driest scenario", ov == sid[0])
        # the overbank reference is used as a MASK: pixels already wet in it
        # are removed from the probability products, so it has to be a dry
        # scenario whose map is baseline standing water, not a flood map
        ck(f"{tag}: overbank reference scenario is dry", mm[0] < 1.0, f"{mm[0]:.3f} mm")
        wet0 = float((np.asarray(depth[0]) >= 0.10).mean()) * 100
        OBWET[pcode] = round(wet0, 3)
        ck(f"{tag}: overbank mask covers only baseline water", wet0 < 5.0,
           f"{wet0:.2f} percent of the window at 0.10 m")
        ck(f"{tag}: aoc file referenced exists",
           os.path.isfile(f"{OUT}/fim_config/aoc/Comoros_{pcode}_{sl}_aoc.geojson"))

        gj = json.load(open(f"{OUT}/fim_config/aoc/Comoros_{pcode}_{sl}_aoc.geojson"))
        f0 = gj["features"][0]
        ck(f"{tag}: aoc properties", f0["properties"]["pcode"] == pcode
           and f0["properties"]["country"] == "Comoros")
        gs = gpd.GeoSeries([adm[adm.adm3_pcode == pcode].geometry.iloc[0]], crs=adm.crs)
        agj = gpd.GeoSeries.from_file(f"{OUT}/fim_config/aoc/Comoros_{pcode}_{sl}_aoc.geojson")
        ck(f"{tag}: aoc geometry equals the shapefile polygon",
           bool(agj.geometry.iloc[0].equals(gs.iloc[0])))
        ck(f"{tag}: aoc has no degenerate part",
           min(p.area for p in getattr(agj.geometry.iloc[0], "geoms", [agj.geometry.iloc[0]]))
           * 111000 ** 2 > 10000)

    low = sorted(((c, p, n) for p, (n, c) in COVER.items() if c < 99.99))
    print("\nmodel grid coverage below 100 percent: "
          + (", ".join(f"{p} {n} {c:.2f}%" for c, p, n in low) if low else "none"))
    print("overbank mask, largest baseline wet fraction at 0.10 m: "
          + ", ".join(f"{k} {v:.2f}%" for k, v in sorted(OBWET.items(), key=lambda kv: -kv[1])[:3]))
    print(f"\npixels compared against the blob: {n_pixel_checks:,}")
    print(f"stores checked: {len(rows)}")
    if FAIL:
        print(f"\n{len(FAIL)} CHECK(S) FAILED")
        for f in FAIL[:40]:
            print("   ", f)
        return 1
    print("\nALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
