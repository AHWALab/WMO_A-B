Offline precip archive for Antigua and Barbuda training, 30 m (2025-10-10 01:00 UTC).

Populate once after a good online hindcast:

  bash offline/materialize_offline_precips.sh

Expected layout:

  offline_precips/
    stream_sat/caribbean/ensP1..N/*.tif
    stormlab/lesserantilles/ensQ1..M/*.tif
    precipEF5/antigua_30m/streamsat_ens*  stormlab_ens*_sl*

Run (ONLY this time — other timestamps are refused in --offline):

  TITO_RUNTIME=apptainer ./tito-run.sh hindcast "2025-10-10 01:00" "2025-10-10 01:00" --regions Antigua --offline

Allowed cycle key: 202510100100
(override with env TITO_OFFLINE_ALLOWED_CYCLES=... if you extend the archive)

For any other date/time, drop --offline (online downloads).
