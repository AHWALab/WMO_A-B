"""Build the IBF receptor preloads for island countries (static data).

One country = one job. Produces, under --out/<Country>/:

    <slug>_overture_bld_rds.gpkg      layers: buildings (id, subtype, class,
                                      height), roads (id, class); Overture
                                      Maps release data, bbox of the country
    <Country>_adm1_population.gpkg    layer adm1: ADM1_PCODE, ADM1_EN,
                                      population, pop_year, pop_source
    <Country>_GHS_BUILT_C_FUN_E2018_R2023A_54009_10.tif
                                      GHS BUILT-C functional class raster
                                      cropped to the country (Mollweide,
                                      10 m), for the dasymetric weights
    manifest_ibf_<Country>.json       counts, sources, file sizes

Jobs file (JSON list), example entry:

    {"country": "Antigua", "slug": "antigua",
     "shapefile": "islands/shp/atg/atg_admbnda_adm1_2019.shp",
     "bbox": [-62.42, 16.90, -61.60, 17.80],
     "population_csv": "population_adm1_Antigua.csv",
     "buildings_parquet": "raw/atg_buildings.parquet",
     "segments_parquet": "raw/atg_segments.parquet",
     "ghs_tile_zip": "raw/GHS_..._R8_C12.zip"}

If buildings_parquet / segments_parquet are missing the script downloads
them with the overturemaps package (pip install overturemaps). The GHS tile
zips come from the JRC GHSL open data repository (GHS_BUILT_C_FUN_E2018
R2023A, 10 m, Mollweide tiles R8_C12 and R8_C13 for these islands).

Run:  python build_island_ibf_static.py --jobs ibf_jobs.json --out ibf_data
"""

import argparse
import json
import os
import subprocess
import sys
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd

ROAD_KEEP_DEFAULT = ["motorway", "trunk", "primary", "secondary",
                     "tertiary", "residential", "unclassified"]


def log(msg):
    print(msg, flush=True)


def download_overture(bbox, otype, out_parquet):
    cmd = [sys.executable, "-m", "overturemaps", "download",
           f"--bbox={','.join(str(b) for b in bbox)}",
           "-f", "geoparquet", f"--type={otype}", "-o", out_parquet]
    log(f"    overture download {otype} -> {out_parquet}")
    subprocess.run(cmd, check=True)


def build_country(job, out_root):
    country = job["country"]
    slug = job["slug"]
    outdir = os.path.join(out_root, country)
    os.makedirs(outdir, exist_ok=True)
    manifest = {"country": country, "sources": {}, "counts": {}}

    # 1. admin units + census population -----------------------------------
    adm = gpd.read_file(job["shapefile"])
    pop = pd.read_csv(job["population_csv"])
    for col in ("ADM1_PCODE", "population"):
        if col not in pop.columns:
            raise SystemExit(f"population csv missing column {col}")
    adm = adm.merge(pop, on="ADM1_PCODE", how="left", suffixes=("", "_csv"))
    if adm["population"].isna().any():
        missing = adm.loc[adm["population"].isna(), "ADM1_PCODE"].tolist()
        raise SystemExit(f"no population for units: {missing}")
    name_col = "ADM1_EN" if "ADM1_EN" in adm.columns else "ADM1_EN_csv"
    keep = ["ADM1_PCODE", name_col, "population", "pop_year", "pop_source",
            "geometry"]
    adm_out = adm[[c for c in keep if c in adm.columns]].rename(
        columns={name_col: "ADM1_EN"})
    adm_out["population"] = adm_out["population"].astype(float)
    adm_gpkg = os.path.join(outdir, f"{country}_adm1_population.gpkg")
    if os.path.exists(adm_gpkg):
        os.remove(adm_gpkg)
    adm_out.to_file(adm_gpkg, layer="adm1", driver="GPKG")
    manifest["sources"]["admin"] = {
        "boundaries": os.path.basename(job["shapefile"]) + " (COD-AB 2019)",
        "population_csv": os.path.basename(job["population_csv"])}
    manifest["counts"]["adm1_units"] = int(len(adm_out))
    manifest["counts"]["population_total"] = float(adm_out["population"].sum())
    log(f"  {country}: admin {len(adm_out)} units, "
        f"population {adm_out['population'].sum():,.0f} -> {adm_gpkg}")

    # 2. Overture buildings + roads ----------------------------------------
    bldg_pq = job.get("buildings_parquet")
    seg_pq = job.get("segments_parquet")
    if not (bldg_pq and os.path.isfile(bldg_pq)):
        bldg_pq = os.path.join(outdir, f"{slug}_buildings.parquet")
        download_overture(job["bbox"], "building", bldg_pq)
    if not (seg_pq and os.path.isfile(seg_pq)):
        seg_pq = os.path.join(outdir, f"{slug}_segments.parquet")
        download_overture(job["bbox"], "segment", seg_pq)

    bld = gpd.read_parquet(bldg_pq, columns=["id", "subtype", "class",
                                             "height", "geometry"])
    bld = bld[bld.geometry.notna() & ~bld.geometry.is_empty].copy()
    seg = gpd.read_parquet(seg_pq, columns=["id", "subtype", "class",
                                            "geometry"])
    roads = seg[(seg["subtype"] == "road") & seg.geometry.notna()
                & ~seg.geometry.is_empty][["id", "class", "geometry"]].copy()

    ov_gpkg = os.path.join(outdir, f"{slug}_overture_bld_rds.gpkg")
    if os.path.exists(ov_gpkg):
        os.remove(ov_gpkg)
    bld.to_file(ov_gpkg, layer="buildings", driver="GPKG")
    roads.to_file(ov_gpkg, layer="roads", driver="GPKG")
    n_sub = int(bld["subtype"].notna().sum())
    manifest["sources"]["overture"] = {
        "release": "Overture Maps (overturemaps package download)",
        "bbox": job["bbox"]}
    manifest["counts"].update({
        "buildings": int(len(bld)),
        "buildings_with_subtype": n_sub,
        "road_segments": int(len(roads)),
        "road_class_top": {k: int(v) for k, v in
                           roads["class"].value_counts().head(8).items()}})
    log(f"  {country}: {len(bld):,} buildings ({n_sub:,} with subtype), "
        f"{len(roads):,} road segments -> {ov_gpkg}")

    # 3. GHS BUILT-C crop ----------------------------------------------------
    # A country can straddle a tile boundary, so ghs_tile_zips is a LIST;
    # the tiles are merged before cropping (Antigua needs R7_C12 + R7_C13).
    ghs_zips = job.get("ghs_tile_zips") or (
        [job["ghs_tile_zip"]] if job.get("ghs_tile_zip") else [])
    ghs_zips = [z for z in ghs_zips if os.path.isfile(z)]
    ghs_out = os.path.join(
        outdir, f"{country}_GHS_BUILT_C_FUN_E2018_R2023A_54009_10.tif")
    if ghs_zips:
        import rasterio
        from rasterio.mask import mask as rio_mask
        from rasterio.merge import merge as rio_merge
        tmp_tifs = []
        for i, ghs_zip in enumerate(ghs_zips):
            with zipfile.ZipFile(ghs_zip) as z:
                member = [n for n in z.namelist()
                          if n.lower().endswith(".tif")][0]
                tmp = os.path.join(outdir, f"_ghs_tile_tmp{i}.tif")
                with z.open(member) as src, open(tmp, "wb") as dst:
                    dst.write(src.read())
                tmp_tifs.append(tmp)
        srcs = [rasterio.open(t) for t in tmp_tifs]
        try:
            geoms = adm_out.to_crs(srcs[0].crs).buffer(1000.0)
            shapes = [g.__geo_interface__ for g in geoms.values
                      if not g.is_empty]
            minx, miny, maxx, maxy = gpd.GeoSeries(
                geoms, crs=srcs[0].crs).total_bounds
            if len(srcs) > 1:
                data, transform = rio_merge(
                    srcs, bounds=(minx, miny, maxx, maxy),
                    nodata=srcs[0].nodata)
                profile = srcs[0].profile.copy()
                profile.update(height=data.shape[1], width=data.shape[2],
                               transform=transform)
                merged = os.path.join(outdir, "_ghs_merged_tmp.tif")
                with rasterio.open(merged, "w", **profile) as dst:
                    dst.write(data)
                tmp_tifs.append(merged)
                mask_src = rasterio.open(merged)
            else:
                mask_src = srcs[0]
            data, transform = rio_mask(mask_src, shapes, crop=True,
                                       all_touched=True,
                                       nodata=mask_src.nodata)
            profile = mask_src.profile.copy()
            if mask_src is not srcs[0] or len(srcs) > 1:
                mask_src.close()
        finally:
            for s in srcs:
                try:
                    s.close()
                except Exception:
                    pass
        profile.update(height=data.shape[1], width=data.shape[2],
                       transform=transform, compress="LZW", tiled=False)
        with rasterio.open(ghs_out, "w", **profile) as dst:
            dst.write(data)
        for t in tmp_tifs:
            if os.path.isfile(t):
                os.remove(t)
        vals, counts = np.unique(data, return_counts=True)
        manifest["sources"]["land_use"] = {
            "product": "GHS_BUILT_C_FUN_E2018_GLOBE_R2023A_54009_10 V1-0",
            "tile_zips": [os.path.basename(z) for z in ghs_zips]}
        manifest["counts"]["ghs_value_histogram"] = {
            str(int(v)): int(c) for v, c in zip(vals, counts)}
        log(f"  {country}: GHS BUILT-C crop {data.shape[2]}x{data.shape[1]} "
            f"-> {ghs_out} ({os.path.getsize(ghs_out)/1e6:.1f} MB)")
    else:
        log(f"  {country}: no GHS tile zips given, land_use crop skipped")

    manifest["files"] = {os.path.basename(p): os.path.getsize(p)
                         for p in (adm_gpkg, ov_gpkg, ghs_out)
                         if os.path.isfile(p)}
    mpath = os.path.join(outdir, f"manifest_ibf_{country}.json")
    with open(mpath, "w") as fh:
        json.dump(manifest, fh, indent=2)
    log(f"  {country}: manifest -> {mpath}")
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--out", default="ibf_data")
    a = ap.parse_args()
    with open(a.jobs) as fh:
        jobs = json.load(fh)
    for job in jobs:
        log(f"== {job['country']} ==")
        build_country(job, a.out)


if __name__ == "__main__":
    main()
