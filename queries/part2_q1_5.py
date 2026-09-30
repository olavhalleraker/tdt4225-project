"""
TDT4225 Assignment 2 - Part 2, queries 1-5 (Porto Taxi dataset, porto_db).

Usage (from the project root):
    .venv/bin/python queries/part2_q1_5.py          # run all tasks 1-5
    .venv/bin/python queries/part2_q1_5.py 4        # run only task 4
    .venv/bin/python queries/part2_q1_5.py 1 3 5    # run tasks 1, 3 and 5

All answers are computed in MySQL. Python only formats the results.
Conventions (see docs/report.md, section 2.4):
  * Q1-Q3 count every trip in the cleaned database (incl. is_valid = 0),
    with the valid-only numbers (n_points >= 3) as a secondary line.
  * Q4b and Q5 use valid trips only (is_valid = 1). They are also shown
    without trips that have a GPS jump (has_gap = 1), since those trips
    have an inflated distance_km.
  * start_time is local Porto time (Europe/Lisbon).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from DbConnector import DbConnector  # noqa: E402
from tabulate import tabulate  # noqa: E402

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


# ---------------------------------------------------------------------------
# SQL - one query string per task part
# ---------------------------------------------------------------------------

# Q1: number of taxis, trips and GPS points.
# The GPS point total is counted directly on gps_point (full clustered PK
# scan, ~83M rows). SUM(trip.n_points) is the cheap equivalent and is shown
# as a cross-check (the loader guarantees they are equal).
Q1_COUNTS = """
SELECT
    (SELECT COUNT(*) FROM taxi)       AS taxis,
    (SELECT COUNT(*) FROM trip)       AS trips,
    (SELECT COUNT(*) FROM gps_point)  AS gps_points
"""

# Q1 secondary: the same numbers restricted to valid trips (>= 3 points),
# plus SUM(n_points) over all trips as a cross-check of the COUNT(*) above.
Q1_VALID = """
SELECT
    COUNT(DISTINCT taxi_id)                       AS taxis_with_valid_trip,
    CAST(SUM(is_valid = 1) AS UNSIGNED)           AS valid_trips,
    CAST(SUM(CASE WHEN is_valid = 1 THEN n_points END) AS UNSIGNED) AS points_in_valid_trips,
    CAST(SUM(n_points) AS UNSIGNED)               AS sum_n_points_all_trips
FROM trip
"""

# Q2: average number of trips per taxi = trips / taxis.
# Counted per taxi in a derived table. The taxi table only holds taxis
# with at least one kept trip, so every taxi has n_trips >= 1.
Q2_AVG = """
SELECT
    COUNT(*)                   AS taxis,
    CAST(SUM(n_trips) AS UNSIGNED) AS trips,
    ROUND(AVG(n_trips), 2)     AS avg_trips_per_taxi,
    ROUND(STDDEV_POP(n_trips), 2) AS stddev,
    MIN(n_trips)               AS min_trips,
    MAX(n_trips)               AS max_trips,
    ROUND(AVG(n_valid), 2)     AS avg_valid_trips_per_taxi
FROM (
    SELECT tx.taxi_id,
           COUNT(t.id)                  AS n_trips,
           COALESCE(SUM(t.is_valid), 0) AS n_valid
    FROM taxi tx
    LEFT JOIN trip t ON t.taxi_id = tx.taxi_id
    GROUP BY tx.taxi_id
) per_taxi
"""

# Q3: top 20 taxis by number of trips (all trips). The valid-trip count is
# shown next to it. Ties on the count are ordered by taxi_id.
Q3_TOP20 = """
SELECT
    taxi_id,
    COUNT(*)       AS n_trips,
    CAST(SUM(is_valid) AS UNSIGNED) AS n_valid_trips
FROM trip
GROUP BY taxi_id
ORDER BY n_trips DESC, taxi_id
LIMIT 20
"""

# Q4a: most used call type per taxi (all trips).
# Count trips per (taxi, call_type), then rank the call types inside each
# taxi with ROW_NUMBER(). Ties: the call type with the most trips wins, and
# a tie on the count is broken alphabetically (A < B < C). n_tied reports
# how many call types share the top count, so ties are visible.
Q4A_MOST_USED = """
WITH per_type AS (
    SELECT taxi_id, call_type, COUNT(*) AS n
    FROM trip
    GROUP BY taxi_id, call_type
),
ranked AS (
    SELECT
        taxi_id, call_type, n,
        SUM(n)   OVER (PARTITION BY taxi_id)                          AS taxi_total,
        MAX(n)   OVER (PARTITION BY taxi_id)                          AS max_n,
        ROW_NUMBER() OVER (PARTITION BY taxi_id ORDER BY n DESC, call_type) AS rn
    FROM per_type
)
SELECT
    r.taxi_id,
    r.call_type                         AS most_used_call_type,
    r.n                                 AS n_trips_of_type,
    r.taxi_total                        AS n_trips_total,
    ROUND(100 * r.n / r.taxi_total, 1)  AS pct_of_taxi_trips,
    (SELECT COUNT(*) FROM per_type p
      WHERE p.taxi_id = r.taxi_id AND p.n = r.max_n) AS n_tied
FROM ranked r
WHERE r.rn = 1
ORDER BY r.taxi_id
"""

# Q4b: per call type, average duration (min) and distance (km), and the
# share of trips that start in each 6-hour band of the local start_time.
# WITH ROLLUP adds an "all call types" row. Valid trips only.
# {gap_filter} is '' (main result) or 'AND has_gap = 0' (variant).
Q4B_CALLTYPE = """
SELECT
    COALESCE(call_type, 'ALL')                                   AS call_type,
    COUNT(*)                                                     AS n_trips,
    ROUND(AVG(duration_s) / 60, 2)                               AS avg_duration_min,
    ROUND(AVG(distance_km), 3)                                   AS avg_distance_km,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 0  AND 5 ), 2)      AS pct_00_06,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 6  AND 11), 2)      AS pct_06_12,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 12 AND 17), 2)      AS pct_12_18,
    ROUND(100 * AVG(HOUR(start_time) BETWEEN 18 AND 23), 2)      AS pct_18_24
FROM trip
WHERE is_valid = 1 {gap_filter}
GROUP BY call_type WITH ROLLUP
"""

# Q5: taxis with most total hours driven, with their total distance.
# Hours = SUM(duration_s)/3600 over valid trips. The distance rank is
# computed over all taxis in the same query, so the top-20-by-hours list
# shows where each taxi stands by distance as well.
Q5_TOP_HOURS = """
WITH per_taxi AS (
    SELECT taxi_id,
           COUNT(*)                  AS n_trips,
           SUM(duration_s) / 3600    AS total_hours,
           SUM(distance_km)          AS total_km
    FROM trip
    WHERE is_valid = 1 {gap_filter}
    GROUP BY taxi_id
)
SELECT
    RANK() OVER (ORDER BY total_hours DESC)  AS hours_rank,
    taxi_id,
    n_trips,
    ROUND(total_hours, 1)                    AS total_hours,
    ROUND(total_km, 1)                       AS total_km,
    RANK() OVER (ORDER BY total_km DESC)     AS km_rank
FROM per_taxi
ORDER BY total_hours DESC, taxi_id
LIMIT 20
"""

# Q5 companion: the taxis with most total distance (top 5), with their
# hours rank, to answer "which taxi drove the furthest".
Q5_TOP_KM = """
WITH per_taxi AS (
    SELECT taxi_id,
           COUNT(*)                  AS n_trips,
           SUM(duration_s) / 3600    AS total_hours,
           SUM(distance_km)          AS total_km
    FROM trip
    WHERE is_valid = 1 {gap_filter}
    GROUP BY taxi_id
)
SELECT
    RANK() OVER (ORDER BY total_km DESC)     AS km_rank,
    taxi_id,
    n_trips,
    ROUND(total_km, 1)                       AS total_km,
    ROUND(total_hours, 1)                    AS total_hours,
    RANK() OVER (ORDER BY total_hours DESC)  AS hours_rank
FROM per_taxi
ORDER BY total_km DESC, taxi_id
LIMIT 5
"""

GAP_VARIANTS = [
    ("MAIN: valid trips (is_valid = 1)", ""),
    ("VARIANT: valid trips without GPS jumps (is_valid = 1 AND has_gap = 0)", "AND has_gap = 0"),
]


class Part2Queries:

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor

    def run(self, sql, label=""):
        """Execute a query, return (rows, column_names), print the runtime."""
        t0 = time.perf_counter()
        self.cursor.execute(sql)
        rows = self.cursor.fetchall()
        dt = time.perf_counter() - t0
        print("[%s: %.2f s]" % (label or "query", dt))
        return rows, self.cursor.column_names

    @staticmethod
    def header(title):
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)

    # -----------------------------------------------------------------
    def task1(self):
        self.header("Q1. How many taxis, trips, and total GPS points are there?")
        rows, cols = self.run(Q1_COUNTS, "Q1 counts")
        print(tabulate(rows, headers=cols))
        rows, cols = self.run(Q1_VALID, "Q1 valid-only + cross-check")
        print("\nSecondary: valid trips only (n_points >= 3); last column = SUM(n_points) cross-check")
        print(tabulate(rows, headers=cols))

    def task2(self):
        self.header("Q2. What is the average number of trips per taxi?")
        rows, cols = self.run(Q2_AVG, "Q2")
        print(tabulate(rows, headers=cols))

    def task3(self):
        self.header("Q3. Top 20 taxis with the most trips (all trips; valid trips shown too)")
        rows, cols = self.run(Q3_TOP20, "Q3")
        rows = [(i + 1,) + tuple(r) for i, r in enumerate(rows)]
        print(tabulate(rows, headers=("rank",) + tuple(cols)))

    def task4(self):
        # ---- 4a ----
        self.header("Q4a. Most used call type per taxi (all trips; ties -> alphabetical)")
        rows, cols = self.run(Q4A_MOST_USED, "Q4a")
        print("First 20 of %d taxis (full table: queries/output/q4a_full.txt):" % len(rows))
        print(tabulate(rows[:20], headers=cols))

        summary = {}
        for r in rows:
            summary.setdefault(r[1], 0)
            summary[r[1]] += 1
        n_ties = sum(1 for r in rows if r[5] > 1)
        print("\nSummary: number of taxis per most used call type")
        print(tabulate([(k, v, round(100 * v / len(rows), 1)) for k, v in sorted(summary.items())],
                       headers=("most_used_call_type", "n_taxis", "pct_of_taxis")))
        print("Taxis with a tie for the top call type: %d" % n_ties)
        for r in rows:
            if r[5] > 1:
                print("  tie: taxi %s -> %s chosen (%s trips, %d types tied)" % (r[0], r[1], r[2], r[5]))

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(os.path.join(OUTPUT_DIR, "q4a_full.txt"), "w") as f:
            f.write("Q4a. Most used call type per taxi (all trips). Ties broken alphabetically "
                    "(A < B < C); n_tied = number of call types sharing the top count.\n\n")
            f.write(tabulate(rows, headers=cols))
            f.write("\n")

        # ---- 4b ----
        for title, gap_filter in GAP_VARIANTS:
            self.header("Q4b. Per call type: avg duration/distance and start time-band shares (local time)\n"
                        + title)
            rows, cols = self.run(Q4B_CALLTYPE.format(gap_filter=gap_filter), "Q4b")
            print(tabulate(rows, headers=cols))

    def task5(self):
        for title, gap_filter in GAP_VARIANTS:
            self.header("Q5. Top 20 taxis by total hours driven, with total distance\n" + title)
            rows, cols = self.run(Q5_TOP_HOURS.format(gap_filter=gap_filter), "Q5 hours")
            print(tabulate(rows, headers=cols, floatfmt=",.1f"))
            print("\nTop 5 taxis by total distance:")
            rows, cols = self.run(Q5_TOP_KM.format(gap_filter=gap_filter), "Q5 distance")
            print(tabulate(rows, headers=cols, floatfmt=",.1f"))


def main():
    tasks = [int(a) for a in sys.argv[1:]] or [1, 2, 3, 4, 5]
    program = None
    try:
        program = Part2Queries()
        for t in tasks:
            t0 = time.perf_counter()
            getattr(program, "task%d" % t)()
            print("\n[task %d total: %.2f s]" % (t, time.perf_counter() - t0))
    except Exception as e:
        print("ERROR: Failed to run queries:", e)
        raise
    finally:
        if program:
            program.connection.close_connection()


if __name__ == "__main__":
    main()
