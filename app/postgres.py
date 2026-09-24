"""Postgres-backed repositories. Every write is a single autocommit statement, so the
atomic guarantees of the repository contract come from conditional UPDATEs and
partial unique indexes rather than application locks."""
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.exceptions import InvalidRideStateError, NotFoundError, ValidationError
from app.discounts import build_discount
from app.models import CarType, Coupon, Driver, DriverStatus, FareBreakdown, Location, Ride, RideStatus, User
from app.repository import (CouponRepository, DriverRepository, Repositories, RideRepository,
                            UserRepository)

SCHEMA = Path(__file__).with_name("schema.sql")


class _Base:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def _one(self, sql: str, params=()) -> Optional[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params=()) -> List[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _exec(self, sql: str, params=()) -> int:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).rowcount


class PostgresUserRepository(_Base, UserRepository):
    def add(self, user: User) -> User:
        self._exec("INSERT INTO users (id, name, phone) VALUES (%s, %s, %s)", (user.id, user.name, user.phone))
        return user

    def get(self, user_id: str) -> User:
        row = self._one("SELECT * FROM users WHERE id = %s", (user_id,))
        if row is None:
            raise NotFoundError(f"user '{user_id}' not found")
        return User(**row)


def _driver(row: dict) -> Driver:
    return Driver(row["id"], row["name"], row["phone"], CarType(row["car_type"]),
                  Location(row["lat"], row["lng"]), row["rating"], DriverStatus(row["status"]))


class PostgresDriverRepository(_Base, DriverRepository):
    def add(self, driver: Driver) -> Driver:
        self._exec(
            "INSERT INTO drivers (id, name, phone, car_type, lat, lng, rating, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (driver.id, driver.name, driver.phone, driver.car_type.value, driver.location.lat,
             driver.location.lng, driver.rating, driver.status.value))
        return driver

    def get(self, driver_id: str) -> Driver:
        row = self._one("SELECT * FROM drivers WHERE id = %s", (driver_id,))
        if row is None:
            raise NotFoundError(f"driver '{driver_id}' not found")
        return _driver(row)

    def find_available(self, pickup: Location, radius_km: float,
                       car_type: Optional[CarType] = None) -> List[Driver]:
        if car_type is None:
            rows = self._all("SELECT * FROM drivers WHERE status = 'available'")
        else:
            rows = self._all("SELECT * FROM drivers WHERE status = 'available' AND car_type = %s", (car_type.value,))
        drivers = [_driver(r) for r in rows]
        return [d for d in drivers if d.location.distance_km(pickup) <= radius_km]

    def update_location(self, driver_id: str, location: Location) -> Driver:
        row = self._one("UPDATE drivers SET lat = %s, lng = %s WHERE id = %s RETURNING *",
                        (location.lat, location.lng, driver_id))
        if row is None:
            raise NotFoundError(f"driver '{driver_id}' not found")
        return _driver(row)

    def try_claim(self, driver_id: str) -> bool:
        return self._exec("UPDATE drivers SET status = 'on_ride' WHERE id = %s AND status = 'available'",
                          (driver_id,)) == 1

    def release(self, driver_id: str, location: Optional[Location] = None) -> None:
        if location is None:
            self._exec("UPDATE drivers SET status = 'available' WHERE id = %s", (driver_id,))
        else:
            self._exec("UPDATE drivers SET status = 'available', lat = %s, lng = %s WHERE id = %s",
                       (location.lat, location.lng, driver_id))


def _coupon_json(coupon: Optional[Coupon]):
    if coupon is None:
        return None
    return Jsonb({"code": coupon.code, "kind": coupon.discount.kind, "params": coupon.discount.params()})


def _coupon(code: str, kind: str, params: dict) -> Coupon:
    return Coupon(code, build_discount(kind, params))


def _ride(row: dict) -> Ride:
    c, fare = row["coupon"], row["fare"]
    return Ride(
        id=row["id"], user_id=row["user_id"], driver_id=row["driver_id"],
        requested_car_type=CarType(row["requested_car_type"]),
        assigned_car_type=CarType(row["assigned_car_type"]),
        pickup=Location(row["pickup_lat"], row["pickup_lng"]),
        route=[Location(lat, lng) for lat, lng in row["route"]],
        coupon=_coupon(c["code"], c["kind"], c["params"]) if c else None,
        surge_multiplier=row["surge_multiplier"], status=RideStatus(row["status"]),
        booked_at=row["booked_at"], picked_up_at=row["picked_up_at"], ended_at=row["ended_at"],
        distance_km=row["distance_km"], fare=FareBreakdown(**fare) if fare else None,
        cancellation_fee=row["cancellation_fee"],
    )


def _route_json(route: List[Location]) -> Jsonb:
    return Jsonb([[p.lat, p.lng] for p in route])


class PostgresRideRepository(_Base, RideRepository):
    def create(self, ride: Ride) -> Ride:
        try:
            self._exec(
                "INSERT INTO rides (id, user_id, driver_id, requested_car_type, assigned_car_type, "
                "pickup_lat, pickup_lng, route, coupon, surge_multiplier, status, booked_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ride.id, ride.user_id, ride.driver_id, ride.requested_car_type.value,
                 ride.assigned_car_type.value, ride.pickup.lat, ride.pickup.lng, _route_json(ride.route),
                 _coupon_json(ride.coupon), ride.surge_multiplier, ride.status.value, ride.booked_at))
        except UniqueViolation:
            raise InvalidRideStateError("user already has an active ride")
        return ride

    def get(self, ride_id: str) -> Ride:
        row = self._one("SELECT * FROM rides WHERE id = %s", (ride_id,))
        if row is None:
            raise NotFoundError(f"ride '{ride_id}' not found")
        return _ride(row)

    def for_user(self, user_id: str) -> List[Ride]:
        return [_ride(r) for r in self._all("SELECT * FROM rides WHERE user_id = %s", (user_id,))]

    def for_driver(self, driver_id: str) -> List[Ride]:
        return [_ride(r) for r in self._all("SELECT * FROM rides WHERE driver_id = %s", (driver_id,))]

    def count_booked_near(self, pickup: Location, radius_km: float, since: datetime) -> int:
        rows = self._all("SELECT pickup_lat, pickup_lng FROM rides WHERE booked_at >= %s", (since,))
        return sum(1 for r in rows if Location(r["pickup_lat"], r["pickup_lng"]).distance_km(pickup) <= radius_km)

    def start(self, ride_id: str, picked_up_at: datetime) -> bool:
        return self._exec("UPDATE rides SET status = 'ongoing', picked_up_at = %s WHERE id = %s AND status = 'booked'",
                          (picked_up_at, ride_id)) == 1

    def append_route_point(self, driver_id: str, location: Location) -> None:
        self._exec("UPDATE rides SET route = route || %s WHERE driver_id = %s AND status = 'ongoing'",
                   (Jsonb([[location.lat, location.lng]]), driver_id))

    def close(self, ride: Ride, expected_status: RideStatus, expected_route_len: int) -> bool:
        return self._exec(
            "UPDATE rides SET status = %s, route = %s, ended_at = %s, distance_km = %s, fare = %s, "
            "cancellation_fee = %s WHERE id = %s AND status = %s AND jsonb_array_length(route) = %s",
            (ride.status.value, _route_json(ride.route), ride.ended_at, ride.distance_km,
             Jsonb(asdict(ride.fare)) if ride.fare else None, ride.cancellation_fee, ride.id,
             expected_status.value, expected_route_len)) == 1


class PostgresCouponRepository(_Base, CouponRepository):
    def add(self, coupon: Coupon) -> Coupon:
        try:
            self._exec("INSERT INTO coupons (code, kind, params) VALUES (%s, %s, %s)",
                       (coupon.code, coupon.discount.kind, Jsonb(coupon.discount.params())))
        except UniqueViolation:
            raise ValidationError(f"coupon '{coupon.code}' already exists")
        return coupon

    def find(self, code: str) -> Optional[Coupon]:
        row = self._one("SELECT * FROM coupons WHERE code = %s", (code,))
        if row is None:
            return None
        return _coupon(row["code"], row["kind"], row["params"])

    def delete(self, code: str) -> None:
        if self._exec("DELETE FROM coupons WHERE code = %s", (code,)) == 0:
            raise NotFoundError(f"coupon '{code}' not found")


def open_pool(url: str, max_size: int = 10) -> ConnectionPool:
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=True,
                          kwargs={"autocommit": True, "row_factory": dict_row})
    pool.wait(timeout=5)
    return pool


def apply_schema(pool: ConnectionPool, reset: bool = False) -> None:
    """Create missing tables. There are no migrations: `reset` drops everything first."""
    with pool.connection() as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS rides, drivers, users, coupons")
        conn.execute(SCHEMA.read_text())


def postgres_repositories(pool: ConnectionPool) -> Repositories:
    return Repositories(PostgresUserRepository(pool), PostgresDriverRepository(pool),
                        PostgresRideRepository(pool), PostgresCouponRepository(pool))
