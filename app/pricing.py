from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from app.exceptions import ValidationError
from app.models import CarType, Coupon, DiscountType, Location


class FareStrategy(ABC):
    @abstractmethod
    def base_fare(self, distance_km: float) -> float: ...


class TieredFareStrategy(FareStrategy):
    """Slab pricing: each km is charged at the rate of the slab it falls in.

    tiers are (cumulative_upper_bound_km, rate_per_km); the last bound is None (open-ended).
    [(2, 10), (5, 8), (None, 5)] => km 0-2 @10, km 2-5 @8, km 5+ @5.
    """

    def __init__(self, min_fare: float, tiers: List[Tuple[Optional[float], float]]):
        if not tiers or tiers[-1][0] is not None:
            raise ValidationError("last tier must be open-ended (upper bound None)")
        bounds = [b for b, _ in tiers[:-1]]
        if bounds != sorted(bounds) or len(set(bounds)) != len(bounds):
            raise ValidationError("tier bounds must be strictly increasing")
        self.min_fare = min_fare
        self.tiers = tiers

    def base_fare(self, distance_km: float) -> float:
        cost, lower = 0.0, 0.0
        for upper, rate in self.tiers:
            if distance_km <= lower:
                break
            span = distance_km - lower if upper is None else min(distance_km, upper) - lower
            cost += span * rate
            lower = upper if upper is not None else lower
        return max(cost, self.min_fare)


class SurgeStrategy(ABC):
    @abstractmethod
    def multiplier(self, location: Location) -> float: ...


class NoSurge(SurgeStrategy):
    def multiplier(self, location: Location) -> float:
        return 1.0


class GridDemandSurge(SurgeStrategy):
    """Surge per coarse lat/lng grid cell, derived from demand/supply ratio and capped."""

    def __init__(self, cell_size_deg: float = 0.01, cap: float = 3.0):
        self.cell_size_deg = cell_size_deg
        self.cap = cap
        self._multipliers: Dict[Tuple[int, int], float] = {}

    def _cell(self, location: Location) -> Tuple[int, int]:
        return (int(location.lat // self.cell_size_deg), int(location.lng // self.cell_size_deg))

    def record_demand_supply(self, location: Location, demand: int, supply: int) -> None:
        ratio = demand / supply if supply else self.cap
        self._multipliers[self._cell(location)] = min(max(1.0, ratio), self.cap)

    def multiplier(self, location: Location) -> float:
        return self._multipliers.get(self._cell(location), 1.0)


def apply_coupon(fare: float, coupon: Coupon) -> float:
    if coupon.discount_type == DiscountType.FLAT:
        discount = coupon.value
    else:
        discount = fare * coupon.value / 100
    if coupon.max_discount is not None:
        discount = min(discount, coupon.max_discount)
    return max(fare - discount, 0.0)


class PricingEngine:
    """Fare = car-type tiered fare x surge, then coupon discount."""

    def __init__(self, fare_strategies: Dict[CarType, FareStrategy], surge: Optional[SurgeStrategy] = None):
        self.fare_strategies = fare_strategies
        self.surge = surge or NoSurge()

    def calculate(self, car_type: CarType, distance_km: float, pickup: Location,
                  coupon: Optional[Coupon] = None) -> float:
        fare = self.fare_strategies[car_type].base_fare(distance_km) * self.surge.multiplier(pickup)
        if coupon:
            fare = apply_coupon(fare, coupon)
        return round(fare, 2)
