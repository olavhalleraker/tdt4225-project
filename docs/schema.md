# Database schema – Porto Taxi Trajectory dataset

The DDL is in `sql/schema.sql`. Run `create_tables.py` to create it. That script drops the tables, recreates them and prints `SHOW CREATE TABLE`.

```
call_type (code PK) ──< trip >── taxi (taxi_id PK)
                         │ id PK
                         │
                         └──< gps_point (trip_id, seq) PK
```

| table       | rows (approx.) | purpose                                              |
|-------------|----------------|------------------------------------------------------|
| `call_type` | 3              | lookup table for A/B/C with a description, filled by `schema.sql` |
| `taxi`      | ~450           | one row per distinct `TAXI_ID`                       |
| `trip`      | ~1.7M          | one row per cleaned CSV row (see section 2.4 in `docs/report.md`), raw attributes plus precomputed summary columns |
| `gps_point` | ~80M           | one row per POLYLINE point, stored as compactly as possible |

## 1. Design choices

### 1.1 Entities
- **taxi** is its own entity because many trips refer to the same taxi, and query 1 counts taxis. The dataset has no other taxi attributes, so the table holds only the id. It still gives `trip.taxi_id` a foreign-key target.
- **call_type** is a lookup table. There are only 3 codes, but the table documents what A/B/C mean, a FK guarantees that only valid codes get in, and the query results for 4a/4b can show readable names.
- We did **not** add stand or client tables. `ORIGIN_STAND` and `ORIGIN_CALL` are plain ids with no attributes in the dataset (there is no stand metadata file), so they stay as nullable columns on `trip`. `DAY_TYPE` is also kept on `trip`, as `CHAR(1)` with a `CHECK` constraint, instead of getting its own lookup table.
- **gps_point** is the POLYLINE normalised into rows. This turns queries 1, 6 and 7 into plain SQL, which a JSON column would not.

### 1.2 Trip key: surrogate `INT` instead of TRIP_ID
- `TRIP_ID` is **not unique** in the raw data. 80 ids appear more than once, and the rows differ. For example, `1372702836620000080` appears once as call type B with 2 points and once as C with many points. The loader keeps one copy per id (see section 2.4 in `docs/report.md`), so `trip_code` is unique in the loaded data, but the surrogate key keeps the schema independent of that cleaning rule.
- The trip key is repeated in all ~80M `gps_point` rows. A 4-byte `INT UNSIGNED` instead of an 8-byte `BIGINT` saves about 320 MB.
- The original id is kept as `trip.trip_code BIGINT UNSIGNED`. It has a `UNIQUE` index, so the database enforces that each TRIP_ID occurs once after cleaning, and trips can still be looked up by it.

### 1.3 gps_point compactness (disk budget about 14 GB)
| choice | bytes/row | alternative rejected |
|---|---|---|
| composite clustered PK `(trip_id INT, seq SMALLINT)` | 4 + 2 | surrogate `BIGINT id` would add 8 B and need an extra index for the FK |
| `lat DECIMAL(8,6)` | 4 | `DECIMAL(9,6)` = 5 B. `DOUBLE` = 8 B. `FLOAT` = 4 B but not exact (about 0.4 m rounding) |
| `lon DECIMAL(9,6)` | 5 | needs 3 integer digits to hold the whole valid range ±180 |
| **no per-point timestamp** | 0 | a `DATETIME` would add 5 B/row (about 400 MB). The time is derived instead: `start_time + INTERVAL 15*seq SECOND` |
| **no secondary index** | 0 | an index on `(lat, lon)` measured at 23.9 B/row, about 1.9 GB (see 1.6) |

DECIMAL keeps the CSV's 6 decimals exactly (about 0.1 m), and the values are easy to read in query output.

The FK on `gps_point.trip_id` needs no separate index. The leftmost prefix of the PK serves it. Because the table is clustered on `(trip_id, seq)`, all points of a trip sit next to each other in order, and one PK range scan reads the whole trajectory.

**Measured size.** We inserted 2M synthetic rows into an identical table in PK order, the same way the loader will insert them. `data_length` was 72,974,336 B, which is **36.5 B/row**. That breaks down as 15 B of data, an 18 B InnoDB row header (5 B record header + 6 B trx id + 7 B roll pointer), and page overhead with pages filled to 15/16.

| | per row | 80M rows |
|---|---|---|
| gps_point (clustered PK, no secondary index) | 36.5 B | **≈ 2.9 GB** |
| optional `(lat, lon)` index, built after load | 23.9 B | ≈ 1.9 GB (plus about the same again as temporary sort space while building) |
| for comparison: `BIGINT` trip key + `DATETIME` per point | ≈ 45.5 B | ≈ 3.6 GB |

`trip` is about 1.7M rows × roughly 150 B, i.e. about 250 MB including its 3 secondary indexes. The whole database should come to about **3.2 GB**.

### 1.4 Precomputed columns on `trip`
Every trip-level query (2–5, 7–10) reads only the 1.7M-row `trip` table and never scans the 80M points. The derived columns follow directly from the raw data and are computed once at insert time: `start_time`, `end_time`, `duration_s`, `n_points`, `distance_km`, `has_gap`, start/end coordinates and the bounding box.

`has_gap` is 1 if any segment between two consecutive points is longer than 0.833 km, i.e. implies more than 200 km/h over 15 s. These are GPS jumps or signal gaps (points missing), after which the 15 s-per-point timing no longer holds. The trips are kept, and `distance_km` is still the plain sum over all segments, so distance and speed queries can exclude them with `has_gap = 0`. `is_valid` is a `VIRTUAL` generated column (`n_points >= 3`). It takes no storage and can never disagree with `n_points`.

### 1.5 Time zone
`TIMESTAMP` is unix time, i.e. UTC. We convert it to **local Porto time (`Europe/Lisbon`: WET = UTC+0 in winter, WEST = UTC+1 in summer)** and store it as a naive `DATETIME`. The reason is that queries 4b (time-of-day bands) and 8 (calendar-day midnight crossers) only make sense in the local day of the city. The conversion is done in Python with `zoneinfo`, because the MySQL Docker image has no time-zone tables loaded, so `CONVERT_TZ` with named zones would return NULL. `start_unix` keeps the raw value, so exact interval arithmetic is available even across DST changes (for example idle time in query 10: `next.start_unix - (prev.start_unix + prev.duration_s)`).

### 1.6 Indexes and how each query is served
| query | served by |
|---|---|
| 1 counts | `COUNT(*)` on taxi and trip. Total GPS points = `SUM(trip.n_points)` (cheap), or `COUNT(*)` on gps_point (a PK scan of about 3 GB, a few tens of seconds) |
| 2, 3, 4a, 5 | `idx_trip_taxi_start (taxi_id, start_time)`, grouped by taxi |
| 4b | `trip.call_type` (+ `idx_trip_call_type`), `duration_s`, `distance_km`, `HOUR(start_time)` |
| 6 City Hall 100 m | bounding-box prefilter on `trip.min/max_lat/lon`, then PK range scans on `gps_point` for candidate trips only, then an exact check with `ST_Distance_Sphere` |
| 7 invalid | `WHERE n_points < 3` / `is_valid = 0` |
| 8 midnight | `DATE(end_time) > DATE(start_time)` (use `= DATE(start_time) + INTERVAL 1 DAY` for exactly "the next day") |
| 9 circular | `ST_Distance_Sphere(POINT(start_lon,start_lat), POINT(end_lon,end_lat)) <= 50` (restrict to valid trips) |
| 10 idle | `LAG()` over `PARTITION BY taxi_id ORDER BY start_time`, which walks `idx_trip_taxi_start` |

For query 6 we deliberately did **not** add a `(lat, lon)` index on `gps_point`. It would cost about 1.9 GB of disk, plus temporary sort space, and slow down the bulk load, all for a single query. The trip bounding box (4 cheap columns on the small table) cuts the candidates down to trips whose box overlaps the 100 m square around City Hall. Only those trips' points are then read, as contiguous clustered PK ranges. If query 6 turns out too slow, the index can be added after the load with `ALTER TABLE gps_point ADD INDEX idx_gps_lat_lon (lat, lon);`, provided the disk allows it.

Sketch for query 6 (100 m ≈ 0.000899° latitude and ≈ 0.001193° longitude at 41.16° N):
```sql
SELECT DISTINCT t.id, t.trip_code, t.taxi_id
FROM trip t JOIN gps_point p ON p.trip_id = t.id
WHERE t.min_lat <= 41.15794 + 0.0009 AND t.max_lat >= 41.15794 - 0.0009
  AND t.min_lon <= -8.62911 + 0.0012 AND t.max_lon >= -8.62911 - 0.0012
  AND p.lat BETWEEN 41.15794 - 0.0009 AND 41.15794 + 0.0009
  AND p.lon BETWEEN -8.62911 - 0.0012 AND -8.62911 + 0.0012
  AND ST_Distance_Sphere(POINT(p.lon, p.lat), POINT(-8.62911, 41.15794)) <= 100;
```
(`POINT(x, y)` takes longitude first. `ST_Distance_Sphere` uses R = 6,370,986 m, while the `haversine` package uses 6,371,008.8 m, a difference of 0.0003 %.)

### 1.7 Foreign keys and cascade rules
| FK | ON DELETE | ON UPDATE | reason |
|---|---|---|---|
| `gps_point.trip_id → trip.id` | CASCADE | CASCADE | points are weak entities with no meaning without their trip. Deleting a trip, e.g. during cleaning, removes its points |
| `trip.taxi_id → taxi.taxi_id` | CASCADE | CASCADE | removing a taxi removes its trips and, through the cascade, their points |
| `trip.call_type → call_type.code` | RESTRICT | CASCADE | a code that is in use must not be deleted by accident. Renaming a code propagates |

### 1.8 Invalid and empty trips
All CSV rows that survive cleaning are kept as `trip` rows, including trips with 0, 1 or 2 points, so query 7 can count them (`n_points < 3`) and the query 1 counts reflect the whole dataset. Their points (0–2) are still inserted. Queries where they would add noise (4b averages, 5, 9, 10) can filter with `is_valid = 1`. Each query states whether it uses all trips or only valid ones.

---

## 2. Column mapping (CSV → tables)

How `insert_data.py` fills each column. Cleaning rules are in section 2.4 of `docs/report.md`.


**taxi**
| column | value |
|---|---|
| `taxi_id` | `int(TAXI_ID)` – one row per distinct value |

**trip** (one row per CSV row that survives cleaning)
| column | type | how it is computed |
|---|---|---|
| `id` | INT UNSIGNED | assigned by the loader: 1, 2, 3, … in CSV order. It is inserted explicitly (not via AUTO_INCREMENT), so gps_point rows can be written without looking the id up |
| `trip_code` | BIGINT UNSIGNED | `int(TRIP_ID)` (19 digits, fits in BIGINT). After cleaning each TRIP_ID occurs once, enforced by `UNIQUE KEY uq_trip_code` |
| `taxi_id` | INT UNSIGNED | `int(TAXI_ID)` |
| `call_type` | CHAR(1) | `'A'`/`'B'`/`'C'` |
| `origin_call` | INT UNSIGNED / NULL | `int(ORIGIN_CALL)` if non-empty, else NULL. NULL unless call_type = 'A' |
| `origin_stand` | SMALLINT UNSIGNED / NULL | `int(ORIGIN_STAND)` if non-empty, else NULL. NULL unless call_type = 'B' |
| `day_type` | CHAR(1) | `DAY_TYPE` as-is (A/B/C, CHECK-constrained) |
| `missing_data` | BOOLEAN | `1` if `MISSING_DATA == "True"`, else `0` |
| `start_unix` | INT UNSIGNED | `int(TIMESTAMP)` (unix seconds, UTC) |
| `start_time` | DATETIME | `datetime.fromtimestamp(ts, ZoneInfo("Europe/Lisbon")).replace(tzinfo=None)` → **local naive** datetime |
| `n_points` | SMALLINT UNSIGNED | `len(polyline)` **after** any point cleaning. Equals the number of gps_point rows for the trip, and must be `< 65536` |
| `duration_s` | INT UNSIGNED | **seconds**: `15 * (n_points - 1)` if `n_points >= 1`, else `0` |
| `end_time` | DATETIME | `start_time + timedelta(seconds=duration_s)` (local, i.e. the time of the last point) |
| `distance_km` | DECIMAL(9,3) | **kilometres**: `sum(haversine((lat[i], lon[i]), (lat[i+1], lon[i+1]), unit=Unit.KILOMETERS))` over consecutive points. `0` if `n_points < 2`. Rounded to 3 decimals. **Note the order**: POLYLINE is `[lon, lat]`, while haversine takes `(lat, lon)` |
| `start_lat`, `start_lon` | DECIMAL | first point: `polyline[0][1]`, `polyline[0][0]`. NULL if `n_points = 0` |
| `end_lat`, `end_lon` | DECIMAL | last point: `polyline[-1][1]`, `polyline[-1][0]`. NULL if `n_points = 0` |
| `min_lat`, `max_lat`, `min_lon`, `max_lon` | DECIMAL | min/max over all points. NULL if `n_points = 0` |
| `has_gap` | BOOLEAN | `1` if any consecutive-point segment is > 200 km/h × 15 s = 0.8333 km (haversine), else `0`. `0` if `n_points < 2` |
| `is_valid` | generated | not inserted (VIRTUAL column computed by MySQL) |

**gps_point** (one row per point of each trip)
| column | value |
|---|---|
| `trip_id` | the `trip.id` of the trip |
| `seq` | 0-based index in POLYLINE (0, 1, 2, …). The point's time is then `start_time + 15*seq` s |
| `lat` | `point[1]` (second element), 6 decimals |
| `lon` | `point[0]` (first element), 6 decimals |

Range limits: `lat` must be within ±99.999999 (valid range ±90) and `lon` within ±999.999999 (valid range ±180). Values are written as strings or floats with at most 6 decimals. MySQL rounds anything with more decimals.
