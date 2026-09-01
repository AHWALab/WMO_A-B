"""Build per-administrative-unit FIM scenario stores from hydrodynamic samples.

One zarr store per ADM1 unit. Inputs per island:
  dmax.sampleN.tif    max flood depth per scenario, uint8, CENTIMETERS,
                      saturated at 255 (2.55 m). Stored as meters (dmax/100).
  pcpout.sampleN.tif  rain time series (bands = time steps, int16, mm).
                      Storm total = sum over bands; the pluvial magnitude of
                      a unit is the mean of that total over the unit polygon.

Outputs per unit: fim_store_<PCODE>_<Slug>_v1.zarr (+ .zip), AOC geojson,
site YAML, and per-country magnitude tables.

Usage:
    python build_admin_stores.py --jobs jobs.json --fimutils <path with tito_utils> \
        --out out_stores [--only-magnitudes | --only-stores] [--units AG03,AG04]

jobs.json: list of country jobs, see JOBS_EXAMPLE at the bottom.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
import zipfile

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.windows import Window
import shapefile  # pyshp

WGS84 = "EPSG:4326"


def slugify(name):
    s = re.sub(r"[^A-Za-z0-9]+", "", name.title().replace("'", ""))
    return s or "Unit"


def sample_id(path):
    m = re.search(r"sample(\d+)\.tif$", os.path.basename(path))
    return f"sample_{int(m.group(1)):04d}"


def read_units(shp_path, name_field="ADM1_EN", pcode_field="ADM1_PCODE"):
    r = shapefile.Reader(shp_path)
    fields = [f[0] for f in r.fields[1:]]
    ni, pi = fields.index(name_field), fields.index(pcode_field)
    units = []
    for sr in r.iterShapeRecords():
        geom = sr.shape.__geo_interface__
        units.append({
            "name": sr.record[ni],
            "pcode": sr.record[pi],
            "geom": geom,
            "bbox": sr.shape.bbox,  # (minx, miny, maxx, maxy)
        })
    return units


def island_grid(dmax_dir):
    first = sorted(glob.glob(os.path.join(dmax_dir, "dmax.*.tif")))[0]
    with rasterio.open(first) as r:
        return {"transform": r.transform, "width": r.width, "height": r.height,
                "bounds": tuple(r.bounds), "crs": str(r.crs) if r.crs else WGS84}


def bbox_overlap(b1, b2):
    w = min(b1[2], b2[2]) - max(b1[0], b2[0])
    h = min(b1[3], b2[3]) - max(b1[1], b2[1])
    return max(w, 0.0) * max(h, 0.0)


def unit_island(unit, grids):
    best, area = None, 0.0
    for isl, g in grids.items():
        a = bbox_overlap(unit["bbox"], g["bounds"])
        if a > area:
            best, area = isl, a
    return best


def storm_totals_and_unit_means(pcp_dir, units_on_island, grid, log):
    """Return ({sample_id: {pcode: mean_mm}}, {sample_id: domain_mean_mm})."""
    masks = {}
    for u in units_on_island:
        m = geometry_mask([u["geom"]], out_shape=(grid["height"], grid["width"]),
                          transform=grid["transform"], invert=True)
        masks[u["pcode"]] = m
        if not m.any():
            log(f"  WARNING: unit {u['pcode']} {u['name']} has no cells on this grid")
    per_unit, domain = {}, {}
    files = sorted(glob.glob(os.path.join(pcp_dir, "pcpout.*.tif")))
    for i, p in enumerate(files):
        sid = sample_id(p)
        with rasterio.open(p) as r:
            tot = r.read().astype("float64").sum(axis=0)  # (H, W) storm total, mm
        domain[sid] = float(tot.mean())
        per_unit[sid] = {pc: (float(tot[m].mean()) if m.any() else float("nan"))
                         for pc, m in masks.items()}
        if (i + 1) % 25 == 0:
            log(f"  rain totals {i + 1}/{len(files)}")
    return per_unit, domain


def unit_window(unit, grid, buffer_cells=10):
    tr = grid["transform"]
    inv = ~tr
    c0, r0 = inv * (unit["bbox"][0], unit["bbox"][3])
    c1, r1 = inv * (unit["bbox"][2], unit["bbox"][1])
    col0 = max(int(np.floor(min(c0, c1))) - buffer_cells, 0)
    row0 = max(int(np.floor(min(r0, r1))) - buffer_cells, 0)
    col1 = min(int(np.ceil(max(c0, c1))) + buffer_cells, grid["width"])
    row1 = min(int(np.ceil(max(r0, r1))) + buffer_cells, grid["height"])
    return Window(col0, row0, col1 - col0, row1 - row0)


def build_unit_store(unit, island, grid, dmax_dir, mags, out_dir, tmp_dir,
                     country, build_store, log):
    win = unit_window(unit, grid)
    tr = rasterio.windows.transform(win, grid["transform"])
    slug = slugify(unit["name"])
    store_name = f"fim_store_{unit['pcode']}_{slug}_v1.zarr"
    os.makedirs(tmp_dir, exist_ok=True)
    depth_files = {}
    profile = dict(driver="GTiff", dtype="float32", count=1,
                   width=int(win.width), height=int(win.height),
                   transform=tr, crs=grid["crs"], compress="deflate", nodata=None)
    for p in sorted(glob.glob(os.path.join(dmax_dir, "dmax.*.tif"))):
        sid = sample_id(p)
        with rasterio.open(p) as r:
            d = r.read(1, window=win).astype("float32") / 100.0  # cm -> m
        out_tif = os.path.join(tmp_dir, f"{sid}.tif")
        with rasterio.open(out_tif, "w", **profile) as w:
            w.write(d, 1)
        depth_files[sid] = out_tif
    unit_mags = {sid: mags[sid][unit["pcode"]] for sid in depth_files}
    store_path = os.path.join(out_dir, store_name)
    build_store(
        depth_files, store_path, unit_mags,
        extent_threshold_m=0.05, crs=grid["crs"],
        magnitude_source=(f"pcpout storm totals (band sum, mm), mean over "
                          f"ADM1 unit {unit['pcode']} {unit['name']}, Aug 2026"),
        extra_attrs={
            "country": country, "adm1_pcode": unit["pcode"],
            "adm1_name": unit["name"], "island_model": island,
            "depth_note": ("depth from dmax uint8 centimeters, divided by 100; "
                           "values saturate at 2.55 m"),
        })
    for f in depth_files.values():
        os.remove(f)
    zip_path = store_path + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dn, fns in os.walk(store_path):
            for fn in fns:
                fp = os.path.join(dirpath, fn)
                z.write(fp, os.path.relpath(fp, store_path))
    mb = os.path.getsize(zip_path) / 1e6
    log(f"  {unit['pcode']} {unit['name']}: window {int(win.width)}x{int(win.height)}, "
        f"zip {mb:.1f} MB")
    return store_name, zip_path


AOC_TEMPLATE = {"type": "FeatureCollection", "features": []}

YAML_TEMPLATE = """# FIM site: {country}, {name} ({pcode}). Pluvial only, per ADM1 unit.
# Generated by fim_dev/build_admin_stores.py from the {island} island model.
# Store depths come from dmax (uint8 centimeters, saturated at 2.55 m).
# In orchestrated runs the fim_regions block in Caribbean_Comoros_config.py
# switches the whole country and overrides the thresholds below.
region: {country}_{slug}

outputs_root: outputs/{country}
cycle_format: "%Y%m%d.%H%M%S"

aoc_geojson: fim_config/aoc/{country}_{pcode}_{slug}_aoc.geojson
store: fim_store/{country}/{store}
products_root: outputs/{country}/fim_{pcode}_{slug}

member:
  template: "tmp_output_crest_scampr_{{qpf}}"

trigger:
  threshold: 1.0
  sources:
    - template: "tmp_output_crest_scampr_{{qpf}}"

hazards:
  pluvial:
    enabled: true
    grid: qpe_accum
    band: [0.9, 1.2]
    band_wide: [0.8, 1.3]
  fluvial:
    enabled: false

thresholds_m: [0.10, 0.30, 0.70, 1.00]

overbank:
  enabled: true
  reference_scenario: {ref_scenario}

sampling:
  expand_steps_km: [0, 2, 5, 10, 15]
  min_valid_cells: 25

likelihood_bands:
  very_low: [0.0, 0.2]
  low: [0.2, 0.4]
  medium: [0.4, 0.6]
  high: [0.6, 1.01]
"""


def run_job(job, out_root, build_store, only=None, unit_filter=None, log=print):
    country = job["country"]
    units = read_units(job["shapefile"], job.get("name_field", "ADM1_EN"),
                       job.get("pcode_field", "ADM1_PCODE"))
    grids = {isl: island_grid(cfg["dmax_dir"]) for isl, cfg in job["islands"].items()}
    for g in grids.values():
        if g["crs"] in ("None", "", None):
            g["crs"] = WGS84
    assign = {u["pcode"]: unit_island(u, grids) for u in units}
    log(f"{country}: {len(units)} ADM1 units; island assignment: "
        + ", ".join(f"{u['pcode']}->{assign[u['pcode']] or 'NONE (skip)'}" for u in units))

    store_dir = os.path.join(out_root, "fim_store", country)
    cfg_dir = os.path.join(out_root, "fim_config")
    aoc_dir = os.path.join(cfg_dir, "aoc")
    for d in (store_dir, cfg_dir, aoc_dir):
        os.makedirs(d, exist_ok=True)

    mags_path = os.path.join(out_root, f"magnitudes_{country}_per_unit.json")
    if only != "stores" and not os.path.exists(mags_path):
        all_mags, all_domain = {}, {}
        for isl, cfg in job["islands"].items():
            on_isl = [u for u in units if assign[u["pcode"]] == isl]
            if not on_isl:
                continue
            log(f"{country}/{isl}: rain totals for {len(on_isl)} units ...")
            per_unit, domain = storm_totals_and_unit_means(
                cfg["pcpout_dir"], on_isl, grids[isl], log)
            all_mags[isl] = per_unit
            all_domain[isl] = domain
        json.dump({"per_unit": all_mags, "domain": all_domain}, open(mags_path, "w"))
        log(f"{country}: magnitudes written -> {mags_path}")
    if only == "magnitudes":
        return

    data = json.load(open(mags_path))
    manifest = []
    for u in units:
        isl = assign[u["pcode"]]
        if unit_filter and u["pcode"] not in unit_filter:
            continue
        if isl is None:
            log(f"  {u['pcode']} {u['name']}: outside every island model, SKIPPED")
            manifest.append([u["pcode"], u["name"], "NOT COVERED", "", "", ""])
            continue
        mags = data["per_unit"][isl]
        store_name, _zip = build_unit_store(
            u, isl, grids[isl], job["islands"][isl]["dmax_dir"], mags,
            store_dir, os.path.join(out_root, "_tmp_crops"), country,
            build_store, log)
        vals = sorted((mags[sid][u["pcode"]], sid) for sid in mags)
        ref = vals[0][1]  # smallest-rain scenario for the overbank mask
        slug = slugify(u["name"])
        aoc = dict(AOC_TEMPLATE)
        aoc["features"] = [{"type": "Feature",
                            "properties": {"country": country, "pcode": u["pcode"],
                                           "name": u["name"]},
                            "geometry": u["geom"]}]
        aoc_name = f"{country}_{u['pcode']}_{slug}_aoc.geojson"
        json.dump(aoc, open(os.path.join(aoc_dir, aoc_name), "w"))
        yml = YAML_TEMPLATE.format(country=country, name=u["name"], pcode=u["pcode"],
                                   slug=slug, island=isl, store=store_name,
                                   ref_scenario=ref)
        open(os.path.join(cfg_dir, f"{country}_{slug}.yaml"), "w").write(yml)
        mvals = [mags[sid][u["pcode"]] for sid in mags]
        manifest.append([u["pcode"], u["name"], isl, store_name,
                         f"{min(mvals):.1f}", f"{max(mvals):.1f}"])
    with open(os.path.join(out_root, f"manifest_{country}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pcode", "name", "island_model", "store", "magnitude_min_mm",
                    "magnitude_max_mm"])
        w.writerows(manifest)
    log(f"{country}: done, manifest written")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--fimutils", required=True,
                    help="folder that contains tito_utils/ (for the store builder)")
    ap.add_argument("--out", default="out_stores")
    ap.add_argument("--only-magnitudes", action="store_true")
    ap.add_argument("--only-stores", action="store_true")
    ap.add_argument("--units", default="",
                    help="comma separated ADM1 pcodes to build (default: all)")
    a = ap.parse_args()
    sys.path.insert(0, a.fimutils)
    from tito_utils.fim_utils.store import build_store
    jobs = json.load(open(a.jobs))
    only = "magnitudes" if a.only_magnitudes else ("stores" if a.only_stores else None)
    unit_filter = set(x for x in a.units.split(",") if x) or None
    for job in jobs:
        run_job(job, a.out, build_store, only=only, unit_filter=unit_filter)


JOBS_EXAMPLE = [
    {
        "country": "Antigua",
        "shapefile": "shp/atg/atg_admbnda_adm1_2019.shp",
        "islands": {
            "antigua": {"dmax_dir": "tifs/antigua", "pcpout_dir": "tifs/antigua"},
            "barbuda": {"dmax_dir": "tifs/barbuda", "pcpout_dir": "tifs/barbuda"},
        },
    },
]

if __name__ == "__main__":
    main()
