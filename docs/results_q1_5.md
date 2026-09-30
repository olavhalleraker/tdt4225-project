# Part 2 results – queries 1–5

Program: `queries/part2_q1_5.py` (run `.venv/bin/python queries/part2_q1_5.py [task ...]`). Full console output: `queries/output/part2_q1_5.txt`. Full Q4a table: `queries/output/q4a_full.txt`.

General conventions:
- Q1–Q3 count **all** trips in the cleaned database (1,710,557, including the 43,815 trips with fewer than 3 points). The valid-only numbers (`is_valid = 1`, i.e. `n_points >= 3`) are given as a secondary result.
- Q4b and Q5 use **valid trips only**. They are also shown without trips that have a GPS jump (`has_gap = 1`, any segment > 200 km/h), because `distance_km` is inflated for those trips.
- Time bands use the local Porto start time (`start_time`, Europe/Lisbon).
- Duration is `duration_s = 15 × (n_points − 1)`. Distance is the haversine sum over consecutive points.

Runtimes (MySQL 8.0.39 in Docker, warm buffer pool) are all under 2 s per query. See the table at the end.

---

## Q1. How many taxis, trips, and total GPS points are there?

| taxis | trips | GPS points |
|---:|---:|---:|
| 448 | 1,710,557 | 83,408,413 |

Valid trips only: 1,666,742 trips with 83,363,103 points. All 448 taxis have at least one valid trip.

```sql
SELECT
    (SELECT COUNT(*) FROM taxi)       AS taxis,
    (SELECT COUNT(*) FROM trip)       AS trips,
    (SELECT COUNT(*) FROM gps_point)  AS gps_points;

-- secondary: valid-only numbers + cross-check
SELECT
    COUNT(DISTINCT taxi_id)                       AS taxis_with_valid_trip,
    SUM(is_valid = 1)                             AS valid_trips,
    SUM(CASE WHEN is_valid = 1 THEN n_points END) AS points_in_valid_trips,
    SUM(n_points)                                 AS sum_n_points_all_trips
FROM trip;
```

The GPS points are counted directly in `gps_point`. `SUM(trip.n_points)` gives the same 83,408,413, which confirms the load. Compared with the raw CSV (1,710,670 rows, 83,409,386 points), cleaning removed 113 trips and 973 points (see section 2.4 in `docs/report.md`). Trips with fewer than 3 points make up 2.6 % of the trips but only 45,310 points (0.05 %).

## Q2. What is the average number of trips per taxi?

| taxis | trips | avg trips / taxi | std. dev. | min | max | avg valid trips / taxi |
|---:|---:|---:|---:|---:|---:|---:|
| 448 | 1,710,557 | **3,818.21** | 1,641.93 | 1 | 10,731 | 3,720.41 |

```sql
SELECT
    COUNT(*)                      AS taxis,
    SUM(n_trips)                  AS trips,
    ROUND(AVG(n_trips), 2)        AS avg_trips_per_taxi,
    ROUND(STDDEV_POP(n_trips), 2) AS stddev,
    MIN(n_trips)                  AS min_trips,
    MAX(n_trips)                  AS max_trips,
    ROUND(AVG(n_valid), 2)        AS avg_valid_trips_per_taxi
FROM (
    SELECT tx.taxi_id,
           COUNT(t.id)                  AS n_trips,
           COALESCE(SUM(t.is_valid), 0) AS n_valid
    FROM taxi tx
    LEFT JOIN trip t ON t.taxi_id = tx.taxi_id
    GROUP BY tx.taxi_id
) per_taxi;
```

The average is computed per taxi from the `taxi` table with a LEFT JOIN, so a taxi with no trips would count as 0. In this dataset every taxi has at least one trip. The spread is large: some taxis (e.g. 20000931, 20000940, 20000970) have a single trip, which suggests they only joined or left the fleet at the edge of the one-year period.

## Q3. Top 20 taxis with the most trips

| rank | taxi_id | trips | valid trips |
|---:|---:|---:|---:|
| 1 | 20000080 | 10,731 | 4,211 |
| 2 | 20000403 | 9,236 | 7,515 |
| 3 | 20000066 | 8,443 | 5,952 |
| 4 | 20000364 | 7,821 | 7,270 |
| 5 | 20000483 | 7,729 | 7,623 |
| 6 | 20000129 | 7,608 | 7,101 |
| 7 | 20000307 | 7,497 | 7,290 |
| 8 | 20000621 | 7,276 | 7,250 |
| 9 | 20000089 | 7,265 | 6,976 |
| 10 | 20000424 | 7,176 | 7,010 |
| 11 | 20000492 | 7,171 | 7,115 |
| 12 | 20000529 | 6,937 | 6,918 |
| 13 | 20000616 | 6,924 | 6,466 |
| 14 | 20000678 | 6,538 | 6,475 |
| 15 | 20000372 | 6,535 | 6,257 |
| 16 | 20000304 | 6,505 | 6,374 |
| 17 | 20000042 | 6,467 | 6,437 |
| 18 | 20000325 | 6,459 | 6,351 |
| 19 | 20000179 | 6,430 | 6,366 |
| 20 | 20000235 | 6,410 | 6,387 |

```sql
SELECT taxi_id,
       COUNT(*)      AS n_trips,
       SUM(is_valid) AS n_valid_trips
FROM trip
GROUP BY taxi_id
ORDER BY n_trips DESC, taxi_id
LIMIT 20;
```

The ranking counts all trips. Taxi 20000080 is first, but 6,520 of its 10,731 trips (61 %) have fewer than 3 points (5,287 of them a single point). Counted by valid trips it would not be in first place, so its trip count is probably inflated by aborted or faulty recordings. Taxis 20000403 and 20000066 also have many invalid trips. For the other taxis the two counts are close.

## Q4a. Most used call type per taxi

Taxis per most used call type (all 448 taxis):

| most used call type | taxis | share of taxis |
|---|---:|---:|
| A (dispatched from central) | 12 | 2.7 % |
| B (taxi stand) | 354 | 79.0 % |
| C (hailed on the street) | 82 | 18.3 % |

First 20 rows (full table in `queries/output/q4a_full.txt`):

| taxi_id | most used | trips of that type | trips total | % of taxi's trips | n_tied |
|---:|:-:|---:|---:|---:|---:|
| 20000001 | B | 1,311 | 2,755 | 47.6 | 1 |
| 20000002 | B | 1,576 | 3,869 | 40.7 | 1 |
| 20000003 | C | 844 | 1,898 | 44.5 | 1 |
| 20000004 | B | 2,680 | 4,935 | 54.3 | 1 |
| 20000005 | B | 3,034 | 6,093 | 49.8 | 1 |
| 20000006 | B | 1,702 | 3,363 | 50.6 | 1 |
| 20000007 | B | 2,770 | 5,581 | 49.6 | 1 |
| 20000008 | B | 2,665 | 4,848 | 55.0 | 1 |
| 20000009 | B | 2,099 | 4,775 | 44.0 | 1 |
| 20000010 | B | 2,842 | 5,973 | 47.6 | 1 |
| 20000011 | B | 2,622 | 5,963 | 44.0 | 1 |
| 20000012 | B | 2,257 | 5,546 | 40.7 | 1 |
| 20000013 | B | 2,581 | 5,006 | 51.6 | 1 |
| 20000014 | B | 2,523 | 4,145 | 60.9 | 1 |
| 20000015 | B | 2,819 | 4,963 | 56.8 | 1 |
| 20000017 | B | 1,129 | 2,732 | 41.3 | 1 |
| 20000018 | B | 958 | 1,755 | 54.6 | 1 |
| 20000020 | B | 2,304 | 4,248 | 54.2 | 1 |
| 20000021 | B | 1,901 | 3,313 | 57.4 | 1 |
| 20000022 | B | 1,990 | 4,014 | 49.6 | 1 |

```sql
WITH per_type AS (
    SELECT taxi_id, call_type, COUNT(*) AS n
    FROM trip
    GROUP BY taxi_id, call_type
),
ranked AS (
    SELECT taxi_id, call_type, n,
           SUM(n) OVER (PARTITION BY taxi_id) AS taxi_total,
           MAX(n) OVER (PARTITION BY taxi_id) AS max_n,
           ROW_NUMBER() OVER (PARTITION BY taxi_id ORDER BY n DESC, call_type) AS rn
    FROM per_type
)
SELECT r.taxi_id,
       r.call_type                        AS most_used_call_type,
       r.n                                AS n_trips_of_type,
       r.taxi_total                       AS n_trips_total,
       ROUND(100 * r.n / r.taxi_total, 1) AS pct_of_taxi_trips,
       (SELECT COUNT(*) FROM per_type p
         WHERE p.taxi_id = r.taxi_id AND p.n = r.max_n) AS n_tied
FROM ranked r
WHERE r.rn = 1
ORDER BY r.taxi_id;
```

"Most used" means the call type with the most trips for the taxi (all trips). **Ties** are broken alphabetically (A before B before C) through `ORDER BY n DESC, call_type` in the window, and `n_tied` shows how many call types share the top count. Exactly one taxi has a tie: 20000344 has 1,900 B and 1,900 C trips, and B is reported. B dominates, as expected from its 47.8 % share of all trips. Only 12 taxis mainly work on dispatched (A) calls.

## Q4b. Per call type: average duration, distance and time-band shares

**Main result – valid trips (`is_valid = 1`):**

| call type | trips | avg duration (min) | avg distance (km) | 00–06 | 06–12 | 12–18 | 18–24 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 362,167 | 12.59 | 5.427 | 12.05 % | 31.82 % | 32.59 % | 23.55 % |
| B | 807,438 | 11.23 | 5.111 | 13.02 % | 27.76 % | 34.56 % | 24.65 % |
| C | 497,137 | 13.67 | 6.668 | 31.78 % | 24.40 % | 24.00 % | 19.82 % |
| all | 1,666,742 | 12.25 | 5.644 | 18.40 % | 27.64 % | 30.98 % | 22.97 % |

**Variant – valid trips without GPS jumps (`is_valid = 1 AND has_gap = 0`):**

| call type | trips | avg duration (min) | avg distance (km) | 00–06 | 06–12 | 12–18 | 18–24 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 356,682 | 12.57 | 5.343 | 12.01 % | 31.87 % | 32.60 % | 23.52 % |
| B | 788,459 | 11.21 | 5.007 | 13.02 % | 27.80 % | 34.60 % | 24.58 % |
| C | 483,552 | 13.48 | 6.396 | 31.71 % | 24.36 % | 24.06 % | 19.86 % |
| all | 1,628,693 | 12.18 | 5.493 | 18.35 % | 27.67 % | 31.03 % | 22.95 % |

```sql
SELECT
    COALESCE(call_type, 'ALL')                              AS call_type,
    COUNT(*)                                                AS n_trips,
    ROUND(AVG(duration_s) / 60, 2)                          AS avg_duration_min,
    ROUND(AVG(distance_km), 3)                              AS avg_distance_km,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 0  AND 5 ), 2) AS pct_00_06,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 6  AND 11), 2) AS pct_06_12,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 12 AND 17), 2) AS pct_12_18,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 18 AND 23), 2) AS pct_18_24
FROM trip
WHERE is_valid = 1            -- variant: AND has_gap = 0
GROUP BY call_type WITH ROLLUP;
```

Shares are per call type (each row sums to 100 %). A band is assigned by the hour of the local start time: 0–5, 6–11, 12–18 means hours 12–17, and 18–23. Street-hailed trips (C) are clearly different. They are the longest (13.7 min, 6.7 km), and almost a third start between 00 and 06, which fits night-life traffic when stands and the dispatch central are less used. A and B mostly start in daytime, and B trips (from a stand) are the shortest. Removing `has_gap` trips lowers the average distance by 2–4 % but hardly changes the durations or the time-band shares, so the conclusions do not depend on it. (Check: over all 1,710,557 trips the bands are 18.35 / 27.61 / 30.99 / 23.04 %, which matches the EDA.)

## Q5. Taxis with the most hours and distance driven

**Main result – valid trips (`is_valid = 1`), top 20 by total hours:**

| hours rank | taxi_id | trips | total hours | total km | km rank |
|---:|---:|---:|---:|---:|---:|
| 1 | 20000904 | 4,925 | 1,961.0 | 62,788.2 | 1 |
| 2 | 20000129 | 7,101 | 1,649.6 | 36,542.6 | 31 |
| 3 | 20000307 | 7,290 | 1,587.0 | 40,015.4 | 12 |
| 4 | 20000529 | 6,918 | 1,507.3 | 42,389.0 | 5 |
| 5 | 20000276 | 5,795 | 1,419.8 | 47,068.1 | 3 |
| 6 | 20000436 | 5,488 | 1,410.6 | 44,298.6 | 4 |
| 7 | 20000483 | 7,623 | 1,397.6 | 36,174.2 | 33 |
| 8 | 20000372 | 6,257 | 1,387.1 | 40,687.0 | 11 |
| 9 | 20000616 | 6,466 | 1,348.7 | 35,400.2 | 35 |
| 10 | 20000179 | 6,366 | 1,324.2 | 39,840.4 | 13 |
| 11 | 20000574 | 5,849 | 1,301.8 | 37,551.0 | 22 |
| 12 | 20000235 | 6,387 | 1,287.9 | 36,729.6 | 26 |
| 13 | 20000621 | 7,250 | 1,282.4 | 31,985.2 | 64 |
| 14 | 20000364 | 7,270 | 1,279.8 | 41,596.3 | 8 |
| 15 | 20000435 | 5,824 | 1,277.6 | 40,992.6 | 9 |
| 16 | 20000199 | 6,136 | 1,270.7 | 39,563.4 | 14 |
| 17 | 20000446 | 5,276 | 1,266.6 | 35,163.6 | 38 |
| 18 | 20000011 | 5,919 | 1,266.1 | 38,497.9 | 19 |
| 19 | 20000395 | 5,481 | 1,263.8 | 34,207.9 | 45 |
| 20 | 20000492 | 7,115 | 1,262.5 | 33,190.6 | 54 |

Top 5 by total distance (main result): 20000904 (62,788.2 km), 20000351 (56,638.8 km, only hours rank 51), 20000276 (47,068.1 km), 20000436 (44,298.6 km), 20000529 (42,389.0 km).

**Variant – valid trips without GPS jumps (`is_valid = 1 AND has_gap = 0`), top 20 by total hours:**

| hours rank | taxi_id | trips | total hours | total km | km rank |
|---:|---:|---:|---:|---:|---:|
| 1 | 20000904 | 4,755 | 1,853.0 | 58,225.5 | 1 |
| 2 | 20000129 | 7,045 | 1,631.5 | 35,561.7 | 21 |
| 3 | 20000307 | 7,146 | 1,555.3 | 38,636.7 | 9 |
| 4 | 20000529 | 6,802 | 1,469.4 | 41,015.0 | 4 |
| 5 | 20000436 | 5,410 | 1,383.0 | 42,914.3 | 3 |
| 6 | 20000483 | 7,451 | 1,373.9 | 35,167.0 | 25 |
| 7 | 20000372 | 6,123 | 1,357.2 | 39,262.7 | 8 |
| 8 | 20000616 | 6,390 | 1,319.1 | 34,385.1 | 29 |
| 9 | 20000276 | 5,408 | 1,309.2 | 40,884.0 | 5 |
| 10 | 20000179 | 6,289 | 1,301.5 | 38,545.9 | 10 |
| 11 | 20000621 | 7,154 | 1,262.6 | 31,126.9 | 54 |
| 12 | 20000235 | 6,306 | 1,262.1 | 35,224.4 | 24 |
| 13 | 20000364 | 7,131 | 1,254.9 | 40,311.7 | 6 |
| 14 | 20000011 | 5,853 | 1,249.9 | 37,459.6 | 13 |
| 15 | 20000199 | 6,050 | 1,239.1 | 37,123.0 | 14 |
| 16 | 20000395 | 5,367 | 1,238.6 | 33,149.6 | 41 |
| 17 | 20000574 | 5,568 | 1,230.5 | 34,426.7 | 28 |
| 18 | 20000492 | 6,883 | 1,223.4 | 31,574.9 | 50 |
| 19 | 20000446 | 5,171 | 1,221.7 | 33,044.4 | 43 |
| 20 | 20000042 | 6,349 | 1,219.0 | 31,347.7 | 52 |

Top 5 by total distance (variant): 20000904 (58,225.5 km), 20000351 (51,567.5 km), 20000436 (42,914.3 km), 20000529 (41,015.0 km), 20000276 (40,884.0 km).

```sql
WITH per_taxi AS (
    SELECT taxi_id,
           COUNT(*)               AS n_trips,
           SUM(duration_s) / 3600 AS total_hours,
           SUM(distance_km)       AS total_km
    FROM trip
    WHERE is_valid = 1            -- variant: AND has_gap = 0
    GROUP BY taxi_id
)
SELECT RANK() OVER (ORDER BY total_hours DESC) AS hours_rank,
       taxi_id, n_trips,
       ROUND(total_hours, 1)                   AS total_hours,
       ROUND(total_km, 1)                      AS total_km,
       RANK() OVER (ORDER BY total_km DESC)    AS km_rank
FROM per_taxi
ORDER BY total_hours DESC, taxi_id
LIMIT 20;
-- top 5 by distance: same CTE, ORDER BY total_km DESC LIMIT 5
```

"Hours driven" is the summed trip duration (time with a passenger on a recorded trip), not the shift length. Idle time between trips is not counted. **Taxi 20000904 leads on both hours (1,961 h) and distance (62,788 km)**, in both variants, even though it has fewer trips than many others (4,925). It has many long trips: 19 trips longer than 4 h contribute 139 h. Hours and distance are only loosely related. Taxi 20000351 is second by distance but only 51st by hours, and taxi 20000621 has many short trips (7,250 trips, hours rank 13, km rank 64). Excluding `has_gap` trips moves some taxis a few places (e.g. 20000276 drops from 5th to 9th by hours), but the leader and most of the top 20 stay the same.

---

## Runtimes

Measured in the program (`time.perf_counter()` around execute + fetchall). Warm buffer pool. (In the program, some SUMs are wrapped in `CAST(... AS UNSIGNED)` for printing only.)

| query | runtime |
|---|---:|
| Q1 counts (incl. `COUNT(*)` on gps_point) | 1.79 s |
| Q1 valid-only / cross-check | 0.40 s |
| Q2 | 1.67 s |
| Q3 | 1.41 s |
| Q4a | 0.35 s |
| Q4b main / variant | 1.30 s / 1.40 s |
| Q5 hours main / variant | 1.29 s / 1.37 s |
| Q5 distance main / variant | 1.29 s / 1.37 s |
