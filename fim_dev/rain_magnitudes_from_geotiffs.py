"""Per administrative unit storm magnitudes from RainyDay rain scenario GeoTIFFs.

One GeoTIFF per scenario, one band per time step; the storm total is the sum
over bands. The magnitude of a unit is the AREA WEIGHTED mean of that total
over the unit polygon: each rain cell contributes in proportion to the share
of the cell that lies inside the polygon (computed by rasterizing the polygon
on a refined subgrid, which is equivalent to exact zonal extraction). This
matters here because the rain grid is coarse (about 3 km) while the units are
small, so a plain cell centre mask would bias or even empty small units.

Degenerate polygon parts below --min-part-m2 are dropped and reported: the
COD-AB 2019 boundary of Barbuda for example carries a 900 m2 artifact square
900 km away from the island, which would otherwise widen the unit far beyond
its real extent.

Usage:

    python rain_magnitudes_from_geotiffs.py \
        --shapefile atg_admbnda_adm1_2019.shp \
        --grid AG01=rain/barbuda/scenarios_geotiff \
        --grid "AG03,AG04,AG05,AG06,AG07,AG08=rain/antigua/scenarios_geotiff" \
        --out magnitudes_Antigua_from_rain.json --csv magnitudes_Antigua_from_rain.csv

Outputs one JSON keyed by unit code with {storm_id: magnitude_mm} plus a flat
CSV, and prints coverage diagnostics per unit.
"""

import argparse
import glob
import json
import os
import re

import numpy as np
import rasterio
from rasterio import Affine
from rasterio.features import rasterize

try:
    import geopandas as gpd
except ImportError as exc:  # pragma: no cover
    raise SystemExit("needs geopandas (pip install geopandas)") from exc

from shapely.geometry import MultiPolygon


def clean_geometry(geom, geom_metric, min_part_m2):
    """Drop parts smaller than min_part_m2; return (geometry, dropped list)."""
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    pm = list(geom_metric.geoms) if geom_metric.geom_type == "MultiPolygon" \
        else [geom_metric]
    keep = [p for p, q in zip(parts, pm) if q.area >= min_part_m2]
    dropped = [{"area_m2": round(q.area, 1),
                "bbox": [round(v, 5) for v in p.bounds]}
               for p, q in zip(parts, pm) if q.area < min_part_m2]
    if not keep:
        raise SystemExit("every polygon part was dropped; lower --min-part-m2")
    return (MultiPolygon(keep) if len(keep) > 1 else keep[0]), dropped


def cell_weights(geom, src, refine=32):
    """Fraction of every raster cell covered by geom (exact extract equivalent)."""
    fine_t = src.transform * Affine.scale(1.0 / refine, 1.0 / refine)
    fine = rasterize([(geom, 1)], out_shape=(src.height * refine, src.width * refine),
                     transform=fine_t, fill=0, dtype="uint8", all_touched=False)
    return fine.reshape(src.height, refine, src.width, refine).mean(axis=(1, 3))


def storm_total(path):
    with rasterio.open(path) as src:
        a = src.read(masked=True)
        return a.sum(axis=0).filled(0.0).astype("float64"), src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapefile", required=True)
    ap.add_argument("--id-field", default="ADM1_PCODE")
    ap.add_argument("--name-field", default="ADM1_EN")
    ap.add_argument("--grid", action="append", required=True,
                    help="CODE[,CODE...]=folder of scenario geotiffs")
    ap.add_argument("--metric-crs", default="EPSG:32620")
    ap.add_argument("--min-part-m2", type=float, default=10000.0)
    ap.add_argument("--refine", type=int, default=32)
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv", default="")
    a = ap.parse_args()

    adm = gpd.read_file(a.shapefile)
    adm_m = adm.to_crs(a.metric_crs)

    assign = {}
    for spec in a.grid:
        codes, folder = spec.split("=", 1)
        for c in codes.split(","):
            assign[c.strip()] = folder

    result, rows, diag = {}, [], {}
    for code, folder in assign.items():
        sel = adm.index[adm[a.id_field] == code]
        if len(sel) == 0:
            raise SystemExit(f"unit {code} not in {a.shapefile}")
        i = sel[0]
        name = str(adm.loc[i, a.name_field])
        geom, dropped = clean_geometry(adm.loc[i, "geometry"],
                                       adm_m.loc[i, "geometry"], a.min_part_m2)
        files = sorted(glob.glob(os.path.join(folder, "*.tif")))
        if not files:
            raise SystemExit(f"no geotiffs in {folder}")

        with rasterio.open(files[0]) as src0:
            w = cell_weights(geom, src0, a.refine)
            cell_area = abs(src0.transform.a * src0.transform.e)
            ref = (src0.width, src0.height,
                   tuple(np.round(np.asarray(src0.transform)[:6], 9)), str(src0.crs))
        covered = float(w.sum())
        expected = geom.area / cell_area
        coverage = covered / expected if expected else float("nan")
        if w.sum() <= 0:
            raise SystemExit(f"{code}: polygon does not overlap the rain grid {folder}")

        mags = {}
        for f in files:
            sid = os.path.basename(f).split("__")[0]
            if not re.match(r"^sample_\d+$", sid):
                raise SystemExit(f"unexpected file name {os.path.basename(f)}")
            with rasterio.open(f) as src:
                sig = (src.width, src.height,
                       tuple(np.round(np.asarray(src.transform)[:6], 9)), str(src.crs))
                if sig != ref:
                    raise SystemExit(f"{f}: grid differs from the first scenario")
                tot = src.read(masked=True).sum(axis=0).filled(0.0).astype("float64")
            mags[sid] = round(float((tot * w).sum() / w.sum()), 3)
            rows.append((code, name, sid, mags[sid], os.path.basename(f)))
        result[code] = mags
        vals = np.array(list(mags.values()))
        diag[code] = {"name": name, "n_scenarios": len(mags),
                      "grid_folder": folder,
                      "cells_covered": round(covered, 3),
                      "coverage_of_unit_area": round(coverage, 4),
                      "dropped_parts": dropped,
                      "magnitude_mm": {"min": round(float(vals.min()), 1),
                                       "median": round(float(np.median(vals)), 1),
                                       "max": round(float(vals.max()), 1),
                                       "zero_scenarios": int((vals == 0).sum())}}
        print(f"{code} {name:<14} n={len(mags):3d} cells={covered:6.2f} "
              f"coverage={coverage*100:5.1f}%  mm min/med/max="
              f"{vals.min():7.1f}/{np.median(vals):7.1f}/{vals.max():7.1f}"
              + (f"  DROPPED {len(dropped)} part(s)" if dropped else ""))

    with open(a.out, "w") as fh:
        json.dump({"magnitudes": result, "diagnostics": diag,
                   "method": ("area weighted mean of the band summed storm total "
                              "over the unit polygon, refined rasterization "
                              f"factor {a.refine}")}, fh, indent=1)
    print("wrote", a.out)
    if a.csv:
        import csv as _csv
        with open(a.csv, "w", newline="") as fh:
            w_ = _csv.writer(fh)
            w_.writerow(["unit_code", "unit_name", "storm_id", "magnitude_mm", "file"])
            w_.writerows(sorted(rows))
        print("wrote", a.csv)


if __name__ == "__main__":
    main()
