# FIM and IBF — Antigua and Barbuda (30 m)

Scenario library flood inundation mapping (FIM) matches each cycle's rain
total to the closest simulated storm in a per-site zarr store. IBF then
turns those probabilities into building, road and admin-unit warning
products. Both run in orchestrator STEP 8 after EF5 (non-fatal).

This training tree: **Antigua and Barbuda only**, **30 m only**, 7 ADM1
units (pluvial). Region key: `Antigua`.

## Layout

| Path | Role |
|------|------|
| `tito_utils/fim_utils/` | FIM package |
| `tito_utils/ibf_utils/` | IBF receptor package |
| `fim_config/Antigua_*.yaml` | One YAML per ADM1 unit |
| `fim_config/ibf/Antigua_*_ibf.yaml` | IBF per site |
| `fim_config/aoc/` | Area of concern polygons |
| `fim_store/Antigua/` | 7 scenario store zips |
| `outputs/<cycle>/antigua_30m/fim/<chain>/` | FIM products |
| `outputs/<cycle>/antigua_30m/ibf/<Site>/` | IBF products |

## Switches (`Caribbean_Comoros_config.py`)

```python
fim_enabled = True
fim_default_thresholds_m = [0.10, 0.30, 0.70, 1.00]
fim_regions = {
    "Antigua": {"enabled": True, "thresholds_m": fim_default_thresholds_m},
}

ibf_enabled = True
ibf_regions = {
    "Antigua": {
        "enabled": True,
        "severity_thresholds_m": {"minor": 0.10, "significant": 0.30, "severe": 0.70},
        "hazard_flag_cutoff": 0.50,
        "reporting_threshold": 0.05,
    },
}
```

## When FIM runs

| Rule | Behaviour |
|------|-----------|
| When | After forecast EF5 (Phase C) only |
| Resolution | **30 m** (`required_resolution: "30m"`). 90 m is skipped. |
| Rain | Sum of QPE accums (never QPF accum / long range) |
| Chains | `stream_sat_stormlab`, `stream_sat_arome`, `imerg_stormlab`, `imerg_arome` |

## Sites

| Code | Unit | Hazard |
|------|------|--------|
| AG01 | Barbuda | pluvial |
| AG03 | Saint George | pluvial |
| AG04 | Saint John's | pluvial |
| AG05 | Saint Mary | pluvial |
| AG06 | Saint Paul | pluvial |
| AG07 | Saint Peter | pluvial |
| AG08 | Saint Philip | pluvial |

AG02 Redonda has no store (outside both island models).

## One-time setup

```bash
python fim_store/unzip_stores.py Antigua
```

## Products

```text
outputs/<cycle>/antigua_30m/fim/<chain>/
  pluvial/   prob_depth_ge_10cm.<cycle>.tif ...
```

IBF writes `outputs/<cycle>/antigua_30m/ibf/<Site>/`. Receptor data is in
`ibf_data/Antigua/` (Overture buildings/roads, ADM1 population, optional GHS).

## Manual tests

```bash
python -m tito_utils.fim_utils.pipeline_pf \
  --config fim_config/Antigua_SaintGeorge.yaml --cycle 20251010.010000

python -m tito_utils.ibf_utils.pipeline_ibf \
  --config fim_config/ibf/Antigua_SaintGeorge_ibf.yaml \
  --cycle 20251010.010000 \
  --products-dir outputs/20251010.010000/antigua_30m/fim/stream_sat_stormlab/pluvial
```
