"""Probability-product discovery and receptor sampling.

Discovery understands both product namings:

    prob_depth_ge_{tag}.{cycle}.tif            fim_utils >= 0.4 (tag: 10cm, 76cm, ...)
    qpeprob.{cycle}.{threshold} meters.tif     legacy    (threshold: 0.3048, ...)

The threshold in metres is parsed from the filename and is the single
source of truth. This fixes a real defect found in the IBFv1.0 script,
which hard-mapped ``qpeprob...0.1524 meters`` (15.24 cm, 6 in) to a
variable named ``probability_7p62cm`` and so on for the other grids:
every threshold was labelled one class shallower than the water it
represents. Here the tag is always derived from the parsed value
(0.1524 -> ``15p24cm``), never assumed.

Sampling reads each raster once into memory (FIM windows are small) and
rasterizes every receptor geometry onto its bounding slice: buildings
take the footprint maximum (all_touched retry for sub-cell footprints),
roads the maximum over all touched cells, matching the exactextract
"max" semantics of the IBFv1.0 script without the extra dependency.
"""

import os
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.windows import from_bounds, Window

from .domain import coerce_crs

RE_NEW = re.compile(
    r"^prob_depth_ge_(?P<tag>[0-9p]+)cm(?P<ob>_overbank)?\.(?P<cycle>\d{8}\.\d{6})\.tif$")
RE_LEGACY = re.compile(
    r"^qpeprob\.(?P<cycle>\d{8}\.\d{6})\.(?P<m>[0-9.]+) ?meters\.tif$")


def _tag_from_meters(m: float) -> str:
    cm = m * 100.0
    if abs(cm - round(cm)) < 1e-6:
        return f"{int(round(cm))}cm"
    return f"{cm:.2f}".rstrip("0").rstrip(".").replace(".", "p") + "cm"


@dataclass
class ProbabilityLayer:
    path: str
    threshold_m: float
    tag: str               # canonical, derived from threshold_m
    cycle: str
    overbank: bool = False

    @property
    def field(self) -> str:
        return f"p_ge_{self.tag}"


def parse_probability_filename(name: str):
    """Return ProbabilityLayer (path=name) or None if not a probability grid."""
    base = os.path.basename(name)
    m = RE_NEW.match(base)
    if m:
        meters = float(m.group("tag").replace("p", ".")) / 100.0
        return ProbabilityLayer(name, meters, _tag_from_meters(meters),
                                m.group("cycle"), bool(m.group("ob")))
    m = RE_LEGACY.match(base)
    if m:
        meters = float(m.group("m"))
        return ProbabilityLayer(name, meters, _tag_from_meters(meters),
                                m.group("cycle"), False)
    return None


def discover_probability_products(directory: str, cycle: str = None,
                                  extra_paths=None, prefer_overbank: bool = False):
    """All probability layers in a directory (plus explicit extra paths),
    filtered to one cycle when given, sorted by threshold.

    Returns (layers, skipped): skipped lists files of OTHER cycles so a
    mixed-vintage folder (the second defect found in the IBFv1.0 inputs:
    three grids from one cycle, the fourth from the previous day) is
    surfaced instead of silently blended. Explicit extra_paths are always
    kept, with a flag when their cycle differs.
    """
    found = []
    if directory and os.path.isdir(directory):
        for base in sorted(os.listdir(directory)):
            layer = parse_probability_filename(os.path.join(directory, base))
            if layer:
                found.append(layer)
    for p in (extra_paths or []):
        layer = parse_probability_filename(p)
        if layer:
            layer.explicit = True
            found.append(layer)

    if cycle is None and found:
        cycles = sorted({l.cycle for l in found})
        cycle = cycles[-1]

    layers, skipped = [], []
    for l in found:
        if l.cycle != cycle and not getattr(l, "explicit", False):
            skipped.append(l)
            continue
        layers.append(l)

    if prefer_overbank:
        ob_thresholds = {l.threshold_m for l in layers if l.overbank}
        layers = [l for l in layers
                  if l.overbank or l.threshold_m not in ob_thresholds]
    else:
        layers = [l for l in layers if not l.overbank]

    # one layer per threshold
    seen = {}
    for l in layers:
        seen.setdefault(round(l.threshold_m, 4), l)
    layers = sorted(seen.values(), key=lambda l: l.threshold_m)
    return layers, skipped, cycle


class _InMemoryRaster:
    """Band 1 in memory with NaN nodata, plus a per-geometry reducer."""

    def __init__(self, path: str, bounds=None):
        with rasterio.open(path) as src:
            self.crs = coerce_crs(src.crs)
            if bounds is not None:
                win = from_bounds(*bounds, transform=src.transform)
                win = win.round_offsets().round_lengths().intersection(
                    Window(0, 0, src.width, src.height))
                data = src.read(1, window=win, masked=True)
                self.transform = src.window_transform(win)
            else:
                data = src.read(1, masked=True)
                self.transform = src.transform
            self.data = np.ma.filled(data.astype("float64"), np.nan)

    def reduce(self, geom, op: str, all_touched: bool):
        """max / mode of raster cells under one geometry; NaN if none."""
        rows, cols = self.data.shape
        if rows == 0 or cols == 0 or geom is None or geom.is_empty:
            return np.nan
        minx, miny, maxx, maxy = geom.bounds
        inv = ~self.transform
        c0, r0 = inv * (minx, maxy)
        c1, r1 = inv * (maxx, miny)
        r_lo, r_hi = int(np.floor(min(r0, r1))), int(np.ceil(max(r0, r1)))
        c_lo, c_hi = int(np.floor(min(c0, c1))), int(np.ceil(max(c0, c1)))
        r_lo, c_lo = max(r_lo, 0), max(c_lo, 0)
        r_hi, c_hi = min(r_hi, rows), min(c_hi, cols)
        if r_hi <= r_lo:
            r_hi = min(r_lo + 1, rows)
        if c_hi <= c_lo:
            c_hi = min(c_lo + 1, cols)
        if r_hi <= r_lo or c_hi <= c_lo:
            return np.nan
        window_transform = self.transform * rasterio.Affine.translation(c_lo, r_lo)
        shape = (r_hi - r_lo, c_hi - c_lo)
        mask = rasterize([(geom, 1)], out_shape=shape, transform=window_transform,
                         fill=0, all_touched=all_touched, dtype="uint8")
        if not mask.any() and not all_touched:
            mask = rasterize([(geom, 1)], out_shape=shape,
                             transform=window_transform, fill=0,
                             all_touched=True, dtype="uint8")
        values = self.data[r_lo:r_hi, c_lo:c_hi][mask == 1]
        values = values[~np.isnan(values)]
        if values.size == 0:
            return np.nan
        if op == "max":
            return float(values.max())
        if op == "mode":
            uniq, counts = np.unique(values, return_counts=True)
            return float(uniq[np.argmax(counts)])
        raise ValueError(f"Unknown op '{op}'")


def sample_raster(gdf, raster_path: str, op: str = "max",
                  all_touched: bool = None, bounds_pad_m: float = 100.0):
    """Sample one raster onto every geometry of a GeoDataFrame.

    Geometries are reprojected to the raster CRS; the raster is read once,
    windowed to the receptors' extent. all_touched defaults to True for
    lines and False (with retry) for polygons.
    """
    if len(gdf) == 0:
        return pd.Series(dtype="float64")
    with rasterio.open(raster_path) as src:
        raster_crs = coerce_crs(src.crs)
    geoms = gdf.geometry.to_crs(raster_crs)
    minx, miny, maxx, maxy = geoms.total_bounds
    ras = _InMemoryRaster(raster_path, bounds=(minx - bounds_pad_m, miny - bounds_pad_m,
                                               maxx + bounds_pad_m, maxy + bounds_pad_m))
    line_like = geoms.geom_type.isin(["LineString", "MultiLineString"])
    out = np.full(len(gdf), np.nan)
    for i, (geom, is_line) in enumerate(zip(geoms.values, line_like.values)):
        touched = all_touched if all_touched is not None else bool(is_line)
        out[i] = ras.reduce(geom, op, touched)
    return pd.Series(out, index=gdf.index)


def sample_probabilities(gdf, layers, fill_zero: bool = True):
    """Add one p_ge_{tag} column per probability layer.

    Max over every cell the feature touches (all_touched=True): the
    worst-case reading, and the same semantics as exactextract's "max"
    used by the IBFv1.0 script, verified feature-by-feature against its
    Guatemala outputs.
    """
    out = gdf.copy()
    for layer in layers:
        vals = sample_raster(out, layer.path, op="max", all_touched=True)
        if fill_zero:
            vals = vals.fillna(0.0)
        out[layer.field] = vals.round(4)
    return out
