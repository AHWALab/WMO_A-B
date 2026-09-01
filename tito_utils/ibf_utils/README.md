# ibf_utils

Impact-based forecasting (IBF) receptor layer for TITO. Consumes the
probabilistic FIM products of `fim_utils` and produces receptor-level
warning products: buildings, roads and administrative units carrying
exceedance probabilities, warning classes from the standard flood risk
matrix, and exposure summaries.

```
fim_utils.pipeline_pf            ibf_utils.pipeline_ibf
  prob_depth_ge_10cm.{cycle}.tif   1. discover products (cycle-aware)
  prob_depth_ge_30cm.{cycle}.tif   2. receptor cache (preload -> clip -> tag)
  prob_depth_ge_70cm.{cycle}.tif   3. sample max probability per feature
        ... (any thresholds)       4. classify: matrix + IBFv1.0 fields
                                   5. ibf_receptors.{cycle}.gpkg + csv + json
```

## Run

```
python -m tito_utils.ibf_utils.pipeline_ibf \
    --config fim_config/ibf/Antigua_SaintGeorge_ibf.yaml \
    [--cycle 20251010.010000] [--products-dir DIR] [--rebuild-cache]
```

One YAML per site (see `fim_config/ibf/`, paired with the FIM site YAML by
name). The first run over a domain builds the receptor cache from the
national preload (bbox-filtered read, a multi GB country GeoPackage is
never loaded whole); every later cycle reuses it. Dense Antigua and Barbuda
units (Saint John's) run in about 2 to 3 minutes cold and under 2 minutes
warm; small units in seconds.

## Static data per country

- Antigua and Barbuda: `ibf_data/Antigua/` IN the repo.
  Overture Maps buildings and roads (layers `buildings`, `roads`), admin
  units with census population (`<Country>_adm1_population.gpkg`, layer
  `adm1`, fields ADM1_PCODE / ADM1_EN / population / pop_year /
  pop_source), and the GHS BUILT-C FUN 10 m crop for the dasymetric
  weights. `manifest_ibf_<Country>.json` records sources and counts;
  `fim_dev/build_island_ibf_static.py` rebuilds everything from scratch
  (Overture download, census CSV merge, GHS tile crop).

In orchestrated runs `tito_hook` chains IBF right after each FIM site and
applies the `ibf_regions` overrides from `Caribbean_Comoros_config.py`
(likelihood cutoff, severity depths, reporting threshold). The user default
for the likelihood cutoff is 0.50; the IBFv1.0 reference runs used 0.30.

## Classification

Every feature gets, per probability product, a `p_ge_{tag}` column, and:

- `risk_class` / `risk_level` / `risk_color`: the flood risk matrix of
  Speight et al. (2018, Fig. 1; SFFS / UK FGS standard). Each severity
  level of the potential-impact axis (config `severity_thresholds_m`,
  project default 0.10 / 0.30 / 0.70 m for every country, the first
  three FIM depth thresholds; IBFv1.0 used 0.76 m) is served by the closest available
  probability grid; that grid's FGS likelihood band (Very Low < 20 %,
  Low 20-40 %, Medium 40-60 %, High > 60 %, 5 % reporting threshold)
  enters the matrix, and the feature keeps the worst cell. Likelihood
  bands are identical to `fim_utils.probability.DEFAULT_BANDS`.
- `hazard_flag`: IBFv1.0 compatibility field, the highest threshold
  whose probability >= `hazard_flag_cutoff` (default 0.3).

Admin units get the IBFv1.0 summary set (`res_*`, `hzrd_*`, `IWF_*`,
`impact_flag`, with the team's published thresholds as defaults) plus
the matrix `risk_class`. Baselines (`total_pop`, `bldg_count`,
`bldg_area_m2`, `rd_len_m`, `res_*`) are computed once per cache over
each unit's FULL receptor stock; only hazard exposure is per-cycle.
The dasymetric population allocation is the IBFv1.0 algorithm unchanged
(Overture subtype weight where present, else GHS BUILT-C land-class
weight, footprint-area share of the admin census total).

## Differences from the original IBFv1.0 receptor script (intentional)

1. Threshold labels are parsed from filenames, never assumed. The v1.0
   script mapped `qpeprob...0.1524 meters` (15.24 cm) to a variable
   named `probability_7p62cm`, and so on: every layer was labelled one
   class shallower than its water depth. Regression-tested here.
2. Files from other cycles in the products folder are skipped and
   reported, not blended (the v1.0 inputs mixed a grid from the
   previous day's cycle, which also made its top hazard class
   unreachable).
3. The preloaded national GeoPackage is actually used. The v1.0 preload
   branch tested the literal strings `"buildings_gpkg_path"` /
   `"buildings_gpkg_layer"` instead of the variables, so it always fell
   back to a live Overture download.
4. Population weights span the full admin unit even when the FIM window
   covers a small part of it, so per-building occupancy stays realistic.
5. Likelihood and severity stay two axes. A single probability cutoff
   turns a 20 % chance of deep flooding into "no hazard"; the matrix
   keeps it visible as a yellow/amber cell.

## Verification against the IBFv1.0 outputs

Re-running this module on the original IBFv1.0 event-1 inputs
(same receptors, same four grids): probability sampling agrees on
95-98 % of buildings (mean absolute difference below 0.02), hazard_flag
on 96.9 % of the 4,689 team-flagged buildings, residential_class on
99.2 %, and dasymetric population correlates at 0.999 with equal sums.
Residual differences are sub-cell edge cases where exactextract's
coverage-fraction cell inclusion and rasterio's all_touched rasterize
pick different border cells; swapping the sampler for exactextract is a
contained change inside `sampling.py` if exact parity is ever required.

## Dependencies

Beyond tito_env: `geopandas` + `pyogrio` (added to `tito_env.yml`).
Not required: exactextract, rioxarray, overturemaps, geoquetzal - the
Overture download and other data-prep utilities remain with the IBF
team's `scripts_to_preload` and run offline, once, to produce the
national preload this module consumes.
