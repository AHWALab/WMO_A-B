# TITO — Antigua and Barbuda training (30 m)

**Threading Inputs to Outputs (TITO)** is AHWA Lab's framework for running the **EF5** hydrologic model with satellite QPE, ensemble nowcast/QPF products, scenario library flood inundation mapping (FIM) and impact based forecasting (IBF).

This tree is the **Antigua and Barbuda training package**: region key `Antigua`, **30 m only**. FIM is 30 m (7 ADM1 units). StormLab domain is `lesserantilles`.

Partners need **either Docker or Apptainer/Singularity, not both**.

## What this package does

| Piece | Role |
|--------|------|
| **EF5** | Distributed hydrologic model (CREST + KW) at **30 m**. |
| **STREAM-Sat** | Ensemble satellite QPE. Code: `tito_utils/qpe_utils/STREAM-Sat-realtime/`. |
| **StormLab** | Ensemble precipitation (`lesserantilles`, 10–18°N). Run as **QPE** (no EF5 long-range). |
| **FIM** | Scenario-library inundation **after the forecast phase**, **30 m only**. 7 ADM1 units. |
| **IBF** | Receptor products chained after FIM (`ibf_regions`). |

**QPE-only EF5 chain (no long-range block):**

| Mode | Phase A | Phase B | Phase C |
|------|---------|---------|---------|
| **Hindcast STREAM-Sat + StormLab** | STREAM-Sat QPE + dry | skipped | StormLab as QPE |
| **Ops STREAM-Sat + StormLab/AROME** | STREAM-Sat QPE + dry | SCaMPR gap | StormLab **or** AROME as QPE |
| **Ops IMERG + StormLab/AROME** | IMERG QPE + dry @ T−4 h | SCaMPR gap | StormLab **or** AROME as QPE |

AROME has **no hindcast archive**. If `qpf_source` includes AROME, hindcast **stops**.

Lists in `region_forcing_map` are a Cartesian product (ops), each pair with SCaMPR gap-fill.

---

## Quick start

1. Install **Docker Desktop** or Docker Engine / **Apptainer**.
2. Load images once (see below).
3. Extract FIM stores once: `python fim_store/unzip_stores.py Antigua`
4. Run (examples use the offline training cycle):

```sh
# Linux / macOS / Git Bash / WSL
./tito-run.sh hindcast "2025-10-10 01:00" "2025-10-10 01:00" --regions Antigua
```

```bat
REM Windows CMD
tito-run.cmd hindcast "2025-10-10 01:00" "2025-10-10 01:00" --regions Antigua
```

```sh
# HPC Apptainer + offline precip
TITO_RUNTIME=apptainer ./tito-run.sh hindcast \
    "2025-10-10 01:00" "2025-10-10 01:00" --regions Antigua --offline
```

`--regions Antigua` is the region key for Antigua and Barbuda.

---

## Loading Docker images

```text
dist/docker-archives/tito_latest.tar.gz
dist/docker-archives/ef5-container_latest.tar.gz
```

| Platform | Load once | Then run |
|----------|-----------|----------|
| **Linux / macOS** | `./load-docker-images.sh` or `./tito-run.sh load-images` | `./tito-run.sh hindcast "…" "…" --regions Antigua` |
| **Windows (CMD)** | `load-docker-images.cmd` or `tito-run.cmd load-images` | `tito-run.cmd hindcast "…" "…" --regions Antigua` |

```sh
docker images tito
docker images ef5-container
```

Expect `tito:latest` and `ef5-container:latest`.

---

## Offline mode

Classroom / no-network. Only with `--offline`. Allowed cycle: **2025-10-10 01:00 UTC**.

```sh
TITO_RUNTIME=apptainer ./tito-run.sh hindcast \
    "2025-10-10 01:00" "2025-10-10 01:00" --regions Antigua --offline
```

Archive:

```text
offline_precips/
  stream_sat/caribbean/ensP*/
  stormlab/lesserantilles/ensQ*/
  precipEF5/antigua_30m/
```

Refresh after a good online run: `bash offline/materialize_offline_precips.sh`  
More: [offline/README.md](offline/README.md).

---

## Folder structure

```text
TITO_AntiguaTraining/
  Caribbean_Comoros_config.py
  orchestrator.py  hindcast_manager.py
  tito-run.sh / tito-run.cmd
  tito_utils/
  EF5_conf/
    basic/          DEM/FAC/FDIR antigua_30m
    parameters/     CREST_Antigua_30m  KW_Antigua_30m
    templates/      ef5_Antigua_30m_control_template.txt
    states/  precip/  precipEF5/  qpf_store/
  outputs/<cycle>/antigua_30m/<product>/
  offline/  offline_precips/
  fim_config/  fim_store/Antigua/
```

### Cycle-first outputs

```text
outputs/20251010.010000/antigua_30m/
  stream_sat/ensOut1/
  stormlab/ensOut1_sl1/
  arome/ensOut1/          # ops AROME chain
  fim/stream_sat_stormlab/
  ibf/<Site>/
```

---

## Config highlights (`Caribbean_Comoros_config.py`)

```python
region_resolution_map = {"Antigua": "30m"}
regions_to_run = ["Antigua"]

region_forcing_map = {
    "Antigua": {"qpe_source": "STREAM_SAT", "qpf_source": "STORMLAB"},
    # ops lists (Cartesian product + SCaMPR gap):
    # "Antigua": {
    #     "qpe_source": ["STREAM_SAT", "IMERG"],
    #     "qpf_source": ["STORMLAB", "AROME"],
    # },
}

stream_sat_ensemble_size = 2
stormlab_ensemble_size = 2
ef5_max_workers = 1

fim_enabled = True          # 30 m after forecast
fim_regions = {"Antigua": {"enabled": True, "thresholds_m": [0.10, 0.30, 0.70, 1.00]}}
ibf_enabled = True
ibf_regions = {"Antigua": {"enabled": True, ...}}
```

Control template: `EF5_conf/templates/ef5_Antigua_30m_control_template.txt`

FIM stores: `python fim_store/unzip_stores.py Antigua` after clone. See [README_FIM.md](README_FIM.md).

---

## Reset after a crash

| OS | Command |
|----|---------|
| Linux / macOS | `./reset_tito.sh` |
| Preview | `./reset_tito.sh --dry-run` |
| Windows CMD | `reset_tito.cmd` |

Wipes `outputs/`, live precip, STREAM-Sat / StormLab runtime output. Does **not** wipe `offline_precips/`.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `docker load` 502 | Docker Desktop not ready |
| EF5 exit **137** | OOM: lower ensembles, `ef5_max_workers=1`, raise Docker RAM |
| FIM `no_runs` | Check `outputs/<cycle>/antigua_30m/stormlab/` vs chain tag |
| FIM skip 90 m | This package is **30 m only** |
| Offline refused cycle | Only 2025-10-10 01:00 UTC |
| Hindcast + AROME | Error: AROME has no archive — use STORMLAB or GFS |

---

## Contact

Naman Mehta - naman-mehta@uiowa.edu  
Vanessa Robledo - vanessa-robledodelgado@uiowa.edu  
AHWA Laboratory - [ahwa.lab.uiowa.edu](https://ahwa.lab.uiowa.edu/) - engr-ahwa-lab@uiowa.edu

## Cite

Robledo Delgado, V., & Vergara, H. (2025). Threading Inputs to Outputs (TITO) (v2.0.0). Zenodo. https://doi.org/10.5281/zenodo.17246491
