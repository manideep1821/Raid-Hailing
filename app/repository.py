"""Storage contracts plus an in-memory implementation.

State transitions that must be race-free are repository operations, so each backend enforces
them atomically in its own way: a driver is claimed with a compare-and-set, and a ride is
changed by a locked read-modify-write (`modify`). The in-memory store hands out copies so it
behaves like a real database.
"""
import copy
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional

from app.exceptions import InvalidRideStateError, NotFoundError, ValidationError
from app.models import CarType, Coupon, Driver, DriverStatus, Location, Ride, RideStatus, User


class UserRepository(ABC):
    @abstractmethod
    def add(self, user: User) -> User: ...

    @abstractmethod
    def get(self, user_id: str) -> User: ...


class DriverRepository(ABC):
    @abstractmethod
    def add(self, driver: Driver) -> Driver: ...

    @abstractmethod
    def get(self, driver_id: str) -> Driver: ...

    @abstractmethod
    def find_available(self, pickup: Location, radius_km: float, seen_since: datetime,
                       car_type: Optional[CarType] = None) -> List[Driver]:
        """Available drivers within the radius that reported a location at or after `seen_since`;
        all car types when `car_type` is None."""

    @abstractmethod
    def update_location(self, driver_id: str, location: Location, seen_at: datetime) -> Driver: ...

    @abstractmethod
    def try_claim(self, driver_id: str) -> bool:
        """Atomically AVAILABLE -> ON_RIDE. False if someone else got there first."""

    @abstractmethod
    def release(self, driver_id: str) -> None:
        """Mark AVAILABLE."""


RideChange = Callable[[Ride], None]


class RideRepository(ABC):
    @abstractmethod
    def create(self, ride: Ride) -> Ride:
        """Insert a booked ride; InvalidRideStateError if the user already has an active one."""

    @abstractmethod
    def get(self, ride_id: str) -> Ride: ...

    @abstractmethod
    def for_user(self, user_id: str) -> List[Ride]: ...

    @abstractmethod
    def for_driver(self, driver_id: str) -> List[Ride]: ...

    @abstractmethod
    def count_booked_near(self, pickup: Location, radius_km: float, since: datetime) -> int:
        """Rides booked at or after `since` whose pickup is within the radius."""

    @abstractmethod
    def modify(self, ride_id: str, change: RideChange) -> Ride:
        """Lock the ride, apply `change` to it and save it, all or nothing: no other change to the
        ride can interleave, and if `change` raises nothing is saved. Returns the saved ride."""

    @abstractmethod
    def modify_ongoing_for_driver(self, driver_id: str, change: RideChange) -> Optional[Ride]:
        """`modify` the driver's ONGOING ride (rider on board); None if there isn't one."""


class CouponRepository(ABC):
    @abstractmethod
    def add(self, coupon: Coupon) -> Coupon:
        """ValidationError if the code already exists."""

    @abstractmethod
    def find(self, code: str) -> Optional[Coupon]: ...

    @abstractmethod
    def delete(self, code: str) -> None: ...


@dataclass
class Repositories:
    users: UserRepository
    drivers: DriverRepository
    rides: RideRepository
    coupons: CouponRepository


def _get(items: Dict, key: str, name: str):
    if key not in items:
        raise NotFoundError(f"{name} '{key}' not found")
    return copy.deepcopy(items[key])


class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self._items: Dict[str, User] = {}

    def add(self, user: User) -> User:
        self._items[user.id] = copy.deepcopy(user)
        return user

    def get(self, user_id: str) -> User:
        return _get(self._items, user_id, "user")


class InMemoryDriverRepository(DriverRepository):
    def __init__(self):
        self._items: Dict[str, Driver] = {}
        self._lock = threading.Lock()

    def add(self, driver: Driver) -> Driver:
        self._items[driver.id] = copy.deepcopy(driver)
        return driver

    def get(self, driver_id: str) -> Driver:
        return _get(self._items, driver_id, "driver")

    def find_available(self, pickup: Location, radius_km: float, seen_since: datetime,
                       car_type: Optional[CarType] = None) -> List[Driver]:
        return [
            copy.deepcopy(d) for d in list(self._items.values())
            if d.status == DriverStatus.AVAILABLE
            and d.last_seen_at >= seen_since
            and car_type in (None, d.car_type)
            and d.location.distance_km(pickup) <= radius_km
        ]

    def update_location(self, driver_id: str, location: Location, seen_at: datetime) -> Driver:
        with self._lock:
            _get(self._items, driver_id, "driver")
            self._items[driver_id].location = location
            self._items[driver_id].last_seen_at = seen_at
            return copy.deepcopy(self._items[driver_id])

    def try_claim(self, driver_id: str) -> bool:
        with self._lock:
            driver = self._items.get(driver_id)
            if driver is None or driver.status != DriverStatus.AVAILABLE:
                return False
            driver.status = DriverStatus.ON_RIDE
            return True

    def release(self, driver_id: str) -> None:
        with self._lock:
            self._items[driver_id].status = DriverStatus.AVAILABLE


class InMemoryRideRepository(RideRepository):
    def __init__(self):
        self._items: Dict[str, Ride] = {}
        self._lock = threading.Lock()

    def create(self, ride: Ride) -> Ride:
        with self._lock:
            if any(r.user_id == ride.user_id and r.is_active for r in self._items.values()):
                raise InvalidRideStateError("user already has an active ride")
            self._items[ride.id] = copy.deepcopy(ride)
            return ride

    def get(self, ride_id: str) -> Ride:
        return _get(self._items, ride_id, "ride")

    def for_user(self, user_id: str) -> List[Ride]:
        return [copy.deepcopy(r) for r in list(self._items.values()) if r.user_id == user_id]

    def for_driver(self, driver_id: str) -> List[Ride]:
        return [copy.deepcopy(r) for r in list(self._items.values()) if r.driver_id == driver_id]

    def count_booked_near(self, pickup: Location, radius_km: float, since: datetime) -> int:
        return sum(1 for r in list(self._items.values())
                   if r.booked_at >= since and r.pickup.distance_km(pickup) <= radius_km)

    def modify(self, ride_id: str, change: RideChange) -> Ride:
        with self._lock:
            return self._apply(_get(self._items, ride_id, "ride"), change)

    def modify_ongoing_for_driver(self, driver_id: str, change: RideChange) -> Optional[Ride]:
        with self._lock:
            for r in self._items.values():
                if r.driver_id == driver_id and r.status == RideStatus.ONGOING:
                    return self._apply(copy.deepcopy(r), change)
            return None

    def _apply(self, ride: Ride, change: RideChange) -> Ride:
        """`ride` is a copy, so if `change` raises, storage is untouched. Caller holds the lock."""
        change(ride)
        self._items[ride.id] = copy.deepcopy(ride)
        return ride


class InMemoryCouponRepository(CouponRepository):
    def __init__(self):
        self._items: Dict[str, Coupon] = {}
        self._lock = threading.Lock()

    def add(self, coupon: Coupon) -> Coupon:
        with self._lock:
            if coupon.code in self._items:
                raise ValidationError(f"coupon '{coupon.code}' already exists")
            self._items[coupon.code] = copy.deepcopy(coupon)
            return coupon

    def find(self, code: str) -> Optional[Coupon]:
        return copy.deepcopy(self._items.get(code))

    def delete(self, code: str) -> None:
        with self._lock:
            if self._items.pop(code, None) is None:
                raise NotFoundError(f"coupon '{code}' not found")


def in_memory_repositories() -> Repositories:
    return Repositories(InMemoryUserRepository(), InMemoryDriverRepository(),
                        InMemoryRideRepository(), InMemoryCouponRepository())
