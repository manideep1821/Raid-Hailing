import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from app.discounts import Discount
from app.exceptions import ValidationError

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float

    def __post_init__(self):
        if not (-90 <= self.lat <= 90 and -180 <= self.lng <= 180):
            raise ValidationError(f"invalid coordinates ({self.lat}, {self.lng}): "
                                  "latitude must be within ±90 and longitude within ±180")

    def distance_km(self, other: "Location") -> float:
        """Great-circle (haversine) distance."""
        lat1, lng1, lat2, lng2 = map(math.radians, (self.lat, self.lng, other.lat, other.lng))
        a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
        return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class CarType(str, Enum):
    HATCHBACK = "hatchback"
    SEDAN = "sedan"


class DriverStatus(str, Enum):
    AVAILABLE = "available"
    ON_RIDE = "on_ride"


class RideStatus(str, Enum):
    BOOKED = "booked"        # driver assigned, on the way to the pickup
    ONGOING = "ongoing"      # rider picked up; the route (and the fare) is measured from here
    COMPLETED = "completed"
    CANCELLED = "cancelled"


ACTIVE_RIDE_STATUSES = (RideStatus.BOOKED, RideStatus.ONGOING)


@dataclass
class User:
    id: str
    name: str
    phone: str


@dataclass
class Driver:
    id: str
    name: str
    phone: str
    car_type: CarType
    location: Location
    rating: float = 5.0
    status: DriverStatus = DriverStatus.AVAILABLE
    last_seen_at: datetime = field(default_factory=datetime.now)  # last location update from the driver


@dataclass
class Coupon:
    code: str
    discount: Discount


@dataclass(frozen=True)
class FareBreakdown:
    base_fare: float         # tiered fare for the billed car type, minimum fare applied
    surge_multiplier: float
    surged_fare: float
    discount: float
    total: float


@dataclass
class Ride:
    id: str
    user_id: str
    driver_id: str
    requested_car_type: CarType
    assigned_car_type: CarType
    pickup: Location
    last_location: Location          # where the running distance was last measured to
    coupon: Optional[Coupon] = None  # snapshot validated at booking
    surge_multiplier: float = 1.0    # locked at booking
    status: RideStatus = RideStatus.BOOKED
    booked_at: datetime = field(default_factory=datetime.now)
    picked_up_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    distance_km: float = 0.0         # accumulated while ONGOING
    fare: Optional[FareBreakdown] = None
    cancellation_fee: Optional[float] = None

    @property
    def upgraded(self) -> bool:
        return self.requested_car_type != self.assigned_car_type

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_RIDE_STATUSES

    def move_to(self, location: Location) -> None:
        self.distance_km += self.last_location.distance_km(location)
        self.last_location = location
