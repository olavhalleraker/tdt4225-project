"""
Shared parsing and cleaning rules.

"""
import csv
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from haversine import haversine

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "porto.csv")

COLUMNS = ["TRIP_ID", "CALL_TYPE", "ORIGIN_CALL", "ORIGIN_STAND", "TAXI_ID",
           "TIMESTAMP", "DAY_TYPE", "MISSING_DATA", "POLYLINE"]

# The taxis report one GPS point every 15 seconds.
SAMPLING_INTERVAL_S = 15

# A trip needs at least this many GPS points to be valid (definition from task 2.7).
MIN_VALID_POINTS = 3

# A GPS point that is further from the previous accepted point than a taxi can
# drive in the elapsed time is a GPS error. Portuguese motorways are limited to
# 120 km/h; 200 km/h leaves plenty of room for GPS noise and fast driving.
MAX_SPEED_KMH = 200

# A trip may start with a few stale positions (the GPS still reporting an old
# location for up to a minute) before jumping to where the taxi really is.
MAX_STALE_START_POINTS = 4

# Times are stored in UTC; Porto is UTC+0 in winter and UTC+1 in summer time.
PORTO_TZ = ZoneInfo("Europe/Lisbon")

# Area the taxis can realistically drive in: mainland Portugal plus Galicia.
# Points outside it (e.g. in the Atlantic or in France) are GPS errors.
LAT_RANGE = (36.9, 43.8)
LON_RANGE = (-9.6, -6.0)


def read_rows(path=CSV_PATH):
    """Yield the CSV rows one at a time (the file is ~1.9 GB, so it is streamed)."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert header == COLUMNS, "Unexpected CSV header: %s" % header
        for row in reader:
            yield row


def count_points(polyline_text):
    """Number of GPS points in a POLYLINE string without parsing it ('[]' -> 0)."""
    return polyline_text.count("[") - 1


def find_duplicate_rows(path=CSV_PATH):
    """TRIP_ID should be unique, but 80 IDs occur more than once. In almost all
    cases one copy has a real trajectory and the other only 0-2 GPS points
    (and sometimes another CALL_TYPE). We keep the copy with the most GPS
    points (the first one on ties, e.g. for exact copies).

    Returns the set of data-row numbers (0-based) that should be skipped."""
    best = {}  # TRIP_ID -> (number of points, row number)
    skip = set()
    for row_number, row in enumerate(read_rows(path)):
        trip_id, n_points = row[0], count_points(row[8])
        if trip_id not in best:
            best[trip_id] = (n_points, row_number)
        elif n_points > best[trip_id][0]:
            skip.add(best[trip_id][1])
            best[trip_id] = (n_points, row_number)
        else:
            skip.add(row_number)
    return skip


def parse_polyline(polyline_text):
    """'[[lon, lat], ...]' -> [(lat, lon), ...]. Note the swap to (lat, lon), which
    is the order the haversine package expects."""
    return [(lat, lon) for lon, lat in json.loads(polyline_text)]


def parse_timestamp(unix_seconds):
    """Unix time -> naive datetime in UTC.

    Unix time counts from 1970-01-01 00:00 UTC, so the values are converted as
    UTC and stored that way. Read as UTC, the dataset starts at 2013-07-01 00:00
    and ends at 2014-06-30 23:59. Use to_porto_time() for Porto clock time.
    """
    return datetime.fromtimestamp(int(unix_seconds), tz=timezone.utc).replace(tzinfo=None)


def to_porto_time(utc_time):
    """Naive UTC datetime -> naive Porto clock time (one hour later in summer time)."""
    return utc_time.replace(tzinfo=timezone.utc).astimezone(PORTO_TZ).replace(tzinfo=None)


def in_region(lat, lon):
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def reachable(p, q):
    """Can a taxi drive from point p to the later point q in the elapsed time
    without exceeding MAX_SPEED_KMH? Points are (point_index, lat, lon)."""
    hours = (q[0] - p[0]) * SAMPLING_INTERVAL_S / 3600
    return haversine((p[1], p[2]), (q[1], q[2])) / hours <= MAX_SPEED_KMH


def run_length(points, start):
    """Number of consecutive points from points[start] where each point is
    reachable from the one before it."""
    n = 1
    while start + n < len(points) and reachable(points[start + n - 1], points[start + n]):
        n += 1
    return n


def clean_points(points):
    """Remove GPS errors from a trajectory.

    1. Region rule: points outside the region are dropped.
    2. Start rule: a trip often starts with a few stale positions (the GPS
       still reporting an old location) followed by an impossible jump to
       where the taxi really is. If at most MAX_STALE_START_POINTS points come
       before an impossible jump, and more points follow after the jump, the
       starting points are dropped.
    3. Speed rule: every following point is kept only if it can be reached
       from the last kept point without driving faster than MAX_SPEED_KMH.
       A single wrong point in the middle is therefore skipped, and after a
       real gap in the recording the trajectory is picked up again as soon as
       the elapsed time makes the new position reachable.

    The original position in the POLYLINE (point_index) is kept, so the time
    of each point is still start_time + 15 s * point_index.

    Returns (kept_points, n_outside_region, n_speed_outliers) where
    kept_points is a list of (point_index, lat, lon)."""
    inside = [(i, lat, lon) for i, (lat, lon) in enumerate(points) if in_region(lat, lon)]
    n_outside = len(points) - len(inside)

    # Start rule: move the start past short stale runs
    start = 0
    while start < len(inside):
        first_run = run_length(inside, start)
        if start + first_run == len(inside):
            break  # no impossible jump after the first run
        too_long_to_be_stale = start + first_run > MAX_STALE_START_POINTS
        not_shorter_than_next = first_run >= run_length(inside, start + first_run)
        if too_long_to_be_stale or not_shorter_than_next:
            break
        start += first_run

    # Speed rule

    kept = []
    for p in inside[start:]:
        if not kept or reachable(kept[-1], p):
            kept.append(p)
    return kept, n_outside, len(inside) - len(kept)


def path_length_km(kept_points):
    """Driven distance: sum of haversine distances between consecutive points."""
    return sum(haversine((a[1], a[2]), (b[1], b[2]))
               for a, b in zip(kept_points, kept_points[1:]))


def duration_seconds(n_points):
    """Trip duration from the number of GPS points (one point every 15 s).
    A trip without any GPS point has an unknown duration (None)."""
    if n_points == 0:
        return None
    return (n_points - 1) * SAMPLING_INTERVAL_S


def build_trip(row):
    """Turn one CSV row into a cleaned trip (dict) and its cleaned GPS points.

    Missing values: ORIGIN_CALL / ORIGIN_STAND are empty strings in the CSV
    when not applicable and become NULL (None). A trip without GPS points
    gets NULL duration, end time, distance and start/end coordinates."""
    (trip_id, call_type, origin_call, origin_stand, taxi_id,
     timestamp, day_type, missing_data, polyline) = row

    raw_points = parse_polyline(polyline)
    kept, n_outside, n_speed = clean_points(raw_points)
    duration = duration_seconds(len(raw_points))
    start_time = parse_timestamp(timestamp)

    trip = {
        "trip_id": int(trip_id),
        "taxi_id": int(taxi_id),
        "call_type": call_type,
        "origin_call": int(origin_call) if origin_call else None,
        "origin_stand": int(origin_stand) if origin_stand else None,
        "day_type": day_type,
        "missing_data": missing_data == "True",
        "start_time": start_time,
        "end_time": None if duration is None else datetime.fromtimestamp(
            int(timestamp) + duration, tz=timezone.utc).replace(tzinfo=None),
        "duration_s": duration,
        "n_points_raw": len(raw_points),
        "n_points_clean": len(kept),
        "distance_km": path_length_km(kept) if kept else None,
        "start_lat": kept[0][1] if kept else None,
        "start_lon": kept[0][2] if kept else None,
        "end_lat": kept[-1][1] if kept else None,
        "end_lon": kept[-1][2] if kept else None,
        # Not stored, only used for reporting:
        "n_outside_region": n_outside,
        "n_speed_outliers": n_speed,
    }
    return trip, kept
