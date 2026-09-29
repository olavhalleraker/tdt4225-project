"""
TDT4225 Assignment 2 - Part 2, queries 6-10 (Porto taxi dataset, MySQL).

Usage (from the project root):
    .venv/bin/python queries/part2_q6_10.py          # all tasks
    .venv/bin/python queries/part2_q6_10.py 6 9      # only tasks 6 and 9

Every task has one SQL string (shown as a class constant), is timed, and
prints its result with tabulate. Large full lists are written to
queries/output/*.csv.
"""
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from DbConnector import DbConnector  # noqa: E402
from tabulate import tabulate  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# Porto City Hall
CH_LON, CH_LAT = -8.62911, 41.15794


class Part2Queries6to10:

    # ------------------------------------------------------------------
    # Q6: trips that passed within 100 m of City Hall.
    # 1) trip bounding box must overlap a box around City Hall
    #    (100 m = 0.000899 deg lat / 0.001193 deg lon at 41.16 N, the box
    #    uses 0.0010 / 0.0013 deg to be slightly generous),
    # 2) only for those trips, read their points via the gps_point PK
    #    range (trip_id, seq) and keep points inside the same box,
    # 3) exact check: minimum ST_Distance_Sphere over the points <= 100 m.
    # Only the GPS points are tested, not the line segments between them.
    # ------------------------------------------------------------------
    Q6 = """
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
        ORDER BY t.id
    """

    # ------------------------------------------------------------------
    # Q7: invalid trips = fewer than 3 GPS points, split by 0 / 1 / 2 points.
    # ------------------------------------------------------------------
    Q7 = """
        SELECT n_points, COUNT(*) AS trips
        FROM trip
        WHERE n_points < 3
        GROUP BY n_points WITH ROLLUP
    """

    # ------------------------------------------------------------------
    # Q8: midnight crossers - start and end on different local calendar
    # days (times are stored as Europe/Lisbon local time).
    # Valid trips only. days_crossed > 1 = trip spans more than one midnight.
    # ------------------------------------------------------------------
    Q8 = """
        SELECT id, trip_code, taxi_id, start_time, end_time,
               ROUND(duration_s / 60, 1) AS duration_min,
               DATEDIFF(end_time, start_time) AS days_crossed
        FROM trip
        WHERE is_valid = 1
          AND DATE(end_time) > DATE(start_time)
        ORDER BY start_time
    """
    Q8_ALL = """
        SELECT COUNT(*) FROM trip WHERE DATE(end_time) > DATE(start_time)
    """

    # ------------------------------------------------------------------
    # Q9: circular trips - start and end point within 50 m (valid trips).
    # ------------------------------------------------------------------
    Q9 = """
        SELECT id, trip_code, taxi_id, start_time, n_points, distance_km,
               ROUND(ST_Distance_Sphere(POINT(start_lon, start_lat),
                                        POINT(end_lon, end_lat)), 1) AS start_end_m
        FROM trip
        WHERE is_valid = 1
          AND ST_Distance_Sphere(POINT(start_lon, start_lat),
                                 POINT(end_lon, end_lat)) <= 50
        ORDER BY id
    """

    # ------------------------------------------------------------------
    # Q10: average idle time between consecutive trips per taxi.
    # Valid trips ordered per taxi by start (LAG over the taxi partition).
    # idle = next.start - previous.end, computed on unix seconds
    # (start_unix, start_unix + duration_s) so DST changes do not distort it.
    # The columns are UNSIGNED, so they are cast to SIGNED before subtracting.
    # Negative idle (overlapping trips) is excluded.
    #   avg_idle_all_h : plain average over all gaps >= 0 (assignment wording)
    #   avg_idle_8h_min: average over gaps between 0 and 8 h only
    #                    (longer gaps treated as off-shift / days off)
    # Ranking is by the plain average.
    # ------------------------------------------------------------------
    Q10 = """
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
               SUM(idle_s >= 0)                                   AS n_gaps,
               ROUND(AVG(CASE WHEN idle_s >= 0 THEN idle_s END) / 3600, 2)
                                                                  AS avg_idle_all_h,
               SUM(idle_s BETWEEN 0 AND 28800)                    AS n_gaps_le_8h,
               ROUND(AVG(CASE WHEN idle_s BETWEEN 0 AND 28800 THEN idle_s END) / 60, 1)
                                                                  AS avg_idle_8h_min,
               SUM(idle_s < 0)                                    AS n_overlaps
        FROM ordered
        WHERE idle_s IS NOT NULL
        GROUP BY taxi_id
        ORDER BY avg_idle_all_h DESC
    """

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor
        os.makedirs(OUT_DIR, exist_ok=True)

    # helpers ------------------------------------------------------------
    def run(self, sql):
        t0 = time.time()
        self.cursor.execute(sql)
        rows = self.cursor.fetchall()
        elapsed = time.time() - t0
        return rows, list(self.cursor.column_names), elapsed

    @staticmethod
    def header(title, sql):
        print("\n" + "=" * 78)
        print(title)
        print("=" * 78)
        print("SQL:" + sql.rstrip())
        print("-" * 78)

    @staticmethod
    def save_csv(name, cols, rows):
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        print(f"Full list ({len(rows):,} rows) written to queries/output/{name}")

    # tasks --------------------------------------------------------------
    def task6(self):
        self.header("Task 6: trips that passed within 100 m of Porto City Hall", self.Q6)
        rows, cols, dt = self.run(self.Q6)
        valid = [r for r in rows if r[5] == 1]
        print(tabulate([["valid trips (>= 3 points)", len(valid)],
                        ["invalid trips (< 3 points)", len(rows) - len(valid)],
                        ["all trips", len(rows)]],
                       headers=["trips within 100 m", "count"], intfmt=","))
        print("\nFirst 20 valid trips (by id):")
        print(tabulate([[r[0], r[1], r[2], r[3], r[6]] for r in valid[:20]],
                       headers=["id", "trip_code", "taxi_id", "start_time", "min_dist_m"]))
        self.save_csv("q6_trips.csv", cols, rows)
        print(f"Runtime: {dt:.2f} s")

    def task7(self):
        self.header("Task 7: number of invalid trips (< 3 GPS points)", self.Q7)
        rows, cols, dt = self.run(self.Q7)
        table = [["total" if r[0] is None else r[0], r[1]] for r in rows]
        print(tabulate(table, headers=["n_points", "trips"], intfmt=","))
        print(f"Runtime: {dt:.2f} s")

    def task8(self):
        self.header("Task 8: trips that start on one day and end on the next", self.Q8)
        rows, cols, dt = self.run(self.Q8)
        (n_all,), = self.run(self.Q8_ALL)[0]
        (n_valid,), = self.run("SELECT COUNT(*) FROM trip WHERE is_valid = 1")[0]
        multi = [r for r in rows if r[6] > 1]
        print(tabulate([["valid midnight crossers", len(rows)],
                        ["share of valid trips (%)", round(100 * len(rows) / n_valid, 3)],
                        ["spanning more than one midnight", len(multi)],
                        ["incl. invalid trips", n_all]],
                       headers=["", "value"], intfmt=","))
        print("\nFirst 20 (by start_time):")
        print(tabulate(rows[:20], headers=cols))
        if multi:
            print("\nTrips spanning more than one midnight:")
            print(tabulate(multi, headers=cols))
        self.save_csv("q8_midnight_trips.csv", cols, rows)
        print(f"Runtime: {dt:.2f} s")

    def task9(self):
        self.header("Task 9: circular trips (start and end within 50 m)", self.Q9)
        rows, cols, dt = self.run(self.Q9)
        (n_valid,), = self.run("SELECT COUNT(*) FROM trip WHERE is_valid = 1")[0]
        stationary = sum(1 for r in rows if r[5] < 0.1)
        print(tabulate([["circular valid trips", len(rows)],
                        ["share of valid trips (%)", round(100 * len(rows) / n_valid, 2)],
                        ["of which distance_km < 0.1 (near-stationary)", stationary],
                        ["of which distance_km >= 0.1", len(rows) - stationary],
                        ["of which distance_km >= 1", sum(1 for r in rows if r[5] >= 1)]],
                       headers=["", "value"], intfmt=","))
        print("\nFirst 20 (by id):")
        print(tabulate(rows[:20], headers=cols))
        print("\nFirst 20 with distance_km >= 1 (real round trips):")
        print(tabulate([r for r in rows if r[5] >= 1][:20], headers=cols))
        self.save_csv("q9_circular_trips.csv", cols, rows)
        print(f"Runtime: {dt:.2f} s")

    def task10(self):
        self.header("Task 10: average idle time between consecutive trips per taxi", self.Q10)
        rows, cols, dt = self.run(self.Q10)
        print("Top 20 taxis by plain average idle time (all gaps >= 0):")
        print(tabulate(rows[:20], headers=cols, showindex=range(1, 21)))
        by_capped = sorted(rows, key=lambda r: -(r[4] or 0))
        print("\nVariant: top 20 taxis by average idle time over gaps <= 8 h only:")
        print(tabulate(by_capped[:20], headers=cols, showindex=range(1, 21)))
        n_neg = sum(int(r[5]) for r in rows)
        print(f"\nTaxis: {len(rows)}, overlapping (negative) gaps excluded: {n_neg:,}")
        self.save_csv("q10_idle_all_taxis.csv", cols, rows)
        print(f"Runtime: {dt:.2f} s")


def main():
    tasks = sys.argv[1:] or ["6", "7", "8", "9", "10"]
    program = None
    try:
        program = Part2Queries6to10()
        for t in tasks:
            getattr(program, f"task{t}")()
    except Exception as e:
        print("ERROR: Failed to use database:", e)
        raise
    finally:
        if program:
            program.connection.close_connection()


if __name__ == "__main__":
    main()
