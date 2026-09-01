"""Build a scenario store straight from a delivered flood map library zip.

The deliveries arrive as one big zip per site with the layout

    <Prefix>/sample_0001/MaxVeloc-dept.zip     (MaximumDepth.tif + MaxVelocMagnitude.tif)
    <Prefix>/sample_0001/output-time-maps.zip  (time step maps, huge, not used)
    <Prefix>/sample_0002/...

This script reads ONLY the MaximumDepth member of every sample, without
extracting anything else (the nested zips are stored uncompressed inside
the outer zip, so each depth layer is read directly), quantizes depths to
1 cm, and builds the standard fim_utils zarr store (same layout as
fim_utils.store.build_store, written scenario by scenario so huge grids
never fill memory). Samples whose grid differs from the first one are
reported and skipped. Requires: numpy, rasterio, zarr (pip install).

Magnitudes: pass --magnitudes CSV with columns storm_id,magnitude_mm for
real RainyDay totals. Without it the store gets PLACEHOLDER wet volume
ranks (1..N, marked in magnitude_source) and must not be used
operationally until fim_utils.store.attach_magnitudes replaces them.

Examples:
    python fim_dev/build_store_from_library_zip.py Gris.zip fim_store/Haiti/fim_store_Haiti_Gris_v1.zarr
    python fim_dev/build_store_from_library_zip.py Morales.zip out.zarr --magnitudes mags.csv --zip --split-mb 80
"""

import argparse
import csv
import io
import json
import os
import struct
import zipfile
import zlib

import numpy as np


def data_offset(f, header_offset):
    f.seek(header_offset)
    h = f.read(30)
    assert h[:4] == b"PK\x03\x04", "not a local zip header"
    nlen, elen = struct.unpack("<HH", h[26:30])
    return header_offset + 30 + nlen + elen


def nested_member_span(f, nbase, nsize, suffix):
    tail = min(nsize, 66000)
    f.seek(nbase + nsize - tail)
    t = f.read(tail)
    e = t.rfind(b"PK\x05\x06")
    cd_size, cd_off = struct.unpack("<II", t[e + 12:e + 20])
    f.seek(nbase + cd_off)
    cd = f.read(cd_size)
    p = 0
    while p < len(cd):
        csize, = struct.unpack("<I", cd[p + 20:p + 24])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        hoff, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        if name.endswith(suffix):
            return data_offset(f, nbase + hoff), csize
        p += 46 + nlen + elen + clen
    raise RuntimeError(f"{suffix} not found in nested zip")


def read_depth(outer_path, info):
    with open(outer_path, "rb") as f:
        nbase = data_offset(f, info.header_offset)
        doff, csize = nested_member_span(f, nbase, info.file_size,
                                         "MaximumDepth.tif")
        f.seek(doff)
        comp = f.read(csize)
    return zlib.decompressobj(-15).decompress(comp)


def create_array(group, name, shape, dtype, chunks):
    kwargs = dict(shape=shape, dtype=dtype, chunks=chunks)
    try:
        return group.create_array(name, **kwargs)
    except (AttributeError, TypeError):
        return group.create_dataset(name, **kwargs)


def main():
    import rasterio
    from rasterio.io import MemoryFile
    import zarr

    ap = argparse.ArgumentParser()
    ap.add_argument("library_zip")
    ap.add_argument("out_store")
    ap.add_argument("--magnitudes", help="CSV storm_id,magnitude_mm (real totals)")
    ap.add_argument("--magnitude-column", default="magnitude_mm")
    ap.add_argument("--extent-threshold-m", type=float, default=0.05)
    ap.add_argument("--zip", action="store_true", help="zip the store afterwards")
    ap.add_argument("--split-mb", type=int, default=0,
                    help="split the zip into parts of this many MB")
    a = ap.parse_args()

    z = zipfile.ZipFile(a.library_zip)
    members = sorted(
        (i for i in z.infolist()
         if len(i.filename.split("/")) == 3
         and i.filename.split("/")[1].startswith("sample_")
         and i.filename.split("/")[2] == "MaxVeloc-dept.zip"
         and i.compress_type == 0),
        key=lambda i: i.filename)
    print(f"{len(members)} samples in {a.library_zip}")

    real_mags = None
    if a.magnitudes:
        real_mags = {}
        for r in csv.DictReader(open(a.magnitudes)):
            real_mags[r["storm_id"]] = float(r[a.magnitude_column])

    ref = None
    arrays = {}
    for i in members:
        sample = i.filename.split("/")[1]
        raw = read_depth(a.library_zip, i)
        with MemoryFile(raw) as mf, mf.open() as src:
            sig = (src.width, src.height,
                   tuple(np.round(np.asarray(src.transform)[:6], 9)),
                   str(src.crs))
            arr = src.read(1, masked=True).filled(0.0).astype("float32")
            if ref is None:
                ref = sig
                crs, transform, nodata = str(src.crs), \
                    tuple(np.asarray(src.transform)[:6]), src.nodata
                ny, nx = src.height, src.width
        if sig != ref:
            print(f"  SKIP {sample}: grid {sig[0]}x{sig[1]} differs from "
                  f"{ref[0]}x{ref[1]}")
            continue
        arr[arr < 0] = 0.0
        arr = (np.round(arr * 100.0) / 100.0).astype("float32")
        arrays[sample] = arr
        if len(arrays) % 20 == 0:
            print(f"  ...{len(arrays)} read")

    ids = sorted(arrays)
    if real_mags:
        missing = [s for s in ids if s not in real_mags]
        if missing:
            raise SystemExit(f"magnitudes CSV misses {len(missing)} samples, "
                             f"e.g. {missing[:3]}")
        mags = {s: real_mags[s] for s in ids}
        source = f"real magnitudes from {os.path.basename(a.magnitudes)}"
    else:
        vols = {s: float(arrays[s].sum()) for s in ids}
        order = sorted(vols, key=lambda k: vols[k])
        mags = {s: float(r + 1) for r, s in enumerate(order)}
        source = ("PLACEHOLDER wet-volume RANKS (1..N), NOT millimeters; "
                  "attach real RainyDay totals with "
                  "fim_utils.store.attach_magnitudes before operational use")

    order = sorted(ids, key=lambda s: mags[s])
    n = len(order)
    import shutil
    shutil.rmtree(a.out_store, ignore_errors=True)
    root = zarr.open_group(a.out_store, mode="w")
    depth = create_array(root, "depth", (n, ny, nx), "float32", (1, ny, nx))
    extent = create_array(root, "extent", (n, ny, nx), "uint8", (1, ny, nx))
    for k, s in enumerate(order):
        depth[k] = arrays[s]
        extent[k] = (arrays[s] >= a.extent_threshold_m).astype("uint8")
        arrays[s] = None
    mag_arr = create_array(root, "magnitude_mm", (n,), "float64", (n,))
    mag_arr[:] = np.array([mags[s] for s in order])
    mlen = max(len(s) for s in order)
    sid = create_array(root, "storm_id", (n,), f"S{mlen}", (n,))
    sid[:] = np.array([s.encode() for s in order], dtype=f"S{mlen}")
    root.attrs.update({
        "crs": crs, "transform": list(transform),
        "extent_threshold_m": a.extent_threshold_m,
        "source_nodata": None if nodata is None else float(nodata),
        "magnitude_source": source,
        "n_storms": n, "grid_shape": [ny, nx],
    })
    with open(os.path.join(a.out_store, "index.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["storm_index", "storm_id", "magnitude_mm"])
        for k, s in enumerate(order):
            w.writerow([k, s, round(mags[s], 2)])
    with open(os.path.join(a.out_store, "meta.json"), "w") as fh:
        json.dump(dict(root.attrs), fh, indent=2)
    print(f"store built: {a.out_store} ({n} scenarios, {ny}x{nx}, {crs})")

    if a.zip:
        zp = a.out_store + ".zip"
        if os.path.exists(zp):
            os.remove(zp)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zo:
            for r, _, fs in os.walk(a.out_store):
                for fn in fs:
                    p = os.path.join(r, fn)
                    zo.write(p, os.path.relpath(p, a.out_store))
        print(f"zip: {zp} ({os.path.getsize(zp)/1e6:.1f} MB)")
        if a.split_mb:
            CH = a.split_mb * 1024 * 1024
            with open(zp, "rb") as f:
                k = 0
                while True:
                    d = f.read(CH)
                    if not d:
                        break
                    k += 1
                    open(f"{zp}.part{k:02d}", "wb").write(d)
            print(f"split into {k} parts of <= {a.split_mb} MB")


if __name__ == "__main__":
    main()
