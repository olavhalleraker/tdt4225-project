"""
Part 2 - Answer the ten questions with MySQL queries and Python.

For definitions used throughout, see the report.

Run:  python part2_queries.py | tee ../output/part2_results.txt
"""
import csv
import math
import os
import textwrap
import time
from collections import Counter
from decimal import Decimal

from haversine import haversine
from tabulate import tabulate

from DbConnector import DbConnector

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")

CITY_HALL = (41.15794, -8.62911)  # (latitude, longitude) of Porto City Hall
EARTH_RADIUS_KM = 6371.0088       # the mean radius used by the haversine package

# Times are stored in UTC. Questions about clock time or calendar day (tasks 4b
# and 8) convert them to Porto time, which is UTC+1 in summer time.
PORTO_TZ = "Europe/Lisbon"


def print_table(rows, headers):
    """Print rows with tabulate: counts get thousands separators, decimals two
    digits, and ID columns are printed as they are."""
    # MySQL returns SUM() of integers as Decimal; show whole numbers as int
    rows = [[int(v) if isinstance(v, Decimal) and v == v.to_integral_value() else v for v in row]
            for row in rows]
    id_columns = [i for i, h in enumerate(headers) if h.endswith("_id")]
    print(tabulate(rows, headers=headers, tablefmt="simple", floatfmt=".2f", intfmt=",",
                   disable_numparse=id_columns or False))


def save_csv(filename, headers, rows):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print("(all {:,} rows written to output/{})".format(len(rows), filename))


class PortoQueries:

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor
        # CONVERT_TZ returns NULL when MySQL has no time zone tables
        if self.query("SELECT CONVERT_TZ('2013-07-01', '+00:00', %s)", (PORTO_TZ,))[0][0] is None:
            raise RuntimeError("MySQL has no time zone tables (load them with mysql_tzinfo_to_sql)")

    def query(self, sql, params=None):
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    # ------------------------------------------------------------------ #
    def task1(self):
        """How many taxis, trips, and total GPS points are there?"""
        rows = self.query("""
            SELECT (SELECT COUNT(*) FROM taxi)               AS taxis,
                   (SELECT COUNT(*) FROM trip)               AS trips,
                   (SELECT COUNT(*) FROM trip WHERE is_valid) AS valid_trips,
                   (SELECT COUNT(*) FROM gps_point)          AS gps_points_stored,
                   (SELECT SUM(n_points_raw) FROM trip)      AS gps_points_before_cleaning
        """)
        print_table(rows, self.cursor.column_names)

    def task2(self):
        """What is the average number of trips per taxi?"""
        rows = self.query("""
            SELECT AVG(n_trips)       AS avg_trips_per_taxi,
                   AVG(n_valid_trips) AS avg_valid_trips_per_taxi,
                   MIN(n_trips)       AS min_trips,
                   MAX(n_trips)       AS max_trips
            FROM (SELECT taxi.taxi_id,
                         COUNT(trip.trip_id)            AS n_trips,
                         COALESCE(SUM(trip.is_valid), 0) AS n_valid_trips
                  FROM taxi LEFT JOIN trip ON trip.taxi_id = taxi.taxi_id
                  GROUP BY taxi.taxi_id) AS per_taxi
        """)
        print_table(rows, self.cursor.column_names)

    def task3(self):
        """List the top 20 taxis with the most trips."""
        rows = self.query("""
            SELECT taxi_id, COUNT(*) AS trips, SUM(is_valid) AS valid_trips
            FROM trip
            GROUP BY taxi_id
            ORDER BY trips DESC
            LIMIT 20
        """)
        print_table([[rank] + list(r) for rank, r in enumerate(rows, start=1)],
                    ["rank"] + list(self.cursor.column_names))

    def task4a(self):
        """What is the most used call type per taxi?"""
        rows = self.query("""
            WITH per_type AS (
                SELECT taxi_id, call_type, COUNT(*) AS trips,
                       SUM(COUNT(*)) OVER (PARTITION BY taxi_id)                AS taxi_trips,
                       RANK() OVER (PARTITION BY taxi_id ORDER BY COUNT(*) DESC) AS rnk
                FROM trip
                GROUP BY taxi_id, call_type
            )
            SELECT taxi_id, call_type AS most_used_call_type, trips,
                   100 * trips / taxi_trips AS share_of_taxi_trips_pct
            FROM per_type
            WHERE rnk = 1
            ORDER BY taxi_id, call_type
        """)
        headers = list(self.cursor.column_names)
        taxis_with_tie = [t for t, n in Counter(r[0] for r in rows).items() if n > 1]
        summary = Counter(r[1] for r in rows)
        print("Number of taxis per most used call type:")
        print_table([[ct, summary[ct]] for ct in sorted(summary)], ["most_used_call_type", "taxis"])
        print("Taxis with a tie between two call types: %d %s" % (len(taxis_with_tie), taxis_with_tie))
        print("\nFirst 20 taxis:")
        print_table(rows[:20], headers)
        save_csv("task4a_most_used_call_type.csv", headers, rows)

    def task4b(self):
        """For each call type: average duration and distance, and the share of
        trips starting in the time bands 00-06, 06-12, 12-18 and 18-24 (Porto time)."""
        rows = self.query("""
            SELECT call_type,
                   COUNT(*)                                       AS trips,
                   AVG(duration_s) / 60                           AS avg_duration_min,
                   AVG(distance_km)                               AS avg_distance_km,
                   100 * AVG(HOUR(start_porto) BETWEEN 0 AND 5)   AS pct_00_06,
                   100 * AVG(HOUR(start_porto) BETWEEN 6 AND 11)  AS pct_06_12,
                   100 * AVG(HOUR(start_porto) BETWEEN 12 AND 17) AS pct_12_18,
                   100 * AVG(HOUR(start_porto) BETWEEN 18 AND 23) AS pct_18_24
            FROM (SELECT call_type, duration_s, distance_km,
                         CONVERT_TZ(start_time, '+00:00', %s) AS start_porto
                  FROM trip
                  WHERE is_valid) AS valid_trip
            GROUP BY call_type
            ORDER BY call_type
        """, (PORTO_TZ,))
        print("Time bands use the start time in Porto time.")
        print_table(rows, self.cursor.column_names)

    def task5(self):
        """Taxis with the most total hours driven and total distance driven,
        listed in order of total hours."""
        rows = self.query("""
            SELECT taxi_id,
                   COUNT(*)                                         AS trips,
                   SUM(duration_s) / 3600                           AS total_hours,
                   SUM(distance_km)                                 AS total_distance_km,
                   RANK() OVER (ORDER BY SUM(distance_km) DESC)     AS distance_rank
            FROM trip
            WHERE is_valid
            GROUP BY taxi_id
            ORDER BY total_hours DESC
            LIMIT 20
        """)
        print("Top 20 taxis by total hours (distance_rank = position when sorted by distance):")
        print_table([[rank] + list(r) for rank, r in enumerate(rows, start=1)],
                    ["hours_rank"] + list(self.cursor.column_names))

    def task6(self):
        """Trips that passed within 100 m of Porto City Hall.

        MySQL first selects the GPS points inside a small bounding box around
        the City Hall (cheap comparisons), then Python computes the exact
        haversine distance for those candidates."""
        radius_km = 0.1
        d_lat = math.degrees(radius_km / EARTH_RADIUS_KM)
        d_lon = d_lat / math.cos(math.radians(CITY_HALL[0]))
        candidates = self.query("""
            SELECT p.trip_id, t.taxi_id, t.start_time, p.latitude, p.longitude
            FROM gps_point AS p JOIN trip AS t ON t.trip_id = p.trip_id
            WHERE t.is_valid
              AND p.latitude  BETWEEN %s AND %s
              AND p.longitude BETWEEN %s AND %s
        """, (CITY_HALL[0] - d_lat, CITY_HALL[0] + d_lat, CITY_HALL[1] - d_lon, CITY_HALL[1] + d_lon))

        closest = {}  # trip_id -> (distance in m, taxi_id, start_time)
        points_within = 0
        for trip_id, taxi_id, start_time, lat, lon in candidates:
            meters = haversine(CITY_HALL, (float(lat), float(lon))) * 1000
            if meters <= radius_km * 1000:
                points_within += 1
                if trip_id not in closest or meters < closest[trip_id][0]:
                    closest[trip_id] = (meters, taxi_id, start_time)

        rows = sorted([[trip_id, taxi_id, start_time, round(meters, 1)]
                       for trip_id, (meters, taxi_id, start_time) in closest.items()],
                      key=lambda r: r[2])
        headers = ["trip_id", "taxi_id", "start_time", "closest_distance_m"]
        print("GPS points in the bounding box: {:,}, of them within 100 m: {:,}".format(len(candidates), points_within))
        print("Trips that passed within 100 m of Porto City Hall: {:,}\n".format(len(rows)))
        print("First 20 (by start time):")
        print_table(rows[:20], headers)
        save_csv("task6_trips_near_city_hall.csv", headers, rows)

    def task7(self):
        """Number of invalid trips (fewer than 3 GPS points)."""
        rows = self.query("""
            SELECT n_points_raw AS gps_points, COUNT(*) AS trips
            FROM trip
            WHERE NOT is_valid
            GROUP BY n_points_raw
            ORDER BY n_points_raw
        """)
        headers = list(self.cursor.column_names)
        total_invalid = sum(r[1] for r in rows)
        (all_trips,), = self.query("SELECT COUNT(*) FROM trip")
        print_table(rows + [["total", total_invalid]], headers)
        print("\nInvalid trips: {:,} of {:,} ({:.2f} %)".format(total_invalid, all_trips, 100 * total_invalid / all_trips))
        (after_cleaning,), = self.query("SELECT COUNT(*) FROM trip WHERE is_valid AND n_points_clean < 3")
        print("In addition, {:,} valid trips have fewer than 3 points left after removing GPS errors."
              .format(after_cleaning))

    def task8(self):
        """Trips that started on one calendar day and ended on the next, using
        the Porto calendar day."""
        rows = self.query("""
            WITH porto AS (
                SELECT trip_id, taxi_id, call_type, duration_s,
                       CONVERT_TZ(start_time, '+00:00', %s) AS start_time_porto,
                       CONVERT_TZ(end_time, '+00:00', %s)   AS end_time_porto
                FROM trip
                WHERE is_valid
            )
            SELECT trip_id, taxi_id, call_type, start_time_porto, end_time_porto,
                   duration_s / 60 AS duration_min
            FROM porto
            WHERE DATE(end_time_porto) = DATE(start_time_porto) + INTERVAL 1 DAY
            ORDER BY start_time_porto
        """, (PORTO_TZ, PORTO_TZ))
        headers = list(self.cursor.column_names)
        print("Midnight-crossing trips (Porto time): {:,}\n".format(len(rows)))
        print("First 20:")
        print_table(rows[:20], headers)
        save_csv("task8_midnight_crossers.csv", headers, rows)

    def task9(self):
        """Trips whose start and end points are within 50 m of each other.
        Start/end are the first/last cleaned GPS points; the trip needs at least
        3 cleaned points so that start and end are different points."""
        trips = self.query("""
            SELECT trip_id, taxi_id, start_time, duration_s / 60 AS duration_min, distance_km,
                   start_lat, start_lon, end_lat, end_lon
            FROM trip
            WHERE is_valid AND n_points_clean >= 3
        """)
        rows = []
        for trip_id, taxi_id, start_time, duration_min, distance_km, s_lat, s_lon, e_lat, e_lon in trips:
            meters = haversine((float(s_lat), float(s_lon)), (float(e_lat), float(e_lon))) * 1000
            if meters <= 50:
                rows.append([trip_id, taxi_id, start_time, float(duration_min), distance_km, round(meters, 1)])
        headers = ["trip_id", "taxi_id", "start_time", "duration_min", "distance_km", "start_end_distance_m"]

        print("Circular trips (start and end within 50 m): {:,} of {:,} trips checked ({:.2f} %)\n"
              .format(len(rows), len(trips), 100 * len(rows) / len(trips)))
        # How far did the taxi drive during the circular trips?
        groups = [("drove < 100 m (taxi did not move)", 0, 0.1),
                  ("drove 100 m - 1 km", 0.1, 1),
                  ("drove >= 1 km (real round trip)", 1, float("inf"))]
        group_rows = []
        for label, low, high in groups:
            durations = [r[3] for r in rows if low <= r[4] < high]
            group_rows.append([label, len(durations), sum(durations) / len(durations) if durations else 0])
        print_table(group_rows, ["circular trips that ...", "trips", "avg_duration_min"])
        print("\nFirst 20 (by start time):")
        rows.sort(key=lambda r: r[2])
        print_table(rows[:20], headers)
        save_csv("task9_circular_trips.csv", headers, rows)

    def task10(self):
        """Average idle time between consecutive trips per taxi; top 20.

        Idle time = start of a trip - end of the taxi's previous trip.
        Overlapping trips (the next trip starts before the previous one ends,
        a few hundred pairs, see the EDA) count as 0 idle time."""
        rows = self.query("""
            WITH ordered AS (
                SELECT taxi_id, start_time,
                       LAG(end_time) OVER (PARTITION BY taxi_id ORDER BY start_time) AS previous_end
                FROM trip
                WHERE is_valid
            ),
            gaps AS (
                SELECT taxi_id, GREATEST(TIMESTAMPDIFF(SECOND, previous_end, start_time), 0) AS idle_s
                FROM ordered
                WHERE previous_end IS NOT NULL
            )
            SELECT taxi_id,
                   COUNT(*)                     AS idle_periods,
                   AVG(idle_s) / 3600           AS avg_idle_hours,
                   MAX(idle_s) / 86400          AS longest_idle_days,
                   AVG(CASE WHEN idle_s <= 6 * 3600 THEN idle_s END) / 60 AS avg_idle_min_gaps_under_6h
            FROM gaps
            GROUP BY taxi_id
            ORDER BY avg_idle_hours DESC
            LIMIT 20
        """)
        print_table([[rank] + list(r) for rank, r in enumerate(rows, start=1)],
                    ["rank"] + list(self.cursor.column_names))


def main():
    program = None
    try:
        program = PortoQueries()
        tasks = [
            ("1", "How many taxis, trips, and total GPS points are there?", program.task1),
            ("2", "What is the average number of trips per taxi?", program.task2),
            ("3", "List the top 20 taxis with the most trips.", program.task3),
            ("4a", "What is the most used call type per taxi?", program.task4a),
            ("4b", "For each call type, compute the average trip duration and distance, and also "
                   "report the share of trips starting in four time bands: 00-06, 06-12, 12-18, "
                   "and 18-24.", program.task4b),
            ("5", "Find the taxis with the most total hours driven as well as total distance "
                  "driven. List them in order of total hours.", program.task5),
            ("6", "Find the trips that passed within 100 m of Porto City Hall "
                  "(longitude, latitude) = (-8.62911, 41.15794).", program.task6),
            ("7", "Identify the number of invalid trips (fewer than 3 GPS points).", program.task7),
            ("8", "Find the trips that started on one calendar day and ended on the next "
                  "(midnight crossers).", program.task8),
            ("9", "Find the trips whose start and end points are within 50 m of each other "
                  "(circular trips).", program.task9),
            ("10", "For each taxi, compute the average idle time between consecutive trips. "
                   "List the top 20 taxis with the highest average idle time.", program.task10),
        ]
        for name, question, task in tasks:
            print("\n" + "=" * 78)
            print(textwrap.fill("Task %s: %s" % (name, question), width=78))
            print("=" * 78)
            started = time.time()
            task()
            print("[task %s took %.1f s]" % (name, time.time() - started))
    except Exception as e:
        print("ERROR: Failed to use database:", e)
        raise
    finally:
        if program:
            program.connection.close_connection()


if __name__ == "__main__":
    main()
