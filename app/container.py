from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from app.config import AppConfig
from app.services.coupons import CouponService
from app.services.drivers import DriverService
from app.services.rides import RideService
from app.services.users import UserService
from app.storage.base import Repositories
from app.storage.memory import in_memory_repositories
from app.strategies.matching import MATCHING_STRATEGIES
from app.strategies.pricing import PricingEngine
from app.strategies.surge import DemandSupplySurge, NoSurge


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
    surge = (DemandSupplySurge(repos.drivers, repos.rides, s.area_radius_km, s.window, s.cap,
                               config.driver_timeout, clock)
             if s.enabled else NoSurge())
    coupon_service = CouponService(repos.coupons)
    return Container(
        users=UserService(repos.users),
        drivers=DriverService(repos.drivers, clock),
        coupons=coupon_service,
        rides=RideService(
            repos.users, repos.drivers, repos.rides, coupon_service,
            pricing=PricingEngine(config.fare_strategies),
            surge=surge,
            matching=MATCHING_STRATEGIES[config.default_matching_strategy],
            cancellation=config.cancellation_policy,
            upgrade_path=config.upgrade_path,
            default_radius_km=config.default_radius_km,
            driver_timeout=config.driver_timeout,
            clock=clock,
        ),
    )
