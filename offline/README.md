# TITO offline training mode (Antigua and Barbuda, 30 m)

No network downloads. Uses pre-staged STREAM-Sat + StormLab precip for:

```bash
TITO_RUNTIME=apptainer ./tito-run.sh hindcast "2025-10-10 01:00" "2025-10-10 01:00" --regions Antigua --offline
```

## How it works

1. `--offline` sets `TITO_OFFLINE=1` and monkey-patches `prepare_cycle_precip`.
2. The hook stages precip from `offline_precips/` into `EF5_conf/precip/` (no downloads).
3. If `stream_sat_ensemble_size` / `stormlab_ensemble_size` is smaller than the archive, the wettest members become `ensP1..N` / `ensQ1..N`.
4. EF5 + FIM then run unchanged. StormLab domain for Antigua is `lesserantilles`.

## Populate `offline_precips/`

```bash
bash offline/materialize_offline_precips.sh
```

Expected tree:

```text
offline_precips/
  stream_sat/caribbean/ensP1..10/
  stormlab/lesserantilles/ensQ1..5/
  precipEF5/antigua_30m/streamsat_ens*/  stormlab_ens*_sl*/
```

Allowed cycle: **2025-10-10 01:00** (`202510100100`).
Override with `TITO_OFFLINE_ALLOWED_CYCLES=...` if you extend the archive.
