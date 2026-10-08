"""
Part 1.2 - Create the tables and insert the cleaned Porto data into MySQL.

Run:  python part1_insert.py | tee ../output/part1_insert.txt
"""
import time

from tabulate import tabulate

import cleaning
from DbConnector import DbConnector

CREATE_TABLES = [
    """
    CREATE TABLE taxi (
        taxi_id INT NOT NULL PRIMARY KEY
    )
    """,
    f"""
    CREATE TABLE trip (
        trip_id        BIGINT            NOT NULL PRIMARY KEY,
        taxi_id        INT               NOT NULL,
        call_type      CHAR(1)           NOT NULL,
        origin_call    INT               NULL,
        origin_stand   SMALLINT          NULL,
        day_type       CHAR(1)           NOT NULL,
        missing_data   BOOLEAN           NOT NULL,
        start_time     DATETIME          NOT NULL,
        end_time       DATETIME          NULL,
        duration_s     INT               NULL,
        n_points_raw   SMALLINT UNSIGNED NOT NULL,
        n_points_clean SMALLINT UNSIGNED NOT NULL,
        is_valid       BOOLEAN AS (n_points_raw >= {cleaning.MIN_VALID_POINTS}) STORED,
        distance_km    DOUBLE            NULL,
        start_lat      DECIMAL(9, 6)     NULL,
        start_lon      DECIMAL(9, 6)     NULL,
        end_lat        DECIMAL(9, 6)     NULL,
        end_lon        DECIMAL(9, 6)     NULL,
        CONSTRAINT fk_trip_taxi FOREIGN KEY (taxi_id) REFERENCES taxi (taxi_id) ON DELETE CASCADE,
        CONSTRAINT chk_call_type CHECK (call_type IN ('A', 'B', 'C')),
        CONSTRAINT chk_day_type CHECK (day_type IN ('A', 'B', 'C')),
        INDEX idx_trip_taxi_start (taxi_id, start_time)
    )
    """,
    """
    CREATE TABLE gps_point (
        trip_id     BIGINT            NOT NULL,
        point_index SMALLINT UNSIGNED NOT NULL,
        latitude    DECIMAL(9, 6)     NOT NULL,
        longitude   DECIMAL(9, 6)     NOT NULL,
        PRIMARY KEY (trip_id, point_index),
        CONSTRAINT fk_point_trip FOREIGN KEY (trip_id) REFERENCES trip (trip_id) ON DELETE CASCADE
    )
    """,
]

TRIP_COLUMNS = ["trip_id", "taxi_id", "call_type", "origin_call", "origin_stand", "day_type",
                "missing_data", "start_time", "end_time", "duration_s", "n_points_raw",
                "n_points_clean", "distance_km", "start_lat", "start_lon", "end_lat", "end_lon"]

INSERT_TRIP = "INSERT INTO trip (%s) VALUES (%s)" % (
    ", ".join(TRIP_COLUMNS), ", ".join(["%s"] * len(TRIP_COLUMNS)))
INSERT_POINT = "INSERT INTO gps_point (trip_id, point_index, latitude, longitude) VALUES (%s, %s, %s, %s)"

TRIPS_PER_BATCH = 2000


class PortoInserter:

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor

    def create_tables(self):
        # Drop children before parents because of the foreign keys
        for table in ("gps_point", "trip", "taxi"):
            self.cursor.execute("DROP TABLE IF EXISTS %s" % table)
        for statement in CREATE_TABLES:
            self.cursor.execute(statement)
        self.db_connection.commit()
        print("Created tables taxi, trip and gps_point")

    def insert_taxis(self):
        taxi_ids = sorted({int(row[4]) for row in cleaning.read_rows()})
        self.cursor.executemany("INSERT INTO taxi (taxi_id) VALUES (%s)", [(t,) for t in taxi_ids])
        self.db_connection.commit()
        print("Inserted %d taxis" % len(taxi_ids))

    def insert_trips_and_points(self):
        skip = cleaning.find_duplicate_rows()
        stats = {"CSV rows": 0, "Duplicate rows skipped": len(skip), "Trips inserted": 0,
                 "GPS points in inserted trips": 0, "GPS points inserted": 0,
                 "Points outside region (removed)": 0, "Points removed by start/speed rules": 0}
        trips, points = [], []
        started = time.time()

        for row_number, row in enumerate(cleaning.read_rows()):
            stats["CSV rows"] += 1
            if row_number in skip:
                continue
            trip, kept = cleaning.build_trip(row)
            trips.append(tuple(trip[c] for c in TRIP_COLUMNS))
            points.extend((trip["trip_id"], index, lat, lon) for index, lat, lon in kept)

            stats["Trips inserted"] += 1
            stats["GPS points in inserted trips"] += trip["n_points_raw"]
            stats["GPS points inserted"] += len(kept)
            stats["Points outside region (removed)"] += trip["n_outside_region"]
            stats["Points removed by start/speed rules"] += trip["n_speed_outliers"]

            if len(trips) >= TRIPS_PER_BATCH:
                self.insert_batch(trips, points)
                trips, points = [], []
                if stats["Trips inserted"] % 100000 == 0:
                    print("  %9d trips inserted (%.0f s)" % (stats["Trips inserted"], time.time() - started))
        self.insert_batch(trips, points)

        print("\nInsert finished in %.1f minutes" % ((time.time() - started) / 60))
        print(tabulate([[k, f"{v:,}"] for k, v in stats.items()], headers=["Step", "Count"],
                       tablefmt="simple"))

    def insert_batch(self, trips, points):
        # A trip must exist before its points can reference it (foreign key)
        if trips:
            self.cursor.executemany(INSERT_TRIP, trips)
        if points:
            self.cursor.executemany(INSERT_POINT, points)
        self.db_connection.commit()

    def show_result(self):
        print("\nRows per table:")
        rows = []
        for table in ("taxi", "trip", "gps_point"):
            self.cursor.execute("SELECT COUNT(*) FROM %s" % table)
            rows.append([table, f"{self.cursor.fetchone()[0]:,}"])
        print(tabulate(rows, headers=["Table", "Rows"], tablefmt="simple"))
        # The trip table is wide, so its columns are shown in two groups
        samples = [("trip", "trip_id, taxi_id, call_type, origin_call, origin_stand, day_type, "
                            "missing_data, start_time, end_time", "trip_id"),
                   ("trip", "trip_id, duration_s, n_points_raw, n_points_clean, is_valid, distance_km, "
                            "start_lat, start_lon, end_lat, end_lon", "trip_id"),
                   ("gps_point", "*", "trip_id, point_index")]
        for table, columns, order in samples:
            self.cursor.execute("SELECT %s FROM %s ORDER BY %s LIMIT 5" % (columns, table, order))
            print("\nSELECT %s FROM %s LIMIT 5:" % (columns, table))
            print(tabulate(self.cursor.fetchall(), headers=self.cursor.column_names, tablefmt="simple",
                           disable_numparse=True))


def main():
    program = None
    try:
        program = PortoInserter()
        program.create_tables()
        program.insert_taxis()
        program.insert_trips_and_points()
        program.show_result()
    except Exception as e:
        print("ERROR: Failed to use database:", e)
        raise
    finally:
        if program:
            program.connection.close_connection()


if __name__ == "__main__":
    main()
