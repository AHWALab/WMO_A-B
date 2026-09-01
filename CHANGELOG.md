# Changelog

All notable changes to TITO Caribbean and Comoros will be documented here.

---

## [1.8.0] - 2026-08-26 - Haiti live: 200 scenarios at both sites, real rain and real discharge, lighter stores

### Fixed

- THE REPORTED PROBLEM. `fim_store/Haiti/*`: the Haiti stores carried
  PLACEHOLDER rank magnitudes and both site YAMLs were switched off, which
  is why `magnitude_mm` came back empty in the Haiti magnitude tables and
  why `fim_config/Haiti_Gris.yaml` and `fim_config/Haiti_LaQuinte.yaml`
  looked disabled. The first rain delivery for Haiti consisted of symlink
  stubs, so no real storm totals existed at the time. The second delivery
  is complete and real, and both stores are rebuilt from it with REAL
  magnitudes and REAL boundary discharge for every scenario, both sites
  active.

### Changed

- Both Haiti stores hold ALL 200 scenarios and BOTH indexes
  (`magnitude_mm` and `fluvial_q`), so each site runs pluvial, fluvial and
  combined exactly like the Guatemala basins. Gris matches on one gauge
  (cuenca_griss), La Quinte on two (cuenca_laquinta_1 and _2),
  standardized nearest neighbour. No gauge is nan anywhere.
- GRIS PLACEHOLDERS. Only 50 of the 200 Gris flood maps were delivered
  (samples 0001 to 0050). By request the store still carries all 200
  scenarios: the 150 undelivered ones hold a COPY of `sample_0004`'s map
  (a real delivered Gris flood, 2.63 m max depth) as a stand in, listed in
  the store attribute `placeholder_scenarios` and marked in `index.csv`
  and `magnitudes_Gris.csv`. Their magnitudes and discharges are real;
  only the map is borrowed. Products matched to those scenarios show
  sample_0004's flooding until the real maps arrive; rerunning
  `fim_dev/build_haiti_stores.py` then replaces them with nothing else to
  change.
- STORE FORMAT: depth is now stored as uint16 CENTIMETRES with a
  `depth_scale` attribute (0.01) and zstd level 19.
  `FimStore.depth()` applies the scale, so every consumer keeps seeing
  float32 metres. The data was at 1 cm precision either way, so this is
  lossless relative to the previous format and about a third smaller:
  La Quinte 458 MB instead of 671, Gris 117 MB for 200 scenarios instead
  of 138 for 50. Older stores without the attribute read exactly as
  before; verified by rerunning the Comoros and Morales end to end
  harnesses against their existing float32 stores through the patched
  reader.
- `fim_store/Haiti/fim_store_Haiti_LaQuinte_v1.zarr.zip`: rebuilt with ALL
  200 scenarios (was 143). `sample_0011` is no longer excluded: it arrived
  on a 1367 x 1423 grid at 5 m rather than 3417 x 3557 at 2 m, but with
  the same origin and the same extent, so it is a coarser rendering of the
  same domain and is resampled nearest neighbour onto the 2 m site grid
  (recorded in the store's `resampling_notes` attribute).
- `fim_config/Haiti_Gris.yaml`, `fim_config/Haiti_LaQuinte.yaml`: active,
  both hazards on, overbank references on the lowest rain magnitude
  scenario of each library, delivery and placeholder notes in the headers.
- `Caribbean_Comoros_config.py`: `fim_regions["Haiti"]` switched on.
  `ibf_regions["Haiti"]` stays off, there is no receptor data for Haiti.
- `tito_utils/fim_utils/store.py`: `FimStore.depth()` understands the
  `depth_scale` attribute (see STORE FORMAT above); everything else in the
  module is unchanged.
- `fim_store/Haiti/README_Haiti.md`, `README_FIM.md`: current status.

### Added

- `fim_store/Haiti/magnitudes_Gris.csv` and `magnitudes_LaQuinte.csv`: the
  full 200 row tables with `storm_id`, `scenario_name`, `magnitude_mm`
  (filled), `map_status` (real or placeholder) and the per gauge maximum
  discharge. They replace the old `magnitude_template_*.csv` files, whose
  magnitude column was empty by design.
- `fim_dev/build_haiti_stores.py`, `fim_dev/verify_haiti.py`,
  `fim_dev/run_haiti_e2e.py`: builder, independent verifier and end to end
  harness. The builder reads ONLY `MaximumDepth.tif` out of each sample's
  nested `MaxVeloc-dept.zip` by byte range, so the 457 GB of Haiti
  deliveries were never extracted; 11.2 GB of depth members were read and
  nothing else was touched.
- `fim_dev/haiti_extract/`: the byte range extraction scripts (standard
  library plus numpy) plus `make_archive.py`, which wrote the kept maximum
  depth layers as compact georeferenced GeoTIFFs (uint16 centimetres,
  deflate, verified pixel exact against the deliveries) so the two
  delivery zips could be deleted locally. The originals remain on
  ownCloud.

### Data issues found in the deliveries, please read

- GRIS COVERAGE. 50 of the nominal 200 samples were delivered (0001 to
  0050). See GRIS PLACEHOLDERS above for how the store bridges the gap
  until the rest arrive.
- Four of the 50 delivered Gris maps (samples 0003, 0010, 0027, 0028) are
  byte identical and fully dry.
- Two pairs of La Quinte maps are byte identical in the delivery, 0002
  with 0007 and 0181 with 0182.
- Maximum depths reach 16.9 m at Gris and 12.6 m at La Quinte. Those are
  the delivered values, carried through unchanged, but they are worth a
  sanity check on the modelling side.

---

## [1.7.0] - 2026-08-23 - Comoros live on 55 municipalities, Barbados on the real rain, Morales fluvial

### Added

- `fim_store/Comoros/`: 55 scenario stores, one per ADM3 municipality, the
  whole country. Grande Comore 29 units, Anjouan 20, Moheli 6; 200
  scenarios each, max depth in metres on the island model grid (EPSG:5629,
  30.57 m) at 1 cm precision, extent mask at 0.05 m. Which rain box feeds
  which island was established from the geographic bounds of the delivered
  grids rather than from the folder names: NE_No1 is Grande Comore,
  SE_No3 is Anjouan, SW_No2 is Moheli, and each island grid sits fully
  inside its box. Magnitudes are REAL RainyDay storm totals, the area
  weighted mean of the band summed scenario rain over each municipality
  polygon, coverage 99.21 to 100.59 percent of the unit area.
- `fim_config/Comoros_<Unit>.yaml` and `fim_config/aoc/Comoros_*.geojson`:
  55 site configs and their areas of concern, pluvial only, standard
  thresholds, overbank reference set to the driest scenario of each unit.
- `tito_utils/fim_utils/fluvial.py`: the Morales fluvial index, and with
  it the second Guatemala site that matches on boundary discharge.
- `fim_dev/build_comoros_stores.py` and `fim_dev/verify_comoros.py`: the
  builder and the independent verifier used for this release. The verifier
  goes back to the sources (depth rasters, reference grids, shapefile,
  magnitude tables) and re-derives every window, order and value rather
  than trusting the builder.
- `fim_store/Barbados/magnitudes_Barbados_pcpout_vs_rain.csv`: both
  magnitude columns per scenario and unit, kept for provenance.

### Changed

- `fim_store/Barbados/`: all 11 parish stores are re-indexed on the REAL
  RainyDay scenario rain geotiffs, area weighted per parish, the same
  treatment Antigua and Barbuda received in v1.6.0 and for the same
  reason: the real time side matches against QPE and QPF rainfall, so the
  store index must be source rainfall rather than the hydrodynamic model's
  internal applied field. The previous pcpout magnitudes agree closely
  (correlation 1.000 per parish, median difference 1.80 to 2.16 percent,
  largest single difference 27 mm on totals of hundreds of mm), which is a
  mutual validation of both products. Coverage of the parish area by the
  weighted rain cells is 99.79 to 100.42 percent.
- `fim_store/Guatemala/fim_store_Morales_v1.zarr.zip`: the fluvial index is
  attached, so Morales now runs pluvial, fluvial and combined like Santa
  Ines Petapa. The index is the per scenario maximum CREST discharge from
  GUATEMALA_outputs_Q_MOTAGUA.zip, matched standardized nearest neighbour.
  The store now ships as five parts instead of two, all under 20 MB.
- `fim_config/Guatemala_Morales.yaml`: fluvial enabled, with the gauge data
  issue documented at the top of the file.
- `Caribbean_Comoros_config.py`: `fim_regions["Comoros"]` switched on and
  the Guatemala and Barbados comments brought up to date. Nothing else in
  the file changed; `ibf_regions["Comoros"]` stays off because there is no
  receptor data for the country yet.
- `fim_store/Barbados/README_Barbados.md`,
  `fim_store/Guatemala/README_Guatemala.md`, `README_FIM.md`: current
  status of the three countries touched here.

### Fixed

- `tito_utils/fim_utils/fluvial.py`, `member_boundary_q`: the boundary
  discharge of a member was reduced with a plain `max()` over the CREST
  series. EF5 writes `nan` while the routing warms up, and `max()` keeps
  the FIRST element when every comparison is False, so a series that
  starts with `nan` returned `nan` for the whole member, `FluvialMatcher`
  flagged it `missing_discharge` and the member silently lost its fluvial
  map. The reduction is now nan aware, and a gauge that is nan from end to
  end is reported with an `all_nan_<file>` flag instead of passing a quiet
  nan downstream. Found on the real Morales delivery, where the warm up
  rows are present in every series.

### Data issues found in the deliveries, please read

- `ts.cuenca_motagua_1.crest.csv` (Morales fluvial delivery) carries NO
  discharge at all: Discharge, SM, Fast Flow and Slow Flow are nan on
  every row of all 200 scenarios, only Precip and PET are filled. That is
  the signature of a gauge point outside the routed basin. The fluvial
  index is therefore built on `cuenca_motagua_2` alone and the site YAML
  lists only that gauge; listing a gauge that returns nan would make the
  matcher discard the member. When the modelling side fixes gauge 1 the
  index can be rebuilt with both columns.
- Mledjele (KM331, Moheli) also administers the Nioumachoua islets off the
  south coast, which lie outside the Moheli hydrodynamic model domain, so
  4.89 km2 of the commune's 37.17 km2 has no depth data (86.85 percent
  covered). The commune's mainland, 31.90 km2, is fully covered. The note
  is repeated at the top of its site YAML and in `README_Comoros.md`.
  Every other municipality of the country is covered in full.
- Two delivered Comoros depth rasters, `max_moheli_59_rev.tif` and
  `max_anjouan_108pr.tif`, are byte identical duplicates of scenarios
  already present; they were skipped rather than counted twice.

---

## [1.6.0] - 2026-08-23 - Antigua and Barbuda indexed on the real RainyDay rain, Barbuda area of concern fixed

### Changed

- `fim_store/Antigua/`: all seven unit stores are re-indexed on the REAL
  RainyDay scenario rain geotiffs delivered for the two island models
  (200 scenarios each, 72 bands, 0.027 degree grid). The magnitude of a
  unit is the area weighted mean of the band summed storm total over the
  unit polygon, so each rain cell counts in proportion to the share of
  the cell inside the polygon; with 3 km rain cells and parishes of about
  3 cells a plain cell centre mask would bias or empty the small units.
  The previous pcpout based magnitudes agree closely (correlation 1.000
  per unit, median difference 1.8 to 2.2 percent, largest single
  difference 23 mm), which is a mutual validation of both products; the
  switch was made because the real time side matches against QPE and QPF
  rainfall, so the store index should be source rainfall rather than the
  hydrodynamic model's internal applied field. Both values are kept in
  `magnitudes_Antigua_pcpout_vs_rain.csv`.
- `fim_config/Antigua_*.yaml` and `fim_config/Barbados_*.yaml` (18 files):
  `outputs_root` corrected from `outputs/<Country>` to `outputs`. With the
  country folder in the path the member search resolved to
  `outputs/Antigua/<cycle>/<rkey>/...`, one level below where the
  orchestrator writes EF5 runs, so every island site would have reported
  `no_runs` for ever. Reproduced and fixed under test.

### Fixed

- `fim_config/aoc/Antigua_AG01_Barbuda_aoc.geojson`: the COD-AB 2019
  boundary of Barbuda carries a 900 m2 artifact square at (-62.0001,
  16.9999), about 80 km south of the island. The area of concern is used
  as a BOUNDING BOX when the cycle rain is sampled, so that one stray
  part stretched Barbuda's sampling box to 28.6 by 81.0 km instead of
  16.5 by 20.7 km, and the areal rain feeding the analog matching was
  taken over 60 km of open sea. Measured against the delivered scenario
  rain, 80 of the 195 wet scenarios came out more than 10 percent wrong,
  from 38 percent too low to 111 percent too high, which is wider than
  the 0.9 to 1.2 matching band. Parts below 10000 m2 are now dropped and
  the removal is recorded in the file. The other 17 unit polygons were
  checked the same way and are correct.
- `tito_utils/fim_utils/store.py`: `attach_magnitudes` now rewrites
  `index.csv` and `meta.json` as well. They kept the old order and the
  old values after a magnitude update, and they are the copy humans read.

### Added

- `fim_dev/rain_magnitudes_from_geotiffs.py`: the reusable builder for
  per unit magnitudes from scenario rain geotiffs, with coverage
  diagnostics and degenerate part removal. Use it for the other countries
  as their rain geotiffs arrive.

---

## [1.5.0] - 2026-08-23 - Morales store live, Haiti stores prepared

### Added

- `fim_store/Guatemala/fim_store_Morales_v1.zarr.zip` (split parts): the
  Morales (Rio Motagua) scenario store, 200 scenarios, max depth per
  scenario at 1 cm precision, 5 m grid, EPSG 3857, built from the
  delivered flood map library (only the MaximumDepth layer of each
  scenario was used; velocity and time step maps were left out by
  design). Magnitudes are REAL RainyDay storm totals: the footprint mean
  of each scenario's 72 band rain geotiff over the Morales model domain
  (new column footprint_mean_mm in magnitudes_Morales_real.csv; the box
  means were reverified against the delivered geotiffs, agreement 0.02
  percent). The site YAML is ACTIVE for pluvial; fluvial still waits for
  per scenario boundary discharges.
- `fim_store/Haiti/`: prepared stores for the two Haiti pilots, built the
  same way but NOT ACTIVE: Riviere Grise (40 of 200 scenarios delivered)
  and La Quinte (143 usable of 200; sample_0011 excluded, its grid
  differs from the rest). The Haiti rain scenario netcdf zips contained
  symlink stubs instead of data, so these stores carry PLACEHOLDER wet
  volume rank magnitudes (marked in magnitude_source) and their site
  YAMLs ship with enabled false. magnitude_template_Gris.csv and
  magnitude_template_LaQuinte.csv list every scenario name for the
  RainyDay side to fill; attach with fim_utils.store.attach_magnitudes.
- `fim_config/`: Guatemala_Morales.yaml activated (pluvial), new
  Haiti_Gris.yaml and Haiti_LaQuinte.yaml (parked), three new AOC
  polygons.
- `fim_store/unzip_stores.py`: now joins split store parts
  (<name>.zarr.zip.part01, .part02, ...) automatically before extracting,
  and handles both zip layouts.
- `fim_dev/build_store_from_library_zip.py`: reproducible builder that
  reads a delivered library zip (sample_NNNN/MaxVeloc-dept.zip layout)
  directly, keeps only MaximumDepth, quantizes to 1 cm and builds the
  store; rebuilds any of these three stores from the original deliveries.

### Notes

- Depths in the new stores are stored at 1 cm precision (float32 meters),
  which more than halves the store sizes with no effect on products (the
  smallest depth threshold is 10 cm).
- No config change: Guatemala was already on in fim_regions (Morales
  activates through its YAML), and Haiti stays off until real magnitudes
  arrive.

---

## [1.4.0] - 2026-08-20 - Barbados IBF on census enumeration districts

### Added

- `ibf_data/Barbados/Barbados_enum_districts_population.gpkg`: the 609
  census enumeration districts of Barbados with their 2010 census
  populations (TOT_PERS, national total 260,535), extracted from the IBF
  team's own preliminary analysis layer and now the ACTIVE admin source
  for all 11 Barbados IBF sites. Dasymetric population weights and admin
  exposure summaries therefore run at ED granularity instead of the 11
  parishes. The parish layer stays in the repo for reference and coarse
  reporting. `fim_dev/extract_barbados_ed_population.py` rebuilds the
  file from the team package.

### Changed

- The 11 `fim_config/ibf/Barbados_*_ibf.yaml` point their admin block at
  the ED layer (id ED_CODE); Antigua and Barbuda stays on its ADM1
  parishes (no ED layer exists for it yet). Generator updated.

---

## [1.3.1] - 2026-08-20 - One severity default for every country

### Changed

- IBF severity depths are now the SAME for every country: minor 0.10,
  significant 0.30, severe 0.70 m, the first three FIM depth thresholds
  (10, 30, 70, 100 cm). Guatemala moved from severe 0.76 to 0.70 in
  `ibf_regions`, in its IBF site YAML and in the ibf_utils library
  default. The 0.76 value came from the IBF team's legacy rasters; the
  severity matcher still accepts a 76cm grid for the 0.70 target when
  that is what a folder contains, and 0.76 can be set back in
  `ibf_regions` to reproduce IBFv1.0 runs. The Guatemala site YAML
  likelihood cutoff also now shows the user default 0.50 (config
  overrides applied either way).

---

## [1.3.0] - 2026-08-20 - IBF static data for the islands, user thresholds, domain fix

### Added

- `ibf_data/Antigua/` and `ibf_data/Barbados/`: complete IBF receptor
  preloads IN the repo, nothing to download. Per country: Overture Maps
  buildings and roads GeoPackage (71,255 buildings and 15,042 road
  segments for Antigua and Barbuda; 204,764 and 31,312 for Barbados),
  admin units with census population (2018 Statistics Division estimates
  for the Antigua parishes, Barbuda 2025 estimate; 2021 Barbados
  Statistical Service estimates), and the GHS BUILT-C functional class
  crop (10 m) for the dasymetric population weights. Sources and counts in
  each `manifest_ibf_<Country>.json` and in the population CSVs.
- `fim_config/ibf/`: 18 island IBF site YAMLs, one per FIM unit site,
  paired by name (`<Site>_ibf.yaml`).
- `fim_dev/build_island_ibf_static.py` and `fim_dev/gen_island_ibf_yamls.py`:
  reproducible builders for the preloads and the YAMLs.
- `Caribbean_Comoros_config.py`: Antigua and Barbados switched on in
  `ibf_regions`. USER defaults made explicit: hazard_flag_cutoff 0.50
  (50 percent likelihood; IBFv1.0 reference runs used 0.30) and severity
  depths equal to the FIM thresholds of each region (islands 10/30/70 cm,
  Guatemala 10/30/76 cm).

### Fixed

- `ibf_utils/domain.py`: the domain buffer was applied in DOMAIN units
  before reprojection, so with EPSG:4326 FIM products the 250 m buffer
  became 250 degrees: the receptor window silently grew to the whole
  country, caches and outputs bloated to every admin unit, and every
  cycle sampled the entire national receptor stock. The buffer is now
  applied after reprojection, in meters. Receptor cache keys carry a
  version salt so stale country-wide caches are not reused; delete
  `outputs/ibf_cache/` on machines that ran IBF before this fix.

---

## [1.2.0] - 2026-08-20 - FIM island stores, IBF in the cycle, no LFS for stores

### Added

- `fim_store/Antigua/` (7 zips) and `fim_store/Barbados/` (11 zips): per
  ADM1 unit scenario stores for Antigua and Barbuda and for Barbados. Each
  administrative unit is its own area of concern with its own zarr store,
  clipped from the 200 hydrodynamic samples of the island models, with real
  pluvial magnitudes (storm totals averaged over the unit polygon). Country
  manifests and per-unit magnitude tables included.
- `fim_config/`: 18 island site YAMLs plus their AOC polygons under
  `fim_config/aoc/`. One YAML per unit; the country key in `fim_regions`
  switches all of a country's units at once.
- `fim_dev/build_admin_stores.py`: reproducible builder for the per-unit
  stores (shapefile + dmax + pcpout in, stores + YAMLs + manifests out).
- `tito_hook.py`: IBF receptor stage chained right after each FIM site run
  (STEP 8). Config gated via `ibf_enabled` / `ibf_regions`; consumes the
  cycle's fresh probability rasters; non-fatal like FIM.
- `Caribbean_Comoros_config.py`: new IBF block (`ibf_enabled`,
  `ibf_regions` with per-region severity thresholds, hazard flag cutoff and
  reporting threshold). Antigua and Barbados switched on in `fim_regions`.

### Changed

- Store zips are now plain git files: `*.zarr.zip` removed from
  `.gitattributes`, and `fim_store/Guatemala/fim_store_SantaInesPetapa_v1.zarr.zip`
  converted from an LFS pointer to a regular file. Download ZIP and plain
  clones now always deliver working stores. The `*.tif` LFS rule is unchanged.
- `fim_utils` version 0.6.0. README.md, README_FIM.md, fim_config/README.md
  and fim_store/README_STORES.md refreshed to match.

---

## [1.1.0] - 2026-08-16 - feature/ibf-receptors (merged to main as PR 3)

### Added

- `tito_utils/ibf_utils/`: impact-based forecasting receptor layer. Consumes the
  probabilistic FIM products (`prob_depth_ge_{tag}.{cycle}.tif`, legacy `qpeprob`
  naming supported with correct unit parsing) and produces per-cycle receptor
  warning products: buildings and roads with per-threshold exceedance
  probabilities, flood-risk-matrix warning classes (Speight et al. 2018 / FGS
  standard) and IBFv1.0-compatible hazard/IWF fields; admin units with exposure
  summaries and impact warning flags. Receptor base (national Overture preload,
  GHS BUILT-C classing, dasymetric census population) is clipped to the FIM
  domain and cached, so warm cycles run in seconds.
- `fim_config/ibf/Guatemala_SantaInesPetapa_ibf.yaml`: region config example.
- `tests/test_ibf_utils.py`: filename/unit parsing regressions, matrix
  invariants, synthetic end-to-end cycle, cache-reuse test.
- tito_env.yml: geopandas + pyogrio (vector IO for ibf_utils).

---

## [1.0.0] - 2026-03-13 - Initial Commit

### Added

- Added HSAF precipitation input support; pipeline can now select between IMERG and HSAF as the QPE source.
- Added GFS for LR (Long Range) forecasting; GFS QPF can be paired with either IMERG or HSAF QPE inputs.
