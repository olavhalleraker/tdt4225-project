"""
Part 1.1 - Exploratory data analysis (EDA) of the Porto taxi dataset.

The CSV file (~1.9 GB) is streamed row by row, so it is never loaded into
memory as a whole. Statistics for every column are printed with tabulate,
and figures are written to report/figures/.

Run:  python part1_eda.py | tee ../output/part1_eda.txt
"""
import os
from array import array
from collections import Counter, defaultdict
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from haversine import haversine
from tabulate import tabulate

import cleaning

FIGURE_DIR = os.path.join(os.path.dirname(__file__), "..", "report", "figures")

# Portuguese public holidays in the period (four holidays were suspended 2013-2015).
HOLIDAYS = {
    date(2013, 8, 15): "Assumption Day", date(2013, 12, 8): "Immaculate Conception",
    date(2013, 12, 25): "Christmas Day", date(2014, 1, 1): "New Year's Day",
    date(2014, 4, 18): "Good Friday", date(2014, 4, 25): "Freedom Day",
    date(2014, 5, 1): "Labour Day", date(2014, 6, 10): "Portugal Day",
    date(2014, 6, 24): "St. John (Porto holiday)",
}

# Chart colours
COLORS = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a"}
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK_2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
THOUSANDS = FuncFormatter(lambda value, _: f"{value:,.0f}")


def heading(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def percentile(sorted_values, p):
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(p / 100 * len(sorted_values)))]


class EDA:
    def __init__(self):
        # Raw column statistics
        self.n_rows = 0
        self.empty = Counter()            # column -> number of empty values
        self.trip_id_rows = Counter()     # TRIP_ID -> number of rows
        self.call_type = Counter()
        self.call_type_origin = Counter() # (CALL_TYPE, ORIGIN_CALL set, ORIGIN_STAND set) -> rows
        self.day_type = Counter()
        self.day_type_holidays = defaultdict(Counter)
        self.missing_data = Counter()
        self.missing_data_trips = []
        self.non_numeric = Counter()      # column -> values that are not integers
        self.trips_per_taxi = Counter()
        self.trips_per_month = Counter()
        self.trips_per_hour = defaultdict(Counter)  # call type -> hour -> trips
        self.trips_per_weekday = Counter()
        self.first_ts = None
        self.last_ts = None

        # Trajectory statistics (deduplicated rows, same cleaning as the insert)
        self.points_per_trip = array("i")
        self.raw_points = 0
        self.kept_points = 0
        self.outside_region = 0
        self.speed_outliers = 0
        self.trips_with_removed_points = 0
        self.trips_mostly_removed = 0
        self.became_invalid = 0
        self.segment_speed_hist = Counter()  # 10 km/h buckets of raw consecutive points
        self.durations_min = array("d")
        self.distances_km = array("d")
        self.taxi_intervals = defaultdict(list)
        self.sample_lat = array("d")
        self.sample_lon = array("d")
        self.duplicate_details = defaultdict(list)

    # ------------------------------------------------------------------ #
    def scan(self):
        skip = cleaning.find_duplicate_rows()
        for row_number, row in enumerate(cleaning.read_rows()):
            self.scan_columns(row)
            if row_number not in skip:
                self.scan_trajectory(row)
        # Details of the duplicated TRIP_IDs (second small pass)
        for row_number, row in enumerate(cleaning.read_rows()):
            if self.trip_id_rows[row[0]] > 1:
                self.duplicate_details[row[0]].append(
                    (row_number in skip, row[1], row[2], row[3], cleaning.count_points(row[8]), row))

    def scan_columns(self, row):
        self.n_rows += 1
        for name, value in zip(cleaning.COLUMNS, row):
            if value == "" or (name == "POLYLINE" and value == "[]"):
                self.empty[name] += 1
        for name, value in zip(cleaning.COLUMNS[:6], row[:6]):
            if name != "CALL_TYPE" and value != "" and not value.isdigit():
                self.non_numeric[name] += 1

        trip_id, call_type, origin_call, origin_stand, taxi_id, ts, day_type, missing, polyline = row
        self.trip_id_rows[trip_id] += 1
        self.call_type[call_type] += 1
        self.call_type_origin[(call_type, origin_call != "", origin_stand != "")] += 1
        self.day_type[day_type] += 1
        self.missing_data[missing] += 1
        if missing == "True":
            self.missing_data_trips.append((trip_id, call_type, taxi_id, cleaning.count_points(polyline)))
        self.trips_per_taxi[taxi_id] += 1

        start = cleaning.parse_timestamp(ts)
        self.first_ts = min(self.first_ts or start, start)
        self.last_ts = max(self.last_ts or start, start)
        self.trips_per_month[start.strftime("%Y-%m")] += 1
        self.trips_per_hour[call_type][start.hour] += 1
        self.trips_per_weekday[start.strftime("%a")] += 1
        if start.date() in HOLIDAYS:
            self.day_type_holidays[start.date()][day_type] += 1

    def scan_trajectory(self, row):
        trip, kept = cleaning.build_trip(row)
        raw = cleaning.parse_polyline(row[8])
        n_raw = trip["n_points_raw"]
        self.points_per_trip.append(n_raw)
        self.raw_points += n_raw
        self.kept_points += trip["n_points_clean"]
        self.outside_region += trip["n_outside_region"]
        self.speed_outliers += trip["n_speed_outliers"]
        removed = n_raw - trip["n_points_clean"]
        if removed:
            self.trips_with_removed_points += 1
            if removed > n_raw / 2:
                self.trips_mostly_removed += 1

        for a, b in zip(raw, raw[1:]):
            speed = haversine(a, b) / (cleaning.SAMPLING_INTERVAL_S / 3600)
            self.segment_speed_hist[min(int(speed // 10) * 10, 400)] += 1

        for i in range(0, len(kept), 20):  # every 20th point for the density map
            self.sample_lat.append(kept[i][1])
            self.sample_lon.append(kept[i][2])

        if n_raw >= cleaning.MIN_VALID_POINTS:
            if trip["n_points_clean"] < cleaning.MIN_VALID_POINTS:
                self.became_invalid += 1
            self.durations_min.append(trip["duration_s"] / 60)
            self.distances_km.append(trip["distance_km"] or 0.0)
            self.taxi_intervals[trip["taxi_id"]].append((trip["start_time"], trip["end_time"]))

    # ------------------------------------------------------------------ #
    def report(self):
        n = self.n_rows
        heading("1. Overview")
        print(tabulate([["File", "data/porto.csv"],
                        ["Size", "%.2f GB" % (os.path.getsize(cleaning.CSV_PATH) / 1e9)],
                        ["Rows (trips)", f"{n:,}"],
                        ["Columns", ", ".join(cleaning.COLUMNS)],
                        ["First trip start", self.first_ts],
                        ["Last trip start", self.last_ts]], tablefmt="simple"))

        heading("2. Missing / empty values per column")
        integer_columns = ("TRIP_ID", "ORIGIN_CALL", "ORIGIN_STAND", "TAXI_ID", "TIMESTAMP")
        rows = [[c, f"{self.empty[c]:,}", "%.2f %%" % pct(self.empty[c], n),
                 self.non_numeric[c] if c in integer_columns else "-"]
                for c in cleaning.COLUMNS]
        print(tabulate(rows, headers=["Column", "Empty values", "Share", "Non-integer values"],
                       tablefmt="simple"))
        print("(POLYLINE counts '[]', i.e. a trip without any GPS point, as empty.)")

        heading("3. TRIP_ID uniqueness")
        dup_ids = {k: v for k, v in self.trip_id_rows.items() if v > 1}
        identical = sum(1 for rows in self.duplicate_details.values()
                        if all(r[5] == rows[0][5] for r in rows))
        differs = Counter()
        for rows in self.duplicate_details.values():
            for i, name in enumerate(cleaning.COLUMNS):
                if len({r[5][i] for r in rows}) > 1:
                    differs[name] += 1
        print(tabulate([["Unique TRIP_IDs", f"{len(self.trip_id_rows):,}"],
                        ["TRIP_IDs that occur more than once", len(dup_ids)],
                        ["Extra (duplicate) rows", sum(v - 1 for v in dup_ids.values())],
                        ["Duplicate groups that are exact copies", identical],
                        ["Groups where POLYLINE differs", differs["POLYLINE"]],
                        ["Groups where CALL_TYPE differs", differs["CALL_TYPE"]],
                        ["Groups where ORIGIN_STAND differs", differs["ORIGIN_STAND"]],
                        ["Groups where ORIGIN_CALL differs", differs["ORIGIN_CALL"]],
                        ["Groups where TAXI_ID / TIMESTAMP differ",
                         differs["TAXI_ID"] + differs["TIMESTAMP"]]], tablefmt="simple"))
        dropped_points = Counter(min(r[4], 3) for rows in self.duplicate_details.values()
                                 for r in rows if r[0])
        print("\nGPS points in the dropped copies: " +
              ", ".join(f"{'3+' if k == 3 else k} points: {v}" for k, v in sorted(dropped_points.items())))
        print("\nExamples (kept copy = the one with most GPS points):")
        examples = []
        for trip_id, rows in list(self.duplicate_details.items())[:4]:
            for dropped, ct, oc, os_, npts, _ in rows:
                examples.append([trip_id, ct, oc or "NULL", os_ or "NULL", npts,
                                 "dropped" if dropped else "kept"])
        print(tabulate(examples, headers=["TRIP_ID", "CALL_TYPE", "ORIGIN_CALL", "ORIGIN_STAND",
                                          "GPS points", "Decision"], tablefmt="simple"))

        heading("4. CALL_TYPE and its relation to ORIGIN_CALL / ORIGIN_STAND")
        print(tabulate([[ct, f"{c:,}", "%.1f %%" % pct(c, n)] for ct, c in sorted(self.call_type.items())],
                       headers=["CALL_TYPE", "Trips", "Share"], tablefmt="simple"))
        print()
        print(tabulate([[ct, "yes" if oc else "no", "yes" if os_ else "no", f"{c:,}"]
                        for (ct, oc, os_), c in sorted(self.call_type_origin.items())],
                       headers=["CALL_TYPE", "ORIGIN_CALL set", "ORIGIN_STAND set", "Trips"],
                       tablefmt="simple"))
        print("-> ORIGIN_CALL is set exactly for type A. ORIGIN_STAND is never set for A/C,")
        print("   but %s type-B trips (%.1f %% of B) have no ORIGIN_STAND (kept as NULL)." % (
            f"{self.call_type_origin[('B', False, False)]:,}",
            pct(self.call_type_origin[("B", False, False)], self.call_type["B"])))

        heading("5. DAY_TYPE")
        print(tabulate([[dt, f"{c:,}"] for dt, c in sorted(self.day_type.items())],
                       headers=["DAY_TYPE", "Trips"], tablefmt="simple"))
        print("\nDAY_TYPE on Portuguese public holidays (should have been 'B'):")
        print(tabulate([[d, HOLIDAYS[d], dict(self.day_type_holidays[d])] for d in sorted(HOLIDAYS)],
                       headers=["Date", "Holiday", "DAY_TYPE counts"], tablefmt="simple"))
        print("-> DAY_TYPE is 'A' for every trip, also on holidays, so the column carries no information.")

        heading("6. MISSING_DATA")
        print(tabulate([[k, f"{c:,}"] for k, c in sorted(self.missing_data.items())],
                       headers=["MISSING_DATA", "Trips"], tablefmt="simple"))
        print("\nTrips flagged with missing GPS points:")
        print(tabulate(self.missing_data_trips, headers=["TRIP_ID", "CALL_TYPE", "TAXI_ID", "GPS points"],
                       tablefmt="simple"))

        heading("7. TAXI_ID")
        counts = sorted(self.trips_per_taxi.values())
        print(tabulate([["Distinct taxis", len(counts)],
                        ["Trips per taxi: min", counts[0]],
                        ["Trips per taxi: median", percentile(counts, 50)],
                        ["Trips per taxi: mean", "%.1f" % (sum(counts) / len(counts))],
                        ["Trips per taxi: max", counts[-1]],
                        ["Taxis with < 100 trips", sum(1 for c in counts if c < 100)]],
                       tablefmt="simple"))

        heading("8. TIMESTAMP (start of trip)")
        print(tabulate([[m, f"{c:,}"] for m, c in sorted(self.trips_per_month.items())],
                       headers=["Month", "Trips"], tablefmt="simple"))
        print()
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        print(tabulate([[d, f"{self.trips_per_weekday[d]:,}"] for d in days],
                       headers=["Weekday", "Trips"], tablefmt="simple"))

        heading("9. POLYLINE - GPS points per trip (after removing duplicate rows)")
        pts = sorted(self.points_per_trip)
        trips = len(pts)
        buckets = [("0 (empty)", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3-9", 3, 9), ("10-49", 10, 49),
                   ("50-99", 50, 99), ("100-499", 100, 499), ("500-999", 500, 999), (">= 1000", 1000, 10**9)]
        hist = Counter(self.points_per_trip)
        rows = []
        for label, lo, hi in buckets:
            c = sum(v for k, v in hist.items() if lo <= k <= hi)
            rows.append([label, f"{c:,}", "%.2f %%" % pct(c, trips)])
        print(tabulate(rows, headers=["GPS points", "Trips", "Share"], tablefmt="simple"))
        invalid = sum(1 for p in pts if p < cleaning.MIN_VALID_POINTS)
        print()
        print(tabulate([["Trips", f"{trips:,}"],
                        ["GPS points in total", f"{self.raw_points:,}"],
                        ["Points per trip: median / mean / max",
                         "%d / %.1f / %d" % (percentile(pts, 50), self.raw_points / trips, pts[-1])],
                        ["Invalid trips (< 3 GPS points)", f"{invalid:,} ({pct(invalid, trips):.2f} %)"]],
                       tablefmt="simple"))

        heading("10. GPS errors (cleaning rules, see cleaning.py)")
        print(tabulate([
            ["Points outside the region (lat %.1f..%.1f, lon %.1f..%.1f)" % (*cleaning.LAT_RANGE, *cleaning.LON_RANGE),
             f"{self.outside_region:,}"],
            ["Points removed by the start/speed rules (> %d km/h jump)" % cleaning.MAX_SPEED_KMH,
             f"{self.speed_outliers:,}"],
            ["Points removed in total",
             f"{self.raw_points - self.kept_points:,} ({pct(self.raw_points - self.kept_points, self.raw_points):.3f} %)"],
            ["Points kept", f"{self.kept_points:,}"],
            ["Trips with at least one removed point", f"{self.trips_with_removed_points:,}"],
            ["Trips that lost more than half of their points", f"{self.trips_mostly_removed:,}"],
            ["Valid trips with < 3 points left after cleaning", f"{self.became_invalid:,}"]],
            tablefmt="simple"))
        total_segments = sum(self.segment_speed_hist.values())
        print("\nSpeed between consecutive raw GPS points (15 s apart):")
        speed_rows = []
        for label, lo, hi in [("0-49 km/h", 0, 49), ("50-99 km/h", 50, 99), ("100-149 km/h", 100, 149),
                              ("150-199 km/h", 150, 199), (">= 200 km/h", 200, 10**9)]:
            c = sum(v for k, v in self.segment_speed_hist.items() if lo <= k <= hi)
            speed_rows.append([label, f"{c:,}", "%.3f %%" % pct(c, total_segments)])
        print(tabulate(speed_rows, headers=["Speed", "Segments", "Share"], tablefmt="simple"))

        heading("11. Duration and distance of valid trips (>= 3 GPS points)")
        dur = sorted(self.durations_min)
        dist = sorted(self.distances_km)
        rows = []
        for p in (1, 25, 50, 75, 99):
            rows.append(["p%d" % p, "%.1f" % percentile(dur, p), "%.2f" % percentile(dist, p)])
        rows.append(["max", "%.1f" % dur[-1], "%.2f" % dist[-1]])
        rows.append(["mean", "%.1f" % (sum(dur) / len(dur)), "%.2f" % (sum(dist) / len(dist))])
        print(tabulate(rows, headers=["", "Duration (min)", "Distance (km)"], tablefmt="simple"))
        print()
        print(tabulate([["Trips longer than 2 hours", f"{sum(1 for d in dur if d > 120):,}"],
                        ["Trips longer than 6 hours", f"{sum(1 for d in dur if d > 360):,}"],
                        ["Trips that moved less than 100 m in total", f"{sum(1 for d in dist if d < 0.1):,}"]],
                       tablefmt="simple"))

        heading("12. Overlapping trips of the same taxi")
        pairs = overlaps = 0
        overlap_s = []
        for intervals in self.taxi_intervals.values():
            intervals.sort()
            for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
                pairs += 1
                if s2 < e1:
                    overlaps += 1
                    overlap_s.append((e1 - s2).total_seconds())
        overlap_s.sort()
        print(tabulate([["Consecutive trip pairs (same taxi)", f"{pairs:,}"],
                        ["Pairs where the next trip starts before the previous ends", f"{overlaps:,}"],
                        ["Median overlap (s)", percentile(overlap_s, 50)],
                        ["90th percentile overlap (s)", percentile(overlap_s, 90)]], tablefmt="simple"))

    # ------------------------------------------------------------------ #
    def figures(self):
        os.makedirs(FIGURE_DIR, exist_ok=True)
        plt.rcParams.update({
            "font.family": "sans-serif", "font.size": 10, "axes.edgecolor": MUTED,
            "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
            "axes.titlecolor": INK, "axes.titlesize": 11, "axes.titleweight": "bold",
            "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
            "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
            "legend.frameon": False, "savefig.dpi": 160, "savefig.bbox": "tight",
        })

        # Share of trips per start hour by call type
        fig, ax = plt.subplots(figsize=(9, 3.6))
        width = 0.27
        for i, ct in enumerate(["A", "B", "C"]):
            total = sum(self.trips_per_hour[ct].values())
            shares = [pct(self.trips_per_hour[ct][h], total) for h in range(24)]
            ax.bar([h + (i - 1) * width for h in range(24)], shares, width=width * 0.9,
                   color=COLORS[ct], label={"A": "A - dispatched", "B": "B - taxi stand",
                                            "C": "C - street"}[ct])
        ax.set_xticks(range(24))
        ax.set_xlabel("Start hour")
        ax.set_ylabel("Share of the call type's trips (%)")
        ax.set_title("Trips by start hour and call type")
        ax.set_ylim(0, 8.5)
        ax.grid(axis="x", visible=False)
        ax.legend(ncol=3, loc="upper right")
        fig.savefig(os.path.join(FIGURE_DIR, "eda_trips_per_hour.png"))
        plt.close(fig)

        # GPS points per trip (0-150)
        hist = Counter(self.points_per_trip)
        fig, ax = plt.subplots(figsize=(9, 3.4))
        xs = list(range(0, 151))
        ax.bar(xs, [hist[x] for x in xs], width=0.85,
               color=[ORANGE if x < cleaning.MIN_VALID_POINTS else BLUE for x in xs])
        ax.set_xlabel("GPS points in the trip (0-150 shown)")
        ax.set_ylabel("Trips")
        ax.set_title("GPS points per trip (orange: invalid trips with < 3 points)")
        ax.yaxis.set_major_formatter(THOUSANDS)
        ax.grid(axis="x", visible=False)
        fig.savefig(os.path.join(FIGURE_DIR, "eda_points_per_trip.png"))
        plt.close(fig)

        # Trips per month
        months = sorted(self.trips_per_month)
        fig, ax = plt.subplots(figsize=(9, 3.2))
        ax.bar(range(len(months)), [self.trips_per_month[m] / 1000 for m in months], width=0.7, color=BLUE)
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels(months, rotation=45, ha="right")
        ax.set_ylabel("Trips (thousands)")
        ax.set_title("Trips per month")
        ax.grid(axis="x", visible=False)
        fig.savefig(os.path.join(FIGURE_DIR, "eda_trips_per_month.png"))
        plt.close(fig)

        # Density of (cleaned) GPS points around Porto
        from matplotlib.colors import LinearSegmentedColormap
        cmap = LinearSegmentedColormap.from_list(
            "blues", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
        fig, ax = plt.subplots(figsize=(7.5, 6))
        hb = ax.hexbin(self.sample_lon, self.sample_lat, gridsize=220, bins="log", mincnt=1,
                       cmap=cmap, extent=(-8.72, -8.50, 41.08, 41.26), linewidths=0)
        ax.plot(-8.62911, 41.15794, marker="o", markersize=9, markerfacecolor=ORANGE,
                markeredgecolor="white", markeredgewidth=1.5, linestyle="none", label="Porto City Hall")
        ax.set_xlim(-8.72, -8.50)
        ax.set_ylim(41.08, 41.26)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title("Density of GPS points (every 20th cleaned point)")
        ax.grid(False)
        ax.legend(loc="lower right")
        fig.colorbar(hb, ax=ax, label="Points per cell (log scale)")
        fig.savefig(os.path.join(FIGURE_DIR, "eda_gps_density.png"))
        plt.close(fig)

        # Duration and distance of valid trips
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.2))
        axes[0].hist([d for d in self.durations_min if d <= 60], bins=60, color=BLUE, rwidth=0.85)
        axes[0].set_xlabel("Duration (min, trips <= 60 min shown)")
        axes[0].set_ylabel("Trips")
        axes[0].set_title("Trip duration")
        axes[1].hist([d for d in self.distances_km if d <= 30], bins=60, color=BLUE, rwidth=0.85)
        axes[1].set_xlabel("Distance (km, trips <= 30 km shown)")
        axes[1].set_title("Trip distance")
        for ax in axes:
            ax.yaxis.set_major_formatter(THOUSANDS)
            ax.grid(axis="x", visible=False)
        fig.tight_layout()
        fig.savefig(os.path.join(FIGURE_DIR, "eda_duration_distance.png"))
        plt.close(fig)

        # Speed between consecutive raw GPS points
        fig, ax = plt.subplots(figsize=(9, 3.2))
        speeds = sorted(self.segment_speed_hist)
        ax.bar(speeds, [self.segment_speed_hist[s] for s in speeds], width=8.5, align="edge",
               color=[ORANGE if s >= cleaning.MAX_SPEED_KMH else BLUE for s in speeds])
        ax.set_yscale("log")
        ax.set_xlabel("Speed between consecutive GPS points (km/h, last bar = 400+)")
        ax.set_ylabel("Segments (log scale)")
        ax.set_title("Implied speed between GPS points (orange: above the %d km/h limit)"
                     % cleaning.MAX_SPEED_KMH)
        ax.grid(axis="x", visible=False)
        fig.savefig(os.path.join(FIGURE_DIR, "eda_segment_speed.png"))
        plt.close(fig)
        print("\nFigures written to report/figures/")


def main():
    eda = EDA()
    eda.scan()
    eda.report()
    eda.figures()


if __name__ == "__main__":
    main()
