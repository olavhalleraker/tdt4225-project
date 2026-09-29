# TDT4225 Assignment 2 – Porto Taxi Trajectories (findings report)

Working draft that collects every finding so far. It follows the structure of the official report template (Introduction, Results, Discussion, AI use) so sections can be moved into the final PDF.

Sources: `eda/eda.py` (checks S1–S15, figures with `--figures`), `sql/schema.sql`, `docs/schema.md`, `docs/cleaning.md`.

---

## 1. Introduction

We analyse the Porto Taxi Trajectory dataset: 1.7M taxi trips from July 2013 to June 2014. Each trip has a GPS point every 15 s. The work has three parts: (1) exploratory data analysis and cleaning, (2) designing a MySQL schema and inserting the cleaned data, and (3) answering ten questions with SQL and Python.

**Setup.** MySQL 8.0.39 runs locally in Docker (`docker-compose.yml`). Python 3 uses `mysql-connector-python`, `pandas`, `haversine` and `tabulate`. Credentials are read from a `.env` file that is not committed. The code is in the group's Git repository.

---

## 2. Part 1 – Exploratory data analysis

Each finding below has a check number. Running `.venv/bin/python eda/eda.py <number>` reproduces it.

### 2.1 Size and structure

| | Value |
|---|---|
| Trips (CSV rows) | **1,710,670** |
| GPS points | **83,409,386** |
| Taxis | **448** |
| Time span | 2013-07-01 – 2014-06-30 (exactly one year) |
| Columns | TRIP_ID, CALL_TYPE, ORIGIN_CALL, ORIGIN_STAND, TAXI_ID, TIMESTAMP, DAY_TYPE, MISSING_DATA, POLYLINE |

### 2.2 Metadata columns

- **Empty values (S1).** Only ORIGIN_CALL (78.7 % empty) and ORIGIN_STAND (52.9 % empty) have empty values. This is by design, because they only apply to call types A and B. They are stored as `NULL`.
- **TRIP_ID format (S2).** In every row, TRIP_ID is the string `TIMESTAMP || '6' || TAXI_ID`. The ID therefore carries no information of its own.
- **Duplicate TRIP_IDs (S3).** 80 IDs occur more than once (79 twice, 1 three times), which is 81 extra rows. In only 3 of the 80 is the POLYLINE identical across the copies. The rest are *different* trips that share an ID, often a short stub next to the real trip. As a result, TRIP_ID cannot be the primary key without cleaning.
- **CALL_TYPE (S4).** A (central) 21.3 %, B (stand) 47.8 %, C (street) 30.9 %. The origin columns follow the rules in the assignment, with one exception: **11,302 B trips have no ORIGIN_STAND.**
- **DAY_TYPE (S5).** The value is **'A' for 100 % of rows**, including Christmas Day. The column carries no information.
- **MISSING_DATA (S6).** Only **10** rows are `True`. Meanwhile ~38k trips flagged `False` have obvious signal gaps (see S13). The flag is therefore not a reliable indicator of missing data.
- **Taxis (S7).** 448 taxis. Trips per taxi: median 3,732, mean 3,818.5, max 10,746. Six taxis have fewer than 10 trips.
- **Time and timezone (S8).** TIMESTAMP is unix time (UTC), and Porto uses Europe/Lisbon time (UTC+0 in winter, UTC+1 in summer). **157,929 trips fall in a different 6-hour band depending on the timezone**, so the choice changes the answer to query 4b. We use local Porto time.

  | Band | Europe/Lisbon | UTC |
  |---|---|---|
  | 00–06 | 18.35 % | 18.35 % |
  | 06–12 | 27.61 % | 28.73 % |
  | 12–18 | 30.99 % | 30.86 % |
  | 18–24 | 23.04 % | 22.05 % |

Figures: `eda/figures/metadata_call_type.png`, `metadata_trips_per_taxi.png`, `metadata_trips_by_hour.png`, `metadata_trips_by_month.png`, `metadata_trips_by_weekday.png`.

### 2.3 Trajectories (POLYLINE)

- **Points per trip (S9).** Median 41, mean 48.8, max 3,881. **43,904 trips (2.57 %) are invalid (< 3 points):**

  | Points | Trips |
  |---|---|
  | 0 (empty `[]`) | 5,901 |
  | 1 | 30,609 |
  | 2 | 7,394 |

- **Duration (S10).** Duration is (points − 1) × 15 s. Median 10.2 min, p99 47.5 min, max 16 h. 847 trips last more than 3 h and 403 more than 4 h.
- **Distance (S11).** Distance is the sum of haversine distances between consecutive points. Median 4.0 km, p99 24.4 km, max 1,229 km. 15 trips never move.
- **Points outside the area (S12).** 7,142 trips leave a generous Porto box. These are mostly real motorway trips, so we do *not* clip the data to Porto. Only **32 trips** have points outside mainland Portugal, with coordinates as far off as latitude 51 and longitude 53. These points are certainly GPS errors.
- **GPS jumps (S13).** Points are 15 s apart, so a gap of more than 833 m between two consecutive points means driving faster than 200 km/h. **38,216 trips (2.2 %)** contain at least one such jump, and 3,764 contain one faster than 1,000 km/h. Most jumps go one way and never come back. That looks like missing points (a signal gap) rather than a single bad point, and it is what inflates the extreme distances.
- **Precision (S14).** No coordinate has more than 6 decimals (6 decimals ≈ 0.1 m). `DECIMAL` therefore stores the values exactly.
- **Reference counts (S15)**, computed from the raw CSV to sanity-check the SQL later:
  - 126,295 valid trips pass within 100 m of City Hall.
  - 21,472 valid trips start and end within 50 m of each other.

Figures: `eda/figures/polyline_points_per_trip.png`, `polyline_duration.png`, `polyline_distance.png`, `polyline_max_speed.png`, `polyline_map_hexbin.png`.

### 2.4 Cleaning decisions

| Rule | Rows affected (actual load) | Reason |
|---|---|---|
| Keep only the copy with the most points for each duplicate TRIP_ID (ties: first occurrence) | 81 trips / 103 points dropped | The copies are different trips. Every dropped copy had at most 6 points. |
| Drop trips with any point outside mainland Portugal | 32 trips / 870 points dropped | The points are obviously wrong and would distort distances. |
| Keep trips with < 3 points and flag them `is_valid = 0` | 43,815 kept (5,894 empty) | Query 7 must count them. The other queries filter them out. |
| Keep trips with a > 200 km/h jump and flag them `has_gap = 1` | 38,198 flagged | Dropping them would change the trip counts. Distance queries can exclude them. |
| Keep long trips and trips outside Porto | – | They are real data. |
| Store empty ORIGIN_* as NULL | – | Consistent handling of missing values. |
| Store DAY_TYPE and MISSING_DATA but don't use them for cleaning | – | S5 and S6 show they are unreliable. |

**After cleaning: 448 taxis, 1,710,557 trips and 83,408,413 GPS points.** Only 113 rows were removed in total. The invalid and gap counts are slightly lower than in the EDA because several of the removed rows were short trips.

### 2.5 Insertion

The loader is `insert_data.py` (details in `docs/cleaning.md`) and works in two passes:
- Pass 1 decides which rows to keep, i.e. deduplicates the IDs and finds points outside Portugal.
- Pass 2 parses the trips in parallel batches of 20k and bulk-loads them with `LOAD DATA LOCAL INFILE`, with foreign key checks turned off during the load.

The full load took about 4 minutes. Afterwards it verified that:
- `COUNT(gps_point) = SUM(trip.n_points)`
- there are no orphan rows
- five spot-checked trips match the raw CSV.

The database uses 3.3 GB on disk, 3.0 GB of which is `gps_point`.

---

## 3. Part 1 – Schema

Four tables. Full DDL is in `sql/schema.sql`, and the reasoning is in `docs/schema.md`.

```
call_type (code PK, description)                      -- A/B/C lookup
taxi      (taxi_id PK)                                 -- 448 rows
trip      (id PK, trip_code, taxi_id FK, call_type FK, origin_call, origin_stand,
           day_type, missing_data, start_unix, start_time, end_time, duration_s,
           n_points, is_valid (generated), distance_km, has_gap,
           start/end lat/lon, min/max lat/lon)         -- ~1.7M rows
gps_point (trip_id FK, seq, lat, lon) PK(trip_id, seq) -- ~83M rows
```

Key choices:
- **Surrogate `trip.id` (INT).** TRIP_ID is not unique in the raw data. A 4-byte key also saves ~320 MB in `gps_point` compared with an 8-byte BIGINT.
- **Precomputed trip summaries** (times, duration, distance, start/end point, bounding box). Queries 1–5 and 7–10 never have to scan 83M points. Query 6 uses the bounding box to prefilter before looking at points.
- **Compact `gps_point`.** It has a composite PK, `DECIMAL(8,6)`/`DECIMAL(9,6)` coordinates, no per-point timestamp (the time is `start_time + 15·seq` s), and no secondary indexes. The estimate is ~36 B/row, or ~2.9 GB.
- **Foreign keys.** `gps_point → trip` and `trip → taxi` use `ON DELETE CASCADE`. `trip → call_type` uses `RESTRICT`.
- **Time.** DATETIME stores local Europe/Lisbon time. `start_unix` keeps the raw value.

---

## 4. Part 2 – Query results

Programs: `queries/part2_q1_5.py` and `queries/part2_q6_10.py`. Console output is in `queries/output/`, and the full answers, SQL and interpretation are in `docs/results_q1_5.md` and `docs/results_q6_10.md`. @Olav please Add a screenshot for each task.


- Unless stated otherwise, Q1–Q3 count all trips in the cleaned database.
- Q4b–Q10 use only valid trips (≥ 3 points).
- Times are local Porto time (Europe/Lisbon).

| # | Question | Result |
|---|---|---|
| 1 | Taxis, trips, GPS points | **448 taxis, 1,710,557 trips, 83,408,413 GPS points.** Valid trips only: 1,666,742 trips with 83,363,103 points. |
| 2 | Avg trips per taxi | **3,818.2** (3,720.4 counting valid trips only). Min 1, max 10,731. |
| 3 | Top 20 taxis by trips | #1 is 20000080 (10,731), then 20000403 (9,236) and 20000066 (8,443). #20 is 20000235 (6,410). Note: 61 % of #1's trips have fewer than 3 points. |
| 4a | Most used call type per taxi | B for 354 taxis (79.0 %), C for 82 (18.3 %) and A for 12 (2.7 %). One tie (20000344, B = C = 1,900) is resolved alphabetically. |
| 4b | Per call type: duration, distance, time bands | See the table below. |
| 5 | Taxis with most hours and distance | **20000904** leads both, with 1,961.0 h and 62,788 km. #2 on hours is 20000129 (1,649.6 h). #2 on distance is 20000351 (56,639 km), which is only 51st on hours. |
| 6 | Trips within 100 m of City Hall | **126,295** (126,532 including invalid trips) |
| 7 | Invalid trips (< 3 points) | **43,815** (2.56 %): 5,894 with 0 points, 30,532 with 1, 7,389 with 2 |
| 8 | Midnight crossers | **8,028** (0.48 % of valid trips). None crosses more than one midnight. |
| 9 | Circular trips (start–end ≤ 50 m) | **21,467** (1.29 %). 7,608 of them are nearly stationary (< 100 m driven), and 12,177 drove ≥ 1 km. |
| 10 | Top 20 taxis by avg idle time | The top 2 (20000941 with 1,746.6 h, 20000969 with 282.1 h) barely operated. From #3 (20000312, 13.1 h) the values are around 5–13 h. Counting only gaps of 0–8 h, the top is 85–106 min. |

**Q4b (valid trips):**

| Call type | Avg duration (min) | Avg distance (km) | 00–06 | 06–12 | 12–18 | 18–24 |
|---|---:|---:|---:|---:|---:|---:|
| A (central) | 12.59 | 5.43 | 12.05 % | 31.82 % | 32.59 % | 23.55 % |
| B (stand) | 11.23 | 5.11 | 13.02 % | 27.76 % | 34.56 % | 24.65 % |
| C (street) | 13.67 | 6.67 | 31.78 % | 24.40 % | 24.00 % | 19.82 % |

Excluding `has_gap` trips lowers the average distances by 2–4 % (A 5.34, B 5.01, C 6.40 km). Street hails (C) dominate at night.

**Cross-checks against the EDA:**
- Q6 matches the raw-CSV reference exactly (126,295).
- Q9 differs by 5 trips (21,472 → 21,467) because of cleaning.
- Q7 matches the load report.

---

## 5. Discussion (notes so far)

- **The dataset's flags are unreliable.** DAY_TYPE is constant and MISSING_DATA marks only 10 trips. We derive data quality from the trajectories instead (point count, speed between points).
- **Choosing the timezone** affects query 4b and query 8. This choice is not given in the assignment, so we state it explicitly.
- **Duplicate IDs** meant the obvious primary key (TRIP_ID) could not be used.
- **Disk and performance.** 83M points was the main practical constraint. Turning off the binary log, loading in bulk and keeping `gps_point` narrow kept the database at a few GB.
- **Distance values** include GPS jumps unless `has_gap` trips are excluded. We report which variant each query uses. In Q4b the difference is only 2–4 %.
- **Invalid trips skew per-taxi rankings.** The top taxi in Q3 has 61 % invalid trips. In Q10 the two highest averages come from taxis with 4 and 25 trips.
- **Idle time definition (Q10).** Idle time is next start minus previous end. Overlapping trips (792 gaps) are excluded. Without a cap, off-days dominate the average, which is why we also report an 8 h cap.
  - *@Olav – to discuss:* The top 2 in the plain Q10 ranking are not meaningful answers. Taxi 20000941 has only 3 gaps (average 1,746 h ≈ 73 days) and 20000969 has 24 gaps. An average over so few gaps says little about how much a taxi is actually idle. Options: (a) keep the plain ranking as the main answer and explain the top 2 (as now), (b) make the main answer a ranking restricted to taxis with enough data, e.g. `HAVING n_gaps >= 100`, and state the threshold, or (c) use the 8 h-capped average as the main answer. Which one do we go with?
- **Q6 checks GPS points only**, not the line segments between them. A trip that drives past City Hall between two samples 15 s apart can therefore be missed.

---

## 6. AI use

We started with a group meeting where we went through the assignment and agreed on a plan: what the EDA should investigate, how the data should be cleaned, how the schema should be structured and how each query should be approached. We then used Claude Code (AI agents) to sketch out this plan as code. The agents wrote first versions of the EDA script, the schema, the loader and the Part 2 queries, based on our instructions.

We then went through the output together. Where the agents suggested different solutions, we chose between them and justified the choice (for example the cleaning rules in section 2.4, and keeping trips with fewer than 3 points so that query 7 can count them). We reviewed and adjusted the code, and verified the results by cross-checking the query results against the EDA and inspecting the data directly in the database. We also used the AI to explain features of the dataset and MySQL functionality. All interpretations and choices in this report are our own.
