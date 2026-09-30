# Results – Part 2, queries 6–10

Program: `queries/part2_q6_10.py` (`.venv/bin/python queries/part2_q6_10.py [6 7 8 9 10]`).
Full console output: `queries/output/part2_q6_10.txt`. Full result lists: `queries/output/q6_trips.csv`, `q8_midnight_trips.csv`, `q9_circular_trips.csv`, `q10_idle_all_taxis.csv`.
"Valid" means `is_valid = 1`, i.e. at least 3 GPS points (1,666,742 trips). Runtimes were measured on the local Docker MySQL 8.0.39.

## Q6 – Trips that passed within 100 m of Porto City Hall

**Answer:** **126,295 valid trips** passed within 100 m of City Hall (−8.62911, 41.15794). If invalid trips (< 3 points) are counted too, the number is 126,532 (+237). The first 20 trips are in the console output, and all trip ids with their minimum distance are in `q6_trips.csv`.

```sql
SELECT t.id, t.trip_code, t.taxi_id, t.start_time, t.n_points, t.is_valid,
       ROUND(x.min_dist_m, 1) AS min_dist_m
FROM (
    SELECT p.trip_id,
           MIN(ST_Distance_Sphere(POINT(p.lon, p.lat),
                                  POINT(-8.62911, 41.15794))) AS min_dist_m
    FROM trip t
    JOIN gps_point p ON p.trip_id = t.id
    WHERE t.min_lat <= 41.15794 + 0.0010 AND t.max_lat >= 41.15794 - 0.0010
      AND t.min_lon <= -8.62911 + 0.0013 AND t.max_lon >= -8.62911 - 0.0013
      AND p.lat BETWEEN 41.15794 - 0.0010 AND 41.15794 + 0.0010
      AND p.lon BETWEEN -8.62911 - 0.0013 AND -8.62911 + 0.0013
    GROUP BY p.trip_id
    HAVING min_dist_m <= 100
) x
JOIN trip t ON t.id = x.trip_id
ORDER BY t.id;
```

**Approach and assumptions:** `gps_point` has no spatial index, so the query first keeps only trips whose precomputed bounding box overlaps a box around City Hall. 100 m is 0.000899° latitude and 0.001193° longitude at 41.16° N, and the box uses 0.0010° / 0.0013° to be slightly generous. For those 462,270 candidate trips, the points are read through the clustered primary key `(trip_id, seq)`, and the exact spherical distance (`ST_Distance_Sphere`) decides. Only the recorded GPS points are tested, not the straight line segments between them, so a trip that drove past City Hall between two 15-second samples without a point inside 100 m is not counted. Runtime: 7.6 s with a warm buffer pool (31.6 s on the first cold run). The result is identical to the EDA on the raw CSV (126,295).

## Q7 – Number of invalid trips (< 3 GPS points)

**Answer:** **43,815 invalid trips** (2.56 % of 1,710,557).

| n_points | trips |
|---:|---:|
| 0 | 5,894 |
| 1 | 30,532 |
| 2 | 7,389 |
| **total** | **43,815** |

```sql
SELECT n_points, COUNT(*) AS trips
FROM trip
WHERE n_points < 3
GROUP BY n_points WITH ROLLUP;
```

**Interpretation:** most invalid trips have a single point, so the taxi meter was started and stopped almost immediately, or the GPS never logged. 5,894 have an empty POLYLINE. The counts are slightly lower than in the raw-CSV EDA (43,904) because the cleaning dropped 89 short duplicate or out-of-Portugal rows (see section 2.4 in `docs/report.md`). Runtime: 0.2 s.

## Q8 – Trips that started on one calendar day and ended on the next

**Answer:** **8,028 valid trips** cross midnight (0.48 % of valid trips). No trip spans more than one midnight (the maximum `DATEDIFF` is 1). If invalid trips are included the count is 8,029, because only one 2-point trip crosses midnight. A sample of 20 is in the console output, and the full list is in `q8_midnight_trips.csv`.

```sql
SELECT id, trip_code, taxi_id, start_time, end_time,
       ROUND(duration_s / 60, 1) AS duration_min,
       DATEDIFF(end_time, start_time) AS days_crossed
FROM trip
WHERE is_valid = 1
  AND DATE(end_time) > DATE(start_time)
ORDER BY start_time;
```

**Assumptions:** calendar days are local Porto days (Europe/Lisbon), since `start_time` and `end_time` are stored in local time. The end time is the time of the last GPS point (`start + 15 s × (n_points − 1)`). Invalid trips last at most 15 s, so they are excluded as noise, and doing so changes the answer by only 1 trip. Runtime: 0.4 s.

## Q9 – Circular trips (start and end within 50 m)

**Answer:** **21,467 valid trips** (1.29 % of valid trips) end within 50 m of where they started. Of these, **7,608 are near-stationary** (total distance < 0.1 km), 13,859 moved at least 0.1 km, and 12,177 drove at least 1 km, which makes them real round trips. The full list is in `q9_circular_trips.csv`.

```sql
SELECT id, trip_code, taxi_id, start_time, n_points, distance_km,
       ROUND(ST_Distance_Sphere(POINT(start_lon, start_lat),
                                POINT(end_lon, end_lat)), 1) AS start_end_m
FROM trip
WHERE is_valid = 1
  AND ST_Distance_Sphere(POINT(start_lon, start_lat),
                         POINT(end_lon, end_lat)) <= 50
ORDER BY id;
```

**Interpretation:** the start and end points are precomputed on `trip`, so the query never touches `gps_point`. Only valid trips are counted, because with 1 point the start and end are the same point and the test would be meaningless. About a third of the matches are taxis that barely moved (the meter was started and cancelled at the stand, or GPS jitter). These fit the geometric definition but are not real round trips, and a stricter definition would add `distance_km >= 0.1` (13,859 trips). The raw-CSV EDA found 21,472. The difference of 5 comes from cleaning. Runtime: 1.5–2.0 s.

## Q10 – Average idle time between consecutive trips, top 20 taxis

```sql
WITH ordered AS (
    SELECT taxi_id,
           CAST(start_unix AS SIGNED)
             - CAST(LAG(start_unix + duration_s)
                    OVER (PARTITION BY taxi_id ORDER BY start_unix, id)
               AS SIGNED) AS idle_s
    FROM trip
    WHERE is_valid = 1
)
SELECT taxi_id,
       SUM(idle_s >= 0)                                                   AS n_gaps,
       ROUND(AVG(CASE WHEN idle_s >= 0 THEN idle_s END) / 3600, 2)        AS avg_idle_all_h,
       SUM(idle_s BETWEEN 0 AND 28800)                                    AS n_gaps_le_8h,
       ROUND(AVG(CASE WHEN idle_s BETWEEN 0 AND 28800 THEN idle_s END) / 60, 1)
                                                                          AS avg_idle_8h_min,
       SUM(idle_s < 0)                                                    AS n_overlaps
FROM ordered
WHERE idle_s IS NOT NULL
GROUP BY taxi_id
ORDER BY avg_idle_all_h DESC;
```

**Answer (plain average over all gaps ≥ 0, as the assignment asks):**

| # | taxi_id | gaps | avg idle (h) | | # | taxi_id | gaps | avg idle (h) |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 1 | 20000941 | 3 | 1746.61 | | 11 | 20000902 | 1,040 | 7.55 |
| 2 | 20000969 | 24 | 282.07 | | 12 | 20000170 | 6 | 6.73 |
| 3 | 20000312 | 581 | 13.12 | | 13 | 20000535 | 1,275 | 6.64 |
| 4 | 20000510 | 634 | 12.96 | | 14 | 20000315 | 1,305 | 6.47 |
| 5 | 20000609 | 556 | 11.96 | | 15 | 20000225 | 1,346 | 6.29 |
| 6 | 20000579 | 743 | 11.43 | | 16 | 20000071 | 1,354 | 6.25 |
| 7 | 20000079 | 97 | 9.70 | | 17 | 20000545 | 1,243 | 5.92 |
| 8 | 20000072 | 915 | 9.34 | | 18 | 20000407 | 1,440 | 5.64 |
| 9 | 20000185 | 667 | 9.02 | | 19 | 20000205 | 1,319 | 5.57 |
| 10 | 20000449 | 961 | 8.86 | | 20 | 20000443 | 1,551 | 5.42 |

**Variant (only gaps between 0 and 8 h, i.e. idle time within a shift):** the top 5 are 20000030 (106.4 min, 3,071 gaps), 20000315 (103.1), 20000072 (101.1), 20000539 (101.0) and 20000535 (95.4). The full top 20 is in the console output, and all 441 taxis are in `q10_idle_all_taxis.csv`.

**Approach and assumptions:** for each taxi, the valid trips are ordered by start, and `LAG` gives the previous trip's end. Idle time is next start − previous end, computed on unix seconds (`start_unix + duration_s`) so that DST changes do not distort it. The columns are UNSIGNED, so they are cast to SIGNED before subtracting. 792 negative gaps (overlapping trips of the same taxi, i.e. recording errors) are excluded. 441 of the 448 taxis have at least one gap. The plain average is dominated by nights, days off and long breaks. Taxis 20000941 (4 valid trips over about 7 months) and 20000969 (25 trips) rank highest only because they barely operated, so the top of this list says more about inactive taxis than about waiting time between fares. The 8-hour-capped variant treats longer gaps as off-shift time and gives a more meaningful "waiting for the next customer" measure, 85–106 minutes for its top 20. Runtime: about 6 s.
