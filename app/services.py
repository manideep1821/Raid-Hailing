import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from app.cancellation import CancellationPolicy
from app.discounts import Discount
from app.exceptions import InvalidCouponError, InvalidRideStateError, NoDriverAvailableError, ValidationError
from app.matching import MatchingStrategy
from app.models import CarType, Coupon, Driver, Location, Ride, RideStatus, User
from app.pricing import PricingEngine
from app.repository import CouponRepository, DriverRepository, RideRepository, UserRepository
from app.surge import SurgeStrategy


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def upgrade_chain(requested: CarType, upgrade_path: Dict[CarType, CarType]) -> List[CarType]:
    """The requested type, then each free upgrade in turn, e.g. hatchback -> sedan -> ..."""
    chain = [requested]
    upgrade = upgrade_path.get(requested)
    while upgrade is not None and upgrade not in chain:
        chain.append(upgrade)
        upgrade = upgrade_path.get(upgrade)
    return chain


class UserService:
    def __init__(self, users: UserRepository):
        self.users = users

    def register(self, name: str, phone: str) -> User:
        _require(bool(name.strip()) and bool(phone.strip()), "name and phone are required")
        return self.users.add(User(_new_id("U"), name.strip(), phone.strip()))


class DriverService:
    def __init__(self, drivers: DriverRepository, rides: RideRepository):
        self.drivers = drivers
        self.rides = rides

    def register(self, name: str, phone: str, car_type: CarType, location: Location,
                 rating: float = 5.0) -> Driver:
        _require(bool(name.strip()) and bool(phone.strip()), "name and phone are required")
        _require(0 <= rating <= 5, "rating must be between 0 and 5")
        return self.drivers.add(Driver(_new_id("D"), name.strip(), phone.strip(), car_type, location, rating))

    def get(self, driver_id: str) -> Driver:
        return self.drivers.get(driver_id)

    def update_location(self, driver_id: str, location: Location) -> Driver:
        driver = self.drivers.update_location(driver_id, location)
        self.rides.append_route_point(driver_id, location)
        return driver


class CouponService:
    def __init__(self, coupons: CouponRepository):
        self.coupons = coupons

    def add(self, code: str, discount: Discount) -> Coupon:
        code = code.strip().upper()
        _require(bool(code), "coupon code is required")
        return self.coupons.add(Coupon(code, discount))

    def delete(self, code: str) -> None:
        self.coupons.delete(code.strip().upper())

    def validate(self, code: str) -> Coupon:
        coupon = self.coupons.find(code.strip().upper())
        if coupon is None:
            raise InvalidCouponError(f"coupon '{code}' is invalid or expired")
        return coupon


class RideService:
    def __init__(self, users: UserRepository, drivers: DriverRepository, rides: RideRepository,
                 coupon_service: CouponService, pricing: PricingEngine, surge: SurgeStrategy,
                 matching: MatchingStrategy, cancellation: CancellationPolicy,
                 upgrade_path: Dict[CarType, CarType], default_radius_km: float,
                 clock: Callable[[], datetime] = datetime.now):
        self.users = users
        self.drivers = drivers
        self.rides = rides
        self.coupon_service = coupon_service
        self.pricing = pricing
        self.surge = surge
        self.matching = matching
        self.cancellation = cancellation
        # Requested type -> next type tried, still billed at the requested type's price.
        self.upgrade_path = upgrade_path
        self.default_radius_km = default_radius_km
        self.clock = clock

    def book(self, user_id: str, pickup: Location, car_type: CarType,
             radius_km: Optional[float] = None, coupon_code: Optional[str] = None,
             matching: Optional[MatchingStrategy] = None) -> Ride:
        self.users.get(user_id)
        radius_km = self.default_radius_km if radius_km is None else radius_km
        _require(radius_km > 0, "radius must be positive")
        coupon = self.coupon_service.validate(coupon_code) if coupon_code else None
        if any(r.is_active for r in self.rides.for_user(user_id)):
            raise InvalidRideStateError("user already has an active ride")
        strategy = matching or self.matching
        surge_multiplier = self.surge.multiplier(pickup)

        for candidate_type in upgrade_chain(car_type, self.upgrade_path):
            for driver in strategy.rank(self.drivers.find_available(pickup, radius_km, candidate_type), pickup):
                # Losing the claim means a concurrent booking took this driver; try the next one.
                if not self.drivers.try_claim(driver.id):
                    continue
                ride = Ride(
                    id=_new_id("R"), user_id=user_id, driver_id=driver.id,
                    requested_car_type=car_type, assigned_car_type=candidate_type,
                    pickup=pickup, route=[pickup], coupon=coupon, surge_multiplier=surge_multiplier,
                    booked_at=self.clock(),
                )
                try:
                    return self.rides.create(ride)
                except InvalidRideStateError:
                    self.drivers.release(driver.id)
                    raise
        raise NoDriverAvailableError(f"no {car_type.value} available within {radius_km} km")

    def start(self, ride_id: str) -> Ride:
        """The rider is picked up: from now on location updates extend the billed route."""
        ride = self._in_status(ride_id, RideStatus.BOOKED, "start")
        ride.picked_up_at = self.clock()
        if not self.rides.start(ride.id, ride.picked_up_at):
            raise InvalidRideStateError(f"ride '{ride.id}' changed while starting it")
        ride.status = RideStatus.ONGOING
        return ride

    def end(self, ride_id: str, drop: Optional[Location] = None) -> Ride:
        while True:
            ride = self._in_status(ride_id, RideStatus.ONGOING, "end")
            points_read = len(ride.route)
            if drop:
                ride.route.append(drop)
            ride.distance_km = round(ride.route_distance_km(), 3)
            ride.fare = self.pricing.price_ride(ride)
            ride.status = RideStatus.COMPLETED
            ride.ended_at = self.clock()
            if self.rides.close(ride, RideStatus.ONGOING, points_read):
                break
            # A location update landed after the read, so the fare missed it: price it again.
        self.drivers.release(ride.driver_id, ride.route[-1])
        return ride

    def cancel(self, ride_id: str) -> Ride:
        ride = self._in_status(ride_id, RideStatus.BOOKED, "cancel")
        ride.ended_at = self.clock()
        ride.cancellation_fee = self.cancellation.fee(ride, ride.ended_at)
        ride.status = RideStatus.CANCELLED
        if not self.rides.close(ride, RideStatus.BOOKED, len(ride.route)):
            raise InvalidRideStateError(f"ride '{ride.id}' changed while cancelling it")
        # The driver never reached the pickup: leave them where they are.
        self.drivers.release(ride.driver_id)
        return ride

    def history_for_user(self, user_id: str) -> Dict[str, List[Ride]]:
        self.users.get(user_id)
        return self._partition(self.rides.for_user(user_id))

    def history_for_driver(self, driver_id: str) -> Dict[str, List[Ride]]:
        self.drivers.get(driver_id)
        return self._partition(self.rides.for_driver(driver_id))

    def _in_status(self, ride_id: str, expected: RideStatus, action: str) -> Ride:
        ride = self.rides.get(ride_id)
        if ride.status != expected:
            raise InvalidRideStateError(f"cannot {action} ride '{ride_id}': it is {ride.status.value}, "
                                        f"not {expected.value}")
        return ride

    @staticmethod
    def _partition(rides: List[Ride]) -> Dict[str, List[Ride]]:
        """Newest first; "ongoing" covers booked rides too (a driver is assigned either way)."""
        newest_first = sorted(rides, key=lambda r: r.booked_at, reverse=True)
        return {
            "ongoing": [r for r in newest_first if r.is_active],
            RideStatus.COMPLETED.value: [r for r in newest_first if r.status == RideStatus.COMPLETED],
            RideStatus.CANCELLED.value: [r for r in newest_first if r.status == RideStatus.CANCELLED],
        }
