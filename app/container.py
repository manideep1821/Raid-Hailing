from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from app.matching import MatchingStrategy
from app.pricing import DEFAULT_FARE_STRATEGIES, PricingEngine, SurgeStrategy
from app.repository import Repositories, in_memory_repositories
from app.services import CouponService, DriverService, RideService, UserService


@dataclass
class Container:
    users: UserService
    drivers: DriverService
    coupons: CouponService
    rides: RideService


def build_container(repos: Optional[Repositories] = None, surge: Optional[SurgeStrategy] = None,
                    matching: Optional[MatchingStrategy] = None,
                    clock: Callable[[], datetime] = datetime.now) -> Container:
    repos = repos or in_memory_repositories()
    coupon_service = CouponService(repos.coupons)
    return Container(
        users=UserService(repos.users),
        drivers=DriverService(repos.drivers, repos.rides),
        coupons=coupon_service,
        rides=RideService(repos.users, repos.drivers, repos.rides, coupon_service,
                          PricingEngine(DEFAULT_FARE_STRATEGIES, surge), matching, clock=clock),
    )
