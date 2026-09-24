import threading
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from app.cancellation import CancellationPolicy, GracePeriodCancellationPolicy
from app.exceptions import (InvalidCouponError, InvalidRideStateError, NoDriverAvailableError,
                            ValidationError)
from app.matching import MatchingStrategy, NearestDriverStrategy
from app.models import (CarType, Coupon, DiscountType, Driver, DriverStatus, Location, Ride, RideStatus,
                        User)
from app.pricing import PricingEngine
from app.repository import CouponRepository, DriverRepository, RideRepository, UserRepository

DEFAULT_SEARCH_RADIUS_KM = 5.0

# Cheaper type -> type we may substitute at the requested type's price.
UPGRADE_PATH: Dict[CarType, CarType] = {CarType.HATCHBACK: CarType.SEDAN}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    def register(self, name: str, phone: str) -> User:
        _require(bool(name.strip()) and bool(phone.strip()), "name and phone are required")
        user = User(_new_id("U"), name.strip(), phone.strip())
        return self.users.save(user.id, user)


class DriverService:
    def __init__(self, drivers: DriverRepository, rides: RideRepository):
        self.drivers = drivers
        self.rides = rides

    def register(self, name: str, phone: str, car_type: CarType, location: Location,
                 rating: float = 5.0) -> Driver:
        _require(bool(name.strip()) and bool(phone.strip()), "name and phone are required")
        _require(0 <= rating <= 5, "rating must be between 0 and 5")
        driver = Driver(_new_id("D"), name.strip(), phone.strip(), car_type, location, rating)
        return self.drivers.save(driver.id, driver)

    def update_location(self, driver_id: str, location: Location) -> Driver:
        driver = self.drivers.get(driver_id)
        driver.location = location
        ride = self.rides.ongoing_for_driver(driver_id)
        if ride:
            ride.route.append(location)
        return driver


class CouponService:
    def __init__(self, coupons: CouponRepository):
        self.coupons = coupons

    def add(self, code: str, discount_type: DiscountType, value: float,
            max_discount: Optional[float] = None) -> Coupon:
        code = code.strip().upper()
        _require(bool(code), "coupon code is required")
        _require(self.coupons.find(code) is None, f"coupon '{code}' already exists")
        _require(value > 0, "discount value must be positive")
        _require(discount_type != DiscountType.PERCENTAGE or value <= 100, "percentage must be <= 100")
        _require(max_discount is None or max_discount > 0, "max_discount must be positive")
        return self.coupons.save(code, Coupon(code, discount_type, value, max_discount))

    def delete(self, code: str) -> None:
        self.coupons.delete(code.strip().upper())

    def validate(self, code: str) -> Coupon:
        coupon = self.coupons.find(code.strip().upper())
        if coupon is None:
            raise InvalidCouponError(f"coupon '{code}' is invalid or expired")
        return coupon


class RideService:
    def __init__(self, users: UserRepository, drivers: DriverRepository, rides: RideRepository,
                 coupon_service: CouponService, pricing: PricingEngine,
                 matching: Optional[MatchingStrategy] = None,
                 cancellation: Optional[CancellationPolicy] = None,
                 clock: Callable[[], datetime] = datetime.now):
        self.users = users
        self.drivers = drivers
        self.rides = rides
        self.coupon_service = coupon_service
        self.pricing = pricing
        self.matching = matching or NearestDriverStrategy()
        self.cancellation = cancellation or GracePeriodCancellationPolicy()
        self.clock = clock
        # Guards driver status transitions so one driver can't be handed to two bookings.
        self._lock = threading.Lock()

    def book(self, user_id: str, pickup: Location, car_type: CarType,
             radius_km: float = DEFAULT_SEARCH_RADIUS_KM, coupon_code: Optional[str] = None,
             matching: Optional[MatchingStrategy] = None) -> Ride:
        self.users.get(user_id)
        _require(radius_km > 0, "radius must be positive")
        coupon = self.coupon_service.validate(coupon_code) if coupon_code else None
        strategy = matching or self.matching

        with self._lock:
            if any(r.status == RideStatus.ONGOING for r in self.rides.for_user(user_id)):
                raise InvalidRideStateError("user already has an ongoing ride")
            driver, assigned_type = self._find_driver(pickup, car_type, radius_km, strategy)
            driver.status = DriverStatus.ON_RIDE
            ride = Ride(
                id=_new_id("R"), user_id=user_id, driver_id=driver.id,
                requested_car_type=car_type, assigned_car_type=assigned_type,
                pickup=pickup, route=[pickup], coupon=coupon, started_at=self.clock(),
            )
            return self.rides.save(ride.id, ride)

    def _find_driver(self, pickup: Location, car_type: CarType, radius_km: float,
                     strategy: MatchingStrategy):
        for candidate_type in (car_type, UPGRADE_PATH.get(car_type)):
            if candidate_type is None:
                continue
            ranked = strategy.rank(self.drivers.find_available(pickup, radius_km, candidate_type), pickup)
            if ranked:
                return ranked[0], candidate_type
        raise NoDriverAvailableError(f"no {car_type.value} available within {radius_km} km")

    def end(self, ride_id: str, drop: Optional[Location] = None) -> Ride:
        with self._lock:
            ride = self._ongoing(ride_id)
            if drop:
                ride.route.append(drop)
            ride.distance_km = round(ride.route_distance_km(), 3)
            # Priced on the *requested* type: an upgrade is free for the rider.
            ride.fare = self.pricing.calculate(ride.requested_car_type, ride.distance_km, ride.pickup, ride.coupon)
            ride.status = RideStatus.COMPLETED
            ride.ended_at = self.clock()
            self._release_driver(ride, at=ride.route[-1])
            return ride

    def cancel(self, ride_id: str) -> Ride:
        with self._lock:
            ride = self._ongoing(ride_id)
            now = self.clock()
            ride.cancellation_fee = self.cancellation.fee(ride, now)
            ride.status = RideStatus.CANCELLED
            ride.ended_at = now
            self._release_driver(ride, at=ride.route[-1])
            return ride

    def history_for_user(self, user_id: str) -> Dict[str, List[Ride]]:
        self.users.get(user_id)
        return self._partition(self.rides.for_user(user_id))

    def history_for_driver(self, driver_id: str) -> Dict[str, List[Ride]]:
        self.drivers.get(driver_id)
        return self._partition(self.rides.for_driver(driver_id))

    def _ongoing(self, ride_id: str) -> Ride:
        ride = self.rides.get(ride_id)
        if ride.status != RideStatus.ONGOING:
            raise InvalidRideStateError(f"ride '{ride_id}' is already {ride.status.value}")
        return ride

    def _release_driver(self, ride: Ride, at: Location) -> None:
        driver = self.drivers.get(ride.driver_id)
        driver.status = DriverStatus.AVAILABLE
        driver.location = at

    @staticmethod
    def _partition(rides: List[Ride]) -> Dict[str, List[Ride]]:
        by_start = sorted(rides, key=lambda r: r.started_at, reverse=True)
        return {
            "ongoing": [r for r in by_start if r.status == RideStatus.ONGOING],
            "completed": [r for r in by_start if r.status == RideStatus.COMPLETED],
            "cancelled": [r for r in by_start if r.status == RideStatus.CANCELLED],
        }
