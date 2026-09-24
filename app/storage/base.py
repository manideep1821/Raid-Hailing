"""Storage contracts. Services depend only on these; `memory.py` and `postgres.py` implement them.

State transitions that must be race-free are repository operations, so each backend enforces
them atomically in its own way. A driver's status follows their ride: creating a ride claims
the driver and closing it releases them, in the same transaction, so a crash can never leave a
driver on a ride that doesn't exist.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from app.domain.models import CarType, Coupon, Driver, Location, Ride, User


class UserRepository(ABC):
    @abstractmethod
    def add(self, user: User) -> User:
        """ValidationError if the phone number is already registered."""

    @abstractmethod
    def get(self, user_id: str) -> User: ...


RideChange = Callable[[Ride], None]


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
    def update_location(self, driver_id: str, location: Location, seen_at: datetime,
                        ride_change: Optional[RideChange] = None) -> Driver:
        """Move the driver and record them as seen. In the same transaction, apply `ride_change`
        to their ONGOING ride (rider on board), if there is one: both happen, or neither."""


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
