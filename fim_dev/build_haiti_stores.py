# NOTE ON PATHS: the constants below are the paths of the machine this was
# built on (the extracted max depth blobs, the magnitude table, the discharge
# table). Point them at your own copies before rerunning. Needs numpy and zarr.
"""Build the two Haiti scenario stores: Riviere Grise and La Quinte.

Inputs
  blob + state   the delivered MaximumDepth.tif of every sample, pulled out of
                 the nested MaxVeloc-dept.zip without extracting the 457 GB of
                 deliveries, quantized to centimetres and deflated on the device
  magnitudes     per site RainyDay storm totals, area weighted over the model
                 footprint (magnitudes_Haiti_from_rain.json)
  discharge      per scenario boundary discharge maxima (fluvial_Haiti.json)

Output, per site
  fim_store/Haiti/fim_store_Haiti_<Site>_v1.zarr(.zip)

Both sites carry ALL 200 scenarios and BOTH indexes, pluvial magnitude_mm and
fluvial fluvial_q, so they run pluvial, fluvial and combined exactly like the
Guatemala basins.

Storage format (since v1.8.0): depth is uint16 CENTIMETRES with a depth_scale
attribute of 0.01; FimStore.depth() applies the scale, so every consumer keeps
seeing float32 metres. The source data is stored at 1 cm precision anyway, so
this is lossless relative to what was shipped before and about a third
smaller (half the raw bytes, and quantized integers compress better than the
float32 encoding of the same values).

GRIS PLACEHOLDERS: only 50 of the 200 flood maps were delivered (samples 0001
to 0050). By request, every missing scenario carries a COPY OF ONE delivered
map (PLACEHOLDER below) so the full 200 scenario pipeline runs now; the
magnitudes and discharges of all 200 scenarios are real. The placeholder
scenarios are listed in the store attrs and marked in index.csv, and the maps
will be replaced when the remaining deliveries arrive (rerun this script,
nothing else changes).
"""
import csv
import json
import os
import shutil
import sys
import zipfile
import zlib

import numpy as np
import zarr
import zarr.codecs as zc

U = "/mnt/user-data/uploads/FIM_version/Data/_haiti_build"
ROOT = "/home/claude/hti"
OUT = f"{ROOT}/out"
EXTENT_M = 0.05
ZLEVEL_DEPTH = 19
ZLEVEL_EXTENT = 9
CRS = "EPSG:32618"
PLACEHOLDER = {"Gris": "sample_0004"}   # real delivered map used for missing scenarios

SITES = {
    "Gris": {"key": "gris", "res": 2.0, "origin": (779271.552, 2061763.471),
             "grid": (4995, 7160), "gauges": ["Q_cuenca_griss_m3s"],
             "series": ["ts.cuenca_griss.crest.{cycle}.csv"]},
    "LaQuinte": {"key": "laquinte", "res": 2.0, "origin": (740413.47, 2156979.145),
                 "grid": (3557, 3417),
                 "gauges": ["Q_cuenca_laquinta_1_m3s", "Q_cuenca_laquinta_2_m3s"],
                 "series": ["ts.cuenca_laquinta_1.crest.{cycle}.csv",
                            "ts.cuenca_laquinta_2.crest.{cycle}.csv"]},
}


def load_map(entry, grid):
    """uint16 centimetres on the site grid; a coarser delivery is resampled."""
    with open(os.path.join(U, entry["blob"]), "rb") as f:
        f.seek(entry["start"])
        raw = zlib.decompress(f.read(entry["csize"]))
    a = np.frombuffer(raw, dtype="<u2").reshape(entry["H"], entry["W"])
    H, W = grid
    if a.shape == (H, W):
        return a, None
    # same origin and extent, coarser pixel: nearest neighbour onto the site grid
    sy = entry["pixel_scale"][1]
    sx = entry["pixel_scale"][0]
    ry = np.minimum((np.arange(H) * 2.0 / sy).astype(int), a.shape[0] - 1)
    rx = np.minimum((np.arange(W) * 2.0 / sx).astype(int), a.shape[1] - 1)
    note = (f"delivered at {sx:g} m on a {a.shape[1]}x{a.shape[0]} grid, resampled "
            f"nearest neighbour onto the {W}x{H} 2 m site grid")
    return np.ascontiguousarray(a[np.ix_(ry, rx)]), note


def zip_store(store_dir, zip_path):
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r, _, fs in os.walk(store_dir):
            for fn in fs:
                p = os.path.join(r, fn)
                z.write(p, os.path.relpath(p, store_dir))
    return os.path.getsize(zip_path)


def build(site):
    cfg = SITES[site]
    st = json.load(open(f"{U}/state_{cfg['key']}.json"))["done"]
    mags_all = json.load(open(f"{ROOT}/magnitudes_Haiti_from_rain.json"))["sites"][site]
    mags, names = mags_all["magnitudes"], mags_all["scenario_names"]
    flu = json.load(open(f"{ROOT}/fluvial_Haiti.json"))[site]
    H, W = cfg["grid"]
    res, (x0, y0) = cfg["res"], cfg["origin"]
    ph = PLACEHOLDER.get(site)

    # ALL 200 scenarios, magnitude sorted; missing maps borrow the placeholder
    order = sorted(mags, key=lambda s: mags[s])
    n = len(order)
    missing = [s for s in order if s not in st]
    if missing and not ph:
        raise SystemExit(f"{site}: {len(missing)} maps missing and no placeholder set")
    print(f"== {site}: {n} scenarios, {n - len(missing)} delivered maps, "
          f"{len(missing)} placeholder copies of {ph}" if missing else
          f"== {site}: {n} scenarios, all maps delivered", flush=True)

    os.makedirs(f"{OUT}/fim_store/Haiti", exist_ok=True)
    sd = f"{OUT}/fim_store/Haiti/fim_store_Haiti_{site}_v1.zarr"
    prog_path = f"{OUT}/progress_{site}.json"
    # resumable: progress is checkpointed so a killed run continues; the format
    # tag guards against resuming into a store built by an older revision
    prog = json.load(open(prog_path)) if os.path.exists(prog_path) else None
    if (prog and prog.get("order") == order and prog.get("fmt") == "u16z19"
            and os.path.isdir(sd)):
        root = zarr.open_group(sd, mode="a")
        depth, extent = root["depth"], root["extent"]
        notes, wet_frac = prog["notes"], prog["wet_frac"]
        done = len(wet_frac)
        print(f"   resuming at scenario {done} of {n}", flush=True)
    else:
        shutil.rmtree(sd, ignore_errors=True)
        root = zarr.open_group(sd, mode="w")
        depth = root.create_array("depth", shape=(n, H, W), dtype="uint16",
                                  chunks=(1, H, W),
                                  compressors=[zc.ZstdCodec(level=ZLEVEL_DEPTH)])
        extent = root.create_array("extent", shape=(n, H, W), dtype="uint8",
                                   chunks=(1, H, W),
                                   compressors=[zc.ZstdCodec(level=ZLEVEL_EXTENT)])
        notes, wet_frac, done = {}, [], 0
    budget = float(os.environ.get("BUILD_BUDGET", "1e9"))
    import time as _t
    t0 = _t.time()
    ph_cache = None
    for k in range(done, n):
        if _t.time() - t0 > budget:
            print(f"   budget reached at {k} of {n}, rerun to continue", flush=True)
            json.dump({"order": order, "notes": notes, "wet_frac": wet_frac,
                       "fmt": "u16z19"}, open(prog_path, "w"))
            return None
        s = order[k]
        if s in st:
            q, note = load_map(st[s], (H, W))
            if note:
                notes[s] = note
                print(f"   {s}: {note}", flush=True)
        else:
            if ph_cache is None:
                ph_cache = load_map(st[ph], (H, W))[0]
            q = ph_cache
        depth[k] = q
        extent[k] = (q >= int(EXTENT_M * 100)).astype("uint8")
        wet_frac.append(float((q >= int(EXTENT_M * 100)).mean()))
        if k % 10 == 9 or k == n - 1:
            json.dump({"order": order, "notes": notes, "wet_frac": wet_frac,
                       "fmt": "u16z19"}, open(prog_path, "w"))
            print(f"   {k + 1}/{n}", flush=True)
    mm = np.array([mags[s] for s in order], dtype="float64")
    root.create_array("magnitude_mm", shape=(n,), dtype="float64", chunks=(n,))[:] = mm
    L = max(len(s) for s in order)
    sid = root.create_array("storm_id", shape=(n,), dtype=f"S{L}", chunks=(n,))
    sid[:] = np.array([s.encode() for s in order], dtype=f"S{L}")

    # fluvial index, rows in STORE order, real for all 200 scenarios
    q = np.array([flu["q"][s] for s in order], dtype="float64")
    fa = root.create_array("fluvial_q", shape=q.shape, dtype="float64")
    fa[:] = q

    root.attrs.update({
        "crs": CRS, "transform": [res, 0.0, x0, 0.0, -res, y0],
        "extent_threshold_m": EXTENT_M, "source_nodata": None,
        "n_storms": n, "grid_shape": [H, W],
        "depth_scale": 0.01, "depth_units": "centimeters",
        "magnitude_source": (
            "real RainyDay storm totals: area weighted mean of the band summed "
            "scenario rain geotiff over the hydrodynamic model footprint "
            "(200 scenarios, 72 bands, 0.027 degree grid)"),
        "depth_source": (
            f"{site}.zip delivery, MaximumDepth.tif of each sample's "
            "MaxVeloc-dept.zip (float32 metres, 2 m grid), stored as uint16 "
            "centimetres"),
        "scenario_names": {s: names[s] for s in order},
        "real_maps_of_scenarios": [n - len(missing), n],
        "placeholder_scenarios": sorted(missing),
        "placeholder_source": ph if missing else "",
        "placeholder_note": (
            f"the {len(missing)} scenarios listed carry a COPY of {ph}'s map "
            "as a stand in until their flood maps are delivered; their "
            "magnitudes and discharges are real" if missing else ""),
        "resampling_notes": notes,
        "fluvial_index_names": cfg["gauges"],
        "fluvial_index_source": (
            f"HAITI_outputs_Q_{'GRISS' if site == 'Gris' else 'LAQUINTA'}.zip, per "
            "scenario maximum of the CREST discharge series"),
        "fluvial_mu": [float(v) for v in q.mean(axis=0)],
        "fluvial_sigma": [float(v) for v in q.std(axis=0, ddof=1)],
    })
    with open(os.path.join(sd, "index.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["storm_index", "storm_id", "scenario_name", "magnitude_mm"]
                   + cfg["gauges"] + ["map_status", "wet_fraction"])
        for i, s in enumerate(order):
            w.writerow([i, s, names[s], round(float(mm[i]), 2)]
                       + [round(float(v), 2) for v in q[i]]
                       + ["real" if s in st else f"placeholder_copy_of_{ph}",
                          round(wet_frac[i], 6)])
    with open(os.path.join(sd, "meta.json"), "w") as fh:
        json.dump(dict(root.attrs), fh, indent=2)
    size = zip_store(sd, sd + ".zip")
    shutil.rmtree(sd)
    print(f"   zip {size/1e6:.1f} MB, magnitudes {mm.min():.2f}..{mm.max():.1f} mm, "
          f"Q {q.min():.0f}..{q.max():.0f} m3/s, overbank ref {order[0]}", flush=True)
    return {"site": site, "n": n, "real_maps": n - len(missing),
            "zip_mb": round(size / 1e6, 2), "order": order,
            "mag_min": float(mm.min()), "mag_max": float(mm.max()),
            "placeholder": ph if missing else "", "notes": notes}


if __name__ == "__main__":
    res = [build(s) for s in (sys.argv[1:] or list(SITES))]
    res = [r for r in res if r]
    p = f"{OUT}/build_report.json"
    old = json.load(open(p)) if os.path.exists(p) else {}
    for r in res:
        old[r["site"]] = r
    json.dump(old, open(p, "w"), indent=1)
