from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from app.matching import MatchingStrategy
from app.pricing import DEFAULT_FARE_STRATEGIES, PricingEngine, SurgeStrategy
from app.repository import CouponRepository, DriverRepository, RideRepository, UserRepository
from app.services import CouponService, DriverService, RideService, UserService


@dataclass
class Container:
    users: UserService
    drivers: DriverService
    coupons: CouponService
    rides: RideService


def build_container(surge: Optional[SurgeStrategy] = None, matching: Optional[MatchingStrategy] = None,
                    clock: Callable[[], datetime] = datetime.now) -> Container:
    user_repo, driver_repo, ride_repo, coupon_repo = (
        UserRepository(), DriverRepository(), RideRepository(), CouponRepository())
    coupon_service = CouponService(coupon_repo)
    return Container(
        users=UserService(user_repo),
        drivers=DriverService(driver_repo, ride_repo),
        coupons=coupon_service,
        rides=RideService(user_repo, driver_repo, ride_repo, coupon_service,
                          PricingEngine(DEFAULT_FARE_STRATEGIES, surge), matching, clock=clock),
    )
