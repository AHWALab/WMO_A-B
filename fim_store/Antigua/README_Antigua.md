# FIM store folder: Antigua and Barbuda (region key: Antigua)

STATUS: READY. One scenario store per ADM1 administrative unit, built by
fim_dev/build_admin_stores.py from the hydrodynamic sample libraries of the
two island models (antigua and barbuda).

Seven units carry a store (zip per unit, unzip with
python fim_store/unzip_stores.py):

    AG01 Barbuda        from the barbuda island model
    AG03 Saint George   from the antigua island model
    AG04 Saint John's   from the antigua island model
    AG05 Saint Mary     from the antigua island model
    AG06 Saint Paul     from the antigua island model
    AG07 Saint Peter    from the antigua island model
    AG08 Saint Philip   from the antigua island model

AG02 Redonda is outside both island model domains and has no store.

Each store: 200 scenarios, max depth in meters on the model grid clipped to
the unit (depths come from the dmax product, uint8 centimeters, saturated
at 2.55 m) and extent mask at 0.05 m.

## Magnitudes (the matching axis), updated August 2026

Since v1.6.0 the pluvial magnitude of every scenario is taken from the
RainyDay SCENARIO RAIN GEOTIFFS delivered for the two island models
(antigua_rain_scenarios_geotiff_files, barbuda_rain_scenarios_geotiff_files,
200 scenarios each, 72 bands, 0.027 degree grid). The magnitude of a unit
is the AREA WEIGHTED mean of the band summed storm total over the unit
polygon: each rain cell counts in proportion to the share of the cell
inside the polygon. That matters here because the rain grid is about 3 km
while the smallest parish is about 3 rain cells; a plain cell centre mask
would bias or empty the small units. Rebuild with
fim_dev/rain_magnitudes_from_geotiffs.py.

Until v1.6.0 the magnitudes came from pcpout, the rain the hydrodynamic
model itself applied. The two agree closely, which is a useful mutual
validation: correlation 1.000 for every unit, median difference 1.8 to 2.2
percent, largest single difference 23 mm on totals of hundreds of mm, with
the source rain slightly higher throughout. The switch was made because the
real time side matches against QPE and QPF rainfall totals, so the store
index should be the same physical quantity (source rainfall), not the
model's internal applied field. Both columns are kept for provenance in
magnitudes_Antigua_pcpout_vs_rain.csv.

Ranges per unit are in manifest_Antigua.csv, full tables in
magnitudes_Antigua_per_unit.json. One scenario per unit has zero rain over
the unit and can never be matched by a positive rain total; that is the
overbank reference scenario of the site configs.

Site configs: fim_config/Antigua_<Unit>.yaml (pluvial only). Country switch
and thresholds: fim_regions["Antigua"] in Caribbean_Comoros_config.py.
