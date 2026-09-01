"""Per site pluvial magnitudes for Haiti from the RainyDay scenario rain.

Same method as the islands and Comoros: the magnitude of a scenario is the
AREA WEIGHTED mean of its band summed storm total over the site polygon, so
each rain cell counts in proportion to the share of the cell inside the
polygon. Here the polygon is the hydrodynamic model footprint, which is also
the area of concern the real time side samples its rain over, so the store
index and the cycle rain are the same physical quantity.
"""
import glob
import json
import os
import re

import numpy as np
import rasterio
from affine import Affine
from rasterio.features import rasterize
from shapely.geometry import shape

ROOT = "/home/claude/hti"
AOC = "/mnt/user-data/uploads/Tito/main/TITOCaribbeanAndComoros/fim_config/aoc"
SITES = {"Gris": ("gris", "Haiti_Gris"), "LaQuinte": ("laquinte", "Haiti_LaQuinte")}


def cell_weights(geom, src, refine=32):
    fine_t = src.transform * Affine.scale(1.0 / refine, 1.0 / refine)
    fine = rasterize([(geom, 1)], out_shape=(src.height * refine, src.width * refine),
                     transform=fine_t, fill=0, dtype="uint8", all_touched=False)
    return fine.reshape(src.height, refine, src.width, refine).mean(axis=(1, 3))


def main():
    out = {}
    for site, (key, aoc_name) in SITES.items():
        geom = shape(json.load(open(f"{AOC}/{aoc_name}_aoc.geojson"))["features"][0]["geometry"])
        tifs = sorted(glob.glob(f"{ROOT}/rain/{key}/*.tif"))
        assert len(tifs) == 200, (site, len(tifs))
        with rasterio.open(tifs[0]) as src:
            w = cell_weights(geom, src)
            crs, res = src.crs, src.transform.a
            bounds = src.bounds
        tot_w = w.sum()
        # the polygon area in cell units, for a coverage check
        area_cells = geom.area / (res * res)
        mags, names = {}, {}
        for f in tifs:
            m = re.match(r"(sample_\d+)__scenario_(.+)\.tif", os.path.basename(f))
            sid, scen = m.group(1), m.group(2)
            with rasterio.open(f) as src:
                a = src.read()
                nod = src.nodata
            if nod is not None:
                a = np.where(a == nod, 0.0, a)
            tot = a.sum(axis=0)
            mags[sid] = float((tot * w).sum() / tot_w)
            names[sid] = scen
        v = np.array([mags[s] for s in sorted(mags)])
        print(f"== {site}: {len(mags)} scenarios, grid {w.shape[1]}x{w.shape[0]} {crs} {res:.5f} deg")
        print(f"   weighted cells {tot_w:.3f}, polygon area {area_cells:.3f} cells, "
              f"coverage {tot_w / area_cells * 100:.2f} percent")
        print(f"   magnitude mm  min {v.min():.1f}  median {np.median(v):.1f}  max {v.max():.1f}  "
              f"zeros {(v == 0).sum()}")
        out[site] = {"magnitudes": mags, "scenario_names": names,
                     "diagnostics": {"n_scenarios": len(mags),
                                     "rain_grid": [int(w.shape[1]), int(w.shape[0])],
                                     "rain_crs": str(crs), "rain_res_deg": float(res),
                                     "rain_bounds": [float(b) for b in bounds],
                                     "cells_covered": round(float(tot_w), 4),
                                     "polygon_area_cells": round(float(area_cells), 4),
                                     "coverage_of_footprint": round(float(tot_w / area_cells), 4)}}
    json.dump({"method": ("area weighted mean of the band summed storm total over the "
                          "hydrodynamic model footprint, refined rasterization factor 32"),
               "sites": out}, open(f"{ROOT}/magnitudes_Haiti_from_rain.json", "w"), indent=1)
    import csv
    with open(f"{ROOT}/magnitudes_Haiti_from_rain.csv", "w", newline="") as fh:
        wtr = csv.writer(fh)
        wtr.writerow(["site", "storm_id", "scenario_name", "magnitude_mm"])
        for site in SITES:
            d = out[site]
            for s in sorted(d["magnitudes"]):
                wtr.writerow([site, s, d["scenario_names"][s], round(d["magnitudes"][s], 2)])
    print("\nwrote magnitudes_Haiti_from_rain.json and .csv")


if __name__ == "__main__":
    main()
