from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable

from app.domain.models import Location
from app.storage.base import DriverRepository, RideRepository


class SurgeStrategy(ABC):
    @abstractmethod
    def multiplier(self, pickup: Location, user_id: str) -> float:
        """Surge for `user_id` booking at `pickup`, computed at booking time and locked onto the ride."""


class NoSurge(SurgeStrategy):
    def multiplier(self, pickup: Location, user_id: str) -> float:
        return 1.0


class DemandSupplySurge(SurgeStrategy):
    """demand = this rider plus the other riders who booked within `area_radius_km` of the pickup
    in the last `window` (distinct riders, so cancelling and rebooking can't raise your own price);
    supply = drivers available in the same area (any car type) and seen within `driver_timeout`.

    The multiplier is demand / supply within [1, cap]. Supply is counted as at least 1, so a rider
    with no competition is never surged, even when the nearest driver is outside the area."""

    def __init__(self, drivers: DriverRepository, rides: RideRepository, area_radius_km: float,
                 window: timedelta, cap: float, driver_timeout: timedelta, clock: Callable[[], datetime]):
        self.drivers = drivers
        self.rides = rides
        self.area_radius_km = area_radius_km
        self.window = window
        self.cap = cap
        self.driver_timeout = driver_timeout
        self.clock = clock

    def multiplier(self, pickup: Location, user_id: str) -> float:
        now = self.clock()
        supply = len(self.drivers.find_available(pickup, self.area_radius_km, now - self.driver_timeout))
        demand = 1 + self.rides.count_riders_near(pickup, self.area_radius_km, now - self.window, user_id)
        return round(min(max(demand / max(supply, 1), 1.0), self.cap), 2)
