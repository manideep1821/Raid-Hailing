CREATE TABLE IF NOT EXISTS users (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    phone TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drivers (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    phone    TEXT NOT NULL,
    car_type TEXT NOT NULL,
    lat      DOUBLE PRECISION NOT NULL,
    lng      DOUBLE PRECISION NOT NULL,
    rating   DOUBLE PRECISION NOT NULL,
    status   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drivers_available_idx ON drivers (car_type) WHERE status = 'available';

CREATE TABLE IF NOT EXISTS coupons (
    code          TEXT PRIMARY KEY,
    discount_type TEXT NOT NULL,
    value         DOUBLE PRECISION NOT NULL,
    max_discount  DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS rides (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES users (id),
    driver_id          TEXT NOT NULL REFERENCES drivers (id),
    requested_car_type TEXT NOT NULL,
    assigned_car_type  TEXT NOT NULL,
    pickup_lat         DOUBLE PRECISION NOT NULL,
    pickup_lng         DOUBLE PRECISION NOT NULL,
    route              JSONB NOT NULL,
    coupon             JSONB,
    status             TEXT NOT NULL,
    started_at         TIMESTAMP NOT NULL,
    ended_at           TIMESTAMP,
    distance_km        DOUBLE PRECISION,
    fare               DOUBLE PRECISION,
    cancellation_fee   DOUBLE PRECISION
);
-- Backstops for the booking invariants, enforced even across processes.
CREATE UNIQUE INDEX IF NOT EXISTS one_ongoing_ride_per_user ON rides (user_id) WHERE status = 'ongoing';
CREATE UNIQUE INDEX IF NOT EXISTS one_ongoing_ride_per_driver ON rides (driver_id) WHERE status = 'ongoing';
CREATE INDEX IF NOT EXISTS rides_user_idx ON rides (user_id);
CREATE INDEX IF NOT EXISTS rides_driver_idx ON rides (driver_id);
