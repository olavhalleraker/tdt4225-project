# Data cleaning – Porto Taxi dataset

The cleaning is done by `insert_data.py` while loading `data/porto.csv` into the schema in `sql/schema.sql`. The EDA numbers below come from `eda/eda.py` (checks S1–S15, output in `eda/output/eda_full_run.txt`). The "actual" numbers come from the full load (log of 2026-09-28).

## Summary

| | rows (trips) | GPS points |
|---|---:|---:|
| raw CSV | 1,710,670 | 83,409,386 |
| rule 2: extra copies of duplicate TRIP_IDs dropped | −81 | −103 |
| rule 3: trips with a point outside mainland Portugal dropped | −32 | −870 |
| **loaded** | **1,710,557** | **83,408,413** |
| of which `is_valid = 0` (< 3 points, rule 1) | 43,815 | |
| of which `n_points = 0` (empty POLYLINE) | 5,894 | |
| of which `has_gap = 1` (rule 4) | 38,198 | |

Rows dropped in total: 113 (0.0066 %). Points dropped: 973 (0.0012 %). 448 taxis are loaded, and no taxi is lost.

## Rules

### 1. Trips with fewer than 3 points are kept
- **EDA:** 43,904 trips (2.57 %) have < 3 points. 5,901 of them have an empty POLYLINE `[]`, 30,609 have 1 point and 7,394 have 2 points.
- **Decision:** keep them as `trip` rows, with their 0–2 points in `gps_point`. Query 7 asks for the number of invalid trips, so they must be in the database. Filtering happens through the generated column `trip.is_valid = (n_points >= 3)`. Queries where short trips only add noise use `is_valid = 1`.
- **Actual:** 43,815 trips have `is_valid = 0`, of which 5,894 have no points. This is 89 fewer than in the EDA, because 75 of the dropped duplicate copies (rule 2) and 14 of the out-of-Portugal trips (rule 3) had < 3 points.

### 2. Duplicate TRIP_IDs: keep one row per id
- **EDA:** 80 TRIP_IDs occur more than once (79 twice, 1 three times), which is 161 rows. Most copies are not real duplicates. Often one copy is a short stub or empty (`[]`) and the other is the real trip, and in 23 ids the CALL_TYPE also differs.
- **Decision:** keep exactly one row per TRIP_ID: the copy with the **most GPS points**. When copies tie, keep the first occurrence in the CSV. A first pass over the file collects (TRIP_ID, number of points) for every row, and the keep decision is made before anything is inserted. After this, `trip.trip_code` is unique. The surrogate primary key `trip.id` and the non-unique index on `trip_code` are unchanged.
- **Actual:** 80 ids / 161 rows affected, **81 rows dropped**. The dropped copies are all short: 7 empty, 75 with < 3 points, at most 6 points, 103 points in total. In 26 ids the copies had the same number of points, and the first occurrence was kept. This includes the ids with identical copies.
- Example: `1372702836620000080` has a B copy with 2 points and a C copy with 54 points. The C copy is kept.

### 3. Trips with a point outside mainland Portugal are dropped
- **EDA:** only 38 points in 32 trips lie outside the box lon [−9.6, −6.1], lat [36.9, 42.2]. They reach lon −36.9 to 52.9 and lat 32.0 to 51.0. These points are certainly GPS garbage. They include the 2 MISSING_DATA = True trips that have a single wrong point.
- **Decision:** drop the whole trip if **any** point lies outside the box. There are so few such trips that it isn't worth repairing them point by point.
- **Actual:** **32 trips dropped** (870 points, 14 of the trips had < 3 points). None of them overlaps with the copies dropped by rule 2. Rule 2 is applied first, then rule 3. After this the longest trip is 751.6 km, where the EDA maximum was 1,229 km.

### 4. `has_gap` flag for GPS jumps / signal gaps (not dropped)
- **EDA:** 46,985 segments in 38,216 trips (2.23 %) imply > 200 km/h between two consecutive points, i.e. more than 0.833 km in 15 s. Only 208 of them are isolated single-point spikes. Most are one-way "teleports" where points are missing, and after such a gap the 15 s-per-point timing no longer holds.
- **Decision:** add `trip.has_gap BOOLEAN NOT NULL DEFAULT 0`. It is `1` if any consecutive segment has a haversine distance > 200 km/h × 15 s = 0.8333 km. The trips and all their points are kept. `distance_km` stays the plain sum over **all** segments, so queries about distance or speed can exclude these trips with `has_gap = 0`, and should say so in the report.
- **Actual:** **38,198 trips** have `has_gap = 1`. This is 18 fewer than the EDA, because some of the trips dropped by rules 2 and 3 had gaps. 38,049 of them are valid trips (≥ 3 points).

### 5. No other trip filtering
We deliberately do **not**:
- clip to a Porto bounding box. 7,142 trips leave the Porto box, and they are mostly real long-distance trips on motorways.
- drop long trips. 847 trips are > 3 h and 403 are > 4 h. The report should mention them where they matter.
- drop trips with 0 m distance.
- use `DAY_TYPE` (always 'A') or `MISSING_DATA` (only 10 True, 8 after rule 3) to drop anything. Both are stored as they are.

### 6. Empty ORIGIN_CALL / ORIGIN_STAND → NULL
- Empty strings become SQL `NULL`, not 0 or ''.
- **Actual:** `origin_call` is non-NULL only for call type A (all 364,762 A trips). `origin_stand` is non-NULL only for B. **11,302 B trips have no stand** (NULL). This matches the EDA, and those trips are kept.

## Derived values (for reference)
- `start_time` / `end_time`: unix `TIMESTAMP` converted to **Europe/Lisbon** local time (naive DATETIME). `end_time = start_time + 15 × (n_points − 1)` s. The loaded range is 2013-07-01 01:00:53 to 2014-07-01 00:59:56 local time.
- `distance_km`: sum of haversine distances (`haversine.haversine_vector`, mean Earth radius 6371.0088 km) over consecutive points, rounded to 3 decimals. POLYLINE is `[lon, lat]`, haversine takes `(lat, lon)`.
- Coordinates are written to the database as the original CSV text (at most 6 decimals), so `gps_point` holds exactly the values in the file.

## Verification after the full load
| check | result |
|---|---|
| `COUNT(*) gps_point` = `SUM(trip.n_points)` | 83,408,413 = 83,408,413 |
| trip.id range | 1 … 1,710,557 (contiguous) |
| distinct `trip_code` | 1,710,557 (unique) |
| trips without a taxi / points without a trip | 0 / 0 |
| trips where `COUNT(points)` ≠ `n_points` or `MAX(seq)` ≠ `n_points − 1` | 0 |
| `LOAD DATA` warnings | none (the loader aborts on any warning) |
| spot checks against the CSV (json.loads + scalar haversine) | 5 trips (a duplicate id, a winter and a summer trip, a `has_gap` trip, an empty trip): all points identical, and start/end time, duration, distance, call type and origin match |
