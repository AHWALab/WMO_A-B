# fim_store — Antigua and Barbuda (30 m)

Layout: `fim_store/Antigua/` (region key `Antigua` in
`Caribbean_Comoros_config.py`).

    Antigua/     READY: 7 stores, one per ADM1 unit of Antigua and Barbuda
                 (Barbuda AG01 + six Antigua parishes; Redonda has no store)

Each store is a zarr library of pre-simulated flood maps (max depth and
extent) with rainfall magnitudes for pluvial matching.

Stores ship as `<name>.zarr.zip` (plain git, no LFS). After clone or pull:

    python fim_store/unzip_stores.py Antigua

Do not walk other country `.zarr` trees; pass `Antigua` so NFS does not stall.

Unzipped `.zarr` folders stay out of git. Rebuild island stores with
`fim_dev/build_admin_stores.py`. See `fim_store/Antigua/README_Antigua.md`.
