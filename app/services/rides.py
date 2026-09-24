from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from app.domain.exceptions import InvalidRideStateError, NoDriverAvailableError
from app.domain.models import CarType, Location, Ride, RideStatus
from app.services.common import new_id, require
from app.services.coupons import CouponService
from app.storage.base import DriverRepository, RideRepository, UserRepository
from app.strategies.cancellation import CancellationPolicy
from app.strategies.matching import MatchingStrategy
from app.strategies.pricing import PricingEngine
from app.strategies.surge import SurgeStrategy


def upgrade_chain(requested: CarType, upgrade_path: Dict[CarType, CarType]) -> List[CarType]:
    """The requested type, then each free upgrade in turn, e.g. hatchback -> sedan -> ..."""
    chain = [requested]
    upgrade = upgrade_path.get(requested)
    while upgrade is not None and upgrade not in chain:
        chain.append(upgrade)
        upgrade = upgrade_path.get(upgrade)
    return chain


class RideService:
    def __init__(self, users: UserRepository, drivers: DriverRepository, rides: RideRepository,
                 coupon_service: CouponService, pricing: PricingEngine, surge: SurgeStrategy,
                 matching: MatchingStrategy, cancellation: CancellationPolicy,
                 upgrade_path: Dict[CarType, CarType], default_radius_km: float, driver_timeout: timedelta,
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
        # A driver who hasn't sent a location for this long is treated as offline.
        self.driver_timeout = driver_timeout
        self.clock = clock

    def book(self, user_id: str, pickup: Location, car_type: CarType,
             radius_km: Optional[float] = None, coupon_code: Optional[str] = None,
             matching: Optional[MatchingStrategy] = None) -> Ride:
        self.users.get(user_id)
        radius_km = self.default_radius_km if radius_km is None else radius_km
        require(radius_km > 0, "radius must be positive")
        coupon = self.coupon_service.validate(coupon_code) if coupon_code else None
        if any(r.is_active for r in self.rides.for_user(user_id)):
            raise InvalidRideStateError("user already has an active ride")
        strategy = matching or self.matching
        surge_multiplier = self.surge.multiplier(pickup, user_id)
        seen_since = self.clock() - self.driver_timeout

        for candidate_type in upgrade_chain(car_type, self.upgrade_path):
            candidates = self.drivers.find_available(pickup, radius_km, seen_since, candidate_type)
            for driver in strategy.rank(candidates, pickup):
                ride = Ride(
                    id=new_id("R"), user_id=user_id, driver_id=driver.id,
                    requested_car_type=car_type, assigned_car_type=candidate_type,
                    pickup=pickup, last_location=pickup, coupon=coupon, surge_multiplier=surge_multiplier,
                    booked_at=self.clock(),
                )
                # False means a concurrent booking claimed this driver first; try the next one.
                if self.rides.create(ride):
                    return ride
        raise NoDriverAvailableError(f"no {car_type.value} available within {radius_km} km")

    def start(self, ride_id: str) -> Ride:
        """The rider is picked up: from now on location updates add to the billed distance."""
        def pick_up(ride: Ride) -> None:
            self._require_status(ride, RideStatus.BOOKED, "start")
            ride.status = RideStatus.ONGOING
            ride.picked_up_at = self.clock()
        return self.rides.modify(ride_id, pick_up)

    def end(self, ride_id: str, drop: Optional[Location] = None) -> Ride:
        # Priced under the ride's lock, so no location update can land between measuring and billing.
        def finish(ride: Ride) -> None:
            self._require_status(ride, RideStatus.ONGOING, "end")
            if drop:
                ride.move_to(drop)
            ride.distance_km = round(ride.distance_km, 3)
            ride.fare = self.pricing.price_ride(ride)
            ride.status = RideStatus.COMPLETED
            ride.ended_at = self.clock()
        # Closing the ride also frees the driver at the drop point, in the same transaction.
        return self.rides.modify(ride_id, finish)

    def cancel(self, ride_id: str) -> Ride:
        def cancel_before_pickup(ride: Ride) -> None:
            self._require_status(ride, RideStatus.BOOKED, "cancel")
            ride.ended_at = self.clock()
            ride.cancellation_fee = self.cancellation.fee(ride, ride.ended_at)
            ride.status = RideStatus.CANCELLED
        # Frees the driver in the same transaction, where they are: they never reached the pickup.
        return self.rides.modify(ride_id, cancel_before_pickup)

    def history_for_user(self, user_id: str) -> Dict[str, List[Ride]]:
        self.users.get(user_id)
        return self._partition(self.rides.for_user(user_id))

    def history_for_driver(self, driver_id: str) -> Dict[str, List[Ride]]:
        self.drivers.get(driver_id)
        return self._partition(self.rides.for_driver(driver_id))

    @staticmethod
    def _require_status(ride: Ride, expected: RideStatus, action: str) -> None:
        if ride.status != expected:
            raise InvalidRideStateError(f"cannot {action} ride '{ride.id}': it is {ride.status.value}, "
                                        f"not {expected.value}")

    @staticmethod
    def _partition(rides: List[Ride]) -> Dict[str, List[Ride]]:
        """Newest first; "ongoing" covers booked rides too (a driver is assigned either way)."""
        newest_first = sorted(rides, key=lambda r: r.booked_at, reverse=True)
        return {
            "ongoing": [r for r in newest_first if r.is_active],
            RideStatus.COMPLETED.value: [r for r in newest_first if r.status == RideStatus.COMPLETED],
            RideStatus.CANCELLED.value: [r for r in newest_first if r.status == RideStatus.CANCELLED],
        }
