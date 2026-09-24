from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from app.cancellation import GracePeriodCancellationPolicy
from app.config import AppConfig
from app.matching import MATCHING_STRATEGIES
from app.pricing import PricingEngine
from app.repository import Repositories, in_memory_repositories
from app.services import CouponService, DriverService, RideService, UserService
from app.surge import DemandSupplySurge, NoSurge


@dataclass
class Container:
    users: UserService
    drivers: DriverService
    coupons: CouponService
    rides: RideService


def build_container(config: AppConfig, repos: Optional[Repositories] = None,
                    clock: Callable[[], datetime] = datetime.now) -> Container:
    repos = repos or in_memory_repositories()
    s = config.surge
    surge = (DemandSupplySurge(repos.drivers, repos.rides, s.area_radius_km, s.window, s.cap, clock)
             if s.enabled else NoSurge())
    coupon_service = CouponService(repos.coupons)
    return Container(
        users=UserService(repos.users),
        drivers=DriverService(repos.drivers, repos.rides),
        coupons=coupon_service,
        rides=RideService(
            repos.users, repos.drivers, repos.rides, coupon_service,
            pricing=PricingEngine(config.fare_strategies),
            surge=surge,
            matching=MATCHING_STRATEGIES[config.default_matching_strategy],
            cancellation=GracePeriodCancellationPolicy(config.cancellation_grace, config.cancellation_fee),
            upgrade_path=config.upgrade_path,
            default_radius_km=config.default_radius_km,
            clock=clock,
        ),
    )
