-- There are no migrations: bump SCHEMA_VERSION in app/postgres.py when a table changes,
-- and an older database is refused with instructions to reset it.
CREATE TABLE IF NOT EXISTS schema_version (version INT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS drivers (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    phone    TEXT NOT NULL UNIQUE,
    car_type TEXT NOT NULL,
    lat      DOUBLE PRECISION NOT NULL,
    lng      DOUBLE PRECISION NOT NULL,
    rating       DOUBLE PRECISION NOT NULL,
    status       TEXT NOT NULL,
    last_seen_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS drivers_available_idx ON drivers (car_type) WHERE status = 'available';

CREATE TABLE IF NOT EXISTS coupons (
    code   TEXT PRIMARY KEY,
    kind   TEXT NOT NULL,     -- key of app.discounts.DISCOUNT_TYPES
    params JSONB NOT NULL     -- that discount type's fields
);

CREATE TABLE IF NOT EXISTS rides (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users (id),
    driver_id          TEXT NOT NULL REFERENCES drivers (id),
    requested_car_type TEXT NOT NULL,
    assigned_car_type  TEXT NOT NULL,
    pickup_lat         DOUBLE PRECISION NOT NULL,
    pickup_lng         DOUBLE PRECISION NOT NULL,
    last_lat           DOUBLE PRECISION NOT NULL,   -- running distance is measured up to here
    last_lng           DOUBLE PRECISION NOT NULL,
    distance_km        DOUBLE PRECISION NOT NULL,
    coupon             JSONB,             -- {code, kind, params} snapshot taken at booking
    surge_multiplier   DOUBLE PRECISION NOT NULL,
    status             TEXT NOT NULL,
    booked_at          TIMESTAMP NOT NULL,
    picked_up_at       TIMESTAMP,
    ended_at           TIMESTAMP,
    fare               JSONB,             -- FareBreakdown
    cancellation_fee   DOUBLE PRECISION
);
-- Backstops for the booking invariants, enforced even across processes.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_ride_per_user ON rides (user_id) WHERE status IN ('booked', 'ongoing');
CREATE UNIQUE INDEX IF NOT EXISTS one_active_ride_per_driver ON rides (driver_id) WHERE status IN ('booked', 'ongoing');
CREATE INDEX IF NOT EXISTS rides_user_idx ON rides (user_id);
CREATE INDEX IF NOT EXISTS rides_driver_idx ON rides (driver_id);
CREATE INDEX IF NOT EXISTS rides_booked_at_idx ON rides (booked_at);
