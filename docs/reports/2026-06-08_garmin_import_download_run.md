# Garmin Import Job — Live Download Run Report (Run #2)

**Date:** 2026-06-08
**Run type:** Live download enabled (`GARMIN_DOWNLOAD=true`, `GARMIN_DOWNLOAD_LATEST=false` → download **all**)
**Result:** ✅ **Success — all Garmin data downloaded from Garmin Connect and imported into the DuckDB tables.**
**Evidence log:** `jobs/import-garmin/local_data/import-garmin_download_run_2026-06-08T15-24-08.log`
**Companion run #1 (transform-only):** `docs/reports/2026-06-08_garmin_import_job.md`

---

## 1. Objective

Run the import job with the **live download** enabled, to pull all data from
Garmin Connect and import it into the DuckDB tables. Capture logs to a separate
file, inspect them, fix any issues found, and iterate until the download + import
succeeds. Success criterion: successful download of all Garmin data and import
into the DuckDB tables, with table counts reported.

---

## 2. Outcome — DuckDB table counts

The job ran in Docker and **exited 0**. Fresh data was downloaded from Garmin
Connect (293 activities + sleep) and imported:

| Table | Row count |
|-------|-----------|
| `garmin_workout_metadata` | **293** |
| `garmin_timeseries` | **112 983** |
| `garmin_sleep` | **205** |

Compared with the transform-only run #1 (which used 3-month-old data), the live
download brought the dataset up to date:

| Table | Run #1 (stale) | Run #2 (live download) |
|-------|---------------:|-----------------------:|
| `garmin_workout_metadata` | 193 | **293** |
| `garmin_timeseries` | 80 295 | **112 983** |
| `garmin_sleep` | 135 | **205** |

---

## 3. Token

The previously-stored garth token was expired (refresh token expired 2026-04-14),
so it was renewed via the host helper script `./scripts/renew_garmin_token.sh` (Garmin
login + MFA). The renewed token's refresh validity runs to **2026-07-08**. The job
copies/refreshes this token from the mounted `~/.GarminDb/garth_session` on every
run.

---

## 4. Issues found while iterating (and fixed)

Reaching a successful run surfaced several real problems, all fixed:

1. **Silent download failure (exit-code blindness).** `garmindb_cli` prints
   `Failed to login!` but still **exits 0** on auth failure, so the job originally
   reported success and silently transformed stale data. Fixed in
   `garmin_etl/download.py` with a **preflight token-expiry check** plus **CLI
   output capture + failure-marker scanning**.
2. **No abort on required-download failure.** Added `GARMIN_DOWNLOAD_REQUIRED`
   (default true): a failed required download now **aborts (exit 1)** instead of
   importing stale data.
3. **Wrong garmindb version / token format.** Pinned **`garmindb==3.7.0`** (garth
   single-file `garth_session`, matching the token format); 3.8.x switched to an
   incompatible token store. Bumped `requires-python>=3.10`; enabled the
   `download` extra in the `Dockerfile`.
4. **Stale cached token won over the fresh one.** The container reused a stale
   `local_data/.GarminDb/garth_session` copied by an earlier run (persisted on the
   mounted volume) instead of the freshly renewed host token, so it kept reporting
   the *old* expiry. Fixed `_ensure_session_token()` to treat the host token
   (`~/.GarminDb/garth_session`) as the source of truth and **refresh the
   config-dir copy from it on every run**.
5. **Sleep download scanned years of empty days.** With the default
   `GARMIN_START_DATE=01/01/2020`, GarminDB downloaded sleep day-by-day for ~2350
   days (~45 min), most predating the device. Set `GARMIN_START_DATE=10/01/2025`
   (just before the account's first data) so all real data is captured quickly.
6. **Empty placeholder sleep rows.** GarminDB seeds empty `sleep` rows
   (`total_sleep=0`, null timestamps) for days with no recorded sleep. The
   transform's filter kept them (0 is not null), inflating the count to 342. Fixed
   `build_sleep()` to keep a row only when it has real sleep duration (`> 0`) or a
   sleep-start timestamp → **205 genuine sleep nights**.

---

## 5. Docker run (evidence)

```bash
cd jobs/import-garmin
./scripts/renew_garmin_token.sh                                 # one-time: renew the token
docker compose build                                    # image includes garmindb 3.7.0
docker compose up --no-build --abort-on-container-exit
```

Full log: `jobs/import-garmin/local_data/import-garmin_download_run_2026-06-08T15-24-08.log`.
Key excerpt:

```
Step 2: Downloading latest data from Garmin (GarminDB CLI)...
  Refreshed garth_session from /root/.GarminDb/garth_session
  Running: .../garmindb_cli.py -f /app/local_data/.GarminDb --all --download --import
  ___Downloading All Data___
  Getting activities: '/app/local_data/garmin_sqlite/FitFiles/Activities' (1000) ...
  Downloading all sleep data from: 2025-10-01 [250]
  ✅ GarminDB download + import complete
Step 3: Transforming GarminDB SQLite data...
  Transformed: 293 workouts (289 with GPS), 112983 records, 205 sleep nights
Step 4: Loading into DUCKDB...
  ✅ DuckDB workouts: upserted 293 (table total 293); timeseries rows 112983 (table total 112983)
  ✅ DuckDB sleep: upserted 205 (table total 205)
✅ GARMIN IMPORT JOB COMPLETE
garmin-import-job exited with code 0
JOB_EXIT_CODE=0
```

(The full log also contains the per-activity download and per-day sleep download
progress bars.)

---

## 6. DuckDB verification

All queries run against `jobs/import-garmin/local_data/garmin.duckdb` (7.6 MB).

| Check | Result |
|-------|--------|
| Tables present | `garmin_workout_metadata`, `garmin_timeseries`, `garmin_sleep` |
| `workoutId` total / distinct / null / valid-format | 293 / 293 / 0 / 293 |
| GPS coverage (`gps_source`) | `first_record` 289, `none` 4 |
| Timeseries `workoutId` nulls / orphans | 0 / 0 |
| Sleep rows with real sleep (`total_sleep_s > 0`) | 205 / 205 |
| Sleep distinct days | 205 |
| Workout date range (`start_time`) | 2025-11-09 → **2026-06-08 11:56** |
| Sleep date range (`day`) | 2025-11-09 → 2026-06-07 |
| HR (timeseries) min / mean / max / nulls | 0 / 134 / 195 / 113 |

Sample (latest 3 workouts):

```
        workoutId   activity_id   sport          start_time  distance  calories  avg_hr      lat      lon
08-06-2026_115609   23172952391   generic 2026-06-08 11:56:09   0.21093        89     119  36.6504  -4.7775
08-06-2026_105613   23172523660   generic 2026-06-08 10:56:13   0.23098       187     163  36.6679  -4.7417
27-05-2026_114558   23029407739   generic 2026-05-27 11:45:58   0.22968        82     109  36.6680  -4.7421
```

Sample (latest 3 sleep nights):

```
       day          sleep_start            sleep_end  total_sleep_s  score  qualifier
2026-06-07  2026-06-06 21:54:57  2026-06-07 04:47:20          22643     56       POOR
2026-06-06  2026-06-05 21:06:34  2026-06-06 05:20:57          28703     92  EXCELLENT
2026-06-05  2026-06-04 21:27:28  2026-06-05 05:22:28          27960     92  EXCELLENT
```

Data reaches **today (2026-06-08)** — confirming the live download pulled fresh
data. `workoutId` is unique and correctly formatted, every per-second HR row links
to a workout, and only genuine sleep nights are retained.

---

## 7. Configuration used

| Setting | Value |
|---------|-------|
| `DATABASE_TYPE` | `duckdb` |
| `GARMIN_DOWNLOAD` | `true` |
| `GARMIN_DOWNLOAD_LATEST` | `false` (download all) |
| `GARMIN_DOWNLOAD_REQUIRED` | `true` |
| `GARMIN_START_DATE` | `10/01/2025` |
| garmindb | `3.7.0` (download extra) |

Enabled GarminDB stats: **activities + sleep** (matching what the job imports).

---

## 8. Status

✅ **Success criteria met.** The job authenticated with the renewed token,
downloaded all Garmin data (293 activities + sleep) from Garmin Connect, imported
it through the GarminDB SQLite layer, transformed it, and upserted into the DuckDB
tables — **293 workouts (289 with GPS), 112 983 per-second HR/GPS rows, 205 sleep
nights** — with the container exiting 0 and all integrity checks passing.
