"""
Cleans data/porto.csv and loads it into the MySQL schema from sql/schema.sql
(taxi -> trip -> gps_point). Cleaning rules are documented in docs/cleaning.md,
the column mapping in docs/schema.md section 2.

Run create_tables.py first (the loader expects empty tables):
    .venv/bin/python create_tables.py
    .venv/bin/python insert_data.py [--limit N]

--limit N only reads the first N rows of the CSV (for testing).

How it works
  Pass 1 (parallel): read TRIP_ID and POLYLINE of every row, count the points
      and check whether any point lies outside mainland Portugal. From this we
      decide which rows to keep (duplicate TRIP_IDs, GPS garbage) and assign
      trip.id = 1..N to the kept rows in CSV order.
  Pass 2 (parallel): worker processes turn each batch of CSV rows into two
      temporary CSV files (trip rows and gps_point rows, in PK order). The
      main process bulk-loads them with LOAD DATA LOCAL INFILE, commits and
      deletes the files right away, so at most a few batches are on disk.
"""
import argparse
import csv
import os
import shutil
import sys
import tempfile
import time
from collections import deque
from datetime import datetime, timedelta
from multiprocessing import Pool
from zoneinfo import ZoneInfo

import mysql.connector as mysql
import numpy as np
import pandas as pd
from haversine import Unit, haversine_vector
from tabulate import tabulate

from DbConnector import DbConnector

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data", "porto.csv")
# Temporary chunk files (deleted right after loading). Override with TMP_DIR=...
TMP_DIR = os.getenv("TMP_DIR", os.path.join(tempfile.gettempdir(), "porto_load"))

BATCH_SIZE = 20_000           # CSV rows per work unit
N_WORKERS = max(1, (os.cpu_count() or 2) - 2)
MAX_PENDING = N_WORKERS + 2   # batches in flight -> bounds memory and temp-file disk usage

LOCAL_TZ = ZoneInfo("Europe/Lisbon")
INTERVAL_S = 15               # one GPS point every 15 s
GAP_KMH = 200                 # a segment faster than this = GPS jump / signal gap
GAP_KM = GAP_KMH * INTERVAL_S / 3600.0     # 0.8333 km in 15 s
# Mainland Portugal: a trip with any point outside this box is dropped
PT_LON = (-9.6, -6.1)
PT_LAT = (36.9, 42.2)

csv.field_size_limit(sys.maxsize)


# ---------------------------------------------------------------------------
# Helpers (run inside worker processes)
# ---------------------------------------------------------------------------
def split_polyline(poly):
    """'[[lon,lat],[lon,lat]]' -> ['lon','lat','lon','lat'] (strings, unchanged).

    Much faster than json.loads and keeps the original decimal text, which is
    written to the database as-is (at most 6 decimals, see check 14 in eda/eda.py).
    """
    if len(poly) <= 2:                      # "[]"
        return []
    return poly[2:-2].replace("],[", ",").split(",")


def read_batches(path, limit=None):
    """Yield lists of CSV rows (lists of strings), BATCH_SIZE rows each."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader)                        # header
        batch, n = [], 0
        for row in reader:
            if limit is not None and n >= limit:
                break
            batch.append(row)
            n += 1
            if len(batch) == BATCH_SIZE:
                yield batch
                batch = []
        if batch:
            yield batch


def scan_batch(rows):
    """Pass 1: per row -> (trip_code, taxi_id, n_points, any point outside Portugal)."""
    codes = np.array([int(r[0]) for r in rows], dtype=np.uint64)
    taxis = np.array([int(r[4]) for r in rows], dtype=np.int64)
    n = np.zeros(len(rows), dtype=np.int64)
    outside = np.zeros(len(rows), dtype=bool)
    for i, r in enumerate(rows):
        c = split_polyline(r[8])
        if c:
            xy = np.array(c, dtype=np.float64)
            lon, lat = xy[0::2], xy[1::2]
            n[i] = lon.size
            outside[i] = ((lon < PT_LON[0]) | (lon > PT_LON[1]) |
                          (lat < PT_LAT[0]) | (lat > PT_LAT[1])).any()
    return codes, taxis, n, outside


def fmt(x):
    return "%.6f" % x


def build_batch(args):
    """Pass 2: write the trip and gps_point rows of one batch to two temp files.

    `keep` says which rows survive cleaning, `first_id` is the trip.id of the
    first kept row. Returns the file names and some counters.
    """
    batch_no, rows, keep, first_id, tmp_dir = args
    trip_path = os.path.join(tmp_dir, "trip_%05d.tsv" % batch_no)
    gps_path = os.path.join(tmp_dir, "gps_%05d.csv" % batch_no)
    n_trips = n_points = n_gap = 0
    trip_id = first_id

    with open(trip_path, "w") as ft, open(gps_path, "w") as fg:
        for row, k in zip(rows, keep):
            if not k:
                continue
            (trip_code, call_type, origin_call, origin_stand, taxi_id,
             ts, day_type, missing, poly) = row
            c = split_polyline(poly)
            n = len(c) // 2
            if n >= 65536:                  # seq and n_points are SMALLINT UNSIGNED
                raise ValueError("too many points in trip %s" % trip_code)

            start_unix = int(ts)
            start_time = datetime.fromtimestamp(start_unix, LOCAL_TZ).replace(tzinfo=None)
            duration_s = INTERVAL_S * (n - 1) if n >= 1 else 0
            end_time = start_time + timedelta(seconds=duration_s)

            dist_km, has_gap = 0.0, 0
            coord_cols = ["\\N"] * 8
            if n >= 1:
                xy = np.array(c, dtype=np.float64)
                lon, lat = xy[0::2], xy[1::2]
                if n >= 2:
                    # haversine takes (lat, lon); POLYLINE is [lon, lat]
                    p = np.column_stack((lat, lon))
                    seg = haversine_vector(p[:-1], p[1:], Unit.KILOMETERS)
                    dist_km = float(seg.sum())
                    has_gap = int((seg > GAP_KM).any())
                # start_lat, start_lon, end_lat, end_lon (original text), then bbox
                coord_cols = [c[1], c[0], c[-1], c[-2],
                              fmt(lat.min()), fmt(lat.max()), fmt(lon.min()), fmt(lon.max())]

                # gps_point rows in PK order: trip_id, seq, lon, lat
                fg.write("\n".join("%d,%d,%s,%s" % (trip_id, s, c[2 * s], c[2 * s + 1])
                                   for s in range(n)))
                fg.write("\n")

            ft.write("\t".join([
                str(trip_id), trip_code, taxi_id, call_type,
                origin_call if origin_call else "\\N",       # empty -> NULL
                origin_stand if origin_stand else "\\N",     # empty -> NULL
                day_type, "1" if missing == "True" else "0",
                str(start_unix), start_time.strftime("%Y-%m-%d %H:%M:%S"),
                end_time.strftime("%Y-%m-%d %H:%M:%S"), str(duration_s),
                str(n), "%.3f" % dist_km, str(has_gap), *coord_cols]) + "\n")

            n_trips += 1
            n_points += n
            n_gap += has_gap
            trip_id += 1

    return batch_no, trip_path, gps_path, n_trips, n_points, n_gap


# ---------------------------------------------------------------------------
# Loader (main process)
# ---------------------------------------------------------------------------
TRIP_COLUMNS = ("id, trip_code, taxi_id, call_type, origin_call, origin_stand, day_type, "
                "missing_data, start_unix, start_time, end_time, duration_s, n_points, "
                "distance_km, has_gap, start_lat, start_lon, end_lat, end_lon, "
                "min_lat, max_lat, min_lon, max_lon")


class InsertData:

    def __init__(self):
        self.connection = DbConnector()
        self.db_connection = self.connection.db_connection
        self.cursor = self.connection.cursor
        # Separate connection for LOAD DATA LOCAL INFILE (DbConnector does not enable it)
        self.load_conn = mysql.connect(host=os.getenv("DB_HOST", "localhost"),
                                       port=int(os.getenv("DB_PORT", "3306")),
                                       database=os.getenv("DB_NAME"),
                                       user=os.getenv("DB_USER"),
                                       password=os.getenv("DB_PASSWORD"),
                                       allow_local_infile=True)
        self.load_cur = self.load_conn.cursor()
        # Safe because the loader guarantees consistency by construction
        # (parents first, unique ids); verified afterwards in verify().
        self.load_cur.execute("SET SESSION foreign_key_checks = 0")
        self.load_cur.execute("SET SESSION unique_checks = 0")

    def check_empty(self):
        for table in ("taxi", "trip", "gps_point"):
            self.cursor.execute("SELECT COUNT(*) FROM %s" % table)
            if self.cursor.fetchone()[0]:
                raise RuntimeError("table %s is not empty - run create_tables.py first" % table)

    # ---- pass 1 -----------------------------------------------------------
    def scan(self, pool, limit):
        t0 = time.time()
        parts = list(pool.imap(scan_batch, read_batches(CSV_PATH, limit)))
        codes = np.concatenate([p[0] for p in parts])
        taxis = np.concatenate([p[1] for p in parts])
        n = np.concatenate([p[2] for p in parts])
        outside = np.concatenate([p[3] for p in parts])
        print("Pass 1: scanned %d rows in %.1f s" % (len(codes), time.time() - t0), flush=True)
        return codes, taxis, n, outside

    @staticmethod
    def decide_keep(codes, n, outside):
        """Apply the row-level cleaning rules. Returns the keep mask and stats."""
        df = pd.DataFrame({"code": codes, "n": n})
        # Rule 2: one row per TRIP_ID - the copy with most points, ties -> first
        # occurrence (idxmax returns the first index of the maximum).
        best = df.groupby("code", sort=False)["n"].idxmax().to_numpy()
        keep_dup = np.zeros(len(df), dtype=bool)
        keep_dup[best] = True
        dup_codes = df["code"].duplicated(keep=False).to_numpy()

        # Rule 3: drop trips with any point outside mainland Portugal
        keep = keep_dup & ~outside

        stats = {
            "rows read": len(df),
            "duplicate TRIP_IDs": int(pd.Series(codes[dup_codes]).nunique()),
            "rows with a duplicate TRIP_ID": int(dup_codes.sum()),
            "dropped: duplicate copy": int((~keep_dup).sum()),
            "dropped: point outside Portugal": int((keep_dup & outside).sum()),
            "(outside Portugal, any copy)": int(outside.sum()),
            "rows kept (trips)": int(keep.sum()),
        }
        return keep, stats

    def insert_taxis(self, taxi_ids):
        rows = [(int(t),) for t in sorted(set(taxi_ids.tolist()))]
        self.cursor.executemany("INSERT INTO taxi (taxi_id) VALUES (%s)", rows)
        self.db_connection.commit()
        print("Inserted %d taxis" % len(rows), flush=True)

    # ---- pass 2 -----------------------------------------------------------
    def load_files(self, trip_path, gps_path):
        for path, table, opts in (
                (trip_path, "trip", "FIELDS TERMINATED BY '\\t' LINES TERMINATED BY '\\n' (%s)" % TRIP_COLUMNS),
                (gps_path, "gps_point", "FIELDS TERMINATED BY ',' LINES TERMINATED BY '\\n' (trip_id, seq, lon, lat)")):
            if os.path.getsize(path) > 0:
                self.load_cur.execute("LOAD DATA LOCAL INFILE '%s' INTO TABLE %s %s" % (path, table, opts))
                self.load_cur.execute("SHOW WARNINGS LIMIT 5")
                warnings = self.load_cur.fetchall()
                if warnings:
                    raise RuntimeError("warnings while loading %s: %s" % (table, warnings))
            os.remove(path)
        self.load_conn.commit()

    def load(self, pool, limit, keep):
        os.makedirs(TMP_DIR, exist_ok=True)
        total_rows = len(keep)
        pending = deque()
        next_id = 1
        rows_done = trips = points = gaps = 0
        t0 = time.time()

        def finish_oldest():
            nonlocal rows_done, trips, points, gaps
            batch_no, trip_path, gps_path, nt, npts, ngap = pending.popleft().get()
            self.load_files(trip_path, gps_path)
            rows_done += min(BATCH_SIZE, total_rows - batch_no * BATCH_SIZE)
            trips, points, gaps = trips + nt, points + npts, gaps + ngap
            if batch_no % 5 == 0 or rows_done == total_rows:
                el = time.time() - t0
                rate = rows_done / el
                print("  %8d/%d rows  %9d points  %6.0f rows/s  %7.0f points/s  "
                      "elapsed %5.0f s  ETA %5.0f s  disk free %.1f GB"
                      % (rows_done, total_rows, points, rate, points / el, el,
                         (total_rows - rows_done) / rate, shutil.disk_usage("/").free / 1e9),
                      flush=True)

        for batch_no, rows in enumerate(read_batches(CSV_PATH, limit)):
            k = keep[batch_no * BATCH_SIZE: batch_no * BATCH_SIZE + len(rows)]
            pending.append(pool.apply_async(build_batch, ((batch_no, rows, k, next_id, TMP_DIR),)))
            next_id += int(k.sum())
            if len(pending) >= MAX_PENDING:
                finish_oldest()
        while pending:
            finish_oldest()
        print("Pass 2: loaded %d trips, %d points (%d with has_gap) in %.1f s"
              % (trips, points, gaps, time.time() - t0), flush=True)
        return trips, points, gaps

    # ---- verification -----------------------------------------------------
    def verify(self):
        checks = [
            ("taxis", "SELECT COUNT(*) FROM taxi"),
            ("trips", "SELECT COUNT(*) FROM trip"),
            ("gps points", "SELECT COUNT(*) FROM gps_point"),
            ("SUM(trip.n_points)", "SELECT SUM(n_points) FROM trip"),
            ("min/max trip.id", "SELECT CONCAT(MIN(id), ' / ', MAX(id)) FROM trip"),
            ("distinct trip_code", "SELECT COUNT(DISTINCT trip_code) FROM trip"),
            ("trips is_valid = 0", "SELECT COUNT(*) FROM trip WHERE is_valid = 0"),
            ("trips n_points = 0", "SELECT COUNT(*) FROM trip WHERE n_points = 0"),
            ("trips has_gap = 1", "SELECT COUNT(*) FROM trip WHERE has_gap = 1"),
            ("orphan trips (no taxi)",
             "SELECT COUNT(*) FROM trip t LEFT JOIN taxi x ON x.taxi_id = t.taxi_id WHERE x.taxi_id IS NULL"),
            ("trips whose last seq != n_points-1",
             "SELECT COUNT(*) FROM trip t JOIN (SELECT trip_id, MAX(seq) AS m, COUNT(*) AS c "
             "FROM gps_point GROUP BY trip_id) g ON g.trip_id = t.id "
             "WHERE g.m <> t.n_points - 1 OR g.c <> t.n_points"),
            ("orphan points (max trip_id > max id)",
             "SELECT (SELECT MAX(trip_id) FROM gps_point) > (SELECT MAX(id) FROM trip)"),
        ]
        rows = []
        for label, sql in checks:
            self.cursor.execute(sql)
            rows.append((label, self.cursor.fetchone()[0]))
        print(tabulate(rows, headers=["check", "value"]))


def main():
    parser = argparse.ArgumentParser(description="Clean and load data/porto.csv into MySQL")
    parser.add_argument("--limit", type=int, default=None, help="only read the first N CSV rows")
    args = parser.parse_args()

    program = None
    t_start = time.time()
    try:
        program = InsertData()
        program.check_empty()
        with Pool(N_WORKERS) as pool:
            codes, taxis, n, outside = program.scan(pool, args.limit)
            keep, stats = program.decide_keep(codes, n, outside)
            stats["points in kept trips"] = int(n[keep].sum())
            stats["kept trips with < 3 points"] = int((n[keep] < 3).sum())
            print(tabulate(stats.items(), headers=["cleaning", "rows"]), flush=True)

            program.insert_taxis(taxis[keep])
            trips, points, gaps = program.load(pool, args.limit, keep)
            if trips != stats["rows kept (trips)"] or points != stats["points in kept trips"]:
                raise RuntimeError("pass 2 loaded %d trips / %d points, pass 1 expected %d / %d"
                                   % (trips, points, stats["rows kept (trips)"],
                                      stats["points in kept trips"]))
        program.verify()
        print("Total runtime: %.1f s" % (time.time() - t_start))
    except Exception as e:
        print("ERROR: Failed to insert data:", e)
        raise
    finally:
        if program:
            program.load_conn.close()
            program.connection.close_connection()


if __name__ == '__main__':
    main()
