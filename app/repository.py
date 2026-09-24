"""Storage contracts plus an in-memory implementation.

State transitions that must be race-free are repository operations, so each backend enforces
them atomically in its own way. A driver's status follows their ride: creating a ride claims
the driver and closing it releases them, in the same transaction, so a crash can never leave a
driver on a ride that doesn't exist. The in-memory store hands out copies and shares one lock
between drivers and rides, so it behaves like a real database.
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
    def add(self, user: User) -> User:
        """ValidationError if the phone number is already registered."""

    @abstractmethod
    def get(self, user_id: str) -> User: ...


class DriverRepository(ABC):
    @abstractmethod
    def add(self, driver: Driver) -> Driver:
        """ValidationError if the phone number is already registered."""

    @abstractmethod
    def get(self, driver_id: str) -> Driver: ...

    @abstractmethod
    def find_available(self, pickup: Location, radius_km: float, seen_since: datetime,
                       car_type: Optional[CarType] = None) -> List[Driver]:
        """Available drivers within the radius that reported a location at or after `seen_since`;
        all car types when `car_type` is None."""

    @abstractmethod
    def update_location(self, driver_id: str, location: Location, seen_at: datetime) -> Driver: ...


RideChange = Callable[[Ride], None]


class RideRepository(ABC):
    @abstractmethod
    def create(self, ride: Ride) -> bool:
        """Atomically claim the ride's driver (AVAILABLE -> ON_RIDE) and insert the booked ride.
        False if the driver was no longer available. InvalidRideStateError, with nothing written,
        if the user already has an active ride."""

    @abstractmethod
    def get(self, ride_id: str) -> Ride: ...

    @abstractmethod
    def for_user(self, user_id: str) -> List[Ride]: ...

    @abstractmethod
    def for_driver(self, driver_id: str) -> List[Ride]: ...

    @abstractmethod
    def count_riders_near(self, pickup: Location, radius_km: float, since: datetime, excluding_user_id: str) -> int:
        """Distinct users, other than `excluding_user_id`, who booked a ride at or after `since`
        with a pickup within the radius."""

    @abstractmethod
    def modify(self, ride_id: str, change: RideChange) -> Ride:
        """Lock the ride, apply `change` to it and save it, all or nothing: no other change to the
        ride can interleave, and if `change` raises nothing is saved. Returns the saved ride.

        If the change closes the ride, the same transaction releases the driver; a completed ride
        also moves the driver to where it ended, unless the driver has since reported a newer
        location."""

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


def _check_phone_free(items: Dict, phone: str) -> None:
    if any(item.phone == phone for item in items.values()):
        raise ValidationError(f"phone {phone} is already registered")


class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self._items: Dict[str, User] = {}
        self._lock = threading.Lock()

    def add(self, user: User) -> User:
        with self._lock:
            _check_phone_free(self._items, user.phone)
            self._items[user.id] = copy.deepcopy(user)
            return user

    def get(self, user_id: str) -> User:
        return _get(self._items, user_id, "user")


class InMemoryDriverRepository(DriverRepository):
    def __init__(self):
        self._items: Dict[str, Driver] = {}
        self._lock = threading.Lock()

    def add(self, driver: Driver) -> Driver:
        with self._lock:
            _check_phone_free(self._items, driver.phone)
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


class InMemoryRideRepository(RideRepository):
    def __init__(self, drivers: InMemoryDriverRepository):
        self._items: Dict[str, Ride] = {}
        # Rides change their driver's status, so both stores share one lock: the in-memory
        # equivalent of a transaction spanning the two tables.
        self._drivers = drivers._items
        self._lock = drivers._lock

    def create(self, ride: Ride) -> bool:
        with self._lock:
            if any(r.user_id == ride.user_id and r.is_active for r in self._items.values()):
                raise InvalidRideStateError("user already has an active ride")
            driver = self._drivers[ride.driver_id]
            if driver.status != DriverStatus.AVAILABLE:
                return False
            driver.status = DriverStatus.ON_RIDE
            self._items[ride.id] = copy.deepcopy(ride)
            return True

    def get(self, ride_id: str) -> Ride:
        return _get(self._items, ride_id, "ride")

    def for_user(self, user_id: str) -> List[Ride]:
        return [copy.deepcopy(r) for r in list(self._items.values()) if r.user_id == user_id]

    def for_driver(self, driver_id: str) -> List[Ride]:
        return [copy.deepcopy(r) for r in list(self._items.values()) if r.driver_id == driver_id]

    def count_riders_near(self, pickup: Location, radius_km: float, since: datetime, excluding_user_id: str) -> int:
        return len({r.user_id for r in list(self._items.values())
                    if r.booked_at >= since and r.user_id != excluding_user_id
                    and r.pickup.distance_km(pickup) <= radius_km})

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
        was_active = ride.is_active
        change(ride)
        if was_active and not ride.is_active:
            driver = self._drivers[ride.driver_id]
            driver.status = DriverStatus.AVAILABLE
            if ride.status == RideStatus.COMPLETED and driver.last_seen_at <= ride.ended_at:
                driver.location = ride.last_location
                driver.last_seen_at = ride.ended_at
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
    drivers = InMemoryDriverRepository()
    return Repositories(InMemoryUserRepository(), drivers, InMemoryRideRepository(drivers),
                        InMemoryCouponRepository())
