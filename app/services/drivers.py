from datetime import datetime
from typing import Callable

from app.domain.models import CarType, Driver, Location
from app.services.common import new_id, require
from app.storage.base import DriverRepository, RideRepository


class DriverService:
    def __init__(self, drivers: DriverRepository, rides: RideRepository,
                 clock: Callable[[], datetime] = datetime.now):
        self.drivers = drivers
        self.rides = rides
        self.clock = clock

    def register(self, name: str, phone: str, car_type: CarType, location: Location,
                 rating: float = 5.0) -> Driver:
        require(bool(name.strip()) and bool(phone.strip()), "name and phone are required")
        require(0 <= rating <= 5, "rating must be between 0 and 5")
        return self.drivers.add(Driver(new_id("D"), name.strip(), phone.strip(), car_type, location, rating,
                                       last_seen_at=self.clock()))

    def get(self, driver_id: str) -> Driver:
        return self.drivers.get(driver_id)

    def update_location(self, driver_id: str, location: Location) -> Driver:
        """Also a heartbeat, and, with a rider on board, adds the leg to the ride's distance."""
        driver = self.drivers.update_location(driver_id, location, self.clock())
        self.rides.modify_ongoing_for_driver(driver_id, lambda ride: ride.move_to(location))
        return driver
