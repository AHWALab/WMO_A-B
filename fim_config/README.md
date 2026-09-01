# fim_config — Antigua and Barbuda (30 m)

One YAML file = one FIM site. After each cycle’s EF5 runs, STEP 8 picks up
files named `Antigua*.yaml` when `regions_to_run` includes `Antigua`.

Toggles: `fim_enabled = False` skips FIM; `fim_regions["Antigua"]` switches
all 7 units and overrides `thresholds_m`; `enabled: false` in a YAML parks
one site.

## Current sites

| file | site | hazards | status |
| --- | --- | --- | --- |
| `Antigua_Barbuda.yaml` | Barbuda (AG01) | pluvial | READY |
| `Antigua_SaintGeorge.yaml` | Saint George (AG03) | pluvial | READY |
| `Antigua_SaintJohnS.yaml` | Saint John's (AG04) | pluvial | READY |
| `Antigua_SaintMary.yaml` | Saint Mary (AG05) | pluvial | READY |
| `Antigua_SaintPaul.yaml` | Saint Paul (AG06) | pluvial | READY |
| `Antigua_SaintPeter.yaml` | Saint Peter (AG07) | pluvial | READY |
| `Antigua_SaintPhilip.yaml` | Saint Philip (AG08) | pluvial | READY |

All YAMLs set `required_resolution: "30m"`. This package does not run FIM at 90 m.

## IBF

`ibf/Antigua_*_ibf.yaml` pairs with each FIM site. Switches live in
`ibf_enabled` / `ibf_regions` in `Caribbean_Comoros_config.py`.

## Hazards

Antigua and Barbuda sites are **pluvial only** (`fluvial.enabled: false`).

## Depth thresholds

    thresholds_m: [0.10, 0.30, 0.70, 1.00]

Orchestrated runs use `fim_regions["Antigua"]["thresholds_m"]`.

## Before a site can run

Unzip stores once:

    python fim_store/unzip_stores.py Antigua

AoC polygons ship under `fim_config/aoc/`.

## Manual test

    python -m tito_utils.fim_utils.pipeline_pf \
        --config fim_config/Antigua_SaintGeorge.yaml --cycle 20251010.010000
