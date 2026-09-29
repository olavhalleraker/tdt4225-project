-- =====================================================================
-- TDT4225 Assignment 2 - Porto Taxi Trajectory dataset
-- MySQL 8.0.39 schema (database: porto_db)
--
-- Tables:  call_type (lookup)  <-  trip  ->  taxi
--                                   ^
--                                   |
--                               gps_point   (~80M rows, kept compact)
--
-- Conventions
--   * All DATETIME values are LOCAL Porto time (Europe/Lisbon, WET/WEST),
--     converted from the unix TIMESTAMP in Python (zoneinfo) at insert time.
--     start_unix keeps the raw unix value for exact, DST-safe arithmetic.
--   * Coordinates are DECIMAL with 6 decimals (the precision of the CSV),
--     latitude DECIMAL(8,6) = 4 bytes, longitude DECIMAL(9,6) = 5 bytes.
--   * Per-point timestamps are NOT stored: point time = trip.start_time
--     + INTERVAL 15*seq SECOND (the dataset samples every 15 s).
--
-- Note: create_tables.py splits this file on semicolons, so comments
-- must not contain semicolons.
-- =====================================================================

-- Drop in reverse foreign-key order (children first)
DROP TABLE IF EXISTS gps_point;
DROP TABLE IF EXISTS trip;
DROP TABLE IF EXISTS taxi;
DROP TABLE IF EXISTS call_type;

-- ---------------------------------------------------------------------
-- call_type: lookup for CALL_TYPE (A/B/C). Tiny, gives the codes a
-- human-readable description and lets a FK guarantee valid codes.
-- ---------------------------------------------------------------------
CREATE TABLE call_type (
    code        CHAR(1)      NOT NULL,
    description VARCHAR(40)  NOT NULL,
    PRIMARY KEY (code)
) ENGINE = InnoDB;

INSERT INTO call_type (code, description) VALUES
    ('A', 'Dispatched from central'),
    ('B', 'Requested at taxi stand'),
    ('C', 'Hailed on the street');

-- ---------------------------------------------------------------------
-- taxi: one row per distinct TAXI_ID (about 450 taxis).
-- ---------------------------------------------------------------------
CREATE TABLE taxi (
    taxi_id     INT UNSIGNED NOT NULL,        -- TAXI_ID from CSV, e.g. 20000589
    PRIMARY KEY (taxi_id)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- trip: one row per cleaned CSV row (about 1.7M). Raw attributes + derived
-- columns precomputed at insert time so that queries 2-5, 7-10 never
-- touch gps_point, and query 6 can prefilter on the bounding box.
--
-- Surrogate key `id` (INT, 4 bytes) instead of the 19-digit TRIP_ID:
--   * TRIP_ID is NOT unique in the raw dataset (80 ids occur more than
--     once, with different content). The loader keeps one row per id, but
--     the surrogate key keeps the schema independent of that cleaning rule.
--   * gps_point repeats the trip key 80M times - 4 bytes instead of 8
--     saves about 320 MB there.
-- ---------------------------------------------------------------------
CREATE TABLE trip (
    id            INT UNSIGNED      NOT NULL AUTO_INCREMENT,  -- surrogate key (assigned by loader, 1..N)
    trip_code     BIGINT UNSIGNED   NOT NULL,                 -- original TRIP_ID (unique after cleaning, enforced)
    taxi_id       INT UNSIGNED      NOT NULL,                 -- FK -> taxi
    call_type     CHAR(1)           NOT NULL,                 -- FK -> call_type (A/B/C)
    origin_call   INT UNSIGNED      NULL,                     -- client id, only for call_type A
    origin_stand  SMALLINT UNSIGNED NULL,                     -- stand id, only for call_type B
    day_type      CHAR(1)           NOT NULL,                 -- A/B/C (DAY_TYPE)
    missing_data  BOOLEAN           NOT NULL,                 -- MISSING_DATA flag

    -- time (local Europe/Lisbon)
    start_unix    INT UNSIGNED      NOT NULL,                 -- raw TIMESTAMP (unix seconds, UTC)
    start_time    DATETIME          NOT NULL,                 -- local start time
    end_time      DATETIME          NOT NULL,                 -- local time of last point = start + duration_s
    duration_s    INT UNSIGNED      NOT NULL,                 -- 15 * (n_points - 1), 0 if n_points <= 1

    -- trajectory summary
    n_points      SMALLINT UNSIGNED NOT NULL,                 -- number of points stored in gps_point
    is_valid      BOOLEAN AS (n_points >= 3) VIRTUAL,         -- query 7: invalid = fewer than 3 points
    distance_km   DECIMAL(9,3)      NOT NULL,                 -- sum of haversine between consecutive points, km
    has_gap       BOOLEAN           NOT NULL DEFAULT 0,       -- 1 if any segment implies > 200 km/h (GPS jump or signal gap)

    start_lat     DECIMAL(8,6)      NULL,                     -- first point (NULL if no points)
    start_lon     DECIMAL(9,6)      NULL,
    end_lat       DECIMAL(8,6)      NULL,                     -- last point (NULL if no points)
    end_lon       DECIMAL(9,6)      NULL,
    min_lat       DECIMAL(8,6)      NULL,                     -- bounding box of all points
    max_lat       DECIMAL(8,6)      NULL,                     --   (query 6 prefilter)
    min_lon       DECIMAL(9,6)      NULL,
    max_lon       DECIMAL(9,6)      NULL,

    PRIMARY KEY (id),
    UNIQUE KEY uq_trip_code (trip_code),
    KEY idx_trip_taxi_start (taxi_id, start_time),            -- per-taxi queries 3, 4a, 5, 10 (+ FK index)
    KEY idx_trip_call_type (call_type),                       -- FK index, query 4b

    CONSTRAINT fk_trip_taxi FOREIGN KEY (taxi_id)
        REFERENCES taxi (taxi_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_trip_call_type FOREIGN KEY (call_type)
        REFERENCES call_type (code)
        ON DELETE RESTRICT ON UPDATE CASCADE,

    CONSTRAINT chk_trip_day_type CHECK (day_type IN ('A', 'B', 'C')),
    CONSTRAINT chk_trip_times    CHECK (end_time >= start_time)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- gps_point: one row per POLYLINE point (about 80M rows).
-- Designed for minimum bytes per row:
--   * composite clustered PK (trip_id, seq) - no surrogate id, and the PK
--     doubles as the index required by the FK on trip_id
--   * seq SMALLINT (2 B), lat DECIMAL(8,6) (4 B), lon DECIMAL(9,6) (5 B)
--   * no per-point timestamp (derived: start_time + 15*seq seconds)
--   * NO secondary indexes (a (lat,lon) index would cost about 2 GB)
-- Rows are stored physically grouped by trip and ordered by seq, so all
-- points of one trip are read with a single PK range scan.
-- ---------------------------------------------------------------------
CREATE TABLE gps_point (
    trip_id  INT UNSIGNED      NOT NULL,   -- FK -> trip.id
    seq      SMALLINT UNSIGNED NOT NULL,   -- 0-based position in POLYLINE
    lat      DECIMAL(8,6)      NOT NULL,   -- latitude  (2nd value of [lon, lat])
    lon      DECIMAL(9,6)      NOT NULL,   -- longitude (1st value of [lon, lat])

    PRIMARY KEY (trip_id, seq),

    CONSTRAINT fk_gps_point_trip FOREIGN KEY (trip_id)
        REFERENCES trip (id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE = InnoDB;
