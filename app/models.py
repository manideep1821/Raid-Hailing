import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float

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
    ONGOING = "ongoing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DiscountType(str, Enum):
    FLAT = "flat"
    PERCENTAGE = "percentage"


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


@dataclass
class Coupon:
    code: str
    discount_type: DiscountType
    value: float
    max_discount: Optional[float] = None


@dataclass
class Ride:
    id: str
    user_id: str
    driver_id: str
    requested_car_type: CarType
    assigned_car_type: CarType
    pickup: Location
    route: List[Location]
    coupon: Optional[Coupon] = None  # snapshot validated at booking
    status: RideStatus = RideStatus.ONGOING
    started_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    distance_km: Optional[float] = None
    fare: Optional[float] = None
    cancellation_fee: Optional[float] = None

    @property
    def upgraded(self) -> bool:
        return self.requested_car_type != self.assigned_car_type

    def route_distance_km(self) -> float:
        return sum(a.distance_km(b) for a, b in zip(self.route, self.route[1:]))
