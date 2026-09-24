"""In-memory implementation of the storage contracts, used by the tests and `rides shell --memory`."""
import copy
import threading
from datetime import datetime
from typing import Dict, List, Optional

from app.domain.exceptions import InvalidRideStateError, NotFoundError, ValidationError
from app.domain.models import CarType, Coupon, Driver, DriverStatus, Location, Ride, RideStatus, User
from app.storage.base import (CouponRepository, DriverRepository, Repositories, RideChange, RideRepository,
                              UserRepository)


class InMemoryDatabase:
    """Every table behind one lock: the in-memory stand-in for a database, so an operation that
    touches rides and drivers is atomic the way one transaction is in Postgres. Repositories hand
    out copies, so nothing changes unless a repository writes it back."""

    def __init__(self):
        self.lock = threading.Lock()
        self.users: Dict[str, User] = {}
        self.drivers: Dict[str, Driver] = {}
        self.rides: Dict[str, Ride] = {}
        self.coupons: Dict[str, Coupon] = {}


def _get(items: Dict, key: str, name: str):
    """A copy of the item. Caller holds the lock."""
    if key not in items:
        raise NotFoundError(f"{name} '{key}' not found")
    return copy.deepcopy(items[key])


def _check_phone_free(items: Dict, phone: str) -> None:
    if any(item.phone == phone for item in items.values()):
        raise ValidationError(f"phone {phone} is already registered")


class InMemoryUserRepository(UserRepository):
    def __init__(self, db: InMemoryDatabase):
        self.db = db

    def add(self, user: User) -> User:
        with self.db.lock:
            _check_phone_free(self.db.users, user.phone)
            self.db.users[user.id] = copy.deepcopy(user)
            return user

    def get(self, user_id: str) -> User:
        with self.db.lock:
            return _get(self.db.users, user_id, "user")


class InMemoryDriverRepository(DriverRepository):
    def __init__(self, db: InMemoryDatabase):
        self.db = db

    def add(self, driver: Driver) -> Driver:
        with self.db.lock:
            _check_phone_free(self.db.drivers, driver.phone)
            self.db.drivers[driver.id] = copy.deepcopy(driver)
            return driver

    def get(self, driver_id: str) -> Driver:
        with self.db.lock:
            return _get(self.db.drivers, driver_id, "driver")

    def find_available(self, pickup: Location, radius_km: float, seen_since: datetime,
                       car_type: Optional[CarType] = None) -> List[Driver]:
        with self.db.lock:
            return [
                copy.deepcopy(d) for d in self.db.drivers.values()
                if d.status == DriverStatus.AVAILABLE
                and d.last_seen_at >= seen_since
                and car_type in (None, d.car_type)
                and d.location.distance_km(pickup) <= radius_km
            ]

    def update_location(self, driver_id: str, location: Location, seen_at: datetime) -> Driver:
        with self.db.lock:
            _get(self.db.drivers, driver_id, "driver")
            driver = self.db.drivers[driver_id]
            driver.location = location
            driver.last_seen_at = seen_at
            return copy.deepcopy(driver)


class InMemoryRideRepository(RideRepository):
    def __init__(self, db: InMemoryDatabase):
        self.db = db

    def create(self, ride: Ride) -> bool:
        with self.db.lock:
            if any(r.user_id == ride.user_id and r.is_active for r in self.db.rides.values()):
                raise InvalidRideStateError("user already has an active ride")
            driver = self.db.drivers[ride.driver_id]
            if driver.status != DriverStatus.AVAILABLE:
                return False
            driver.status = DriverStatus.ON_RIDE
            self.db.rides[ride.id] = copy.deepcopy(ride)
            return True

    def get(self, ride_id: str) -> Ride:
        with self.db.lock:
            return _get(self.db.rides, ride_id, "ride")

    def for_user(self, user_id: str) -> List[Ride]:
        with self.db.lock:
            return [copy.deepcopy(r) for r in self.db.rides.values() if r.user_id == user_id]

    def for_driver(self, driver_id: str) -> List[Ride]:
        with self.db.lock:
            return [copy.deepcopy(r) for r in self.db.rides.values() if r.driver_id == driver_id]

    def count_riders_near(self, pickup: Location, radius_km: float, since: datetime, excluding_user_id: str) -> int:
        with self.db.lock:
            return len({r.user_id for r in self.db.rides.values()
                        if r.booked_at >= since and r.user_id != excluding_user_id
                        and r.pickup.distance_km(pickup) <= radius_km})

    def modify(self, ride_id: str, change: RideChange) -> Ride:
        with self.db.lock:
            return self._apply(_get(self.db.rides, ride_id, "ride"), change)

    def modify_ongoing_for_driver(self, driver_id: str, change: RideChange) -> Optional[Ride]:
        with self.db.lock:
            for r in self.db.rides.values():
                if r.driver_id == driver_id and r.status == RideStatus.ONGOING:
                    return self._apply(copy.deepcopy(r), change)
            return None

    def _apply(self, ride: Ride, change: RideChange) -> Ride:
        """`ride` is a copy, so if `change` raises, nothing is saved. Caller holds the lock."""
        was_active = ride.is_active
        change(ride)
        if was_active and not ride.is_active:
            driver = self.db.drivers[ride.driver_id]
            driver.status = DriverStatus.AVAILABLE
            if ride.status == RideStatus.COMPLETED and driver.last_seen_at <= ride.ended_at:
                driver.location = ride.last_location
                driver.last_seen_at = ride.ended_at
        self.db.rides[ride.id] = copy.deepcopy(ride)
        return ride


class InMemoryCouponRepository(CouponRepository):
    def __init__(self, db: InMemoryDatabase):
        self.db = db

    def add(self, coupon: Coupon) -> Coupon:
        with self.db.lock:
            if coupon.code in self.db.coupons:
                raise ValidationError(f"coupon '{coupon.code}' already exists")
            self.db.coupons[coupon.code] = copy.deepcopy(coupon)
            return coupon

    def find(self, code: str) -> Optional[Coupon]:
        with self.db.lock:
            return copy.deepcopy(self.db.coupons.get(code))

    def delete(self, code: str) -> None:
        with self.db.lock:
            if self.db.coupons.pop(code, None) is None:
                raise NotFoundError(f"coupon '{code}' not found")


def in_memory_repositories() -> Repositories:
    db = InMemoryDatabase()
    return Repositories(InMemoryUserRepository(db), InMemoryDriverRepository(db),
                        InMemoryRideRepository(db), InMemoryCouponRepository(db))
