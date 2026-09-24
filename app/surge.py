from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable

from app.models import Location
from app.repository import DriverRepository, RideRepository


class SurgeStrategy(ABC):
    @abstractmethod
    def multiplier(self, pickup: Location) -> float:
        """Surge for a booking at `pickup`, computed at booking time and locked onto the ride."""


class NoSurge(SurgeStrategy):
    def multiplier(self, pickup: Location) -> float:
        return 1.0


class DemandSupplySurge(SurgeStrategy):
    """demand = rides booked within `area_radius_km` of the pickup in the last `window`, plus this request;
    supply = drivers available in the same area (any car type) and seen within `driver_timeout`.
    The multiplier is demand / supply, kept within [1, cap]. With no supply at all it is the cap."""

    def __init__(self, drivers: DriverRepository, rides: RideRepository, area_radius_km: float,
                 window: timedelta, cap: float, driver_timeout: timedelta, clock: Callable[[], datetime]):
        self.drivers = drivers
        self.rides = rides
        self.area_radius_km = area_radius_km
        self.window = window
        self.cap = cap
        self.driver_timeout = driver_timeout
        self.clock = clock

    def multiplier(self, pickup: Location) -> float:
        now = self.clock()
        supply = len(self.drivers.find_available(pickup, self.area_radius_km, now - self.driver_timeout))
        if supply == 0:
            return self.cap
        demand = 1 + self.rides.count_booked_near(pickup, self.area_radius_km, since=now - self.window)
        return round(min(max(demand / supply, 1.0), self.cap), 2)
