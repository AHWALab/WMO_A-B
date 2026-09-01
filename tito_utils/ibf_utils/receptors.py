"""Receptor preparation: preload -> clip -> tag -> cache.

The national Overture GeoPackage (buildings + roads, prepared once with
the team's scripts_to_preload utilities or overturemaps) is the preload;
per FIM domain this module extracts only the intersecting features with
a bbox-filtered read (pyogrio), so the 6+ GB country file is never
loaded whole. The clipped, tagged receptor base is cached on disk and
reused by every forecast cycle over the same domain.

Tagging reproduces the IBFv1.0 script semantics:
    residential_class      GHS-BUILT-C FUN majority class under the feature
                           (0 non-built, 1 residential, 2 non-residential)
    admin id/name/pop      largest-overlap administrative unit
    population_per_building  dasymetric: admin census total shared by
                           weighted footprint area (subtype weight where
                           Overture subtype exists, else land-class weight)
    critical               subtype in receptors.critical_subtypes
Also fixed here: the IBFv1.0 preload branch tested and read the literal
strings "buildings_gpkg_path"/"buildings_gpkg_layer" instead of the
variables, so the preloaded file could never actually be used and the
script silently fell back to a live Overture download.
"""

import hashlib
import json
import os
import shutil
import tempfile

import numpy as np
import pandas as pd

try:
    import geopandas as gpd
except ImportError as exc:  # pragma: no cover
    raise ImportError("ibf_utils requires geopandas (see tito_env.yml)") from exc

from .config import resolve
from .sampling import sample_raster


def write_gpkg_layers(path, layers):
    """Write named GeoDataFrame layers to one GeoPackage.

    SQLite/GPKG ``Failed to start transaction`` is common on NFS and Docker
    bind mounts. Build the file in local temp, then copy the finished gpkg.
    """
    dest = os.path.abspath(path)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".gpkg")
    os.close(fd)
    os.remove(tmp)
    extras = (tmp + "-wal", tmp + "-shm", tmp + "-journal")
    try:
        os.environ.setdefault("OGR_SQLITE_JOURNAL", "DELETE")
        first = True
        for name, gdf in layers:
            kwargs = {"layer": name, "driver": "GPKG"}
            if not first:
                kwargs["mode"] = "a"
            gdf.to_file(tmp, **kwargs)
            first = False
        if os.path.exists(dest):
            os.remove(dest)
        shutil.copy2(tmp, dest)
    finally:
        for p in (tmp,) + extras:
            if os.path.exists(p):
                os.remove(p)


def _cache_key(cfg, domain) -> str:
    rec = cfg["receptors"]
    parts = []
    for src in (rec["buildings"], rec["roads"], rec["admin"]):
        path = resolve(cfg, src["source"])
        try:
            stat = os.stat(path)
            parts.append(f"{path}:{stat.st_size}:{int(stat.st_mtime)}")
        except OSError:
            parts.append(path)
    parts.append(",".join(f"{b:.1f}" for b in domain.bounds))
    parts.append(str(domain.crs))
    parts.append(str(rec["domain_buffer_m"]))
    parts.append(rec.get("work_crs") or "domain")
    # cache format salt: v2 = domain buffer applied after reprojection
    # (meters, not domain units); old country-wide caches must not be reused
    parts.append("cachev2")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def _read_clip(cfg, spec, work_crs, geom_types, clip_poly):
    """bbox-filtered read of one national layer, reprojected to work_crs
    and subset to clip_poly (a polygon in work_crs)."""
    path = resolve(cfg, spec["source"])
    kwargs = {"layer": spec["layer"]} if spec.get("layer") else {}
    src_crs = gpd.read_file(path, rows=1, **kwargs).crs
    minx, miny, maxx, maxy = clip_poly.bounds
    if src_crs is not None:
        from pyproj import CRS, Transformer
        tr = Transformer.from_crs(CRS.from_user_input(work_crs),
                                  CRS.from_user_input(src_crs), always_xy=True)
        bbox = tr.transform_bounds(minx, miny, maxx, maxy)
    else:
        bbox = (minx, miny, maxx, maxy)
    gdf = gpd.read_file(path, bbox=bbox, **kwargs)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    gdf = gdf[gdf.geometry.geom_type.isin(geom_types)].copy()
    gdf = gdf.to_crs(work_crs)
    gdf = gdf[gdf.intersects(clip_poly)].copy()
    id_field = spec.get("id_field", "id")
    if id_field in gdf.columns:
        gdf["feature_id"] = gdf[id_field].astype(str)
    else:
        gdf["feature_id"] = gdf.index.astype(str)
    return gdf


def _assign_admin(gdf, admin, fields):
    """Largest-overlap admin attributes (area for polygons, length for lines)."""
    if len(gdf) == 0:
        for f in fields:
            gdf[f] = pd.Series(dtype=admin[f].dtype if f in admin else "object")
        return gdf
    inter = gpd.overlay(
        gdf[["feature_id", "geometry"]],
        admin[fields + ["geometry"]],
        how="intersection", keep_geom_type=False)
    if len(inter) == 0:
        for f in fields:
            gdf[f] = np.nan
        return gdf
    measure = np.where(
        inter.geometry.geom_type.isin(["LineString", "MultiLineString"]),
        inter.geometry.length, inter.geometry.area)
    inter["_m"] = measure
    best = inter.sort_values("_m").groupby("feature_id").tail(1)
    gdf = gdf.merge(best[["feature_id"] + fields], on="feature_id", how="left")
    return gdf


def _dasymetric_population(bldgs, cfg, admin_id, admin_pop):
    """IBFv1.0 population allocation, unchanged semantics."""
    rec = cfg["receptors"]
    land_w = {int(k): float(v) for k, v in rec["land_class_weights"].items()}
    sub_w = {str(k).lower(): float(v) for k, v in rec["subtype_weights"].items()}

    b = bldgs
    b["building_area_m2"] = b.geometry.area.round(2)
    land_class_weight = (b["residential_class"].map(land_w)).fillna(0.0)
    subtype = b["subtype"].astype("string").str.lower()
    subtype_weight = pd.Series(np.nan, index=b.index, dtype="float64")
    has_sub = subtype.notna()
    subtype_weight[has_sub] = subtype[has_sub].map(sub_w).fillna(0.0).astype("float64")
    b["final_area_weight"] = subtype_weight.fillna(land_class_weight)

    weighted = b["building_area_m2"] * b["final_area_weight"]
    total_w = weighted.groupby(b[admin_id]).transform("sum")
    b["population_per_building"] = np.where(
        total_w > 0, b[admin_pop] * weighted / total_w, 0.0).round(2)
    return b


def prepare_receptors(cfg, domain, rebuild: bool = False, verbose: bool = True):
    """Return dict(buildings=, roads=, admin=) for the FIM domain, cached.

    The cache key covers source paths/sizes/mtimes, domain bounds/CRS,
    buffer and work CRS: any change rebuilds, otherwise the cached
    GeoPackage is read back in a few seconds.
    """
    log = print if verbose else (lambda *a, **k: None)
    rec = cfg["receptors"]
    work_crs = rec.get("work_crs") or domain.crs
    cfg["_domain_poly_work"] = domain.polygon_in(work_crs, rec["domain_buffer_m"])

    cache_dir = resolve(cfg, rec["cache_dir"])
    os.makedirs(cache_dir, exist_ok=True)
    key = _cache_key(cfg, domain)
    cache_gpkg = os.path.join(cache_dir, f"receptors_{cfg['region']}_{key}.gpkg")
    manifest_path = cache_gpkg.replace(".gpkg", ".json")

    if os.path.isfile(cache_gpkg) and not rebuild:
        log(f"    receptors: cache hit {os.path.basename(cache_gpkg)}")
        return {name: gpd.read_file(cache_gpkg, layer=name)
                for name in ("buildings", "roads", "admin")}, manifest_path

    log("    receptors: building cache (bbox-filtered read of national layers)")
    adm_spec = rec["admin"]
    admin = gpd.read_file(resolve(cfg, adm_spec["source"]),
                          **({"layer": adm_spec["layer"]} if adm_spec.get("layer") else {}))
    admin = admin.to_crs(work_crs)
    admin = admin[admin.intersects(cfg["_domain_poly_work"])].copy()
    admin_id = adm_spec["id_field"]
    admin_pop = adm_spec["population_field"]
    admin_name = adm_spec.get("name_field") or None
    keep = [admin_id, admin_pop] + ([admin_name] if admin_name else [])
    admin = admin[keep + ["geometry"]].copy()

    # Dasymetric weights and admin baselines must span each unit's FULL
    # receptor stock, not just the FIM window; otherwise the unit's census
    # population is shared among window buildings only and per-building
    # occupancy inflates (the defect of the first Guatemala test data).
    # So the one-time cache read covers the intersecting admin units,
    # while the cached receptor layers keep only the FIM window subset.
    admin_union = admin.geometry.union_all()
    bldgs = _read_clip(cfg, rec["buildings"], work_crs,
                       ["Polygon", "MultiPolygon"], admin_union)
    roads = _read_clip(cfg, rec["roads"], work_crs,
                       ["LineString", "MultiLineString"], admin_union)
    sub_field = rec["buildings"]["subtype_field"]
    bldgs["subtype"] = bldgs[sub_field] if sub_field in bldgs.columns else pd.NA

    cls_field = rec["roads"]["class_field"]
    keep_classes = rec["roads"]["keep_classes"]
    if keep_classes and cls_field in roads.columns:
        roads = roads[roads[cls_field].isin(keep_classes)].copy()
    roads["road_class"] = roads[cls_field] if cls_field in roads.columns else pd.NA
    log(f"    receptors: {len(bldgs):,} buildings, {len(roads):,} road segments "
        f"across {len(admin)} admin units (full-unit read)")

    # residential class from the GHS BUILT-C FUN raster (majority under feature)
    lu_path = resolve(cfg, rec["land_use"].get("source", ""))
    if lu_path and os.path.isfile(lu_path):
        bldgs["residential_class"] = sample_raster(bldgs, lu_path, op="mode")
        roads["residential_class"] = sample_raster(roads, lu_path, op="mode")
    else:
        log("    receptors: no land_use raster, residential_class unset")
        bldgs["residential_class"] = np.nan
        roads["residential_class"] = np.nan

    critical = [s.lower() for s in rec["critical_subtypes"]]
    bldgs["critical"] = bldgs["subtype"].astype("string").str.lower().isin(critical)

    fields = [admin_id, admin_pop] + ([admin_name] if admin_name else [])
    bldgs = _assign_admin(bldgs, admin, fields)
    roads = _assign_admin(roads, admin, [admin_id])
    bldgs = _dasymetric_population(bldgs, cfg, admin_id, admin_pop)
    roads["road_length_m"] = roads.geometry.length.round(2)

    # admin baselines from the full-unit sets (IBFv1.0 column names)
    base_b = (bldgs.groupby(admin_id)
                   .agg(total_pop=("population_per_building", "sum"),
                        bldg_count=("feature_id", "count"),
                        bldg_area_m2=("building_area_m2", "sum"))
                   .round(2).reset_index())
    base_r = (roads.groupby(admin_id)
                   .agg(rd_len_m=("road_length_m", "sum"))
                   .round(2).reset_index())
    res_b = _class_wide(bldgs, admin_id, "residential_class",
                        {"total_pop": ("population_per_building", "sum"),
                         "bldg_count": ("feature_id", "count"),
                         "bldg_area_m2": ("building_area_m2", "sum")})
    res_r = _class_wide(roads, admin_id, "residential_class",
                        {"rd_len_m": ("road_length_m", "sum")})
    for extra in (base_b, base_r, res_b, res_r):
        admin = admin.merge(extra, on=admin_id, how="left")
    num = admin.select_dtypes("number").columns
    admin[num] = admin[num].fillna(0)

    # cache layers: FIM window subset only
    window = cfg["_domain_poly_work"]
    n_full_b, n_full_r = len(bldgs), len(roads)
    bldgs = bldgs[bldgs.intersects(window)].copy()
    roads = roads[roads.intersects(window)].copy()
    log(f"    receptors: window subset {len(bldgs):,}/{n_full_b:,} buildings, "
        f"{len(roads):,}/{n_full_r:,} road segments")

    keep_b = ["feature_id", "subtype", "critical", "residential_class",
              admin_id] + ([admin_name] if admin_name else []) + \
             [admin_pop, "building_area_m2", "final_area_weight",
              "population_per_building", "geometry"]
    keep_r = ["feature_id", "road_class", "residential_class", admin_id,
              "road_length_m", "geometry"]
    bldgs = bldgs[[c for c in keep_b if c in bldgs.columns]]
    roads = roads[[c for c in keep_r if c in roads.columns]]

    write_gpkg_layers(cache_gpkg, (
        ("buildings", bldgs),
        ("roads", roads),
        ("admin", admin),
    ))
    manifest = {
        "region": cfg["region"], "cache_key": key,
        "domain_bounds": list(domain.bounds), "domain_crs": str(domain.crs),
        "work_crs": str(work_crs), "buffer_m": rec["domain_buffer_m"],
        "counts": {"buildings_window": int(len(bldgs)),
                   "roads_window": int(len(roads)),
                   "buildings_full": int(n_full_b), "roads_full": int(n_full_r),
                   "admin": int(len(admin))},
        "sources": {k: resolve(cfg, rec[k]["source"])
                    for k in ("buildings", "roads", "admin")},
    }
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    log(f"    receptors: cached -> {os.path.basename(cache_gpkg)}")
    return {"buildings": bldgs, "roads": roads, "admin": admin}, manifest_path


def _class_wide(df, admin_id, class_col, metrics, classes=(0, 1, 2)):
    """res_{c}_{metric} baseline columns with classes 0/1/2 forced."""
    agg = (df.dropna(subset=[class_col])
             .groupby([admin_id, class_col])
             .agg(**{name: spec for name, spec in metrics.items()})
             .reset_index())
    wide = agg.pivot_table(index=admin_id, columns=class_col,
                           values=list(metrics.keys()),
                           fill_value=0, aggfunc="sum")
    cols = pd.MultiIndex.from_product([list(metrics.keys()), list(classes)])
    wide = wide.reindex(columns=cols, fill_value=0)
    wide.columns = [f"res_{int(c)}_{m}" for m, c in wide.columns]
    return wide.round(2).reset_index()
