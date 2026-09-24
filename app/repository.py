"""Storage contracts plus an in-memory implementation.

State transitions that must be race-free (claiming a driver, opening/closing a ride) are
repository operations, so each backend enforces them atomically in its own way.
The in-memory store hands out copies so it behaves like a real database.
"""
import copy
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

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
    def find_available(self, pickup: Location, radius_km: float, car_type: CarType) -> List[Driver]: ...

    @abstractmethod
    def update_location(self, driver_id: str, location: Location) -> Driver: ...

    @abstractmethod
    def try_claim(self, driver_id: str) -> bool:
        """Atomically AVAILABLE -> ON_RIDE. False if someone else got there first."""

    @abstractmethod
    def release(self, driver_id: str, location: Location) -> None: ...


class RideRepository(ABC):
    @abstractmethod
    def create(self, ride: Ride) -> Ride:
        """Insert an ongoing ride; InvalidRideStateError if the user already has one."""

    @abstractmethod
    def get(self, ride_id: str) -> Ride: ...

    @abstractmethod
    def for_user(self, user_id: str) -> List[Ride]: ...

    @abstractmethod
    def for_driver(self, driver_id: str) -> List[Ride]: ...

    @abstractmethod
    def append_route_point(self, driver_id: str, location: Location) -> None:
        """Extend the route of the driver's ongoing ride, if any."""

    @abstractmethod
    def close(self, ride: Ride) -> bool:
        """Persist a completed/cancelled ride only if it is still ongoing in storage."""


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

    def find_available(self, pickup: Location, radius_km: float, car_type: CarType) -> List[Driver]:
        return [
            copy.deepcopy(d) for d in list(self._items.values())
            if d.status == DriverStatus.AVAILABLE
            and d.car_type == car_type
            and d.location.distance_km(pickup) <= radius_km
        ]

    def update_location(self, driver_id: str, location: Location) -> Driver:
        with self._lock:
            _get(self._items, driver_id, "driver")
            self._items[driver_id].location = location
            return copy.deepcopy(self._items[driver_id])

    def try_claim(self, driver_id: str) -> bool:
        with self._lock:
            driver = self._items.get(driver_id)
            if driver is None or driver.status != DriverStatus.AVAILABLE:
                return False
            driver.status = DriverStatus.ON_RIDE
            return True

    def release(self, driver_id: str, location: Location) -> None:
        with self._lock:
            driver = self._items[driver_id]
            driver.status = DriverStatus.AVAILABLE
            driver.location = location


class InMemoryRideRepository(RideRepository):
    def __init__(self):
        self._items: Dict[str, Ride] = {}
        self._lock = threading.Lock()

    def create(self, ride: Ride) -> Ride:
        with self._lock:
            if any(r.user_id == ride.user_id and r.status == RideStatus.ONGOING for r in self._items.values()):
                raise InvalidRideStateError("user already has an ongoing ride")
            self._items[ride.id] = copy.deepcopy(ride)
            return ride

    def get(self, ride_id: str) -> Ride:
        return _get(self._items, ride_id, "ride")

    def for_user(self, user_id: str) -> List[Ride]:
        return [copy.deepcopy(r) for r in list(self._items.values()) if r.user_id == user_id]

    def for_driver(self, driver_id: str) -> List[Ride]:
        return [copy.deepcopy(r) for r in list(self._items.values()) if r.driver_id == driver_id]

    def append_route_point(self, driver_id: str, location: Location) -> None:
        with self._lock:
            for r in self._items.values():
                if r.driver_id == driver_id and r.status == RideStatus.ONGOING:
                    r.route.append(location)

    def close(self, ride: Ride) -> bool:
        with self._lock:
            if self._items[ride.id].status != RideStatus.ONGOING:
                return False
            self._items[ride.id] = copy.deepcopy(ride)
            return True


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
