"""Generate one fim_config/ibf/<Site>_ibf.yaml per island FIM site.

Reads the island FIM site YAMLs (Antigua_*.yaml, Barbados_*.yaml), reuses
their region and products_root, and writes the paired IBF YAML pointing at
the country's static data under ibf_data/<Country>/.
"""

import glob
import os
import sys

import yaml

FIM_DIR = sys.argv[1] if len(sys.argv) > 1 else "fim_config"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "out_ibf_yamls"

META = {
    "Antigua": {
        "slug": "antigua", "work_crs": "EPSG:32620",
        "label": "Antigua and Barbuda"},
    "Barbados": {
        "slug": "barbados", "work_crs": "EPSG:32621",
        "label": "Barbados"},
}

TEMPLATE = """# IBF receptor product: {label}, {unit_label} (paired with the FIM site
# {site}.yaml; same stem plus _ibf).
#
# Static data lives IN the repo under ibf_data/{country}/: Overture Maps
# buildings and roads, admin units with census population, and the GHS
# BUILT-C functional class raster for the dasymetric weights. Nothing to
# download before running.
#
# In orchestrated runs tito_hook overrides fim_products with the cycle
# first path, and the ibf_regions block in Caribbean_Comoros_config.py
# OVERRIDES severity_thresholds_m, hazard_flag_cutoff and
# reporting_threshold, so operators normally edit them there.
region: {site}

receptors:
  buildings:
    source: ibf_data/{country}/{slug}_overture_bld_rds.gpkg
    layer: buildings
    id_field: id
    subtype_field: subtype
  roads:
    source: ibf_data/{country}/{slug}_overture_bld_rds.gpkg
    layer: roads
    id_field: id
    class_field: class
    keep_classes: [motorway, trunk, primary, secondary, tertiary, residential, unclassified]
  admin:
    source: ibf_data/{country}/{country}_adm1_population.gpkg
    layer: adm1
    id_field: ADM1_PCODE
    name_field: ADM1_EN
    population_field: population
  land_use:
    source: ibf_data/{country}/{country}_GHS_BUILT_C_FUN_E2018_R2023A_54009_10.tif
  work_crs: "{work_crs}"
  domain_buffer_m: 250
  cache_dir: outputs/ibf_cache

fim_products:
  # manual pipeline_pf runs write {products_root}/<cycle>/<mode>
  root: {products_root}/{{cycle}}/{{mode}}
  mode: pluvial
  prefer_overbank: false

classification:
  # User defaults for the islands: likelihood cutoff 50 percent, severity
  # depths equal to the FIM depth thresholds of these sites (10, 30, 70 cm).
  reporting_threshold: 0.05
  severity_thresholds_m:
    minor: 0.10
    significant: 0.30
    severe: 0.70
  hazard_flag_cutoff: 0.50

outputs:
  # Orchestrated runs override to outputs/<cycle>/<rkey>/ibf/<Site>/
  root: outputs
  append_cycle: false
"""


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    n = 0
    for country in META:
        for path in sorted(glob.glob(os.path.join(FIM_DIR, f"{country}_*.yaml"))):
            with open(path) as fh:
                fim = yaml.safe_load(fh)
            site = fim["region"]
            unit_label = site.split("_", 1)[1]
            out = os.path.join(OUT_DIR, f"{site}_ibf.yaml")
            with open(out, "w") as fh:
                fh.write(TEMPLATE.format(
                    label=META[country]["label"], unit_label=unit_label,
                    site=site, country=country, slug=META[country]["slug"],
                    work_crs=META[country]["work_crs"],
                    products_root=fim["products_root"]))
            n += 1
            print("wrote", out)
    print(f"{n} IBF yamls")


if __name__ == "__main__":
    main()
