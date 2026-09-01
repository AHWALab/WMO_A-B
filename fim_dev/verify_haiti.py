"""Independent check of the two Haiti stores, back against the sources.

Nothing here reuses the builder: the depth is re-read from the delivery blob,
the magnitudes from the rain table, the discharges from the delivery zips, and
the geometry from the TIFF tie points recorded at extraction time.
"""
import csv
import io
import json
import os
import random
import zipfile
import zlib

import numpy as np
import zarr

U = "/mnt/user-data/uploads/FIM_version/Data/_haiti_build"
Q = "/mnt/user-data/uploads/FIM_version/Data/Haitii"
ROOT = "/home/claude/hti"
OUT = f"{ROOT}/out"
EXTENT_M = 0.05

SITES = {
    "Gris": {"key": "gris", "grid": (4995, 7160), "origin": (779271.552, 2061763.471),
             "zip": "HAITI_outputs_Q_GRISS", "gauges": ["ts.cuenca_griss.crest.csv"]},
    "LaQuinte": {"key": "laquinte", "grid": (3557, 3417), "origin": (740413.47, 2156979.145),
                 "zip": "HAITI_outputs_Q_LAQUINTA",
                 "gauges": ["ts.cuenca_laquinta_1.crest.csv", "ts.cuenca_laquinta_2.crest.csv"]},
}

FAIL = []
random.seed(20260825)


def ck(name, ok, detail=""):
    if not ok:
        FAIL.append(name)
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))


def blob_map(entry, grid):
    with open(os.path.join(U, entry["blob"]), "rb") as f:
        f.seek(entry["start"])
        raw = zlib.decompress(f.read(entry["csize"]))
    a = np.frombuffer(raw, dtype="<u2").reshape(entry["H"], entry["W"])
    H, W = grid
    if a.shape == (H, W):
        return a
    sx, sy = entry["pixel_scale"][0], entry["pixel_scale"][1]
    ry = np.minimum((np.arange(H) * 2.0 / sy).astype(int), a.shape[0] - 1)
    rx = np.minimum((np.arange(W) * 2.0 / sx).astype(int), a.shape[1] - 1)
    return a[np.ix_(ry, rx)]


def gauge_max(zf, sample, gauge):
    rd = list(csv.reader(io.StringIO(zf.read(f"{sample}/{gauge}").decode())))
    col = [i for i, h in enumerate(rd[0]) if h.startswith("Discharge")][0]
    vals = []
    for r in rd[1:]:
        try:
            vals.append(float(r[col]))
        except (ValueError, IndexError):
            pass
    clean = [v for v in vals if v == v]
    return max(clean) if clean else float("nan")


def main():
    mags_all = json.load(open(f"{ROOT}/magnitudes_Haiti_from_rain.json"))["sites"]
    npx = 0
    for site, cfg in SITES.items():
        print(f"\n== {site}")
        st = json.load(open(f"{U}/state_{cfg['key']}.json"))["done"]
        mags = mags_all[site]["magnitudes"]
        names = mags_all[site]["scenario_names"]
        zp = f"{OUT}/fim_store/Haiti/fim_store_Haiti_{site}_v1.zarr.zip"
        with zipfile.ZipFile(zp) as z:
            root = zarr.open_group(zarr.storage.ZipStore(zp, mode="r"), mode="r")
            depth, extent = root["depth"], root["extent"]
            mm = np.asarray(root["magnitude_mm"][:], dtype="float64")
            fq = np.asarray(root["fluvial_q"][:], dtype="float64")
            sid = [s.decode() if isinstance(s, bytes) else str(s) for s in root["storm_id"][:]]
            at = dict(root.attrs)
            icsv = list(csv.DictReader(io.StringIO(z.read("index.csv").decode())))
            meta = json.loads(z.read("meta.json").decode())
        H, W = cfg["grid"]
        n = len(sid)

        ck(f"{site}: ALL 200 scenarios are in the store", set(sid) == set(mags),
           f"{n} in store, {len(st)} maps delivered")
        ph = at.get("placeholder_source", "")
        want_ph = sorted(s0 for s0 in mags if s0 not in st)
        ck(f"{site}: placeholder list is exactly the undelivered scenarios",
           sorted(at.get("placeholder_scenarios", [])) == want_ph,
           f"{len(want_ph)} scenarios, source {ph or 'none'}")
        ck(f"{site}: storm ids unique", len(set(sid)) == n)
        ck(f"{site}: magnitudes ascending", bool(np.all(np.diff(mm) >= -1e-9)))
        ck(f"{site}: shapes", depth.shape == (n, H, W) and extent.shape == (n, H, W),
           str(depth.shape))
        ck(f"{site}: depth is uint16 centimetres with scale 0.01",
           str(depth.dtype) == "uint16" and at.get("depth_scale") == 0.01
           and str(extent.dtype) == "uint8")
        ck(f"{site}: crs and transform",
           at["crs"] == "EPSG:32618"
           and np.allclose(at["transform"], [2.0, 0.0, cfg["origin"][0], 0.0, -2.0, cfg["origin"][1]]),
           f"{at['crs']} {at['transform']}")
        ck(f"{site}: grid_shape attr", list(at["grid_shape"]) == [H, W])
        ck(f"{site}: extent threshold recorded", at["extent_threshold_m"] == EXTENT_M)
        ck(f"{site}: real_maps_of_scenarios attr",
           list(at["real_maps_of_scenarios"]) == [len(st), 200])

        # magnitudes must be the rain table values, in its sort order
        ck(f"{site}: magnitudes come from the rain table",
           np.allclose(mm, [mags[s] for s in sid], rtol=0, atol=1e-9))
        ck(f"{site}: order is the magnitude sort of ALL 200 scenarios",
           sid == sorted(mags, key=lambda s: mags[s]))
        ck(f"{site}: scenario names recorded",
           all(at["scenario_names"][s] == names[s] for s in sid))

        # fluvial index re-read from the delivery zip
        zf = zipfile.ZipFile(f"{Q}/{cfg['zip']}.zip")
        want = np.array([[gauge_max(zf, s, g) for g in cfg["gauges"]] for s in sid])
        ck(f"{site}: fluvial index matches the delivered discharge, in store order",
           fq.shape == want.shape and np.allclose(fq, want, equal_nan=True),
           f"{fq.shape} max {np.nanmax(fq):.0f} m3/s")
        ck(f"{site}: fluvial mu and sigma", np.allclose(at["fluvial_mu"], fq.mean(axis=0))
           and np.allclose(at["fluvial_sigma"], fq.std(axis=0, ddof=1)))
        ck(f"{site}: no gauge is all nan", bool(np.isfinite(fq).all()),
           f"{int((~np.isfinite(fq)).sum())} nan values")

        # depth against the delivery blob; placeholders against the source map
        real_idx = [k for k in range(n) if sid[k] in st]
        ph_idx = [k for k in range(n) if sid[k] not in st]
        picks = random.sample(real_idx, min(12, len(real_idx)))
        bad = None
        for k in picks:
            want_d = blob_map(st[sid[k]], (H, W))
            got = np.asarray(depth[k])
            npx += got.size
            if not np.array_equal(got, want_d):
                bad = (k, "depth"); break
            if not np.array_equal(np.asarray(extent[k]),
                                  (want_d >= EXTENT_M * 100).astype("uint8")):
                bad = (k, "extent"); break
        ck(f"{site}: real depth and extent equal the delivered maps ({len(picks)} scenarios)",
           bad is None, "" if bad is None else str(bad))
        if ph_idx:
            ph_map = blob_map(st[ph], (H, W))
            bad = None
            for k in random.sample(ph_idx, min(8, len(ph_idx))):
                npx += ph_map.size
                if not np.array_equal(np.asarray(depth[k]), ph_map):
                    bad = k; break
            ck(f"{site}: placeholder scenarios carry exactly {ph}'s map "
               f"(8 of {len(ph_idx)} checked)", bad is None, str(bad) if bad else "")
            stat = {r["storm_id"]: r["map_status"] for r in icsv}
            ck(f"{site}: index.csv map_status marks every placeholder",
               all(stat[s0] == f"placeholder_copy_of_{ph}" for s0 in want_ph)
               and all(stat[s0] == "real" for s0 in sid if s0 in st))

        ck(f"{site}: index.csv agrees with the arrays",
           [r["storm_id"] for r in icsv] == sid
           and np.allclose([float(r["magnitude_mm"]) for r in icsv], mm, atol=0.005))
        ck(f"{site}: meta.json agrees", meta["n_storms"] == n
           and meta["grid_shape"] == [H, W])

        # the overbank reference must be a dry scenario
        d0 = np.asarray(depth[0]).astype("float32") * 0.01
        wet0 = float((d0 >= 0.10).mean()) * 100
        ck(f"{site}: driest scenario is dry enough to be the overbank reference",
           mm[0] < 1.0 and wet0 < 5.0, f"{mm[0]:.2f} mm, {wet0:.3f} percent wet at 0.10 m")

        print(f"   store {os.path.getsize(zp)/1e6:.1f} MB, {n} scenarios, "
              f"magnitudes {mm.min():.2f}..{mm.max():.1f} mm, "
              f"Q {fq.min():.0f}..{fq.max():.0f} m3/s, overbank {sid[0]}")

    print(f"\npixels compared against the delivery: {npx:,}")
    if FAIL:
        print(f"\n{len(FAIL)} CHECK(S) FAILED")
        for f in FAIL:
            print("   ", f)
        return 1
    print("\nALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
