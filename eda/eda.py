"""
Step-by-step EDA of the Porto taxi dataset.

Every finding in the report has its own numbered check below, written as
plainly as possible so you can see exactly how the number was found. The
plots for the report are made here too (--figures).

Usage (from the project root):
    .venv/bin/python eda/eda.py                 # run all checks
    .venv/bin/python eda/eda.py 3 13            # run only check 3 and 13
    .venv/bin/python eda/eda.py --figures       # only save the plots to eda/figures/
    .venv/bin/python eda/eda.py --sample 50000  # quick run on the first 50k rows

The first run parses every POLYLINE once (a few minutes) and caches one row of
statistics per trip in data/trip_stats_*.csv. Later runs read the cache.
Delete that file to recompute.
"""
# THE BIG PICTURE (read this first)
#
# What is the data?
#   porto.csv has one row per taxi trip, 1.7 million trips from one year.
#   Most columns are plain facts about the trip: who drove it (TAXI_ID), when
#   it started (TIMESTAMP), how the customer got the taxi (CALL_TYPE), etc.
#
# What is POLYLINE?
#   The last column, POLYLINE, is the route the taxi drove. The taxi's GPS
#   wrote down where it was every 15 seconds, and POLYLINE is that list of
#   positions, stored as text:
#
#       "[[-8.618643,41.141412],[-8.618499,41.141376],[-8.620326,41.14251]]"
#          ^ point 1 (t=0 s)     ^ point 2 (t=15 s)    ^ point 3 (t=30 s)
#
#   Each point is [longitude, latitude] (note: longitude first, the opposite
#   of what Google Maps shows). So a trip with 41 points lasted
#   (41 - 1) * 15 s = 10 minutes. If you "connect the dots" you get the
#   route drawn on a map; that is why it is called a polyline.
#
# Why is this script split in two ("meta" and "stats")?
#   The POLYLINE column is huge (most of the 1.9 GB file), and turning the
#   text into numbers is slow. So we read the data in two parts:
#     meta  = every column EXCEPT POLYLINE, as plain text. Fast to read.
#     stats = for every trip, a few numbers we computed from its POLYLINE
#             (how many points, how far it drove, top speed, ...). Slow to
#             compute the first time, so it is saved to
#             data/trip_stats_full.csv and just re-read on later runs.
#   Both tables have one row per trip IN THE SAME ORDER as porto.csv, so row
#   5 in meta and row 5 in stats are the same trip. That is how we combine
#   them (e.g. meta.join(stats)) without needing a key.
#
# What does each check do?
#   Each check_N function asks one question about the data ("are the IDs
#   unique?", "are there GPS points in Spain?"), prints the numbers that
#   answer it, and ends with a "->" line saying what we decided to do about
#   it when cleaning. Checks 1-8 look at the normal columns, checks 9-15 at
#   the GPS routes.
# ---------------------------------------------------------------------------

import argparse
import csv
import json
import os
import re
import sys
import time
import zlib

import matplotlib
matplotlib.use("Agg")   # no pop-up windows, we only save PNG files
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tabulate import tabulate

CSV_PATH = "data/porto.csv"
FIG_DIR = "eda/figures"
META_COLUMNS = ["TRIP_ID", "CALL_TYPE", "ORIGIN_CALL", "ORIGIN_STAND",
                "TAXI_ID", "TIMESTAMP", "DAY_TYPE", "MISSING_DATA"]

INTERVAL_S = 15                 # one GPS point every 15 seconds
SPEED_LIMIT_KMH = 200           # faster than this between two points = GPS jump
CITY_HALL = (-8.62911, 41.15794)  # (lon, lat), from the assignment
# Rough rectangles on the map, (lon_min, lon_max, lat_min, lat_max).
# A point outside PORTO_BOX is just "far from the city" (could be a real
# long trip). A point outside PORTUGAL_BOX is in Spain or the sea: GPS error.
PORTO_BOX = (-8.80, -8.40, 41.00, 41.35)
PORTUGAL_BOX = (-9.60, -6.10, 36.90, 42.20)

# Some POLYLINEs are longer than Python's csv module allows by default
csv.field_size_limit(sys.maxsize)
# Finds the digits after every "." in the text, e.g. "41.141412" -> "141412"
DECIMALS_RE = re.compile(r"\.(\d+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def header(number, title):
    print("\n" + "=" * 78)
    print("CHECK %d: %s" % (number, title))
    print("=" * 78)


def pct(part, whole):
    return "%.2f %%" % (100 * part / whole) if whole else "-"


def counts_table(series, name):
    """Value counts with share, e.g. for CALL_TYPE."""
    counts = series.value_counts(dropna=False).sort_index()
    rows = [[value, count, pct(count, len(series))] for value, count in counts.items()]
    return tabulate(rows, headers=[name, "rows", "share"], intfmt=",")


def haversine_m(lon1, lat1, lon2, lat2):
    """Distance in metres between points (works on numpy arrays).
    Same formula as the `haversine` package, just vectorised."""
    # Why not just Pythagoras? Longitude/latitude are angles on a globe, not
    # metres on a flat map, and one degree of longitude is shorter than one
    # degree of latitude up here. Haversine is the standard "distance along
    # the Earth's surface" formula. Works on whole arrays at once, so we can
    # measure every segment of a route in one call.
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * np.arcsin(np.sqrt(a))   # 6371008.8 m = Earth's radius


def outside_box(lon, lat, box):
    """True for every point that lies outside the rectangle."""
    lon_min, lon_max, lat_min, lat_max = box
    return (lon < lon_min) | (lon > lon_max) | (lat < lat_min) | (lat > lat_max)


def fetch_rows(row_numbers):
    """Return the raw CSV rows (as dicts) for the given 0-based row numbers.
    Used to show concrete examples. Reads the file once, so it takes ~10 s."""
    # `stats` only has summary numbers, not the actual points. When a check
    # wants to print a real example route, it looks the row up here.
    wanted = set(row_numbers)
    found = {}
    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i in wanted:
                found[i] = row
                if len(found) == len(wanted):
                    break
    return found


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_metadata(nrows):
    """All columns except POLYLINE, read as strings so we can see what is
    really in the file (e.g. empty strings instead of NULL)."""
    # If we let pandas guess types, it would turn "" into NaN and big IDs
    # into floats, and we would no longer see what the raw file looks like.
    return pd.read_csv(CSV_PATH, usecols=META_COLUMNS, dtype=str,
                       keep_default_na=False, nrows=nrows)


def polyline_stats(polyline):
    """Everything we want to know about one trajectory, as one dict.

    POLYLINE looks like "[[-8.618643,41.141412],[-8.618499,41.141376],...]",
    i.e. a JSON list of [longitude, latitude] pairs, one every 15 s.
    """
    # The text happens to be valid JSON, so json.loads turns it into a
    # Python list of [lon, lat] pairs.
    points = json.loads(polyline)
    n = len(points)
    decimals = [len(d) for d in DECIMALS_RE.findall(polyline)]
    # Default values, used as-is when the trip has no points at all ("[]")
    stats = {
        "n_points": n,
        # crc32 turns the whole POLYLINE text into one number. Two trips with
        # the same number have (practically) the same route. Much cheaper
        # than keeping and comparing the full text (used in check 3).
        "poly_crc": zlib.crc32(polyline.encode()),
        "max_decimals": max(decimals) if decimals else 0,
        "dist_km": 0.0, "max_speed_kmh": 0.0, "n_jumps": 0,
        "n_outside_porto": 0, "n_outside_portugal": 0,
        "start_end_m": np.nan, "min_city_hall_m": np.nan,
    }
    if n == 0:
        return stats

    # Turn the list into a numpy table: column 0 = longitude, column 1 = latitude
    arr = np.array(points, dtype=float)
    lon, lat = arr[:, 0], arr[:, 1]
    stats["n_outside_porto"] = int(outside_box(lon, lat, PORTO_BOX).sum())
    stats["n_outside_portugal"] = int(outside_box(lon, lat, PORTUGAL_BOX).sum())
    # Distance from the first point to the last point (query 9: circular trips)
    stats["start_end_m"] = float(haversine_m(lon[0], lat[0], lon[-1], lat[-1]))
    # How close did the trip ever get to City Hall? (query 6)
    stats["min_city_hall_m"] = float(haversine_m(lon, lat, *CITY_HALL).min())

    if n >= 2:
        # Measure each "step" of the route: point 0 -> 1, 1 -> 2, 2 -> 3, ...
        # lon[:-1] is "every point except the last", lon[1:] is "every point
        # except the first", so pairing them up gives each point and the next.
        segments_m = haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:])
        # Each step takes 15 s, so metres per step -> km/h
        speeds_kmh = segments_m / INTERVAL_S * 3.6
        stats["dist_km"] = float(segments_m.sum() / 1000)   # total route length
        stats["max_speed_kmh"] = float(speeds_kmh.max())
        # A step faster than 200 km/h can't be real driving: the GPS jumped
        stats["n_jumps"] = int((speeds_kmh > SPEED_LIMIT_KMH).sum())
    return stats


def load_trip_stats(nrows):
    """One row of polyline statistics per trip (same order as the CSV)."""
    cache = "data/trip_stats_%s.csv" % ("full" if nrows is None else "sample%d" % nrows)
    if os.path.exists(cache):
        print("Reading cached polyline statistics from %s" % cache)
        return pd.read_csv(cache)

    print("Parsing every POLYLINE (first run only, takes a few minutes)...")
    t0 = time.time()
    rows = []
    # Read only the POLYLINE column, 100k trips at a time so we don't need
    # the whole 1.9 GB in memory at once
    chunks = pd.read_csv(CSV_PATH, usecols=["POLYLINE"], dtype=str,
                         keep_default_na=False, chunksize=100_000, nrows=nrows)
    for chunk in chunks:
        rows.extend(polyline_stats(p) for p in chunk["POLYLINE"])
        print("  %9d trips  (%.0f s)" % (len(rows), time.time() - t0), flush=True)
    stats = pd.DataFrame(rows)
    stats.to_csv(cache, index=False)
    print("Saved %s" % cache)
    return stats


# ---------------------------------------------------------------------------
# Part A: the normal columns (everything except POLYLINE)
# ---------------------------------------------------------------------------

def check_1_overview(meta, stats):
    """How big is the file, and where are the empty values?"""
    # First look: for every column, how many different values and how many
    # blanks. Tells us which columns can be NULL in the database.
    header(1, "Rows, columns and empty values")
    print("Rows (trips): %d" % len(meta))
    rows = []
    for col in META_COLUMNS:
        empty = int((meta[col] == "").sum())
        rows.append([col, meta[col].nunique(), empty, pct(empty, len(meta))])
    print(tabulate(rows, headers=["column", "distinct values", "empty", "empty %"],
                   intfmt=","))
    print("\n-> Only ORIGIN_CALL and ORIGIN_STAND have empty values. They are empty on"
          "\n   purpose (they only apply to call type A and B), so they should become NULL.")


def check_2_trip_id_format(meta, stats):
    """Is TRIP_ID built from the other columns?"""
    # Looking at a few rows, TRIP_ID looked like TIMESTAMP and TAXI_ID glued
    # together. Here we test that guess on every row by gluing them ourselves
    # (as text) and comparing.
    header(2, "TRIP_ID format")
    plain = meta["TIMESTAMP"] + meta["TAXI_ID"]
    with_six = meta["TIMESTAMP"] + "6" + meta["TAXI_ID"]
    print("Example row: TRIP_ID=%s  TIMESTAMP=%s  TAXI_ID=%s"
          % (meta.at[0, "TRIP_ID"], meta.at[0, "TIMESTAMP"], meta.at[0, "TAXI_ID"]))
    print("TRIP_ID == TIMESTAMP + TAXI_ID       : %d rows" % (plain == meta["TRIP_ID"]).sum())
    print("TRIP_ID == TIMESTAMP + '6' + TAXI_ID : %d of %d rows"
          % ((with_six == meta["TRIP_ID"]).sum(), len(meta)))
    # Does the biggest ID still fit in a MySQL BIGINT column?
    biggest = meta["TRIP_ID"].astype("uint64").max()
    print("Largest TRIP_ID: %d  (BIGINT max is %d)" % (biggest, 2**63 - 1))
    print("\n-> TRIP_ID is just start time + '6' + taxi. Two trips by the same taxi"
          "\n   starting in the same second would get the same ID (see check 3).")


def check_3_duplicate_trip_ids(meta, stats):
    """Can TRIP_ID be the primary key? Only if it is unique."""
    header(3, "Duplicate TRIP_IDs")
    # keep=False marks EVERY copy as a duplicate (not just the 2nd, 3rd, ...)
    dup_mask = meta["TRIP_ID"].duplicated(keep=False)
    # Stick the point count and route fingerprint next to each duplicate row
    dups = meta[dup_mask].join(stats[["n_points", "poly_crc"]])
    n_ids = dups["TRIP_ID"].nunique()
    print("TRIP_IDs that occur more than once: %d" % n_ids)
    if n_ids == 0:
        return
    print("Rows involved: %d  (= %d extra rows)" % (len(dups), len(dups) - n_ids))
    print("Copies per ID: %s" % dups.groupby("TRIP_ID").size().value_counts().to_dict())

    # Are the copies the same trip written twice, or different trips that
    # happen to share an ID? Compare the copies of each ID: first the normal
    # columns, then the route (via the fingerprint).
    groups = dups.groupby("TRIP_ID")
    other_columns = [c for c in META_COLUMNS if c != "TRIP_ID"]
    same_meta = groups[other_columns].apply(lambda g: len(g.drop_duplicates()) == 1)
    same_poly = groups["poly_crc"].nunique() == 1
    print("IDs where all metadata columns are identical: %d of %d" % (same_meta.sum(), n_ids))
    print("IDs where the POLYLINE is identical too    : %d of %d" % (same_poly.sum(), n_ids))
    print("IDs where one copy has an empty POLYLINE   : %d"
          % (groups["n_points"].min() == 0).sum())

    # Show one example where the copies are clearly different trips
    example_id = same_poly[~same_poly].index[0]
    print("\nExample, TRIP_ID %s:" % example_id)
    print(tabulate(dups[dups["TRIP_ID"] == example_id][["TRIP_ID", "CALL_TYPE", "TAXI_ID",
                                                         "TIMESTAMP", "n_points"]],
                   headers="keys", showindex=True))
    print("\n-> TRIP_ID is NOT unique, and the copies are mostly different trips (not"
          "\n   exact copies). Decision: keep the copy with the most GPS points (drops"
          "\n   %d rows) and use a surrogate integer key in the database." % (len(dups) - n_ids))


def check_4_call_type(meta, stats):
    """Distribution of CALL_TYPE, and do ORIGIN_CALL/ORIGIN_STAND follow the rules?"""
    # CALL_TYPE = how the trip was ordered:
    #   A = phoned the taxi central (then ORIGIN_CALL = the customer's number)
    #   B = picked up at a taxi stand (then ORIGIN_STAND = which stand)
    #   C = anything else, e.g. hailed on the street (both should be empty)
    # We count the rows that break those rules.
    header(4, "CALL_TYPE and ORIGIN_CALL / ORIGIN_STAND consistency")
    print(counts_table(meta["CALL_TYPE"], "CALL_TYPE"))
    has_call = meta["ORIGIN_CALL"] != ""
    has_stand = meta["ORIGIN_STAND"] != ""
    is_a, is_b = meta["CALL_TYPE"] == "A", meta["CALL_TYPE"] == "B"
    # & = and, ~ = not (pandas' versions of them, working on whole columns)
    rules = [
        ["A (central) without ORIGIN_CALL", (is_a & ~has_call).sum()],
        ["ORIGIN_CALL set, but not type A", (~is_a & has_call).sum()],
        ["B (stand) without ORIGIN_STAND", (is_b & ~has_stand).sum()],
        ["ORIGIN_STAND set, but not type B", (~is_b & has_stand).sum()],
    ]
    print("\nRule violations (the assignment says ORIGIN_CALL only for A, ORIGIN_STAND only for B):")
    print(tabulate(rules, headers=["rule", "rows"], intfmt=","))
    print("\n-> Rules hold except %d B-trips with unknown stand. Kept, with ORIGIN_STAND = NULL."
          % (is_b & ~has_stand).sum())


def check_5_day_type(meta, stats):
    """DAY_TYPE should be A (normal), B (holiday) or C (day before holiday)."""
    # Easy test: Christmas Day is a holiday, so trips that day should be "B".
    header(5, "DAY_TYPE")
    print(counts_table(meta["DAY_TYPE"], "DAY_TYPE"))
    christmas = pd.to_datetime(meta["TIMESTAMP"].astype(int), unit="s").dt.strftime("%m-%d") == "12-25"
    print("DAY_TYPE values on Christmas Day: %s" % meta.loc[christmas, "DAY_TYPE"].unique().tolist())
    print("\n-> DAY_TYPE is 'A' for every trip, even on Christmas. The column carries no"
          "\n   information and cannot be trusted.")


def check_6_missing_data(meta, stats):
    """MISSING_DATA = True should mean the trajectory has gaps."""
    # The dataset has its own "this route has holes" flag. We test it
    # against the gaps we find ourselves (the GPS jumps from check 13).
    header(6, "MISSING_DATA")
    print(counts_table(meta["MISSING_DATA"], "MISSING_DATA"))
    flagged = meta["MISSING_DATA"] == "True"
    print("\nThe flagged trips:")
    print(tabulate(meta[flagged][["TRIP_ID", "CALL_TYPE"]].join(
        stats[["n_points", "dist_km", "n_outside_portugal"]]), headers="keys", floatfmt=".2f"))
    has_jump = stats["n_jumps"] > 0
    print("\nTrips flagged False, but with a >%d km/h jump (a real gap): %d"
          % (SPEED_LIMIT_KMH, (~flagged & has_jump).sum()))
    print("\n-> Only a handful of trips are flagged, while tens of thousands have real gaps"
          "\n   (check 13). The flag is kept as a column but not used for cleaning.")


def check_7_taxis(meta, stats):
    """How many taxis, and how many trips does each have?"""
    header(7, "Taxis and trips per taxi")
    per_taxi = meta["TAXI_ID"].value_counts()   # taxi -> number of trips
    print("Distinct taxis: %d" % len(per_taxi))
    desc = per_taxi.describe()   # min, max, mean, median, ... in one go
    print(tabulate([[k, "%.1f" % v] for k, v in desc.items()], headers=["trips per taxi", ""]))
    print("Taxis with fewer than 10 trips: %d" % (per_taxi < 10).sum())
    print("\n-> A few taxis have almost no trips. They are real data and are kept, but they"
          "\n   pull 'per taxi' averages down a little.")


def check_8_time(meta, stats):
    """Time range, and does the timezone choice matter?"""
    # TIMESTAMP is seconds since 1970 (Unix time), which is always UTC.
    # Porto is on UTC in winter but UTC+1 in summer, so "what hour did the
    # trip start" depends on which one we use. We check how much it matters.
    header(8, "TIMESTAMP: time range and timezone")
    utc = pd.to_datetime(meta["TIMESTAMP"].astype(int), unit="s", utc=True)
    local = utc.dt.tz_convert("Europe/Lisbon")   # Porto local time (UTC+0 / UTC+1 in summer)
    print("First trip: %s  (UTC %s)" % (local.min(), utc.min()))
    print("Last trip : %s  (UTC %s)" % (local.max(), utc.max()))

    # Put every trip into a 6-hour band, once by local time and once by UTC
    labels = ["00-06", "06-12", "12-18", "18-24"]
    band_local = pd.cut(local.dt.hour, [0, 6, 12, 18, 24], right=False, labels=labels)
    band_utc = pd.cut(utc.dt.hour, [0, 6, 12, 18, 24], right=False, labels=labels)
    rows = [[b, pct((band_local == b).sum(), len(meta)), pct((band_utc == b).sum(), len(meta))]
            for b in labels]
    print("\nShare of trips per start-time band (needed for query 4b):")
    print(tabulate(rows, headers=["band", "Europe/Lisbon", "UTC"]))
    print("Trips that land in a different band depending on timezone: %d"
          % (band_local.astype(str) != band_utc.astype(str)).sum())
    print("\n-> Exactly one year of data. The timezone changes the answer to query 4b,"
          "\n   so we store local Porto time (Europe/Lisbon) and say so in the report.")


# ---------------------------------------------------------------------------
# Part B: POLYLINE (the GPS routes)
# From here on we mostly use `stats`, the numbers computed per route in
# polyline_stats() above.
# ---------------------------------------------------------------------------

def check_9_points_per_trip(meta, stats):
    """How many GPS points are there, and how many trips are 'invalid' (< 3 points)?"""
    # The assignment calls a trip with fewer than 3 points "invalid": you
    # can't really call it a route. Some trips have no points at all ("[]").
    header(9, "GPS points per trip and invalid trips")
    n = stats["n_points"]
    print("Total GPS points: %d" % n.sum())
    rows = [["0 points (POLYLINE = '[]')", (n == 0).sum(), pct((n == 0).sum(), len(n))],
            ["1 point", (n == 1).sum(), pct((n == 1).sum(), len(n))],
            ["2 points", (n == 2).sum(), pct((n == 2).sum(), len(n))],
            ["< 3 points = invalid", (n < 3).sum(), pct((n < 3).sum(), len(n))]]
    print(tabulate(rows, headers=["trips with", "trips", "share"], intfmt=","))
    print("Points per trip: median %d, mean %.1f, max %d" % (n.median(), n.mean(), n.max()))
    print("\n-> Invalid trips are kept in the trip table (query 7 must count them), and"
          "\n   marked with is_valid = 0 so the other queries can skip them.")


def check_10_duration(meta, stats):
    """Trip duration = (points - 1) * 15 s. Are there unrealistic durations?"""
    # There is no "end time" column. But points come every 15 s, so
    # 3 points = 2 gaps = 30 s. That gives us the duration for free.
    header(10, "Trip duration")
    valid = stats["n_points"] >= 3
    minutes = (stats.loc[valid, "n_points"] - 1) * INTERVAL_S / 60
    print("Duration (valid trips): median %.1f min, 99th percentile %.1f min, max %.0f min"
          % (minutes.median(), minutes.quantile(0.99), minutes.max()))
    rows = [["> %d h" % h, (minutes > h * 60).sum()] for h in (1, 2, 3, 4, 8, 12)]
    print(tabulate(rows, headers=["duration", "trips"], intfmt=","))
    print("\n-> Most trips are 5-20 minutes. A few hundred run for hours (meter left on?)."
          "\n   They are few, so we keep them and mention them in the report.")


def check_11_distance(meta, stats):
    """Trip distance = sum of haversine distances between consecutive points."""
    # dist_km was computed in polyline_stats(): measure every step of the
    # route and add them up. Here we look for distances that make no sense.
    header(11, "Trip distance")
    valid = stats["n_points"] >= 3
    km = stats.loc[valid, "dist_km"]
    print("Distance (valid trips): median %.2f km, mean %.2f km, max %.0f km"
          % (km.median(), km.mean(), km.max()))
    rows = [["0 m (never moved)", (km == 0).sum()],
            ["< 100 m", (km < 0.1).sum()],
            ["> 100 km", (km > 100).sum()],
            ["> 1000 km", (km > 1000).sum()]]
    print(tabulate(rows, headers=["distance", "trips"], intfmt=","))
    print("\n-> A 1000 km taxi trip inside ~15 minutes is impossible: those distances"
          "\n   come from GPS jumps (check 13), not real driving.")


def check_12_outside_area(meta, stats):
    """Are there points far away from Porto?"""
    header(12, "GPS points outside Porto / outside Portugal")
    # "points" = how many GPS points in total, "trips" = how many trips
    # have at least one such point
    rows = [
        ["Porto box %s" % (PORTO_BOX,), int(stats["n_outside_porto"].sum()),
         int((stats["n_outside_porto"] > 0).sum())],
        ["Mainland Portugal box %s" % (PORTUGAL_BOX,), int(stats["n_outside_portugal"].sum()),
         int((stats["n_outside_portugal"] > 0).sum())],
    ]
    print(tabulate(rows, headers=["outside", "points", "trips"], intfmt=","))

    bad = stats.index[stats["n_outside_portugal"] > 0].tolist()
    if bad:
        found = fetch_rows(bad)
        # Collect every point outside Portugal, to see how far off they are
        far = np.array([p for row in found.values() for p in json.loads(row["POLYLINE"])
                        if outside_box(p[0], p[1], PORTUGAL_BOX)])
        print("\nThe points outside Portugal reach lon %.1f .. %.1f and lat %.1f .. %.1f"
              % (far[:, 0].min(), far[:, 0].max(), far[:, 1].min(), far[:, 1].max()))

        # Print the bad point(s) of the first such trip next to their
        # neighbours, so you can see it is one crazy point in the middle of
        # an otherwise normal route
        row = found[bad[0]]
        points = json.loads(row["POLYLINE"])
        lon, lat = np.array(points).T
        idx = np.flatnonzero(outside_box(lon, lat, PORTUGAL_BOX))   # positions of the bad points
        print("\nExample: trip %s has %d points, of which point(s) %s are outside Portugal:"
              % (row["TRIP_ID"], len(points), idx.tolist()))
        for i in idx[:3]:
            print("  point %d: %s   (neighbours: %s ... %s)"
                  % (i, points[i], points[max(i - 1, 0)], points[min(i + 1, len(points) - 1)]))
    print("\n-> Outside the Porto box are mostly real long trips on motorways, so we do NOT"
          "\n   cut to Porto. Outside Portugal is certainly wrong: those trips are dropped.")


def check_13_speed_jumps(meta, stats):
    """Two consecutive points are 15 s apart. If the distance between them means
    driving faster than 200 km/h, the GPS jumped (or points are missing)."""
    # Example: two points 2 km apart, 15 s between them = 480 km/h. No taxi
    # does that, so either the GPS glitched or some points are missing.
    header(13, "Unrealistic jumps between consecutive points (> %d km/h)" % SPEED_LIMIT_KMH)
    print("200 km/h for 15 s = %.0f m between two points" % (SPEED_LIMIT_KMH / 3.6 * INTERVAL_S))
    jumps = stats["n_jumps"]
    rows = [["jump segments in total", jumps.sum()],
            ["trips with at least one jump", (jumps > 0).sum()],
            ["trips with a jump > 1000 km/h", (stats["max_speed_kmh"] > 1000).sum()]]
    print(tabulate(rows, headers=["", "count"], intfmt=","))

    # Is a jump one bad point (the GPS blips away and straight back), or a
    # real gap where points are missing? We call it a "spike" when the step
    # INTO a point and the step OUT of it are both jumps, but the point before
    # and the point after are close to each other. That needs the actual
    # points, so we re-read the trips that have jumps.
    limit_m = SPEED_LIMIT_KMH / 3.6 * INTERVAL_S
    spikes = 0
    for row in fetch_rows(stats.index[jumps > 0].tolist()).values():
        lon, lat = np.array(json.loads(row["POLYLINE"])).T
        if len(lon) < 3:
            continue
        step = haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:])   # point i -> i+1
        skip = haversine_m(lon[:-2], lat[:-2], lon[2:], lat[2:])   # point i -> i+2, skipping one
        spikes += int(((step[:-1] > limit_m) & (step[1:] > limit_m) & (skip <= limit_m)).sum())
    print("Single-point spikes (each one causes 2 jump segments): %d" % spikes)

    # Show the segments of one trip with a jump. We pick a short trip with
    # exactly one jump so the printout is small and the jump stands out.
    example = stats.index[(jumps == 1) & (stats["n_points"].between(10, 40))][:1].tolist()
    if example:
        row = fetch_rows(example)[example[0]]
        lon, lat = np.array(json.loads(row["POLYLINE"])).T
        seg = haversine_m(lon[:-1], lat[:-1], lon[1:], lat[1:])   # same step trick as polyline_stats()
        print("\nExample trip %s, metres between consecutive points:" % row["TRIP_ID"])
        print("  " + " ".join("%d" % s for s in seg))
        print("  the jump is segment %d: %.0f m in 15 s = %.0f km/h"
              % (seg.argmax(), seg.max(), seg.max() / INTERVAL_S * 3.6))
    print("\n-> MISSING_DATA misses these gaps. We keep the trips but flag them with"
          "\n   has_gap = 1, so distance/hours queries can exclude them.")


def check_14_precision(meta, stats):
    """How many decimals do the coordinates have? Decides the column type."""
    # If we know the max number of decimals, we can pick a database type
    # that stores them exactly instead of rounding.
    header(14, "Coordinate precision")
    print(counts_table(stats.loc[stats["n_points"] > 0, "max_decimals"], "max decimals in trip"))
    print("\n-> Never more than 6 decimals, so DECIMAL(9,6) stores every value exactly"
          "\n   (6 decimals = about 0.1 m). FLOAT would round to about 1 m.")


def check_15_reference_counts(meta, stats):
    """Rough answers to query 6 and 9, to sanity-check the SQL later."""
    # Not a data problem, just a cheat sheet: if our SQL for query 6 or 9
    # later gives a wildly different number than this, the SQL is wrong.
    header(15, "Reference counts for query 6 and 9")
    valid = stats["n_points"] >= 3
    print("Valid trips with start and end within 50 m (query 9): %d"
          % (stats.loc[valid, "start_end_m"] <= 50).sum())
    print("Valid trips with a point within 100 m of City Hall (query 6): %d"
          % (stats.loc[valid, "min_city_hall_m"] <= 100).sum())
    print("\n-> Computed from the raw CSV (before cleaning), so the SQL answers should be"
          "\n   very close to these.")


# ---------------------------------------------------------------------------
# Figures (run with --figures). These are the plots used in the report.
# ---------------------------------------------------------------------------

def save_figure(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(FIG_DIR, name), dpi=120)
    plt.close(fig)
    print("  saved %s/%s" % (FIG_DIR, name))


def bar_chart(labels, values, title, figsize=(7, 4)):
    fig, ax = plt.subplots(figsize=figsize)
    ax.bar(labels, values, color="#4C72B0")
    ax.set_title(title)
    ax.set_ylabel("trips")
    return fig, ax


def histogram(values, clip, title, xlabel, name, log_y=False):
    """Histogram where everything above `clip` is piled into the last bar.
    Otherwise a handful of crazy outliers (a 16 h trip, a 1229 km trip)
    stretch the x-axis so far that all the normal trips end up in one bar."""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(np.clip(values, 0, clip), bins=100, color="#4C72B0", edgecolor="white", linewidth=0.5)
    if log_y:
        ax.set_yscale("log")   # so the rare values are still visible next to the common ones
    ax.set_title(title, loc="left")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("trips (log scale)" if log_y else "trips")
    ax.text(0.99, 0.95, "%d trips > %s (in the last bar)" % ((values > clip).sum(), clip),
            transform=ax.transAxes, ha="right", va="top", fontsize=9, color="#555555")
    save_figure(fig, name)


def sample_points(nrows, every=100):
    """All GPS points of every 100th trip, for the map. Plotting all 83M
    points would take forever and look the same."""
    lons, lats = [], []
    chunks = pd.read_csv(CSV_PATH, usecols=["POLYLINE"], dtype=str,
                         keep_default_na=False, chunksize=100_000, nrows=nrows)
    for chunk in chunks:
        for polyline in chunk["POLYLINE"].iloc[::every]:
            for lon, lat in json.loads(polyline):
                lons.append(lon)
                lats.append(lat)
    return np.array(lons), np.array(lats)


def make_figures(meta, stats, nrows):
    os.makedirs(FIG_DIR, exist_ok=True)
    print("\nSaving figures to %s/ ..." % FIG_DIR)

    # --- Metadata ---
    counts = meta["CALL_TYPE"].value_counts().sort_index()
    names = {"A": "A (central)", "B": "B (stand)", "C": "C (street)"}
    fig, ax = bar_chart([names.get(k, k) for k in counts.index], counts.values,
                        "Trips per CALL_TYPE", figsize=(6, 4))
    for i, v in enumerate(counts.values):
        ax.text(i, v, "%d" % v, ha="center", va="bottom", fontsize=9)   # number on top of each bar
    save_figure(fig, "metadata_call_type.png")

    per_taxi = meta["TAXI_ID"].value_counts()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(per_taxi.values, bins=50, color="#4C72B0", edgecolor="white")
    ax.axvline(per_taxi.median(), color="black", linestyle="--",
               label="median = %d" % per_taxi.median())
    ax.set_title("Trips per taxi (%d taxis)" % len(per_taxi))
    ax.set_xlabel("trips")
    ax.set_ylabel("number of taxis")
    ax.legend()
    save_figure(fig, "metadata_trips_per_taxi.png")

    # Same local Porto time as in check 8
    local = pd.to_datetime(meta["TIMESTAMP"].astype(int), unit="s", utc=True).dt.tz_convert("Europe/Lisbon")

    per_hour = local.dt.hour.value_counts().sort_index()
    fig, ax = bar_chart(per_hour.index, per_hour.values,
                        "Trips by start hour (Europe/Lisbon)")
    ax.set_xlabel("hour of day")
    ax.set_xticks(range(0, 24, 2))
    save_figure(fig, "metadata_trips_by_hour.png")

    # Note: the last hour of 30 June UTC is already 1 July in Porto, so a
    # tiny "2014-07" bar shows up at the end
    per_month = local.dt.strftime("%Y-%m").value_counts().sort_index()
    fig, ax = bar_chart(per_month.index, per_month.values,
                        "Trips by month (Europe/Lisbon)", figsize=(8, 4))
    ax.tick_params(axis="x", rotation=45)
    save_figure(fig, "metadata_trips_by_month.png")

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    per_weekday = local.dt.day_name().value_counts().reindex(weekdays)
    fig, ax = bar_chart([d[:3] for d in weekdays], per_weekday.values,
                        "Trips by weekday (Europe/Lisbon)", figsize=(6, 4))
    save_figure(fig, "metadata_trips_by_weekday.png")

    # --- POLYLINE ---
    n = stats["n_points"]
    valid = n >= 3
    histogram(n, 500, "GPS points per trip (all trips)", "points per trip",
              "polyline_points_per_trip.png", log_y=True)
    histogram((n[valid] - 1) * INTERVAL_S / 60, 120, "Trip duration (valid trips)",
              "duration [min] = (points - 1) * 15 s", "polyline_duration.png")
    histogram(stats.loc[valid, "dist_km"], 50, "Trip distance (valid trips)",
              "distance [km] (sum of haversine steps)", "polyline_distance.png")
    histogram(stats.loc[valid, "max_speed_kmh"], 400,
              "Top speed between two consecutive points (valid trips)",
              "max step speed [km/h]", "polyline_max_speed.png", log_y=True)

    # Map: where in Porto are the GPS points? Each hexagon is coloured by
    # how many points fall inside it, so busy roads show up dark.
    lon, lat = sample_points(nrows)
    inside = ~outside_box(lon, lat, PORTO_BOX)
    fig, ax = plt.subplots(figsize=(7, 7))
    hb = ax.hexbin(lon[inside], lat[inside], gridsize=200, bins="log", cmap="Blues", mincnt=1)
    ax.plot(*CITY_HALL, marker="x", color="#d03b3b", markersize=8)
    ax.annotate("City Hall", CITY_HALL, textcoords="offset points", xytext=(6, 6), fontsize=9)
    ax.set_title("GPS point density (every 100th trip, Porto box)", loc="left")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    # A degree of longitude is shorter than a degree of latitude this far
    # north; this stretches the map so Porto isn't squashed sideways
    ax.set_aspect(1 / np.cos(np.radians(41.15)))
    fig.colorbar(hb, ax=ax, label="points (log)", shrink=0.7)
    save_figure(fig, "polyline_map_hexbin.png")


CHECKS = [check_1_overview, check_2_trip_id_format, check_3_duplicate_trip_ids,
          check_4_call_type, check_5_day_type, check_6_missing_data, check_7_taxis,
          check_8_time, check_9_points_per_trip, check_10_duration, check_11_distance,
          check_12_outside_area, check_13_speed_jumps, check_14_precision,
          check_15_reference_counts]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checks", nargs="*", type=int, help="check numbers to run (default: all)")
    parser.add_argument("--sample", type=int, help="only use the first N rows")
    parser.add_argument("--figures", action="store_true", help="save the plots to eda/figures/")
    args = parser.parse_args()

    meta = load_metadata(args.sample)
    stats = load_trip_stats(args.sample)
    # The two tables are matched up by row position, so they must be the same length
    assert len(meta) == len(stats)

    if args.figures:
        make_figures(meta, stats, args.sample)

    # No numbers given = run all checks (unless we only came for the figures)
    selected = args.checks or ([] if args.figures else range(1, len(CHECKS) + 1))
    for number in selected:
        CHECKS[number - 1](meta, stats)


if __name__ == "__main__":
    main()
